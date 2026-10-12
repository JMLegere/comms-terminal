"""Slack adapter (Plain-style, ADR 0002): Events API in through /hooks/slack, replies out as Jeremy."""

import hashlib
import hmac
import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from . import inbox

SIGNATURE_WINDOW = 300  # seconds
CHANNEL_KINDS = {"channel": "channel", "group": "private", "im": "im", "mpim": "mpim"}
MESSAGE_SUBTYPES = {None, "thread_broadcast", "file_share"}


def verify(headers, body: bytes, signing_secret: bytes, now: datetime) -> bool:
    """Slack v0 signature over "v0:<timestamp>:<body>"."""
    ts, sig = headers.get("X-Slack-Request-Timestamp", ""), headers.get("X-Slack-Signature", "")
    if not ts.isdigit() or abs(now.timestamp() - int(ts)) > SIGNATURE_WINDOW:
        return False
    want = "v0=" + hmac.new(signing_secret, f"v0:{ts}:".encode() + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(want, sig)


def selected(name: str | None, kind: str, rules: list[tuple[str, str]]) -> bool:
    """FR-4 / FR-4a: DMs and group DMs always; channels by name, prefix, or suffix rule."""
    if kind in ("im", "mpim"):
        return True
    name = name or ""
    return any((k == "channel" and name == v) or (k == "prefix" and name.startswith(v))
               or (k == "suffix" and name.endswith(v)) for k, v in rules)


def _at(ts: str) -> datetime:
    return datetime.fromtimestamp(float(ts), timezone.utc)


def parse(event: dict, connection, rules, client) -> inbox.Incoming | None:
    """One Slack message event -> Incoming, or None when not selected or not a message."""
    if event.get("type") != "message":
        return None
    channel = event["channel"]
    kind = CHANNEL_KINDS.get(event.get("channel_type"), "channel")
    if not selected(None if kind in ("im", "mpim") else client.conversation_name(channel), kind, rules):
        return None
    subtype = event.get("subtype")
    if subtype == "message_changed":
        msg = event["message"]
        user, ts = msg.get("user"), msg["ts"]
        native_id, kind_, content = f"{channel}:{ts}:{msg.get('edited', {}).get('ts', event['ts'])}", "EDIT", msg.get("text")
        thread_ts = msg.get("thread_ts")
    elif subtype == "message_deleted":
        user, ts = event.get("previous_message", {}).get("user"), event["deleted_ts"]
        native_id, kind_, content, thread_ts = f"{channel}:{ts}", "DELETE", None, None
        ts = event["ts"]
    elif subtype in MESSAGE_SUBTYPES:
        user, ts, thread_ts = event.get("user"), event["ts"], event.get("thread_ts")
        native_id, kind_, content = f"{channel}:{ts}", "MESSAGE", event.get("text")
    else:
        return None
    if user == connection["self_id"]:
        sender = None
    else:
        sender = inbox.Identity("slack_user", user, profile_email=client.user_email(user))
    refs = [f"{channel}:{thread_ts}"] if thread_ts and thread_ts != ts else []
    return inbox.Incoming(
        connection_id=connection["id"], channel=channel, channel_kind=kind, kind=kind_,
        native_id=native_id, sender=sender, occurred_at=_at(event.get("event_ts", ts)),
        content=content, thread_ref=refs,
        data={"channel": channel, "ts": ts, **({"thread_ts": thread_ts} if thread_ts else {})})


def _post(url, token, payload):
    req = urllib.request.Request(url, json.dumps(payload).encode(),
                                 {"Authorization": f"Bearer {token}",
                                  "Content-Type": "application/json; charset=utf-8"}, method="POST")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def _get(url, token, params):
    req = urllib.request.Request(f"{url}?{urllib.parse.urlencode(params)}",
                                 headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


class SlackClient:
    """Web API reads (form/GET methods) with Jeremy's user token; cached for the process lifetime."""

    def __init__(self, token: str, get=_get):
        self.token, self.get, self._names, self._emails = token, get, {}, {}

    def conversation_name(self, channel: str) -> str | None:
        if channel not in self._names:
            r = self.get("https://slack.com/api/conversations.info", self.token, {"channel": channel})
            self._names[channel] = r.get("channel", {}).get("name") if r.get("ok") else None
        return self._names[channel]

    def user_email(self, user: str) -> str | None:
        if user not in self._emails:
            r = self.get("https://slack.com/api/users.info", self.token, {"user": user})
            self._emails[user] = r.get("user", {}).get("profile", {}).get("email") if r.get("ok") else None
        return self._emails[user]


class SlackSender:
    """Posts replies with Jeremy's user token, so they appear under his name (FR-4c)."""

    def __init__(self, token: str, post=_post):
        self.token, self.post = token, post

    def send(self, thread, text, to, cc, history) -> str:
        channel = next((h["data"]["channel"] for h in history if "channel" in h["data"]), None)
        if channel is None:
            raise inbox.SendFailed("slack send failed: Thread has no Slack channel")
        thread_ts = next((h["data"]["thread_ts"] for h in history if h["data"].get("thread_ts")), None)
        payload = {"channel": channel, "text": text, **({"thread_ts": thread_ts} if thread_ts else {})}
        try:
            r = self.post("https://slack.com/api/chat.postMessage", self.token, payload)
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise inbox.SendFailed(f"slack send failed: {e}") from e
        if not r.get("ok"):
            raise inbox.SendFailed(f"slack send failed: {r.get('error', 'unknown')}")
        return f"{channel}:{r['ts']}"


def handle_hook(db, connection_id: int, headers, body: bytes, signing_secret: bytes, now: datetime,
                client) -> tuple[int, str]:
    """POST /hooks/slack -> (HTTP status, response body)."""
    if not verify(headers, body, signing_secret, now):
        return 401, ""
    payload = json.loads(body)
    if payload.get("type") == "url_verification":
        return 200, payload["challenge"]
    event = payload.get("event", {})
    if event.get("type") in ("tokens_revoked", "app_uninstalled"):  # NFR-2, AS-8
        inbox.set_auth(db, connection_id, False)
        return 200, ""
    connection = db.execute("SELECT * FROM connections WHERE id = ?", (connection_id,)).fetchone()
    rules = [(r["kind"], r["value"]) for r in db.execute(
        "SELECT kind, value FROM channel_rules WHERE connection_id = ?", (connection_id,))]
    incoming = parse(event, connection, rules, client)
    if incoming is not None:
        inbox.ingest(db, incoming)
    return 200, ""
