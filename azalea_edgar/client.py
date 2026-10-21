"""A declared, paced HTTP client with verified disk cache and bounded retries."""

from __future__ import annotations

import hashlib
import http.client
import json
import math
import re
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Callable


class SecError(Exception):
    """A recoverable error recorded in a run report."""


class AccessBlocked(SecError):
    """Stop all subsequent network requests for this run."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def atomic_write(path: Path, body: bytes) -> None:
    """Use a sibling temporary file so replacement stays on the same filesystem."""
    import os
    import tempfile

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as file:
            file.write(body)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def write_json(path: Path, value: object) -> None:
    atomic_write(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode())


def validate_sec_url(url: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != "https" or parsed.netloc not in {"www.sec.gov", "data.sec.gov"}
            or parsed.query or parsed.fragment):
        raise SecError("Only canonical HTTPS SEC URLs are allowed")
    if ".." in parsed.path or "%2f" in parsed.path.lower() or "%2e" in parsed.path.lower():
        raise SecError("Unsafe SEC URL path")


class RateLimiter:
    """One shared limiter for BOTH SEC hosts and EVERY HTTP attempt; no bursts."""

    def __init__(self, rps: float = 2.0, *, clock=time.monotonic, sleep=time.sleep):
        if not math.isfinite(rps) or not 0 < rps <= 10:
            raise ValueError("requests-per-second must be in (0, 10]")
        self.interval = 1.0 / rps + 0.001
        self.clock, self.sleep = clock, sleep
        self.next_at = 0.0
        self.lock = threading.Lock()

    def acquire(self) -> None:
        with self.lock:
            while self.clock() < self.next_at:
                self.sleep(self.next_at - self.clock())
            self.next_at = self.clock() + self.interval


@dataclass
class Response:
    status: int
    body: bytes
    headers: dict[str, str]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    # A redirect is a separate HTTP request. Reject it rather than bypass pacing.
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class UrllibTransport:
    def __init__(self):
        self.opener = urllib.request.build_opener(NoRedirect())

    def __call__(self, url: str, headers: dict, timeout: float, limit: int) -> Response:
        request = urllib.request.Request(url, headers=headers)
        try:
            file = self.opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as error:
            # HTTPError also owns a response socket. Always close it.
            with error:
                return Response(error.code, error.read(min(limit + 1, 8192)),
                                {k.lower(): v for k, v in error.headers.items()})
        with file:
            response_headers = {k.lower(): v for k, v in file.headers.items()}
            declared_size = response_headers.get("content-length", "")
            if declared_size.isdigit() and int(declared_size) > limit:
                raise SecError(f"Response exceeds {limit} byte limit")
            try:
                body = file.read(limit + 1)
            except http.client.IncompleteRead as error:
                raise urllib.error.URLError("Truncated HTTP response") from error
            if len(body) > limit:
                raise SecError(f"Response exceeds {limit} byte limit")
            if declared_size.isdigit() and len(body) != int(declared_size):
                raise urllib.error.URLError("Truncated HTTP response: Content-Length mismatch")
            return Response(file.status, body, response_headers)


class SecClient:
    def __init__(self, cache_dir: Path, user_agent: str | None = None, *, rps=2.0,
                 retries=2, timeout=20.0, max_bytes=32 * 1024 * 1024,
                 offline=False, transport: Callable | None = None,
                 clock=time.monotonic, sleep=time.sleep, wall_clock=time.time):
        if retries < 0 or retries > 5 or timeout <= 0 or max_bytes <= 0:
            raise ValueError("Invalid HTTP limits")
        if not offline and transport is None:
            match = re.search(r"\b[\w.+-]+@([\w.-]+\.[a-zA-Z]{2,})\b", user_agent or "")
            if not match or match.group(1).lower().endswith((".invalid", ".test", "example.com")):
                raise ValueError("Set SEC_USER_AGENT to project name plus a real contact email")
        if "\r" in (user_agent or "") or "\n" in (user_agent or ""):
            raise ValueError("User-Agent must be one line")
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.user_agent = user_agent or "OFFLINE: no HTTP requests"
        self.transport = transport or UrllibTransport()
        self.response_origin = "injected_transport" if transport is not None else "sec_http"
        self.limiter = RateLimiter(rps, clock=clock, sleep=sleep)
        self.sleep, self.wall_clock = sleep, wall_clock
        self.retries, self.timeout, self.max_bytes = retries, timeout, max_bytes
        self.offline = offline
        self.blocked_reason: str | None = None
        self.stats = {"requests": 0, "http_successes": 0, "http_failures": 0,
                      "retries": 0, "cache_hits": 0, "not_modified": 0,
                      "cache_corruptions": 0}
        self.events: list[dict] = []

    def _paths(self, url):
        key = sha256(url.encode())
        return self.cache_dir / (key + ".body"), self.cache_dir / (key + ".json")

    def invalidate(self, url):
        for path in self._paths(url):
            path.unlink(missing_ok=True)

    def _cache(self, url):
        body_path, meta_path = self._paths(url)
        if not body_path.exists() or not meta_path.exists():
            return None
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            body = body_path.read_bytes()
            if (meta["url"] != url or sha256(body) != meta["sha256"]
                    or len(body) != meta["bytes"] or len(body) > self.max_bytes):
                raise ValueError("Cache checksum mismatch")
            float(meta["validated_at"])
            if (not isinstance(meta["headers"], dict) or not isinstance(meta["fetched_at"], str)
                    or not math.isfinite(float(meta["validated_at"]))):
                raise ValueError("Invalid cache metadata")
            return body, meta
        except (OSError, ValueError, KeyError, TypeError):
            self.stats["cache_corruptions"] += 1
            self.invalidate(url)
            return None

    def _save(self, url, body, headers):
        body_path, meta_path = self._paths(url)
        atomic_write(body_path, body)
        meta = {"url": url, "sha256": sha256(body), "bytes": len(body),
                "headers": headers, "fetched_at": utc_now(),
                "response_origin": self.response_origin,
                "validated_at": self.wall_clock()}
        write_json(meta_path, meta)
        return meta

    def _retry_delay(self, headers, attempt):
        value = headers.get("retry-after", "")
        if value:
            try:
                delay = float(value)
            except ValueError:
                try:
                    delay = parsedate_to_datetime(value).timestamp() - self.wall_clock()
                except (ValueError, TypeError, OverflowError):
                    delay = 0.0
            if math.isfinite(delay) and delay > 30:
                # Do not cap Retry-After and then send a request prematurely.
                self.blocked_reason = f"SEC requested Retry-After={value}; stop this run"
                raise AccessBlocked(self.blocked_reason)
            if math.isfinite(delay):
                return max(delay, 2.0 ** attempt)
        return 2.0 ** attempt

    def get(self, url: str, *, ttl: float | None = None) -> tuple[bytes, dict]:
        validate_sec_url(url)
        cached = self._cache(url)
        if cached and (self.offline or ttl is None or (ttl > 0 and
                self.wall_clock() - cached[1]["validated_at"] < ttl)):
            self.stats["cache_hits"] += 1
            return cached
        if self.offline:
            raise SecError(f"Offline cache miss: {url}")
        if self.blocked_reason:
            raise AccessBlocked(self.blocked_reason)
        headers = {"User-Agent": self.user_agent, "Accept-Encoding": "identity",
                   "Accept": "application/json, text/html, text/plain; q=0.9"}
        if cached:
            for header, validator in (("If-None-Match", "etag"),
                                      ("If-Modified-Since", "last-modified")):
                if cached[1]["headers"].get(validator):
                    headers[header] = cached[1]["headers"][validator]
        last_error = ""
        for attempt in range(self.retries + 1):
            self.limiter.acquire()
            self.stats["requests"] += 1
            try:
                response = self.transport(url, headers, self.timeout, self.max_bytes)
            except (urllib.error.URLError, TimeoutError, ConnectionError, socket.gaierror,
                    ssl.SSLError) as error:
                response = Response(0, b"", {})
                last_error = f"{type(error).__name__}: {error}"
            except SecError as error:
                self.stats["http_failures"] += 1
                self.events.append({"url": url, "attempt": attempt + 1, "status": None,
                                    "at": utc_now(), "error": str(error)})
                raise
            response.headers = {k.lower(): v for k, v in response.headers.items()}
            event = {"url": url, "attempt": attempt + 1, "status": response.status,
                     "at": utc_now(), "error": last_error if response.status == 0 else None}
            self.events.append(event)
            if response.status == 304 and cached:
                self.stats["http_successes"] += 1
                self.stats["not_modified"] += 1
                cached[1]["validated_at"] = self.wall_clock()
                cached[1]["headers"].update(response.headers)
                write_json(self._paths(url)[1], cached[1])
                return cached
            if response.status == 200:
                if not response.body or len(response.body) > self.max_bytes:
                    self.stats["http_failures"] += 1
                    raise SecError("Empty or oversized HTTP body")
                prefix = response.body[:8192].lower()
                if (b"<title>sec.gov |" in prefix and any(marker in prefix for marker in
                        (b"undeclared automated tool", b"request rate threshold exceeded",
                         b"your request originates from an undeclared"))):
                    self.blocked_reason = "SEC returned an automated-access block page"
                    self.stats["http_failures"] += 1
                    raise AccessBlocked(self.blocked_reason)
                if response.headers.get("content-encoding", "identity").lower() != "identity":
                    raise SecError("Unsupported Content-Encoding despite identity request")
                self.stats["http_successes"] += 1
                return response.body, self._save(url, response.body, response.headers)
            self.stats["http_failures"] += 1
            if response.status == 403:
                self.blocked_reason = f"HTTP 403 from {url}; no automatic retry or bypass"
                raise AccessBlocked(self.blocked_reason)
            retryable = response.status in {0, 429, 500, 502, 503, 504}
            if not retryable or attempt == self.retries:
                if response.status == 429:
                    self.blocked_reason = "HTTP 429 retry budget exhausted; stop this run"
                    raise AccessBlocked(self.blocked_reason)
                raise SecError(f"HTTP {response.status} from {url}" +
                               (f": {last_error}" if last_error else ""))
            delay = self._retry_delay(response.headers, attempt)
            self.stats["retries"] += 1
            self.sleep(delay)
        raise AssertionError("unreachable")

    def json(self, url: str, *, ttl: float | None = 0) -> dict:
        body, _ = self.get(url, ttl=ttl)
        try:
            value = json.loads(body)
            if not isinstance(value, dict):
                raise ValueError("Expected JSON object")
            return value
        except (ValueError, UnicodeError) as error:
            self.invalidate(url)
            raise SecError(f"Invalid SEC JSON from {url}: {error}") from error
