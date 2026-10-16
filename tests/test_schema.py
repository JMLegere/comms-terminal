"""Schema guarantees behind FR-2 / FR-2a / I-2 that no feature step reaches directly."""

import sqlite3
from datetime import datetime, timezone

import pytest

from universal_inbox import files, inbox


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


def test_migrates_a_version_1_database(tmp_path):
    import sqlite3
    db = sqlite3.connect(tmp_path / "v1.db")
    db.execute("CREATE TABLE cases (id INTEGER PRIMARY KEY, title TEXT NOT NULL, created_at TEXT NOT NULL) STRICT")
    db.execute("INSERT INTO cases (title, created_at) VALUES ('Acme', '2026-10-01T00:00:00+00:00')")
    db.execute("CREATE TABLE threads (id INTEGER PRIMARY KEY, status_detail TEXT NOT NULL,"
               " status_changed_at TEXT NOT NULL) STRICT")
    db.execute("INSERT INTO threads (status_detail, status_changed_at) VALUES"
               " ('WAITING_FOR_CUSTOMER', '2026-10-01T09:30:00.250000+00:00'), ('CREATED', '2026-10-01T09:30:00+00:00')")
    db.execute("CREATE TABLE connections (id INTEGER PRIMARY KEY, platform TEXT NOT NULL CHECK (platform IN"
               " ('email', 'slack')), external_id TEXT NOT NULL UNIQUE, self_id TEXT NOT NULL, label TEXT NOT NULL,"
               " connected_at TEXT NOT NULL, auth_ok INTEGER NOT NULL DEFAULT 1, last_error TEXT) STRICT")
    db.executemany("INSERT INTO connections (platform, external_id, self_id, label, connected_at)"
                   " VALUES (?, ?, ?, ?, '2026-10-01T00:00:00+00:00')",
                   [("slack", "T0", "U0", "Slack"), ("email", "me@x.io", "me@x.io", "Email"),
                    ("email", "me@fastmail.com", "me@fastmail.com", "Fastmail")])
    db.execute("CREATE TABLE identities (id INTEGER PRIMARY KEY, person_id INTEGER, kind TEXT NOT NULL CHECK (kind IN"
               " ('email', 'slack_user')), value TEXT NOT NULL, blocked INTEGER NOT NULL DEFAULT 0, UNIQUE (kind, value)) STRICT")
    db.execute("INSERT INTO identities (kind, value) VALUES ('email', 'ana@acme.com')")
    db.execute("CREATE TABLE organizations (id INTEGER PRIMARY KEY, domain TEXT NOT NULL UNIQUE, name TEXT NOT NULL,"
               " created_at TEXT NOT NULL) STRICT")
    db.executemany("INSERT INTO organizations (domain, name, created_at) VALUES (?, ?, '2026-10-01T00:00:00+00:00')",
                   [("accounts.google.com", "accounts.google.com"), ("google.com", "google.com"),
                    ("ws-ts.nb.ca", "ws-ts.nb.ca"), ("mail.attio.com", "Attio")])
    db.execute("PRAGMA user_version = 1")
    inbox.migrate(db)
    assert db.execute("SELECT description FROM cases").fetchone()[0] == ""
    # v5 (SMS): rebuilt tables keep their rows and accept the new platform and identity kind
    # v7: one Organization key; subdomains fold into the registrable domain, a public suffix (nb.ca) does not
    assert [tuple(r) for r in db.execute("SELECT domain, name FROM organizations ORDER BY domain")] == [
        ("attio.com", "Attio"), ("google.com", "google.com"), ("ws-ts.nb.ca", "ws-ts.nb.ca")]
    # v6: Platforms are services; the email Platform splits by its Fastmail label
    assert db.execute("SELECT platform, external_id FROM connections ORDER BY id").fetchall() == [
        ("slack", "T0"), ("gmail", "me@x.io"), ("fastmail", "me@fastmail.com")]
    assert db.execute("SELECT kind, value FROM identities").fetchall() == [("email", "ana@acme.com")]
    db.execute("INSERT INTO connections (platform, external_id, self_id, label, connected_at)"
               " VALUES ('quo', '+15068324154', '+15068324154', 'Quo', '2026-10-12T00:00:00+00:00')")
    db.execute("INSERT INTO identities (kind, value) VALUES ('phone', '+15066092430')")
    # v3 (#116): threads already Waiting get a follow-up 5 days after they began waiting, in _iso's format
    assert [r[0] for r in db.execute("SELECT follow_up_at FROM threads ORDER BY id")] == [
        "2026-10-06T09:30:00.250000+00:00", None]
    assert db.execute("PRAGMA user_version").fetchone()[0] == inbox.SCHEMA_VERSION


def test_an_echo_of_a_sent_reply_is_not_stored_twice():
    """Resend/SES rewrites the Message-ID, so a forwarded copy of Jeremy's reply is matched by text."""
    from datetime import timedelta
    at = datetime(2026, 10, 12, 4, 36, tzinfo=timezone.utc)
    db = inbox.connect()
    inbox.migrate(db)
    with db:
        cid = db.execute("INSERT INTO connections (platform, external_id, self_id, label, connected_at)"
                         " VALUES ('gmail', 'me@x.io', 'me@x.io', 'Gmail', ?)", (at.isoformat(),)).lastrowid
    ana = inbox.Identity("email", "ana@acme.com")
    t = inbox.ingest(db, inbox.Incoming(cid, "me@x.io", "mailbox", "MESSAGE", "<a1@acme.com>", ana, at, "hi", [], "Hi"))
    mine = inbox.Incoming(cid, "me@x.io", "mailbox", "MESSAGE", "<sent@x.io>", None, at + timedelta(minutes=5),
                          "test reply", ["<a1@acme.com>"], "Re: Hi")
    assert inbox.ingest(db, mine) is not None
    echo = inbox.Incoming(cid, "me@x.io", "mailbox", "MESSAGE", "<ses-rewritten@amazonses.com>", None,
                          at + timedelta(minutes=5, seconds=1), "test reply\r\n", ["<a1@acme.com>"], "Re: Hi")
    assert inbox.ingest(db, echo) is None
    # Replies quote the earlier message (decision #109); the copy with the quote is still the echo,
    quoted = inbox.Incoming(cid, "me@x.io", "mailbox", "MESSAGE", "<ses-2@amazonses.com>", None,
                            at + timedelta(minutes=5, seconds=2),
                            "test reply\r\n\r\nOn Mon, Oct 12, 2026 at 4:36 AM Ana <ana@acme.com> wrote:\r\n> hi",
                            ["<a1@acme.com>"], "Re: Hi")
    assert inbox.ingest(db, quoted) is None
    # but a new message that merely starts with the same words is not.
    longer = inbox.Incoming(cid, "me@x.io", "mailbox", "MESSAGE", "<other@x.io>", None,
                            at + timedelta(minutes=6), "test reply, and one more thing", ["<a1@acme.com>"], "Re: Hi")
    assert inbox.ingest(db, longer) is not None
    assert db.execute("SELECT count(*) FROM activities WHERE thread_id = ?", (t.id,)).fetchone()[0] == 3



def test_waiting_returns_to_todo_after_four_days_without_a_reply():
    """#116: a reply puts the Thread in Waiting until they answer, or for 4 days, then it comes back to follow up."""
    from datetime import timedelta
    at = datetime(2026, 10, 12, 9, tzinfo=timezone.utc)
    db = inbox.connect()
    inbox.migrate(db)
    with db:
        cid = db.execute("INSERT INTO connections (platform, external_id, self_id, label, connected_at)"
                         " VALUES ('gmail', 'me@x.io', 'me@x.io', 'Gmail', ?)", (at.isoformat(),)).lastrowid
    mine = inbox.Incoming(cid, "me@x.io", "mailbox", "MESSAGE", "<m1@x.io>", None, at, "Proposal attached", [], "Proposal")
    t = inbox.ingest(db, mine)
    assert (t.status_detail, t.follow_up_at) == ("WAITING_FOR_CUSTOMER", "2026-10-16T09:00:00.000000+00:00")
    assert inbox.wake(db, at + timedelta(days=3)) == 0
    assert inbox.wake(db, at + timedelta(days=4)) == 1
    t = inbox.get_thread(db, t.id)
    assert (t.status, t.status_detail, t.follow_up_at) == ("TODO", "NEW_REPLY", None)


def test_version_12_upgrade_adds_only_receipts_and_preserves_records_files_and_guards(tmp_path):
    from test_attachment_store import NOW, setup

    db, thread_id, activity_id = setup(tmp_path)
    try:
        sha = files.store_file(db, b"retained file")
        inbox.add_attachments(db, activity_id, [{"name": "retained.txt", "type": "text/plain", "size": 13, "sha": sha}])
        inbox.stage(db, sha=sha, name="reply.txt", type="text/plain", size=13, thread_id=thread_id)
        inbox.save_draft(db, {"connection_id": 1, "kind": "email", "sender": "me@x.io", "to": "ana@acme.com",
                             "cc": "", "subject": "Retained draft", "body": "Unsent"})
        raw = b"Message-ID: <retained@test>\r\n\r\nRetained raw"
        raw_sha = files.store_raw(db, raw)
        inbox.ingest(db, inbox.Incoming(1, "me@x.io", "mailbox", "MESSAGE", "<retained@test>", None, NOW,
                                        "Retained raw", data={"raw": raw_sha}))
        db.executescript("DROP TABLE audit_log; DROP INDEX composition_drafts_thread; ALTER TABLE composition_drafts DROP COLUMN thread_id;"
                         " DROP TABLE resend_receipts; PRAGMA user_version = 12;")
        tables = [r[0] for r in db.execute("SELECT name FROM sqlite_schema WHERE type = 'table'")]
        columns = {name: ", ".join(f'"{c[1]}"' for c in db.execute(f'PRAGMA table_info("{name}")')) for name in tables}
        read = lambda: {name: [tuple(r) for r in db.execute(f'SELECT {columns[name]} FROM "{name}" ORDER BY rowid')]  # noqa: E731
                        for name in tables}
        rows = read()
        unchanged = "SELECT type, name, tbl_name, sql FROM sqlite_schema WHERE tbl_name NOT IN ('resend_receipts', 'composition_drafts', 'audit_log') ORDER BY name"
        schema = [tuple(r) for r in db.execute(unchanged)]
        inbox.migrate(db)
        assert db.execute("PRAGMA user_version").fetchone()[0] == inbox.SCHEMA_VERSION == 15
        assert read() == rows  # 14 only adds composition_drafts.thread_id, NULL on every existing Draft
        assert db.execute("SELECT count(*) FROM composition_drafts WHERE thread_id IS NOT NULL").fetchone()[0] == 0
        assert [tuple(r) for r in db.execute(unchanged)] == schema
        assert files.attachment_path(db, sha).read_bytes() == b"retained file"
        assert (files.raw_dir(db) / f"{raw_sha}.eml").read_bytes() == raw
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO resend_receipts VALUES ('provider', 999, '<wire@test>')")
        db.rollback()
        for sql in ("UPDATE activities SET native_id = '<rewritten@test>'", "DELETE FROM activities"):
            with pytest.raises(sqlite3.DatabaseError, match="append-only"):
                db.execute(sql)
            db.rollback()
        after = "\n".join(db.iterdump())
        inbox.migrate(db)
        assert "\n".join(db.iterdump()) == after
    finally:
        db.close()


def test_failed_version_13_upgrade_rolls_back_ddl_and_version_and_restores_fk(tmp_path):
    from test_attachment_store import setup

    db, thread_id, activity_id = setup(tmp_path)
    try:
        db.executescript("DROP TABLE audit_log; DROP INDEX composition_drafts_thread; ALTER TABLE composition_drafts DROP COLUMN thread_id;"
                         " DROP TABLE resend_receipts; PRAGMA user_version = 12; PRAGMA foreign_keys = OFF;")
        with db:
            db.execute("INSERT INTO attachments (activity_id, position, name, type, size, state)"
                       " VALUES (999, 0, 'broken', 'text/plain', 0, 'pending')")
        db.execute("PRAGMA foreign_keys = ON")
        before = "\n".join(db.iterdump())
        with pytest.raises(ValueError, match="broken foreign key"):
            inbox.migrate(db)
        assert db.execute("PRAGMA user_version").fetchone()[0] == 12
        assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert db.execute("SELECT name FROM sqlite_schema WHERE name = 'resend_receipts'").fetchall() == []
        assert "\n".join(db.iterdump()) == before
    finally:
        db.close()
