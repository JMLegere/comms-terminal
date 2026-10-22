"""Commands for a terminal agent: Threads, Drafts, and Due Tasks as Markdown, changes to a Draft, enrolling a
Person in a Sequence, and Jeremy's calendar Events. Nothing here sends, completes, or finishes; Jeremy does that from the console (FR-SEQ-22)."""

import json
import sys
from datetime import date, datetime, timedelta

from . import calendar, files, inbox, sequences
from .platforms import PLATFORMS
from .tui import (CONTEXT_SOURCES, DRAFT_FIELDS, ReplyError, connection_label, draft_history, draft_text,
                  socket_context, staged_files, task_markdown, thread_addresses, thread_text)


def add_parsers(sub):
    t = sub.add_parser("threads", help="list Threads in a Status view: id, status detail, who and where, title")
    t.add_argument("view", nargs="?", default="Todo", choices=list(inbox.VIEWS))
    t = sub.add_parser("thread", help="one Thread as Markdown, with its Attio and Tana context")
    t.add_argument("id", type=int)
    sub.add_parser("drafts", help="list Drafts, then the Connections a new Draft can use")
    d = sub.add_parser("draft", help="one Draft as Markdown; with --to/--cc/--subject/--body, change it first (never sends)")
    d.add_argument("id", help="a Draft id, or `new` (needs --connection for a new Thread, or --thread for a reply)")
    d.add_argument("--revision", type=int, help="the revision you read; required to change an existing Draft")
    d.add_argument("--connection", type=int, help="new Draft: the Connection id it sends from")
    d.add_argument("--thread", type=int, help="new Draft: the Thread it replies to; its reply box sends it")
    for field in ("to", "cc", "subject"):
        d.add_argument(f"--{field}")
    d.add_argument("--body", help="the body, or - to read it from stdin")
    sub.add_parser("tasks", help="Due Tasks as Markdown, each with its Person and Draft (Sequences)")
    e = sub.add_parser("enroll", help="enroll one Person in a Sequence; its first Task is due today, or from --start")
    e.add_argument("person", help="a Person id, email address, or exact name")
    e.add_argument("sequence", help="a Sequence id or exact name")
    e.add_argument("--start", help="a later Start date: YYYY-MM-DD, or a number of days from today")
    v = sub.add_parser("events", help="Jeremy's calendar Events, repeats expanded, local time (ADR 0005)")
    v.add_argument("--from", dest="start", type=date.fromisoformat, help="first day, YYYY-MM-DD (default today)")
    v.add_argument("--to", dest="end", type=date.fromisoformat, help="last day, YYYY-MM-DD (default 7 days on)")
    v.add_argument("--attendee", help="only Events with this address, or anyone at this domain")
    v.add_argument("--query", help="only Events whose title contains this text")
    v.add_argument("--last", type=int, metavar="N", help="the last N Events that already started, newest first")
    v.add_argument("--deleted", action="store_true", help="include Deleted Events (gone from Google, kept here)")
    v.add_argument("--json", action="store_true", help="one JSON object per line")


def run(a, db):
    try:
        if a.cmd == "threads":
            for t in inbox.queue(db, a.view):
                print(f"{t['id']}\t{t['detail']}\t{t['who']} · {t['where']}\t{t['title']}")
        elif a.cmd == "thread":
            print(thread(db, a.id, a.socket), end="")
        elif a.cmd == "drafts":
            for d in inbox.list_drafts(db):
                reply = f" reply · Thread {d['thread_id']}" if d["thread_id"] else ""  # sent from the Thread's reply box
                print(f"{d['id']}\t{d['kind']}{reply}\t{d['to'] or '-'}\t{d['subject'] or ''}")
            print("\nConnections (draft new --connection ID):")
            for c in inbox.connections(db):
                print(f"{c['id']}\t{connection_label(c)}")
        elif a.cmd == "tasks":
            due = sequences.tasks_due(db)
            print("".join(task_markdown(db, t) + "\n" for t in due) or "No Tasks due.\n", end="")
        elif a.cmd == "enroll":
            print(enroll(db, a.person, a.sequence, getattr(a, "start", None)))
        elif a.cmd == "events":
            print(events(db, a), end="")
        else:
            changes = {k: getattr(a, k) for k in ("to", "cc", "subject", "body") if getattr(a, k) is not None}
            if changes.get("body") == "-":
                changes["body"] = sys.stdin.read()
            print(draft(db, a.id, changes, a.revision, a.connection, getattr(a, "thread", None)), end="")
    except (KeyError, ValueError, inbox.DraftConflict) as e:  # sequences.Refused is a ValueError
        raise SystemExit(f"comms-terminal {a.cmd}: {e.args[0] if e.args else e}") from e


def thread(db, thread_id, socket) -> str:
    v = inbox.thread_view(db, thread_id)
    emails, domains = thread_addresses([v])
    context = {}
    for source in CONTEXT_SOURCES if emails or domains else ():
        try:
            context[source] = (socket_context(socket, source)(emails, domains), None)
        except ReplyError as e:
            context[source] = (None, str(e))
    return thread_text(v, files.raw_dir(db), context)


def draft(db, ref, changes, revision, connection_id, thread_id=None) -> str:
    if ref == "new" and thread_id is not None:  # a reply Draft: the Thread gives its envelope
        if old := inbox.reply_draft(db, thread_id):
            raise ValueError(f"Thread {thread_id} already has reply Draft {old['id']}: change it with "
                             f"`comms-terminal draft {old['id']} --revision {old['revision']}`")
        d = inbox.save_reply_draft(db, thread_id, changes.pop("body", ""))
        if d is None:
            raise ValueError("a reply Draft needs --body")
        if changes:
            d = inbox.save_draft(db, {k: d[k] for k in DRAFT_FIELDS} | changes, draft_id=d["id"],
                                 expected_revision=d["revision"])
    elif ref == "new":
        c = next((c for c in inbox.connections(db) if c["id"] == connection_id), None)
        if c is None:
            raise ValueError("a new Draft needs --connection: an id from `comms-terminal drafts`")
        kind = PLATFORMS[c["platform"]].format
        values = dict(connection_id=c["id"], kind=kind, sender=c["self_id"] or "", to="", cc="",
                      subject="" if kind == "email" else None, body="")
        d = inbox.save_draft(db, values | changes)
    else:
        d = inbox.get_draft(db, int(ref))
        if changes:
            if revision is None:
                raise ValueError(f"pass --revision {d['revision']} (the revision you read) to change Draft {d['id']}")
            d = inbox.save_draft(db, {k: d[k] for k in DRAFT_FIELDS} | changes, draft_id=d["id"],
                                 expected_revision=revision)
    c = next((c for c in inbox.connections(db) if c["id"] == d["connection_id"]), None)
    history = draft_history(db, d["kind"], d["connection_id"], d["to"]) or []
    return draft_text(d, connection_label(c), history, files.raw_dir(db), (d["id"], d["revision"]),
                      staged_files(db, ("draft", d["id"])))


def enroll(db, person: str, sequence: str, start: str | None = None) -> str:
    rows = db.execute("SELECT p.id FROM persons p WHERE CAST(p.id AS TEXT) = ? OR lower(p.display_name) = lower(?)"
                      " UNION SELECT person_id FROM identities WHERE lower(value) = lower(?) AND person_id IS NOT NULL",
                      (person, person, person)).fetchall()
    if len(rows) != 1:
        raise ValueError(f"{'no' if not rows else 'more than one'} Person matches {person!r}")
    match = [q for q in sequences.sequences(db) if str(q["id"]) == sequence or q["name"].lower() == sequence.lower()]
    if len(match) != 1:
        raise ValueError(f"no Sequence matches {sequence!r}")
    now = sequences._now()
    day = sequences.parse_start(start, now) if start else None
    if start and day is None:
        raise ValueError(f"--start {start!r} is neither YYYY-MM-DD nor a number of days")
    eid = sequences.enroll(db, rows[0][0], match[0]["id"], now, start=day)
    return f"Enrolled in {match[0]['name']}: the first Task is due {sequences.first_due(db, eid, now)}."


def events(db, a) -> str:
    today = date.today()
    if a.last:
        start, end = date(1970, 1, 1), today + timedelta(days=1)
    else:
        start, end = a.start or today, (a.end or (a.start or today) + timedelta(days=7)) + timedelta(days=1)
    found = calendar.expand(db, start, end, deleted=a.deleted)
    if a.attendee:
        key = a.attendee.lower().lstrip("@")
        hit = lambda m: m == key or m.endswith("@" + key)  # noqa: E731
        found = [e for e in found if any(hit(p["email"]) for p in e["attendees"] + [e["organizer"] or {"email": ""}])]
    if a.query:
        found = [e for e in found if a.query.lower() in e["title"].lower()]
    if a.last:
        now = datetime.now().astimezone()
        found = [e for e in found if calendar._local(e["start"]) <= now][::-1][:a.last]
    if a.json:
        return "".join(json.dumps({**{k: e[k] for k in ("id", "kind", "all_day", "title", "location", "join_url",
                                                          "organizer", "attendees", "state", "repeats", "deleted_at")},
                                   "start": e["start"].isoformat(), "end": e["end"].isoformat()}) + "\n" for e in found)
    return "".join(event_line(e) + "\n" for e in found) or "No Events.\n"


def event_line(e) -> str:
    start, end = calendar._local(e["start"]), calendar._local(e["end"])
    when = f"{start:%Y-%m-%d %a} all day" if e["all_day"] else f"{start:%Y-%m-%d %a %H:%M}-{end:%H:%M}"
    who = e["organizer"]["email"] + " (organizer)" if e["organizer"] else ""
    more = f" +{len(e['attendees']) - 1}" if len(e["attendees"]) > 1 else ""
    gone = f"\tdeleted {e['deleted_at'][:10]}" if e["deleted_at"] else ""
    return f"{when}\t{e['title']}\t[{e['state'].lower()}]\t{who}{more}{gone}"
