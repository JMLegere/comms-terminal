"""Commands for a terminal agent: Threads and Drafts as Markdown, and changes to a Draft. Nothing here sends;
Jeremy sends from the console."""

import sys

from . import files, inbox
from .platforms import PLATFORMS
from .tui import (CONTEXT_SOURCES, DRAFT_FIELDS, ReplyError, connection_label, draft_history, draft_text,
                  socket_context, staged_files, thread_addresses, thread_text)


def add_parsers(sub):
    t = sub.add_parser("threads", help="list Threads in a Status view: id, status detail, who and where, title")
    t.add_argument("view", nargs="?", default="Todo", choices=list(inbox.VIEWS))
    t = sub.add_parser("thread", help="one Thread as Markdown, with its Attio and Tana context")
    t.add_argument("id", type=int)
    sub.add_parser("drafts", help="list Drafts, then the Connections a new Draft can use")
    d = sub.add_parser("draft", help="one Draft as Markdown; with --to/--cc/--subject/--body, change it first (never sends)")
    d.add_argument("id", help="a Draft id, or `new` (needs --connection)")
    d.add_argument("--revision", type=int, help="the revision you read; required to change an existing Draft")
    d.add_argument("--connection", type=int, help="new Draft: the Connection id it sends from")
    for field in ("to", "cc", "subject"):
        d.add_argument(f"--{field}")
    d.add_argument("--body", help="the body, or - to read it from stdin")


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
        else:
            changes = {k: getattr(a, k) for k in ("to", "cc", "subject", "body") if getattr(a, k) is not None}
            if changes.get("body") == "-":
                changes["body"] = sys.stdin.read()
            print(draft(db, a.id, changes, a.revision, a.connection), end="")
    except (KeyError, ValueError, inbox.DraftConflict) as e:
        raise SystemExit(f"universal-inbox {a.cmd}: {e.args[0] if e.args else e}") from e


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


def draft(db, ref, changes, revision, connection_id) -> str:
    if ref == "new":
        c = next((c for c in inbox.connections(db) if c["id"] == connection_id), None)
        if c is None:
            raise ValueError("a new Draft needs --connection: an id from `universal-inbox drafts`")
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
