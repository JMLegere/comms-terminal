"""Quo (formerly OpenPhone) SMS Connection (.agents/discovery/2026-10-12-sms.md).

Texts to and from the Work number arrive through /hooks/quo as signed webhooks (Standard Webhooks signing,
Quo-Api-Version 2026-03-30): message.received for incoming Texts, message.delivered for Texts Jeremy sends,
from the Quo app or the console. Each 1:1 conversation is a Channel threaded like a Slack DM. Group texts
are out of scope: Quo fires no event for a group Text sent from the app (V2), so they are dropped.
Replies go out through Quo's send API; the delivered event for a console reply carries the same message ID
and is dropped by ingest as a duplicate.

Calls (.agents/discovery/2026-10-15-phone-calls.md): call.completed becomes a Phone call, stored as a MESSAGE
Activity whose data carries `call` (direction, outcome, length, and Quo's answered_at and completed_at when
present); its content is the one-line summary. A Call
transcript (call.transcript.completed, or call.voicemail.completed's text) is an EDIT Activity targeting the
call, from Jeremy so it never moves Status; thread_view shows it under the summary.
"""

import base64
import hashlib
import hmac
import json
import urllib.error
import urllib.request
from datetime import datetime

from . import inbox

SIGNATURE_WINDOW = 300  # seconds
EVENTS = {"message.received", "message.delivered"}
TRANSCRIPTS = {"call.transcript.completed": "transcript", "call.voicemail.completed": "voicemail"}
SEND_URL = "https://api.openphone.com/v1/messages"


def verify(headers, body: bytes, key: bytes, now: datetime) -> bool:
    """Standard Webhooks: base64 HMAC-SHA256 over "<webhook-id>.<webhook-timestamp>.<body>" with the
    base64-decoded `whsec_` key; webhook-signature holds space-separated "v1,<sig>" entries."""
    wid, ts, sigs = (headers.get(h, "") for h in ("webhook-id", "webhook-timestamp", "webhook-signature"))
    if not wid or not ts.isdigit() or abs(now.timestamp() - int(ts)) > SIGNATURE_WINDOW:
        return False
    secret = base64.b64decode(key.removeprefix(b"whsec_"))
    want = base64.b64encode(hmac.new(secret, f"{wid}.{ts}.".encode() + body, hashlib.sha256).digest()).decode()
    return any(hmac.compare_digest(want, s.partition(",")[2]) for s in sigs.split() if s.startswith("v1,"))


def _length(seconds: int) -> str:
    return f"{seconds // 60}m {seconds % 60}s" if seconds >= 60 else f"{seconds}s"


def call_summary(call: dict) -> str:
    """The Phone call's one line, also the title of a Thread it starts: "Missed call", "Phone call, 4m 12s"."""
    outcome, length = call["outcome"], call["length"]
    if outcome == "missed":
        return "Missed call"
    if outcome == "voicemail":
        return f"Voicemail, {_length(length)}"
    if outcome == "no answer":
        return "Outgoing call, no answer"
    return f"{call['direction'].capitalize()} call, {_length(length)}"


def _peer(context) -> str | None:
    """The one other party on a call; None for conference calls and unresolved participants."""
    external = (context.get("participants") or {}).get("external") or []
    return external[0] if len(external) == 1 else None


def _call(event: dict, connection) -> inbox.Incoming | None:
    r, c = event["data"]["resource"], event["data"]["context"]
    peer = _peer(c)
    if peer is None:
        return None
    incoming, status = r["direction"] == "incoming", r.get("status")
    # Forwarded and AI-handled calls read as missed: Jeremy did not talk (discovery, 2026-10-15).
    # ponytail: an outgoing call that reaches their voicemail may report "answered" (V2); fix once a test call shows it.
    outcome = ("connected" if status == "answered" else "voicemail" if incoming and r.get("hasVoicemail")
               else "missed" if incoming else "no answer")
    call = {"direction": r["direction"], "outcome": outcome,
            "length": int(r.get("duration") or 0) if outcome in ("connected", "voicemail") else 0,
            **{k: r[q] for k, q in (("answered_at", "answeredAt"), ("completed_at", "completedAt")) if r.get(q)}}
    link = (event["data"].get("links") or {}).get("quo")
    summary = call_summary(call)
    return inbox.Incoming(
        connection_id=connection["id"], channel=peer, channel_kind="im", kind="MESSAGE", native_id=r["id"],
        sender=inbox.Identity("phone", peer) if incoming else None,
        occurred_at=datetime.fromisoformat(r["createdAt"].replace("Z", "+00:00")), content=summary, title=summary,
        data={"peer": peer, "conversation": c.get("conversationId"), "call": call, **({"link": link} if link else {})},
        channel_name=peer, synchronous="call")


def _transcript(event: dict, connection) -> inbox.Incoming | None:
    """A Call transcript as an EDIT of its call, from Jeremy so it never changes Status; None when empty."""
    kind, r = TRANSCRIPTS[event["type"]], event["data"]["resource"]
    peer = _peer(event["data"]["context"])
    if kind == "voicemail":
        text = (r.get("transcript") or "").strip()
    else:
        text = "\n".join(f"{'You' if d.get('userId') else 'Them'}: {d.get('content', '').strip()}"
                         for d in (r.get("dialogue") or []) if (d.get("content") or "").strip())
    if not text or not r.get("callId") or peer is None:
        return None
    return inbox.Incoming(
        connection_id=connection["id"], channel=peer, channel_kind="im", kind="EDIT",
        native_id=f"{r['callId']}:{kind}", sender=None,
        occurred_at=datetime.fromisoformat(r["createdAt"].replace("Z", "+00:00")), content=text,
        data={"peer": peer, "transcript": kind})


def parse(event: dict, connection) -> inbox.Incoming | None:
    """One Quo event -> Incoming, or None for other events and group Texts."""
    if event.get("type") == "call.completed":
        return _call(event, connection)
    if event.get("type") in TRANSCRIPTS:
        return _transcript(event, connection)
    if event.get("type") not in EVENTS:
        return None
    r, c = event["data"]["resource"], event["data"]["context"]
    others = {n for n in [c.get("senderIdentifier"), *c.get("recipientIdentifiers", [])]
              if n and n != connection["external_id"]}
    if len(others) != 1:
        return None
    peer = others.pop()
    text = r.get("text") or ""
    media = [[m.get("type") or "", m["url"]] for m in r.get("media") or []]
    link = (event["data"].get("links") or {}).get("quo")
    return inbox.Incoming(
        connection_id=connection["id"], channel=peer, channel_kind="im", kind="MESSAGE", native_id=r["id"],
        sender=inbox.Identity("phone", peer) if r["direction"] == "incoming" else None,
        occurred_at=datetime.fromisoformat(r["createdAt"].replace("Z", "+00:00")), content=text,
        title=(next((ln.strip() for ln in text.splitlines() if ln.strip()), "") or ("Photo" if media else "Text"))[:80],
        data={"peer": peer, "conversation": c.get("conversationId"),
              **({"media": media, "attachments": [["photo", t, 0] for t, _ in media]} if media else {}),
              **({"link": link} if link else {})},
        channel_name=peer)


def handle_hook(db, connection, headers, body: bytes, key: bytes, now: datetime) -> int:
    """POST /hooks/quo (through serve.intake) -> HTTP status. Quo retries any non-2xx, and ingest drops repeats
    by message ID."""
    if not verify(headers, body, key, now):
        return 401
    incoming = parse(json.loads(body), connection)
    if incoming is None:
        return 204
    if incoming.kind == "EDIT" and db.execute(  # a transcript before its call: 503 so Quo delivers it again
            "SELECT 1 FROM activities WHERE kind = 'MESSAGE' AND native_id = ?",
            (incoming.native_id.rsplit(":", 1)[0],)).fetchone() is None:
        return 503
    inbox.ingest(db, incoming)
    return 204


def _post(url, key, payload):
    req = urllib.request.Request(url, json.dumps(payload).encode(),
                                 {"Authorization": key, "Content-Type": "application/json",
                                  "User-Agent": "comms-terminal"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def make_sender(connection, credential, shared):
    key = credential("quo-api-key")
    return QuoSender(key.decode(), connection["external_id"]) if key else None


def _get(url, key):
    req = urllib.request.Request(url, headers={"Authorization": key, "Quo-Api-Version": "2026-03-30",
                                               "User-Agent": "comms-terminal"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def probe(connection, credential, shared, get=_get) -> str | None:
    """Health check (decision #119): Quo accepts the key and still has an enabled webhook to /hooks/quo."""
    key = credential("quo-api-key")
    if key is None:
        return "Quo API key is not installed"
    try:
        hooks = get("https://api.quo.com/webhooks", key.decode())["data"]
    except urllib.error.HTTPError as e:
        return f"Quo refused the key: HTTP {e.code}"
    except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
        return f"Quo unreachable: {e}"
    if not any(h.get("url", "").endswith("/hooks/quo") and h.get("status") == "enabled" for h in hooks):
        return "Quo has no enabled webhook to /hooks/quo"
    return None


class QuoSender:
    """Sends replies from the Work number through Quo's API; Quo files them in the conversation."""

    def __init__(self, key: str, number: str, post=_post):
        self.key, self.number, self.post = key, number, post

    def send(self, thread, text, to, cc, history, files=()) -> str:
        if files:
            raise inbox.SendFailed("SMS can't send Attachments")
        peer = next((h["data"]["peer"] for h in history if "peer" in h["data"]), None)
        if peer is None:
            raise inbox.SendFailed("quo send failed: Thread has no phone number")
        try:
            r = self.post(SEND_URL, self.key, {"content": text, "from": self.number, "to": [peer]})
        except urllib.error.HTTPError as e:
            error = inbox.SendUnknown if e.code >= 500 else inbox.SendFailed
            raise error(f"quo send failed: HTTP {e.code} {e.read()[:200].decode(errors='replace')}") from e
        except urllib.error.URLError as e:  # urllib wraps failures before the request is out (connect, DNS)
            raise inbox.SendFailed(f"quo send failed: {e}") from e
        except (OSError, ValueError) as e:  # the response was lost or unreadable: it may have gone out
            raise inbox.SendUnknown(f"quo send outcome unknown: {e}") from e
        return r["data"]["id"]

    def start(self, to, cc, subject, body, key, files=()) -> str:
        if files:
            raise inbox.SendFailed("SMS can't send Attachments")
        try:
            r = self.post(SEND_URL, self.key, {"content": body, "from": self.number, "to": to})
        except urllib.error.HTTPError as e:
            error = inbox.SendUnknown if e.code >= 500 else inbox.SendFailed
            raise error(f"HTTP {e.code} {e.read()[:200].decode(errors='replace')}") from e
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise inbox.SendUnknown(f"quo send outcome unknown: {e}") from e
        return r["data"]["id"]
