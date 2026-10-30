"""Send later: ctrl+enter can schedule a Draft for tomorrow or next Monday, 7-9 am; the service sends it when due."""

from datetime import datetime, timedelta, timezone

import pytest

from test_console import seed_demo  # puts scripts/ on the path
from comms_terminal import inbox, serve
from comms_terminal.sequences import LOCAL
from test_attachment_send import FakeSender


class Recorder(FakeSender):
    def __init__(self, result="<sent-1@example.test>"):
        super().__init__(result)
        self.calls = []

    def send(self, thread, text, to, cc, history, files=()):
        self.calls.append((text, to, cc))
        return self._done(files)


@pytest.fixture
def db(tmp_path):
    db = inbox.connect(str(tmp_path / "inbox.db"))
    return db, seed_demo.seed(db)


def local(*args):
    return datetime(*args, tzinfo=LOCAL)


def test_send_time_is_tomorrow_or_next_monday_between_7_and_9_am():
    friday_night = local(2026, 10, 16, 22, 30)
    assert inbox.send_time("tomorrow", friday_night, lambda: 0) == local(2026, 10, 17, 7, 0)
    assert inbox.send_time("monday", friday_night, lambda: 0.9999) == local(2026, 10, 19, 8, 59)
    monday = local(2026, 10, 19, 6, 0)
    assert inbox.send_time("monday", monday, lambda: 0.5) == local(2026, 10, 26, 8, 0)  # a week out, never today
    assert inbox.send_time("tomorrow", local(2026, 10, 18, 23, 59), lambda: 0).date() == local(2026, 10, 19).date()
    for _ in range(200):
        at = inbox.send_time("tomorrow", friday_night).astimezone(LOCAL)
        assert at.date() == local(2026, 10, 17).date() and 7 <= at.hour < 9


def test_due_reply_sends_with_its_scheduled_recipients_once(db):
    db, ids = db
    now = datetime(2026, 10, 17, 12, tzinfo=timezone.utc)
    d = inbox.schedule_reply(db, ids["ana"], "Tuesday works.", ["ana@acme.com"], [], now + timedelta(minutes=5))
    inbox.save_reply_draft(db, ids["ana"], "Tuesday works for us.")  # an edit keeps the schedule and recipients
    assert (inbox.get_draft(db, d["id"])["to"], inbox.get_draft(db, d["id"])["cc"]) == ("ana@acme.com", "")
    sender = Recorder()
    senders = serve.Senders(made={"1": sender})
    assert serve.send_due(db, senders, now) == 0 and sender.calls == []
    assert serve.send_due(db, senders, now + timedelta(minutes=5)) == 1
    assert sender.calls == [("Tuesday works for us.", ["ana@acme.com"], [])]
    assert inbox.reply_draft(db, ids["ana"]) is None
    assert serve.send_due(db, senders, now + timedelta(hours=1)) == 0 and len(sender.calls) == 1


def test_refused_scheduled_send_keeps_the_draft_with_its_error_and_is_not_retried(db):
    db, ids = db
    now = datetime(2026, 10, 17, 12, tzinfo=timezone.utc)
    inbox.schedule_reply(db, ids["ana"], "Hi", ["ana@acme.com"], [], now)
    senders = serve.Senders(made={"1": Recorder(inbox.SendFailed("HTTP 422 bad address"))})
    assert serve.send_due(db, senders, now) == 0
    d = inbox.reply_draft(db, ids["ana"])
    assert (d["body"], d["send_at"], d["send_error"]) == ("Hi", None, "HTTP 422 bad address")
    assert serve.send_due(db, senders, now + timedelta(hours=1)) == 0
    assert inbox.schedule_draft(db, d["id"], None)["send_error"] is None  # rescheduling or unscheduling clears it


def test_due_new_draft_sends_as_a_new_thread(db):
    db, _ = db
    now = datetime(2026, 10, 17, 12, tzinfo=timezone.utc)
    d = inbox.save_draft(db, {"connection_id": "1", "kind": "email", "sender": "jeremy@agentmaple.ai",
                              "to": "bo@acme.test", "cc": "", "subject": "Checklist", "body": "Hi Bo"})
    inbox.schedule_draft(db, d["id"], now)
    assert serve.send_due(db, serve.Senders(made={"1": FakeSender()}), now) == 1
    assert d["id"] not in {x["id"] for x in inbox.list_drafts(db)}
    assert db.execute("SELECT count(*) FROM threads WHERE title = 'Checklist'").fetchone()[0] == 1


def test_ctrl_enter_in_a_reply_box_schedules_it_for_tomorrow(tmp_path):
    from test_console import run, seeded
    from comms_terminal.tui import Console, Pick
    path, ids = seeded(tmp_path)
    sent, seen = [], {}
    app = Console(path, reply=lambda *a: sent.append(a) or {})

    async def open_ana(app, pilot):
        app.main.open_thread(ids["ana"])

    async def menu(app, pilot):
        seen["menu"] = isinstance(app.screen, Pick)

    async def check(app, pilot):
        box = app.main.thread_view.query_one(".replybox")
        seen["open"] = box.has_class("open")
        seen["idle"] = str(box.query_one(".idle").render())

    run(app, [open_ana, "r", *"Tuesday works.", "ctrl+enter", menu, "down", "enter", check])
    d = inbox.reply_draft(inbox.connect(path), ids["ana"])
    at = datetime.fromisoformat(d["send_at"]).astimezone(LOCAL)
    assert seen["menu"] and sent == [] and not seen["open"] and "Sends" in seen["idle"]
    assert (d["body"], d["to"], d["cc"]) == ("Tuesday works.", "ana@acme.com", "")
    assert at.date() == datetime.now(LOCAL).date() + timedelta(days=1) and 7 <= at.hour < 9
