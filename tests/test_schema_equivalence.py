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
        added = {"resend_receipts", "sqlite_autoindex_resend_receipts_1", "composition_drafts_thread",
                 "composition_drafts", "audit_log", "audit_log_thread", "audit_log_no_delete",
                 "audit_log_no_update", "case_log", "case_log_case", "case_log_no_delete", "case_log_no_update"}
    # 13: resend_receipts; 14: composition_drafts.thread_id; 15: audit_log; 16: case_log
        assert {key: value for key, value in shape(old).items() if key[1] not in added} == \
            {key: value for key, value in before.items() if key[1] not in added}
        assert "thread_id integer references threads(id)" in shape(old)[("table", "composition_drafts")]
    finally:
        fresh.close()
        old.close()
