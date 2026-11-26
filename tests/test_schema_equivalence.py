"""schema.sql (fresh databases) and MIGRATIONS (existing ones) must build the same schema."""

import re
import sqlite3

import pytest

from comms_terminal import inbox
from schema_history import changed_since, downgrade
from test_email_reconcile import SCHEMA3  # frozen schema-3 database, the oldest live shape


def shape(db) -> dict:
    """Every table, index, trigger, and view, with SQL normalised (comments, spacing, quoting, case)."""
    def norm(sql):
        sql = re.sub(r"--[^\n]*", "", sql or "")
        sql = re.sub(r"\s+", " ", sql).replace('"', "").lower()
        return re.sub(r"\s*([(),=<>])\s*", r"\1", sql).strip()
    return {(r["type"], r["name"]): norm(r["sql"]) for r in db.execute(
        "SELECT type, name, sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'")}


def test_migrated_schema_matches_a_fresh_one(tmp_path):
    fresh = inbox.connect()
    inbox.migrate(fresh)
    old = inbox.connect(str(tmp_path / "v3.db"))
    old.executescript(SCHEMA3)
    inbox.migrate(old)
    want, got = shape(fresh), shape(old)
    assert sorted(want) == sorted(got)
    assert {k: v for k, v in got.items() if want[k] != v} == {}


def test_version_12_upgrade_matches_fresh_schema(monkeypatch):
    fresh, old = inbox.connect(), inbox.connect()
    try:
        inbox.migrate(fresh)
        old.executescript(SCHEMA3)
        with monkeypatch.context() as m:
            m.setattr(inbox, "SCHEMA_VERSION", 12)
            inbox.migrate(old)
        before = shape(old)
        inbox.migrate(old)
        assert shape(old) == shape(fresh)
        changed = changed_since(12)
        assert {k: v for k, v in shape(old).items() if k in before and k[1] not in changed} == \
            {k: v for k, v in before.items() if k[1] not in changed}
        assert "thread_id text references threads(id)" in shape(old)[("table", "composition_drafts")]
    finally:
        fresh.close()
        old.close()


def test_v50_promise_history_survives_and_remains_append_only():
    db = inbox.connect()
    inbox.migrate(db)
    fresh = shape(db)
    downgrade(db, 50)
    with db:
        db.execute("INSERT INTO promises (id, what, due_at, state, created_at)"
                   " VALUES ('promise', 'Send the checklist', 'due', 'OPEN', 'created')")
        db.execute("INSERT INTO promise_changes"
                   " (rowid, id, promise_id, at, change, detail, origin_kind, origin_ref, words)"
                   " VALUES (2, 'recorded', 'promise', 'at', 'recorded', '{}', 'words', NULL, 'I will send it'),"
                   " (9, 'changed', 'promise', 'at', 'changed', ?, 'tana', 'node', NULL)",
                   ('{ "before": {"work_minutes": 20}, "after": {"work_minutes": 30} }',))

    def history():
        return [tuple(r) for r in db.execute("SELECT rowid, * FROM promise_changes ORDER BY at, rowid")]

    before = history()
    sync_cursor = db.execute("SELECT max(seq) FROM changes").fetchone()[0]
    inbox.migrate(db)
    assert shape(db) == fresh
    assert history() == before
    assert db.execute("SELECT max(seq) FROM changes").fetchone()[0] == sync_cursor
    assert db.execute("SELECT work_log FROM promises WHERE id = 'promise'").fetchone()[0] == ""
    for sql in ("DELETE FROM promise_changes", "UPDATE promise_changes SET words = 'different'"):
        with pytest.raises(sqlite3.IntegrityError, match="append-only"), db:
            db.execute(sql)
    assert history() == before
    db.close()
