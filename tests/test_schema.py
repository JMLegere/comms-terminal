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
