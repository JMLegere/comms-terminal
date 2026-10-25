"""Slack adapter (Plain-style, ADR 0002): Events API in through /hooks/slack, replies out as Jeremy."""

import hashlib
import hmac
import http.client
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from . import clock, inbox

SIGNATURE_WINDOW = 300  # seconds
CHANNEL_KINDS = {"channel": "channel", "group": "private", "im": "im", "mpim": "mpim"}
MESSAGE_SUBTYPES = {None, "thread_broadcast", "file_share"}
REACTION_NAMES = {"+1", "pray", "eyes"}
# Only documented rejections establish no change; unknown/new source errors stay uncertain.
REACTION_REFUSALS = {
    "access_denied", "accesslimited", "account_inactive", "bad_timestamp", "channel_not_found",
    "deprecated_endpoint", "ekm_access_denied", "enterprise_is_restricted", "external_channel_migrating",
    "invalid_arg_name", "invalid_arguments", "invalid_array_arg", "invalid_auth", "invalid_charset",
    "invalid_form_data", "invalid_name", "invalid_post_type", "is_archived", "message_not_found",
    "method_deprecated", "missing_post_type", "missing_scope", "no_access", "no_item_specified",
    "no_permission", "not_allowed_token_type", "not_authed", "not_reactable", "org_login_required",
    "ratelimited", "team_access_not_granted", "team_added_to_org", "thread_locked", "token_expired",
    "token_revoked", "too_many_emoji", "too_many_reactions", "two_factor_setup_required",
}


class ReactionUnavailable(Exception):
    """A read failed; its safe message never contains provider bodies or transport exception text."""


def _reaction_error(code):
    if code == "missing_scope":
        return "Slack reaction access is missing; reactions:read and reactions:write need approval"
    if code == "ratelimited":
        return "Slack rate limit reached; wait before checking or trying again"
    return f"Slack refused reactions: {code}" if isinstance(code, str) and code in REACTION_REFUSALS else \
        "Slack reactions are unavailable"


def _reaction_http_error(error):
    message = _reaction_error("ratelimited") if error.code == 429 else f"Slack reactions: HTTP {error.code}"
    retry = (error.headers or {}).get("Retry-After", "")
    if isinstance(retry, str) and re.fullmatch(r"[0-9]{1,6}", retry):
        message += f"; wait {int(retry)} seconds"
    return message


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


def _title(text: str | None) -> str | None:
    """A Thread title from Slack text: rendered, first line, without markup (#115)."""
    from .render import plain, slack_markup
    line = next((ln.strip() for ln in plain(slack_markup(text or "")).splitlines() if ln.strip()), "")
    return line[:80] or None


def parse(event: dict, connection, rules, client, thread_member=lambda channel, ts: None) -> inbox.Incoming | None:
    """One Slack message event -> Incoming, or None when not selected or not a message.

    DMs, group DMs, and ruled channels are taken whole (FR-4). From any other channel Jeremy is in, the
    user token also delivers messages (#114); only those that need him are kept: a message mentioning him,
    and a reply in a thread that is already in the inbox or that he has posted in. `thread_member(channel,
    thread_ts)` returns a stored message of that Slack thread, if any: an earlier reply, else its parent
    (the hook passes `stored_member`). A reply joins the Thread of an earlier reply; the first reply to a
    top-level message starts its own Thread (FR-4b).
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
        files = [[f.get("name") or f.get("title") or "file", f.get("mimetype") or "", f.get("size") or 0,
                  {"id": f.get("id"), "url": f.get("url_private_download") or f.get("url_private")}]
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
    sender = None if user == self_id else inbox.Identity("slack_user", user, client.real_name(user),
                                                         profile_email=client.user_email(user))
    refs = [member] if member and member != f"{channel}:{thread_ts}" else []
    return inbox.Incoming(
        connection_id=connection["id"], channel=channel, channel_kind=kind, kind=kind_,
        native_id=native_id, sender=sender, occurred_at=_at(event.get("event_ts", ts)),
        content=client.name_mentions(content) if content else content, thread_ref=refs,
        title=_title(content) if kind_ == "MESSAGE" else None,
        data={"channel": channel, "ts": ts, "kind": kind, **({"thread_ts": thread_ts} if thread_ts else {}),
              **({"attachments": files} if files else {})},
        channel_name=client.conversation_label(channel, kind))


USER_AGENT = "comms-terminal/0.1 (+https://github.com/JMLegere/comms-terminal)"


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


def _upload(url, data: bytes) -> int:
    """POST a file's bytes to a files.getUploadURLExternal upload_url."""
    req = urllib.request.Request(url, data, {"Content-Type": "application/octet-stream", "User-Agent": USER_AGENT},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.status


class SlackClient:
    """Web API reads (form/GET methods) with Jeremy's user token; cached for the process lifetime."""

    def __init__(self, token: str, get=_get):
        self.token, self.get, self._names, self._users, self._threads = token, get, {}, {}, {}

    def _reaction_query(self, method, params):
        try:
            result = self.get(f"https://slack.com/api/{method}", self.token, params)
        except urllib.error.HTTPError as error:
            raise ReactionUnavailable(_reaction_http_error(error)) from None
        except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException):
            raise ReactionUnavailable("Slack reaction read failed; check the source before trying again") from None
        if not isinstance(result, dict) or result.get("ok") is not True:
            code = result.get("error") if isinstance(result, dict) else None
            raise ReactionUnavailable(_reaction_error(code))
        return result

    def identity(self) -> dict:
        """Authenticated user-token identity, checked afresh; no Connection or credential cache."""
        result = self._reaction_query("auth.test", {})
        team, user = result.get("team_id"), result.get("user_id")
        if (not isinstance(team, str) or not re.fullmatch(r"T[A-Z0-9]+", team)
                or not isinstance(user, str) or not re.fullmatch(r"[UW][A-Z0-9]+", user)
                or result.get("bot_id")):
            raise ReactionUnavailable("Slack user identity could not be verified")
        return {"team_id": team, "user_id": user}

    def reaction_snapshot(self, channel, ts, self_id) -> list[dict]:
        """All source totals; Slack guarantees the authenticated user is in each partial users list if present."""
        result = self._reaction_query("reactions.get", {"channel": channel, "timestamp": ts})
        message = result.get("message")
        if (result.get("type") != "message" or result.get("channel") != channel
                or not isinstance(message, dict) or message.get("type") != "message" or message.get("ts") != ts):
            raise ReactionUnavailable("Slack returned an invalid reaction message")
        # Slack's message object documents reactions as optional when no reactions have been added.
        reactions = message.get("reactions", [])
        if not isinstance(reactions, list):
            raise ReactionUnavailable("Slack returned invalid reaction groups")
        groups, names = [], set()
        for reaction in reactions:
            if not isinstance(reaction, dict):
                raise ReactionUnavailable("Slack returned an invalid reaction group")
            name, count, users = (reaction.get(k) for k in ("name", "count", "users"))
            if (not isinstance(name, str) or not name or len(name) > 100 or not name.isprintable()
                    or type(count) is not int or count <= 0 or not isinstance(users, list)
                    or any(not isinstance(user, str) or not re.fullmatch(r"[UW][A-Z0-9]+", user) for user in users)
                    or len(set(users)) != len(users) or count < len(users)):
                raise ReactionUnavailable("Slack returned an invalid reaction group")
            name = "+1" if name == "thumbsup" else name
            if name in names:
                raise ReactionUnavailable("Slack returned duplicate reaction groups")
            names.add(name)
            groups.append({"name": name, "count": count, "mine": self_id in users})
        return groups

    def _info(self, channel: str) -> dict:
        if channel not in self._names:
            r = self.get("https://slack.com/api/conversations.info", self.token, {"channel": channel})
            self._names[channel] = r.get("channel", {}) if r.get("ok") else {}
        return self._names[channel]

    def conversation_name(self, channel: str) -> str | None:
        return self._info(channel).get("name") or None

    def conversation_label(self, channel: str, kind: str) -> str | None:
        """A friendly name: the channel's name, the other person in a DM, or the people in a group DM."""
        info = self._info(channel)
        if kind == "im":
            return self.user_name(info["user"]) if info.get("user") else None
        if kind == "mpim":  # Slack names these "mpdm-ana--bo--jeremy-1"
            name = info.get("name") or ""
            people = name.removeprefix("mpdm-").rsplit("-", 1)[0].split("--") if name.startswith("mpdm-") else []
            return ", ".join(people) or info.get("purpose", {}).get("value") or None
        return info.get("name") or None

    def _profile(self, user: str) -> dict:
        if user not in self._users:
            r = self.get("https://slack.com/api/users.info", self.token, {"user": user})
            self._users[user] = r.get("user", {}) if r.get("ok") else {}
        return self._users[user]

    def user_email(self, user: str) -> str | None:
        return self._profile(user).get("profile", {}).get("email")

    def real_name(self, user: str) -> str | None:
        """First and last name, for the Person (Jeremy, 2026-10-12); the handle only when Slack has none."""
        u = self._profile(user)
        p = u.get("profile", {})
        full = " ".join(x for x in (p.get("first_name"), p.get("last_name")) if x)
        return p.get("real_name_normalized") or p.get("real_name") or full or self.user_name(user)

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


KIND_OF = {"is_im": "im", "is_mpim": "mpim", "is_private": "group"}


def refresh_names(db, client) -> int:
    """Friendly names stored before names were kept (run at service start): Slack Channels, and Persons
    still named by their Slack user ID."""
    for r in db.execute("SELECT p.id, i.value FROM persons p JOIN identities i ON i.person_id = p.id"
                        " WHERE i.kind = 'slack_user' AND p.display_name = i.value").fetchall():
        name = client.real_name(r["value"])
        if name and name != r["value"]:
            with db:
                db.execute("UPDATE persons SET display_name = ? WHERE id = ?", (name, r["id"]))
    rows = db.execute("SELECT ch.id, ch.native_id, ch.kind FROM channels ch JOIN connections c ON c.id = ch.connection_id"
                      " WHERE c.platform = 'slack' AND ch.name IS NULL").fetchall()
    done = 0
    for r in rows:
        name = client.conversation_label(r["native_id"], r["kind"])
        if name:
            with db:
                db.execute("UPDATE channels SET name = ? WHERE id = ?", (name, r["id"]))
            done += 1
    return done


def conversations(client) -> list[dict]:
    """Every conversation Jeremy is a member of: channels, private channels, DMs, and group DMs."""
    out, cursor = [], ""
    while True:
        r = client.get("https://slack.com/api/users.conversations", client.token, {
            "types": "public_channel,private_channel,im,mpim", "exclude_archived": "true", "limit": 200,
            **({"cursor": cursor} if cursor else {})})
        if not r.get("ok"):
            raise RuntimeError(f"users.conversations failed: {r.get('error')}")
        out += r.get("channels", [])
        cursor = r.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            return out


def _pages(client, method, params):
    cursor = ""
    while True:
        r = client.get(f"https://slack.com/api/{method}", client.token, {**params, "limit": 200,
                                                                          **({"cursor": cursor} if cursor else {})})
        if not r.get("ok"):
            raise RuntimeError(f"{method} failed: {r.get('error')}")
        yield from r.get("messages", [])
        cursor = r.get("response_metadata", {}).get("next_cursor")
        if not cursor or not r.get("has_more", True):
            return


def history_events(client, since: datetime):
    """Message events for the last stretch of history, as the Events API would have sent them."""
    oldest = f"{since.timestamp():.6f}"
    for c in conversations(client):
        kind = next((k for flag, k in KIND_OF.items() if c.get(flag)), "channel")
        for m in _pages(client, "conversations.history", {"channel": c["id"], "oldest": oldest}):
            yield c["id"], kind, m
            if m.get("reply_count") and m.get("thread_ts") == m.get("ts"):
                for r in _pages(client, "conversations.replies", {"channel": c["id"], "ts": m["ts"], "oldest": oldest}):
                    if r.get("ts") != m["ts"]:
                        yield c["id"], kind, r


def stored_member(db, channel, thread_ts) -> str | None:
    """A stored message of this Slack thread: an earlier reply first, else the parent message."""
    parent = f"{channel}:{thread_ts}"
    r = db.execute("SELECT native_id FROM activities WHERE kind = 'MESSAGE' AND (native_id = ? OR"
                   " (json_extract(data, '$.channel') = ? AND json_extract(data, '$.thread_ts') = ?))"
                   " ORDER BY native_id = ? LIMIT 1", (parent, channel, thread_ts, parent)).fetchone()
    return r["native_id"] if r else None


def import_recent(db, connection, client, days: int) -> int:
    """One-time import (Jeremy, 2026-10-12): the last `days` of Slack through the live filter, oldest first,
    so Threads form as they would have live; then every touched Thread goes to Todo, as Jeremy asked.
    Returns the number of messages stored."""
    since = clock.now().timestamp() - days * 86400
    rules = [(r["kind"], r["value"]) for r in db.execute(
        "SELECT kind, value FROM channel_rules WHERE connection_id = ?", (connection["id"],))]
    events = sorted(({"type": "message", "channel": ch, "channel_type": kind, "user": m.get("user"),
                      "text": m.get("text"), "ts": m["ts"], "event_ts": m["ts"], "subtype": m.get("subtype"),
                      "files": m.get("files"), **({"thread_ts": m["thread_ts"]} if m.get("thread_ts") else {})}
                     for ch, kind, m in history_events(client, datetime.fromtimestamp(since, timezone.utc))),
                    key=lambda e: float(e["ts"]))

    stored, threads = 0, set()
    for e in events:
        inc = parse(e, connection, rules, client, lambda c, t: stored_member(db, c, t))
        t = inbox.ingest(db, inc) if inc is not None else None
        if t is not None:
            stored += 1
            threads.add(t.id)
    for tid in threads:
        inbox.reopen_imported(db, tid)
    return stored


def make_sender(connection, credential, shared):
    token = credential("slack-user-token")
    return SlackSender(token.decode(), client=SlackClient(token.decode())) if token else None


def probe(connection, credential, shared, get=_get) -> str | None:
    """Health check (decision #119): auth.test accepts Jeremy's user token. Event delivery cannot be checked."""
    token = credential("slack-user-token")
    if token is None:
        return "Slack user token is not installed"
    try:
        r = get("https://slack.com/api/auth.test", token.decode(), {})
    except (urllib.error.URLError, OSError, ValueError) as e:
        return f"Slack unreachable: {e}"
    return None if r.get("ok") else f"Slack refused the token: {r.get('error', 'unknown error')}"


class SlackSender:
    """Posts replies and Slack Drafts with Jeremy's user token, so they appear under his name (FR-4c)."""

    def __init__(self, token: str, post=_post, client=None, get=_get, upload=_upload, sleep=time.sleep):
        self.token, self.post, self.client, self.get, self.upload, self.sleep = token, post, client, get, upload, sleep

    def change_reaction(self, channel, ts, name, op) -> dict:
        """One explicit own add/remove, never retried; definitive desired state is separate from a new change."""
        if op not in ("add", "remove") or not isinstance(name, str) or name not in REACTION_NAMES:
            raise inbox.SendFailed("Choose an explicit supported Slack reaction change")
        try:
            result = self.post(f"https://slack.com/api/reactions.{op}", self.token,
                               {"channel": channel, "timestamp": ts, "name": name})
        except urllib.error.HTTPError as error:
            refused = 400 <= error.code < 500 and error.code != 408
            raise (inbox.SendFailed if refused else inbox.SendUnknown)(_reaction_http_error(error)) from None
        except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException):
            raise inbox.SendUnknown("Slack reaction outcome unknown; check the original source message") from None
        if isinstance(result, dict) and result.get("ok") is True:
            return {"mine": op == "add", "changed": True}
        code = result.get("error") if isinstance(result, dict) and result.get("ok") is False else None
        if isinstance(code, str) and code == ("already_reacted" if op == "add" else "no_reaction"):
            return {"mine": op == "add", "changed": False}
        if isinstance(code, str) and code in REACTION_REFUSALS:
            raise inbox.SendFailed(_reaction_error(code))
        raise inbox.SendUnknown("Slack reaction outcome unknown; check the original source message")

    def start(self, to, cc, subject, body, key, files=()) -> str:
        """A Slack Draft: one top-level message in an existing conversation. Slack has no idempotency key."""
        channel = to[0]
        if files:
            return self._share(channel, body, None, files)
        try:
            r = self.post("https://slack.com/api/chat.postMessage", self.token, {"channel": channel, "text": body})
        except urllib.error.HTTPError as e:
            error = inbox.SendUnknown if e.code >= 500 else inbox.SendFailed
            raise error(f"slack send: HTTP {e.code}") from e
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise inbox.SendUnknown(f"slack send outcome unknown: {e}") from e
        if not r.get("ok"):
            raise inbox.SendFailed(f"slack send failed: {r.get('error', 'unknown')}")
        return f"{channel}:{r['ts']}"

    def conversation(self, channel: str) -> tuple[str, str | None]:
        """(Channel kind, friendly name) for a conversation a Draft was sent to."""
        info = self.client._info(channel)
        kind = CHANNEL_KINDS[next((k for flag, k in KIND_OF.items() if info.get(flag)),
                                  "im" if channel.startswith("D") else "channel")]
        return kind, self.client.conversation_label(channel, kind)

    def conversations(self) -> list[dict]:
        """Every conversation Jeremy is in, as {id, kind, label}, for the Slack Draft picker."""
        found = conversations(self.client)
        if any(c.get("is_im") and c.get("user") not in self.client._users for c in found):
            cursor = ""  # one users.list pass names every DM partner, instead of a users.info call each
            while True:
                r = self.client.get("https://slack.com/api/users.list", self.token,
                                    {"limit": 200, **({"cursor": cursor} if cursor else {})})
                if not r.get("ok"):
                    break
                self.client._users.update((u["id"], u) for u in r.get("members", []))
                cursor = r.get("response_metadata", {}).get("next_cursor")
                if not cursor:
                    break
        out = []
        for c in found:
            if c.get("is_user_deleted"):
                continue
            kind = CHANNEL_KINDS[next((k for flag, k in KIND_OF.items() if c.get(flag)), "channel")]
            self.client._names.setdefault(c["id"], c)
            label = self.client.conversation_label(c["id"], kind) or c["id"]
            out.append({"id": c["id"], "kind": kind, "label": f"#{label}" if kind in ("channel", "private") else label})
        return sorted(out, key=lambda c: c["label"].lstrip("#").casefold())

    def send(self, thread, text, to, cc, history, files=()) -> str:
        channel = next((h["data"]["channel"] for h in history if "channel" in h["data"]), None)
        if channel is None:
            raise inbox.SendFailed("slack send failed: Thread has no Slack channel")
        # In a channel, always answer inside a Slack reply-thread (FR-4c): a Thread made of replies answers in
        # that reply-thread, a Thread of top-level messages under its latest top-level message. A DM or group
        # DM answers top-level unless the Thread is made of replies.
        slack = [h["data"] for h in history if h["data"].get("ts")]
        replies = [d["thread_ts"] for d in slack if d.get("thread_ts") not in (None, d["ts"])]
        dm = any(d.get("kind") in ("im", "mpim") for d in slack)
        thread_ts = replies[-1] if replies else slack[-1]["ts"] if slack and not dm else None
        if files:
            return self._share(channel, text, thread_ts, files)
        payload = {"channel": channel, "text": text, **({"thread_ts": thread_ts} if thread_ts else {})}
        try:
            r = self.post("https://slack.com/api/chat.postMessage", self.token, payload)
        except urllib.error.HTTPError as e:
            error = inbox.SendUnknown if e.code >= 500 else inbox.SendFailed
            raise error(f"slack send: HTTP {e.code}") from e
        except urllib.error.URLError as e:  # urllib wraps failures before the request is out (connect, DNS)
            raise inbox.SendFailed(f"slack send failed: {e}") from e
        except (OSError, ValueError) as e:  # the response was lost or unreadable: it may have gone out
            raise inbox.SendUnknown(f"slack send outcome unknown: {e}") from e
        if not r.get("ok"):
            raise inbox.SendFailed(f"slack send failed: {r.get('error', 'unknown')}")
        return f"{channel}:{r['ts']}"

    def _share(self, channel, text, thread_ts, files) -> str:
        """Text with files as one message (FR-A14): upload each file, share them all with the text as
        initial_comment, then read the message ts from a file's share, which completeUploadExternal leaves
        empty (U3). Returns the `channel:ts` chat.postMessage would, so the echo is a duplicate."""
        ids = []
        try:  # nothing is posted before completeUploadExternal
            for f in files:
                data = f.path.read_bytes()
                r = self.get("https://slack.com/api/files.getUploadURLExternal", self.token,
                             {"filename": f.name, "length": len(data)})
                if not r.get("ok"):
                    raise inbox.SendFailed(f"slack upload failed: {r.get('error', 'unknown')}")
                self.upload(r["upload_url"], data)
                ids.append({"id": r["file_id"], "title": f.name})
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise inbox.SendFailed(f"slack upload failed: {e}") from e
        payload = {"files": ids, "channel_id": channel, "initial_comment": text,
                   **({"thread_ts": thread_ts} if thread_ts else {})}
        try:
            r = self.post("https://slack.com/api/files.completeUploadExternal", self.token, payload)
        except urllib.error.HTTPError as e:
            error = inbox.SendUnknown if e.code >= 500 else inbox.SendFailed
            raise error(f"slack send: HTTP {e.code}") from e
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise inbox.SendUnknown(f"slack send outcome unknown: {e}") from e
        if not r.get("ok"):
            raise inbox.SendFailed(f"slack send failed: {r.get('error', 'unknown')}")
        for _ in range(10):  # the share appeared after about 1.7 s in the P0.3 spike
            self.sleep(1)
            try:
                info = self.get("https://slack.com/api/files.info", self.token, {"file": ids[0]["id"]})
            except (urllib.error.URLError, OSError, ValueError):
                continue
            shares = info.get("file", {}).get("shares", {})
            ts = next((s["ts"] for kind in ("private", "public") for s in shares.get(kind, {}).get(channel) or []), None)
            if ts:
                return f"{channel}:{ts}"
        raise inbox.SendUnknown("slack send outcome unknown: the shared files' message was not found")


def handle_hook(db, connection, headers, body: bytes, signing_secret: bytes, now: datetime,
                client) -> tuple[int, str]:
    """POST /hooks/slack (through serve.intake) -> (HTTP status, response body)."""
    if not verify(headers, body, signing_secret, now):
        return 401, ""
    payload = json.loads(body)
    if payload.get("type") == "url_verification":
        return 200, payload["challenge"]
    event = payload.get("event", {})
    if event.get("type") in ("tokens_revoked", "app_uninstalled"):  # NFR-2, AS-8
        inbox.set_auth(db, connection["id"], False)
        return 200, ""
    rules = [(r["kind"], r["value"]) for r in db.execute(
        "SELECT kind, value FROM channel_rules WHERE connection_id = ?", (connection["id"],))]
    incoming = parse(event, connection, rules, client, lambda c, t: stored_member(db, c, t))
    if incoming is not None:
        inbox.ingest(db, incoming)
    return 200, ""
