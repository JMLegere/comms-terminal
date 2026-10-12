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
