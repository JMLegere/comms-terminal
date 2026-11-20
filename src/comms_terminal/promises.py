"""Promises (ADR 0024, 0027; .agents/prd/2026-11-18-promises.md): what specific People expect Jeremy to do by a
time. Only an agent changes them, through the CLI, and every change cites an Origin (FR-PR-3, FR-PR-4): an
Activity, a Customer request, a Tana node, or Jeremy's own recorded words. Slack-time, Urgent, At risk and Overdue are worked
out from the time, the work and the travel (FR-PR-7), never stored.

A Promise is Tentative, Open, Fulfilled or Cancelled. Cancelled needs an Origin showing the Person knows, or, for a
Tentative promise, evidence it was never owed (FR-PR-5; GLOSSARY). Linking an Enrollment lets the Promise's time set
that Enrollment's start (FR-PR-14).

The "captured up to" mark (FR-PR-10) is the last Activity whose Promise changes the capture agent has written; it
only moves forward, in Activity insertion order, so a late Call transcript (an EDIT Activity) is still read.

When an Open Promise first goes At risk, and again when it first goes Urgent or Overdue, the Service sends Jeremy one SMS (FR-PR-18, ADR 0026), and only
once Jeremy's own mobile is set.

Interface: record, firm, change, fulfil, cancel, link, advance_capture, set_alert_mobile (writers); find, promise,
view, kept, slack_time, capture_mark, pending, alert_mobile (readers); alert (the Service's wake pass).
"""

import json
import logging
import re
from datetime import date, datetime, time, timedelta, timezone

from . import clock, commands
from .writers import writer

STATES = ("TENTATIVE", "OPEN", "FULFILLED", "CANCELLED")
OPEN_STATES = ("TENTATIVE", "OPEN")
ORIGINS = ("activity", "customer_request", "tana", "words")
LINKS = ("thread", "case", "issue", "draft", "enrollment", "tana", "url")
DUE_TIME = time(17)  # a Promise given only a day is due at 17:00 Jeremy's time
NO_UNDO = "Promises change only by an agent with an Origin (ADR 0024); record a new change instead"
SECTIONS = ("overdue", "urgent", "at_risk", "today", "this_week", "later", "tentative")


class Refused(ValueError):
    """A Promise change the rules do not allow; the message says why."""


def _local():
    from .sequences import LOCAL
    return LOCAL


def due_from(text: str) -> datetime:
    """A time as an agent writes it: YYYY-MM-DD (17:00 Jeremy's time), YYYY-MM-DD HH:MM (his time), or ISO with an
    offset."""
    text = text.strip()
    try:
        day = date.fromisoformat(text)
    except ValueError:
        when = datetime.fromisoformat(text.replace(" ", "T"))
        return (when if when.tzinfo else when.replace(tzinfo=_local())).astimezone(timezone.utc)
    return datetime.combine(day, DUE_TIME, _local()).astimezone(timezone.utc)


def _iso(when: datetime) -> str:
    return when.astimezone(timezone.utc).isoformat()


def origin_from(text: str) -> dict:
    """An Origin as an agent writes it: activity:ID, customer_request:ID, tana:ID, or words:TEXT."""
    kind, sep, value = (text or "").partition(":")
    if not sep or kind not in ORIGINS or not value.strip():
        raise Refused("an Origin is activity:ID, customer_request:ID, tana:ID or words:TEXT")
    return {"kind": kind, "words": value.strip()} if kind == "words" else {"kind": kind, "ref": value.strip()}


def _agent_only() -> None:
    """Jeremy reads Promises in the Console and the App; only an agent's CLI or the Service changes them (ADR 0024)."""
    if commands.current_source() in ("console", "app"):
        raise Refused("only an agent changes Promises; tell an agent what to change (ADR 0024)")


def _origin(db, origin: dict | None) -> tuple[str, str | None, str | None]:
    _agent_only()
    if not origin or origin.get("kind") not in ORIGINS:
        raise Refused("every change to a Promise cites its Origin (ADR 0024)")
    kind = origin["kind"]
    if kind == "words":
        words = (origin.get("words") or "").strip()
        if not words:
            raise Refused("an Origin of Jeremy's words needs the words")
        return kind, None, words
    ref = (origin.get("ref") or "").strip()
    if not ref:
        raise Refused(f"a {kind} Origin needs its id")
    if kind == "activity" and not db.execute("SELECT 1 FROM activities WHERE id = ?", (ref,)).fetchone():
        raise Refused(f"no Activity {ref}")
    return kind, ref, None


def _log(db, promise_id: str, change: str, origin: tuple, now: datetime, **detail) -> None:
    kind, ref, words = origin
    db.execute("INSERT INTO promise_changes (promise_id, at, change, detail, origin_kind, origin_ref, words)"
               " VALUES (?, ?, ?, ?, ?, ?, ?)", (promise_id, _iso(now), change, json.dumps(detail, default=str),
                                                 kind, ref, words))


def find(db, promise_id: str):
    row = db.execute("SELECT * FROM promises WHERE id = ?", (promise_id,)).fetchone()
    if row is None:
        raise KeyError(f"no Promise {promise_id}")
    return row


def _set_people(db, promise_id: str, people: list[str]) -> None:
    if not people:
        raise Refused("a Promise is owed to one or more People")
    for person_id in people:
        if not db.execute("SELECT 1 FROM persons WHERE id = ?", (person_id,)).fetchone():
            raise Refused(f"no Person {person_id}")
    db.execute("DELETE FROM promise_people WHERE promise_id = ?", (promise_id,))
    db.executemany("INSERT INTO promise_people (promise_id, person_id) VALUES (?, ?)",
                   [(promise_id, p) for p in dict.fromkeys(people)])


def _starts(db, promise_id: str, due_at: str, now: datetime) -> None:
    """Each linked Enrollment starts on the Promise's day (FR-PR-14)."""
    from . import sequences
    day = datetime.fromisoformat(due_at).astimezone(_local()).date()
    for (eid,) in db.execute("SELECT target FROM promise_links WHERE promise_id = ? AND kind = 'enrollment'",
                             (promise_id,)).fetchall():
        sequences.reschedule(db, eid, day, now=now)


def _label(row, words: str) -> None:
    commands.label(f"Promise {words}: {row['what']}")
    commands.target("promises", row["id"])
    commands.not_undoable(NO_UNDO)


@writer("write")
def record(db, what: str, people: list[str], due_at: datetime, origin: dict, *, inferred: bool = False,
           work_minutes: int = 0, travel_minutes: int = 0, tentative: bool = False,
           now: datetime | None = None) -> str:
    """A new Promise (FR-PR-1); Tentative when the agent is not yet sure it is owed."""
    now = now or clock.now()
    o = _origin(db, origin)
    if not (what or "").strip():
        raise Refused("a Promise says what is owed")
    if work_minutes < 0 or travel_minutes < 0:
        raise Refused("work and travel are minutes, 0 or more")
    pid = db.execute("INSERT INTO promises (what, due_at, due_inferred, work_minutes, travel_minutes, state,"
                     " created_at) VALUES (?, ?, ?, ?, ?, ?, ?) RETURNING id",
                     (what.strip(), _iso(due_at), int(inferred), work_minutes, travel_minutes,
                      "TENTATIVE" if tentative else "OPEN", _iso(now))).fetchone()[0]
    _set_people(db, pid, people)
    _log(db, pid, "recorded", o, now, due_at=_iso(due_at), tentative=tentative)
    _label(find(db, pid), "recorded")
    return pid


def _open(db, promise_id: str):
    row = find(db, promise_id)
    if row["state"] not in OPEN_STATES:
        raise Refused(f"the Promise is already {row['state'].title()}")
    return row


@writer("write")
def firm(db, promise_id: str, origin: dict, *, now: datetime | None = None) -> None:
    """A Tentative promise is owed after all."""
    now = now or clock.now()
    o = _origin(db, origin)
    row = find(db, promise_id)
    if row["state"] != "TENTATIVE":
        raise Refused("only a Tentative promise firms up")
    db.execute("UPDATE promises SET state = 'OPEN' WHERE id = ?", (promise_id,))
    _log(db, promise_id, "firmed", o, now)
    _label(row, "firmed")


@writer("write")
def change(db, promise_id: str, origin: dict, *, what: str | None = None, due_at: datetime | None = None,
           inferred: bool | None = None, work_minutes: int | None = None, travel_minutes: int | None = None,
           people: list[str] | None = None, now: datetime | None = None) -> None:
    """Changes what, the time, work, travel or People of an owed Promise; the history keeps the old values."""
    now = now or clock.now()
    o = _origin(db, origin)
    row = _open(db, promise_id)
    fields, before = {}, {}
    for name, value in (("what", what), ("due_at", _iso(due_at) if due_at else None),
                        ("due_inferred", None if inferred is None else int(inferred)),
                        ("work_minutes", work_minutes), ("travel_minutes", travel_minutes)):
        if value is not None and value != row[name]:
            if name.endswith("_minutes") and value < 0:
                raise Refused("work and travel are minutes, 0 or more")
            fields[name], before[name] = value, row[name]
    if fields:
        db.execute(f"UPDATE promises SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?",
                   (*fields.values(), promise_id))
    if people is not None:
        before["people"] = [r[0] for r in db.execute("SELECT person_id FROM promise_people WHERE promise_id = ?",
                                                     (promise_id,))]
        _set_people(db, promise_id, people)
        fields["people"] = people
    if not fields:
        raise Refused("nothing to change")
    _log(db, promise_id, "changed", o, now, before=before, after=fields)
    if "due_at" in fields:
        _starts(db, promise_id, fields["due_at"], now)
    _label(row, "changed")


@writer("write")
def fulfil(db, promise_id: str, origin: dict, *, at: datetime | None = None, actual_minutes: int | None = None,
           now: datetime | None = None) -> None:
    """The Promise was kept, as its Origin shows; late when after its time. actual_minutes is the work it took,
    when the evidence shows it (FR-PR-21)."""
    now = now or clock.now()
    o = _origin(db, origin)
    row = _open(db, promise_id)
    if actual_minutes is not None and actual_minutes < 0:
        raise Refused("actual is minutes, 0 or more")
    kept = _iso(at or now)
    db.execute("UPDATE promises SET state = 'FULFILLED', closed_at = ?, actual_minutes = ? WHERE id = ?",
               (kept, actual_minutes, promise_id))
    _log(db, promise_id, "fulfilled", o, now, at=kept, late=kept > row["due_at"], actual_minutes=actual_minutes)
    _label(row, "fulfilled")


@writer("write")
def cancel(db, promise_id: str, origin: dict, *, now: datetime | None = None) -> None:
    """No longer owed: the Person knows, or a Tentative promise was never owed. Never silent (FR-PR-5)."""
    now = now or clock.now()
    o = _origin(db, origin)
    row = _open(db, promise_id)
    db.execute("UPDATE promises SET state = 'CANCELLED', closed_at = ? WHERE id = ?", (_iso(now), promise_id))
    _log(db, promise_id, "cancelled", o, now)
    _label(row, "cancelled")


@writer("write")
def link(db, promise_id: str, kind: str, target: str, origin: dict, *, now: datetime | None = None) -> None:
    """Adds context (FR-PR-6). An Enrollment link starts that Enrollment on the Promise's day (FR-PR-14)."""
    now = now or clock.now()
    o = _origin(db, origin)
    row = find(db, promise_id)
    if kind not in LINKS:
        raise Refused(f"a link is one of {', '.join(LINKS)}")
    if not (target or "").strip():
        raise Refused("a link needs its target")
    db.execute("INSERT OR IGNORE INTO promise_links (promise_id, kind, target) VALUES (?, ?, ?)",
               (promise_id, kind, target.strip()))
    _log(db, promise_id, "linked", o, now, kind=kind, target=target.strip())
    if kind == "enrollment" and row["state"] in OPEN_STATES:
        _starts(db, promise_id, row["due_at"], now)
    _label(row, f"linked to {kind}")


def capture_mark(db) -> dict | None:
    """Where capture is: the Activity (`activity_id`, its `occurred_at`) and when the mark moved (`at`)."""
    row = db.execute("SELECT c.*, a.occurred_at FROM promise_capture c LEFT JOIN activities a ON a.id = c.activity_id"
                     " WHERE c.id = 1").fetchone()
    return dict(row) if row else None


@writer("write")
def advance_capture(db, activity_id: str, *, now: datetime | None = None) -> None:
    """Moves "captured up to" to this Activity, once its Promise changes are written; never backwards."""
    now = now or clock.now()
    _agent_only()
    found = db.execute("SELECT rowid FROM activities WHERE id = ?", (activity_id,)).fetchone()
    if found is None:
        raise Refused(f"no Activity {activity_id}")
    mark = capture_mark(db)
    if mark and found[0] <= mark["activity_rowid"]:
        raise Refused("captured up to is already at or past that Activity")
    db.execute("INSERT INTO promise_capture (id, activity_rowid, activity_id, at) VALUES (1, ?, ?, ?)"
               " ON CONFLICT (id) DO UPDATE SET activity_rowid = excluded.activity_rowid,"
               " activity_id = excluded.activity_id, at = excluded.at", (found[0], activity_id, _iso(now)))
    commands.label("Promises captured up to a new Activity")
    commands.not_undoable(NO_UNDO)


def pending(db, limit: int = 50) -> list[dict]:
    """The Activities after "captured up to", oldest first, with who wrote each (None = Jeremy)."""
    mark = capture_mark(db)
    rows = db.execute(
        "SELECT a.rowid AS seq, a.id, a.kind, a.occurred_at, a.thread_id, t.title, a.identity_id, a.target_id,"
        " p.display_name AS author, a.content FROM activities a JOIN threads t ON t.id = a.thread_id"
        " LEFT JOIN identities i ON i.id = a.identity_id LEFT JOIN persons p ON p.id = i.person_id"
        " WHERE a.rowid > ? ORDER BY a.rowid LIMIT ?", (mark["activity_rowid"] if mark else 0, limit)).fetchall()
    return [dict(r) for r in rows]


def slack_time(row, now: datetime) -> int:
    """Minutes left before its time, less its work and travel (FR-PR-7); negative when it can no longer fit."""
    left = (datetime.fromisoformat(row["due_at"]) - now).total_seconds() // 60
    return int(left) - row["work_minutes"] - row["travel_minutes"]


URGENT_TIMES, AT_RISK_TIMES = 2, 12  # time left under 2x / 12x the work and travel (FR-PR-7; Jeremy, 2026-11-20)


def risk(row, now: datetime) -> str | None:
    """'overdue' past its time; 'urgent' when the time left is under twice its work and travel, 'at_risk' under
    twelve times (a 2-hour task: Urgent 4 hours before, At risk 24 hours before) (FR-PR-7); else None."""
    if row["state"] not in OPEN_STATES:
        return None
    if datetime.fromisoformat(row["due_at"]) <= now:
        return "overdue"
    left, need = slack_time(row, now) + row["work_minutes"] + row["travel_minutes"], row["work_minutes"] + row["travel_minutes"]
    return "urgent" if left < URGENT_TIMES * need else "at_risk" if left < AT_RISK_TIMES * need else None


def kept(db, limit: int = 100) -> list[dict]:
    """Fulfilled Promises, newest first, with their estimated work and travel and the work they actually took:
    what the capture agent estimates a new Promise from (FR-PR-21)."""
    return [dict(r) for r in db.execute(
        "SELECT id, what, due_at, closed_at, work_minutes, travel_minutes, actual_minutes FROM promises"
        " WHERE state = 'FULFILLED' ORDER BY closed_at DESC LIMIT ?", (limit,))]


def promise(db, promise_id: str, now: datetime | None = None) -> dict:
    """One Promise with its People, links, Slack-time, risk, whether a Draft is ready, and its history."""
    now = now or clock.now()
    row = find(db, promise_id)
    people = db.execute("SELECT p.id, p.display_name FROM promise_people pp JOIN persons p ON p.id = pp.person_id"
                        " WHERE pp.promise_id = ? ORDER BY p.display_name", (promise_id,)).fetchall()
    links = db.execute("SELECT kind, target FROM promise_links WHERE promise_id = ? ORDER BY kind, target",
                       (promise_id,)).fetchall()
    history = db.execute("SELECT at, change, detail, origin_kind, origin_ref, words FROM promise_changes"
                         " WHERE promise_id = ? ORDER BY at, rowid", (promise_id,)).fetchall()
    threads = [l["target"] for l in links if l["kind"] == "thread"]
    drafts = [l["target"] for l in links if l["kind"] == "draft"]
    ready = bool(db.execute(
        f"SELECT 1 FROM composition_drafts WHERE thread_id IN ({','.join('?' * len(threads)) or 'NULL'})"
        f" OR id IN ({','.join('?' * len(drafts)) or 'NULL'}) LIMIT 1", (*threads, *drafts)).fetchone())
    return dict(row) | {"people": [dict(p) for p in people], "links": [dict(l) | {"label": _link_label(db, l)} for l in links],
                        "history": [dict(h) | {"detail": json.loads(h["detail"]), "origin": origin_label(db, h),
                                               "origin_via": origin_via(db, h)} for h in history],
                        "slack_time": slack_time(row, now), "risk": risk(row, now), "draft_ready": ready}


def _link_label(db, link) -> str:
    """What a link points at, as Jeremy reads it: a Thread's or Case's title, a Draft's subject; else the target."""
    sql = {"thread": "SELECT title FROM threads WHERE id = ?", "case": "SELECT title FROM cases WHERE id = ?",
           "draft": "SELECT coalesce(subject, body) FROM composition_drafts WHERE id = ?"}.get(link["kind"])
    found = db.execute(sql, (link["target"],)).fetchone() if sql else None
    return (found[0] or link["target"]) if found else link["target"]


def origin_label(db, h) -> str:
    """An Origin as Jeremy reads it: who wrote what, in which Thread; his own words quoted."""
    if h["origin_kind"] == "words":
        return f'Jeremy: "{h["words"]}"'
    if h["origin_kind"] == "activity":
        a = db.execute("SELECT a.occurred_at, t.title, p.display_name FROM activities a JOIN threads t ON t.id = a.thread_id"
                       " LEFT JOIN identities i ON i.id = a.identity_id LEFT JOIN persons p ON p.id = i.person_id"
                       " WHERE a.id = ?", (h["origin_ref"],)).fetchone()
        if a:
            day = datetime.fromisoformat(a["occurred_at"]).astimezone(_local()).strftime("%d %b")
            return f"{a['display_name'] or 'Jeremy'} in “{a['title']}”, {day}"
    return f"{h['origin_kind'].replace('_', ' ')} {h['origin_ref']}"


VIA = {"gmail": "email", "fastmail": "email", "slack": "slack", "quo": "sms", "gcal": "meeting"}


def origin_via(db, h) -> str:
    """How an Origin reached Jeremy, as a Thread's messages show it: email, slack, sms, call, meeting, words,
    customer_request or tana."""
    if h["origin_kind"] != "activity":
        return h["origin_kind"]
    a = db.execute("SELECT c.platform, a.data FROM activities a JOIN threads t ON t.id = a.thread_id"
                   " JOIN channels ch ON ch.id = t.channel_id JOIN connections c ON c.id = ch.connection_id"
                   " WHERE a.id = ?", (h["origin_ref"],)).fetchone()
    if a is None:
        return "activity"
    sync = (json.loads(a["data"] or "{}") or {}).get("synchronous")
    return sync if sync in ("call", "meeting") else VIA.get(a["platform"], "email")


STATUSES = ("overdue", "urgent", "at_risk", "on_track", "tentative")


def status(row) -> str:
    """A row's status, the bullet every list shows: Tentative, Overdue, Urgent, At risk, else On track. A Prepped Meeting
    is On track too (Jeremy, 2026-11-18); its tick only stops its Prep minutes counting toward At risk."""
    if row["state"] == "TENTATIVE":
        return "tentative"
    return row.get("risk") or "on_track"


def section(row, now: datetime) -> str:
    if row["state"] == "TENTATIVE":
        return "tentative"
    r = risk(row, now)
    if r:
        return r
    day = datetime.fromisoformat(row["due_at"]).astimezone(_local()).date()
    today = now.astimezone(_local()).date()
    return "today" if day <= today else "this_week" if day < today + timedelta(days=7) else "later"


def view(db, now: datetime | None = None) -> dict:
    """The Promises page (FR-PR-8): owed Promises by section, soonest first, and "captured up to"."""
    now = now or clock.now()
    out = {s: [] for s in SECTIONS}
    for (pid,) in db.execute("SELECT id FROM promises WHERE state IN ('TENTATIVE', 'OPEN') ORDER BY due_at, id"):
        p = promise(db, pid, now)
        out[section(p, now)].append(p | {"kind": "promise"})
    from . import prep  # prep reads promises: imported here, not at the top
    for m in prep.rows(db, now):  # Prep beside Promises, marked as Prep (FR-PR-17)
        out[m["section"]].append(prep_row(m))
    for rows in out.values():
        rows.sort(key=lambda r: r["due_at"])
        for r in rows:
            r["status"] = status(r)
    return {"sections": out, "captured": capture_mark(db), "count": sum(len(v) for v in out.values())}


def prep_row(m: dict) -> dict:
    """A Meeting's Prep shaped as a Promise row, so every list shows it the same way: "Prep" stands where the
    People owed stand, the Meeting's title is what, its start is the time. `kind` tells them apart."""
    # ponytail: "Prep" as a People name keeps every list unchanged; give lists a `who` field if it confuses agents
    return {"kind": "prep", "id": m["event_uri"], "what": m["title"], "due_at": m["start_at"], "due_inferred": 0,
            "work_minutes": 0 if m["prepared"] else (m["prep_minutes"] or 0),
            "travel_minutes": m["travel_minutes"] or 0, "state": "OPEN", "people": [{"id": None, "display_name": "Prep"}],
            "links": [{"kind": "tana", "target": m["brief"], "label": "Brief"}] if m["brief"] else [],
            "history": [], "draft_ready": False, "slack_time": m["slack_time"], "risk": m["risk"],
            "prepared": m["prepared"], "has_prep": bool(m["has_prep"]), "unread": m["unread"],
            "location": m["location"], "leave_at": m["leave_at"], "end_at": m["end_at"]}


ALERT_MOBILE = "alert_mobile"  # service_settings: where the At-risk SMS goes; absent = no SMS


def alert_mobile(db) -> str | None:
    row = db.execute("SELECT value FROM service_settings WHERE name = ?", (ALERT_MOBILE,)).fetchone()
    return row[0] if row else None


@writer("write")
def set_alert_mobile(db, number: str | None, *, now: datetime | None = None) -> None:
    """Jeremy's own mobile for the At-risk SMS (ADR 0026), or None to stop them. Only ever his own number."""
    if number is None:
        db.execute("DELETE FROM service_settings WHERE name = ?", (ALERT_MOBILE,))
        commands.label("At-risk SMS off")
    else:
        if not re.fullmatch(r"\+[1-9]\d{7,14}", number):
            raise Refused("a mobile number is written +15065550100")
        db.execute("INSERT INTO service_settings (name, value) VALUES (?, ?)"
                   " ON CONFLICT (name) DO UPDATE SET value = excluded.value", (ALERT_MOBILE, number))
        commands.label("At-risk SMS on")
    commands.not_undoable("set it again with comms-terminal alerts")


RISK_NAMES = {"overdue": "Overdue", "urgent": "Urgent", "at_risk": "At risk"}


def alert_text(p: dict, now: datetime) -> str:
    """One line: what, for whom, by when, and how to open it."""
    due = datetime.fromisoformat(p["due_at"]).astimezone(_local()).strftime("%a %d %b %H:%M")
    who = ", ".join(x["display_name"] for x in p["people"]) or "someone"
    head = "Overdue" if p["risk"] == "overdue" else f"{RISK_NAMES[p['risk']]} ({p['slack_time']} min Slack-time)"
    return f"{head}: {p['what']} for {who}, due {due}. comms-terminal promise {p['id']}"


ALERT_LEVEL = {"at_risk": "at_risk", "urgent": "urgent", "overdue": "urgent"}  # one SMS per level (FR-PR-18)


def alert(db, send, now: datetime | None = None) -> int:
    """The wake pass (FR-PR-18): one SMS to Jeremy's mobile per Open Promise and per Meeting's Prep the first time
    it is At risk, and one more the first time it is Urgent or Overdue (Jeremy, 2026-11-20). The alert is recorded before the send, so a lost send is never repeated (at most once).
    `send(to, text)` is the Quo sender; None or no mobile set means no SMS."""
    to = alert_mobile(db)
    if to is None or send is None:
        return 0
    now, n = now or clock.now(), 0
    sent: dict[tuple, set] = {}
    for kind, item, level in db.execute("SELECT kind, item_id, level FROM promise_alerts"):
        sent.setdefault((kind, item), set()).add(level)

    def due(kind: str, item: str, risk: str | None) -> bool:
        """Records and says yes to a level not yet texted, nor any above it (Urgent covers Overdue)."""
        level = ALERT_LEVEL.get(risk)
        done = sent.get((kind, item), set())
        if level is None or level in done or (level == "at_risk" and "urgent" in done):
            return False
        with db:
            db.execute("INSERT INTO promise_alerts (kind, item_id, level, sent_at) VALUES (?, ?, ?, ?)",
                       (kind, item, level, _iso(now)))
        return True

    for (pid,) in db.execute("SELECT id FROM promises WHERE state = 'OPEN' ORDER BY due_at").fetchall():
        p = promise(db, pid, now)
        if due("promise", pid, p["risk"]):
            n += _send(send, to, alert_text(p, now), pid)
    from . import prep  # prep reads promises: imported here, not at the top
    for m in prep.rows(db, now):
        if due("prep", m["event_uri"], m["risk"]):
            n += _send(send, to, prep_alert_text(m), m["event_uri"])
    return n


def _send(send, to: str, text: str, item: str) -> int:
    try:
        send(to, text)
        return 1
    except Exception as e:  # noqa: BLE001 - at most once: a failed SMS is logged, not retried
        logging.getLogger("comms_terminal.promises").info("At-risk SMS for %s failed: %s", item, e)
        return 0


def prep_alert_text(m: dict) -> str:
    start = datetime.fromisoformat(m["start_at"]).astimezone(_local()).strftime("%a %d %b %H:%M")
    need = "not prepared" if not m["prepared"] else "leave soon"
    leave = datetime.fromisoformat(m["leave_at"]).astimezone(_local()).strftime("%H:%M")
    return f"Prep {RISK_NAMES[m['risk']].lower()} ({need}): {m['title']} at {start}, leave by {leave}. {m['brief'] or 'No Brief yet'}"
