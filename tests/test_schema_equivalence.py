"""schema.sql (fresh databases) and MIGRATIONS (existing ones) must build the same schema."""

import re

from universal_inbox import inbox
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
        changed = ("composition_drafts", "persons")  # 14: reply Drafts; 17: Task Drafts and Unsubscribed
        assert {k: v for k, v in shape(old).items() if k in before and k[1] not in changed} == \
            {k: v for k, v in before.items() if k[1] not in changed}
        assert "thread_id integer references threads(id)" in shape(old)[("table", "composition_drafts")]
    finally:
        fresh.close()
        old.close()
