"""SQLite persistence for the Recall memory provider."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .audit import hash_event, verify_audit_chain
from .redaction import redact_text
from .schema import SCHEMA_SQL, SCHEMA_VERSION


_QUERY_STOPWORDS = {
    "a",
    "an",
    "are",
    "is",
    "if",
    "needed",
    "only",
    "reply",
    "the",
    "using",
    "was",
    "what",
    "your",
}


def _query_terms(query: str) -> list[str]:
    """Extract high-signal, FTS-safe query terms from user text."""
    import re

    terms = re.findall(r"[\w.-]+", query.lower(), flags=re.UNICODE)
    return [term for term in terms if term and term not in _QUERY_STOPWORDS]


def _fts_query(query: str) -> str:
    """Convert arbitrary user text into a safe FTS5 query.

    Raw paths like ``E:\\Projects`` or ``/mnt/e`` contain FTS syntax
    characters. Tokenize and quote terms so search never raises syntax errors.
    """
    quoted = [f'"{term.replace(chr(34), chr(34) + chr(34))}"' for term in _query_terms(query)]
    return " OR ".join(quoted)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class RecallStore:
    """Profile-scoped SQLite store for episodes, observations, and audit events."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self.conn.executescript(SCHEMA_SQL)
            self.conn.execute(
                "INSERT OR REPLACE INTO schema_meta(key, value) VALUES('schema_version', ?)",
                (SCHEMA_VERSION,),
            )
            self.conn.commit()

    def close(self) -> None:
        with self._lock:
            self.conn.close()

    def add_episode(
        self,
        *,
        session_id: str,
        project_path: str,
        user_text: str,
        assistant_text: str,
    ) -> str:
        episode_id = str(uuid.uuid4())
        safe_user_text = redact_text(user_text)[:4000]
        safe_assistant_text = redact_text(assistant_text)[:8000]
        with self._lock:
            self.conn.execute(
                """
                INSERT INTO episodes(id, session_id, project_path, user_text, assistant_text, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (episode_id, session_id, project_path, safe_user_text, safe_assistant_text, utc_now()),
            )
            self.conn.commit()
        return episode_id

    def add_observation(
        self,
        *,
        content: str,
        type: str = "fact",
        scope: str = "project",
        trust_level: str = "archive",
        confidence: float = 0.5,
        importance: float = 0.5,
        status: str = "active",
        source_session_id: str = "",
        project_path: str = "",
        expires_at: str | None = None,
        supersedes: str | None = None,
    ) -> str:
        observation_id = str(uuid.uuid4())
        safe_content = redact_text(content)
        with self._lock:
            self.conn.execute(
                """
                INSERT INTO observations(
                    id, type, scope, trust_level, confidence, importance, status,
                    content, redacted_content, source_session_id, project_path,
                    created_at, expires_at, supersedes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    observation_id,
                    type,
                    scope,
                    trust_level,
                    float(confidence),
                    float(importance),
                    status,
                    safe_content,
                    safe_content,
                    source_session_id,
                    project_path,
                    utc_now(),
                    expires_at,
                    supersedes,
                ),
            )
            self.conn.commit()
        return observation_id

    def get_observation(self, observation_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self.conn.execute("SELECT * FROM observations WHERE id=?", (observation_id,)).fetchone()
        return dict(row) if row else None

    def mark_observation_status(self, observation_id: str, status: str) -> bool:
        with self._lock:
            cur = self.conn.execute("UPDATE observations SET status=? WHERE id=?", (status, observation_id))
            self.conn.commit()
            return cur.rowcount > 0

    def list_candidates(
        self,
        *,
        status: str = "candidate",
        type: str | None = None,
        scope: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        clauses = ["status = ?"]
        params: list[Any] = [status]
        if type:
            clauses.append("type = ?")
            params.append(type)
        if scope:
            clauses.append("scope = ?")
            params.append(scope)
        params.append(int(limit))
        where = " AND ".join(clauses)
        with self._lock:
            rows = self.conn.execute(
                f"SELECT * FROM observations WHERE {where} ORDER BY importance DESC, created_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [dict(r) for r in rows]

    def search_observations(
        self,
        query: str,
        *,
        limit: int = 5,
        scope: str | None = None,
        project_path: str | None = None,
    ) -> list[dict[str, Any]]:
        if not query.strip():
            return []
        fts = _fts_query(query)
        if not fts:
            return []
        clauses = ["o.status NOT IN ('rejected', 'deleted')"]
        params: list[Any] = [fts]
        if scope:
            clauses.append("o.scope = ?")
            params.append(scope)
        if project_path:
            clauses.append("o.project_path = ?")
            params.append(project_path)
        params.append(int(limit))
        where = " AND ".join(clauses)
        query_terms = _query_terms(query)
        with self._lock:
            rows = self.conn.execute(
                f"""
                SELECT o.*, bm25(observations_fts) AS score
                FROM observations_fts
                JOIN observations o ON o.rowid = observations_fts.rowid
                WHERE observations_fts MATCH ? AND {where}
                ORDER BY score ASC, o.importance DESC, o.confidence DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        results = []
        for row in rows:
            item = dict(row)
            searchable = " ".join(
                str(item.get(field) or "") for field in ("redacted_content", "content", "type", "scope", "project_path")
            ).lower()
            item["matched_query_terms"] = [term for term in query_terms if term in searchable]
            item["content"] = redact_text(item.get("redacted_content") or item.get("content") or "")
            item["redacted_content"] = item["content"]
            results.append(item)
        return results

    def append_audit_event(
        self,
        phase: str,
        operation: str,
        target: str,
        content_preview: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        with self._lock:
            prev = self.conn.execute(
                "SELECT event_hash FROM audit_events ORDER BY seq DESC LIMIT 1"
            ).fetchone()
            prev_hash = prev["event_hash"] if prev else ""
            event_id = str(uuid.uuid4())
            created_at = utc_now()
            metadata_json = json.dumps(metadata or {}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            preview = redact_text(content_preview)[:500]
            cur = self.conn.execute(
                """
                INSERT INTO audit_events(event_id, phase, operation, target, content_preview, prev_hash, event_hash, created_at, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, '', ?, ?)
                """,
                (event_id, phase, operation, target, preview, prev_hash, created_at, metadata_json),
            )
            seq = cur.lastrowid
            row = {
                "seq": seq,
                "event_id": event_id,
                "phase": phase,
                "operation": operation,
                "target": target,
                "content_preview": preview,
                "prev_hash": prev_hash,
                "created_at": created_at,
                "metadata_json": metadata_json,
            }
            event_hash = hash_event(row)
            self.conn.execute("UPDATE audit_events SET event_hash=? WHERE seq=?", (event_hash, seq))
            self.conn.commit()
        return event_id

    def audit_events(self, *, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM audit_events ORDER BY seq DESC LIMIT ?", (int(limit),)
            ).fetchall()
        return [dict(r) for r in rows]

    def archive_stats(self) -> dict[str, Any]:
        with self._lock:
            status_rows = self.conn.execute(
                "SELECT status, COUNT(*) AS count FROM observations GROUP BY status ORDER BY status"
            ).fetchall()
            type_rows = self.conn.execute(
                "SELECT type, COUNT(*) AS count FROM observations GROUP BY type ORDER BY type"
            ).fetchall()
            episode_count = self.conn.execute("SELECT COUNT(*) AS count FROM episodes").fetchone()["count"]
            observation_bounds = self.conn.execute(
                "SELECT MIN(created_at) AS oldest, MAX(created_at) AS newest FROM observations"
            ).fetchone()
            audit = verify_audit_chain(self.conn)
        db_size = self.db_path.stat().st_size if self.db_path.exists() else 0
        return {
            "db_path": str(self.db_path),
            "observations_by_status": {row["status"]: row["count"] for row in status_rows},
            "observations_by_type": {row["type"]: row["count"] for row in type_rows},
            "episode_count": episode_count,
            "audit": audit,
            "oldest_observation_at": observation_bounds["oldest"],
            "newest_observation_at": observation_bounds["newest"],
            "db_size_bytes": db_size,
        }
