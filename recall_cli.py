#!/usr/bin/env python3
"""Standalone operator CLI for Hermes Recall archives."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from store import RecallStore


def _default_db_path() -> Path:
    hermes_home = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")).expanduser()
    return hermes_home / "recall_memory.sqlite"


def _print(payload: Any, *, as_json: bool) -> None:
    if as_json or isinstance(payload, (dict, list)):
        print(json.dumps(payload, ensure_ascii=False, indent=None if as_json else 2))
    else:
        print(payload)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect and manage a Hermes Recall SQLite archive.")
    parser.add_argument("--db", default=str(_default_db_path()), help="Path to recall_memory.sqlite")
    sub = parser.add_subparsers(dest="command", required=True)

    stats = sub.add_parser("stats", help="Print archive counts and audit health")
    stats.add_argument("--json", action="store_true", help="Emit compact JSON")

    search = sub.add_parser("search", help="Search archive observations")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=5)
    search.add_argument("--scope")
    search.add_argument("--project-path")
    search.add_argument("--json", action="store_true", help="Emit compact JSON")

    current = sub.add_parser("current", help="List current active, unexpired, non-superseded observations")
    current.add_argument("--limit", type=int, default=50)
    current.add_argument("--scope")
    current.add_argument("--project-path")
    current.add_argument("--json", action="store_true", help="Emit compact JSON")

    verify = sub.add_parser("verify", help="Verify the audit hash chain")
    verify.add_argument("--json", action="store_true", help="Emit compact JSON")

    diagnose = sub.add_parser("diagnose", help="Run Recall operator diagnostics")
    diagnose.add_argument("--json", action="store_true", help="Emit compact JSON")

    sub.add_parser("export", help="Export archive JSON to stdout")

    import_cmd = sub.add_parser("import", help="Import archive JSON from a file or stdin")
    import_cmd.add_argument("file", nargs="?", help="JSON file path; stdin if omitted")
    import_cmd.add_argument("--json", action="store_true", help="Emit compact JSON summary")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    store = RecallStore(args.db)
    try:
        if args.command == "stats":
            _print(store.archive_stats(), as_json=args.json)
        elif args.command == "search":
            payload = {
                "results": store.search_observations(
                    args.query,
                    limit=args.limit,
                    scope=args.scope,
                    project_path=args.project_path,
                )
            }
            _print(payload, as_json=args.json)
        elif args.command == "current":
            payload = {
                "results": store.current_observations(
                    limit=args.limit,
                    scope=args.scope,
                    project_path=args.project_path,
                ),
                "trust": "lower-trust archive evidence; built-in MEMORY.md/USER.md remain authoritative",
            }
            _print(payload, as_json=args.json)
        elif args.command == "verify":
            from audit import verify_audit_chain

            _print(verify_audit_chain(store.conn), as_json=args.json)
        elif args.command == "diagnose":
            _print(store.diagnose(), as_json=args.json)
        elif args.command == "export":
            print(json.dumps(store.export_archive(), ensure_ascii=False))
        elif args.command == "import":
            text = Path(args.file).read_text(encoding="utf-8") if args.file else sys.stdin.read()
            summary = store.import_archive(json.loads(text))
            _print(summary, as_json=args.json)
        else:  # pragma: no cover - argparse prevents this
            parser.error(f"Unknown command: {args.command}")
    finally:
        store.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
