"""A local, English-language interface for the SEC ingestion pipeline."""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from .client import RateLimiter, SecClient, SecError, utc_now
from .competitors import extract_competitors
from .discovery import ticker_map
from .pipeline import Config, sync
from .storage import Store

STATIC = Path(__file__).with_name("static")
CIK = re.compile(r"[0-9]{10}")
ACCESSION = re.compile(r"[0-9]{10}-[0-9]{2}-[0-9]{6}")
TICKER = re.compile(r"[A-Z][A-Z0-9.-]{0,11}")


class RequestError(ValueError):
    pass


class BusyError(RuntimeError):
    pass


def default_dates():
    today = datetime.now(ZoneInfo("America/New_York")).date()
    return (today - timedelta(days=365)).isoformat(), today.isoformat()


def parse_collection(payload):
    if not isinstance(payload, dict):
        raise RequestError("Send a JSON object.")
    target = payload.get("ticker", "")
    if not isinstance(target, str):
        raise RequestError("Enter a ticker or numeric CIK.")
    target = target.strip().upper()
    is_cik = bool(re.fullmatch(r"[0-9]{1,10}", target)) and int(target) > 0
    if not is_cik and not TICKER.fullmatch(target):
        raise RequestError("Enter one ticker (for example, SPGI) or a numeric CIK.")
    since, until = default_dates()
    forms = payload.get("forms", ["10-K", "10-Q"])
    if (not isinstance(forms, list) or not forms or
            any(not isinstance(f, str) or f not in {"10-K", "10-Q"} for f in forms)):
        raise RequestError("Choose 10-K, 10-Q, or both.")
    limit = payload.get("max_per_form", 1)
    if type(limit) is not int or not 1 <= limit <= 3:
        raise RequestError("Reports per form must be between 1 and 3.")
    refresh = payload.get("refresh_documents", False)
    if type(refresh) is not bool:
        raise RequestError("Refresh must be true or false.")
    config = Config(tickers=() if is_cik else (target,), ciks=(target,) if is_cik else (),
                    since=payload.get("since", since), until=payload.get("until", until),
                    forms=tuple(dict.fromkeys(forms)), max_per_form=limit,
                    refresh_documents=refresh)
    config.validate()
    return config


class CollectorService:
    def __init__(self, root, user_agent=None, client_factory=None):
        self.root = Path(root).resolve()
        self.user_agent = user_agent
        self.client_factory = client_factory
        self.limiter = RateLimiter(2)
        self.lock = threading.Lock()
        self.jobs = {}
        self.active_job = None

    def config(self):
        since, until = default_dates()
        return {"contact_configured": bool(self.user_agent or self.client_factory),
                "since": since, "until": until, "default_ticker": "SPGI",
                "active_job": self.active_job,
                "supported_forms": ["10-K", "10-Q"]}

    def start(self, payload):
        config = parse_collection(payload)
        if not self.user_agent and not self.client_factory:
            raise RequestError("Set SEC_USER_AGENT in .env.local before collecting filings.")
        with self.lock:
            if self.active_job:
                raise BusyError("A collection is already running. Wait for it to finish.")
            identifier = uuid.uuid4().hex
            self.jobs[identifier] = {"id": identifier, "state": "running",
                "target": (config.tickers or config.ciks)[0], "started_at": utc_now(),
                "finished_at": None, "report": None, "error": None}
            self.active_job = identifier
        threading.Thread(target=self._run, args=(identifier, config), daemon=True).start()
        return self.job(identifier)

    def _run(self, identifier, config):
        try:
            client = (self.client_factory(self.root) if self.client_factory else
                      SecClient(self.root / "cache", self.user_agent, rps=2, timeout=20, retries=2))
            if self.client_factory is None:
                client.limiter = self.limiter
            report = sync(self.root, client, config,
                          data_kind="synthetic_fixture" if self.client_factory else "live_sec")
            update = {"state": report["status"], "report": report}
        except Exception as error:
            update = {"state": "failed", "error": str(error)}
        with self.lock:
            self.jobs[identifier].update(update, finished_at=utc_now())
            self.active_job = None
            # The durable run reports are in SQLite and runs/. Bound session memory.
            for old in list(self.jobs)[:-50]:
                del self.jobs[old]

    def job(self, identifier):
        with self.lock:
            if identifier not in self.jobs:
                raise KeyError("Collection not found. Reload the library for saved files.")
            return dict(self.jobs[identifier])

    def library(self):
        manifest = self.root / "manifest.sqlite"
        if not manifest.exists():
            return {"companies": [], "filings": [], "last_run": None}
        db = sqlite3.connect(manifest.as_uri() + "?mode=ro", uri=True, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            companies = [json.loads(r[0]) for r in db.execute(
                "SELECT metadata_json FROM companies ORDER BY cik")]
            records = []
            for row in db.execute("SELECT * FROM filings ORDER BY filing_date DESC,accession DESC"):
                discovery = json.loads(row["discovery_json"])
                artifact = json.loads(row["artifact_json"]) if row["artifact_json"] else {}
                records.append({"cik": row["cik"], "accession": row["accession"],
                    "status": row["status"], "error": row["error"],
                    "form": row["form"], "filing_date": row["filing_date"],
                    "report_date": discovery.get("report_date"),
                    "source_url": discovery["source_url"],
                    "primary_document": discovery["primary_document"],
                    "ticker": artifact.get("ticker"),
                    "company_name": artifact.get("company_name"),
                    "characters": artifact.get("characters"), "words": artifact.get("words"),
                    "fetched_at": artifact.get("fetched_at"),
                    "data_kind": artifact.get("data_kind"),
                    "warnings": artifact.get("warnings", [])})
            run = db.execute("SELECT report_json FROM runs ORDER BY rowid DESC LIMIT 1").fetchone()
            return {"companies": companies, "filings": records,
                    "last_run": json.loads(run[0]) if run else None}
        finally:
            db.close()

    def document(self, cik, accession, kind):
        if not CIK.fullmatch(cik) or not ACCESSION.fullmatch(accession):
            raise RequestError("Invalid filing identifier.")
        if kind not in {"text", "raw", "metadata"}:
            raise RequestError("Unsupported document type.")
        if not (self.root / "manifest.sqlite").exists():
            raise KeyError("No filings have been collected.")
        with self.lock:
            if self.active_job:
                raise BusyError("Collection is running. Open the document when it finishes.")
            store = Store(self.root)
            try:
                record = store.get(cik, accession)
                if record is None:
                    raise KeyError("Filing not found.")
                issues = store.issues(record)
                if issues:
                    raise RequestError("This filing needs to be collected again: " + "; ".join(issues))
                artifact = record["artifact"]
                key = {"text": "text_path", "raw": "raw_path", "metadata": "metadata_path"}[kind]
                path = store.path(artifact[key])
                return path.read_bytes(), path.name, artifact
            finally:
                store.close()

    def competitors(self, text, artifact):
        # Identity lookup is offline: inspecting a report never triggers SEC downloads.
        try:
            directory = list(ticker_map(SecClient(self.root / "cache", offline=True)).values())
        except (SecError, ValueError, OSError):
            directory = []
        result = extract_competitors(text, directory, artifact.get("company_name", ""))
        result["directory_available"] = bool(directory)
        return result


def make_handler(service):
    class Handler(BaseHTTPRequestHandler):
        server_version = "Azalea/0.1"

        def _send(self, status, body, mime="application/json; charset=utf-8", attachment=None):
            if not isinstance(body, bytes):
                body = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; "
                "style-src 'self'; img-src 'self' data:; connect-src 'self'; "
                "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            if attachment:
                self.send_header("Content-Disposition", 'attachment; filename="' + attachment + '"')
            self.end_headers()
            self.wfile.write(body)

        def _error(self, error, status=400):
            self._send(status, {"error": str(error)})

        def do_GET(self):
            parsed = urlsplit(self.path)
            path = parsed.path
            try:
                assets = {"/": ("index.html", "text/html; charset=utf-8"),
                          "/app.js": ("app.js", "application/javascript; charset=utf-8"),
                          "/styles.css": ("styles.css", "text/css; charset=utf-8"),
                          "/favicon.svg": ("favicon.svg", "image/svg+xml")}
                if path in assets:
                    filename, mime = assets[path]
                    self._send(200, (STATIC / filename).read_bytes(), mime)
                elif path == "/api/config":
                    self._send(200, service.config())
                elif path == "/api/library":
                    self._send(200, service.library())
                elif re.fullmatch(r"/api/jobs/[a-f0-9]{32}", path):
                    self._send(200, service.job(path.rsplit("/", 1)[1]))
                elif path.startswith("/documents/"):
                    parts = path.strip("/").split("/")
                    if len(parts) != 4:
                        raise RequestError("Invalid document path.")
                    _, cik, accession, kind = parts
                    body, name, artifact = service.document(cik, accession, kind)
                    if kind == "text" and "download" not in parse_qs(parsed.query):
                        text = body.decode("utf-8")
                        self._send(200, {"text": text, "truncated": False,
                                         "metadata": artifact,
                                         "competitors": service.competitors(text, artifact)})
                    else:
                        mime = "text/plain; charset=utf-8" if kind == "text" else (
                            "application/json; charset=utf-8" if kind == "metadata" else "application/octet-stream")
                        self._send(200, body, mime, attachment=name)
                else:
                    self._error("Not found.", 404)
            except KeyError as error:
                self._error(error, 404)
            except BusyError as error:
                self._error(error, 409)
            except (ValueError, SecError, OSError, sqlite3.Error) as error:
                self._error(error)

        def do_POST(self):
            if self.path != "/api/collect":
                self._error("Not found.", 404)
                return
            origin = self.headers.get("Origin")
            port = self.server.server_port
            if origin and origin not in {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}:
                self._error("Cross-origin collection requests are not allowed.", 403)
                return
            try:
                if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
                    raise RequestError("Use application/json.")
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 8192:
                    raise RequestError("Invalid request size.")
                job = service.start(json.loads(self.rfile.read(length)))
                self._send(202, job)
            except BusyError as error:
                self._error(error, 409)
            except (ValueError, SecError, TypeError) as error:
                self._error(error)

        def log_message(self, format, *args):
            # Request paths only; never print the SEC contact configuration.
            print("Azalea:", format % args, flush=True)

    return Handler


def load_contact(root):
    if os.environ.get("SEC_USER_AGENT"):
        return os.environ["SEC_USER_AGENT"]
    local = Path(root) / ".env.local"
    if local.is_file():
        for line in local.read_text().splitlines():
            if line.startswith("SEC_USER_AGENT="):
                return line.split("=", 1)[1].strip().strip("\"'")
    return None


def main():
    parser = argparse.ArgumentParser(description="Run the local Azalea filings workspace.")
    parser.add_argument("--port", type=int, default=59942)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    args = parser.parse_args()
    service = CollectorService(args.data_dir, load_contact(Path.cwd()))
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(service))
    print(f"Azalea: http://127.0.0.1:{server.server_port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
