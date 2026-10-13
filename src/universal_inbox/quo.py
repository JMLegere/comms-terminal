"""Quo (formerly OpenPhone) SMS Connection (.agents/discovery/2026-10-12-sms.md).

Texts to and from the Work number arrive through /hooks/quo as signed webhooks (Standard Webhooks signing,
Quo-Api-Version 2026-03-30): message.received for incoming Texts, message.delivered for Texts Jeremy sends,
from the Quo app or the console. Each 1:1 conversation is a Channel threaded like a Slack DM. Group texts
are out of scope: Quo fires no event for a group Text sent from the app (V2), so they are dropped.
Replies go out through Quo's send API; the delivered event for a console reply carries the same message ID
and is dropped by ingest as a duplicate.
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


def parse(event: dict, connection) -> inbox.Incoming | None:
    """One Quo message event -> Incoming, or None for other events and group Texts."""
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


def handle_hook(db, connection_id: int | None, headers, body: bytes, key: bytes, now: datetime) -> int:
    """POST /hooks/quo -> HTTP status. Quo retries any non-2xx, and ingest drops repeats by message ID."""
    if not verify(headers, body, key, now):
        return 401
    if connection_id is None:
        return 503
    connection = db.execute("SELECT * FROM connections WHERE id = ?", (connection_id,)).fetchone()
    incoming = parse(json.loads(body), connection)
    if incoming is not None:
        inbox.ingest(db, incoming)
    return 204


def _post(url, key, payload):
    req = urllib.request.Request(url, json.dumps(payload).encode(),
                                 {"Authorization": key, "Content-Type": "application/json",
                                  "User-Agent": "universal-inbox"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def make_sender(connection, credential, shared):
    key = credential("quo-api-key")
    return QuoSender(key.decode(), connection["external_id"]) if key else None


def _get(url, key):
    req = urllib.request.Request(url, headers={"Authorization": key, "Quo-Api-Version": "2026-03-30",
                                               "User-Agent": "universal-inbox"})
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

    def send(self, thread, text, to, cc, history) -> str:
        peer = next((h["data"]["peer"] for h in history if "peer" in h["data"]), None)
        if peer is None:
            raise inbox.SendFailed("quo send failed: Thread has no phone number")
        try:
            r = self.post(SEND_URL, self.key, {"content": text, "from": self.number, "to": [peer]})
        except urllib.error.HTTPError as e:
            raise inbox.SendFailed(f"quo send failed: HTTP {e.code} {e.read()[:200].decode(errors='replace')}") from e
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise inbox.SendFailed(f"quo send failed: {e}") from e
        return r["data"]["id"]
