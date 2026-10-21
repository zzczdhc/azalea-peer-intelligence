"""Official ticker mapping, recent filings and date-overlapping history shards."""

from __future__ import annotations

import re
from datetime import date
from urllib.parse import quote

from .client import SecClient, SecError


TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_ROOT = "https://data.sec.gov/submissions/"
SUPPORTED_FORMS = {"10-K", "10-Q", "10-K/A", "10-Q/A"}


def normalize_cik(value) -> str:
    text = str(value).strip()
    if not re.fullmatch(r"[0-9]{1,10}", text) or int(text) == 0:
        raise SecError(f"Invalid CIK: {value!r}")
    return text.zfill(10)


def iso_date(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise SecError(f"Invalid ISO date: {value!r}")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as error:
        raise SecError(f"Invalid ISO date: {value!r}") from error


def ticker_map(client: SecClient) -> dict:
    payload = client.json(TICKERS_URL, ttl=24 * 3600)
    mapping = {}
    for row in payload.values():
        if not isinstance(row, dict) or not {"ticker", "cik_str", "title"} <= row.keys():
            raise SecError("Unexpected company_tickers.json schema")
        ticker = str(row["ticker"]).strip().upper()
        candidate = {"ticker": ticker, "cik": normalize_cik(row["cik_str"]),
                     "name": row["title"]}
        if ticker in mapping and mapping[ticker]["cik"] != candidate["cik"]:
            raise SecError(f"Ambiguous ticker: {ticker}")
        mapping[ticker] = candidate
    if not mapping:
        raise SecError("Empty ticker mapping")
    return mapping


def primary_url(cik: str, accession: str, document: str) -> str:
    if not re.fullmatch(r"\d{10}-\d{2}-\d{6}", accession):
        raise SecError(f"Invalid accession number: {accession!r}")
    if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", document)
            or ".." in document
            or not document.lower().endswith((".htm", ".html", ".xhtml", ".txt"))):
        raise SecError(f"Unsafe or unsupported primary document: {document!r}")
    # The accession's prefix can be a filing agent's CIK; use the issuer CIK.
    return (f"https://www.sec.gov/Archives/edgar/data/{int(normalize_cik(cik))}/"
            f"{accession.replace('-', '')}/{quote(document, safe='')}")


def _column_rows(columns: dict, forms: set[str]) -> list[dict]:
    required = ("accessionNumber", "filingDate", "form", "primaryDocument")
    if not isinstance(columns, dict) or any(not isinstance(columns.get(k), list) for k in required):
        raise SecError("Missing submissions column arrays")
    count = len(columns["accessionNumber"])
    for key, values in columns.items():
        if isinstance(values, list) and len(values) != count:
            raise SecError(f"Misaligned submissions column {key}")
    rows = []
    for index in range(count):
        if columns["form"][index] not in forms:
            continue
        def field(key):
            values = columns.get(key)
            return values[index] if isinstance(values, list) else None
        row = {"accession": field("accessionNumber"), "form": field("form"),
               "filing_date": iso_date(field("filingDate")),
               "report_date": field("reportDate") or None,
               "acceptance_datetime": field("acceptanceDateTime") or None,
               "primary_document": field("primaryDocument"),
               "is_xbrl": field("isXBRL"), "is_inline_xbrl": field("isInlineXBRL")}
        if row["report_date"]:
            row["report_date"] = iso_date(row["report_date"])
        rows.append(row)
    return rows


def discover(client: SecClient, cik: str, ticker: str | None, *, since: str,
             until: str, forms: set[str], max_per_form: int,
             submissions_ttl: float = 0) -> tuple[dict, list[dict], dict]:
    cik = normalize_cik(cik)
    url = SUBMISSIONS_ROOT + "CIK" + cik + ".json"
    payload = client.json(url, ttl=submissions_ttl)
    if normalize_cik(payload.get("cik", "")) != cik or not payload.get("name"):
        raise SecError("Submissions issuer identity mismatch or missing company name")
    company = {"cik": cik, "ticker": ticker or (payload.get("tickers") or [cik])[0],
               "name": payload["name"], "tickers": payload.get("tickers", []),
               "exchanges": payload.get("exchanges", []), "sic": payload.get("sic"),
               "sic_description": payload.get("sicDescription"),
               "sector": None, "sector_source": None,
               "fiscal_year_end": payload.get("fiscalYearEnd"),
               "source_url": url}
    filings = payload.get("filings", {})
    candidates = _column_rows(filings.get("recent"), forms)
    sources = [url]
    for shard in filings.get("files", []):
        start, end = shard.get("filingFrom"), shard.get("filingTo")
        if (start and iso_date(start) > until) or (end and iso_date(end) < since):
            continue
        name = shard.get("name", "")
        if not re.fullmatch(r"CIK" + cik + r"-submissions-\d+\.json", name):
            raise SecError(f"Unexpected historical submissions filename: {name!r}")
        shard_url = SUBMISSIONS_ROOT + name
        # Revalidate historical indexes too: post-acceptance corrections can exist.
        history = client.json(shard_url, ttl=submissions_ttl)
        for row in _column_rows(history, forms):
            row["discovery_source_url"] = shard_url
            candidates.append(row)
        sources.append(shard_url)
    unique = {}
    for row in candidates:
        if not since <= row["filing_date"] <= until:
            continue
        row["cik"] = cik
        row["source_url"] = primary_url(cik, row["accession"], row["primary_document"])
        row["index_url"] = row["source_url"].rsplit("/", 1)[0] + "/" + row["accession"] + "-index.htm"
        row.setdefault("discovery_source_url", url)
        # Recent metadata has precedence when the same accession also occurs in history.
        unique.setdefault(row["accession"], row)
    selected = []
    available_by_form = {}
    for form in sorted(forms):
        matches = sorted((r for r in unique.values() if r["form"] == form),
                         key=lambda r: (r["filing_date"], r["accession"]), reverse=True)
        available_by_form[form] = len(matches)
        selected.extend(matches if max_per_form == 0 else matches[:max_per_form])
    return company, selected, {"sources": sources, "in_scope": len(unique),
                               "available_by_form": available_by_form,
                               "selected": len(selected)}
