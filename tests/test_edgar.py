from __future__ import annotations

import json
import tempfile
import unittest
import urllib.error
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from unittest.mock import patch

from azalea_edgar.client import AccessBlocked, RateLimiter, Response, SecClient, SecError, UrllibTransport
from azalea_edgar.discovery import SUBMISSIONS_ROOT, TICKERS_URL, discover, normalize_cik, primary_url
from azalea_edgar.pipeline import Config, sync
from azalea_edgar.storage import Store, run_lock
from azalea_edgar.text import clean_document, text_issues
from tests.support import COMPANIES, FakeClock, FixtureTransport, columns, filing, fixture_client, html


class TemporaryTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.fixture = FixtureTransport()
        self.client = fixture_client(self.root, self.fixture)

    def run_sync(self, config=None):
        return sync(self.root, self.client, config or Config(), data_kind="synthetic_fixture")

    def records(self):
        store = Store(self.root)
        try:
            return store.records()
        finally:
            store.close()

    def document_url(self):
        return next(url for url in self.fixture.resources if "/Archives/" in url)


class ClientTests(TemporaryTest):
    def test_real_transport_requires_declared_nonplaceholder_contact(self):
        for value in (None, "Azalea", "Azalea contact@example.com", "Azalea user@fixture.invalid"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                SecClient(self.root / "unused", value)
        # This validates configuration only and sends no HTTP.
        SecClient(self.root / "unused", "Azalea configured-contact@nyu.edu")

    def test_pacing_no_bursts_and_sec_ceiling(self):
        clock = FakeClock()
        limiter = RateLimiter(10, clock=clock.now, sleep=clock.sleep)
        times = []
        for _ in range(30):
            limiter.acquire()
            times.append(clock.now())
        self.assertTrue(all(b - a >= 0.1 for a, b in zip(times, times[1:])))
        self.assertTrue(all(sum(start <= t < start + 1 for t in times) <= 10 for start in times))
        for invalid in (0, -1, 10.1, float("nan")):
            with self.assertRaises(ValueError):
                RateLimiter(invalid)

    def test_shared_host_limiter_and_user_agent(self):
        self.client.json(TICKERS_URL)
        self.client.json(SUBMISSIONS_ROOT + "CIK0000064040.json")
        calls = self.fixture.calls
        self.assertGreaterEqual(calls[1]["at"] - calls[0]["at"], 0.5)
        self.assertTrue(all(c["headers"]["User-Agent"] == self.client.user_agent for c in calls))

    def test_immutable_cache_avoids_http_and_verifies_checksum(self):
        url = self.document_url()
        body, _ = self.client.get(url)
        self.client.get(url)
        self.assertEqual(len(self.fixture.calls), 1)
        self.client._paths(url)[0].write_bytes(b"tampered")
        recovered, _ = self.client.get(url)
        self.assertEqual(recovered, body)
        self.assertEqual(self.client.stats["cache_corruptions"], 1)
        self.assertEqual(len(self.fixture.calls), 2)

    def test_conditional_304_reuses_verified_body(self):
        original = self.client.json(TICKERS_URL, ttl=0)
        self.fixture.overrides[TICKERS_URL] = [Response(304, b"", {"etag": '"fixture-v1"'})]
        self.assertEqual(self.client.json(TICKERS_URL, ttl=0), original)
        self.assertEqual(self.fixture.calls[-1]["headers"]["If-None-Match"], '"fixture-v1"')
        self.assertEqual(self.client.stats["not_modified"], 1)

    def test_429_retry_after_paces_the_retry(self):
        self.fixture.overrides[TICKERS_URL] = [Response(429, b"slow down", {"retry-after": "3"})]
        self.client.json(TICKERS_URL)
        self.assertEqual(self.client.stats["retries"], 1)
        self.assertGreaterEqual(self.fixture.calls[1]["at"] - self.fixture.calls[0]["at"], 3)

    def test_retry_after_http_date(self):
        clock = self.fixture.clock
        retry_at = format_datetime(datetime.fromtimestamp(clock.now() + 4, timezone.utc), usegmt=True)
        self.fixture.overrides[TICKERS_URL] = [Response(503, b"unavailable", {"Retry-After": retry_at})]
        self.client.json(TICKERS_URL)
        self.assertGreaterEqual(self.fixture.calls[1]["at"] - self.fixture.calls[0]["at"], 4)

    def test_long_retry_after_stops_instead_of_retrying_early(self):
        self.fixture.overrides[TICKERS_URL] = [Response(429, b"slow down", {"retry-after": "120"})]
        with self.assertRaises(AccessBlocked):
            self.client.json(TICKERS_URL)
        self.assertEqual(len(self.fixture.calls), 1)

    def test_403_circuit_breaker_stops_both_hosts(self):
        self.fixture.overrides[TICKERS_URL] = [Response(403, b"blocked", {})]
        with self.assertRaises(AccessBlocked):
            self.client.json(TICKERS_URL)
        with self.assertRaises(AccessBlocked):
            self.client.json(SUBMISSIONS_ROOT + "CIK0000064040.json")
        self.assertEqual(len(self.fixture.calls), 1)

    def test_transient_errors_retry_with_bounded_budget(self):
        error = urllib.error.URLError("synthetic connection failure")
        self.fixture.overrides[TICKERS_URL] = [error, Response(503, b"unavailable", {})]
        self.client.json(TICKERS_URL)
        self.assertEqual(self.client.stats["requests"], 3)
        self.assertEqual(self.client.stats["retries"], 2)
        self.fixture.overrides[TICKERS_URL] = [error, error, error, error]
        with self.assertRaises(SecError):
            self.client.json(TICKERS_URL, ttl=0)
        self.assertEqual(len(self.fixture.overrides[TICKERS_URL]), 1)

    def test_404_and_redirect_are_not_retried(self):
        for code in (404, 302):
            self.fixture.overrides[TICKERS_URL] = [Response(code, b"error", {})]
            before = len(self.fixture.calls)
            with self.assertRaises(SecError):
                self.client.json(TICKERS_URL)
            self.assertEqual(len(self.fixture.calls) - before, 1)

    def test_offline_mode_never_calls_transport(self):
        self.client.json(TICKERS_URL)
        offline = SecClient(self.root / "cache", offline=True)
        offline.transport = lambda *args: self.fail("Network invoked in offline mode")
        self.assertIn("0", offline.json(TICKERS_URL, ttl=0))
        with self.assertRaises(SecError):
            offline.json(SUBMISSIONS_ROOT + "CIK0000000001.json")
        self.assertEqual(offline.stats["requests"], 0)

    def test_invalid_json_is_not_cached(self):
        self.fixture.overrides[TICKERS_URL] = [Response(200, b"<html>not JSON</html>", {})]
        with self.assertRaises(SecError):
            self.client.json(TICKERS_URL)
        self.assertFalse(self.client._paths(TICKERS_URL)[0].exists())

    def test_external_and_traversal_urls_rejected_before_http(self):
        for url in ("http://www.sec.gov/x", "https://example.com/x", "https://sec.gov.evil/x",
                    "https://www.sec.gov/../x", "https://www.sec.gov/%2e%2e/x"):
            with self.subTest(url=url), self.assertRaises(SecError):
                self.client.get(url)
        self.assertEqual(self.fixture.calls, [])

    def test_fake_200_sec_block_page_opens_circuit(self):
        page = b"<html><title>SEC.gov | Your Request Originates from an Undeclared Automated Tool</title></html>"
        self.fixture.overrides[TICKERS_URL] = [Response(200, page, {})]
        with self.assertRaises(AccessBlocked):
            self.client.get(TICKERS_URL)
        self.assertFalse(self.client._paths(TICKERS_URL)[0].exists())

    def test_oversized_response_rejected(self):
        client = fixture_client(self.root, self.fixture, max_bytes=5)
        with self.assertRaises(SecError):
            client.get(TICKERS_URL)

    def test_damaged_cache_metadata_is_refetched(self):
        self.client.json(TICKERS_URL)
        _, meta_path = self.client._paths(TICKERS_URL)
        meta = json.loads(meta_path.read_text())
        del meta["headers"]
        meta_path.write_text(json.dumps(meta))
        self.client.json(TICKERS_URL)
        self.assertEqual((self.client.stats["cache_corruptions"], len(self.fixture.calls)), (1, 2))

    def test_urllib_truncated_http_body_is_retryable_and_closed(self):
        from io import BytesIO
        class TruncatedResponse(BytesIO):
            status = 200
            headers = {"Content-Length": "100"}
        response = TruncatedResponse(b"short")
        transport = UrllibTransport()
        with patch.object(transport.opener, "open", return_value=response):
            with self.assertRaises(urllib.error.URLError):
                transport(TICKERS_URL, {}, 20, 1024)
        self.assertTrue(response.closed)


class DiscoveryTests(TemporaryTest):
    def test_normalization_and_agent_accession_prefix(self):
        self.assertEqual(normalize_cik(64040), "0000064040")
        self.assertEqual(primary_url("64040", "0001193125-26-123456", "report.htm"),
            "https://www.sec.gov/Archives/edgar/data/64040/000119312526123456/report.htm")
        for invalid in ("../a.htm", "../../escape", "report.pdf", "file.htm?x=1"):
            with self.assertRaises(SecError):
                primary_url("64040", "0001193125-26-123456", invalid)

    def test_history_date_overlap_filter_and_deduplication(self):
        url = SUBMISSIONS_ROOT + "CIK0000064040.json"
        payload = self.fixture.get_json(url)
        name = "CIK0000064040-submissions-001.json"
        payload["filings"]["files"] = [
            {"name": name, "filingFrom": "2025-01-01", "filingTo": "2026-03-01"},
            {"name": "CIK0000064040-submissions-002.json", "filingFrom": "2020-01-01", "filingTo": "2021-01-01"}]
        self.fixture.set_json(url, payload)
        duplicate = {key: values[2] for key, values in payload["filings"]["recent"].items()}
        old = filing("0000064040", "SPGI", "10-K", 5, "2025-02-01", "2024-12-31")
        self.fixture.set_json(SUBMISSIONS_ROOT + name, columns([old, duplicate]))
        _, rows, result = discover(self.client, "64040", "SPGI", since="2025-01-01",
            until="2026-10-14", forms={"10-K", "10-Q"}, max_per_form=0)
        self.assertEqual(len(rows), 3)
        self.assertEqual(len({r["accession"] for r in rows}), 3)
        self.assertEqual(result["available_by_form"], {"10-K": 2, "10-Q": 1})
        self.assertFalse(any("002.json" in c["url"] for c in self.fixture.calls))

    def test_misaligned_columns_fail_instead_of_silent_truncation(self):
        url = SUBMISSIONS_ROOT + "CIK0000064040.json"
        payload = self.fixture.get_json(url)
        payload["filings"]["recent"]["primaryDocument"].pop()
        self.fixture.set_json(url, payload)
        with self.assertRaises(SecError):
            discover(self.client, "64040", "SPGI", since="2025-10-15", until="2026-10-14",
                     forms={"10-K", "10-Q"}, max_per_form=1)

    def test_issuer_identity_mismatch_rejected(self):
        url = SUBMISSIONS_ROOT + "CIK0000064040.json"
        payload = self.fixture.get_json(url)
        payload["cik"] = "789019"
        self.fixture.set_json(url, payload)
        with self.assertRaises(SecError):
            discover(self.client, "64040", "SPGI", since="2025-10-15", until="2026-10-14",
                     forms={"10-K", "10-Q"}, max_per_form=1)


class TextTests(unittest.TestCase):
    def test_hidden_content_removed_visible_numbers_and_table_cells_preserved(self):
        text, quality = clean_document(html("SPGI", "10-K"), "text/html; charset=utf-8", "fixture.htm")
        self.assertNotIn("LEAK_", text)
        self.assertNotIn("INVENTED TEST FILE", text)
        self.assertIn("Test revenue\t1,234", text)
        self.assertIn("Test loss\t(56.7)", text)
        self.assertIn("A&B", text)
        self.assertIn("$9.50", text)
        self.assertEqual(text_issues(text, "10-K"), [])
        self.assertGreater(quality["characters"], 1000)

    def test_cp1252_plain_text_decoding_is_explicit(self):
        text, quality = clean_document("FORM 10-Q\nItem 1. A £10 — result".encode("cp1252"),
                                        "text/plain", "filing.txt")
        self.assertIn("£10", text)
        self.assertEqual(quality["encoding"], "cp1252")
        self.assertTrue(quality["warnings"])

    def test_short_error_page_cannot_pass_quality_gate(self):
        text, _ = clean_document(b"<html>Access denied</html>", "text/html", "report.htm")
        self.assertEqual(len(text_issues(text, "10-K")), 3)

    def test_pdf_is_not_misparsed_as_text(self):
        with self.assertRaises(SecError):
            clean_document(b"%PDF-1.7\n", "application/pdf", "report.htm")


class PipelineTests(TemporaryTest):
    def test_first_sync_six_complete_idempotent_second_and_one_incremental(self):
        first = self.run_sync()
        self.assertEqual((first["status"], first["processed"], first["audit"]["valid"]), ("complete", 6, 6))
        self.assertEqual(first["metrics"]["company_form_coverage"], 1)
        calls_before = len(self.fixture.calls)
        second = self.run_sync()
        self.assertEqual((second["processed"], second["already_ready"]), (0, 6))
        self.assertTrue(all("/submissions/" in c["url"] for c in self.fixture.calls[calls_before:]))
        self.fixture.add_quarter()
        third = self.run_sync()
        self.assertEqual((third["processed"], third["already_ready"], third["audit"]["valid"]), (1, 5, 7))
        self.assertEqual(len(self.records()), 7)
        for record in self.records():
            self.assertEqual(record["artifact"]["data_kind"], "synthetic_fixture")

    def test_failed_download_counts_in_denominator_and_retries_next_run(self):
        url = self.document_url()
        self.fixture.overrides[url] = [Response(404, b"not found", {})]
        first = self.run_sync()
        self.assertEqual((first["processed"], first["failed"], first["status"]), (5, 1, "partial"))
        self.assertAlmostEqual(first["metrics"]["processing_success_rate"], 5 / 6)
        self.assertAlmostEqual(first["metrics"]["company_form_coverage"], 5 / 6)
        self.assertEqual(first["audit"]["invalid"], 1)
        second = self.run_sync()
        self.assertEqual((second["processed"], second["already_ready"], second["audit"]["invalid"]), (1, 5, 0))

    def test_failed_previous_quarter_retried_even_after_new_quarter_arrives(self):
        quarter_url = next(url for url in self.fixture.resources if "spgi-SYNTHETIC-quarterly" in url)
        self.fixture.overrides[quarter_url] = [Response(404, b"not found", {})]
        self.assertEqual(self.run_sync()["failed"], 1)
        self.fixture.add_quarter()
        result = self.run_sync()
        self.assertEqual((result["selected"], result["processed"], result["audit"]["valid"]), (7, 2, 7))

    def test_corrupt_artifacts_rebuilt_from_verified_cache(self):
        self.run_sync()
        records = self.records()
        for record, field in zip(records[:3], ("raw_path", "text_path", "metadata_path")):
            (self.root / record["artifact"][field]).write_bytes(b"corrupt")
        store = Store(self.root)
        self.assertEqual(store.audit()["invalid"], 3)
        store.close()
        calls_before = len(self.fixture.calls)
        report = self.run_sync()
        self.assertEqual((report["processed"], report["audit"]["invalid"]), (3, 0))
        self.assertFalse(any("/Archives/" in c["url"] for c in self.fixture.calls[calls_before:]))

    def test_bad_200_document_fails_and_invalidates_cache(self):
        url = self.document_url()
        self.fixture.overrides[url] = [Response(200, b"<html>temporary error</html>", {})]
        report = self.run_sync()
        self.assertEqual(report["failed"], 1)
        self.assertFalse(self.client._paths(url)[0].exists())
        self.assertEqual(self.run_sync()["processed"], 1)

    def test_unknown_ticker_does_not_prevent_other_companies(self):
        result = self.run_sync(Config(tickers=("UNKNOWN", "SPGI")))
        self.assertEqual((result["processed"], result["status"]), (2, "partial"))
        self.assertEqual(result["metrics"]["company_form_coverage"], 0.5)
        self.assertEqual(result["metrics"]["discovery_success_rate"], 0.5)

    def test_zero_discovery_is_not_reported_as_100_percent(self):
        result = self.run_sync(Config(since="2026-10-10", until="2026-10-14"))
        self.assertEqual(result["status"], "partial")
        self.assertIsNone(result["metrics"]["selected_ready_rate"])
        self.assertIsNone(result["audit"]["integrity_rate"])
        self.assertEqual(result["metrics"]["company_form_coverage"], 0)

    def test_amendment_accession_is_distinct_and_opt_in(self):
        cik = "0000064040"
        url = SUBMISSIONS_ROOT + "CIK" + cik + ".json"
        payload = self.fixture.get_json(url)
        amended = filing(cik, "SPGI", "10-K/A", 9, "2026-09-01", "2025-12-31")
        for key in payload["filings"]["recent"]:
            payload["filings"]["recent"][key].insert(0, amended.get(key, ""))
        self.fixture.set_json(url, payload)
        self.fixture.add_document(cik, "SPGI", amended)
        self.assertEqual(self.run_sync(Config(tickers=("SPGI",)))["processed"], 2)
        result = self.run_sync(Config(tickers=("SPGI",), forms=("10-K", "10-Q", "10-K/A")))
        self.assertEqual((result["processed"], result["already_ready"], result["audit"]["valid"]), (1, 2, 3))

    def test_explicit_cik_and_alias_targets_deduplicate(self):
        report = self.run_sync(Config(tickers=("SPGI", "spgi"), ciks=("64040",)))
        self.assertEqual((len(report["companies"]), report["processed"]), (1, 2))

    def test_blocked_sec_access_stops_further_network_requests(self):
        self.fixture.overrides[TICKERS_URL] = [Response(403, b"blocked", {})]
        result = self.run_sync()
        self.assertEqual((result["status"], result["selected"]), ("blocked", 0))
        self.assertEqual(len(self.fixture.calls), 1)
        self.assertEqual(result["metrics"]["company_form_coverage"], 0)

    def test_crash_pending_record_recovers_and_single_writer_lock(self):
        with run_lock(self.root):
            with self.assertRaises(SecError):
                with run_lock(self.root):
                    pass
        company, rows, _ = discover(self.client, "64040", "SPGI", since="2025-10-15",
                                   until="2026-10-14", forms={"10-K", "10-Q"}, max_per_form=1)
        store = Store(self.root)
        store.company(company)
        store.discover(rows[0])  # Simulate interruption before publishing artifacts.
        store.close()
        result = self.run_sync(Config(tickers=("SPGI",)))
        self.assertEqual((result["processed"], result["audit"]["valid"]), (2, 2))

    def test_explicit_refresh_detects_changed_document_same_accession(self):
        config = Config(tickers=("SPGI",))
        self.run_sync(config)
        url = self.document_url()
        original = next(r for r in self.records() if r["discovery"]["source_url"] == url)
        self.fixture.resources[url].body = self.fixture.resources[url].body.replace(b"1,234", b"4,321")
        self.assertEqual(self.run_sync(config)["processed"], 0)
        refreshed = self.run_sync(Config(tickers=("SPGI",), refresh_documents=True))
        updated = next(r for r in self.records() if r["discovery"]["source_url"] == url)
        self.assertEqual(refreshed["processed"], 2)
        self.assertNotEqual(updated["artifact"]["raw_sha256"], original["artifact"]["raw_sha256"])
        self.assertIn("4,321", (self.root / updated["artifact"]["text_path"]).read_text())

    def test_binary_error_body_is_not_permanently_cached(self):
        url = self.document_url()
        self.fixture.overrides[url] = [Response(200, b"%PDF-1.7\n", {"content-type": "application/pdf"})]
        self.assertEqual(self.run_sync()["failed"], 1)
        self.assertFalse(self.client._paths(url)[0].exists())
        self.assertEqual(self.run_sync()["processed"], 1)


if __name__ == "__main__":
    unittest.main()
