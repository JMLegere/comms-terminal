"""Public Draft storage and captured-history steps; synthetic local records only."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from pytest_bdd import given, parsers, then, when

from comms_terminal import inbox

AT = datetime(2026, 10, 1, 10, tzinfo=timezone.utc)
SELF = "me@example.test"
FIELDS = {"Connection": "connection_id", "Message format": "kind", "Sender": "sender",
          "To": "to", "Cc": "cc", "Subject": "subject", "Body": "body"}


@pytest.fixture
def composition(tmp_path):
    path = str(tmp_path / "composition.db")
    db = inbox.connect(path)
    inbox.migrate(db)
    with db:
        db.executemany(
            "INSERT INTO connections (id, platform, external_id, self_id, label, connected_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            [(1, "gmail", SELF, SELF, "Mail", AT.isoformat()),
             (2, "quo", "+15550001001", "+15550001001", "SMS", AT.isoformat())])
    ctx = SimpleNamespace(db=db, path=path, values=None, draft=None, error=None,
                          read=None, thread=None, identity_id=None, history=None, before=None)
    yield ctx
    ctx.db.close()


def fields(datatable):
    values = {FIELDS[name]: value for name, value in datatable[1:]}
    values["connection_id"] = None if values["connection_id"] == "none" else int(values["connection_id"])
    if values["kind"] == "sms" and values["subject"] == "none":
        values["subject"] = None
    return values


@given("a new Draft has fields:")
def new_draft(composition, datatable):
    composition.values = fields(datatable)


@given(parsers.parse('a saved email Draft with Body "{body}"'))
def saved_draft(composition, body):
    composition.values = {"connection_id": None, "kind": "email", "sender": "", "to": "",
                          "cc": "", "subject": "", "body": body}
    save_draft(composition)


@when("Save Draft is called")
def save_draft(composition):
    composition.draft = inbox.save_draft(composition.db, composition.values)


@when(parsers.parse('Save Draft is called with Body "{body}" at Draft revision {revision:d}'))
def save_edited_draft(composition, body, revision):
    composition.values = composition.values | {"body": body}
    try:
        composition.draft = inbox.save_draft(composition.db, composition.values,
                                             draft_id=composition.draft["id"], expected_revision=revision)
    except inbox.DraftConflict as error:
        composition.error = error


@when(parsers.parse('another writer calls Save Draft with Body "{body}" at Draft revision {revision:d}'))
def other_writer(composition, body, revision):
    db = inbox.connect(composition.path)
    try:
        inbox.save_draft(db, composition.values | {"body": body},
                         draft_id=composition.draft["id"], expected_revision=revision)
    finally:
        db.close()


@when("the Draft store closes and reopens")
def reopen(composition):
    composition.db.close()
    composition.db = inbox.connect(composition.path)
    inbox.migrate(composition.db)


@when("Get Draft is called for that Draft")
def get_draft(composition):
    composition.read = inbox.get_draft(composition.db, composition.draft["id"])


@then(parsers.parse("that Draft has Draft revision {revision:d} and fields:"))
def exact_draft(composition, revision, datatable):
    expected = fields(datatable)
    assert composition.read["revision"] == revision
    assert {k: composition.read[k] for k in expected} == expected


@then("List Drafts returns that Draft")
def list_drafts(composition):
    assert inbox.list_drafts(composition.db) == [composition.read]


@then("no Thread or Activity was created")
def no_work(composition):
    assert composition.db.execute("SELECT count(*) FROM threads").fetchone()[0] == 0
    assert composition.db.execute("SELECT count(*) FROM activities").fetchone()[0] == 0


@then("Save Draft raises Draft Conflict")
def conflict(composition):
    assert isinstance(composition.error, inbox.DraftConflict)


@then(parsers.parse('Get Draft reads Body "{body}" and Draft revision {revision:d}'))
def saved_body(composition, body, revision):
    draft = inbox.get_draft(composition.db, composition.draft["id"])
    assert draft["body"] == body and draft["revision"] == revision


@then(parsers.parse('the edited Draft Body remains "{body}"'))
def edited_body(composition, body):
    assert composition.values["body"] == body


@given(parsers.parse('a captured outgoing Thread for Kind "{kind}" and Value "{value}" without a Person'))
def captured_outgoing(composition, kind, value):
    captured(composition, kind, value, incoming=False)
    assert composition.db.execute("SELECT count(*) FROM persons").fetchone()[0] == 0
    assert composition.db.execute("SELECT count(*) FROM identities").fetchone()[0] == 0


@given(parsers.parse('a captured incoming Thread for Kind "{kind}" and Value "{value}" with a detached Identity'))
def captured_detached(composition, kind, value):
    captured(composition, kind, value, incoming=True)
    composition.identity_id = composition.db.execute("SELECT id FROM identities WHERE kind = ? AND value = ?",
                                                    (kind, value)).fetchone()[0]
    inbox.remove_identity(composition.db, composition.identity_id)


def captured(composition, kind, value, *, incoming):
    is_email = kind == "email"
    data = {"from": value if incoming else SELF, "to": [SELF if incoming else value], "cc": []} if is_email else {"peer": value}
    activity = inbox.Incoming(1 if is_email else 2, SELF if is_email else value,
                             "mailbox" if is_email else "im", "MESSAGE", "<captured@example.test>",
                             inbox.Identity(kind, value) if incoming else None,
                             AT, "Synthetic captured text", title="Synthetic captured work", data=data)
    composition.thread = inbox.ingest(composition.db, activity)


@when(parsers.parse('Recipient History is called for Kind "{kind}" and Value "{value}"'))
def history(composition, kind, value):
    composition.before = "\n".join(composition.db.iterdump())
    composition.history = inbox.recipient_threads(composition.db, kind, value)


@then("Recipient History returns that canonical Thread")
def history_thread(composition):
    assert composition.history == [inbox.get_thread(composition.db, composition.thread.id)]


@then("captured records and Person associations are unchanged")
def unchanged(composition):
    assert "\n".join(composition.db.iterdump()) == composition.before


@then("the captured outgoing Activity has no Identity")
def outgoing_actor(composition):
    assert composition.db.execute("SELECT identity_id FROM activities").fetchone()[0] is None


@then("that Identity remains detached")
def detached(composition):
    assert composition.db.execute("SELECT person_id FROM identities WHERE id = ?",
                                  (composition.identity_id,)).fetchone()[0] is None
