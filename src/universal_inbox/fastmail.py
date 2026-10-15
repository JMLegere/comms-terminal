"""Fastmail adapter: a second email Connection.

Incoming mail is event-driven, like Gmail (#113): a Fastmail rule sends a copy to INBOUND, Cloudflare Email
Routing hands it to the Email Worker, and /hooks/email files it under this Connection with its raw source.

Everything else goes through Fastmail's MCP server, since Jeremy's Basic plan refuses JMAP and IMAP:
Universal Inbox signs in as a connected app, the way omp does (OAuth with PKCE; scopes mcp-read,
mcp-action, mcp-send; `scripts/oauth_mcp.py`). Replies go out with `send_email`, which threads and
quotes them the Fastmail way; a 10-minute poll of Sent catches replies Jeremy writes in Fastmail itself;
the one-time import reads Inbox and Sent as text.
"""

import logging
import threading
from datetime import datetime, timedelta, timezone

from . import inbox
from .mcp import McpClient, McpError

MCP = "https://api.fastmail.com/mcp"
TOKEN = "https://api.fastmail.com/oauth/refresh"
INBOUND = "fastmail@inbox.agentmaple.com"  # the Fastmail rule's "Send a copy to" address (#113)
BOUNCES = ("mailer-daemon@", "postmaster@")
log = logging.getLogger("universal_inbox.fastmail")


class Client(McpClient):
    mcp, token_url, name = MCP, TOKEN, "Fastmail"


# --- inbound -------------------------------------------------------------------------------------

def native_id(message_id) -> str:
    mid = message_id[0] if isinstance(message_id, list) and message_id else str(message_id or "")
    mid = mid.strip()
    return mid if mid.startswith("<") else f"<{mid}>"


def _addrs(items) -> list[str]:
    return [a["email"].lower() for a in items or [] if a.get("email")]


def to_incoming(db, connection, e: dict, mine: set[str], thread_ids=()) -> inbox.Incoming | None:
    """A read_email result -> Incoming; threads by Fastmail's thread ID and by the Message-IDs of the other
    messages in that Fastmail thread (`thread_ids`, which the forwarded copies are stored under); None for
    a bounce."""
    sender = (e.get("from") or [{}])[0]
    addr = (sender.get("email") or "").lower()
    if addr.startswith(BOUNCES):
        return None
    refs = [r[0] for r in db.execute(
        "SELECT native_id FROM activities WHERE kind = 'MESSAGE' AND json_extract(data, '$.fm_thread') = ?",
        (e["threadId"],))] + list(thread_ids)
    attachments = [[a.get("name") or "attachment", a.get("type") or "", a.get("size") or 0]
                   for a in e.get("attachments") or []]
    return inbox.Incoming(
        connection_id=connection["id"], channel=connection["self_id"], channel_kind="mailbox", kind="MESSAGE",
        native_id=native_id(e.get("messageId")) if e.get("messageId") else f"<fastmail-{e['id']}@inbox>",
        sender=None if addr in mine else inbox.Identity("email", addr, sender.get("name") or None),
        occurred_at=datetime.fromisoformat(e["receivedAt"].replace("Z", "+00:00")),
        content=e.get("bodyText") or "", thread_ref=refs, title=e.get("subject") or None,
        data={"from": addr, "from_name": sender.get("name") or "", "subject": e.get("subject") or "",
              "to": _addrs(e.get("to")), "cc": _addrs(e.get("cc")), "fm_id": e["id"], "fm_thread": e["threadId"],
              "attachments": attachments})


def identities(client) -> set[str]:
    out = client.tool("list_identities")
    items = out.get("items", out) if isinstance(out, dict) else out
    return {i["email"].lower() for i in items if i.get("email")}


def _known(db, nid: str) -> bool:
    return db.execute("SELECT 1 FROM activities WHERE kind = 'MESSAGE' AND native_id = ?", (nid,)).fetchone() is not None


def thread_message_ids(client, thread_id: str, but: str) -> list[str]:
    out = client.tool("read_thread", threadId=thread_id)
    emails = out if isinstance(out, list) else out.get("emails") or out.get("results") or []
    return [native_id(x["messageId"]) for x in emails if x.get("messageId") and x.get("id") != but]


def sync(db, client, connection, since: datetime, mine: set[str], limit=50, pages=1,
         queries=("in:inbox", "in:sent")) -> list[int]:
    """Ingest messages received after `since` that are not stored yet; returns Thread IDs."""
    threads, found = [], []
    for query in queries:
        cursor = None
        for _ in range(pages):
            args = {"query": query, "limit": limit} | ({"cursor": cursor} if cursor else {})
            page = client.tool("search_email", **args)
            for r in page.get("results", []):
                at = datetime.fromisoformat(r["receivedAt"].replace("Z", "+00:00"))
                if at > since and not _known(db, native_id(r.get("messageId")) if r.get("messageId") else ""):
                    found.append((at, r["id"]))
            cursor = page.get("nextCursor")
            if not page.get("hasMore") or not cursor:
                break
    for _, fm_id in sorted(set(found)):  # oldest first, so replies find their Thread
        e = client.tool("read_email", id=fm_id)
        inc = to_incoming(db, connection, e, mine, thread_message_ids(client, e["threadId"], e["id"]))
        if inc is None:
            continue
        t = inbox.ingest(db, inc)
        log.info("fastmail %s %s", "stored" if t else "duplicate", inc.native_id)
        if t:
            threads.append(t.id)
    return [inbox.get_thread(db, tid).id for tid in threads]


def poll_loop(db_path: str, client, stop: threading.Event, every: float = 600) -> None:
    """Replies Jeremy wrote in Fastmail itself, every 10 minutes (incoming mail arrives by the hook);
    this poll is also the Connection's health check (decision #119)."""
    mine = None
    while True:
        db, conn = inbox.connect(db_path), None
        try:
            conn = db.execute("SELECT * FROM connections WHERE platform = 'fastmail'").fetchone()
            if conn is not None:
                mine = mine or identities(client)
                since = datetime.fromisoformat(conn["connected_at"])
                sync(db, client, conn, since, mine, queries=("in:sent",))
                inbox.set_probe(db, conn["id"], None)
        except (McpError, OSError, ValueError, KeyError) as e:
            log.info("fastmail poll failed: %s", e)
            if conn is not None:
                inbox.set_probe(db, conn["id"], str(e)[:200])
        finally:
            db.close()
        if stop.wait(every):
            return


def import_recent(db, client, connection, days: int) -> int:
    """One-time import (Jeremy, 2026-10-12): the last `days` of Inbox and Sent, every Thread left in Todo."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    tids = {inbox.get_thread(db, tid).id for tid in
            sync(db, client, connection, since, identities(client), limit=50, pages=40)}
    for tid in tids:
        inbox.reopen_imported(db, tid)
    return len(tids)


# --- outbound ------------------------------------------------------------------------------------

def make_sender(connection, credential, shared):
    """Uses the service's one Fastmail client (it rotates refresh tokens, so only one may exist)."""
    client = shared.get("fastmail")
    return FastmailSender(client) if client is not None else None


class FastmailSender:
    """Replies through Fastmail's send_email (mode reply): Fastmail sets the threading headers, quotes the
    original, files the copy in Sent, and the Sent poll then sees it as already stored."""

    def __init__(self, client):
        self.client, self.mine = client, None

    def send(self, thread, text, to, cc, history, files=()) -> str:
        if files:
            raise inbox.SendFailed("Fastmail can't send Attachments")
        try:
            self.mine = self.mine or identities(self.client)
        except (McpError, OSError, ValueError) as err:
            raise inbox.SendFailed(f"Fastmail send failed: {err}") from err
        inbound = [h for h in history if h["data"].get("from") and h["data"]["from"] not in self.mine]
        target = (inbound or history or [None])[-1]
        if target is None:
            raise inbox.SendFailed("no Fastmail message to reply to in this thread")
        fm_id = target["data"].get("fm_id") or self.find(target)
        args = {"mode": "reply", "emailId": fm_id, "body": text, "format": "text"}
        if to:
            args["to"] = [{"email": a} for a in to]
        if cc:
            args["cc"] = [{"email": a} for a in cc]
        try:
            sent = self.client.tool("send_email", **args)
            e = self.client.tool("read_email", id=sent["emailId"])
        except (McpError, OSError, KeyError, ValueError) as err:
            raise inbox.SendFailed(f"Fastmail send failed: {err}") from err
        return native_id(e.get("messageId")) if e.get("messageId") else f"<fastmail-{sent['emailId']}@inbox>"

    def start(self, to, cc, subject, body, key, files=()) -> str:
        """A new email via send_email with no reply mode; the Sent poll then sees it as already stored."""
        if files:
            raise inbox.SendFailed("Fastmail can't send Attachments")
        args = {"to": [{"email": a} for a in to], "subject": subject, "body": body, "format": "text"}
        if cc:
            args["cc"] = [{"email": a} for a in cc]
        try:
            sent = self.client.tool("send_email", **args)
        except McpError as err:  # a 5xx may come after Fastmail sent it
            raise (inbox.SendUnknown if "MCP HTTP 5" in str(err) else inbox.SendFailed)(str(err)) from err
        except (OSError, ValueError) as err:
            raise inbox.SendUnknown(f"Fastmail send outcome unknown: {err}") from err
        try:
            e = self.client.tool("read_email", id=sent["emailId"])
        except (McpError, OSError, KeyError, ValueError):
            e = {}
        return native_id(e["messageId"]) if e.get("messageId") else f"<fastmail-{sent.get('emailId')}@inbox>"

    def find(self, h) -> str:
        """Fastmail's ID for a message that arrived as a forwarded copy: search by sender, match Message-ID."""
        sender = h["data"].get("from") or ""
        try:
            page = self.client.tool("search_email", query=f"from:{sender}" if sender else "in:inbox", limit=50)
        except (McpError, OSError, ValueError) as err:
            raise inbox.SendFailed(f"Fastmail send failed: {err}") from err
        for r in page.get("results", []):
            if r.get("messageId") and native_id(r["messageId"]) == h["native_id"]:
                return r["id"]
        raise inbox.SendFailed("could not find this message in Fastmail to reply to")
