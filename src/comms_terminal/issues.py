"""Issues (ADR 0019; .agents/prd/2026-11-07-issues-and-action-queue.md): the copy of every Issue assigned to Jeremy
on a Platform with an Issue connection, and its teams' statuses. The Platform owns every field; the copy keeps
only `planned_day` of its own, across every update. Adapters hand in Issues already normalized:

    {"id", "identifier", "title", "description", "url", "team_id", "state_id", "priority", "estimate", "due_date",
     "created_at", "updated_at", "completed_at", "canceled_at", "data": {...}}

Jeremy's changes (update, create, undo) apply to the copy at once and go to the Platform as Actions (ADR 0020);
carry_update and carry_create send them.

Interface: issue_connection, upsert, remove, apply_read, replace_statuses, set_account, record_read, issues,
find, update, create, undo, carry_update, carry_create.
"""

import json
from datetime import date, datetime, time, timedelta

from . import actions, clock, commands, inbox
from .writers import writer

FIELDS = ("identifier", "title", "description", "url", "team_id", "state_id", "priority", "estimate", "due_date",
          "created_at", "updated_at", "completed_at", "canceled_at")
READ_ERROR = "Issue read failed: "  # the Issue connection's Last Error after a failed full read


def issue_connection(db, platform: str):
    return db.execute("SELECT * FROM connections WHERE platform = ? AND kind = 'issue'", (platform,)).fetchone()


def upsert(db, connection_id: str, issue: dict, now: datetime) -> None:
    """Takes the Platform's version of one Issue; an older version than the copy's is ignored, and the Planned
    day stays as it is."""
    with db:
        old = db.execute("SELECT updated_at FROM issues WHERE id = ?", (issue["id"],)).fetchone()
        if old is not None and issue["updated_at"] < old["updated_at"]:
            return  # a late or replayed event: the copy already holds something newer
        data = dict(issue.get("data") or {})
        if old is not None and "blocking" not in data:  # a webhook carries no relations: keep the last read's
            kept = json.loads(db.execute("SELECT data FROM issues WHERE id = ?", (issue["id"],)).fetchone()[0])
            data |= {k: kept[k] for k in ("blocking", "blocked") if k in kept}
        values = [issue.get(f) for f in FIELDS] + [json.dumps(data, sort_keys=True), clock.iso(now)]
        if old is None:
            db.execute(f"INSERT INTO issues (id, connection_id, {', '.join(FIELDS)}, data, copied_at)"
                       f" VALUES (?, ?, {', '.join('?' * len(FIELDS))}, ?, ?)", [issue["id"], connection_id, *values])
        else:
            db.execute(f"UPDATE issues SET {', '.join(f'{f} = ?' for f in FIELDS)}, data = ?, copied_at = ?"
                       " WHERE id = ?", [*values, issue["id"]])


def remove(db, issue_id: str) -> None:
    """The Issue left Jeremy (reassigned, deleted, archived): the copy drops it, Planned day and all (FR-IS-12)."""
    with db:
        db.execute("DELETE FROM issues WHERE id = ?", (issue_id,))


def apply_read(db, connection_id: str, issues: list[dict], now: datetime) -> int:
    """A full read (FR-IS-3): every Issue it returns is upserted, and every copied Issue it no longer returns is
    removed. Returns how many the copy holds."""
    seen = {i["id"] for i in issues}
    for issue in issues:
        upsert(db, connection_id, issue, now)
    with db:
        for (issue_id,) in db.execute("SELECT id FROM issues WHERE connection_id = ?", (connection_id,)).fetchall():
            if issue_id not in seen:
                db.execute("DELETE FROM issues WHERE id = ?", (issue_id,))
    return len(seen)


def replace_statuses(db, connection_id: str, statuses: list[dict]) -> None:
    """Every team status the Platform has: {"id", "team_id", "name", "type", "position"}."""
    with db:
        keep = {s["id"] for s in statuses}
        for s in statuses:
            db.execute("INSERT INTO issue_statuses (id, connection_id, team_id, name, type, position)"
                       " VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (id) DO UPDATE SET team_id = excluded.team_id,"
                       " name = excluded.name, type = excluded.type, position = excluded.position"
                       " WHERE (team_id, name, type, position) IS NOT (excluded.team_id, excluded.name,"
                       " excluded.type, excluded.position)",
                       (s["id"], connection_id, s["team_id"], s["name"], s["type"], s.get("position") or 0))
        for (status_id,) in db.execute("SELECT id FROM issue_statuses WHERE connection_id = ?",
                                       (connection_id,)).fetchall():
            if status_id not in keep:
                db.execute("DELETE FROM issue_statuses WHERE id = ?", (status_id,))


def set_account(db, connection_id: str, account_id: str) -> None:
    """Jeremy's own id on the Platform, which says whether a pushed Issue is assigned to him."""
    with db:
        db.execute("UPDATE connections SET self_id = ? WHERE id = ? AND self_id != ?",
                   (account_id, connection_id, account_id))


def record_read(db, connection_id: str, error: str | None) -> None:
    """A full read's outcome on the Issue connection: a failure is its Last Error until a read succeeds."""
    row = db.execute("SELECT last_error FROM connections WHERE id = ?", (connection_id,)).fetchone()
    current = row["last_error"] or ""
    if error is not None:
        inbox.set_connection_error(db, connection_id, READ_ERROR + error)
    elif current.startswith(READ_ERROR):
        inbox.set_connection_error(db, connection_id, None)


def issues(db) -> list[dict]:
    """The copy with each Issue's status name and type and, when its latest change was refused, "Not saved: …"."""
    return [dict(r) | {"data": json.loads(r["data"]), "not_saved": actions.latest_refusal(db, r["id"])}
            for r in db.execute("SELECT i.*, s.name AS state_name, s.type AS state_type FROM issues i"
                                " LEFT JOIN issue_statuses s ON s.id = i.state_id ORDER BY i.identifier")]


def find(db, key: str):
    """An Issue by its id or its identifier (BIZ-1426)."""
    return db.execute("SELECT * FROM issues WHERE id = ? OR identifier = ? COLLATE NOCASE", (key, key)).fetchone()


# --- Jeremy's changes (ADR 0020) -------------------------------------------------------------------------------

EDITABLE = ("title", "description", "state_id", "priority", "estimate", "due_date")  # the core fields (FR-IS-11)
LINEAR_INPUT = {"title": "title", "description": "description", "state_id": "stateId", "priority": "priority",
                "estimate": "estimate", "due_date": "dueDate"}


def _check(db, row, fields: dict) -> None:
    if not fields or set(fields) - set(EDITABLE):
        raise ValueError(f"an Issue change sets only {', '.join(EDITABLE)}")
    if "title" in fields and not (fields["title"] or "").strip():
        raise ValueError("an Issue needs a title")
    if fields.get("estimate") is not None and fields["estimate"] != int(fields["estimate"]):
        raise ValueError("an estimate is whole points (Linear's estimate is an Int)")
    if fields.get("priority") is not None and fields["priority"] not in (0, 1, 2, 3, 4):
        raise ValueError("priority is 0 (none), 1 (urgent), 2 (high), 3 (medium) or 4 (low)")
    if fields.get("state_id") is not None and db.execute(
            "SELECT 1 FROM issue_statuses WHERE team_id = ?", (row["team_id"],)).fetchone() and not db.execute(
            "SELECT 1 FROM issue_statuses WHERE id = ? AND team_id = ?", (fields["state_id"], row["team_id"])).fetchone():
        raise ValueError("that status belongs to another team")


@writer("write", module="issues")
def update(db, issue_id: str, fields: dict, *, now: datetime | None = None) -> str | None:
    """Sets some of an Issue's core fields in the copy at once and enqueues the Action that sets them on its
    Platform (FR-IS-11, FR-AQ-1). Returns the Action's id, or None when nothing changes."""
    now = now or clock.now()
    row = find(db, issue_id)
    if row is None:
        raise KeyError(f"no Issue {issue_id}")
    _check(db, row, fields)
    changed = {k: v for k, v in fields.items() if row[k] != v}
    if not changed:
        return None
    previous = {k: row[k] for k in changed}
    with inbox._write_snapshot(db):
        db.execute(f"UPDATE issues SET {', '.join(f'{k} = ?' for k in changed)} WHERE id = ?",
                   [*changed.values(), row["id"]])
        action = actions.enqueue(db, row["connection_id"], "issue_update", row["id"], changed, previous, True, now)
    commands.label(f"{row['identifier']} {', '.join(changed)}: {row['title']}")
    commands.target("issues", row["id"])
    commands.inverse("issues.undo", action)  # a new Action sets the old values back (ADR 0020)
    return action


@writer("write", module="issues")
def create(db, issue_id: str, team_id: str, title: str, *, description: str | None = None,
           state_id: str | None = None, priority: int = 0, estimate: float | None = None,
           due_date: str | None = None, now: datetime | None = None) -> str:
    """A new Issue assigned to Jeremy, in the copy at once with the id the Client chose (so a repeated create
    cannot make two, FR-AQ-5), and the Action that creates it on Linear. Its identifier arrives when confirmed."""
    now = now or clock.now()
    commands.label(f"New Issue: {title}")
    commands.not_undoable("a new Linear Issue is not undone")  # D-3
    connection = issue_connection(db, "linear")
    if connection is None:
        raise ValueError("Linear has no Issue connection")
    fields = {"title": title, "description": description, "state_id": state_id, "priority": priority,
              "estimate": estimate, "due_date": due_date}
    _check(db, {"team_id": team_id}, {k: v for k, v in fields.items() if v is not None} or {"title": title})
    stamp = clock.iso(now)
    with inbox._write_snapshot(db):
        db.execute("INSERT INTO issues (id, connection_id, identifier, title, description, team_id, state_id,"
                   " priority, estimate, due_date, created_at, updated_at, copied_at)"
                   " VALUES (?, ?, 'NEW', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                   (issue_id, connection["id"], title, description, team_id, state_id, priority, estimate, due_date,
                    stamp, stamp, stamp))
        return actions.enqueue(db, connection["id"], "issue_create", issue_id,
                               {**{k: v for k, v in fields.items() if v is not None}, "team_id": team_id}, None,
                               True, now)


@writer("write", module="issues")
def undo(db, action_id: str, *, now: datetime | None = None) -> str | None:
    """Undo of an Issue change is a new Action that sets the old values back (FR-AQ-6)."""
    commands.label("Issue change undone")
    commands.not_undoable("it is an undo")
    a = db.execute("SELECT * FROM actions WHERE id = ?", (action_id,)).fetchone()
    if a is None or a["kind"] != "issue_update":
        raise ValueError("only an Issue change can be undone; delete a new Issue in Linear")
    return update(db, a["target"], json.loads(a["previous"]), now=now)


def _linear_input(payload: dict) -> dict:
    """Column values as Linear's input fields; estimate is an Int there (introspected 2026-11-07)."""
    return {LINEAR_INPUT[k]: int(v) if k == "estimate" and v is not None else v for k, v in payload.items()}


def _confirmed(db, issue_id: str, issue: dict, now: datetime) -> None:
    with db:
        db.execute("UPDATE issues SET identifier = ?, url = ?, updated_at = max(updated_at, ?), copied_at = ?"
                   " WHERE id = ?", (issue["identifier"], issue.get("url"), issue["updatedAt"], clock.iso(now),
                                    issue_id))


def _snap_back(db, a) -> None:
    """A refused change: each field it set takes its old value back, unless something newer replaced it."""
    payload, previous = json.loads(a["payload"]), json.loads(a["previous"] or "null")
    with db:
        if previous is None:  # a refused create: the Issue never existed
            db.execute("DELETE FROM issues WHERE id = ? AND identifier = 'NEW'", (a["target"],))
            return
        for field, value in payload.items():
            db.execute(f"UPDATE issues SET {field} = ? WHERE id = ? AND {field} IS ?", (previous[field], a["target"],
                                                                                       value))


def carry_update(db, a, client, now: datetime) -> dict:
    payload = json.loads(a["payload"])
    out = client.write("issueUpdate", {"id": a["target"], "input": _linear_input(payload)})
    if out["outcome"] == "confirmed":
        _confirmed(db, a["target"], out["issue"], now)
    elif out["outcome"] == "refused":
        _snap_back(db, a)
    return out


def carry_create(db, a, client, now: datetime) -> dict:
    payload = json.loads(a["payload"])
    if a["attempts"]:  # an earlier try (counted before this one) had no answer: it may have made the Issue
        from .linear import LinearError
        try:
            found = client.query(ISSUE_BY_ID, {"id": a["target"]}).get("issue")
        except LinearError:
            found = None
        if found:
            _confirmed(db, a["target"], found, now)
            return {"outcome": "confirmed", "issue": found}
    connection = db.execute("SELECT self_id FROM connections WHERE id = ?", (a["connection_id"],)).fetchone()
    data = {"id": a["target"], "teamId": payload.pop("team_id"), "assigneeId": connection["self_id"] or None,
            **_linear_input(payload)}
    out = client.write("issueCreate", {"input": {k: v for k, v in data.items() if v is not None}})
    if out["outcome"] == "confirmed":
        _confirmed(db, a["target"], out["issue"], now)
    elif out["outcome"] == "refused":
        _snap_back(db, a)
    return out


ISSUE_BY_ID = """query IssueById($id: String!) { issue(id: $id) { id identifier url createdAt updatedAt } }"""


# --- Planning (PRD FR-IS-5–10, FR-IS-13): Planned day, Day capacity, urgency, warnings, Rollover ----------------

DAY_HOURS = 9                # a workday's capacity before busy time (FR-IS-6)
DEFAULT_HOURS = 1            # an Issue with no estimate (FR-IS-7)
POINT_HOURS = {"linear": 0.5}  # one estimate point, per Issue connection's Platform: Linear 1 point = 30 minutes
DUE_SOON_DAYS = 7            # an Unplanned Issue due this soon warns (FR-IS-13)
DAY_END = time(18)           # Jeremy's working day ends: today's capacity counts only the hours before it (ADR 0023)
FEASIBILITY = ((0, 1.0), (1, 0.75), (3, 0.4))  # SuperPlan: at most this many days of room before the due date,
                                               # this share of the feasibility coefficient (FR-PL-2)
HORIZON_DAYS = 3650          # Plan Issues' safety cap, not a planning horizon (FR-PL-4)


def _open(i) -> bool:
    return not (i["completed_at"] or i["canceled_at"])


def today(now: datetime | None = None) -> date:
    """Jeremy's day (America/Halifax, as Sequences count days)."""
    from .sequences import LOCAL
    return (now or clock.now()).astimezone(LOCAL).date()


@writer("write", module="issues")
def plan(db, issue_id: str, day: str | None, *, now: datetime | None = None) -> None:
    """Sets or clears an Issue's Planned day (FR-IS-5). Local only: no Action, nothing goes to the Platform."""
    row = find(db, issue_id)
    if row is None:
        raise KeyError(f"no Issue {issue_id}")
    if day is not None:
        day = date.fromisoformat(day).isoformat()
    with inbox._write_snapshot(db):
        db.execute("UPDATE issues SET planned_day = ? WHERE id = ?", (day, row["id"]))
    commands.label(f"{row['identifier']} planned {day or 'for no day'}: {row['title']}")
    commands.target("issues", row["id"])
    commands.inverse("issues.plan", row["id"], row["planned_day"])


def size_hours(issue, platform: str = "linear") -> float:
    """Its estimate in hours, else DEFAULT_HOURS (FR-IS-7)."""
    return DEFAULT_HOURS if issue["estimate"] is None else issue["estimate"] * POINT_HOURS[platform]


def _busy_hours(db, day: date, now: datetime) -> float | None:
    """Busy calendar hours on `day` (Jeremy's time), merged where Events overlap; today counts only what is still
    to come. None when a busy All-day Event takes the whole day."""
    from . import calendar
    from .sequences import LOCAL
    lo = datetime.combine(day, time(), LOCAL)
    hi = lo + timedelta(days=1)
    if day == today(now):  # today: only what is still to come before the working day ends (FR-PL-10)
        lo, hi = max(lo, now.astimezone(LOCAL)), datetime.combine(day, DAY_END, LOCAL)
    spans = []
    for e in calendar.expand(db, day, day + timedelta(days=1), deleted=False):
        if not e["busy"]:
            continue
        if e["all_day"]:
            return None
        start, end = max(clock.local(e["start"]), lo), min(clock.local(e["end"]), hi)
        if end > start:
            spans.append((start, end))
    total, last = 0.0, None
    for start, end in sorted(spans):
        if last is not None and start < last:
            start = last
        if end > start:
            total += (end - start).total_seconds() / 3600
            last = end if last is None else max(last, end)
    return total


def _room(db, day: date, now: datetime) -> float:
    """A day's hours before any Issue: 9 less busy time; today only the hours left before DAY_END; a passed day or
    a busy All-day Event none (FR-IS-6, FR-PL-10)."""
    from .sequences import LOCAL
    busy = _busy_hours(db, day, now)
    if busy is None or day < today(now):
        return 0.0
    hours = DAY_HOURS if day > today(now) else min(DAY_HOURS, max(0.0, (datetime.combine(
        day, DAY_END, LOCAL) - now.astimezone(LOCAL)).total_seconds() / 3600))
    return max(0.0, hours - busy)


def capacity(db, day: date, now: datetime | None = None) -> dict:
    """A day's capacity and use (FR-IS-6): {"capacity", "used", "left"} in hours. Weekends hold none unless an
    Issue is planned there."""
    now = now or clock.now()
    planned = [i for i in issues(db) if i["planned_day"] == day.isoformat() and _open(i)]
    used = sum(size_hours(i) for i in planned)
    cap = 0.0 if day.weekday() >= 5 and not planned else _room(db, day, now)
    return {"capacity": round(cap, 2), "used": round(used, 2), "left": round(cap - used, 2)}


def coefficients(db) -> dict:
    return {r["name"]: r["value"] for r in db.execute("SELECT name, value FROM urgency")}


@writer("write", module="issues")
def set_urgency(db, name: str, value: float, *, now: datetime | None = None) -> None:
    """Changes one urgency coefficient (FR-IS-8)."""
    if db.execute("SELECT 1 FROM urgency WHERE name = ?", (name,)).fetchone() is None:
        raise KeyError(f"no urgency coefficient {name}; see `comms-terminal urgency list`")
    old = db.execute("SELECT value FROM urgency WHERE name = ?", (name,)).fetchone()[0]
    with inbox._write_snapshot(db):
        db.execute("UPDATE urgency SET value = ? WHERE name = ?", (float(value), name))
    commands.label(f"Urgency {name} {float(value):g}")
    commands.inverse("issues.set_urgency", name, old)


PRIORITY_NAMES = {0: "none", 1: "urgent", 2: "high", 3: "medium", 4: "low"}


def _due_ramp(due: str, day: date) -> float:
    """Taskwarrior's due factor: 1.0 at 7+ days late, 0.2 at 14+ days out, linear between."""
    days = (date.fromisoformat(due) - day).days
    if days <= -7:
        return 1.0
    if days >= 14:
        return 0.2
    return round(1.0 - (days + 7) * 0.8 / 21, 4)


def urgency(i: dict, c: dict, day: date) -> float:
    """Sum of coefficient × factor (FR-IS-8). The Planned day and the Slip status do not count."""
    score = c[f"priority.{PRIORITY_NAMES[i['priority']]}"]
    if i["due_date"]:
        score += c["due"] * _due_ramp(i["due_date"], day)
    if i.get("state_type") == "started":
        score += c["started"]
    data = i["data"]
    score += c["blocking"] * bool(data.get("blocking")) + c["blocked"] * bool(data.get("blocked"))
    created = date.fromisoformat(i["created_at"][:10])
    score += c["age"] * min(1.0, max(0, (day - created).days) / 365)
    return round(score, 4)


def warnings(db, now: datetime | None = None) -> dict[str, list[str]]:
    """Each open Issue's warnings by id (FR-IS-9, FR-IS-13), as SuperPlan gave them."""
    now = now or clock.now()
    day, c = today(now), coefficients(db)
    open_ = [i for i in issues(db) if _open(i)]
    out: dict[str, list[str]] = {}
    planned = sorted((i for i in open_ if i["planned_day"]), key=lambda i: urgency(i, c, day))
    caps: dict[str, dict] = {}
    for n, i in enumerate(planned):
        found = []
        if i["due_date"] and i["planned_day"] > i["due_date"]:
            found.append("Planned after its due date")
        if len(planned) > 1:  # SuperPlan: an item at percentile p may sit at most 100 - p days out
            p = n / (len(planned) - 1) * 100
            if (date.fromisoformat(i["planned_day"]) - day).days > 100 - p:
                found.append("Planned too far out for its urgency")
        if i["planned_day"] >= day.isoformat():  # a passed day has no capacity left; that Issue is Overdue instead
            cap = caps.setdefault(i["planned_day"], capacity(db, date.fromisoformat(i["planned_day"]), now))
            if size_hours(i) > cap["capacity"]:
                found.append("Larger than a day")
        if found:
            out[i["id"]] = found
    for i in open_:
        if not i["planned_day"] and i["due_date"] and (date.fromisoformat(i["due_date"]) - day).days <= DUE_SOON_DAYS:
            out.setdefault(i["id"], []).append("Due soon and Unplanned")
    return out


def overdue(i: dict, day: date) -> bool:
    """An Overdue Issue (FR-PL-11): open, with its Planned day or due date before `day`."""
    d = day.isoformat()
    return _open(i) and bool((i["planned_day"] and i["planned_day"] < d) or (i["due_date"] and i["due_date"] < d))


def on_today(i: dict, day: date) -> bool:
    """Today lists it (FR-PL-12): open, with its Planned day or due date today or earlier."""
    d = day.isoformat()
    return _open(i) and bool((i["planned_day"] and i["planned_day"] <= d) or (i["due_date"] and i["due_date"] <= d))


def today_load(db, now: datetime | None = None) -> dict:
    """Today's figures (FR-PL-13): its capacity, the hours of the Issues it lists that are not Overdue ("used"),
    and the hours of its Overdue Issues, apart."""
    now = now or clock.now()
    day = today(now)
    rows = [i for i in issues(db) if on_today(i, day)] if "issues" in inbox.modules_on(db) else []
    cap = capacity(db, day, now)["capacity"]
    used = sum(size_hours(i) for i in rows if not overdue(i, day))
    late = sum(size_hours(i) for i in rows if overdue(i, day))
    return {"capacity": cap, "used": round(used, 2), "left": round(cap - used, 2), "overdue": round(late, 2)}


@writer("write", module="issues")
def pin(db, issue_id: str, pinned: bool, *, now: datetime | None = None) -> None:
    """Pins or unpins an Issue (FR-PL-9): Plan Issues leaves a Pinned Issue alone. Local only."""
    row = find(db, issue_id)
    if row is None:
        raise KeyError(f"no Issue {issue_id}")
    with inbox._write_snapshot(db):
        db.execute("UPDATE issues SET pinned = ? WHERE id = ?", (int(bool(pinned)), row["id"]))
    commands.label(f"{row['identifier']} {'pinned' if pinned else 'unpinned'}: {row['title']}")
    commands.target("issues", row["id"])
    commands.inverse("issues.pin", row["id"], bool(row["pinned"]))


def _feasibility(i: dict, room, day0: date, c: dict) -> float:
    """SuperPlan's boost (FR-PL-2): the fewer workdays from today to the due date that still hold the Issue, the
    more of the feasibility coefficient it gains."""
    if not i["due_date"]:
        return 0.0
    due, size, days, d = date.fromisoformat(i["due_date"]), size_hours(i), 0, day0
    while d <= due and days <= FEASIBILITY[-1][0]:
        if d.weekday() < 5 and room(d) >= size:
            days += 1
        d += timedelta(days=1)
    return next((c["feasibility"] * share for most, share in FEASIBILITY if days <= most), 0.0)


@writer("write", module="issues")
def plan_all(db, *, now: datetime | None = None) -> int:
    """Plan Issues (ADR 0023, FR-PL-1–8): every open Issue that is not Pinned gets a Planned day. Workdays fill from
    today on; each takes the most urgent remaining Issue that still fits, until none does. An Issue larger than a
    workday goes on today and uses none of it. Pinned Issues keep their days and use their capacity; a Pinned
    Overdue Issue uses today's. One Command; Undo puts back each Planned day it changed. Returns how many changed."""
    now = now or clock.now()
    day0, c = today(now), coefficients(db)
    open_ = [i for i in issues(db) if _open(i)]
    pinned = [i for i in open_ if i["pinned"] and i["planned_day"]]
    rooms: dict[date, float] = {}

    def room(d: date) -> float:  # a workday's hours left before this run places anything
        if d not in rooms:
            fixed = sum(size_hours(i) for i in pinned if i["planned_day"] == d.isoformat()
                        or (d == day0 and i["planned_day"] < d.isoformat()))
            rooms[d] = _room(db, d, now) - fixed if d.weekday() < 5 else 0.0
        return rooms[d]

    movable = [i for i in open_ if not i["pinned"]]
    score = {i["id"]: urgency(i, c, day0) + _feasibility(i, room, day0, c) for i in movable}
    queue = sorted((i for i in movable if size_hours(i) <= DAY_HOURS), key=lambda i: (-score[i["id"]], i["identifier"]))
    placed = {i["id"]: day0.isoformat() for i in movable if size_hours(i) > DAY_HOURS}  # oversized (FR-PL-5)
    d = day0
    for _ in range(HORIZON_DAYS):  # ponytail: an Issue no workday can hold walks the whole cap, one calendar read a day
        if not queue:
            break
        left = room(d)
        while (pick := next((i for i in queue if size_hours(i) <= left + 1e-9), None)) is not None:
            placed[pick["id"]] = d.isoformat()
            left -= size_hours(pick)
            queue.remove(pick)
        d += timedelta(days=1)
    changed = [i for i in movable if i["planned_day"] != placed.get(i["id"])]
    if not changed:
        return 0
    with inbox._write_snapshot(db):
        db.executemany("UPDATE issues SET planned_day = ? WHERE id = ?", [(placed.get(i["id"]), i["id"]) for i in changed])
    commands.label(f"Planned {len(changed)} Issue{'s' if len(changed) != 1 else ''}")
    for i in changed:
        commands.target("issues", i["id"])
        commands.inverse("issues.plan", i["id"], i["planned_day"])
    return len(changed)


# --- The Issues page (FR-UI-1): Linear's List with its Display options, plus Planned day and Urgency -----------

GROUPINGS = ("planned_day", "status", "priority", "team", "project", "none")
ORDERINGS = ("urgency", "priority", "due_date", "planned_day", "updated")
COMPLETED = {"none": None, "day": 1, "week": 7, "month": 30}  # closed Issues shown, by days since they closed
PROPERTIES = ("status", "priority", "estimate", "due_date", "planned_day", "project", "labels", "team")
DISPLAY = {"grouping": "planned_day", "subgrouping": "none", "ordering": "urgency", "completed": "day",
           "sub_issues": True, "empty_groups": False,
           "properties": ["status", "priority", "estimate", "due_date", "planned_day"]}
PRIORITY_ORDER = (1, 2, 3, 4, 0)  # Linear lists Urgent first and No priority last


def display(options: dict | None) -> dict:
    """Display options, each unknown or missing one at its default."""
    out = dict(DISPLAY)
    for k, v in (options or {}).items():
        if k in ("grouping", "subgrouping") and v in GROUPINGS or k == "ordering" and v in ORDERINGS \
                or k == "completed" and v in COMPLETED or k in ("sub_issues", "empty_groups") and isinstance(v, bool):
            out[k] = v
        elif k == "properties" and isinstance(v, list):
            out[k] = [p for p in v if p in PROPERTIES]
    return out


def _day_title(d: str, day: date) -> str:
    x = date.fromisoformat(d)
    rel = {0: "Today", 1: "Tomorrow"}.get((x - day).days)
    return f"{rel} · {x:%a %b %-d}" if rel else f"{x:%a %b %-d}"


def _group_key(i: dict, by: str, day: date, statuses: dict) -> tuple[tuple, str]:
    """(sort key, title) of an Issue's group."""
    if by == "planned_day":
        return ((1, "") if not i["planned_day"] else (0, i["planned_day"]),
                "Unplanned" if not i["planned_day"] else _day_title(i["planned_day"], day))
    if by == "status":
        s = statuses.get(i["state_id"]) or {"name": "No status", "position": 1e9, "type": "", "team_id": "~"}
        return ((s.get("team_id") or "", s["position"], s["name"]), s["name"])
    if by == "priority":
        return ((PRIORITY_ORDER.index(i["priority"]),), PRIORITY_NAMES[i["priority"]].title() if i["priority"]
                else "No priority")
    if by == "team":
        return ((i["data"].get("team_key") or "",), i["data"].get("team") or i["data"].get("team_key") or "No team")
    if by == "project":
        p = i["data"].get("project")
        return ((p is None, p or ""), p or "No project")
    return ((), "")


def _order_key(i: dict, by: str):
    if by == "urgency":
        return (-i["urgency"], i["identifier"])
    if by == "priority":
        return (PRIORITY_ORDER.index(i["priority"]), -i["urgency"])
    if by == "due_date":
        return (i["due_date"] is None, i["due_date"] or "", -i["urgency"])
    if by == "planned_day":
        return (i["planned_day"] is None, i["planned_day"] or "", -i["urgency"])
    return (-datetime.fromisoformat(i["updated_at"].replace("Z", "+00:00")).timestamp(),)  # newest change first


def view(db, options: dict | None = None, now: datetime | None = None, today_list: bool = False) -> dict:
    """The Issues page as groups (and sub-groups) of Issues, each with its urgency, size, warnings, Overdue mark and
    "Not saved"; a Planned day group carries its Day capacity. `today_list` keeps what Today lists (FR-PL-12)."""
    now = now or clock.now()
    o, day = display(options), today(now)
    c, warns = coefficients(db), warnings(db, now)
    statuses = {r["id"]: dict(r) for r in db.execute("SELECT * FROM issue_statuses")}
    keep = COMPLETED[o["completed"]]
    shown = []
    for i in issues(db) if "issues" in inbox.modules_on(db) else ():  # Issues off: an empty page and pane (ADR 0017)
        closed = i["completed_at"] or i["canceled_at"]
        if closed and (keep is None or (now - datetime.fromisoformat(closed.replace("Z", "+00:00"))).days >= keep):
            continue
        if not o["sub_issues"] and i["data"].get("parent"):
            continue
        if today_list and not on_today(i, day):
            continue
        shown.append(i | {"urgency": urgency(i, c, day), "size": size_hours(i), "warnings": warns.get(i["id"], []),
                          "overdue": overdue(i, day)})

    def groups(rows, by, level):
        found: dict = {}
        for i in rows:
            key, title = _group_key(i, by, day, statuses)
            found.setdefault((key, title), []).append(i)
        if o["empty_groups"] and by == "status":
            for s in statuses.values():
                found.setdefault(((s["team_id"], s["position"], s["name"]), s["name"]), [])
        if o["empty_groups"] and by == "planned_day":
            for n in range(7):
                d = (day + timedelta(days=n)).isoformat()
                found.setdefault(((0, d), _day_title(d, day)), [])
        out = []
        for (key, title), members in sorted(found.items(), key=lambda kv: kv[0][0]):
            g = {"title": title, "count": len(members)}
            if by == "planned_day" and key[0] == 0 and key[1] >= day.isoformat():  # a passed day holds only Overdue
                g["capacity"] = capacity(db, date.fromisoformat(key[1]), now)
            if level == 0 and o["subgrouping"] != "none" and o["subgrouping"] != by:
                g["groups"] = groups(members, o["subgrouping"], 1)
            else:
                g["issues"] = sorted(members, key=lambda i: _order_key(i, o["ordering"]))
            out.append(g)
        return out

    return {"options": o, "groups": groups(shown, o["grouping"], 0) if o["grouping"] != "none" else
            [{"title": "", "count": len(shown), "issues": sorted(shown, key=lambda i: _order_key(i, o["ordering"]))}],
            "today": day.isoformat()}


def flat(v: dict) -> list[tuple[str, dict]]:
    """The view's rows in order: ("group", g), ("subgroup", g), ("issue", i)."""
    out = []
    for g in v["groups"]:
        if g["title"]:
            out.append(("group", g))
        for s in g.get("groups", []):
            out.append(("subgroup", s))
            out += [("issue", i) for i in s["issues"]]
        out += [("issue", i) for i in g.get("issues", [])]
    return out
