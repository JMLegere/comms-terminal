"""Calendar steps (features/calendar.feature): invite Responses, FR-13e.

Respond goes through serve.handle_respond with a real gcal.Client whose HTTP is a fake Google: Events by iCalUID,
each Calendar request recorded. Meeting Threads come from a feed read after go-live, as in the service. Builds on
the inbox steps' `ctx` (its Gmail Connection is Jeremy's address), so their Status and Activity checks apply.
AS-IR-7 drives the real console (Textual's test driver) on a copy of the database.
"""

import json
import urllib.parse
from datetime import date, timedelta
from email.message import EmailMessage

import pytest
from pytest_bdd import given, parsers, then, when

from comms_terminal import calendar as cal, clock, files, gcal, health, inbox, meetings, serve

ME = "jeremy@agentmaple.com"
CRED = {"client_id": "cid.apps.example", "client_secret": "s3cret", "refresh_token": "rt-1"}
ANSWER = {"Accept": "accept", "Maybe": "maybe", "Decline": "decline"}


class Google:
    """A fake Google Calendar: Events by id; every Calendar request is recorded (token refreshes are not)."""

    def __init__(self):
        self.events, self.requests, self.fail = {}, [], None
        self.token = (200, {"access_token": "at-1", "expires_in": 3600})

    def __call__(self, method, url, body, headers, timeout=20):
        u = urllib.parse.urlsplit(url)
        if u.netloc == "oauth2.googleapis.com":
            return self.token[0], {}, json.dumps(self.token[1]).encode()
        self.requests.append({"method": method, "path": u.path, "query": urllib.parse.parse_qs(u.query),
                              "body": json.loads(body) if body else None})
        if self.fail and method == "PATCH":
            raise self.fail
        if method == "GET" and u.path.endswith("/events"):
            uid = urllib.parse.parse_qs(u.query)["iCalUID"][0]
            return 200, {}, json.dumps({"items": [e for e in self.events.values() if e["iCalUID"] == uid]}).encode()
        eid = u.path.rsplit("/", 1)[1]  # an occurrence is "<series id>_<start>"
        ev = self.events.setdefault(eid, {**self.events[eid.split("_")[0]], "id": eid})
        if method == "PATCH":
            ev["attendees"] = json.loads(body)["attendees"]
        return 200, {}, json.dumps(ev).encode()

    def writes(self):
        return [r for r in self.requests if r["method"] == "PATCH"]


@pytest.fixture
def raw_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(files, "raw_dir", lambda db: tmp_path / "raw")


def google(ctx) -> Google:
    if not hasattr(ctx, "google"):
        ctx.google, ctx.feed, ctx.uids = Google(), {}, {}
    return ctx.google


def event(ctx, title, organizer, state="NEEDS-ACTION", weekly=False, guest="brendan.wood@snaptrade.com"):
    """One Event in the feed and in the fake Google; returns its UID."""
    g = google(ctx)
    uid = ctx.uids.setdefault(title, f"ev{len(ctx.uids) + 1}@google.com")
    people = [organizer] + ([guest] if guest != organizer else [])
    ctx.feed[uid] = "\r\n".join(
        [f"UID:{uid}", f"SUMMARY:{title}", "DTSTAMP:20261027T150000Z", "DTSTART:20261105T190000Z",
         "DTEND:20261105T200000Z", f"ORGANIZER:mailto:{organizer}", f"ATTENDEE;PARTSTAT={state}:mailto:{ME}"]
        + [f"ATTENDEE;PARTSTAT=ACCEPTED:mailto:{p}" for p in people if p != ME]
        + (["RRULE:FREQ=WEEKLY"] if weekly else []))
    eid = f"id{len(g.events) + 1}"
    g.events[eid] = {
        "id": eid, "etag": '"e1"', "status": "confirmed", "iCalUID": uid,
        "organizer": {"email": organizer, **({"self": True} if organizer == ME else {})},
        "attendees": [{"email": p, "responseStatus": "accepted", **({"organizer": True} if p == organizer else {})}
                      for p in people if p != ME]
                     + ([{"email": ME, "self": True, "responseStatus": "needsAction"}] if organizer != ME else []),
        **({"recurrence": ["RRULE:FREQ=WEEKLY"]} if weekly else {})}
    return uid


def meeting_thread_for(ctx, uid):
    """The feed read after go-live books the Event's Meeting Thread (FR-OA-12)."""
    meetings.connect(ctx.db)
    later = clock.now() + timedelta(minutes=1)
    feed = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"
            + "".join(f"BEGIN:VEVENT\r\n{e}\r\nEND:VEVENT\r\n" for e in ctx.feed.values()) + "END:VCALENDAR\r\n")
    cal.sync(ctx.db, feed, later.isoformat(timespec="seconds"))
    meetings.synced(ctx.db, later)
    tid = ctx.db.execute("SELECT t.id FROM threads t JOIN channels ch ON ch.id = t.channel_id"
                         " WHERE ch.kind = 'meeting' AND ch.native_id = ?", (uid,)).fetchone()[0]
    ctx.meeting = ctx.thread = inbox.get_thread(ctx.db, tid)


def writer_status(ctx, status):
    """The one write: only Jeremy's entry carries `status`, everyone else is untouched, every guest notified."""
    (patch,) = ctx.google.writes()
    mine = [a for a in patch["body"]["attendees"] if a.get("self")]
    assert [a["responseStatus"] for a in mine] == [status]
    assert all(a["responseStatus"] == "accepted" for a in patch["body"]["attendees"] if not a.get("self"))
    assert patch["query"]["sendUpdates"] == ["all"]
    return patch


def jeremy_activities(ctx, thread_id):
    return [r[0] for r in ctx.db.execute("SELECT content FROM activities WHERE thread_id = ? AND identity_id IS NULL"
                                         " ORDER BY id", (thread_id,))]


@given(parsers.parse('a Meeting Thread for the Event "{title}" organized by "{organizer}" with Jeremy\'s Invite state "{state}"'))
def meeting_thread_with_state(ctx, title, organizer, state):
    meeting_thread_for(ctx, event(ctx, title, organizer, {"Unanswered": "NEEDS-ACTION"}[state]))


@given(parsers.parse('a Meeting Thread for the Event "{title}" organized by "{organizer}"'))
def meeting_thread(ctx, title, organizer):
    meeting_thread_for(ctx, event(ctx, title, organizer))


@given(parsers.parse('a Meeting Thread for the weekly Event "{title}"'))
def weekly_meeting_thread(ctx, title):
    meeting_thread_for(ctx, event(ctx, title, "ryan@serenity.example", weekly=True))


@given("a Meeting Thread for an Event organized by Jeremy")
def own_meeting_thread(ctx):
    meeting_thread_for(ctx, event(ctx, "STMEC demo", ME, state="ACCEPTED", guest="sam@stmec.example"))


@given("a Meeting Thread for the same Event")
def meeting_thread_same_event(ctx):
    meeting_thread_for(ctx, ctx.invite_uid)
    ctx.thread = ctx.email_thread


@given(parsers.parse('an invitation email Thread whose stored message carries a calendar part for the Event "{title}" organized by "{organizer}"'))
def invitation_email_thread(ctx, raw_dir, title, organizer):
    ctx.invite_uid = event(ctx, title, organizer)
    invitation(ctx, ctx.invite_uid, title, organizer)


def invitation(ctx, uid, title, organizer, extra=""):
    """An invitation email Thread whose stored raw message carries the Event's calendar part (`extra` adds lines)."""
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"], msg["Message-ID"] = organizer, ME, f"Invitation: {title}", "<inv1@test>"
    msg.set_content(f"You have been invited to {title}.")
    msg.add_alternative("BEGIN:VCALENDAR\r\nMETHOD:REQUEST\r\nBEGIN:VEVENT\r\n" + ctx.feed[uid] + extra
                        + "\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n", subtype="calendar")
    sha = files.store_raw(ctx.db, msg.as_bytes())
    ctx.email_thread = ctx.thread = inbox.ingest(ctx.db, inbox.Incoming(
        1, ME, "mailbox", "MESSAGE", "<inv1@test>", inbox.Identity("email", organizer), clock.now(),
        "You have been invited.", title=f"Invitation: {title}", data={"raw": sha}))
    return ctx.thread


@given(parsers.parse('the Thread has Status "{status}" and Status Detail "{detail}"'))
def thread_status(ctx, status, detail):
    with ctx.db:
        ctx.db.execute("UPDATE threads SET status = ?, status_detail = ? WHERE id = ?", (status, detail, ctx.thread.id))


@given("the Google Calendar Writer times out")
def writer_times_out(ctx):
    google(ctx).fail = TimeoutError("timed out")


@given("Google refuses the refresh token")
def refresh_refused(ctx):
    google(ctx).token = (400, {"error": "invalid_grant"})


@when(parsers.parse('Jeremy Responds "{answer}" on that Thread'))
@when(parsers.parse('Jeremy Responds "{answer}" on the invitation email Thread'))
def respond(ctx, answer):
    ctx.before = ctx.db.execute("SELECT count(*) FROM activities WHERE thread_id = ?", (ctx.thread.id,)).fetchone()[0]
    client = gcal.Client(dict(CRED), None, http=ctx.google)
    body = json.dumps({"thread_id": ctx.thread.id, "answer": ANSWER[answer]}).encode()
    ctx.status, ctx.out = serve.handle_respond(ctx.db, body, client)


@when("Jeremy opens the Response picker on that Thread and presses Escape")
def picker_escape(ctx, tmp_path):
    """The real console on a copy of the database, with a recording service in place of the socket."""
    import asyncio
    import contextlib
    import sqlite3

    from comms_terminal.tui import Console

    path = str(tmp_path / "console.db")
    with contextlib.closing(sqlite3.connect(path)) as copy:
        ctx.db.backup(copy)
    ctx.before = ctx.db.execute("SELECT count(*) FROM activities WHERE thread_id = ?", (ctx.thread.id,)).fetchone()[0]
    ctx.respond_calls = []
    app = Console(path, respond=lambda payload: ctx.respond_calls.append(payload) or {"outcome": "confirmed"})

    async def go():
        async with app.run_test(size=(200, 50)) as pilot:
            await pilot.pause()
            app.main.open_thread(ctx.thread.id)
            await pilot.pause(0.2)
            await pilot.press("i", "escape")
            await pilot.pause(0.2)
    asyncio.run(go())
    with contextlib.closing(sqlite3.connect(path)) as copy:  # the console's copy is the one it could change
        ctx.copy_activities = copy.execute("SELECT count(*) FROM activities WHERE thread_id = ?",
                                           (ctx.thread.id,)).fetchone()[0]


@then(parsers.parse('the Google Calendar Writer sets Jeremy\'s response to "{status}" for the whole Event and notifies every guest'))
def sets_whole_event(ctx, status):
    patch = writer_status(ctx, status)
    assert "_" not in patch["path"].rsplit("/", 1)[1]


@then(parsers.parse('the Google Calendar Writer sets Jeremy\'s response to "{status}" for the Event with that UID'))
def sets_by_uid(ctx, status):
    writer_status(ctx, status)
    assert ctx.google.requests[0]["query"]["iCalUID"] == [ctx.invite_uid]


@then(parsers.parse('the Google Calendar Writer sets Jeremy\'s response to "{status}" for the series, not one occurrence'))
def sets_series(ctx, status):
    patch = writer_status(ctx, status)
    series = next(e for e in ctx.google.events.values() if e.get("recurrence"))
    assert patch["path"].endswith(f"/events/{series['id']}")


@then(parsers.parse('the outcome is "{outcome}"'))
def outcome_is(ctx, outcome):
    assert ctx.out["outcome"] == outcome and ctx.status == serve.RESPOND_STATUS[outcome]


@then(parsers.parse('the outcome is "{outcome}" with the reason "{reason}"'))
def outcome_with_reason(ctx, outcome, reason):
    assert ctx.out == {"outcome": outcome, "error": reason}


@then(parsers.parse('the Thread has a Meeting Activity from Jeremy saying "{text}"'))
def thread_activity(ctx, text):
    assert jeremy_activities(ctx, ctx.thread.id)[-1] == text


@then(parsers.parse('the Meeting Thread has a Meeting Activity from Jeremy saying "{text}"'))
def meeting_thread_activity(ctx, text):
    assert jeremy_activities(ctx, ctx.meeting.id)[-1] == text


@then("the Google Calendar Writer received no request")
def no_request(ctx):
    assert ctx.respond_calls == [] and ctx.google.requests == [] and ctx.copy_activities == ctx.before


@then("Google Calendar received no write")
def no_write(ctx):
    assert ctx.google.writes() == []


@then("Google Calendar received exactly one write")
def one_write(ctx):
    assert len(ctx.google.writes()) == 1


@then("Google Calendar writes health is broken")
def writes_broken(ctx):
    c = ctx.db.execute("SELECT * FROM connections WHERE platform = 'gcal'").fetchone()
    assert health.problem(c).startswith(gcal.WRITES_BROKEN)


async def open_event_page(app, pilot, uid, day=date(2026, 11, 5)):
    """Open a synthetic feed Event through Calendar's day pane and Enter."""
    from comms_terminal.tui import DayList, EventPage

    app.main.go("calendar")
    await pilot.pause(0.2)
    day_list = app.main.query_one(DayList)
    day_list.show(day, cal.items(app.db, day, day + timedelta(days=1)))
    day_list.highlighted = 1 + next(n for n, item in enumerate(day_list.items) if item.get("uid") == uid)
    day_list.focus()
    await pilot.press("enter")
    await pilot.pause(0.2)
    assert isinstance(app.main.page, EventPage)


@when(parsers.parse('Jeremy Responds "{answer}" from that Event page in Calendar'))
def respond_from_event_page(ctx, tmp_path, answer):
    import asyncio
    import contextlib
    import sqlite3
    import threading

    from comms_terminal.tui import Console, StatusLine

    uid = ctx.db.execute("SELECT native_id FROM channels WHERE id = ?", (ctx.thread.channel_id,)).fetchone()[0]
    path = str(tmp_path / "event-console.db")
    with contextlib.closing(sqlite3.connect(path)) as copy:
        ctx.db.backup(copy)
    ctx.respond_calls = []
    done = threading.Event()

    def respond(payload):
        ctx.respond_calls.append(payload)
        with contextlib.closing(inbox.connect(path)) as db:
            ctx.status, ctx.out = serve.handle_respond(db, json.dumps(payload).encode(),
                                                      gcal.Client(dict(CRED), None, http=ctx.google))
        done.set()
        return ctx.out

    app = Console(path, respond=respond)

    async def go():
        async with app.run_test(size=(200, 50)) as pilot:
            await pilot.pause()
            await open_event_page(app, pilot, uid)
            await pilot.press("i")
            ctx.event_picker = app.main.query_one(StatusLine).shown
            await pilot.press({"Accept": "y", "Maybe": "m", "Decline": "n"}[answer])
            assert await asyncio.to_thread(done.wait, 3)
            await pilot.pause(0.2)
            ctx.event_state = app.main.page.event["state"]
            ctx.event_body = str(app.main.page.query_one("#eventbody").render())
    asyncio.run(go())
    assert ctx.respond_calls == [{"event_uid": uid, "answer": ANSWER[answer]}]
    with contextlib.closing(sqlite3.connect(path)) as copy:
        copy.backup(ctx.db)


@then(parsers.parse('the Event Response picker says "{scope}"'))
def event_picker_scope(ctx, scope):
    assert scope in ctx.event_picker


@then(parsers.parse('the Event page still shows Invite state "{state}" from the feed'))
def event_feed_state(ctx, state):
    assert ctx.event_state == {"Unanswered": "NEEDS-ACTION"}[state]
    assert state in ctx.event_body
