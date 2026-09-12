"""Persistent derived memory/evidence store owned by Core-Hermes."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

KINDS = frozenset({
    "conversation_memory", "topic_context", "prior_answer", "core_evidence",
    "report_metadata", "business_entity_ref", "retrieval_index",
    "derived_summary", "review_candidate", "sync_state",
})
AUTHORITY_CLASSES = frozenset({"derived", "memory", "cache", "evidence"})
SOURCE_CLASSES = frozenset({"authoritative", "derived", "evidence"})
DB_PATH = Path(os.environ.get(
    "HERMES_MEMORY_DB_PATH",
    Path.home() / ".local" / "share" / "fazle-hermes" / "memory.sqlite3",
))

_SECRET_KEY = re.compile(r"(^|_)(api_?key|password|passwd|secret|token|cookie|authorization|private_?key)($|_)", re.I)
_SECRET_TEXT = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----|\bBearer\s+[A-Za-z0-9._~+/=-]{8,}|\bsk-[A-Za-z0-9_-]{12,}", re.I)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _timestamp(value: str | None) -> str | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamps must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat()


def _reject_secrets(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if _SECRET_KEY.search(str(key)) and child not in (None, "", False, [], {}):
                raise ValueError("credential-like fields cannot be stored in Hermes memory")
            _reject_secrets(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _reject_secrets(child)
    elif isinstance(value, str) and _SECRET_TEXT.search(value):
        raise ValueError("credential-like content cannot be stored in Hermes memory")


class MemoryStore:
    def __init__(self, path: str | Path = DB_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS memory_records (
                    record_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    authority_class TEXT NOT NULL CHECK(authority_class IN ('derived','memory','cache','evidence')),
                    subject_key TEXT,
                    topic TEXT,
                    content_json TEXT NOT NULL,
                    search_text TEXT NOT NULL DEFAULT '',
                    stale_after TEXT,
                    retrieved_at TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_memory_kind_subject ON memory_records(kind, subject_key);
                CREATE INDEX IF NOT EXISTS ix_memory_stale_after ON memory_records(stale_after);
                CREATE TABLE IF NOT EXISTS memory_sources (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    record_id TEXT NOT NULL REFERENCES memory_records(record_id) ON DELETE CASCADE,
                    source_domain TEXT NOT NULL,
                    source_record_id TEXT,
                    source_timestamp TEXT,
                    source_version TEXT,
                    retrieved_at TEXT NOT NULL,
                    source_classification TEXT NOT NULL CHECK(source_classification IN ('authoritative','derived','evidence'))
                );
                CREATE INDEX IF NOT EXISTS ix_memory_source_ref
                    ON memory_sources(source_domain, source_record_id);
            """)
        os.chmod(self.path, 0o600)

    def put(
        self, *, kind: str, content: Any, authority_class: str,
        subject_key: str | None = None, topic: str | None = None,
        search_text: str = "", stale_after: str | None = None,
        retrieved_at: str | None = None, sources: list[dict] | None = None,
    ) -> dict:
        if kind not in KINDS:
            raise ValueError("unsupported memory kind")
        if authority_class not in AUTHORITY_CLASSES:
            raise ValueError("Hermes memory cannot be authoritative")
        sources = sources or []
        if kind == "core_evidence" and not sources:
            raise ValueError("core_evidence requires at least one source reference")
        _reject_secrets({
            "content": content, "sources": sources, "subject": subject_key,
            "topic": topic, "search_text": search_text,
        })
        now = _now()
        retrieved = _timestamp(retrieved_at) or now
        stale = _timestamp(stale_after)
        record_id = str(uuid.uuid4())
        encoded = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        normalized_sources = []
        for source in sources:
            source_domain = str(source.get("source_domain") or "").strip()
            classification = str(source.get("source_classification") or "").strip()
            if not source_domain or classification not in SOURCE_CLASSES:
                raise ValueError("source_domain and valid source_classification are required")
            normalized_sources.append({
                "source_domain": source_domain,
                "source_record_id": None if source.get("source_record_id") is None else str(source["source_record_id"]),
                "source_timestamp": _timestamp(source.get("source_timestamp")),
                "source_version": None if source.get("source_version") is None else str(source["source_version"]),
                "retrieved_at": _timestamp(source.get("retrieved_at")) or retrieved,
                "source_classification": classification,
            })
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO memory_records VALUES (?,?,?,?,?,?,?,?,?,?)",
                (record_id, kind, authority_class, subject_key, topic, encoded,
                 search_text, stale, retrieved, now),
            )
            conn.executemany(
                "INSERT INTO memory_sources(record_id,source_domain,source_record_id,source_timestamp,source_version,retrieved_at,source_classification) VALUES (?,?,?,?,?,?,?)",
                [(record_id, s["source_domain"], s["source_record_id"], s["source_timestamp"],
                  s["source_version"], s["retrieved_at"], s["source_classification"])
                 for s in normalized_sources],
            )
        return self.get(record_id)

    def get(self, record_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM memory_records WHERE record_id=?", (record_id,)).fetchone()
            if row is None:
                return None
            sources = conn.execute(
                "SELECT source_domain,source_record_id,source_timestamp,source_version,retrieved_at,source_classification FROM memory_sources WHERE record_id=? ORDER BY id",
                (record_id,),
            ).fetchall()
        return self._project(row, sources)

    def search(
        self, query: str = "", *, kind: str | None = None,
        subject_key: str | None = None, include_stale: bool = False,
        limit: int = 50,
    ) -> list[dict]:
        if kind is not None and kind not in KINDS:
            raise ValueError("unsupported memory kind")
        limit = max(1, min(int(limit), 500))
        clauses, args = [], []
        if kind:
            clauses.append("kind=?"); args.append(kind)
        if subject_key:
            clauses.append("subject_key=?"); args.append(subject_key)
        if query:
            clauses.append("(topic LIKE ? OR subject_key LIKE ? OR search_text LIKE ? OR content_json LIKE ?)")
            term = f"%{query}%"; args.extend([term] * 4)
        if not include_stale:
            clauses.append("(stale_after IS NULL OR stale_after>?)"); args.append(_now())
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM memory_records{where} ORDER BY created_at DESC LIMIT ?",
                (*args, limit),
            ).fetchall()
        return [self.get(row["record_id"]) for row in rows]

    @staticmethod
    def _project(row: sqlite3.Row, sources: list[sqlite3.Row]) -> dict:
        data = dict(row)
        data["content"] = json.loads(data.pop("content_json"))
        stale_after = data.get("stale_after")
        data["is_stale"] = bool(stale_after and stale_after <= _now())
        data["sources"] = [dict(source) for source in sources]
        data["core_authority"] = "external; Hermes record is never authoritative"
        return data


def store() -> MemoryStore:
    return MemoryStore(DB_PATH)
