from datetime import datetime, timedelta, timezone
import sqlite3
from unittest.mock import patch

import pytest

import hermes_memory
import server


def _source():
    return {
        "source_domain": "core:wbom_employees",
        "source_record_id": "42",
        "source_timestamp": "2026-09-12T10:00:00+00:00",
        "source_version": "updated_at:2026-09-12T10:00:00Z",
        "source_classification": "authoritative",
    }


def test_persists_across_fresh_store_and_preserves_core_provenance(tmp_path):
    path = tmp_path / "memory.sqlite3"
    first = hermes_memory.MemoryStore(path)
    saved = first.put(
        kind="core_evidence", authority_class="evidence",
        subject_key="employee:42", topic="attendance",
        content={"summary": "present"}, sources=[_source()],
    )
    restarted = hermes_memory.MemoryStore(path)
    loaded = restarted.get(saved["record_id"])
    assert loaded["content"] == {"summary": "present"}
    assert loaded["authority_class"] == "evidence"
    assert loaded["sources"][0]["source_record_id"] == "42"
    assert loaded["sources"][0]["source_classification"] == "authoritative"
    assert loaded["core_authority"].startswith("external")
    assert path.stat().st_mode & 0o777 == 0o600


def test_stale_records_are_visible_as_stale_but_excluded_by_default(tmp_path):
    store = hermes_memory.MemoryStore(tmp_path / "memory.sqlite3")
    past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    saved = store.put(
        kind="derived_summary", authority_class="derived", topic="payroll",
        content={"summary": "old"}, stale_after=past,
    )
    assert store.get(saved["record_id"])["is_stale"] is True
    assert store.search("old") == []
    assert store.search("old", include_stale=True)[0]["is_stale"] is True


def test_never_accepts_authoritative_memory_or_credentials(tmp_path):
    store = hermes_memory.MemoryStore(tmp_path / "memory.sqlite3")
    with pytest.raises(ValueError, match="cannot be authoritative"):
        store.put(kind="topic_context", authority_class="authoritative", content={})
    with pytest.raises(ValueError, match="credential-like"):
        store.put(kind="topic_context", authority_class="memory", content={"api_key": "not-for-memory"})
    with pytest.raises(ValueError, match="credential-like"):
        store.put(kind="topic_context", authority_class="memory", content={}, search_text="Bearer abcdefgh1234")
    with pytest.raises(ValueError, match="source reference"):
        store.put(kind="core_evidence", authority_class="evidence", content={})
    with sqlite3.connect(store.path) as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO memory_records VALUES ('bad','topic_context','authoritative',NULL,NULL,'{}','',NULL,'now','now')"
        )


def test_mcp_tools_use_configured_persistent_store(tmp_path):
    with patch.object(hermes_memory, "DB_PATH", tmp_path / "memory.sqlite3"):
        saved = server.store_hermes_memory(
            "prior_answer", {"answer": "reviewed"}, "memory",
            subject_key="conversation:abc", topic="billing",
        )
        found = server.search_hermes_memory("reviewed")
        assert found["records"][0]["record_id"] == saved["record_id"]
        assert server.get_hermes_memory(saved["record_id"])["content"]["answer"] == "reviewed"


def test_all_authorized_memory_kinds_can_be_stored(tmp_path):
    store = hermes_memory.MemoryStore(tmp_path / "memory.sqlite3")
    for kind in sorted(hermes_memory.KINDS):
        sources = [_source()] if kind == "core_evidence" else []
        saved = store.put(
            kind=kind, authority_class="evidence" if sources else "memory",
            content={"kind": kind}, sources=sources,
        )
        assert saved["kind"] == kind
