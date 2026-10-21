"""Exercise the web boundary and background jobs without sending requests to SEC."""

import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from azalea_edgar.client import RateLimiter, SecError
from azalea_edgar.web import BusyError, CollectorService, RequestError, make_handler, parse_collection
from tests.support import FixtureTransport, fixture_client

PAYLOAD = {"ticker": "SPGI", "since": "2025-10-15", "until": "2026-10-14"}


def finish(service, identifier):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        job = service.job(identifier)
        if job["state"] != "running":
            return job
        time.sleep(0.01)
    raise AssertionError("Background collection did not finish")


class RequestTests(unittest.TestCase):
    def test_ticker_and_cik_inputs_select_the_correct_lookup(self):
        ticker = parse_collection({**PAYLOAD, "ticker": " spgi "})
        self.assertEqual(ticker.tickers, ("SPGI",))
        cik = parse_collection({**PAYLOAD, "ticker": "64040"})
        self.assertEqual((cik.tickers, cik.ciks), ((), ("64040",)))

    def test_invalid_inputs_are_rejected_before_starting_a_job(self):
        invalid = [{"ticker": "../../private"}, {"ticker": "0"}, {"ticker": None},
                   {"forms": []}, {"forms": "10-K"}, {"forms": ["8-K"]},
                   {"since": "bad date"}, {"since": "2026-10-15"},
                   {"max_per_form": 0}, {"max_per_form": True}, {"refresh_documents": "yes"}]
        for change in invalid:
            with self.subTest(change=change), self.assertRaises((RequestError, SecError, ValueError)):
                parse_collection({**PAYLOAD, **change})

    def test_missing_contact_is_reported_without_attempting_network_access(self):
        with tempfile.TemporaryDirectory() as folder:
            service = CollectorService(folder)
            with self.assertRaisesRegex(RequestError, "SEC_USER_AGENT"):
                service.start(PAYLOAD)
            self.assertEqual(service.jobs, {})


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.service = CollectorService(self.root, client_factory=fixture_client)

    def tearDown(self):
        self.temp.cleanup()

    def test_collection_persists_documents_and_reruns_reuse_them(self):
        first = finish(self.service, self.service.start(PAYLOAD)["id"])
        self.assertEqual((first["state"], first["report"]["processed"]), ("complete", 2))
        library = self.service.library()
        self.assertEqual(len(library["filings"]), 2)
        record = library["filings"][0]
        self.assertEqual(record["data_kind"], "synthetic_fixture")
        body, _, metadata = self.service.document(record["cik"], record["accession"], "text")
        self.assertIn(b"SYNTHETIC", body)
        self.assertEqual(metadata["form"], record["form"])
        rerun = finish(self.service, self.service.start(PAYLOAD)["id"])
        self.assertEqual((rerun["report"]["processed"], rerun["report"]["already_ready"]), (0, 2))

    def test_corrupt_documents_are_not_served_as_valid(self):
        finish(self.service, self.service.start(PAYLOAD)["id"])
        record = self.service.library()["filings"][0]
        path = self.root / "filings" / record["cik"] / record["accession"] / "text.txt"
        path.write_text("damaged")
        with self.assertRaisesRegex(RequestError, "collected again"):
            self.service.document(record["cik"], record["accession"], "text")

    def test_one_collection_runs_at_a_time(self):
        entered, release = threading.Event(), threading.Event()
        def delayed(root):
            entered.set()
            release.wait(timeout=3)
            return fixture_client(root)
        service = CollectorService(self.root, client_factory=delayed)
        job = service.start(PAYLOAD)
        self.assertTrue(entered.wait(timeout=1))
        try:
            with self.assertRaises(BusyError):
                service.start(PAYLOAD)
        finally:
            release.set()
        self.assertEqual(finish(service, job["id"])["state"], "complete")

    def test_document_identifiers_cannot_escape_the_library(self):
        for cik, accession, kind in [("../", "x", "text"),
                                      ("0000064040", "../../secret", "text"),
                                      ("0000064040", "0000064040-26-900001", "secret")]:
            with self.subTest(kind=kind), self.assertRaises(RequestError):
                self.service.document(cik, accession, kind)

    def test_back_to_back_collections_keep_the_same_request_budget(self):
        fixture = FixtureTransport()
        service = CollectorService(self.root, user_agent="Azalea contact@nyu.edu")
        service.limiter = RateLimiter(2, clock=fixture.clock.now, sleep=fixture.clock.sleep)
        with patch("azalea_edgar.web.SecClient", side_effect=lambda *a, **k: fixture_client(self.root, fixture)):
            finish(service, service.start(PAYLOAD)["id"])
            first_count = len(fixture.calls)
            finish(service, service.start(PAYLOAD)["id"])
        self.assertGreater(len(fixture.calls), first_count)
        times = [call["at"] for call in fixture.calls]
        self.assertTrue(all(b - a >= 0.5 for a, b in zip(times, times[1:])))


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service = CollectorService(self.temp.name, client_factory=fixture_client)
        handler = make_handler(self.service)
        handler.log_message = lambda *args: None
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def request(self, path, body=None, origin=None):
        headers = {}
        if body is not None:
            body = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        if origin:
            headers["Origin"] = origin
        request = urllib.request.Request(self.url + path, body, headers)
        try:
            response = urllib.request.urlopen(request, timeout=3)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, response.headers, response.read()

    def test_english_application_and_empty_library_are_available(self):
        code, headers, body = self.request("/")
        self.assertEqual(code, 200)
        self.assertIn(b'lang="en"', body)
        self.assertIn(b"Collect filings", body)
        self.assertIn("default-src 'self'", headers["Content-Security-Policy"])
        code, _, body = self.request("/api/library")
        self.assertEqual(json.loads(body)["filings"], [])

    def test_http_collection_and_text_preview_use_the_stored_artifacts(self):
        code, _, body = self.request("/api/collect", PAYLOAD, self.url)
        self.assertEqual(code, 202)
        job = finish(self.service, json.loads(body)["id"])
        self.assertEqual(job["state"], "complete")
        _, _, body = self.request("/api/library")
        record = json.loads(body)["filings"][0]
        base = f'/documents/{record["cik"]}/{record["accession"]}'
        code, _, body = self.request(base + "/text")
        self.assertEqual(code, 200)
        self.assertIn("SYNTHETIC", json.loads(body)["text"])
        code, headers, _ = self.request(base + "/raw")
        self.assertEqual(headers["Content-Type"], "application/octet-stream")
        self.assertTrue(headers["Content-Disposition"].startswith("attachment;"))

    def test_preview_keeps_sections_beyond_the_old_character_limit(self):
        text = "SYNTHETIC TEST DATA\n" + "Introduction. " * 4500
        text += "\nCompetition\n\nSynthetic Rival provides competing launch services.\nEnd of report."
        base = "/documents/0000064040/0000064040-26-900001"
        with patch.object(self.service, "document", return_value=(
                text.encode(), "text.txt", {"characters": len(text)})):
            code, _, body = self.request(base + "/text")
            self.assertEqual(code, 200)
            preview = json.loads(body)
            self.assertEqual(preview["text"], text)
            self.assertFalse(preview["truncated"])
            code, headers, body = self.request(base + "/text?download=1")
            self.assertEqual(body.decode(), text)
            self.assertTrue(headers["Content-Disposition"].startswith("attachment;"))

    def test_preview_includes_competitor_evidence_without_external_requests(self):
        text = "Competition\n\nOur competitors include Apex Launch and Beacon Space."
        base = "/documents/0000064040/0000064040-26-900001"
        with patch.object(self.service, "document", return_value=(
                text.encode(), "text.txt", {"company_name": "Fixture Issuer"})), patch(
                "azalea_edgar.client.UrllibTransport.__call__", side_effect=AssertionError("Unexpected external request")) as network:
            code, _, body = self.request(base + "/text")
        self.assertEqual(code, 200)
        network.assert_not_called()
        result = json.loads(body)["competitors"]
        self.assertEqual([c["name"] for c in result["candidates"]], ["Apex Launch", "Beacon Space"])
        group = result["groups"][0]
        self.assertEqual(text[group["start"]:group["end"]], group["quote"])
        self.assertFalse(result["directory_available"])

    def test_cross_origin_requests_cannot_start_collection(self):
        code, _, _ = self.request("/api/collect", PAYLOAD, "https://unrelated.example")
        self.assertEqual(code, 403)
        self.assertEqual(self.service.jobs, {})

    def test_invalid_dates_are_json_errors_not_dropped_connections(self):
        code, _, body = self.request("/api/collect", {**PAYLOAD, "since": "yesterday"})
        self.assertEqual(code, 400)
        self.assertIn("date", json.loads(body)["error"].lower())

    def test_private_files_and_unknown_jobs_are_not_exposed(self):
        for path in ("/.env.local", "/../.env.local", "/web.py", "/api/jobs/" + "a" * 32):
            with self.subTest(path=path):
                code, _, _ = self.request(path)
                self.assertEqual(code, 404)


if __name__ == "__main__":
    unittest.main()
