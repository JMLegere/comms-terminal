"""Prep (ADR 0025; FR-PR-16, FR-PR-17): what Jeremy needs ready before a Meeting, owned in Tana and read back here.

The capture agent writes Prep on the Meeting's Tana calendar event, as the event type "Prep" with the fields
Brief, Prep minutes, Travel minutes and Prepped. The Service reads the next 7 days of Meetings from Tana every
5 minutes (`refresh`) into the `preps` table, so Prep moves and disappears with its Meeting. A Meeting is one with
someone outside Agent Maple, or an in-person event (a location and nobody else invited).

Fail safe: a Meeting without Prep, or any Prep while the last read failed, counts as not prepared. At risk works
as for a Promise (promises.risk): the Meeting's start is its time, Prep minutes its work (none once Prepped), and
Travel minutes its travel.

Interface: refresh (the Service's loop), rows (readers: Today, the Promises page, the At-risk SMS).
"""

import re
from datetime import datetime, timedelta

from . import clock, inbox, promises

TYPE = "Prep"
FIELDS = {"brief": "Brief", "prep_minutes": "Prep minutes", "travel_minutes": "Travel minutes", "prepped": "Prepped"}
DAYS = 7
EVERY = 300  # seconds between reads
TIME = re.compile(r"(\d{4}-\d\d-\d\dT\d\d:\d\d[+-]\d\d:\d\d)->(\d\d:\d\d[+-]\d\d:\d\d|\d{4}-\d\d-\d\dT\S+)")
DOMAIN = re.compile(r"@([a-z0-9.-]+\.[a-z]{2,})", re.I)


def _own(db) -> set[str]:
    return {inbox._org(m.rsplit("@", 1)[1].lower()) for (m,) in db.execute("SELECT self_id FROM connections")
            if m and "@" in m}


def _times(text: str) -> tuple[datetime, datetime] | None:
    m = TIME.search(text or "")
    if not m:
        return None  # an all-day event has no clock time: not a Meeting to prepare for
    start = datetime.fromisoformat(m.group(1))
    end = m.group(2)
    end = datetime.fromisoformat(end if "T" in end else f"{m.group(1)[:11]}{end}")
    return start, end


def is_meeting(event: dict, own: set[str]) -> bool:
    """Someone outside Agent Maple is invited, or it is in person with nobody else invited."""
    domains = {inbox._org(d.lower()) for d in DOMAIN.findall(event.get("people") or "")}
    return bool(domains - own) or (domains <= own and "attendees(" not in (event.get("people") or "")
                                   and bool(event.get("location")))


def _fields(event: dict) -> dict:
    """The Prep fields of a read event, by our key; absent when the event is not of type Prep."""
    if ((event.get("meetingType") or {}).get("name") or "") != TYPE:
        return {}
    raw = event.get("fields") or {}
    if isinstance(raw, list):
        raw = {f.get("name"): f.get("value") for f in raw if isinstance(f, dict)}
    by_name = {v: k for k, v in FIELDS.items()}
    return {by_name[n]: v for n, v in raw.items() if n in by_name}


def _minutes(value) -> int | None:
    try:
        return max(0, int(float(value[0] if isinstance(value, list) else value)))
    except (TypeError, ValueError, IndexError):
        return None


def _yes(value) -> bool:
    value = value[0] if isinstance(value, list) and value else value
    return value is True or str(value).strip().lower() in ("true", "yes", "1", "checked")


def read(client, own: set[str], now: datetime) -> list[dict]:
    """The Meetings starting in the next DAYS days, with their Prep, as `preps` rows."""
    local = now.astimezone(promises._local())  # Tana wants an IANA name, not an offset
    events = client.tool("listEvents", startDate=local.date().isoformat(),
                         endDate=(local.date() + timedelta(days=DAYS)).isoformat(), maxResults=100,
                         timezone=str(promises._local())).get("events", [])
    out = []
    for e in events:
        when = _times(e.get("time", ""))
        if e.get("attendance") or when is None or when[0] <= now or not is_meeting(e, own):
            continue
        f = _fields(client.tool("readEvent", eventUri=e["eventUri"]))
        brief = f.get("brief")
        out.append({"event_uri": e["eventUri"], "title": e.get("title") or "Meeting",
                    "start_at": promises._iso(when[0]), "end_at": promises._iso(when[1]),
                    "location": e.get("location"), "has_prep": int(bool(f)),
                    "brief": (brief[0] if isinstance(brief, list) and brief else brief) or None,
                    "prep_minutes": _minutes(f.get("prep_minutes")),
                    "travel_minutes": _minutes(f.get("travel_minutes")), "prepped": int(_yes(f.get("prepped")))})
    return out


COLUMNS = ("event_uri", "title", "start_at", "end_at", "location", "has_prep", "brief", "prep_minutes",
           "travel_minutes", "prepped")


def refresh(db, client, now: datetime | None = None) -> bool:
    """One read from Tana into `preps`; rows are written only when something changed. A failed read is kept as
    the error (every Prep then counts as not prepared) and re-raised for the log. True when rows changed."""
    now = now or clock.now()
    try:
        new = read(client, _own(db), now)
    except Exception as e:
        _status(db, now, (str(e) or type(e).__name__)[:300])
        raise
    old = [dict(r) for r in db.execute(f"SELECT {', '.join(COLUMNS)} FROM preps ORDER BY event_uri")]
    new = sorted(new, key=lambda r: r["event_uri"])
    changed = old != new
    if changed:
        with db:
            db.execute("DELETE FROM preps")
            db.executemany(f"INSERT INTO preps ({', '.join(COLUMNS)}) VALUES ({', '.join('?' * len(COLUMNS))})",
                           [tuple(r[c] for c in COLUMNS) for r in new])
    _status(db, now, None)
    return changed


def _status(db, now: datetime, error: str | None) -> None:
    """The last read's outcome; written only when it changes from ok to failing or back."""
    row = db.execute("SELECT error FROM prep_read WHERE id = 1").fetchone()
    if row is not None and row[0] == error:
        return
    with db:
        db.execute("INSERT INTO prep_read (id, at, error) VALUES (1, ?, ?) ON CONFLICT (id) DO UPDATE SET"
                   " at = excluded.at, error = excluded.error", (promises._iso(now), error))


def read_error(db) -> str | None:
    row = db.execute("SELECT error FROM prep_read WHERE id = 1").fetchone()
    return row[0] if row else None


def rows(db, now: datetime | None = None) -> list[dict]:
    """The coming Meetings with their Prep, soonest first, each with `prepared`, `risk` and `section` as a
    Promise has them. Not prepared: no Prep, not marked Prepped, or the last read failed."""
    now = now or clock.now()
    failed = read_error(db)
    out = []
    for r in db.execute("SELECT * FROM preps WHERE start_at > ? ORDER BY start_at", (promises._iso(now),)):
        r = dict(r)
        prepared = bool(r["has_prep"] and r["prepped"] and not failed)
        work = 0 if prepared else (r["prep_minutes"] or 0)
        row = {"due_at": r["start_at"], "work_minutes": work, "travel_minutes": r["travel_minutes"] or 0,
               "state": "OPEN"}
        out.append(r | {"prepared": prepared, "unread": bool(failed), "slack_time": promises.slack_time(row, now),
                        "risk": promises.risk(row, now), "section": promises.section(row, now),
                        "leave_at": promises._iso(datetime.fromisoformat(r["start_at"])
                                                  - timedelta(minutes=r["travel_minutes"] or 0))})
    return out
