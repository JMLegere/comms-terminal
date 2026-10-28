"""Meetings on Threads (ADR 0007; .agents/prd/2026-10-24-organization-actions.md Phase 4).

A Meeting is synchronous comms, recorded as an Activity on a Thread the way a Phone call is: a MESSAGE whose
`data.meeting` says what happened. Google Calendar is a read-only Platform with one Connection; its creation
time is go-live. Each Event UID with someone besides Jeremy, first seen after go-live, is a Channel of
kind meeting holding one Thread, so a repeating series is one Thread. Every Meeting Activity puts the Thread in
Todo: booked (a new Thread), held (an occurrence's end time passed), cancelled (it became a Deleted Event), and
declined (another attendee said no). A reschedule only moves the Event on the Calendar. Jeremy's own
Response (FR-13e) is a Meeting Activity from him that leaves Status as it is; answering an Event with no Thread yet
(booked before go-live) starts its Thread. Internal Events are Meeting Threads too (decision #142).

Nothing here runs a loop: `synced` runs after a calendar read that changed something, `held` in the wake pass.
"""

import json
import re
import secrets
from datetime import date, datetime, timedelta, timezone

import icalendar

from . import calendar as cal, clock, files, inbox, render

EXTERNAL = "google-calendar"  # the Connection's external_id
NOT_PEOPLE = ("calendar.google.com",)  # rooms and group calendars: resource.calendar.google.com, …
PLACES = (("meet.google.com", "Google Meet"), ("zoom.us", "Zoom"), ("teams.microsoft.com", "Microsoft Teams"))
PHONE = re.compile(r"tel:|\+?\d[\d ().-]{8,}\d")
HELD_WINDOW = timedelta(days=2)  # how far back the wake pass looks for ended occurrences (a service restart)


def connect(db) -> int:
    """The Google Calendar Connection, created the first time the service has the calendar credential."""
    with db:
        db.execute("INSERT OR IGNORE INTO connections (platform, external_id, self_id, label, connected_at)"
                   " VALUES ('gcal', ?, ?, 'Google Calendar', ?)", (EXTERNAL, EXTERNAL, clock.now().isoformat(timespec="seconds")))
    return db.execute("SELECT id FROM connections WHERE external_id = ?", (EXTERNAL,)).fetchone()[0]


def _connection(db):
    return db.execute("SELECT * FROM connections WHERE platform = 'gcal'").fetchone()


def _others(o: dict, mine: set[str]) -> list[dict]:
    """The organizer and attendees besides Jeremy, rooms and group calendars left out, organizer first:
    {email, name, state}."""
    people = ([{**o["organizer"], "state": "ACCEPTED"}] if o["organizer"] else []) + o["attendees"]
    out, seen = [], set()
    for p in people:
        domain = p["email"].rsplit("@", 1)[-1]
        if "@" in p["email"] and p["email"] not in seen | mine and not any(domain.endswith(n) for n in NOT_PEOPLE):
            seen.add(p["email"])
            out.append(p)
    return out


def where(o: dict) -> str:
    """Google Meet, Zoom, Microsoft Teams, phone, or in person; "no place set" when the Event names none."""
    text = f"{o['join_url']} {o['location']}"
    for host, name in PLACES:
        if host in text:
            return name
    if PHONE.search(o["location"]):
        return "phone"
    return "in person" if o["location"].strip() else "no place set"


WHAT = {"booked": "Booked", "held": "Held", "cancelled": "Cancelled", "declined": "Declined"}


def _record(db, c, o: dict, what: str, people: list[dict], rid: str, at: datetime, sender: dict | None = None,
            key: str = "") -> inbox.Thread | None:
    """One Meeting Activity on the UID's Thread; None when it is already recorded (the native id repeats)."""
    sender = sender or people[0]
    start, end = clock.local(o["start"]), clock.local(o["end"])
    meeting = {"what": what, "where": where(o), "start": start.isoformat(), "end": end.isoformat(),
               "join": o["join_url"], "uid": o["uid"], "rid": rid,
               "attendees": [{"email": a["email"], "name": a["name"], "state": a["state"]} for a in o["attendees"]]}
    names = ", ".join(p["name"] or p["email"] for p in people)
    content = "\n".join([f"{WHAT[what]} · {o['title']}", f"{meeting['where']} · {start:%a %b %-d %H:%M}–{end:%H:%M}"]
                        + ([f"Join: {o['join_url']}"] if o["join_url"] else []) + [f"With: {names}"])
    others = [p for p in people if p is not sender]  # every other attendee is a Participant (FR-OA-15)
    return inbox.ingest(db, inbox.Incoming(
        c["id"], o["uid"], "meeting", "MESSAGE", f"gcal:{o['uid']}:{rid}:{what}{key}",
        inbox.Identity("email", sender["email"], sender["name"] or None), at, content, title=o["title"],
        data={"meeting": meeting, "to": [p["email"] for p in others]}, channel_name=o["title"],
        synchronous="meeting", participants=tuple(inbox.Identity("email", p["email"], p["name"] or None) for p in others)))


def _parse(ics: str, mine, series=frozenset()) -> dict:
    return cal._occurrence(icalendar.Event.from_ical(ics), mine, set(series), {})


def _threads(db, c) -> dict[str, dict]:
    """Meeting Threads by Event UID: {uid: {id, created_at}}."""
    return {r["native_id"]: dict(r) for r in db.execute(
        "SELECT ch.native_id, t.id, t.created_at FROM channels ch JOIN threads t ON t.channel_id = ch.id"
        " WHERE ch.connection_id = ? AND ch.kind = 'meeting' AND t.merged_into IS NULL", (c["id"],))}


def synced(db, now: datetime | None = None) -> int:
    """After a calendar read that changed something: book each new Event with someone besides Jeremy, and
    record cancellations and other attendees' declines. Returns the Activities recorded."""
    c = _connection(db)
    if c is None:
        return 0
    now, mine, n = now or clock.now(), cal.me(db), 0
    known = _threads(db, c)
    # Booked (FR-OA-12): a UID first seen after go-live, declined and unanswered invites included
    for uid, ics in db.execute(
            "SELECT uid, ics FROM events WHERE deleted_at IS NULL AND uid IN (SELECT uid FROM events GROUP BY uid"
            " HAVING min(first_seen_at) > ?) ORDER BY uid, recurrence_id != '', recurrence_id", (c["connected_at"],)):
        if uid in known:
            continue
        o = _parse(ics, mine)
        if people := _others(o, mine):
            n += _record(db, c, o, "booked", people, "", now) is not None
            known[uid] = {"created_at": clock.iso(now)}
    # Cancelled and declined (FR-OA-14): only what changed after the Thread began
    for uid, rows in _rows(db, known).items():
        booked = _booked_states(db, c, uid)
        series_gone = any(r["recurrence_id"] == "" and r["deleted_at"] for r in rows)
        for r in rows:
            if r["deleted_at"] and datetime.fromisoformat(r["deleted_at"]) > datetime.fromisoformat(
                    known[uid]["created_at"]) and \
                    (r["recurrence_id"] == "" or not series_gone):
                o = _parse(r["ics"], mine)
                if people := _others(o, mine):
                    n += _record(db, c, o, "cancelled", people, r["recurrence_id"], now) is not None
            elif not r["deleted_at"]:
                o = _parse(r["ics"], mine)
                people = _others(o, mine)
                for p in people:
                    if p["state"] == "DECLINED" and booked.get(p["email"]) != "DECLINED":
                        n += _record(db, c, o, "declined", people, r["recurrence_id"], now, sender=p,
                                     key=f":{p['email']}") is not None
    return n


def _rows(db, known: dict) -> dict[str, list]:
    out: dict[str, list] = {}
    if known:
        for r in db.execute(f"SELECT * FROM events WHERE uid IN ({','.join('?' * len(known))})", list(known)):
            out.setdefault(r["uid"], []).append(r)
    return out


def _booked_states(db, c, uid: str) -> dict[str, str]:
    """Each attendee's answer when the Meeting was booked, so a decline already there is not news."""
    r = db.execute("SELECT data FROM activities WHERE native_id = ?", (f"gcal:{uid}::booked",)).fetchone()
    return {a["email"]: a["state"] for a in json.loads(r[0])["meeting"]["attendees"]} if r else {}


def held(db, now: datetime | None = None) -> int:
    """The wake pass: each occurrence of a Meeting Thread's Event that ended since the Thread began, once, as a
    held Meeting Activity (FR-OA-13). Jeremy's declined occurrences and Deleted Events are not held."""
    c = _connection(db)
    if c is None:
        return 0
    known = _threads(db, c)
    if not known:
        return 0
    now, mine, n = now or clock.now(), cal.me(db), 0
    local = clock.local(now)
    for o in cal.expand(db, (local - HELD_WINDOW).date(), local.date() + timedelta(days=1), deleted=False):
        t = known.get(o["uid"])
        if t is None or o["all_day"] or o["state"] == "DECLINED":
            continue
        end = clock.local(o["end"])
        if datetime.fromisoformat(t["created_at"]) < end <= local and (people := _others(o, mine)):
            n += _record(db, c, o, "held", people, cal._rid(o["start"]), end) is not None
    return n


ANSWERED = {"accepted": "Accepted", "tentative": "Maybe", "declined": "Declined"}  # Google responseStatus -> words


def invite_target(db, thread_id: int) -> tuple[str, str | None] | None:
    """The Event a Thread Responds to (FR-IR-2, FR-IR-5): (UID, None) for a Meeting Thread, so the whole series;
    for an email Thread, the UID and occurrence in its newest stored calendar part, the occurrence as Google's
    instance suffix (UTC `YYYYMMDDTHHMMSSZ`, or `YYYYMMDD` all day). None when the Thread holds no invitation.
    Raises KeyError for an unknown Thread."""
    t = inbox.get_thread(db, thread_id)
    ch = db.execute("SELECT ch.kind, ch.native_id, c.platform FROM channels ch"
                    " JOIN connections c ON c.id = ch.connection_id WHERE ch.id = ?", (t.channel_id,)).fetchone()
    if ch["platform"] == "gcal" and ch["kind"] == "meeting":
        return ch["native_id"], None
    raw = files.raw_dir(db)
    for (sha,) in db.execute(
            "SELECT json_extract(data, '$.raw') FROM thread_activities WHERE thread_id = ? AND kind = 'MESSAGE'"
            " AND json_extract(data, '$.raw') IS NOT NULL ORDER BY occurred_at DESC, id DESC", (t.id,)):
        if raw is None or not re.fullmatch(r"[0-9a-f]{64}", str(sha)):
            continue
        body = render.load(str(raw / f"{sha}.eml"))
        ev = body.invite if body else None
        if ev and ev.get("uid"):
            rid = ev.get("recurrence_id")
            if isinstance(rid, datetime):
                rid = rid.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            elif isinstance(rid, date):
                rid = rid.strftime("%Y%m%d")
            return ev["uid"], rid
    return None


def respond_recorded(db, uid: str, status: str, rid: str | None = None) -> int | None:
    """A confirmed Response (FR-IR-6): one Meeting Activity from Jeremy, "Accepted · Pints", on the UID's Meeting
    Thread; Status stays as it is (FR-IR-7). An Event with no Thread yet (booked before go-live) starts one, in
    Todo like a booking. None without the Calendar Connection or the Event in the feed."""
    c = _connection(db)
    if c is None:
        return None
    native_id = f"gcal:{uid}:{rid or ''}:responded:{secrets.token_hex(4)}"
    meeting = {"what": "responded", "answer": status, "uid": uid, "rid": rid or ""}
    if (t := _threads(db, c).get(uid)) is not None:  # inserted directly: ingest would move a Meeting to Todo
        content = f"{ANSWERED[status]} · {inbox.get_thread(db, t['id']).title}"
        with db:
            return db.execute(
                "INSERT INTO activities (thread_id, kind, occurred_at, native_id, content, data)"
                " VALUES (?, 'MESSAGE', ?, ?, ?, ?)",
                (t["id"], clock.iso(clock.now()), native_id, content, json.dumps({"meeting": meeting}))).lastrowid
    r = db.execute("SELECT ics FROM events WHERE uid = ? AND recurrence_id = '' AND deleted_at IS NULL",
                   (uid,)).fetchone()
    if r is None:
        return None
    mine = cal.me(db)
    o = _parse(r["ics"], mine)
    people = _others(o, mine)
    inbox.ingest(db, inbox.Incoming(
        c["id"], uid, "meeting", "MESSAGE", native_id, None, clock.now(), f"{ANSWERED[status]} · {o['title']}",
        title=o["title"], data={"meeting": meeting, "to": [p["email"] for p in people]}, channel_name=o["title"],
        synchronous="meeting", participants=tuple(inbox.Identity("email", p["email"], p["name"] or None) for p in people)))
    return db.execute("SELECT id FROM activities WHERE native_id = ?", (native_id,)).fetchone()[0]
