"""Slack adapter (Plain-style, ADR 0002): Events API in through /hooks/slack, replies out as Jeremy."""

import hashlib
import hmac
import json
import re
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


def parse(event: dict, connection, rules, client, thread_member=lambda channel, ts: None) -> inbox.Incoming | None:
    """One Slack message event -> Incoming, or None when not selected or not a message.

    DMs, group DMs, and ruled channels are taken whole (FR-4). From any other channel Jeremy is in, the
    user token also delivers messages (#114); only those that need him are kept: a message mentioning him,
    and a reply in a thread that is already in the inbox or that he has posted in. `thread_member(channel,
    thread_ts)` returns a stored message of that Slack thread, if any (the hook passes a DB lookup).
    """
    if event.get("type") != "message":
        return None
    channel = event["channel"]
    kind = CHANNEL_KINDS.get(event.get("channel_type"), "channel")
    ruled = selected(None if kind in ("im", "mpim") else client.conversation_name(channel), kind, rules)
    subtype = event.get("subtype")
    files = []
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
        files = [[f.get("name") or f.get("title") or "file", f.get("mimetype") or "", f.get("size") or 0]
                 for f in event.get("files") or []]
    else:
        return None
    self_id = connection["self_id"]
    reply = bool(thread_ts and thread_ts != ts)
    member = thread_member(channel, thread_ts) if reply else None
    if not ruled and kind_ == "MESSAGE":  # edits and deletions of untracked messages are dropped by ingest
        mentioned = f"<@{self_id}>" in (content or "")
        if not (mentioned or member or (reply and client.in_thread(channel, thread_ts, self_id))):
            return None
    sender = None if user == self_id else inbox.Identity("slack_user", user, profile_email=client.user_email(user))
    refs = [f"{channel}:{thread_ts}"] + ([member] if member else []) if reply else []
    return inbox.Incoming(
        connection_id=connection["id"], channel=channel, channel_kind=kind, kind=kind_,
        native_id=native_id, sender=sender, occurred_at=_at(event.get("event_ts", ts)),
        content=client.name_mentions(content) if content else content, thread_ref=refs,
        data={"channel": channel, "ts": ts, **({"thread_ts": thread_ts} if thread_ts else {}),
              **({"attachments": files} if files else {})})


USER_AGENT = "universal-inbox/0.1 (+https://github.com/JMLegere/universal-inbox)"


def _post(url, token, payload):
    req = urllib.request.Request(url, json.dumps(payload).encode(),
                                 {"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT,
                                  "Content-Type": "application/json; charset=utf-8"}, method="POST")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def _get(url, token, params):
    req = urllib.request.Request(f"{url}?{urllib.parse.urlencode(params)}",
                                 headers={"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


class SlackClient:
    """Web API reads (form/GET methods) with Jeremy's user token; cached for the process lifetime."""

    def __init__(self, token: str, get=_get):
        self.token, self.get, self._names, self._users, self._threads = token, get, {}, {}, {}

    def conversation_name(self, channel: str) -> str | None:
        if channel not in self._names:
            r = self.get("https://slack.com/api/conversations.info", self.token, {"channel": channel})
            self._names[channel] = r.get("channel", {}).get("name") if r.get("ok") else None
        return self._names[channel]

    def _profile(self, user: str) -> dict:
        if user not in self._users:
            r = self.get("https://slack.com/api/users.info", self.token, {"user": user})
            self._users[user] = r.get("user", {}) if r.get("ok") else {}
        return self._users[user]

    def user_email(self, user: str) -> str | None:
        return self._profile(user).get("profile", {}).get("email")

    def user_name(self, user: str) -> str | None:
        u = self._profile(user)
        p = u.get("profile", {})
        return p.get("display_name") or p.get("real_name") or u.get("name")

    def name_mentions(self, text: str) -> str:
        """<@U123> -> <@U123|Ana>, so the console can show names without asking Slack (#114)."""
        return re.sub(r"<@([UW][A-Z0-9]+)>",
                      lambda m: f"<@{m.group(1)}|{self.user_name(m.group(1)) or m.group(1)}>", text)

    def in_thread(self, channel: str, thread_ts: str, user: str) -> bool:
        """Has `user` posted in this Slack thread? Cached once true (a thread never loses a poster)."""
        key = (channel, thread_ts)
        if not self._threads.get(key):
            r = self.get("https://slack.com/api/conversations.replies", self.token,
                         {"channel": channel, "ts": thread_ts, "limit": 200})
            self._threads[key] = r.get("ok", False) and any(m.get("user") == user for m in r.get("messages", []))
        return self._threads[key]


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
    def thread_member(channel, thread_ts):
        r = db.execute("SELECT native_id FROM activities WHERE kind = 'MESSAGE' AND (native_id = ? OR"
                       " (json_extract(data, '$.channel') = ? AND json_extract(data, '$.thread_ts') = ?)) LIMIT 1",
                       (f"{channel}:{thread_ts}", channel, thread_ts)).fetchone()
        return r["native_id"] if r else None

    incoming = parse(event, connection, rules, client, thread_member)
    if incoming is not None:
        inbox.ingest(db, incoming)
    return 200, ""
