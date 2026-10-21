"""SQLite manifest, atomic artifacts, integrity audit and one-writer lock."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .client import SecError, atomic_write, sha256, utc_now
from .discovery import primary_url
from .text import text_issues


@contextmanager
def run_lock(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".sync.lock").open("a+b") as file:
        try:
            import fcntl
            fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            unlock = lambda: fcntl.flock(file.fileno(), fcntl.LOCK_UN)
        except ImportError:
            import msvcrt
            if file.tell() == 0:
                file.write(b"0")
                file.flush()
            file.seek(0)
            try:
                msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as error:
                raise SecError("Another process is using this output directory") from error
            def unlock():
                file.seek(0)
                msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
        except BlockingIOError as error:
            raise SecError("Another process is using this output directory") from error
        try:
            yield
        finally:
            unlock()


class Store:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root / "manifest.sqlite")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS companies (
                cik TEXT PRIMARY KEY, metadata_json TEXT NOT NULL, observed_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS filings (
                cik TEXT NOT NULL REFERENCES companies(cik), accession TEXT NOT NULL,
                form TEXT NOT NULL, filing_date TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('discovered','ready','failed')),
                discovery_json TEXT NOT NULL, artifact_json TEXT,
                error TEXT, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
                PRIMARY KEY(cik, accession)
            );
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY, report_json TEXT NOT NULL
            );
        """)
        self.db.commit()

    def close(self):
        self.db.close()

    def company(self, company):
        self.db.execute("""INSERT INTO companies VALUES(?,?,?) ON CONFLICT(cik)
                           DO UPDATE SET metadata_json=excluded.metadata_json,
                           observed_at=excluded.observed_at""",
                        (company["cik"], json.dumps(company, ensure_ascii=False), utc_now()))
        self.db.commit()

    def discover(self, filing):
        previous = self.get(filing["cik"], filing["accession"])
        status = previous["status"] if previous else "discovered"
        if previous and previous["discovery"].get("source_url") != filing["source_url"]:
            status = "discovered"
        now = utc_now()
        self.db.execute("""INSERT INTO filings(cik,accession,form,filing_date,status,
                            discovery_json,first_seen,last_seen) VALUES(?,?,?,?,?,?,?,?)
                            ON CONFLICT(cik,accession) DO UPDATE SET
                            form=excluded.form,filing_date=excluded.filing_date,
                            status=excluded.status,discovery_json=excluded.discovery_json,
                            last_seen=excluded.last_seen""",
                        (filing["cik"], filing["accession"], filing["form"],
                         filing["filing_date"], status, json.dumps(filing), now, now))
        self.db.commit()

    @staticmethod
    def _record(row):
        if row is None:
            return None
        record = dict(row)
        record["discovery"] = json.loads(record.pop("discovery_json"))
        record["artifact"] = json.loads(record.pop("artifact_json")) if row["artifact_json"] else None
        return record

    def get(self, cik, accession):
        return self._record(self.db.execute("SELECT * FROM filings WHERE cik=? AND accession=?",
                                            (cik, accession)).fetchone())

    def records(self):
        return [self._record(row) for row in self.db.execute("SELECT * FROM filings ORDER BY cik,accession")]

    def pending(self, cik, forms, since, until):
        return [row["discovery"] for row in self.records() if row["cik"] == cik
                and row["status"] != "ready" and row["form"] in forms
                and since <= row["filing_date"] <= until]

    def ready(self, filing, artifact):
        self.db.execute("""UPDATE filings SET status='ready',artifact_json=?,error=NULL
                           WHERE cik=? AND accession=?""",
                        (json.dumps(artifact, ensure_ascii=False), filing["cik"], filing["accession"]))
        self.db.commit()

    def failed(self, filing, error):
        self.db.execute("UPDATE filings SET status='failed',error=? WHERE cik=? AND accession=?",
                        (str(error), filing["cik"], filing["accession"]))
        self.db.commit()

    def path(self, relative):
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root.resolve()):
            raise SecError("Artifact path escapes output directory")
        return path

    def issues(self, record) -> list[str]:
        if record["status"] != "ready" or not record["artifact"]:
            return ["filing is not ready"]
        artifact = record["artifact"]
        issues = []
        required = ("cik", "ticker", "company_name", "accession", "form", "filing_date",
                    "report_date", "primary_document", "source_url", "raw_path",
                    "text_path", "metadata_path", "raw_sha256", "text_sha256",
                    "fetched_at", "cleaner_version", "discovery_source_url")
        for field in required:
            if not artifact.get(field):
                issues.append(f"missing metadata: {field}")
        for field in ("cik", "accession", "form", "filing_date", "report_date",
                      "primary_document", "source_url"):
            if artifact.get(field) != record["discovery"].get(field):
                issues.append(f"artifact/discovery mismatch: {field}")
        try:
            if artifact.get("source_url") != primary_url(record["cik"], record["accession"],
                                                        artifact["primary_document"]):
                issues.append("invalid canonical source URL")
            raw = self.path(artifact["raw_path"]).read_bytes()
            text_bytes = self.path(artifact["text_path"]).read_bytes()
            text = text_bytes.decode("utf-8", errors="strict")
            if sha256(raw) != artifact["raw_sha256"] or len(raw) != artifact["raw_bytes"]:
                issues.append("raw content hash/size mismatch")
            if sha256(text_bytes) != artifact["text_sha256"]:
                issues.append("cleaned text hash mismatch")
            if len(text) != artifact["characters"] or len(text.split()) != artifact["words"]:
                issues.append("text count mismatch")
            issues.extend(text_issues(text, record["form"], artifact["min_chars"]))
            saved = json.loads(self.path(artifact["metadata_path"]).read_text(encoding="utf-8"))
            if saved != artifact:
                issues.append("metadata sidecar does not match manifest")
        except (OSError, KeyError, ValueError, UnicodeError, SecError) as error:
            issues.append(f"artifact read/validation failed: {error}")
        return issues

    def audit(self):
        records = self.records()
        results = [{"cik": r["cik"], "accession": r["accession"], "status": r["status"],
                    "issues": self.issues(r)} for r in records]
        valid = sum(not r["issues"] for r in results)
        return {"checked": len(results), "valid": valid, "invalid": len(results) - valid,
                "integrity_rate": valid / len(results) if results else None,
                "results": results}

    def export(self):
        companies = {row["cik"]: json.loads(row["metadata_json"])
                     for row in self.db.execute("SELECT * FROM companies")}
        atomic_write(self.root / "companies.jsonl", "".join(
            json.dumps(companies[cik], ensure_ascii=False) + "\n"
            for cik in sorted(companies)).encode())
        lines = []
        for record in self.records():
            lines.append(json.dumps({"company": companies[record["cik"]], **record},
                                    ensure_ascii=False) + "\n")
        atomic_write(self.root / "filings.jsonl", "".join(lines).encode())

    def save_run(self, report):
        self.db.execute("INSERT INTO runs VALUES(?,?)",
                        (report["run_id"], json.dumps(report, ensure_ascii=False)))
        self.db.commit()
