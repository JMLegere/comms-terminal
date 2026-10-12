"""Schema guarantees behind FR-2 / FR-2a / I-2 that no feature step reaches directly."""

import sqlite3
from datetime import datetime, timezone

import pytest

from universal_inbox import inbox


def test_activities_are_append_only_except_content_clearing_after_delete():
    db = inbox.connect()
    inbox.migrate(db)
    db.execute("INSERT INTO connections (id, platform, external_id, self_id, label, connected_at)"
               " VALUES (2, 'slack', 'T0', 'U0JEREMY', 'Slack', '2026-10-12T00:00:00+00:00')")
    at = datetime(2026, 10, 12, 10, tzinfo=timezone.utc)
    msg = inbox.Incoming(2, "D0ANA", "im", "MESSAGE", "D0ANA:1", inbox.Identity("slack_user", "U0ANA"), at, "secret")
    thread = inbox.ingest(db, msg)
    assert inbox.ingest(db, msg) is None  # duplicate native_id stored once

    for sql in ("UPDATE activities SET content = 'rewritten'",
                "UPDATE activities SET content = NULL",  # no DELETE yet
                "DELETE FROM activities"):
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            db.execute(sql)

    with pytest.raises(sqlite3.IntegrityError):  # I-2: detail must match status
        db.execute("UPDATE threads SET status = 'DONE', status_detail = 'CREATED' WHERE id = ?", (thread.id,))


def test_migrates_a_version_1_database_to_case_descriptions(tmp_path):
    import sqlite3
    db = sqlite3.connect(tmp_path / "v1.db")
    db.execute("CREATE TABLE cases (id INTEGER PRIMARY KEY, title TEXT NOT NULL, created_at TEXT NOT NULL) STRICT")
    db.execute("INSERT INTO cases (title, created_at) VALUES ('Acme', '2026-10-01T00:00:00+00:00')")
    db.execute("PRAGMA user_version = 1")
    inbox.migrate(db)
    assert db.execute("SELECT description FROM cases").fetchone()[0] == ""
    assert db.execute("PRAGMA user_version").fetchone()[0] == inbox.SCHEMA_VERSION
