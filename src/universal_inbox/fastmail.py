"""Fastmail adapter: a second email Connection.

Incoming mail is event-driven, like Gmail (#113): a Fastmail rule sends a copy to INBOUND, Cloudflare Email
Routing hands it to the Email Worker, and /hooks/email files it under this Connection with its raw source.

Everything else goes through Fastmail's MCP server, since Jeremy's Basic plan refuses JMAP and IMAP:
Universal Inbox signs in as a connected app, the way omp does (OAuth with PKCE; scopes mcp-read,
mcp-action, mcp-send; `scripts/oauth_fastmail.py`). Replies go out with `send_email`, which threads and
quotes them the Fastmail way; a 10-minute poll of Sent catches replies Jeremy writes in Fastmail itself;
the one-time import reads Inbox and Sent as text.
"""

import json
import logging
import os
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import inbox

MCP = "https://api.fastmail.com/mcp"
TOKEN = "https://api.fastmail.com/oauth/refresh"
USER_AGENT = "universal-inbox/0.1 (+https://github.com/JMLegere/universal-inbox)"
LABEL = "Fastmail"  # connections.label that marks the Fastmail Connection
INBOUND = "fastmail@inbox.agentmaple.com"  # the Fastmail rule's "Send a copy to" address (#113)
BOUNCES = ("mailer-daemon@", "postmaster@")
log = logging.getLogger("universal_inbox.fastmail")


class FastmailError(Exception):
    pass


class Retry(FastmailError):
    """Refused before running (token or session expired): safe to repeat, even for send_email."""


def _http(url, body: bytes, headers: dict, timeout=30):
    req = urllib.request.Request(url, body, {"User-Agent": USER_AGENT, **headers}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read()
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in e.headers.items()}, e.read()


class Client:
    """MCP over streamable HTTP with a refreshable OAuth token. `http` is swappable for tests.

    The refresh token comes from the systemd credential (or a 0600 file); if Fastmail ever rotates it,
    the new one is written to `state` (beside the database) and preferred from then on.
    """

    def __init__(self, cred: dict, state: Path | None = None, http=_http):
        self.cred, self.state, self.http = dict(cred), state, http
        self.access, self.expires, self.session = None, datetime.min.replace(tzinfo=timezone.utc), None
        self.ids, self.lock = 0, threading.Lock()

    @classmethod
    def load(cls, cred_bytes: bytes, state: Path | None):
        cred = json.loads(cred_bytes)
        if state is not None and state.exists():
            cred |= json.loads(state.read_text())
        return cls(cred, state)

    def _refresh(self):
        st, _, body = self.http(TOKEN, urllib.parse.urlencode({
            "grant_type": "refresh_token", "refresh_token": self.cred["refresh_token"],
            "client_id": self.cred["client_id"], "resource": self.cred.get("resource", MCP)}).encode(),
            {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"})
        tok = json.loads(body or b"{}")
        if st != 200 or "access_token" not in tok:
            raise FastmailError(f"Fastmail sign-in expired or revoked (HTTP {st} {tok.get('error', '')})".strip())
        self.access = tok["access_token"]
        self.expires = datetime.now(timezone.utc) + timedelta(seconds=int(tok.get("expires_in", 3600)) - 120)
        new = tok.get("refresh_token")
        if new and new != self.cred["refresh_token"]:
            self.cred["refresh_token"] = new
            if self.state is not None:
                fd = os.open(self.state.with_suffix(".tmp"), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "w") as f:
                    json.dump({"refresh_token": new}, f)
                os.replace(self.state.with_suffix(".tmp"), self.state)
        self.session = None

    def _rpc(self, method, params=None, notify=False):
        if self.access is None or datetime.now(timezone.utc) >= self.expires:
            self._refresh()
        if self.session is None and method != "initialize":
            self._initialize()
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        if not notify:
            self.ids += 1
            msg["id"] = self.ids
        headers = {"Authorization": f"Bearer {self.access}", "Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-06-18"}
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        st, h, body = self.http(MCP, json.dumps(msg).encode(), headers)
        if st == 401:
            self.access = None
            raise Retry("Fastmail refused the access token")
        if st == 404 and self.session:  # the server forgot the session: start a new one next call
            self.session = None
            raise Retry("Fastmail MCP session expired")
        if st >= 300:
            raise FastmailError(f"Fastmail MCP HTTP {st}")
        if method == "initialize":
            self.session = h.get("mcp-session-id") or ""
        if notify:
            return None
        text = body.decode()
        if "data:" in text and not text.lstrip().startswith("{"):
            text = "\n".join(ln[5:].strip() for ln in text.splitlines() if ln.startswith("data:"))
        out = json.loads(text)
        if "error" in out:
            raise FastmailError(f"Fastmail MCP error: {out['error'].get('message', out['error'])}")
        return out["result"]

    def _initialize(self):
        self._rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                 "clientInfo": {"name": "universal-inbox", "version": "0.1"}})
        self._rpc("notifications/initialized", notify=True)

    def tool(self, name: str, **args):
        with self.lock:
            for attempt in (1, 2):
                try:
                    res = self._rpc("tools/call", {"name": name, "arguments": args})
                    break
                except Retry:  # never repeat after other failures: send_email may already have sent
                    if attempt == 2:
                        raise
        if res.get("isError"):
            msg = " ".join(c.get("text", "") for c in res.get("content", []))
            raise FastmailError(f"Fastmail {name} failed: {msg[:200]}")
        if "structuredContent" in res:
            return res["structuredContent"]
        return json.loads(res["content"][0]["text"])


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
    return threads


def poll_loop(db_path: str, client, stop: threading.Event, every: float = 600) -> None:
    """Replies Jeremy wrote in Fastmail itself, every 10 minutes (incoming mail arrives by the hook);
    failures show as the Connection's Last Error (health)."""
    mine = None
    while True:
        db, conn = inbox.connect(db_path), None
        try:
            conn = db.execute("SELECT * FROM connections WHERE label = ?", (LABEL,)).fetchone()
            if conn is not None:
                mine = mine or identities(client)
                since = datetime.fromisoformat(conn["connected_at"])
                sync(db, client, conn, since, mine, queries=("in:sent",))
                inbox.set_connection_error(db, conn["id"], None)
        except (FastmailError, OSError, ValueError, KeyError) as e:
            log.info("fastmail poll failed: %s", e)
            if conn is not None:
                inbox.set_connection_error(db, conn["id"], str(e)[:200])
        finally:
            db.close()
        if stop.wait(every):
            return


def import_recent(db, client, connection, days: int) -> int:
    """One-time import (Jeremy, 2026-10-12): the last `days` of Inbox and Sent, every Thread left in Todo."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    tids = sync(db, client, connection, since, identities(client), limit=50, pages=40)
    for tid in set(tids):
        t = inbox.get_thread(db, tid)
        if t.status != "TODO":
            replied = db.execute("SELECT 1 FROM activities WHERE thread_id = ? AND identity_id IS NULL"
                                 " AND kind = 'MESSAGE'", (tid,)).fetchone()
            inbox.act(db, tid, inbox.MarkTodo("NEW_REPLY" if replied else "CREATED"))
    return len(set(tids))


# --- outbound ------------------------------------------------------------------------------------

class FastmailSender:
    """Replies through Fastmail's send_email (mode reply): Fastmail sets the threading headers, quotes the
    original, files the copy in Sent, and the Sent poll then sees it as already stored."""

    def __init__(self, client):
        self.client, self.mine = client, None

    def send(self, thread, text, to, cc, history) -> str:
        try:
            self.mine = self.mine or identities(self.client)
        except (FastmailError, OSError, ValueError) as err:
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
        except (FastmailError, OSError, KeyError, ValueError) as err:
            raise inbox.SendFailed(f"Fastmail send failed: {err}") from err
        return native_id(e.get("messageId")) if e.get("messageId") else f"<fastmail-{sent['emailId']}@inbox>"

    def find(self, h) -> str:
        """Fastmail's ID for a message that arrived as a forwarded copy: search by sender, match Message-ID."""
        sender = h["data"].get("from") or ""
        try:
            page = self.client.tool("search_email", query=f"from:{sender}" if sender else "in:inbox", limit=50)
        except (FastmailError, OSError, ValueError) as err:
            raise inbox.SendFailed(f"Fastmail send failed: {err}") from err
        for r in page.get("results", []):
            if r.get("messageId") and native_id(r["messageId"]) == h["native_id"]:
                return r["id"]
        raise inbox.SendFailed("could not find this message in Fastmail to reply to")
