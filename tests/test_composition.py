"""Drafts-first local persistence and captured history; synthetic records, no provider calls."""

import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from universal_inbox import email, inbox
from test_email_reconcile import SCHEMA3
from test_schema_equivalence import shape

AT = datetime(2026, 10, 1, 10, tzinfo=timezone.utc)
SELF = "me@example.test"
PHONE = "+15550001001"
PEER = "+15550001002"


def envelope(kind="email", **changes):
    values = {"connection_id": None, "kind": kind, "sender": "", "to": "", "cc": "",
              "subject": "" if kind == "email" else None, "body": ""}
    return values | changes


def add_connections(db):
    with db:
        db.executemany(
            "INSERT INTO connections (id, platform, external_id, self_id, label, connected_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            [(1, "gmail", SELF, SELF, "Mail", AT.isoformat()),
             (2, "fastmail", "other@example.test", "other@example.test", "Other mail", AT.isoformat()),
             (3, "quo", PHONE, PHONE, "SMS", AT.isoformat()),
             (4, "slack", "TTEST", "USELF", "Slack", AT.isoformat())])


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(inbox, "_now", lambda: AT)
    db = inbox.connect(str(tmp_path / "inbox.db"))
    inbox.migrate(db)
    add_connections(db)
    yield db
    db.close()


def incoming(native_id, **changes):
    return replace(inbox.Incoming(
        1, SELF, "mailbox", "MESSAGE", native_id, inbox.Identity("email", "ana@acme.test"),
        AT, "Captured body", title="Captured subject",
        data={"from": "ana@acme.test", "to": [SELF], "cc": []}), **changes)


def old_rows(db):
    return {r[0]: [tuple(a) for a in db.execute(f'SELECT * FROM "{r[0]}" ORDER BY rowid')]
            for r in db.execute("SELECT name FROM sqlite_schema WHERE type = 'table'"
                                " AND name NOT LIKE 'sqlite_%' AND name != 'composition_drafts'")}


@pytest.mark.parametrize("kind,connection_id", [("email", 1), ("email", 2), ("sms", 3)])
def test_draft_create_save_reopen_keeps_exact_full_envelope(db, monkeypatch, kind, connection_id):
    before = old_rows(db)
    blank = inbox.save_draft(db, envelope(kind))
    assert {k: blank[k] for k in envelope(kind)} == envelope(kind)
    assert blank["revision"] == 1 and blank["created_at"] == blank["updated_at"] == inbox._iso(AT)
    values = envelope(kind, connection_id=connection_id, sender="  edited sender  ",
                      to=" unfinished destination ", body="  exact text\r\n\n ")
    if kind == "email":
        values |= {"subject": " Edited subject ", "cc": "  Other <OTHER@ACME.TEST>  "}
    monkeypatch.setattr(inbox, "_now", lambda: AT + timedelta(minutes=1))
    saved = inbox.save_draft(db, values, draft_id=blank["id"], expected_revision=1)
    assert {k: saved[k] for k in values} == values
    assert saved["id"] == blank["id"] and saved["revision"] == 2
    assert saved["created_at"] == blank["created_at"]
    assert saved["updated_at"] == inbox._iso(AT + timedelta(minutes=1))
    assert datetime.fromisoformat(saved["updated_at"]).utcoffset() == timedelta(0)
    path = db.execute("PRAGMA database_list").fetchone()[2]
    db.close()
    reopened = inbox.connect(path)
    try:
        inbox.migrate(reopened)
        assert inbox.get_draft(reopened, saved["id"]) == saved
        assert inbox.list_drafts(reopened) == [saved]
        assert old_rows(reopened) == before
    finally:
        reopened.close()


def test_draft_revision_conflict_across_connections_keeps_saved_row_and_edits(db):
    original = inbox.save_draft(db, envelope(body="original"))
    other = inbox.connect(db.execute("PRAGMA database_list").fetchone()[2])
    try:
        stale = inbox.get_draft(other, original["id"])
        saved = inbox.save_draft(db, envelope(body="new saved text"),
                                 draft_id=original["id"], expected_revision=1)
        edits = envelope(to="new@example.test", body="still visible stale edits")
        before = old_rows(db)
        with pytest.raises(inbox.DraftConflict):
            inbox.save_draft(other, edits, draft_id=stale["id"], expected_revision=stale["revision"])
        assert inbox.get_draft(db, original["id"]) == inbox.get_draft(other, original["id"]) == saved
        assert edits == envelope(to="new@example.test", body="still visible stale edits")
        assert old_rows(db) == before
    finally:
        other.close()


@pytest.mark.parametrize("operation", ["INSERT", "UPDATE"])
def test_failed_draft_save_rolls_back_without_losing_previous_row_or_edits(db, operation):
    original = inbox.save_draft(db, envelope(body="saved text"))
    db.execute(f"CREATE TRIGGER draft_failure BEFORE {operation} ON composition_drafts"
               " BEGIN SELECT RAISE(ABORT, 'injected draft failure'); END")
    before, ledger = inbox.list_drafts(db), old_rows(db)
    edits = envelope(body="unsaved edits")
    kwargs = {"draft_id": original["id"], "expected_revision": 1} if operation == "UPDATE" else {}
    with pytest.raises(sqlite3.IntegrityError, match="injected draft failure"):
        inbox.save_draft(db, edits, **kwargs)
    assert inbox.list_drafts(db) == before and old_rows(db) == ledger
    assert edits["body"] == "unsaved edits"


@pytest.mark.parametrize("changes", [
    {"kind": None}, {"kind": []}, {"kind": "slack"}, {"sender": None}, {"to": []},
    {"cc": None}, {"body": 1}, {"subject": None}, {"subject": False},
    {"connection_id": True}, {"connection_id": "1"}, {"connection_id": 0}, {"connection_id": -1},
    {"connection_id": 999}, {"connection_id": 3}, {"connection_id": 4},
    {"kind": "sms", "subject": ""}, {"kind": "sms", "subject": None, "cc": "someone"},
    {"kind": "sms", "subject": None, "connection_id": 1},
    {"kind": "sms", "subject": None, "connection_id": 4}, {"unknown": "value"},
])
def test_draft_strict_validation_preserves_saved_row(db, changes):
    original = inbox.save_draft(db, envelope(body="saved text"))
    with pytest.raises(ValueError):
        inbox.save_draft(db, envelope(**changes), draft_id=original["id"], expected_revision=1)
    assert inbox.get_draft(db, original["id"]) == original


def test_draft_requires_all_fields_and_explicit_valid_revision_pair(db):
    original = inbox.save_draft(db, envelope())
    for values in (None, [], (), False):
        with pytest.raises(ValueError):
            inbox.save_draft(db, values)
    for k in envelope():
        partial = envelope()
        del partial[k]
        with pytest.raises(ValueError):
            inbox.save_draft(db, partial, draft_id=original["id"], expected_revision=1)
    for draft_id, revision in ((None, 1), (1, None), (True, 1), (1, True), ("1", 1),
                               (1, "1"), (0, 1), (1, 0), (-1, 1), (1, -1)):
        with pytest.raises(ValueError):
            inbox.save_draft(db, envelope(), draft_id=draft_id, expected_revision=revision)
    for value in (True, None, "1", 0, -1):
        with pytest.raises(ValueError):
            inbox.get_draft(db, value)
    with pytest.raises(KeyError):
        inbox.get_draft(db, 999)
    with pytest.raises(KeyError):
        inbox.save_draft(db, envelope(), draft_id=999, expected_revision=1)
    assert inbox.list_drafts(db) == [original]


def test_draft_list_latest_update_then_id_and_connection_format_change(db, monkeypatch):
    one = inbox.save_draft(db, envelope(connection_id=1))
    two = inbox.save_draft(db, envelope("sms", connection_id=3))
    assert inbox.list_drafts(db) == [two, one]
    monkeypatch.setattr(inbox, "_now", lambda: AT + timedelta(minutes=1))
    changed = inbox.save_draft(db, envelope("sms", connection_id=3, to=PEER),
                               draft_id=one["id"], expected_revision=1)
    assert inbox.list_drafts(db) == [changed, two]
    cleared = inbox.save_draft(db, envelope(), draft_id=one["id"], expected_revision=2)
    assert cleared["connection_id"] is None and cleared["subject"] == "" and cleared["to"] == ""


def make_v8(db, monkeypatch):
    db.executescript(SCHEMA3)
    with monkeypatch.context() as m:
        m.setattr(inbox, "SCHEMA_VERSION", 8)
        inbox.migrate(db)
    add_connections(db)
    one = inbox.ingest(db, incoming("<one@test>"))
    two = inbox.ingest(db, incoming("<two@test>"))
    inbox.ingest(db, incoming("<bridge@test>", thread_ref=["<one@test>", "<two@test>"]))
    case_id = inbox.create_case(db, "Synthetic case")
    inbox.act(db, one.id, inbox.SetCase(case_id=case_id))
    inbox.add_channel_rule(db, 4, "channel", "CTEST")
    inbox.set_probe(db, 1, "Synthetic probe result")
    assert inbox.get_thread(db, two.id).id == one.id
    return one, two


def test_v8_draft_migration_matches_fresh_preserves_old_rows_aliases_guards_and_fk(tmp_path, monkeypatch):
    old, fresh = inbox.connect(str(tmp_path / "v8.db")), inbox.connect()
    try:
        one, two = make_v8(old, monkeypatch)
        before, old_shape = old_rows(old), shape(old)
        inbox.migrate(old)
        inbox.migrate(fresh)
        assert inbox.SCHEMA_VERSION == 9 and old.execute("PRAGMA user_version").fetchone()[0] == 9
        assert shape(old) == shape(fresh)
        assert {k: v for k, v in shape(old).items() if k in old_shape} == old_shape
        assert old_rows(old) == before and inbox.get_thread(old, two.id).id == one.id
        assert old.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert old.execute("PRAGMA foreign_key_check").fetchall() == []
        assert [tuple(r) for r in old.execute("PRAGMA foreign_key_list(composition_drafts)")] == [
            (0, 0, "connections", "connection_id", "id", "NO ACTION", "NO ACTION", "NONE")]
        for sql in ("DELETE FROM activities", "UPDATE activities SET content = 'changed'",
                    f"UPDATE threads SET merged_into = {two.id} WHERE id = {one.id}"):
            with pytest.raises(sqlite3.DatabaseError), old:
                old.execute(sql)
        assert old_rows(old) == before
        with old:
            old.execute("INSERT INTO connections (id, platform, external_id, self_id, label, connected_at)"
                        " VALUES (5, 'gmail', 'draft@example.test', 'draft@example.test', 'Draft mail', ?)",
                        (AT.isoformat(),))
        draft = inbox.save_draft(old, envelope(connection_id=5, body="preserved"))
        with pytest.raises(sqlite3.IntegrityError), old:
            old.execute("DELETE FROM connections WHERE id = 5")
        assert inbox.get_draft(old, draft["id"]) == draft
        rows, schema = old_rows(old), shape(old)
        inbox.migrate(old)
        assert old_rows(old) == rows and shape(old) == schema and inbox.list_drafts(old) == [draft]
    finally:
        old.close()
        fresh.close()


@pytest.mark.parametrize("version", [0, 8])
def test_draft_migration_sql_failure_rolls_back_schema_version_and_old_rows(tmp_path, monkeypatch, version):
    db = inbox.connect(str(tmp_path / "rollback.db"))
    try:
        if version == 8:
            make_v8(db, monkeypatch)
            monkeypatch.setitem(inbox.MIGRATIONS, 9, inbox.MIGRATIONS[9] + "\nINSERT INTO absent_table VALUES (1);")
        else:
            read_text = inbox.Path.read_text
            monkeypatch.setattr(inbox.Path, "read_text", lambda p, *a, **k:
                                read_text(p, *a, **k) + ("\nINSERT INTO absent_table VALUES (1);"
                                                       if p.name == "schema.sql" else ""))
        before, schema = old_rows(db), shape(db)
        with pytest.raises(sqlite3.DatabaseError):
            inbox.migrate(db)
        assert db.execute("PRAGMA user_version").fetchone()[0] == version
        assert old_rows(db) == before and shape(db) == schema
        assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert db.execute("PRAGMA legacy_alter_table").fetchone()[0] == 0
    finally:
        db.close()


def test_draft_migration_fk_failure_rolls_back_addition_without_repairing_old_data(tmp_path, monkeypatch):
    db = inbox.connect(str(tmp_path / "broken-v8.db"))
    try:
        make_v8(db, monkeypatch)
        db.execute("PRAGMA foreign_keys = OFF")
        with db:
            db.execute("INSERT INTO channel_rules (connection_id, kind, value) VALUES (999, 'channel', 'CBROKEN')")
        db.execute("PRAGMA foreign_keys = ON")
        before, schema = old_rows(db), shape(db)
        with pytest.raises(ValueError, match="broken foreign key"):
            inbox.migrate(db)
        assert db.execute("PRAGMA user_version").fetchone()[0] == 8
        assert old_rows(db) == before and shape(db) == schema
        assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        db.close()


def test_recipient_history_email_raw_unlinked_detached_outgoing_and_canonical_aliases(db):
    inbound = inbox.ingest(db, incoming("<in@test>"))
    identity = db.execute("SELECT id FROM identities WHERE value = 'ana@acme.test'").fetchone()[0]
    inbox.remove_identity(db, identity)
    outbound = inbox.ingest(db, incoming("<out@test>", sender=None,
                                        data={"from": SELF, "to": ["Raw <RAW@ACME.TEST>"],
                                              "cc": ["ana@acme.test", "cc@acme.test"]}))
    raw = (f"From: {SELF}\r\nTo: Raw <RAW@ACME.TEST>\r\nCc: cc@acme.test\r\n"
           "Message-ID: <raw@test>\r\nDate: Thu, 1 Oct 2026 10:00:00 +0000\r\n"
           "Subject: Synthetic source\r\n\r\nCaptured source body").encode()
    source = email.parse(raw, inbox.connections(db)[0])
    inbox.ingest(db, replace(source, data=source.data | {"raw": inbox.store_raw(db, raw)},
                             thread_ref=["<in@test>", "<out@test>"]))
    assert inbox.get_thread(db, outbound.id).id == inbound.id
    before = old_rows(db)
    for value in ("ana@acme.test", "  Ana <ANA@ACME.TEST>  ", "raw@acme.test", "cc@acme.test"):
        assert inbox.recipient_threads(db, "email", value) == [inbox.get_thread(db, inbound.id)]
    for value in ("", "missing@acme.test", "raw@acme.test.evil", "raw@acme.test, cc@acme.test"):
        assert inbox.recipient_threads(db, "email", value) == []
    assert db.execute("SELECT person_id FROM identities WHERE id = ?", (identity,)).fetchone()[0] is None
    assert db.execute("SELECT 1 FROM identities WHERE value = 'raw@acme.test'").fetchone() is None
    assert inbox.thread_view(db, inbound.id)["messages"][1]["who"] == "You"
    assert old_rows(db) == before

def test_recipient_history_ignores_incoming_to_cc_and_body_only_matches(db):
    inbox.ingest(db, incoming("<negative@test>", content="Contact body-only@acme.test",
                             data={"from": "ana@acme.test", "to": [SELF, "to-only@acme.test"],
                                   "cc": ["cc-only@acme.test"]}))
    before = old_rows(db)
    for value in (SELF, "to-only@acme.test", "cc-only@acme.test", "body-only@acme.test"):
        assert inbox.recipient_threads(db, "email", value) == []
    assert old_rows(db) == before


def test_recipient_history_sms_detached_inbound_outgoing_peer_and_reply_channel(db):
    sms = incoming("sms-in", connection_id=3, channel=PEER, channel_kind="im",
                   sender=inbox.Identity("phone", PEER), data={"peer": PEER})
    inbound = inbox.ingest(db, sms)
    identity = db.execute("SELECT id FROM identities WHERE value = ?", (PEER,)).fetchone()[0]
    inbox.remove_identity(db, identity)
    outbound = inbox.ingest(db, replace(sms, native_id="sms-out", sender=None,
                                        occurred_at=AT + timedelta(days=1)))
    unlinked = "+15550001003"
    peer_only = inbox.ingest(db, replace(sms, native_id="sms-peer", sender=None,
                                         channel=unlinked, data={"peer": unlinked}))
    channel_only = inbox.ingest(db, replace(sms, native_id="sms-reply", sender=None,
                                            channel=unlinked, data={"to": [], "cc": []},
                                            occurred_at=AT + timedelta(days=2)))
    inbox.ingest(db, incoming("DFAKE:1", connection_id=4, channel=PEER, channel_kind="im",
                             sender=None, data={"peer": PEER}))
    before = old_rows(db)
    assert inbox.recipient_threads(db, "phone", PEER) == [inbound, outbound]
    assert inbox.recipient_threads(db, "phone", unlinked) == [peer_only, channel_only]
    for value in ("", "15550001002", "+1 (555) 000-1002", PEER + " ", "+15550001099"):
        assert inbox.recipient_threads(db, "phone", value) == []
    assert inbox.recipient_threads(db, "email", PEER) == []
    assert db.execute("SELECT person_id FROM identities WHERE id = ?", (identity,)).fetchone()[0] is None
    assert db.execute("SELECT 1 FROM identities WHERE value = ?", (unlinked,)).fetchone() is None
    assert inbox.thread_view(db, outbound.id)["messages"][0]["who"] == "You"
    assert old_rows(db) == before


@pytest.mark.parametrize("kind,value", [("sms", PEER), ("slack_user", "U1"), (None, SELF), ([], SELF), ("email", None)])
def test_recipient_history_rejects_wrong_types_without_mutation(db, kind, value):
    before = old_rows(db)
    with pytest.raises(ValueError):
        inbox.recipient_threads(db, kind, value)
    assert old_rows(db) == before
