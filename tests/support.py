"""Synthetic SEC-shaped inputs. All filing contents/accessions are FABRICATED."""

from __future__ import annotations

import copy
import json
from pathlib import Path

from azalea_edgar.client import Response, SecClient
from azalea_edgar.discovery import SUBMISSIONS_ROOT, TICKERS_URL, primary_url


COMPANIES = [("SPGI", "0000064040", "S&P Global Inc.", "7320"),
             ("MCO", "0001059556", "MOODY'S CORP", "7320"),
             ("MSFT", "0000789019", "MICROSOFT CORP", "7372")]


class FakeClock:
    def __init__(self):
        self.value = 1000.0
        self.sleeps = []

    def now(self):
        return self.value

    def sleep(self, delay):
        self.sleeps.append(delay)
        self.value += delay


def columns(rows):
    keys = ["accessionNumber", "filingDate", "reportDate", "acceptanceDateTime", "form",
            "primaryDocument", "isXBRL", "isInlineXBRL"]
    return {key: [row.get(key, "") for row in rows] for key in keys}


def filing(cik, ticker, form, serial, filing_date, report_date):
    suffix = "quarterly" if form.startswith("10-Q") else "annual"
    return {"accessionNumber": f"{cik}-26-{900000 + serial:06d}",
            "filingDate": filing_date, "reportDate": report_date,
            "acceptanceDateTime": filing_date + "T16:05:00.000Z", "form": form,
            "primaryDocument": f"{ticker.lower()}-SYNTHETIC-{suffix}-{serial}.htm",
            "isXBRL": 1, "isInlineXBRL": 1}


def html(ticker, form):
    paragraphs = "\n".join(f"<p>Synthetic paragraph {i}: company {ticker} describes example "
        "business operations, market risks, strategy and cash flow. These are invented test "
        "sentences and must never be used for company analysis.</p>" for i in range(12))
    return (f"<!doctype html><html><head><title>INVENTED TEST FILE</title>"
            "<style>.hidden { display:none }</style></head><body>"
            f"<h1>SYNTHETIC TEST FIXTURE — NOT AN SEC FILING: {ticker}</h1>"
            f"<h2>FORM {form}</h2><script>LEAK_SCRIPT</script>"
            "<ix:header><ix:hidden>LEAK_XBRL_HEADER</ix:hidden></ix:header>"
            "<p hidden>LEAK_HIDDEN</p><div style='display: none'>LEAK_CSS</div>"
            "<h3>Item 1. Business / Financial Statements</h3>" + paragraphs +
            "<table><tr><th>Example measure</th><th>Invented value</th></tr>"
            "<tr><td>Test revenue</td><td><ix:nonFraction>1,234</ix:nonFraction></td></tr>"
            "<tr><td>Test loss</td><td>(56.7)</td></tr></table>"
            "<p>Literal ampersand: A&amp;B. Visible dollar value: $9.50.</p></body></html>").encode()


class FixtureTransport:
    def __init__(self, clock=None):
        self.clock = clock or FakeClock()
        self.resources = {}
        self.calls = []
        self.overrides = {}
        mapping = {str(i): {"ticker": ticker, "cik_str": int(cik), "title": name}
                   for i, (ticker, cik, name, sic) in enumerate(COMPANIES)}
        self.set_json(TICKERS_URL, mapping)
        for ticker, cik, name, sic in COMPANIES:
            annual_date = "2026-07-29" if ticker == "MSFT" else "2026-02-10"
            annual_period = "2026-06-30" if ticker == "MSFT" else "2025-12-31"
            annual = filing(cik, ticker, "10-K", 1, annual_date, annual_period)
            quarter = filing(cik, ticker, "10-Q", 2, "2026-08-05", "2026-06-30")
            distractor = filing(cik, ticker, "8-K", 3, "2026-09-01", "2026-09-01")
            payload = {"_fixture": "SYNTHETIC; NOT AN OFFICIAL SEC API RESPONSE", "cik": cik,
                       "name": name, "tickers": [ticker], "exchanges": ["TEST EXCHANGE"],
                       "sic": sic, "sicDescription": "Synthetic classification example",
                       "fiscalYearEnd": "0630" if ticker == "MSFT" else "1231",
                       "filings": {"recent": columns([distractor, quarter, annual]), "files": []}}
            self.set_json(SUBMISSIONS_ROOT + "CIK" + cik + ".json", payload)
            self.add_document(cik, ticker, annual)
            self.add_document(cik, ticker, quarter)

    def set_json(self, url, value):
        self.resources[url] = Response(200, json.dumps(value, ensure_ascii=False).encode(),
                                       {"content-type": "application/json", "etag": '"fixture-v1"'})

    def get_json(self, url):
        return json.loads(self.resources[url].body)

    def add_document(self, cik, ticker, row):
        url = primary_url(cik, row["accessionNumber"], row["primaryDocument"])
        self.resources[url] = Response(200, html(ticker, row["form"]),
                                       {"content-type": "text/html; charset=utf-8"})
        return url

    def add_quarter(self, ticker="SPGI"):
        _, cik, _, _ = next(c for c in COMPANIES if c[0] == ticker)
        url = SUBMISSIONS_ROOT + "CIK" + cik + ".json"
        payload = self.get_json(url)
        row = filing(cik, ticker, "10-Q", 4, "2026-10-01", "2026-09-30")
        for key in payload["filings"]["recent"]:
            payload["filings"]["recent"][key].insert(0, row.get(key, ""))
        self.set_json(url, payload)
        return self.add_document(cik, ticker, row)

    def __call__(self, url, headers, timeout, limit):
        self.calls.append({"url": url, "headers": dict(headers), "at": self.clock.now()})
        if self.overrides.get(url):
            value = self.overrides[url].pop(0)
            if isinstance(value, Exception):
                raise value
            return copy.deepcopy(value)
        return copy.deepcopy(self.resources.get(url, Response(404, b"Not found", {})))


def fixture_client(root: Path, transport: FixtureTransport | None = None, **kwargs):
    transport = transport or FixtureTransport()
    return SecClient(root / "cache", "Azalea OFFLINE FIXTURES offline@azalea.test",
                     transport=transport, clock=transport.clock.now, sleep=transport.clock.sleep,
                     wall_clock=transport.clock.now, **kwargs)
