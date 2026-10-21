from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

from .client import SecClient, SecError, write_json
from .pipeline import Config, sync
from .storage import Store, run_lock


def main(argv=None):
    parser = argparse.ArgumentParser(description="Azalea SEC 10-K/10-Q ingestion prototype")
    sub = parser.add_subparsers(dest="command", required=True)
    collect = sub.add_parser("sync", help="Discover, download, clean and update a local manifest")
    collect.add_argument("--tickers", nargs="+", default=None)
    collect.add_argument("--ciks", nargs="+", default=[])
    collect.add_argument("--forms", nargs="+", default=["10-K", "10-Q"])
    collect.add_argument("--since", default=(date.today() - timedelta(days=365)).isoformat())
    collect.add_argument("--until", default=date.today().isoformat())
    collect.add_argument("--max-per-form", type=int, default=1, help="latest N per company/form; 0 means all")
    collect.add_argument("--min-chars", type=int, default=1000)
    collect.add_argument("--data-dir", type=Path, default=Path("data"))
    collect.add_argument("--rps", type=float, default=2.0)
    collect.add_argument("--retries", type=int, default=2, help="additional attempts, maximum 5")
    collect.add_argument("--timeout", type=float, default=20)
    collect.add_argument("--max-mib", type=int, default=32)
    collect.add_argument("--submissions-ttl", type=float, default=0)
    collect.add_argument("--offline", action="store_true", help="verified cache only; never send HTTP")
    collect.add_argument("--refresh-documents", action="store_true", help="revalidate selected raw documents")
    audit = sub.add_parser("audit", help="Check manifest metadata, artifacts and hashes")
    audit.add_argument("--data-dir", type=Path, default=Path("data"))
    args = parser.parse_args(argv)
    try:
        if args.command == "audit":
            if not (args.data_dir / "manifest.sqlite").is_file():
                raise SecError("No manifest found; audit would have no evidence")
            with run_lock(args.data_dir):
                store = Store(args.data_dir)
                try:
                    result = store.audit()
                    write_json(args.data_dir / "audit.json", result)
                finally:
                    store.close()
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["checked"] and not result["invalid"] else 1
        tickers = tuple(args.tickers if args.tickers is not None else
                        [] if args.ciks else ["SPGI", "MCO", "MSFT"])
        config = Config(tickers=tickers, ciks=tuple(args.ciks), forms=tuple(args.forms),
                        since=args.since, until=args.until, max_per_form=args.max_per_form,
                        min_chars=args.min_chars, submissions_ttl=args.submissions_ttl,
                        refresh_documents=args.refresh_documents)
        config.validate()
        client = SecClient(args.data_dir / "cache", os.environ.get("SEC_USER_AGENT"),
                           rps=args.rps, retries=args.retries, timeout=args.timeout,
                           max_bytes=args.max_mib * 1024 * 1024, offline=args.offline)
        result = sync(args.data_dir, client, config, data_kind="offline_cached" if args.offline else "live_sec")
        summary = {k: result[k] for k in ("run_id", "status", "data_kind", "selected",
                    "processed", "already_ready", "failed", "metrics", "http", "errors")}
        summary["report"] = str((args.data_dir / "last_run.json").resolve())
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if result["status"] == "complete" else 1
    except (SecError, OSError, ValueError) as error:
        print(json.dumps({"status": "error", "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
