"""Issues (ADR 0018; .agents/prd/2026-11-07-issues-and-action-queue.md): the copy of every Issue assigned to Jeremy
on a Platform with an Issue connection, and its teams' statuses. The Platform owns every field; the copy keeps
only `planned_day` of its own, across every update. Adapters hand in Issues already normalized:

    {"id", "identifier", "title", "description", "url", "team_id", "state_id", "priority", "estimate", "due_date",
     "created_at", "updated_at", "completed_at", "canceled_at", "data": {...}}

Interface: issue_connection, upsert, remove, apply_read, replace_statuses, set_account, record_read, issues.
"""

import json
from datetime import datetime

from . import clock, inbox

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
    """The copy with each Issue's status name and type, for lists and the CLI."""
    return [dict(r) | {"data": json.loads(r["data"])} for r in db.execute(
        "SELECT i.*, s.name AS state_name, s.type AS state_type FROM issues i"
        " LEFT JOIN issue_statuses s ON s.id = i.state_id ORDER BY i.identifier")]
