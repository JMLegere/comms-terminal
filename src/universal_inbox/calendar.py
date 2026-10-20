"""Jeremy's work calendar (ADR 0005). The service reads the Event provider, Google Calendar's secret iCal address,
every 30 seconds; `sync` stores each Event as Google gives it and keeps every Event ever seen: one that leaves the
feed becomes a Deleted Event. `expand` turns stored Events into occurrences for any range, so repeating Events
have no horizon; `items` adds the inbox's own dated things (Calendar Items) for the Calendar page."""

import re
import urllib.request
from datetime import date, datetime, time, timedelta, timezone

import icalendar
import recurring_ical_events

CREDENTIAL = "google-calendar-ics"  # the secret address: a bearer secret, held only by the service
SYNTHETIC = "X-UI-REMOVED-OCCURRENCE"  # marks a stored copy of one occurrence Google removed from a series
FAILED = 3  # failed fetches in a row before the Calendar says the Event provider is broken
JOIN = re.compile(r"https://(?:meet\.google\.com|[\w.-]*zoom\.us|teams\.microsoft\.com)/[^\s<>\"\\]+")


def fetch(url: str, timeout: float = 20) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "universal-inbox"})
    with urllib.request.urlopen(request, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _local(value) -> datetime:
    """A date or datetime as an aware local datetime (naive and floating values are local time)."""
    return (value if isinstance(value, datetime) else datetime.combine(value, time())).astimezone()


def _rid(value) -> str:
    """One occurrence's key: its start in UTC, or its date for an all-day Event."""
    if not isinstance(value, datetime):
        return value.strftime("%Y%m%d")
    return (value if value.tzinfo else value.astimezone()).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _exdates(ev) -> dict[str, object]:
    found = ev.get("EXDATE")
    found = found if isinstance(found, list) else [found] if found is not None else []
    return {_rid(d.dt): d.dt for group in found for d in group.dts}


def _removed_occurrence(master_ics: str, when) -> str:
    """A Deleted Event for one occurrence Google dropped from a series (an EXDATE): the series as it was, at
    that occurrence's time."""
    e = icalendar.Event.from_ical(master_ics)
    start = e["DTSTART"].dt
    end = e["DTEND"].dt if "DTEND" in e else start + (e["DURATION"].dt if "DURATION" in e else
                                                      timedelta(days=1) if not isinstance(start, datetime) else timedelta())
    for key in ("RRULE", "RDATE", "EXDATE", "DTSTART", "DTEND", "DURATION"):
        e.pop(key, None)
    e.add("DTSTART", when)
    e.add("DTEND", when + (end - start))
    e.add("RECURRENCE-ID", when)
    e.add(SYNTHETIC, "1")
    return e.to_ical().decode()


def sync(db, feed: str, now: str | None = None) -> bool:
    """Store the feed: new and changed Events written, Events gone from it marked deleted, nothing written when
    nothing changed (each write sends the laptop console a new copy). True when something changed."""
    now = now or _now()
    cal = icalendar.Calendar.from_ical(feed)
    new = {}
    for ev in cal.walk("VEVENT"):
        ev.pop("DTSTAMP", None)  # Google stamps every Event with the fetch time
        rid = ev.get("RECURRENCE-ID")
        new[(str(ev["UID"]), _rid(rid.dt) if rid is not None else "")] = ev
    rows = {(r["uid"], r["recurrence_id"]): r for r in db.execute("SELECT * FROM events")}
    owner = str(cal.get("X-WR-CALNAME", ""))
    writes = []
    for key, ev in new.items():
        ics, old = ev.to_ical().decode(), rows.get(key)
        if old is None:
            writes.append(("INSERT INTO events (uid, recurrence_id, ics, first_seen_at, changed_at) VALUES (?, ?, ?, ?, ?)",
                           (*key, ics, now, now)))
        elif old["ics"] != ics or old["deleted_at"]:
            writes.append(("UPDATE events SET ics = ?, changed_at = ?, deleted_at = NULL WHERE uid = ? AND recurrence_id = ?",
                           (ics, now, *key)))
        if key[1] == "" and old is not None and old["ics"] != ics:
            before, after = _exdates(icalendar.Event.from_ical(old["ics"])), _exdates(ev)
            for rid, when in before.items() - after.items():  # an occurrence is back: its stored copy goes
                if rid not in after and SYNTHETIC in (rows.get((key[0], rid)) or {"ics": ""})["ics"]:
                    writes.append(("DELETE FROM events WHERE uid = ? AND recurrence_id = ?", (key[0], rid)))
            for rid, when in after.items():
                if rid not in before and (key[0], rid) not in rows and (key[0], rid) not in new:
                    writes.append(("INSERT INTO events (uid, recurrence_id, ics, first_seen_at, changed_at, deleted_at)"
                                   " VALUES (?, ?, ?, ?, ?, ?)", (key[0], rid, _removed_occurrence(old["ics"], when), now, now, now)))
    for key, r in rows.items():
        if key not in new and not r["deleted_at"]:
            writes.append(("UPDATE events SET deleted_at = ? WHERE uid = ? AND recurrence_id = ?", (now, *key)))
    current = db.execute("SELECT owner FROM event_provider WHERE id = 1").fetchone()
    if current is None or current["owner"] != owner:
        writes.append(("INSERT INTO event_provider (id, owner) VALUES (1, ?) ON CONFLICT (id) DO UPDATE SET owner = excluded.owner",
                       (owner,)))
    if writes:
        with db:
            for sql, args in writes:
                db.execute(sql, args)
    return bool(writes)


def provider(db) -> dict | None:
    """The Event provider's state: owner, failures in a row, the last error, and when a fetch last worked."""
    r = db.execute("SELECT * FROM event_provider WHERE id = 1").fetchone()
    return dict(r) if r else None


def record_ok(db) -> None:
    """A good fetch: clears a failure run (written only then, so a healthy minute writes nothing)."""
    if (p := provider(db)) and p["failures"]:
        with db:
            db.execute("UPDATE event_provider SET failures = 0, error = NULL, ok_at = NULL WHERE id = 1")


def record_failure(db, error: str, ok_at: str | None) -> None:
    with db:
        db.execute("INSERT INTO event_provider (id, owner, failures, error, ok_at) VALUES (1, '', 1, ?, ?)"
                   " ON CONFLICT (id) DO UPDATE SET failures = failures + 1, error = excluded.error,"
                   " ok_at = coalesce(excluded.ok_at, ok_at)", (error, ok_at))


def broken(db) -> str | None:
    """Why the Event provider counts as broken, or None."""
    p = provider(db)
    return p["error"] or "unreachable" if p and p["failures"] >= FAILED else None


_cache: dict = {}


def _calendars(db):
    """The stored Events as two calendars (live, deleted) and the UIDs that repeat, parsed once per change."""
    key = (id(db), db.execute("PRAGMA database_list").fetchone()[2],
           *db.execute("SELECT count(*), max(changed_at), max(deleted_at), sum(length(ics)) FROM events").fetchone())
    if _cache.get("key") != key:
        live, gone, series = icalendar.Calendar(), icalendar.Calendar(), set()
        for r in db.execute("SELECT uid, recurrence_id, ics, deleted_at FROM events"):
            ev = icalendar.Event.from_ical(r["ics"])
            (gone if r["deleted_at"] else live).add_component(ev)
            if r["recurrence_id"] == "" and "RRULE" in ev:
                series.add(r["uid"])
        deleted = {(r["uid"], r["recurrence_id"]): r["deleted_at"]
                   for r in db.execute("SELECT uid, recurrence_id, deleted_at FROM events WHERE deleted_at IS NOT NULL")}
        _cache.update(key=key, value=(live, gone, series, deleted))
    return _cache["value"]


def me(db) -> set[str]:
    """Jeremy's addresses: the calendar's owner and every Connection's own email address."""
    p = provider(db)
    mine = {c["self_id"].lower() for c in db.execute("SELECT self_id FROM connections") if "@" in (c["self_id"] or "")}
    return mine | ({p["owner"].lower()} if p and "@" in p["owner"] else set())


def _address(prop) -> tuple[str, str]:
    email = str(prop).split(":", 1)[-1].strip().lower()
    return email, str(prop.params.get("CN", "")) if hasattr(prop, "params") else ""


def _occurrence(o, mine: set[str], series: set[str], deleted: dict) -> dict:
    start = o["DTSTART"].dt
    all_day = not isinstance(start, datetime)
    end = o["DTEND"].dt if "DTEND" in o else start + (o["DURATION"].dt if "DURATION" in o else
                                                      timedelta(days=1) if all_day else timedelta())
    found = o.get("ATTENDEE")
    found = found if isinstance(found, list) else [found] if found is not None else []
    attendees = [{"email": e, "name": n, "state": str(a.params.get("PARTSTAT", "NEEDS-ACTION"))}
                 for a in found for e, n in [_address(a)]]
    organizer = dict(zip(("email", "name"), _address(o["ORGANIZER"]))) if "ORGANIZER" in o else None
    mine_row = next((a for a in attendees if a["email"] in mine), None)
    state = mine_row["state"] if mine_row else "ACCEPTED"  # his own Event, or one he holds without a guest list
    others = {a["email"] for a in attendees} | ({organizer["email"]} if organizer else set())
    kind = "all-day" if all_day else "meeting" if others - mine else "block"
    text = f"{o.get('LOCATION', '')}\n{o.get('DESCRIPTION', '')}"
    join = str(o.get("X-GOOGLE-CONFERENCE", "")) or next(iter(JOIN.findall(text)), "")
    uid = str(o["UID"])
    rid = _rid(o["RECURRENCE-ID"].dt) if "RECURRENCE-ID" in o else ""
    deleted_at = deleted.get((uid, rid)) or deleted.get((uid, ""))
    return {"id": f"{uid}|{rid}", "uid": uid, "kind": kind, "start": start, "end": end, "all_day": all_day,
            "title": str(o.get("SUMMARY", "")) or "(no title)", "location": str(o.get("LOCATION", "")),
            "description": str(o.get("DESCRIPTION", "")), "organizer": organizer, "attendees": attendees,
            "state": state, "join_url": join, "repeats": uid in series, "deleted_at": deleted_at}


def expand(db, start: date, end: date, deleted: bool = True) -> list[dict]:
    """Every occurrence that overlaps the local days [start, end), in start order; Deleted Events too unless
    `deleted` is False."""
    live, gone, series, marks = _calendars(db)
    mine = me(db)
    found = [(o, {}) for o in recurring_ical_events.of(live).between(start, end)]
    if deleted:
        found += [(o, marks) for o in recurring_ical_events.of(gone).between(start, end)]
    out = [_occurrence(o, mine, series, m) for o, m in found]
    if not deleted:
        out = [e for e in out if not e["deleted_at"]]
    return sorted(out, key=lambda e: (not e["all_day"], _local(e["start"])))


def now_and_next(db, now: datetime | None = None) -> tuple[dict | None, dict | None]:
    """The timed Event in progress and the next one to start, on any day within a year; Declined and Deleted
    Events do not count."""
    now = now or datetime.now().astimezone()
    timed = [e for e in expand(db, now.date() - timedelta(days=1), now.date() + timedelta(days=366), deleted=False)
             if not e["all_day"] and e["state"] != "DECLINED"]
    current = [e for e in timed if _local(e["start"]) <= now < _local(e["end"])]
    later = [e for e in timed if _local(e["start"]) > now]
    return (max(current, key=lambda e: _local(e["start"])) if current else None,
            min(later, key=lambda e: _local(e["start"])) if later else None)


def _when(value: str | None) -> datetime | None:
    try:
        return _local(datetime.fromisoformat(value)) if value else None
    except ValueError:
        return None


def items(db, start: date, end: date) -> list[dict]:
    """Calendar Items on the local days [start, end): Events, Tasks on their day, Snoozed Threads at their wake
    time, Drafts at their send time, and Enrollments on their start day. Each has kind, at (local), all_day,
    title, and the id it opens."""
    lo, hi = _local(start), _local(end)
    out = [{**e, "kind": "event", "type": e["kind"], "at": _local(e["start"])} for e in expand(db, start, end)]

    def add(kind, at, title, ref, **extra):
        when = _when(at)
        if when is not None and lo <= when < hi:
            out.append({"kind": kind, "at": when, "all_day": when.time() == time(), "title": title, "ref": ref, **extra})
    # ponytail: scans every row of these small tables; filter in SQL once they grow
    for t in db.execute("SELECT t.id, t.due_at, s.kind, p.display_name, q.name FROM tasks t"
                        " JOIN sequence_steps s ON s.id = t.step_id JOIN enrollments e ON e.id = t.enrollment_id"
                        " JOIN sequences q ON q.id = e.sequence_id JOIN persons p ON p.id = e.person_id"
                        " WHERE t.state = 'DUE'"):
        add("task", t["due_at"], f"{t['kind']} · {t['display_name']} · {t['name']}", t["id"])
    for t in db.execute("SELECT id, title, coalesce(snoozed_until, follow_up_at) AS at FROM threads"
                        " WHERE status = 'SNOOZED' AND merged_into IS NULL"):
        add("snooze", t["at"], t["title"], t["id"])
    for d in db.execute('SELECT id, subject, "to", send_at FROM composition_drafts WHERE send_at IS NOT NULL'):
        add("draft", d["send_at"], d["subject"] or f"message to {d['to']}", d["id"])
    for e in db.execute("SELECT e.id, e.starts_at, p.id AS person_id, p.display_name, q.name FROM enrollments e"
                        " JOIN persons p ON p.id = e.person_id JOIN sequences q ON q.id = e.sequence_id"
                        " WHERE e.starts_at IS NOT NULL AND e.state != 'FINISHED'"):
        add("enrollment", e["starts_at"], f"{e['display_name']} starts {e['name']}", e["person_id"])
    return sorted(out, key=lambda i: (not i["all_day"], i["at"]))
