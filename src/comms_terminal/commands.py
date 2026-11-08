"""The Command log (ADR 0021): every change is a Command with its Source, its words and its Inverse; Undo runs the
Inverse of the newest Command from the Console, the App or the CLI.

Interface:
- inside a writer: `label(text)`, `target(table, id)`, `inverse(writer, *args, **kwargs)`, `not_undoable(reason)`;
- `undo(db, command_id)` (a writer), `newest(db)`, `history(db, ...)`;
- `source(name)` for the place a Client call enters, `service(db, label)` around one pass of the Service's own work.

A writer called inside another writer belongs to the outer Command; the outermost writer's own declarations win,
and a writer that declares nothing gets its inner writers' Inverses, last first.
"""

import contextvars
import inspect
import json
from contextlib import contextmanager
from datetime import datetime

from . import clock
from .writers import Held, writer

SOURCES = ("console", "app", "cli", "service")
CLIENTS = ("console", "app", "cli")  # the Sources Undo serves (FR-UN-5)
NAMES = {"console": "Console", "app": "App", "cli": "CLI", "service": "Service"}
default = "service"  # this process's Source; the Console, the Local core and the CLI set theirs at start
_source = contextvars.ContextVar("command_source", default=None)
_state = contextvars.ContextVar("command", default=None)


class Refused(ValueError):
    """Undo cannot run: nothing to undo, a newer change, a change since, or no Inverse. The Command is kept."""


@contextmanager
def source(name: str):
    """Commands made inside come from `name` (a /call's Client, the Local core, the CLI)."""
    if name not in SOURCES:
        raise ValueError(f"unknown source {name!r}")
    token = _source.set(name)
    try:
        yield
    finally:
        _source.reset(token)


def current_source() -> str:
    return _source.get() or default


class _Command:
    def __init__(self, writer_name: str):
        self.writer, self.depth, self.said = writer_name, 0, []  # said: (depth, kind, value)
        self.undoes = None


def _say(kind, value):
    c = _state.get()
    if c is not None:
        c.said.append((c.depth, kind, value))


def label(text: str) -> None:
    """What this change did, in a few words, as History and the undo toast show it."""
    _say("label", text)


def target(table: str, row_id) -> None:
    """A record this change made, changed or removed. Undo needs it as this change left it (FR-UN-8); the first one
    is where History links."""
    _say("target", (table, row_id))


def inverse(name: str, *args, **kwargs) -> None:
    """One writer call that puts this change back; several run last first."""
    _say("inverse", (name, args, kwargs))


def not_undoable(reason: str) -> None:
    """This change cannot be put back cleanly every time (D-3); Undo says why."""
    _say("reason", reason)


def _row(db, table: str, row_id):
    from . import remote
    r = db.execute(f'SELECT * FROM "{table}" WHERE id = ?', (row_id,)).fetchone()
    return None if r is None else json.loads(json.dumps(remote.encode(dict(r)), sort_keys=True))


def _record(db, c: _Command, at: datetime) -> str:
    from . import remote
    first = {}
    for depth, kind, value in c.said:
        first.setdefault(kind, depth)
    texts = [v for d, k, v in c.said if k == "label"]
    words = next((v for d, k, v in c.said if k == "label" and d == first["label"]), None) if texts else None
    targets = list(dict.fromkeys(v for _, k, v in c.said if k == "target"))
    shallow = min((first[k] for k in ("inverse", "reason") if k in first), default=None)
    reasons = [v for d, k, v in c.said if k == "reason" and d == shallow]
    calls = [v for d, k, v in c.said if k == "inverse" and d == shallow]
    reason = reasons[0] if reasons else (None if calls else "This change has no undo yet")
    inv = None if reason else json.dumps([[n, remote.encode(list(a)), remote.encode(k)] for n, a, k in calls])
    guard = json.dumps([[t, i, _row(db, t, i)] for t, i in targets])
    return db.execute(
        "INSERT INTO commands (at, source, writer, label, target, guard, inverse, reason, undoes)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
        (clock.iso(at), current_source(), c.writer, words or c.writer, f"{targets[0][0]}:{targets[0][1]}" if targets
         else None, guard, inv, reason, c.undoes)).fetchone()[0]


def run(name, fn, db, args, kwargs):
    """`writers.writer`'s body: the outermost writer call is a Command, committed with its changes."""
    c = _state.get()
    if c is not None:  # inside another writer, or a Service pass: part of that Command
        c.depth += 1
        try:
            return fn(db, *args, **kwargs)
        finally:
            c.depth -= 1
    try:
        at = inspect.signature(fn).bind_partial(db, *args, **kwargs).arguments.get("now") or clock.now()
    except TypeError:
        at = clock.now()
    c = _Command(name)
    token = _state.set(c)
    hold = isinstance(db, Held) and not db.held and not db.in_transaction
    changed = db.total_changes
    try:
        if hold:
            db.held = True
            db.execute("BEGIN IMMEDIATE")
        was = db.in_transaction
        try:
            result = fn(db, *args, **kwargs)
            if db.total_changes != changed:  # a call that changed nothing is no Command
                _record(db, c, at)
        except BaseException:
            if hold:
                db.held = False
                db.rollback()
            raise
        if hold:
            db.held = False
            db.commit()
        elif not was and db.in_transaction:  # ponytail: a connection that is not Held commits the Command alone
            db.commit()
        return result
    finally:
        _state.reset(token)


@contextmanager
def service(db, words):
    """One pass of the Service's own work: one Service Command, kept only when the pass changed something
    (FR-UN-18). `words` is the label, or a function of the changed tables' counts that makes it."""
    c = _Command("service")
    token = _state.set(c)
    before = db.execute("SELECT coalesce(max(seq), 0) FROM changes").fetchone()[0]
    try:
        yield
    finally:
        _state.reset(token)
    counts = dict(db.execute("SELECT tbl, count(*) FROM changes WHERE seq > ? AND tbl != 'commands' GROUP BY tbl",
                             (before,)).fetchall())
    if counts:
        text = words(counts) if callable(words) else words
        db.execute("INSERT INTO commands (at, source, writer, label, reason) VALUES (?, 'service', 'service', ?,"
                   " 'Service work is not undone')", (clock.iso(clock.now()), text))
        db.commit()


def _client_newest(db):
    return db.execute(f"SELECT * FROM commands WHERE source IN ({', '.join('?' * len(CLIENTS))})"
                      " ORDER BY at DESC, id DESC LIMIT 1", CLIENTS).fetchone()


def newest(db) -> dict | None:
    """The Command z would undo, or None when Undo has nothing (an Undo is newest, or there is none)."""
    r = _client_newest(db)
    return None if r is None or r["undoes"] is not None else dict(r)


@writer("write")
def undo(db, command_id: str, *, now: datetime | None = None) -> str:
    """Undo one Command: the newest from the Console, the App or the CLI, still as it left its records (FR-UN-5–10).
    Returns the undone Command's words."""
    from . import inbox, remote
    c = db.execute("SELECT * FROM commands WHERE id = ?", (command_id,)).fetchone()
    top = _client_newest(db)
    if c is None or top is None or c["undoes"] is not None:
        raise Refused("Nothing to undo.")
    if top["id"] != command_id:  # one level: only the newest, and an Undo has no redo (FR-UN-6, 9)
        raise Refused("Nothing to undo." if top["undoes"] is not None
                      else f"Cannot undo: a newer change came first ({top['label']}).")
    if c["inverse"] is None:
        raise Refused(f"Cannot undo {c['label']}: {c['reason']}.")
    for table, row_id, row in json.loads(c["guard"]):
        if _row(db, table, row_id) != row:
            raise Refused(f"Cannot undo {c['label']}: it changed since.")
    for name, args, kwargs in reversed(json.loads(c["inverse"])):
        args, kwargs = remote.decode(args), remote.decode(kwargs)
        inbox.admit(db, name, args, kwargs)  # an Inverse into an off Module is refused and kept (FR-UN-7)
        module, fn = name.split(".")
        real = getattr(remote.MODULES[module], fn)
        if "now" in inspect.signature(real).parameters and "now" not in kwargs:
            kwargs = kwargs | {"now": now or clock.now()}
        real(db, *args, **kwargs)
    state = _state.get()
    state.undoes = command_id
    label(f"Undo: {c['label']}")
    not_undoable("an Undo has no redo")
    return c["label"]


def history(db, *, day: str | None = None, service: bool = True, limit: int = 500) -> list[dict]:
    """The Command log, newest first: each Command with `undone` (FR-UN-19)."""
    where, args = [], []
    if day:
        where.append("substr(c.at, 1, 10) = ?")
        args.append(day)
    if not service:
        where.append("c.source != 'service'")
    rows = db.execute(
        "SELECT c.id, c.at, c.source, c.writer, c.label, c.target, c.reason, c.undoes,"
        " EXISTS (SELECT 1 FROM commands u WHERE u.undoes = c.id) AS undone FROM commands c"
        f"{' WHERE ' + ' AND '.join(where) if where else ''} ORDER BY c.at DESC, c.id DESC LIMIT ?",
        (*args, limit)).fetchall()
    return [dict(r) | {"undone": bool(r["undone"]), "source_name": NAMES[r["source"]]} for r in rows]
