"""Respond on the service (serve.handle_respond, FR-13e): the edges features/calendar.feature leaves out. Google is
the fake from the calendar steps; the inbox steps' `ctx` holds Jeremy's Gmail Connection. Every Event with someone
besides Jeremy is a Meeting Thread (decision #142), so every recorded answer lands on one."""

import json
import threading
from datetime import date, timedelta

import pytest
from steps.calendar_steps import CRED, event, google, invitation, jeremy_activities, meeting_thread_for, writer_status

from comms_terminal import calendar as cal, clock, gcal, health, inbox, main, meetings, serve
from comms_terminal.tui import _post


def respond(ctx, answer="accept", client=..., *, event_uid=None):
    client = gcal.Client(dict(CRED), None, http=ctx.google) if client is ... else client
    target = {"event_uid": event_uid} if event_uid is not None else {"thread_id": ctx.thread.id}
    return serve.handle_respond(ctx.db, json.dumps({**target, "answer": answer}).encode(), client)


def count(ctx, thread_id):
    return ctx.db.execute("SELECT count(*) FROM activities WHERE thread_id = ?", (thread_id,)).fetchone()[0]


def test_malformed_requests_and_unknown_threads_are_refused(ctx):
    meeting_thread_for(ctx, event(ctx, "Pints", "brendan.wood@snaptrade.com"))
    client = gcal.Client(dict(CRED), None, http=ctx.google)
    for body in (b"{", b"[]", json.dumps({"thread_id": str(ctx.thread.id), "answer": "accept"}).encode(),
                 json.dumps({"thread_id": ctx.thread.id, "answer": "yes"}).encode(),
                 json.dumps({"thread_id": 2**70, "answer": "accept"}).encode(),
                 json.dumps({"thread_id": ctx.thread.id, "answer": "accept", "note": "x"}).encode()):
        assert serve.handle_respond(ctx.db, body, client)[0] == 400
    assert serve.handle_respond(ctx.db, b'{"thread_id": 999, "answer": "accept"}', client) == (
        404, {"outcome": "refused", "error": "No such Thread"})
    assert ctx.google.requests == []


@pytest.mark.parametrize("answer", [[], {}, ["accept"], {"value": "accept"}, None, True, 1, "yes"])
def test_malformed_answers_are_refused_for_both_targets(ctx, monkeypatch, answer):
    uid = event(ctx, "Pints", "brendan.wood@snaptrade.com")
    meeting_thread_for(ctx, uid)
    client = gcal.Client(dict(CRED), None, http=ctx.google)
    monkeypatch.setattr(client, "respond", lambda *args: pytest.fail("Malformed answer reached Google Calendar"))
    before = ctx.db.total_changes
    for target in ({"thread_id": ctx.thread.id}, {"event_uid": uid}):
        status, out = serve.handle_respond(ctx.db, json.dumps({**target, "answer": answer}).encode(), client)
        assert (status, out["outcome"]) == (400, "refused")
    assert ctx.db.total_changes == before
    assert ctx.google.requests == []


def test_invalid_event_targets_are_refused_before_google(ctx, monkeypatch):
    uid = event(ctx, "Pints", "brendan.wood@snaptrade.com")
    meeting_thread_for(ctx, uid)
    client = gcal.Client(dict(CRED), None, http=ctx.google)
    monkeypatch.setattr(client, "respond", lambda *args: pytest.fail("Invalid target reached Google Calendar"))
    before = ctx.db.total_changes
    requests = [{"event_uid": value, "answer": "accept"} for value in (None, False, 1, [], {}, "")]
    requests += [{"thread_id": ctx.thread.id, "event_uid": uid, "answer": "accept"},
                 {"event_uid": uid, "answer": "accept", "note": "x"},
                 {"event_uid": uid, "answer": "accept", "recurrence_id": "20261112T190000Z"},
                 {"event_uid": uid}, {"answer": "accept"}]
    for req in requests:
        status, out = serve.handle_respond(ctx.db, json.dumps(req).encode(), client)
        assert (status, out["outcome"]) == (400, "refused")
    for missing in ("missing@google.com", " ", f" {uid}", f"{uid} "):
        assert respond(ctx, client=client, event_uid=missing) == (
            404, {"outcome": "refused", "error": "No such Event"})
    assert ctx.db.total_changes == before
    assert ctx.google.requests == []


@pytest.mark.parametrize("live_occurrence", [False, True])
def test_a_deleted_event_is_refused_even_with_a_live_occurrence(ctx, monkeypatch, live_occurrence):
    uid = event(ctx, "Serenity touch-in", "ryan@serenity.example", weekly=True)
    meeting_thread_for(ctx, uid)
    remaining = ("BEGIN:VEVENT\r\n" + ctx.feed[uid] + "\r\nRECURRENCE-ID:20261112T190000Z\r\nEND:VEVENT\r\n"
                 if live_occurrence else "")
    cal.sync(ctx.db, "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n" + remaining + "END:VCALENDAR\r\n")
    assert ctx.db.execute("SELECT count(*) FROM events WHERE deleted_at IS NULL").fetchone()[0] == int(live_occurrence)
    client = gcal.Client(dict(CRED), None, http=ctx.google)
    monkeypatch.setattr(client, "respond", lambda *args: pytest.fail("Deleted Event reached Google Calendar"))
    before = ctx.db.total_changes
    assert respond(ctx, client=client, event_uid=uid) == (
        404, {"outcome": "refused", "error": "No such Event"})
    assert ctx.db.total_changes == before
    assert ctx.google.requests == []


def test_a_thread_without_an_invitation_is_refused_before_google(ctx):
    google(ctx)
    ctx.thread = inbox.ingest(ctx.db, inbox.Incoming(1, "jeremy@agentmaple.com", "mailbox", "MESSAGE", "<m1@test>",
                                                     inbox.Identity("email", "sam@stmec.example"),
                                                     ctx.clock, "Hello", title="Hello"))
    assert respond(ctx) == (400, {"outcome": "refused", "error": "This Thread holds no invitation."})
    assert ctx.google.requests == []


def test_without_the_sign_in_a_response_is_unavailable(ctx):
    meeting_thread_for(ctx, event(ctx, "Pints", "brendan.wood@snaptrade.com"))
    before = count(ctx, ctx.thread.id)
    assert respond(ctx, client=None) == (503, {"outcome": "unavailable",
                                               "error": "Google Calendar writes are not connected"})
    assert count(ctx, ctx.thread.id) == before


def test_an_occurrence_invitation_answers_that_occurrence_only(ctx, raw_dir):
    uid = event(ctx, "Serenity touch-in", "ryan@serenity.example", weekly=True)
    invitation(ctx, uid, "Serenity touch-in", "ryan@serenity.example",
               "\r\nRECURRENCE-ID;TZID=America/Halifax:20261112T150000")
    email_thread = ctx.thread
    meeting_thread_for(ctx, uid)
    ctx.thread = email_thread
    status, out = respond(ctx, "decline")
    assert (status, out) == (200, {"outcome": "confirmed", "status": "declined"})
    (patch,) = ctx.google.writes()
    assert patch["path"].endswith("/events/id1_20261112T190000Z")  # 15:00 AST is 19:00 UTC
    data = json.loads(ctx.db.execute("SELECT data FROM activities WHERE thread_id = ? ORDER BY id DESC",
                                     (ctx.meeting.id,)).fetchone()[0])
    assert data["meeting"] == {"what": "responded", "answer": "declined", "uid": uid, "rid": "20261112T190000Z"}


def test_an_internal_event_is_a_meeting_thread_and_records_the_answer(ctx):
    meeting_thread_for(ctx, event(ctx, "Standup", "logan@agentmaple.com", guest="logan@agentmaple.com"))
    assert respond(ctx)[1]["outcome"] == "confirmed"
    assert jeremy_activities(ctx, ctx.thread.id)[-1] == "Accepted · Standup"


def test_answering_an_event_booked_before_go_live_starts_its_meeting_thread(ctx, raw_dir):
    uid = event(ctx, "Board prep", "ana@acme.example")
    feed = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\n" + ctx.feed[uid] + "\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    cal.sync(ctx.db, feed, (clock.now() - timedelta(days=1)).isoformat(timespec="seconds"))  # before go-live
    meetings.connect(ctx.db)
    assert meetings.synced(ctx.db) == 0  # not booked: first seen before go-live
    invitation(ctx, uid, "Board prep", "ana@acme.example")
    assert respond(ctx)[1]["outcome"] == "confirmed"
    tid = ctx.db.execute("SELECT t.id FROM threads t JOIN channels ch ON ch.id = t.channel_id"
                         " WHERE ch.kind = 'meeting' AND ch.native_id = ?", (uid,)).fetchone()[0]
    t = inbox.get_thread(ctx.db, tid)
    assert (t.title, t.status, t.status_detail) == ("Board prep", "TODO", "CREATED")  # like a booking
    assert jeremy_activities(ctx, tid) == ["Accepted · Board prep"]


@pytest.mark.parametrize("answer,status,text", [
    ("accept", "accepted", "Accepted"), ("maybe", "tentative", "Maybe"), ("decline", "declined", "Declined"),
])
def test_an_event_response_starts_its_thread_and_leaves_the_feed_unchanged(ctx, answer, status, text):
    uid = event(ctx, "Board prep", "ana@acme.example", weekly=True, guest="ana@acme.example")
    feed = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\n" + ctx.feed[uid] + "\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    cal.sync(ctx.db, feed, (clock.now() - timedelta(days=1)).isoformat(timespec="seconds"))
    meetings.connect(ctx.db)
    assert meetings.synced(ctx.db) == 0
    assert ctx.db.execute("SELECT count(*) FROM threads").fetchone()[0] == 0
    before = [dict(r) for r in ctx.db.execute("SELECT * FROM events")]
    provider = cal.provider(ctx.db)
    assert respond(ctx, answer, event_uid=uid) == (200, {"outcome": "confirmed", "status": status})
    patch = writer_status(ctx, status)
    assert patch["path"].endswith("/events/id1")
    assert [(r["method"], r["query"]) for r in ctx.google.requests] == [
        ("GET", {"iCalUID": [uid]}), ("PATCH", {"sendUpdates": ["all"]})]
    (activity,) = ctx.db.execute("SELECT * FROM activities").fetchall()
    assert (activity["kind"], activity["identity_id"], activity["content"]) == (
        "MESSAGE", None, f"{text} · Board prep")
    assert json.loads(activity["data"]) == {
        "meeting": {"what": "responded", "answer": status, "uid": uid, "rid": ""},
        "to": ["ana@acme.example"]}
    thread = inbox.get_thread(ctx.db, activity["thread_id"])
    assert (thread.title, thread.status, thread.status_detail) == ("Board prep", "TODO", "CREATED")
    assert [dict(r) for r in ctx.db.execute("SELECT * FROM events")] == before
    assert cal.provider(ctx.db) == provider
    assert cal.expand(ctx.db, date(2026, 11, 5), date(2026, 11, 6))[0]["state"] == "NEEDS-ACTION"


def test_an_event_response_leaves_existing_thread_status_unchanged(ctx):
    uid = event(ctx, "Pints", "brendan.wood@snaptrade.com")
    meeting_thread_for(ctx, uid)
    inbox.act(ctx.db, ctx.thread.id, inbox.Done())
    before = inbox.get_thread(ctx.db, ctx.thread.id)
    assert (before.status, before.status_detail) == ("DONE", "DONE_MANUALLY_SET")
    assert respond(ctx, event_uid=uid)[1]["outcome"] == "confirmed"
    after = inbox.get_thread(ctx.db, ctx.thread.id)
    assert (after.status, after.status_detail) == (before.status, before.status_detail)
    assert jeremy_activities(ctx, after.id)[-1] == "Accepted · Pints"


@pytest.mark.parametrize("failure,outcome,status", [
    ("cancelled", "refused", 400), ("timeout", "unknown", 502),
    ("revoked", "unavailable", 503), ("disconnected", "unavailable", 503),
])
def test_unconfirmed_event_responses_record_nothing(ctx, failure, outcome, status):
    uid = event(ctx, "Board prep", "ana@acme.example")
    feed = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\n" + ctx.feed[uid] + "\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    cal.sync(ctx.db, feed)
    meetings.connect(ctx.db)
    if failure == "cancelled":
        ctx.google.events["id1"]["status"] = "cancelled"
    elif failure == "timeout":
        ctx.google.fail = TimeoutError("timed out")
    elif failure == "revoked":
        ctx.google.token = (400, {"error": "invalid_grant"})
    client = None if failure == "disconnected" else gcal.Client(dict(CRED), None, http=ctx.google)
    before = [dict(r) for r in ctx.db.execute("SELECT * FROM events")]
    provider = cal.provider(ctx.db)
    code, out = respond(ctx, client=client, event_uid=uid)
    assert (code, out["outcome"]) == (status, outcome)
    assert ctx.db.execute("SELECT count(*) FROM activities").fetchone()[0] == 0
    assert ctx.db.execute("SELECT count(*) FROM threads").fetchone()[0] == 0
    assert len(ctx.google.writes()) == (1 if failure == "timeout" else 0)
    assert [dict(r) for r in ctx.db.execute("SELECT * FROM events")] == before
    assert cal.provider(ctx.db) == provider


def gcal_connection(ctx):
    return ctx.db.execute("SELECT * FROM connections WHERE platform = 'gcal'").fetchone()


@pytest.mark.parametrize("event_target", [False, True])
def test_a_later_answer_clears_broken_writes(ctx, event_target):
    uid = event(ctx, "Pints", "brendan.wood@snaptrade.com")
    meeting_thread_for(ctx, uid)
    event_uid = uid if event_target else None
    ctx.google.token = (400, {"error": "invalid_grant", "error_description": "Token rt-1 has been revoked"})
    assert respond(ctx, event_uid=event_uid)[0] == 503
    problem = health.problem(gcal_connection(ctx))
    assert problem.startswith(gcal.WRITES_BROKEN) and "rt-1" not in problem
    ctx.google.token = (200, {"access_token": "at-2", "expires_in": 3600})
    assert respond(ctx, "maybe", event_uid=event_uid)[0] == 200
    assert health.problem(gcal_connection(ctx)) is None


def test_the_respond_route_is_on_the_reply_socket(tmp_path):
    path, sock = str(tmp_path / "inbox.db"), str(tmp_path / "s.sock")
    inbox.migrate(inbox.connect(path))
    server = serve.reply_server(path, sock, serve.Senders())
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        assert _post(sock, "/respond", {"thread_id": 1, "answer": "accept"}) == (
            404, {"outcome": "refused", "error": "No such Thread"})
        assert _post(sock, "/respond", {"event_uid": "missing@google.com", "answer": "accept"}) == (
            404, {"outcome": "refused", "error": "No such Event"})
    finally:
        server.shutdown()


def test_no_agent_command_responds(capsys):
    with pytest.raises(SystemExit):
        main.main(["--help"])
    assert "respond" not in capsys.readouterr().out.lower()
