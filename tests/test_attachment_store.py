"""Attachment store (.agents/prd/2026-10-15-attachments.md P1): migration 12, files/, Attachment rows, staging."""

import sqlite3
from datetime import datetime, timezone

import pytest

from universal_inbox import files, inbox

NOW = datetime(2026, 10, 15, 15, 0, tzinfo=timezone.utc)


def setup(tmp_path):
    db = inbox.connect(str(tmp_path / "inbox.db"))
    inbox.migrate(db)
    with db:
        db.execute("INSERT INTO connections (platform, external_id, self_id, label, connected_at)"
                   " VALUES ('gmail', 'me@x.io', 'me@x.io', 'Email', ?)", (NOW.isoformat(),))
    t = inbox.ingest(db, inbox.Incoming(1, "me@x.io", "mailbox", "MESSAGE", "<a@acme.com>",
                                        inbox.Identity("email", "ana@acme.com", "Ana"), NOW, "contract attached"))
    activity = db.execute("SELECT id FROM activities WHERE thread_id = ?", (t.id,)).fetchone()[0]
    return db, t.id, activity


def test_a_v11_database_gains_the_tables_and_keeps_its_rows(tmp_path):
    db, thread_id, activity = setup(tmp_path)
    db.executescript("DROP TABLE resend_receipts; DROP TABLE staged_attachments;"
                    " DROP TABLE attachments; PRAGMA user_version = 11;")
    inbox.migrate(db)
    assert db.execute("PRAGMA user_version").fetchone()[0] == inbox.SCHEMA_VERSION == 13
    assert db.execute("SELECT count(*) FROM activities").fetchone()[0] == 1
    assert inbox.attachments(db, activity) == []


def test_files_are_content_addressed_and_private(tmp_path):
    db, _, _ = setup(tmp_path)
    sha = files.store_file(db, b"%PDF-1.7 contract")
    path = files.attachment_path(db, sha)
    assert path == tmp_path / "files" / sha and path.read_bytes() == b"%PDF-1.7 contract"
    assert oct(path.stat().st_mode & 0o777) == "0o600" and oct(path.parent.stat().st_mode & 0o777) == "0o700"
    assert files.store_file(db, b"%PDF-1.7 contract") == sha  # same bytes, same file, never rewritten
    assert files.attachment_path(db, "../inbox.db") is None
    assert files.store_file(inbox.connect(), b"x") is None  # in memory: nowhere to keep files


def test_attachments_are_stored_or_wait_for_a_download(tmp_path):
    db, _, activity = setup(tmp_path)
    sha = files.store_file(db, b"png")
    ids = inbox.add_attachments(db, activity, [
        {"name": "logo.png", "type": "image/png", "size": 3, "inline": True, "content_id": "logo", "sha": sha},
        {"name": "deck.pdf", "type": "application/pdf", "size": 1200, "source": {"id": "F1", "url": "https://files.slack.com/F1"}}])
    rows = inbox.attachments(db, activity)
    assert [(r["position"], r["state"], r["inline"]) for r in rows] == [(0, "stored", 1), (1, "pending", 0)]
    assert [r["id"] for r in inbox.pending_attachments(db)] == [ids[1]]
    for _ in range(inbox.ATTACHMENT_TRIES - 1):
        inbox.mark_failed(db, ids[1])
    assert inbox.attachments(db, activity)[1]["state"] == "pending"
    inbox.mark_failed(db, ids[1])
    assert inbox.attachments(db, activity)[1]["state"] == "failed" and inbox.pending_attachments(db) == []
    inbox.mark_stored(db, ids[1], files.store_file(db, b"pdf"))
    assert inbox.attachments(db, activity)[1]["state"] == "stored"


def test_a_stored_attachment_needs_its_sha(tmp_path):
    db, _, activity = setup(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO attachments (activity_id, position, name, type, size, state)"
                   " VALUES (?, 0, 'a', 'b', 0, 'stored')", (activity,))
    with pytest.raises(sqlite3.IntegrityError):  # one position per message
        inbox.add_attachments(db, activity, [{"name": "a", "type": "t", "size": 0}])
        inbox.add_attachments(db, activity, [{"name": "b", "type": "t", "size": 0}])


def test_staging_on_a_draft_or_a_reply(tmp_path):
    db, thread_id, _ = setup(tmp_path)
    draft = inbox.save_draft(db, {"connection_id": 1, "kind": "email", "sender": "me@x.io", "to": "ana@acme.com",
                                  "cc": "", "subject": "Contract", "body": "attached"})
    sha = files.store_file(db, b"%PDF")
    a = inbox.stage(db, sha=sha, name="contract.pdf", type="application/pdf", size=4, draft_id=draft["id"])
    t = inbox.stage(db, sha=sha, name="contract.pdf", type="application/pdf", size=4, thread_id=thread_id)
    assert [s["id"] for s in inbox.staged(db, draft_id=draft["id"])] == [a]
    assert len(inbox.staged(db, thread_id=thread_id)) == 1
    with pytest.raises(ValueError):
        inbox.stage(db, sha=sha, name="x", type="t", size=4, draft_id=draft["id"], thread_id=thread_id)
    with pytest.raises(ValueError):
        inbox.stage(db, sha="0" * 64, name="x", type="t", size=4, thread_id=thread_id)  # no such file
    inbox.unstage(db, t)
    assert inbox.staged(db, thread_id=thread_id) == []
    inbox.delete_draft(db, draft["id"])  # a deleted Draft takes its staged files with it
    assert db.execute("SELECT count(*) FROM staged_attachments").fetchone()[0] == 0
    b = inbox.stage(db, sha=sha, name="c.pdf", type="application/pdf", size=4, thread_id=thread_id)
    inbox.unstage(db, b)
    assert inbox.staged(db, thread_id=thread_id) == []
