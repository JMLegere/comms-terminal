"""Issues (ADR 0018; .agents/prd/2026-11-07-issues-and-action-queue.md): the copy of every Issue assigned to Jeremy
on a Platform with an Issue connection, and its teams' statuses. The Platform owns every field; the copy keeps
only `planned_day` of its own, across every update. Adapters hand in Issues already normalized:

    {"id", "identifier", "title", "description", "url", "team_id", "state_id", "priority", "estimate", "due_date",
     "created_at", "updated_at", "completed_at", "canceled_at", "data": {...}}

Jeremy's changes (update, create, undo) apply to the copy at once and go to the Platform as Actions (ADR 0019);
carry_update and carry_create send them.

Interface: issue_connection, upsert, remove, apply_read, replace_statuses, set_account, record_read, issues,
find, update, create, undo, carry_update, carry_create.
"""

import json
from datetime import datetime

from . import actions, clock, inbox
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
        values = [issue.get(f) for f in FIELDS] + [json.dumps(issue.get("data") or {}, sort_keys=True), clock.iso(now)]
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


# --- Jeremy's changes (ADR 0019) -------------------------------------------------------------------------------

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


@writer("write")
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
        return actions.enqueue(db, row["connection_id"], "issue_update", row["id"], changed, previous, True, now)


@writer("write")
def create(db, issue_id: str, team_id: str, title: str, *, description: str | None = None,
           state_id: str | None = None, priority: int = 0, estimate: float | None = None,
           due_date: str | None = None, now: datetime | None = None) -> str:
    """A new Issue assigned to Jeremy, in the copy at once with the id the Client chose (so a repeated create
    cannot make two, FR-AQ-5), and the Action that creates it on Linear. Its identifier arrives when confirmed."""
    now = now or clock.now()
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


@writer("write")
def undo(db, action_id: str, *, now: datetime | None = None) -> str | None:
    """Undo of an Issue change is a new Action that sets the old values back (FR-AQ-6)."""
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
