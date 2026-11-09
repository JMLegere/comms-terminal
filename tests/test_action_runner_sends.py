"""Sends and Responds on the Action runner (ADR 0022; PRD 2026-11-08-action-queue FR-AQR-1–6, NFR-AQR-1)."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from test_console import seed_demo  # puts scripts/ on the path
from comms_terminal import actions, inbox, serve
from test_attachment_send import FakeSender

NOW = datetime(2026, 11, 9, 14, 0, tzinfo=timezone.utc)


@pytest.fixture
def db(tmp_path):
    db = inbox.connect(str(tmp_path / "inbox.db"))
    return db, seed_demo.seed(db)


def new_draft(db, subject="Checklist"):
    return inbox.save_draft(db, {"connection_id": "1", "kind": "email", "sender": "jeremy@agentmaple.ai",
                                 "to": "bo@acme.test", "cc": "", "subject": subject, "body": "Hi Bo"})


def sends(db):
    return [(a["state"], json.loads(a["payload"]).get("label")) for a in
            db.execute("SELECT * FROM actions WHERE kind = 'send' ORDER BY created_at")]


def test_a_scheduled_send_becomes_a_send_action_the_runner_carries(db):  # AS-AQR-1, FR-AQR-1
    db, _ = db
    d = new_draft(db)
    inbox.schedule_draft(db, d["id"], NOW)
    assert serve.send_due(db, serve.Senders(made={"1": FakeSender()}), NOW) == 1
    assert sends(db) == [("confirmed", "Checklist · bo@acme.test")]
    [row] = [r for r in actions.queue(db, NOW) if r["kind"] == "email"]
    assert (row["section"], row["outcome"]) == ("sent", "confirmed")


def test_a_pressed_send_is_an_action_carried_at_once(db):  # FR-AQR-5
    db, _ = db
    d = new_draft(db, "Now")
    inbox.send_draft(db, d["id"], d["revision"], FakeSender())
    assert sends(db) == [("confirmed", "Now · bo@acme.test")]


def test_a_draft_already_being_sent_cannot_be_pressed_again(db):  # NFR-AQR-1
    db, _ = db
    d = new_draft(db, "Once")
    inbox.schedule_draft(db, d["id"], NOW)
    assert inbox.claim_due_sends(db, NOW, lambda d: None)  # claimed; the runner has not carried it yet
    sender = FakeSender()
    with pytest.raises(inbox.SendUnknown):
        inbox.send_draft(db, d["id"], d["revision"], sender, again=True)
    assert len(sends(db)) == 1


def test_undo_inside_the_window_leaves_no_send_action(db):  # AS-AQR-4
    db, _ = db
    d = new_draft(db, "Oops")
    inbox.schedule_draft(db, d["id"], NOW + timedelta(seconds=10))
    inbox.schedule_draft(db, d["id"], None)  # Undo clears the send time
    assert serve.send_due(db, serve.Senders(made={"1": FakeSender()}), NOW + timedelta(seconds=11)) == 0
    assert sends(db) == []


def test_an_unknown_send_stays_locked_and_is_never_resent(db):  # AS-AQR-3
    db, _ = db
    d = new_draft(db, "Lost")
    inbox.schedule_draft(db, d["id"], NOW)

    senders = serve.Senders(made={"1": FakeSender(TimeoutError("no answer"))})
    assert serve.send_due(db, senders, NOW) == 0
    assert [s for s, _ in sends(db)] == ["unknown"]
    assert inbox.get_draft(db, d["id"])["sending_at"] is not None
    assert actions.run(db, {}, NOW + timedelta(hours=1), senders=senders) == 0 and len(sends(db)) == 1


def test_a_pressed_action_is_called_interrupted_only_once_it_is_stuck(db):
    db, _ = db
    conn = db.execute("SELECT id FROM connections WHERE platform = 'gmail' LIMIT 1").fetchone()[0]
    fresh = actions.start(db, conn, "send", "x1", {"label": "x"}, False, NOW)
    stuck = actions.start(db, conn, "send", "x2", {"label": "y"}, False, NOW - timedelta(seconds=actions.STUCK + 1))
    actions.run(db, {}, NOW)
    state = dict(db.execute("SELECT id, state FROM actions").fetchall())
    assert (state[fresh], state[stuck]) == ("running", "unknown")


def test_an_unanswered_respond_is_sent_again_by_the_runner(db):  # FR-AQR-4
    db, _ = db
    with db:
        db.execute("INSERT INTO connections (platform, external_id, self_id, label, connected_at)"
                   " VALUES ('gcal', 'gcal-feed', 'me@x.example', 'Google Calendar', ?)", (NOW.isoformat(),))
        db.execute("INSERT INTO events (uid, ics, first_seen_at, changed_at) VALUES ('ev1', ?, ?, ?)",
                   ("BEGIN:VEVENT\r\nUID:ev1\r\nSUMMARY:Pints\r\nDTSTART:20261112T190000Z\r\n"
                    "DTEND:20261112T200000Z\r\nEND:VEVENT\r\n", NOW.isoformat(), NOW.isoformat()))

    class Google:
        def __init__(self, *outs):
            self.outs, self.calls = list(outs), 0

        def respond(self, uid, rid, answer):
            self.calls += 1
            return self.outs.pop(0)
    google = Google({"outcome": "unknown", "error": "Google did not answer"},
                    {"outcome": "confirmed", "status": "accepted"})
    status, _ = serve.handle_respond(db, json.dumps({"event_uid": "ev1", "answer": "accept"}).encode(), google)
    assert status == 502  # the press answers as before
    assert actions.run(db, {"gcal": google}, NOW + timedelta(minutes=1)) == 1
    assert google.calls == 2
    assert db.execute("SELECT state FROM actions WHERE kind = 'respond'").fetchone()[0] == "confirmed"
