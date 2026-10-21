"""Discovery -> immutable raw primary document -> visible text -> audited manifest."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from .client import AccessBlocked, SecClient, SecError, atomic_write, sha256, utc_now, write_json
from .discovery import SUPPORTED_FORMS, discover, iso_date, normalize_cik, ticker_map
from .storage import Store, run_lock
from .text import CLEANER_VERSION, clean_document, text_issues


@dataclass(frozen=True)
class Config:
    tickers: tuple[str, ...] = ("SPGI", "MCO", "MSFT")
    ciks: tuple[str, ...] = ()
    since: str = "2025-10-15"
    until: str = "2026-10-14"
    forms: tuple[str, ...] = ("10-K", "10-Q")
    max_per_form: int = 1  # 0 = every matching filing, not a download count cap.
    min_chars: int = 1000
    submissions_ttl: float = 0  # Revalidate mutable indexes on every run.
    refresh_documents: bool = False  # Explicitly recheck for post-acceptance corrections.

    def validate(self):
        if iso_date(self.since) > iso_date(self.until):
            raise ValueError("since must not be after until")
        if not self.tickers and not self.ciks:
            raise ValueError("At least one ticker or CIK is required")
        if not self.forms or not set(self.forms) <= SUPPORTED_FORMS:
            raise ValueError("Only 10-K, 10-Q, 10-K/A and 10-Q/A are supported")
        if self.max_per_form < 0 or self.min_chars < 1 or self.submissions_ttl < 0:
            raise ValueError("Invalid collection limits")


def _materialize(store: Store, client: SecClient, company: dict, filing: dict,
                 min_chars: int, data_kind: str, refresh_documents: bool):
    body, cache_meta = client.get(filing["source_url"], ttl=0 if refresh_documents else None)
    content_type = cache_meta["headers"].get("content-type", "")
    try:
        text, quality = clean_document(body, content_type, filing["primary_document"])
    except SecError:
        client.invalidate(filing["source_url"])
        raise
    issues = text_issues(text, filing["form"], min_chars)
    if issues:
        # A bad 200 response must not poison the immutable cache indefinitely.
        client.invalidate(filing["source_url"])
        raise SecError("Text quality gate: " + "; ".join(issues))
    base = Path("filings") / filing["cik"] / filing["accession"]
    raw_path = (base / filing["primary_document"]).as_posix()
    text_path = (base / "text.txt").as_posix()
    metadata_path = (base / "metadata.json").as_posix()
    encoded_text = text.encode("utf-8")
    artifact = {**filing, "ticker": company["ticker"], "company_name": company["name"],
                "sic": company["sic"], "sic_description": company["sic_description"],
                "sector": company["sector"], "sector_source": company["sector_source"],
                "company_metadata_source_url": company["source_url"],
                "company_metadata_observed_at": utc_now(),
                "raw_path": raw_path, "text_path": text_path, "metadata_path": metadata_path,
                "raw_bytes": len(body), "raw_sha256": sha256(body),
                "text_sha256": sha256(encoded_text), "content_type": content_type,
                "fetched_at": cache_meta["fetched_at"], "processed_at": utc_now(),
                "min_chars": min_chars, "data_kind": "synthetic_fixture" if
                    cache_meta.get("response_origin") == "injected_transport" else data_kind, **quality}
    # Publish all files before declaring the DB row ready. A crash is repaired next run.
    atomic_write(store.path(raw_path), body)
    atomic_write(store.path(text_path), encoded_text)
    write_json(store.path(metadata_path), artifact)
    store.ready(filing, artifact)
    issues = store.issues(store.get(filing["cik"], filing["accession"]))
    if issues:
        raise SecError("Integrity gate: " + "; ".join(issues))


def sync(root: Path, client: SecClient, config: Config, *, data_kind="live_sec") -> dict:
    config.validate()
    root = Path(root)
    before = dict(client.stats)
    event_start = len(client.events)
    report = {"run_id": uuid.uuid4().hex, "started_at": utc_now(), "data_kind": data_kind,
              "network_mode": "offline_cache" if client.offline else
                              "injected_fixture" if data_kind == "synthetic_fixture" else "live",
              "config": asdict(config), "companies": [], "errors": [],
              "selected": 0, "processed": 0, "already_ready": 0, "failed": 0}
    with run_lock(root):
        store = Store(root)
        try:
            targets = []
            tickers = list(dict.fromkeys(t.strip().upper() for t in config.tickers))
            if tickers:
                try:
                    mapping = ticker_map(client)
                except (SecError, ValueError, KeyError, TypeError) as error:
                    mapping = {}
                    report["errors"].append({"stage": "mapping", "error": str(error)})
                for ticker in tickers:
                    if ticker in mapping:
                        targets.append(mapping[ticker])
                    else:
                        report["companies"].append({"target": ticker, "status": "failed",
                                                    "error": "Ticker mapping unavailable or unknown ticker"})
            for cik in dict.fromkeys(config.ciks):
                targets.append({"cik": normalize_cik(cik), "ticker": None})
            seen = set()
            for target in targets:
                if target["cik"] in seen:
                    continue  # Multiple share-class tickers can resolve to one issuer.
                seen.add(target["cik"])
                company_result = {"target": target["ticker"] or target["cik"],
                                  "cik": target["cik"], "status": "failed", "filings": []}
                report["companies"].append(company_result)
                try:
                    company, selected, discovery = discover(
                        client, target["cik"], target["ticker"], since=config.since,
                        until=config.until, forms=set(config.forms),
                        max_per_form=config.max_per_form, submissions_ttl=config.submissions_ttl)
                    store.company(company)
                    # Retry previously selected pending/failed filings even when a newer
                    # filing replaces them in the latest-per-form sample.
                    pending = store.pending(company["cik"], set(config.forms), config.since, config.until)
                    merged = {r["accession"]: r for r in pending}
                    merged.update({r["accession"]: r for r in selected})
                    selected = list(merged.values())
                    company_result.update({"name": company["name"], "discovery": discovery,
                                           "selected_including_retries": len(selected)})
                    report["selected"] += len(selected)
                    for filing in selected:
                        store.discover(filing)
                        record = store.get(filing["cik"], filing["accession"])
                        outcome = {"accession": filing["accession"], "form": filing["form"]}
                        company_result["filings"].append(outcome)
                        if (not config.refresh_documents and not store.issues(record)
                                and record["artifact"]["cleaner_version"] == CLEANER_VERSION
                                and record["artifact"]["min_chars"] >= config.min_chars):
                            report["already_ready"] += 1
                            outcome["status"] = "already_ready"
                            continue
                        try:
                            _materialize(store, client, company, filing, config.min_chars, data_kind,
                                         config.refresh_documents)
                            report["processed"] += 1
                            outcome["status"] = "processed"
                        except (SecError, OSError, ValueError, KeyError, TypeError) as error:
                            store.failed(filing, error)
                            report["failed"] += 1
                            outcome.update({"status": "failed", "error": str(error)})
                            report["errors"].append({"stage": "download_or_process", "cik": filing["cik"],
                                                     "accession": filing["accession"], "error": str(error)})
                    company_result["status"] = "complete" if all(
                        r["status"] != "failed" for r in company_result["filings"]) else "partial"
                except (SecError, ValueError, KeyError, TypeError, OSError) as error:
                    company_result["error"] = str(error)
                    report["errors"].append({"stage": "discovery", "cik": target["cik"], "error": str(error)})
            requested_pairs = len(report["companies"]) * len(set(config.forms))
            covered_pairs = sum(len({r["form"] for r in c.get("filings", [])
                                      if r["status"] in {"processed", "already_ready"}})
                                for c in report["companies"])
            selected_ready = report["processed"] + report["already_ready"]
            attempted = report["processed"] + report["failed"]
            report["metrics"] = {
                "processing_success_rate": report["processed"] / attempted if attempted else None,
                "selected_ready_rate": selected_ready / report["selected"] if report["selected"] else None,
                "company_form_coverage": covered_pairs / requested_pairs if requested_pairs else None,
                "covered_company_form_pairs": covered_pairs, "requested_company_form_pairs": requested_pairs,
                "discovery_success_rate": sum("discovery" in c for c in report["companies"]) /
                                          len(report["companies"]) if report["companies"] else None,
            }
            report["audit"] = store.audit()
            report["http"] = {k: client.stats[k] - before[k] for k in client.stats}
            report["http_events"] = client.events[event_start:]
            report["blocked_reason"] = client.blocked_reason
            report["status"] = "complete" if (not report["errors"] and report["audit"]["invalid"] == 0
                    and report["metrics"]["company_form_coverage"] == 1.0) else "partial"
            if client.blocked_reason:
                report["status"] = "blocked"
            report["finished_at"] = utc_now()
            store.export()
            store.save_run(report)
            write_json(root / "runs" / (report["run_id"] + ".json"), report)
            write_json(root / "last_run.json", report)
            return report
        finally:
            store.close()
