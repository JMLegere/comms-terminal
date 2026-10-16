"""Email adapter (Plain-style, ADR 0002): forwarded mail in through /hooks/email, replies out through Resend."""

import base64
import hashlib
import hmac
import html
import json
import logging
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email import message_from_bytes, policy
from email.parser import BytesHeaderParser
from email.utils import getaddresses, make_msgid, parseaddr, parsedate_to_datetime

from . import inbox, render

SIGNATURE_WINDOW = 300  # seconds

# FR-3a: Plain's auto-reply header signals. A subject prefix alone never counts.
AUTO_REPLY_HEADERS = (
    "X-Autoreply", "X-Autorespond", "X-Amazon-Auto-Reply", "X-amzn-vacation", "X-SFDC-AutoResponse",
    "X-ServiceNow-Generated", "X-Front-Autoreply", "X-CodeTwo-AutoResponse", "X-C2AutoRespond",
    "X-MDAutoResponse", "X-QQ-Auto-Reply", "X-MS-Exchange-Generated-Message-Source",
)


def verify(headers, body: bytes, secret: bytes, now: datetime) -> bool:
    """HMAC-SHA256 over "<timestamp>.<recipient>.<body>" when the Worker sends X-Inbox-To (#113), else
    over "<timestamp>.<body>" (Workers deployed before it); sent as X-Inbox-Signature: sha256=<hex>."""
    ts, sig = headers.get("X-Inbox-Timestamp", ""), headers.get("X-Inbox-Signature", "")
    if not ts.isdigit() or abs(now.timestamp() - int(ts)) > SIGNATURE_WINDOW:
        return False
    to = headers.get("X-Inbox-To")
    signed = ts.encode() + b"." + (to.encode() + b"." if to is not None else b"") + body
    want = "sha256=" + hmac.new(secret, signed, hashlib.sha256).hexdigest()
    return hmac.compare_digest(want, sig)


def is_auto_reply(msg) -> bool:
    auto = (msg.get("Auto-Submitted") or "").strip().lower()
    if auto and auto != "no":
        return True
    if (msg.get("Precedence") or "").strip().lower() in ("auto_reply", "bulk"):
        return True
    return any(msg.get(h) is not None for h in AUTO_REPLY_HEADERS)


def _text(msg) -> str:
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    text = part.get_content()
    if part.get_content_subtype() == "html":  # HTML-only mail: the console's converter, as plain text
        shown, _ = render.html_body(text)
        return render.plain(shown)
    return text


def parse(raw: bytes, connection) -> inbox.Incoming | None:
    """Forwarded RFC 5322 message -> Incoming, or None for an auto-reply."""
    msg = message_from_bytes(raw, policy=policy.default)
    if is_auto_reply(msg):
        return None
    sender = parseaddr(msg.get("From", ""))[1].lower()
    mailbox = connection["self_id"].lower()
    message_id = (msg.get("Message-ID") or "").strip() or f"<sha256-{hashlib.sha256(raw).hexdigest()}@inbox>"
    refs = (msg.get("In-Reply-To", "") + " " + msg.get("References", "")).split()
    try:
        occurred = parsedate_to_datetime(msg["Date"]).astimezone(timezone.utc)
    except (TypeError, ValueError):
        occurred = datetime.now(timezone.utc)
    subject = str(msg.get("Subject", "") or "")
    return inbox.Incoming(
        connection_id=connection["id"], channel=mailbox, channel_kind="mailbox", kind="MESSAGE",
        native_id=message_id,
        sender=None if sender == mailbox else inbox.Identity("email", sender, parseaddr(msg.get("From", ""))[0] or None),
        occurred_at=occurred, content=_text(msg), thread_ref=refs, title=subject or None,
        data={"from": sender, "from_name": parseaddr(msg.get("From", ""))[0], "subject": subject,
              "to": [a.lower() for _, a in getaddresses(msg.get_all("To", [])) if a],
              "cc": [a.lower() for _, a in getaddresses(msg.get_all("Cc", [])) if a]})


def reply_all(history: list[dict], self_address: str) -> tuple[list[str], list[str]]:
    """Default recipients (FR-3b): last inbound From/To/Cc, or last outbound To/Cc, minus Jeremy."""
    me = self_address.lower()
    last = next((h["data"] for h in reversed(history) if h["data"].get("from") not in (None, me)), {})
    if not last:
        last = next((h["data"] for h in reversed(history) if h["data"].get("to")), {})
    to = [a for a in [last.get("from"), *last.get("to", [])] if a and a != me]
    cc = [a for a in last.get("cc", []) if a != me and a not in to]
    return list(dict.fromkeys(to)), list(dict.fromkeys(cc))


def reply_sender(history: list[dict], self_address: str) -> tuple[list[str], list[str]]:
    """Recipients for a reply to the sender only: last inbound From, or last outbound To."""
    me = self_address.lower()
    last = next((h["data"] for h in reversed(history) if h["data"].get("from") not in (None, me)), {})
    if not last:
        last = next((h["data"] for h in reversed(history) if h["data"].get("to")), {})
    to = [last["from"]] if last.get("from") not in (None, me) else last.get("to", [])
    return list(dict.fromkeys(a for a in to if a and a != me)), []


# Resend's API sits behind Cloudflare, which rejects Python's default User-Agent (403, "error code: 1010").
USER_AGENT = "universal-inbox/0.1 (+https://github.com/JMLegere/universal-inbox)"


def _post(url, headers, payload):
    """POST JSON -> (status, body). HTTP errors return their status and the provider's message."""
    req = urllib.request.Request(url, json.dumps(payload).encode(), {**headers, "User-Agent": USER_AGENT},
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw or b"{}")
        except ValueError:
            return e.code, {"message": raw.decode(errors="replace").strip()[:200]}


def quoted(text: str, history: list[dict], self_address: str) -> dict:
    """Text and HTML for a reply that quotes the last inbound message, as Gmail does (decision #109):
    `On <date>, <name> <address> wrote:` then the message; the HTML uses Gmail's markup so mail clients
    fold the quote."""
    me = self_address.lower()
    last = next((h for h in reversed(history) if h["data"].get("from") not in (None, me)), None)
    body_html = html.escape(text).replace("\n", "<br>\n")
    if last is None or not (last.get("content") or "").strip():
        return {"text": text, "html": f"<div dir=\"ltr\">{body_html}</div>"}
    d = last["data"]
    who = f"{d['from_name']} <{d['from']}>" if d.get("from_name") else f"<{d['from']}>"
    at = datetime.fromisoformat(last["at"]).astimezone() if last.get("at") else None
    when = at.strftime("%a, %b %-d, %Y at %-I:%M %p") if at else ""
    head = f"On {when}, {who} wrote:" if when else f"{who} wrote:"
    old = last["content"].replace("\r\n", "\n").strip()
    plain = text.rstrip() + "\n\n" + head + "\n" + "\n".join(f"> {ln}" if ln else ">" for ln in old.split("\n"))
    rich = (f"<div dir=\"ltr\">{body_html}</div><br>\n<div class=\"gmail_quote\"><div dir=\"ltr\" class=\"gmail_attr\">"
            f"{html.escape(head)}<br></div><blockquote class=\"gmail_quote\" style=\"margin:0px 0px 0px 0.8ex;"
            f"border-left:1px solid rgb(204,204,204);padding-left:1ex\">{html.escape(old).replace(chr(10), '<br>' + chr(10))}"
            f"</blockquote></div>")
    return {"text": plain, "html": rich}


def make_sender(connection, credential, shared):
    """The Gmail Connection's sender (Resend, from its address), or None until the key is installed."""
    key = credential("resend-api-key")
    return EmailSender(key.decode(), connection["self_id"]) if key else None


def _get(url, headers):
    """GET JSON -> (status, body), like _post."""
    req = urllib.request.Request(url, headers={**headers, "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}


def probe(connection, credential, shared, get=_get) -> str | None:
    """Health check (decision #119): Resend accepts the key. A send-only key is refused on /domains as
    restricted_api_key, which still proves it valid. Forwarded inbound mail cannot be checked from here."""
    key = credential("resend-api-key")
    if key is None:
        return "Resend key is not installed"
    try:
        status, body = get("https://api.resend.com/domains", {"Authorization": f"Bearer {key.decode()}"})
    except (urllib.error.URLError, OSError, ValueError) as e:
        return f"Resend unreachable: {e}"
    if status < 300 or body.get("name") == "restricted_api_key":
        return None
    return f"Resend refused the key: HTTP {status} {body.get('message', '')}".strip()


def _attachments(files) -> dict:
    """Resend `attachments` for staged files (FR-A13); empty when there are none."""
    try:
        return {"attachments": [{"filename": f.name, "content": base64.b64encode(f.path.read_bytes()).decode(),
                                 **({"content_type": f.type} if f.type else {})} for f in files]} if files else {}
    except OSError as e:  # nothing has been sent yet
        raise inbox.SendFailed(f"Attachment unreadable: {e}") from e


class EmailSender:
    """Sends replies through Resend (decision #70). `post` is swappable for tests."""

    def __init__(self, api_key: str, from_address: str, post=_post):
        self.api_key, self.from_address, self.post = api_key, from_address, post

    def send(self, thread, text, to, cc, history, files=()) -> str:
        if not to:
            to, cc = reply_all(history, self.from_address)
        ids = []
        for message in history:
            mid = message.get("wire_id", message.get("native_id"))
            # Core's explicit projection distinguishes genuine incoming IDs from pending local markers.
            if "wire_id" not in message and mid and mid.lower().endswith("@resend>"):
                mid = None
            ids.append(mid)
        if ids and not ids[-1]:
            raise ValueError(inbox.EMAIL_CONFIRMATION_PENDING)
        ids = [mid for mid in ids if mid]
        message_id = make_msgid(domain=self.from_address.rsplit("@", 1)[1])
        subject = thread.title if thread.title.lower().startswith("re:") else f"Re: {thread.title}"
        headers = {"Message-ID": message_id}
        if ids:
            headers |= {"In-Reply-To": ids[-1], "References": " ".join(ids)}
        payload = {"from": self.from_address, "to": to, "cc": cc, "subject": subject,
                   **quoted(text, history, self.from_address), "headers": headers, **_attachments(files)}
        try:
            status, body = self.post("https://api.resend.com/emails",
                                     {"Authorization": f"Bearer {self.api_key}",
                                      "Content-Type": "application/json",
                                      "Idempotency-Key": message_id}, payload)
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise inbox.SendUnknown(f"email send outcome unknown: {e}") from e
        if status >= 500:
            raise inbox.SendUnknown(f"email send outcome unknown: HTTP {status}")
        if status >= 300:
            raise inbox.SendFailed(f"email send failed: HTTP {status} {body.get('message', '')}".strip())
        try:
            return inbox.resend_native_id(body["id"])
        except (KeyError, TypeError, ValueError) as e:
            raise inbox.SendUnknown("email accepted without a usable receipt ID; outcome unknown") from e

    def start(self, to, cc, subject, body, key, files=()) -> str:
        """A new email; retain its provider receipt until the signed sent event supplies its SMTP identity."""
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        payload = {"from": self.from_address, "to": to, "cc": cc, "subject": subject, "text": body,
                   **_attachments(files)}
        try:
            status, r = self.post("https://api.resend.com/emails", headers | {"Idempotency-Key": key}, payload)
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise inbox.SendUnknown(f"email send outcome unknown: {e}") from e
        if status >= 500:
            raise inbox.SendUnknown(f"email send outcome unknown: HTTP {status}")
        if status >= 300:
            raise inbox.SendFailed(f"HTTP {status} {r.get('message', '')}".strip())
        try:
            return inbox.resend_native_id(r["id"])
        except (KeyError, TypeError, ValueError) as e:
            raise inbox.SendUnknown("email accepted without a usable receipt ID; outcome unknown") from e


log = logging.getLogger("universal_inbox.email")


def store_parts(db, activity_id: int, raw: bytes, raw_sha: str | None) -> int:
    """Copy a Gmail message's file parts into the file store as its Attachments (FR-A2); returns how many.
    A message that already has Attachments, or a database in memory, gets none."""
    if inbox.files_dir(db) is None or inbox.attachments(db, activity_id):
        return 0
    items = [{"name": p.name, "type": p.type, "size": len(p.data), "inline": p.inline, "content_id": p.content_id,
              "source": {"raw": raw_sha, "part": k}, "sha": inbox.store_file(db, p.data)}
             for k, p in enumerate(render.parts(raw))]
    inbox.add_attachments(db, activity_id, items)
    return len(items)


def backfill_parts(db) -> tuple[int, int]:
    """Store the parts of every Gmail message kept in raw/ that has no Attachments yet (FR-A2); idempotent.
    Returns (messages read, Attachments stored)."""
    rows = db.execute(
        "SELECT a.id, json_extract(a.data, '$.raw') AS raw FROM activities a JOIN threads t ON t.id = a.thread_id"
        " JOIN channels ch ON ch.id = t.channel_id JOIN connections c ON c.id = ch.connection_id"
        " WHERE c.platform = 'gmail' AND a.kind = 'MESSAGE' AND json_extract(a.data, '$.raw') IS NOT NULL"
        " AND NOT EXISTS (SELECT 1 FROM attachments x WHERE x.activity_id = a.id)").fetchall()
    read = stored = 0
    for r in rows:
        path = inbox.raw_dir(db) / f"{r['raw']}.eml"
        if path.is_file():
            read += 1
            stored += store_parts(db, r["id"], path.read_bytes(), r["raw"])
    return read, stored


def handle_hook(db, connection_id: int, headers, body: bytes, secret: bytes, now: datetime) -> int:
    """POST /hooks/email -> HTTP status. 401 records a Last Error (AS-19); a valid request clears it.

    Logs one line per request with its outcome and Message-ID only (no addresses or content, NFR-5),
    so "did this email arrive?" can be answered from the journal.
    """
    if not verify(headers, body, secret, now):
        inbox.set_connection_error(db, connection_id, "inbound request failed signature check")
        log.info("email rejected: signature check failed")
        return 401
    mid = (BytesHeaderParser(policy=policy.default).parsebytes(body).get("Message-ID") or "").strip() or "(none)"
    connection = db.execute("SELECT * FROM connections WHERE id = ?", (connection_id,)).fetchone()
    try:
        incoming = parse(body, connection)
    except Exception:  # noqa: BLE001 - malformed mail must not crash the hook
        log.info("email malformed %s", mid)
        return 400
    if incoming is None:
        outcome = "filtered as automated (auto-reply, bounce, or bulk)"
    else:
        incoming.data["raw"] = inbox.store_raw(db, body)  # rendered by the console (decision #106)
        stored = inbox.ingest(db, incoming) is not None
        outcome = "stored" if stored else "duplicate (already stored, or an echo of a sent reply)"
        if stored and connection["platform"] == "gmail":
            activity = db.execute("SELECT id FROM activities WHERE kind = 'MESSAGE' AND native_id = ?",
                                  (incoming.native_id,)).fetchone()
            store_parts(db, activity["id"], body, incoming.data["raw"])
    log.info("email %s %s", outcome, mid)
    inbox.set_connection_error(db, connection_id, None)
    return 204


def handle_resend_hook(db, connection_id: int, body: bytes) -> tuple[int, bool]:
    """Handle an already authenticated receipt; persist only its provider and SMTP identities."""
    try:
        event = json.loads(body)
    except (ValueError, RecursionError):
        return 400, False
    if not isinstance(event, dict) or not isinstance(event.get("type"), str) or not event["type"]:
        return 400, False
    if event["type"] != "email.sent":
        return 204, False
    data = event.get("data")
    if not isinstance(data, dict):
        return 400, False
    try:
        inserted = inbox.record_resend_receipt(db, connection_id, data.get("email_id"), data.get("message_id"))
    except inbox.ResendReceiptConflict:
        return 409, False
    except ValueError:
        return 400, False
    return 204, inserted
