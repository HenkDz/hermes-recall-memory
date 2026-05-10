#!/usr/bin/env python3
"""Deterministic Recall archive stress probe for operator hardening.

Runs against an isolated temporary SQLite DB by default. It exercises bulk
writes, special-character FTS searches, redaction-at-rest, mixed concurrent
reads/writes, audit verification, export/import, and CLI diagnose/search.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path
from typing import Any


def _load_modules(repo: Path):
    sys.path.insert(0, str(repo))
    from audit import verify_audit_chain  # type: ignore
    from store import RecallStore  # type: ignore

    return RecallStore, verify_audit_chain


def run_probe(repo: Path, *, observations: int, episodes: int, audit_events: int, threads: int, thread_ops: int) -> dict[str, Any]:
    RecallStore, verify_audit_chain = _load_modules(repo)
    tmp = tempfile.TemporaryDirectory(prefix="recall-stress-")
    db = Path(tmp.name) / "recall_memory.sqlite"
    store = RecallStore(db)
    results: dict[str, Any] = {
        "ok": False,
        "db": str(db),
        "counts": {
            "observations": observations,
            "episodes": episodes,
            "audit_events": audit_events,
            "threads": threads,
            "thread_ops_each": thread_ops,
        },
        "failures": [],
    }

    def fail(step: str, exc: BaseException) -> None:
        results["failures"].append({"step": step, "error": repr(exc), "trace": traceback.format_exc(limit=5)})

    start = time.perf_counter()
    try:
        t = time.perf_counter()
        for i in range(observations):
            content = (
                f"RECALL_STRESS_MARKER_{i % 137} observation {i} "
                f"path=/mnt/e/Projects/AI/hermes-recall-memory/file_{i}.py "
                f"windows=E:\\Projects\\AI\\recall\\file_{i}.py "
                f"arabic=مرحبا-{i % 17} chinese=中文-{i % 19} "
                f"secret=sk-proj-SECRETSECRETSECRETSECRET{i:08d} "
                + ("x" * (i % 200))
            )
            store.add_observation(
                content=content,
                status="active" if i % 11 else "candidate",
                scope="project" if i % 3 else "global",
                confidence=(i % 100) / 100,
                importance=((i * 7) % 100) / 100,
                source_session_id=f"stress-session-{i % 31}",
                project_path=str(repo),
            )
        results["timing_bulk_observations_sec"] = round(time.perf_counter() - t, 3)

        old_id = store.add_observation(content="RECALL_STRESS_SUPERSEDE old value", status="active")
        new_id = store.add_observation(content="RECALL_STRESS_SUPERSEDE new value", status="active", supersedes=old_id)
        supersede_results = store.search_observations("RECALL_STRESS_SUPERSEDE", limit=10)
        if any(row["id"] == old_id for row in supersede_results) or not any(row["id"] == new_id for row in supersede_results):
            raise AssertionError("superseded search filtering failed")

        mirror_old = store.add_builtin_mirror_observation(
            content="Recall Memory: stress probe old source path.",
            type="fact",
            scope="profile",
            replace=False,
        )
        mirror_new = store.add_builtin_mirror_observation(
            content="Recall Memory: stress probe new source path.",
            type="fact",
            scope="profile",
            replace=True,
        )
        mirror_duplicate = store.add_builtin_mirror_observation(
            content="Recall Memory: stress probe new source path.",
            type="fact",
            scope="profile",
            replace=True,
        )
        if mirror_new != mirror_duplicate:
            raise AssertionError("exact builtin mirror duplicate was not deduped")
        current_mirrors = [row for row in store.current_observations(scope="profile", limit=10) if row["content"].startswith("Recall Memory: stress probe")]
        if len(current_mirrors) != 1 or current_mirrors[0].get("supersedes") != mirror_old:
            raise AssertionError("builtin mirror replacement did not supersede old row")

        t = time.perf_counter()
        for i in range(episodes):
            store.add_episode(
                session_id=f"episode-session-{i % 13}",
                project_path="/tmp/project",
                user_text="user " + ("u" * 6000) + f" KEY=secret-value-{i}",
                assistant_text="assistant " + ("a" * 10000) + f" Bearer abcdefghijklmnop{i}",
            )
        results["timing_episodes_sec"] = round(time.perf_counter() - t, 3)

        t = time.perf_counter()
        for i in range(audit_events):
            store.append_audit_event("stress", "write", f"target-{i % 23}", content_preview=f"preview {i} TOKEN=secret-token-{i}", metadata={"i": i})
        audit = verify_audit_chain(store.conn)
        if not audit.get("ok"):
            raise AssertionError(f"audit verify failed: {audit}")
        results["timing_audit_sec"] = round(time.perf_counter() - t, 3)
        results["audit"] = audit

        special_queries = [
            r"E:\Projects\AI\paperclip\foo:bar \"quoted\"",
            "/mnt/e/Projects/AI/hermes-agent/plugins/memory/recall",
            "THREAD-LIVE-684 OR weird:syntax -minus +plus (paren)",
            "مرحبا ذاكرة recall",
            "中文 memory test",
            "sk-proj-THISSHOULDBEREDACTED1234567890",
            "",
            "the a an only was what your",
        ]
        results["special_query_counts"] = {query: len(store.search_observations(query, limit=20)) for query in special_queries}

        leaks = {
            "observations_secret_hits": store.conn.execute("SELECT COUNT(*) FROM observations WHERE content LIKE '%sk-proj-SECRETSECRET%' OR redacted_content LIKE '%sk-proj-SECRETSECRET%'").fetchone()[0],
            "episodes_secret_hits": store.conn.execute("SELECT COUNT(*) FROM episodes WHERE user_text LIKE '%secret-value%' OR assistant_text LIKE '%Bearer abcdef%'").fetchone()[0],
            "audit_secret_hits": store.conn.execute("SELECT COUNT(*) FROM audit_events WHERE content_preview LIKE '%secret-token%'").fetchone()[0],
        }
        if any(leaks.values()):
            raise AssertionError({"redaction_leaks": leaks})
        results["redaction_at_rest"] = leaks

        t = time.perf_counter()
        errors = []

        def worker(tid: int) -> None:
            try:
                for j in range(thread_ops):
                    if j % 4 == 0:
                        store.add_observation(content=f"RECALL_STRESS_CONCURRENT thread={tid} j={j}", status="active")
                    elif j % 4 == 1:
                        store.search_observations(f"RECALL_STRESS_MARKER_{j % 137}", limit=5)
                    elif j % 4 == 2:
                        store.current_observations(limit=10)
                    else:
                        store.append_audit_event("stress-thread", "op", f"{tid}-{j}", "content")
            except Exception as exc:  # pragma: no cover - reported in output
                errors.append((tid, repr(exc), traceback.format_exc(limit=3)))

        workers = [threading.Thread(target=worker, args=(idx,)) for idx in range(threads)]
        for worker_thread in workers:
            worker_thread.start()
        for worker_thread in workers:
            worker_thread.join()
        if errors:
            raise AssertionError({"concurrency_errors": errors[:3], "count": len(errors)})
        results["timing_concurrency_sec"] = round(time.perf_counter() - t, 3)

        t = time.perf_counter()
        payload = store.export_archive()
        db2 = Path(tmp.name) / "imported.sqlite"
        store2 = RecallStore(db2)
        summary = store2.import_archive(payload)
        audit2 = verify_audit_chain(store2.conn)
        stats = store.archive_stats()
        stats2 = store2.archive_stats()
        if stats["episode_count"] != stats2["episode_count"]:
            raise AssertionError(("episode count mismatch", stats["episode_count"], stats2["episode_count"]))
        if sum(stats["observations_by_status"].values()) != sum(stats2["observations_by_status"].values()):
            raise AssertionError(("observation count mismatch", stats["observations_by_status"], stats2["observations_by_status"]))
        if not audit2.get("ok"):
            raise AssertionError(f"imported audit verify failed: {audit2}")
        results["timing_roundtrip_sec"] = round(time.perf_counter() - t, 3)
        results["roundtrip"] = {"export_bytes": len(json.dumps(payload, ensure_ascii=False)), "import_summary": summary, "imported_audit_count": audit2.get("count")}

        store.close()
        store2.close()
        diag = json.loads(subprocess.run([sys.executable, str(repo / "recall_cli.py"), "--db", str(db), "diagnose", "--json"], text=True, capture_output=True, check=True).stdout)
        search = json.loads(subprocess.run([sys.executable, str(repo / "recall_cli.py"), "--db", str(db), "search", "RECALL_STRESS_MARKER_42", "--limit", "5", "--json"], text=True, capture_output=True, check=True).stdout)
        if not diag.get("ok") or not search.get("results"):
            raise AssertionError({"diag": diag, "search": search})
        results["cli"] = {"diagnose_ok": diag.get("ok"), "search_count": len(search["results"])}
        results["ok"] = True
    except Exception as exc:  # pragma: no cover - probe reports failures in JSON
        fail("main", exc)
    finally:
        try:
            store.close()
        except Exception:
            pass

    results["total_seconds"] = round(time.perf_counter() - start, 3)
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run deterministic Recall archive stress checks against an isolated DB.")
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parents[1]), help="Path to hermes-recall-memory repo")
    parser.add_argument("--observations", type=int, default=1000)
    parser.add_argument("--episodes", type=int, default=120)
    parser.add_argument("--audit-events", type=int, default=300)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--thread-ops", type=int, default=80)
    args = parser.parse_args(argv)

    payload = run_probe(
        Path(args.repo).resolve(),
        observations=args.observations,
        episodes=args.episodes,
        audit_events=args.audit_events,
        threads=args.threads,
        thread_ops=args.thread_ops,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
