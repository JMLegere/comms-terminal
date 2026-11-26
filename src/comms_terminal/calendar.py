"""Jeremy's work calendar (ADR 0005). The service reads the Event provider, Google Calendar's secret iCal address,
every 30 seconds; `sync` stores each Event as Google gives it and keeps every Event ever seen: one that leaves the
feed becomes a Deleted Event. `expand` turns stored Events into occurrences for any range, so repeating Events
have no horizon; `items` adds the inbox's own dated things (Calendar Items) for the Calendar page."""

import hashlib
import html
import json
import re
import urllib.request
from datetime import date, datetime, time, timedelta, timezone

import icalendar
import recurring_ical_events

from . import clock

CREDENTIAL = "google-calendar-ics"  # the secret address: a bearer secret, held only by the service
SYNTHETIC = "X-UI-REMOVED-OCCURRENCE"  # marks a stored copy of one occurrence Google removed from a series
FAILED = 3  # failed fetches in a row before the Calendar says the Event provider is broken
JOIN = re.compile(r"https://(?:meet\.google\.com|[\w.-]*zoom\.us|teams\.microsoft\.com)/[^\s<>\"\\]+")
# Omit links even when an Event puts one in its title, not just in dedicated URL/description fields.
EVIDENCE_URL = re.compile(
    r"<?(?:[a-z][a-z0-9+.-]*://|https?%3a(?:/|%2f){2}|https?:(?:%2f){2}|(?<!\w)//|www\.|"
    r"\b(?:mailto|data|tel|sip|cid):|\b(?:[a-z0-9-]+\.)+[a-z0-9-]+\.?(?::[0-9]+)?(?=[/?#]))\S+",
    re.IGNORECASE,
)
_EVIDENCE_REF = re.compile(
    r"[a-f0-9]{64}@(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2}))@[a-f0-9]{64}"
)
_EVIDENCE_EMAIL = re.compile(r"[\w.!#$%&'*+=^`{|}~-]+@[\w.-]+")
_INVITE_STATES = {"ACCEPTED", "DECLINED", "DELEGATED", "NEEDS-ACTION", "TENTATIVE"}


def fetch(url: str, timeout: float = 20) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "comms-terminal"})
    with urllib.request.urlopen(request, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")




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
    now = now or clock.now().isoformat(timespec="seconds")
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
        _cache.pop("key", None)  # two real changes in the same second can otherwise keep the same cache key
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
            if r["recurrence_id"] == "" and ("RRULE" in ev or "RDATE" in ev):
                series.add(r["uid"])
        deleted = {(r["uid"], r["recurrence_id"]): r["deleted_at"]
                   for r in db.execute("SELECT uid, recurrence_id, deleted_at FROM events WHERE deleted_at IS NOT NULL")}
        _cache.update(key=key, value=(live, gone, series, deleted), expanded={}, days=None)
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
            "state": state, "join_url": join, "repeats": uid in series, "deleted_at": deleted_at,
            "busy": state != "DECLINED" and str(o.get("TRANSP", "OPAQUE")).upper() != "TRANSPARENT"}  # FR-IS-6


def expand(db, start: date, end: date, deleted: bool = True) -> list[dict]:
    """Every occurrence that overlaps the local days [start, end), in start order; Deleted Events too unless
    `deleted` is False. Kept until the stored Events change: Today and the status bar ask for the same year on
    every refresh, and expanding it is most of their cost."""
    live, gone, series, marks = _calendars(db)
    mine = me(db)
    kept = _cache["expanded"]
    key = (start, end, deleted, frozenset(mine))
    if key not in kept:
        if len(kept) >= 8:  # ponytail: drop all at 8 ranges; an LRU if paging the Calendar ever thrashes it
            kept.clear()
        found = [(o, {}) for o in recurring_ical_events.of(live).between(start, end)]
        if deleted:
            found += [(o, marks) for o in recurring_ical_events.of(gone).between(start, end)]
        out = [_occurrence(o, mine, series, m) for o, m in found]
        if not deleted:
            out = [e for e in out if not e["deleted_at"]]
        kept[key] = sorted(out, key=lambda e: (not e["all_day"], clock.local(e["start"])))
    return list(kept[key])  # callers read the occurrences and never change them


def _promise_occurrence(db, row):
    """Only a standalone Event or an explicit override has its own stored occurrence revision."""
    ev = icalendar.Event.from_ical(row["ics"])
    uid, rid = row["uid"], row["recurrence_id"]
    recurrence = ev.get("RECURRENCE-ID")
    if ("DTSTART" not in ev or "RRULE" in ev or "RDATE" in ev or
            (recurrence and recurrence.params.get("RANGE")) or str(ev.get("STATUS", "")).upper() == "CANCELLED"):
        return None
    if not rid and db.execute(
            "SELECT 1 FROM events WHERE uid = ? AND recurrence_id != '' AND deleted_at IS NULL", (uid,)).fetchone():
        return None  # not a uniquely grounded standalone occurrence
    return _occurrence(ev, me(db), set(), {})


def _promise_ref(row):
    # The stable key follows a move even when the occurrence display ID changes. The second digest binds the
    # actual stored revision (changed_at alone is second-precision); neither digest exposes UID, URLs or content.
    key = hashlib.sha256(json.dumps([row["uid"], row["recurrence_id"]]).encode()).hexdigest()
    revision = hashlib.sha256(row["ics"].encode()).hexdigest()
    return f"{key}@{row['changed_at']}@{revision}"


_EVIDENCE_FIELDS = ("title", "start", "end", "attendees", "organizer", "state")


def redact_evidence(text: str) -> str:
    return EVIDENCE_URL.sub("[URL omitted]", html.unescape(text))


def promise_origin(db, event):
    """Return an agent CLI Origin for the exact stored Event behind this displayed occurrence, if grounded."""
    if event["deleted_at"] or broken(db):
        return {}
    uid = event["uid"]
    rid = event["id"][len(uid) + 1:]
    refs = []
    for row in db.execute("SELECT * FROM events WHERE uid = ? AND recurrence_id IN ('', ?) AND deleted_at IS NULL",
                          (uid, rid)):
        if not (stored := _promise_occurrence(db, row)):
            continue
        if event["id"] != stored["id"] and (row["recurrence_id"] or rid != _rid(stored["start"])):
            continue
        if all(event[k] == stored[k] for k in _EVIDENCE_FIELDS) and _EVIDENCE_REF.fullmatch(ref := _promise_ref(row)):
            refs.append({"promise_origin": f"calendar:{ref}", "changed_at": row["changed_at"],
                         "first_seen_at": row["first_seen_at"]})
    return refs[0] if len(refs) == 1 else {}


def promise_evidence(db, ref):
    """Re-check the cited revision against the current local feed and return only the facts History may keep."""
    if broken(db) or not (parsed := _EVIDENCE_REF.fullmatch(ref)):
        return None
    matches = [r for r in db.execute("SELECT * FROM events WHERE changed_at = ? AND deleted_at IS NULL", (parsed[1],))
               if _promise_ref(r) == ref]
    if len(matches) != 1 or not (event := _promise_occurrence(db, matches[0])):
        return None
    if matches[0]["recurrence_id"]:
        shown = current(db, event)
        if not shown or shown["deleted_at"] or any(shown[k] != event[k] for k in _EVIDENCE_FIELDS):
            return None
    return {"changed_at": parsed[1], "first_seen_at": matches[0]["first_seen_at"], **_evidence_facts(event)}


def _evidence_facts(event):
    """Only the fields needed to compare an Event with a Promise; no private access links or provider payloads."""
    organizer = (event["organizer"] or {}).get("email", "")
    return {"title": " ".join(redact_evidence(event["title"]).split())[:512],
            "start": event["start"].isoformat(), "end": event["end"].isoformat(),
            "organizer": organizer if _EVIDENCE_EMAIL.fullmatch(organizer) else None,
            "attendees": [{"email": a["email"], "state": a["state"]}
                          for a in event["attendees"] if _EVIDENCE_EMAIL.fullmatch(a["email"])
                          and a["state"] in _INVITE_STATES]}


def evidence_event(db, event):
    """The capture projection; its occurrence ID does not expose the provider's UID."""
    return {"id": hashlib.sha256(event["id"].encode()).hexdigest(),
            "kind": event["kind"], "deleted_at": event["deleted_at"],
            "state": event["state"] if event["state"] in _INVITE_STATES else None,
            **_evidence_facts(event), **promise_origin(db, event)}


def on_day(db, day: date, today: date) -> list[dict]:
    """The live occurrences that may touch the local `day`: All-day ones exactly, timed ones within a day either
    side (callers clip them to the day). Issue capacity asks day by day, up to one call per planned day on every
    refresh, and each one-day `expand` walks every series again (about 100 ms on a real calendar). So days in the
    year ahead come from the same cached year as `now_and_next`, sorted into days once per change."""
    first, last = today - timedelta(days=1), today + timedelta(days=366)
    if not first <= day < last:
        return expand(db, day, day + timedelta(days=1), deleted=False)
    _calendars(db)  # clears the kept days when the stored Events change
    window = (first, last, frozenset(me(db)))
    kept = _cache.get("days")
    if kept is None or kept[0] != window:
        kept = (window, {})
        one = timedelta(days=1)
        for e in expand(db, first, last, deleted=False):
            if e["all_day"]:
                d, end = e["start"], max(e["end"], e["start"] + one)
            else:
                d, end = clock.local(e["start"]).date() - one, clock.local(e["end"]).date() + 2 * one
            while d < end:
                kept[1].setdefault(d, []).append(e)
                d += one
        _cache["days"] = kept
    return kept[1].get(day, [])


def current(db, event: dict) -> dict | None:
    """Reload an open Event: standalone by UID, recurring by its stable RECURRENCE-ID, across day moves.
    A missing or ambiguous occurrence returns None rather than another occurrence of the same series."""
    uid = event["uid"]
    rid = event["id"][len(uid) + 1:]
    rows = {r["recurrence_id"]: r for r in db.execute(
        "SELECT recurrence_id, ics, deleted_at FROM events WHERE uid = ?", (uid,))}
    candidates = [rows[key] for key in (rid, "") if key in rows]
    for row in sorted(candidates, key=lambda row: bool(row["deleted_at"])):  # live overrides/master before tombstones
        stored = icalendar.Event.from_ical(row["ics"])
        if row["recurrence_id"] == "" and ("RRULE" in stored or "RDATE" in stored):
            if not rid:
                return None
            original = datetime.strptime(rid, "%Y%m%d" if len(rid) == 8 else "%Y%m%dT%H%M%SZ")
            day = original.date() if len(rid) == 8 else clock.local(original.replace(tzinfo=timezone.utc)).date()
            target = event["id"]
        elif row["recurrence_id"] == "" and event["repeats"]:
            continue  # the series occurrence no longer resolves to that recurrence
        else:
            day = clock.local(stored["DTSTART"].dt).date()
            target = f"{uid}|{row['recurrence_id'] or _rid(stored['DTSTART'].dt)}"
        matches = [e for e in expand(db, day, day + timedelta(days=1)) if e["id"] == target
                   and bool(e["deleted_at"]) == bool(row["deleted_at"])]
        if matches:
            return matches[0] if len(matches) == 1 else None
    return None


def now_and_next(db, now: datetime | None = None) -> tuple[dict | None, dict | None]:
    """The timed Event in progress and the next one to start, on any day within a year; Declined and Deleted
    Events do not count."""
    now = now or clock.local()
    timed = [e for e in expand(db, now.date() - timedelta(days=1), now.date() + timedelta(days=366), deleted=False)
             if not e["all_day"] and e["state"] != "DECLINED"]
    current = [e for e in timed if clock.local(e["start"]) <= now < clock.local(e["end"])]
    later = [e for e in timed if clock.local(e["start"]) > now]
    return (max(current, key=lambda e: clock.local(e["start"])) if current else None,
            min(later, key=lambda e: clock.local(e["start"])) if later else None)


def _when(value: str | None) -> datetime | None:
    try:
        return clock.local(datetime.fromisoformat(value)) if value else None
    except ValueError:
        return None


def items(db, start: date, end: date) -> list[dict]:
    """Calendar Items on the local days [start, end): Events, Tasks on their day, Snoozed Threads at their wake
    time, Drafts at their send time, and Enrollments on their start day. Each has kind, at (local), all_day,
    title, and the id it opens."""
    lo, hi = clock.local(start), clock.local(end)
    out = [{**e, "kind": "event", "type": e["kind"], "at": clock.local(e["start"])} for e in expand(db, start, end)]

    def add(kind, at, title, ref, **extra):
        when = _when(at)
        if when is not None and lo <= when < hi:
            out.append({"kind": kind, "at": when, "all_day": when.time() == time(), "title": title, "ref": ref, **extra})
    from . import inbox
    seq = "sequences" in inbox.modules_on(db)  # Sequences off: no Task or Enrollment items (ADR 0017)
    # ponytail: scans every row of these small tables; filter in SQL once they grow
    for t in db.execute("SELECT t.id, t.due_at, s.kind, p.display_name, q.name FROM tasks t"
                        " JOIN sequence_steps s ON s.id = t.step_id JOIN enrollments e ON e.id = t.enrollment_id"
                        " JOIN sequences q ON q.id = e.sequence_id JOIN persons p ON p.id = e.person_id"
                        " WHERE t.state = 'DUE' AND ?", (seq,)):
        add("task", t["due_at"], f"{t['kind']} · {t['display_name']} · {t['name']}", t["id"])
    for t in db.execute("SELECT id, title, coalesce(snoozed_until, follow_up_at) AS at FROM threads"
                        " WHERE status = 'SNOOZED' AND merged_into IS NULL"):
        add("snooze", t["at"], t["title"], t["id"])
    for d in db.execute('SELECT id, subject, "to", send_at, task_id FROM composition_drafts WHERE send_at IS NOT NULL'):
        # an email Task's scheduled send opens its Task, which lists it (decision #146)
        add("draft", d["send_at"], d["subject"] or f"message to {d['to']}", d["id"],
            **({"task": d["task_id"]} if d["task_id"] and seq else {}))
    for e in db.execute("SELECT e.id, e.starts_at, p.id AS person_id, p.display_name, q.name FROM enrollments e"
                        " JOIN persons p ON p.id = e.person_id JOIN sequences q ON q.id = e.sequence_id"
                        " WHERE e.starts_at IS NOT NULL AND e.state != 'FINISHED' AND ?", (seq,)):
        add("enrollment", e["starts_at"], f"{e['display_name']} starts {e['name']}", e["person_id"])
    issues_on = "issues" in inbox.modules_on(db)  # Issues off: no planned Issues on the Calendar (ADR 0017)
    for i in db.execute("SELECT id, identifier, title, planned_day FROM issues WHERE planned_day >= ? AND planned_day < ?"
                        " AND completed_at IS NULL AND canceled_at IS NULL AND ?",
                        (start.isoformat(), end.isoformat(), issues_on)):
        add("issue", clock.local(date.fromisoformat(i["planned_day"])).isoformat(),  # on its Planned day (FR-UI-3)
            f"{i['identifier']} · {i['title']}", i["id"])
    return sorted(out, key=lambda i: (not i["all_day"], i["at"]))
