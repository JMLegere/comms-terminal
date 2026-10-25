"""The service: public hooks (/hooks/email, /hooks/slack, /hooks/quo, /hooks/resend), the local reply socket (POST /reply;
/call, /sync and /raw for Comms Terminal's laptop copy, remote.py), wake(),
and a health check of every Connection each 30 minutes.

Credentials come from systemd's $CREDENTIALS_DIRECTORY (LoadCredentialEncrypted=):
email-hook-secret, slack-signing-secret, slack-user-token, resend-api-key, fastmail-oauth, quo-webhook-secret,
quo-api-key, tana-oauth, attio-token, linear-token, resend-webhook-secret, google-calendar-ics.
"""

import base64
import importlib
import json
import os
import re
import sqlite3
import socketserver
import threading
import time
import urllib.parse
from collections import Counter
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import attio, calendar, email, fastmail, files, inbox, linear, mcp, platforms, quo, slack, tana


def credential(name: str) -> bytes | None:
    """A systemd credential, or None when this platform is not connected yet (its unit drop-in is absent)."""
    directory = os.environ.get("CREDENTIALS_DIRECTORY")
    path = Path(directory) / name if directory else None
    return path.read_bytes().strip() if path is not None and path.exists() else None


def fastmail_client(db) -> fastmail.Client | None:
    cred = credential("fastmail-oauth")
    state = Path(db.execute("PRAGMA database_list").fetchone()[2] or ".").parent / "fastmail-oauth.json"
    return fastmail.Client.load(cred, state) if cred else None


def tana_context(db) -> tana.Context | None:
    cred = credential("tana-oauth")
    state = Path(db.execute("PRAGMA database_list").fetchone()[2] or ".").parent / "tana-oauth.json"
    return tana.Context(tana.Client.load(cred, state)) if cred else None


def attio_context(db) -> tana.Context | None:
    token = credential("attio-token")
    return attio.Context(attio.Client(token.decode())) if token else None


def linear_context(db) -> tana.Context | None:
    token = credential("linear-token")
    return linear.Context(linear.Client(token.decode())) if token else None


def _linear_names(db, emails: list[str], domains: list[str]) -> tuple[list[str], list[str]]:
    """Only unambiguous local full names, selected by this page's exact email/domain keys."""
    emails, domains = set(emails), set(domains)
    with inbox._read_snapshot(db):
        people = [(r["id"], " ".join(r["display_name"].split()))
                  for r in db.execute("SELECT id, display_name FROM persons ORDER BY id")]
        organizations = [(r["domain"].strip().casefold(), " ".join(r["name"].split()))
                         for r in db.execute("SELECT domain, name FROM organizations ORDER BY id")]
        person_ids = {r["person_id"] for r in db.execute("SELECT person_id, value FROM identities WHERE kind = 'email'")
                      if r["value"].strip().casefold() in emails}
    counts = Counter(name.casefold() for _, name in people + organizations)

    def eligible(name):
        return (bool(name) and counts[name.casefold()] == 1 and "@" not in name and "://" not in name
                and any(c.isalpha() for c in name) and not re.fullmatch(r"[\w-]+(?:\.[\w-]+)+\.?", name))

    return ([name for pid, name in people if pid in person_ids and len(name.split()) >= 2 and eligible(name)],
            [name for domain, name in organizations if domain in domains and eligible(name)])


def handle_linear(db, body: bytes, context) -> tuple[int, dict]:
    """POST /linear: email/domain arrays only; unique local names are resolved afresh, without writes."""
    try:
        req = json.loads(body)
        if not isinstance(req, dict):
            raise ValueError
        emails, domains = req.get("emails", []), req.get("domains", [])
        if any(not isinstance(values, list) or any(not isinstance(v, str) for v in values)
               for values in (emails, domains)):
            raise ValueError
    except (ValueError, TypeError):
        return 400, {"error": "expected emails and domains arrays of strings"}
    if context is None:
        return 503, {"error": "Linear is not connected"}
    emails = sorted({value.strip().casefold() for value in emails if value.strip()})
    domains = sorted({value.strip().casefold() for value in domains if value.strip()})
    if not emails and not domains:
        return 200, {"issues": []}
    try:
        person_names, organization_names = _linear_names(db, emails, domains)
        return 200, context.get(emails, domains, person_names, organization_names)
    except (linear.LinearError, OSError, ValueError, KeyError, IndexError, TypeError):
        return 502, {"error": "Linear context could not be loaded"}


def handle_context(body: bytes, context: tana.Context | None, name: str = "Tana") -> tuple[int, dict]:
    """POST /tana and /attio: {"emails", "domains"} -> (status, that Context source's answer or {"error"})
    (FR-13a, FR-13b)."""
    if context is None:
        return 503, {"error": f"{name} is not connected"}
    try:
        req = json.loads(body)
        emails, domains = [str(e) for e in req.get("emails") or []], [str(d) for d in req.get("domains") or []]
    except (ValueError, TypeError, AttributeError):
        return 400, {"error": "expected emails and domains"}
    try:
        return 200, context.get(emails, domains)
    except (mcp.McpError, attio.AttioError, OSError, ValueError, KeyError, IndexError) as e:
        return 502, {"error": str(e) or type(e).__name__}


def handle_attio_people(body: bytes, context: tana.Context | None) -> tuple[int, dict]:
    """POST /attio-people: {"days"} -> Attio People touched in that many days, for `comms-terminal attio-import`."""
    if context is None:
        return 503, {"error": "Attio is not connected"}
    try:
        days = int(json.loads(body)["days"])
    except (ValueError, KeyError, TypeError):
        return 400, {"error": "expected days"}
    try:
        return 200, {"people": attio.recent_people(context.client, datetime.now(timezone.utc) - timedelta(days=days))}
    except (attio.AttioError, OSError, ValueError, KeyError, IndexError) as e:
        return 502, {"error": str(e) or type(e).__name__}


class Senders:
    """Reply senders per Connection, made by its Platform's adapter on first use: a Connection added while the
    service runs can send without a restart. `shared` holds process-wide clients (Fastmail's)."""

    def __init__(self, shared: dict | None = None, made: dict | None = None):
        self.shared, self.made = shared or {}, dict(made or {})
        self._reaction_lock, self._reaction_pending = threading.Lock(), set()

    def get(self, connection):
        if connection["id"] not in self.made:
            adapter = importlib.import_module(f".{platforms.PLATFORMS[connection['platform']].adapter}", __package__)
            sender = adapter.make_sender(connection, credential, self.shared)
            if sender is None:
                return None
            self.made[connection["id"]] = sender
        return self.made[connection["id"]]


def handle_reply(db, body: bytes, senders: Senders) -> tuple[int, dict]:
    """POST /reply: {"thread_id", "text", "to", "cc"} -> (status, JSON body); 502 refused, 504 outcome unknown
    (nothing recorded either way). The Thread's staged Attachments go with it."""
    try:
        req = json.loads(body)
        thread_id, text = int(req["thread_id"]), str(req["text"])
    except (ValueError, KeyError, TypeError):
        return 400, {"error": "expected thread_id and text"}
    r = db.execute("SELECT c.* FROM threads t JOIN channels ch ON ch.id = t.channel_id"
                   " JOIN connections c ON c.id = ch.connection_id WHERE t.id = ?", (thread_id,)).fetchone()
    if r is None:
        return 404, {"error": "unknown thread"}
    sender = senders.get(r)
    if sender is None:
        return 503, {"error": f"{r['label'] or r['platform']} is not connected for sending"}
    try:
        t = inbox.reply(db, thread_id, text, sender, req.get("to") or [], req.get("cc") or [])
    except inbox.SendUnknown as e:
        return 504, {"error": str(e)}
    except inbox.SendFailed as e:
        return 502, {"error": str(e)}
    except ValueError as e:  # pre-send Attachment or pending email identity problem
        return 400, {"error": str(e)}
    return 200, {"status": t.status, "status_detail": t.status_detail}


def handle_send_draft(db, body: bytes, senders: Senders) -> tuple[int, dict]:
    """POST /send-draft: {"draft_id", "revision", "again"} -> 200 {thread_id}; 502 refused (Draft editable);
    504 outcome unknown (Draft locked)."""
    try:
        req = json.loads(body)
        draft_id, revision, again = int(req["draft_id"]), int(req["revision"]), bool(req.get("again"))
    except (ValueError, KeyError, TypeError):
        return 400, {"error": "expected draft_id and revision"}
    try:
        d = inbox.get_draft(db, draft_id)
    except (KeyError, ValueError):
        return 404, {"error": "unknown draft"}
    r = db.execute("SELECT * FROM connections WHERE id = ?", (d["connection_id"],)).fetchone()
    if r is None:
        return 400, {"error": "Choose a Connection"}
    sender = senders.get(r)
    if sender is None:
        return 503, {"error": f"{r['label'] or r['platform']} is not connected for sending"}
    try:
        t = inbox.send_draft(db, draft_id, revision, sender, again=again)
    except inbox.DraftConflict as e:
        return 409, {"error": str(e)}
    except inbox.SendUnknown as e:
        return 504, {"error": str(e)}
    except inbox.SendFailed as e:
        return 502, {"error": str(e)}
    except ValueError as e:
        return 400, {"error": str(e)}
    return 200, {"thread_id": t.id}


def send_due(db, senders: Senders, now: datetime) -> int:
    """Send each Draft whose send-later time has come: a reply Draft into its Thread, any other as a new one.
    Its time is cleared first, so a refused send, or one whose outcome is unknown, is never repeated; the
    reason stays on the Draft as send_error. Returns how many went out."""
    import logging
    sent = 0
    split = lambda s: [a.strip() for a in s.split(",") if a.strip()]  # noqa: E731
    for d in inbox.claim_due_drafts(db, now):
        try:
            r = db.execute("SELECT * FROM connections WHERE id = ?", (d["connection_id"],)).fetchone()
            sender = senders.get(r) if r else None
            if sender is None:
                name = (r["label"] or r["platform"]) if r else "No Connection"
                raise inbox.SendFailed(f"{name} is not connected for sending")
            if d["thread_id"] is None:
                inbox.send_draft(db, d["id"], inbox.get_draft(db, d["id"])["revision"], sender)
            else:
                email_reply = d["kind"] == "email"
                inbox.reply(db, d["thread_id"], d["body"], sender, split(d["to"]) if email_reply else [],
                            split(d["cc"]) if email_reply else [])
            sent += 1
        except Exception as e:  # noqa: BLE001 - one failed Draft must not stop the others
            inbox.set_send_error(db, d["id"], str(e) or type(e).__name__)
            logging.getLogger("comms_terminal.send_later").info("scheduled send of draft %s failed: %s", d["id"], e)
    return sent


def handle_slack_conversations(db, body: bytes, senders: Senders) -> tuple[int, dict]:
    """POST /slack-conversations: {"connection_id"} -> {"conversations": [{id, kind, label}]} for the Slack Draft picker."""
    try:
        connection_id = int(json.loads(body)["connection_id"])
    except (ValueError, KeyError, TypeError):
        return 400, {"error": "expected connection_id"}
    r = db.execute("SELECT * FROM connections WHERE id = ? AND platform = 'slack'", (connection_id,)).fetchone()
    sender = senders.get(r) if r else None
    if sender is None:
        return 503, {"error": "Slack is not connected"}
    try:
        return 200, {"conversations": sender.conversations()}
    except (RuntimeError, OSError, ValueError) as e:
        return 502, {"error": str(e)}


def _slack_reaction_target(db, req):
    """Resolve only this captured original Message; never choose a default, parent, or newest message."""
    view = inbox.thread_view(db, req["thread_id"])
    ch = view["channel"]
    if ch["platform"] != "slack" or ch["connection_id"] != req["connection_id"]:
        raise ValueError("Slack message does not belong to this Connection")
    message = next((m for m in view["messages"] if m["id"] == req["message_id"]), None)
    if message is None:
        raise ValueError("Captured Slack message is unavailable in this Thread")
    connection = db.execute("SELECT * FROM connections WHERE id = ?", (req["connection_id"],)).fetchone()
    data = message["data"]
    channel, ts = data.get("channel"), data.get("ts")
    if (connection is None or not isinstance(channel, str) or not re.fullmatch(r"[CDG][A-Z0-9]+", channel)
            or not isinstance(ts, str) or not re.fullmatch(r"[0-9]+\.[0-9]+", ts)
            or channel != ch["native_id"] or message["native_id"] != f"{channel}:{ts}"
            or ch["kind"] not in ("channel", "private", "im", "mpim")
            or ("kind" in data and data["kind"] != ch["kind"])):
        raise ValueError("Captured Slack source identifiers are unavailable or inconsistent")
    return dict(connection), message, channel, ts


def handle_slack_reactions(db, body: bytes, senders: Senders) -> tuple[int, dict]:
    """Private Slack Reactions: strict captured-target read/add/remove; no ledger or health writes."""
    def unique(pairs):
        result = dict(pairs)
        if len(result) != len(pairs):
            raise ValueError("duplicate fields")
        return result

    def failure(status, outcome, error, target=None):
        return status, {"outcome": outcome, "error": error, **(target or {})}

    try:
        req = json.loads(body, object_pairs_hook=unique)
        if not isinstance(req, dict) or req.get("op") not in ("read", "add", "remove"):
            raise ValueError
        fields = {"op", "thread_id", "message_id", "connection_id"}
        if req["op"] != "read":
            fields |= {"name", "expected_revision"}
        ids = fields - {"op", "name"}
        if (set(req) != fields or any(type(req[k]) is not int or not 0 < req[k] <= 2**63 - 1 for k in ids)
                or (req["op"] != "read" and (not isinstance(req["name"], str)
                                             or req["name"] not in slack.REACTION_NAMES))):
            raise ValueError
    except (ValueError, TypeError):
        return failure(400, "refused", "Expected an explicit Slack reaction operation and positive captured IDs")

    try:
        connection, message, channel, ts = _slack_reaction_target(db, req)
    except (AttributeError, KeyError, ValueError, TypeError):
        return failure(400, "refused", "Captured Slack message, Thread, or Connection is unavailable or mismatched")
    target = {key: req[key] for key in ("thread_id", "message_id", "connection_id")}
    target["revision"] = message["revision"]
    if message["deleted"]:
        return failure(400, "refused", "Slack message was deleted", target)
    if req["op"] != "read" and req["expected_revision"] != message["revision"]:
        return failure(409, "review_required", "Message changed — review", target)
    try:
        sender = senders.get(connection)
        if sender is None:
            return failure(503, "unavailable", "Slack reactions are not connected", target)
        token = sender.token
        client = sender.client or slack.SlackClient(sender.token, get=sender.get)
        if client.token != token:
            return failure(502, "refused", "Slack read and change identities do not match", target)
        identity = client.identity()
    except slack.ReactionUnavailable as error:
        return failure(503, "unavailable", str(error), target)
    except Exception:  # no provider request or exception text is exposed
        return failure(503, "unavailable", "Slack user identity could not be checked", target)
    if identity != {"team_id": connection["external_id"], "user_id": connection["self_id"]}:
        return failure(502, "refused", "Slack user or workspace does not match this Connection", target)

    def recheck():
        try:
            current, msg, current_channel, current_ts = _slack_reaction_target(db, req)
        except (AttributeError, KeyError, ValueError, TypeError):
            return failure(400, "refused", "Original captured Slack target is no longer available", target)
        if sender.token != token or client.token != token:
            return failure(502, "refused", "Original Slack authentication changed; check again", target)
        if ((current["external_id"], current["self_id"], current_channel, current_ts)
                != (connection["external_id"], connection["self_id"], channel, ts)):
            return failure(502, "refused", "Original Slack target or Connection identity changed", target)
        if msg["deleted"]:
            return failure(400, "refused", "Slack message was deleted", {**target, "revision": msg["revision"]})
        if msg["revision"] != message["revision"]:
            return failure(409, "review_required", "Message changed — review", {**target, "revision": msg["revision"]})
        return None

    checked = recheck()  # auth/query latency must not open a known edit/delete/Connection race
    if checked:
        return checked
    if req["op"] == "read":
        try:
            groups = client.reaction_snapshot(channel, ts, connection["self_id"])
        except slack.ReactionUnavailable as error:
            return failure(503, "unavailable", str(error), target)
        except Exception:
            return failure(503, "unavailable", "Slack reactions could not be checked", target)
        checked = recheck()
        return checked if checked else (200, {"outcome": "ready", **target, "groups": groups})
    key = (connection["id"], message["id"])
    with senders._reaction_lock:
        if key in senders._reaction_pending:
            return failure(503, "unavailable", "Slack reaction change is already in progress; check again afterward", target)
        senders._reaction_pending.add(key)
    try:
        checked = recheck()
        if checked:
            return checked
        try:
            result = sender.change_reaction(channel, ts, req["name"], req["op"])
            if (not isinstance(result, dict) or type(result.get("changed")) is not bool
                    or result.get("mine") is not (req["op"] == "add")):
                raise inbox.SendUnknown("Slack reaction outcome unknown; check the original source message")
        except inbox.SendUnknown:
            return failure(504, "unknown", "Slack reaction outcome unknown; check the original source message", target)
        except inbox.SendFailed as error:
            return failure(502, "refused", str(error), target)
        except Exception:  # the change may have reached Slack; never resend
            return failure(504, "unknown", "Slack reaction outcome unknown; check the original source message", target)
        return 200, {"outcome": "confirmed", **target, "op": req["op"], "name": req["name"],
                     "mine": result["mine"], "changed": result["changed"]}
    finally:
        with senders._reaction_lock:
            senders._reaction_pending.discard(key)


RESEND_MAX = 64 * 1024
RESEND_TIMEOUT = 5  # total body-read deadline, not a fresh timeout per chunk


def _verify_resend(headers, body: bytes, key: bytes, now: datetime) -> bool:
    """Strict Svix header adaptation; quo.verify owns the existing HMAC and replay-window check."""
    adapted = {}
    for name in ("id", "timestamp", "signature"):
        values = headers.get_all("svix-" + name, [])
        if len(values) != 1 or not isinstance(values[0], str):
            return False
        adapted["webhook-" + name] = values[0]
    wid, ts, sigs = (adapted["webhook-" + name] for name in ("id", "timestamp", "signature"))
    if not re.fullmatch(r"[!-~]{1,256}", wid) or not re.fullmatch(r"[0-9]{1,12}", ts):
        return False
    if not re.fullmatch(r"v[0-9]+,[A-Za-z0-9+/]+={0,2}(?: +v[0-9]+,[A-Za-z0-9+/]+={0,2})*", sigs):
        return False
    try:
        for sig in sigs.split():
            version, value = sig.split(",")
            raw = base64.b64decode(value, validate=True)
            if base64.b64encode(raw).decode() != value or (version == "v1" and len(raw) != 32):
                return False
        return quo.verify(adapted, body, key, now)
    except (ValueError, TypeError, OverflowError):
        return False


class _Handler(BaseHTTPRequestHandler):
    routes: dict = {}  # path -> callable(headers, body) -> (status, content_type, bytes)

    def _resend_body(self) -> tuple[int, bytes]:
        lengths = self.headers.get_all("Content-Length", [])
        if self.headers.get_all("Transfer-Encoding", []):
            return 400, b""
        if not lengths:
            return 411, b""
        if len(lengths) != 1 or not re.fullmatch(r"[0-9]+", lengths[0]):
            return 400, b""
        if len(lengths[0]) > 10 or int(lengths[0]) > RESEND_MAX:
            return 413, b""
        length, body = int(lengths[0]), bytearray()
        deadline, previous = time.monotonic() + RESEND_TIMEOUT, self.connection.gettimeout()
        try:
            while len(body) < length:
                left = deadline - time.monotonic()
                if left <= 0:
                    return 408, b""
                self.connection.settimeout(left)
                chunk = self.rfile.read1(length - len(body))
                if not chunk:
                    return 400, b""
                body.extend(chunk)
            return 200, bytes(body)
        except TimeoutError:
            return 408, b""
        except OSError:
            return 400, b""
        finally:
            self.connection.settimeout(previous)

    def do_POST(self):
        if self.path != "/hooks/resend":
            # Drain legacy bodies before 404: Unix clients may still be writing an unknown context request.
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        route = self.routes.get(self.path)
        if route is None:
            self.send_error(404)
            return
        if self.path == "/hooks/resend":
            status, body = self._resend_body()
            if status != 200:
                self.close_connection = True
                ctype, out = "text/plain", b""
            else:
                status, ctype, out = route(self.headers, body)
        else:
            status, ctype, out = route(self.headers, body)
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def address_string(self):  # Unix sockets have no peer address
        return "local"

    def log_message(self, fmt, *args):  # no request bodies or tokens in logs (NFR-5)
        pass


def _handler(routes):
    return type("Handler", (_Handler,), {"routes": routes})


class UnixHTTPServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True


def reply_server(db_path: str, socket_path: str, senders: Senders, contexts: dict | None = None) -> UnixHTTPServer:
    """The local socket: POST /reply, POST /send-draft, POST /slack-conversations, and POST /<source> for each Context source in `contexts` ({"tana": …})."""
    # Reactions are a provider operation, not a remote /call ledger write.
    contexts = contexts or {}
    def reply(headers, body):
        db = inbox.connect(db_path)
        try:
            status, out = handle_reply(db, body, senders)
        finally:
            db.close()
        return status, "application/json", json.dumps(out).encode()

    def send_draft(headers, body):
        db = inbox.connect(db_path)
        try:
            status, out = handle_send_draft(db, body, senders)
        finally:
            db.close()
        return status, "application/json", json.dumps(out).encode()

    def slack_conversations(headers, body):
        db = inbox.connect(db_path)
        try:
            status, out = handle_slack_conversations(db, body, senders)
        finally:
            db.close()
        return status, "application/json", json.dumps(out).encode()

    def slack_reactions(headers, body):
        db = inbox.connect(db_path)
        try:
            status, out = handle_slack_reactions(db, body, senders)
        finally:
            db.close()
        return status, "application/json", json.dumps(out).encode()

    def attio_people(headers, body):
        status, out = handle_attio_people(body, contexts.get("attio"))
        return status, "application/json", json.dumps(out).encode()

    def linear_route(headers, body):
        db = inbox.connect(db_path)
        try:
            status, out = handle_linear(db, body, contexts.get("linear"))
        finally:
            db.close()
        return status, "application/json", json.dumps(out).encode()

    def context_route(name):
        def route(headers, body):
            status, out = handle_context(body, contexts.get(name), name.title())
            return status, "application/json", json.dumps(out).encode()
        return route

    from . import remote  # Comms Terminal's laptop copy writes and syncs through these (ADR 0004)
    changes = remote.Changes(db_path)

    def json_route(handle):
        def route(headers, body):
            status, out = handle(body)
            return status, "application/json", json.dumps(out).encode()
        return route

    if os.path.exists(socket_path):
        os.unlink(socket_path)
    old = os.umask(0o177)  # socket created 0600 (NFR-4)
    try:
        return UnixHTTPServer(socket_path, _handler({"/reply": reply, "/send-draft": send_draft,
                                                         "/slack-conversations": slack_conversations, "/attio-people": attio_people, "/tana": context_route("tana"), "/attio": context_route("attio"), "/linear": linear_route,
                                                         "/slack-reactions": slack_reactions,
                                                         "/call": json_route(lambda b: remote.handle_call(db_path, b)),
                                                         "/sync": json_route(lambda b: remote.handle_sync(db_path, changes, b)),
                                                         "/raw": json_route(lambda b: remote.handle_raw(db_path, b))}))
    finally:
        os.umask(old)


def hooks_server(db_path: str, host: str, port: int, client=None,
                 kick: threading.Event | None = None) -> ThreadingHTTPServer:
    """`client` (Slack lookups) is swappable for the 6.3 demo; the service uses the real API. Hooks set `kick`
    for deferred file downloads or durable Resend receipt reconciliation."""
    kick = kick or threading.Event()
    secrets = {n: credential(p.hook_secret) for n, p in platforms.PLATFORMS.items() if p.hook_secret}
    try:
        resend_secret = credential("resend-webhook-secret")
        if (not isinstance(resend_secret, bytes) or not resend_secret.startswith(b"whsec_")
                or not resend_secret[6:]
                or base64.b64encode(base64.b64decode(resend_secret[6:], validate=True)) != resend_secret[6:]):
            resend_secret = None
    except (OSError, ValueError, TypeError):
        resend_secret = None
    token = credential("slack-user-token")
    client = client or (slack.SlackClient(token.decode()) if token else None)
    extra = {"slack": (client,)}  # Slack names users and channels through its API

    def route(path, choose):
        def hook(headers, body):
            name = choose(headers)
            secret, args = secrets[name], extra.get(name, ())
            if secret is None or None in args:
                return 503, "text/plain", f"{path.rsplit('/', 1)[1]} not connected".encode()
            db = inbox.connect(db_path)
            try:
                status, text = intake(db, name, headers, body, secret, datetime.now(timezone.utc), *args)
            finally:
                db.close()
            if name not in platforms.EMAIL:  # email stores its files inline
                kick.set()
            return status, "text/plain", text.encode()
        return hook

    def resend_hook(headers, body):
        if resend_secret is None:
            return 503, "text/plain", b""
        if not _verify_resend(headers, body, resend_secret, datetime.now(timezone.utc)):
            return 401, "text/plain", b""
        try:
            db = inbox.connect(db_path)
            try:
                rows = db.execute("SELECT id, self_id, external_id FROM connections WHERE platform = 'gmail'").fetchall()
                if len(rows) > 1:  # one Resend account sends for every Gmail Connection: the one it sent from
                    sender = email.receipt_sender(body)
                    rows = [r for r in rows if r["self_id"].lower() == sender]
                if (len(rows) != 1 or rows[0]["id"] <= 0
                        or not rows[0]["self_id"].strip() or not rows[0]["external_id"].strip()):
                    return 503, "text/plain", b""
                status, inserted = email.handle_resend_hook(db, rows[0]["id"], body)
            finally:
                db.close()
        except (sqlite3.Error, OSError):
            return 503, "text/plain", b""
        if status == 204 and inserted:
            kick.set()
        return status, "text/plain", b""

    return ThreadingHTTPServer((host, port), _handler({**{path: route(path, choose) for path, choose in HOOKS.items()},
                                                       "/hooks/resend": resend_hook}))


# Public hook path -> the Platform whose Connection it serves. The envelope recipient picks the email Platform:
# Fastmail's rule copies to its own address (#113).
HOOKS = {
    "/hooks/email": lambda h: "fastmail" if (h.get("X-Inbox-To") or "").lower() == fastmail.INBOUND else "gmail",
    "/hooks/slack": lambda h: "slack",
    "/hooks/quo": lambda h: "quo",
}


SIGNATURE_ERROR = "inbound request failed signature check"


def intake(db, platform: str, headers, body: bytes, secret: bytes, now: datetime, *args) -> tuple[int, str]:
    """Every inbound webhook: the Platform's Connection (503 when there is none) goes to its adapter's
    `handle_hook`, which verifies and parses; a failed signature check (401) records a Last Error (AS-19).
    Email picks the Connection whose Inbound address is the envelope recipient (X-Inbox-To), else the Platform's
    first: every email Connection's forward reaches the same Worker and signing secret."""
    to = (headers.get("X-Inbox-To") or "").lower() if platform in platforms.EMAIL else ""
    connection = (to and db.execute("SELECT * FROM connections WHERE inbound = ?", (to,)).fetchone()) or db.execute(
        "SELECT * FROM connections WHERE platform = ? ORDER BY id", (platform,)).fetchone()
    if connection is None:
        return 503, f"no {platforms.PLATFORMS[platform].title} Connection"
    p = platforms.PLATFORMS[connection["platform"]]
    out = importlib.import_module(f".{p.adapter}", __package__).handle_hook(db, connection, headers, body, secret,
                                                                            now, *args)
    status, text = out if isinstance(out, tuple) else (out, "")
    if status == 401:  # the adapter's reason goes on the Connection page, not back to the caller
        inbox.set_connection_error(db, connection["id"], f"{SIGNATURE_ERROR} ({text})" if text else SIGNATURE_ERROR)
        return status, ""
    if status < 300 and (connection["last_error"] or "").startswith(SIGNATURE_ERROR):  # a signed request clears it
        inbox.set_connection_error(db, connection["id"], None)
    return status, text


def fetch_pending(db, getters: dict) -> int:
    """Copy pending Slack and Quo Attachments into the file store (FR-A3, FR-A4); returns how many were stored.
    A download `files.fetch` refuses counts one failed try; a Platform with no getter waits."""
    stored = 0
    for a in inbox.pending_attachments(db):
        if (get := getters.get(a["platform"])) is None:
            continue
        if (data := files.fetch(get, a)) is not None:
            inbox.mark_stored(db, a["id"], files.store_file(db, data), len(data))
            stored += 1
        else:
            inbox.mark_failed(db, a["id"])
    return stored


UPLOAD_MAX = 100 * 1024 * 1024  # one upload request, all files together
# Types a browser would run as a page: served sandboxed, so a received file cannot script the file server's origin.
ACTIVE_TYPES = {"text/html", "application/xhtml+xml", "image/svg+xml", "text/xml", "application/xml"}

UPLOAD_PAGE = b"""<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width">
<title>Attach files</title>
<style>body{font:16px system-ui;margin:2em auto;max-width:36em;padding:0 1em}
#drop{border:2px dashed #888;border-radius:8px;padding:3em 1em;text-align:center}#drop.over{background:#8882}</style>
<h1>Attach files</h1><p>They appear on the reply or Draft in the inbox.</p>
<div id=drop>Drop files here, or <input type=file multiple id=pick></div><ul id=done></ul>
<script>
const drop=document.getElementById('drop'),done=document.getElementById('done');
async function send(files){if(!files.length)return;const f=new FormData();for(const x of files)f.append('file',x,x.name);
const r=await fetch(location.pathname,{method:'POST',body:f});
if(!r.ok){done.insertAdjacentHTML('beforeend','<li>Upload failed ('+r.status+')</li>');return}
for(const x of await r.json()){const li=document.createElement('li');li.textContent=x.name+' attached';done.append(li)}}
document.getElementById('pick').onchange=e=>send(e.target.files);
drop.ondragover=e=>{e.preventDefault();drop.classList.add('over')};drop.ondragleave=()=>drop.classList.remove('over');
drop.ondrop=e=>{e.preventDefault();drop.classList.remove('over');send(e.dataTransfer.files)};
</script>
"""


def handle_upload(db, owner: tuple[str, int], content_type: str, body: bytes) -> tuple[int, list | dict]:
    """POST /up/<token>: multipart/form-data -> each file stored and staged on the token's owner (FR-A9)."""
    from email import policy
    from email.parser import BytesParser
    import mimetypes
    msg = BytesParser(policy=policy.HTTP).parsebytes(b"Content-Type: " + content_type.encode("latin-1") + b"\r\n\r\n" + body)
    if not msg.is_multipart():
        return 400, {"error": "expected multipart/form-data"}
    out = []
    for part in msg.iter_parts():
        name = os.path.basename((part.get_filename() or "").replace("\\", "/"))
        if not name:  # a form field, or the empty part a browser sends when no file was chosen
            continue
        data = part.get_payload(decode=True) or b""
        ctype = part.get_content_type()
        if ctype == "application/octet-stream":
            ctype = mimetypes.guess_type(name)[0] or ctype
        try:
            staged_id = inbox.stage(db, sha=files.store_file(db, data), name=name, type=ctype, size=len(data),
                                    **{f"{owner[0]}_id": owner[1]})
        except sqlite3.IntegrityError:  # the Draft or Thread is gone
            return 404, {"error": "unknown owner"}
        out.append({"id": staged_id, "name": name, "type": ctype, "size": len(data)})
    return 200, out


class _FilesHandler(_Handler):
    db_path, secret = "", b""

    def _send(self, status, ctype, out, headers=()):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(out)))
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(out)

    def do_GET(self):
        parts = self.path.split("?")[0].split("/")
        if len(parts) == 3 and parts[1] == "up" and files.upload_owner(self.secret, parts[2]):
            return self._send(200, "text/html; charset=utf-8", UPLOAD_PAGE, [("X-Content-Type-Options", "nosniff")])
        sha = files.cap_sha(self.secret, parts[2]) if len(parts) == 4 and parts[1] == "f" else None
        db = inbox.connect(self.db_path)
        try:
            ctype = sha and inbox.file_type(db, sha)
            path = ctype and files.attachment_path(db, sha)
        finally:
            db.close()
        if not path or not path.is_file():
            return self.send_error(404)
        name = urllib.parse.unquote(parts[3]) or sha
        total = path.stat().st_size
        try:
            span = byte_range(self.headers.get("Range"), total)
        except ValueError:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{total}")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", "0")
            return self.end_headers()
        start, end = span or (0, total - 1)
        self.send_response(206 if span else 200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(end - start + 1))
        if span:
            self.send_header("Content-Range", f"bytes {start}-{end}/{total}")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Disposition", f"inline; filename*=UTF-8''{urllib.parse.quote(name, safe='')}")
        self.send_header("X-Content-Type-Options", "nosniff")
        if ctype in ACTIVE_TYPES or ctype.endswith("+xml"):
            self.send_header("Content-Security-Policy", "sandbox")
        self.end_headers()
        if self.command == "HEAD":
            return
        with path.open("rb") as f:
            f.seek(start)
            left = end - start + 1
            while left > 0 and (chunk := f.read(min(left, 2**16))):
                self.wfile.write(chunk)
                left -= len(chunk)

    do_HEAD = do_GET

    def do_POST(self):
        parts = self.path.split("?")[0].split("/")
        owner = files.upload_owner(self.secret, parts[2]) if len(parts) == 3 and parts[1] == "up" else None
        if owner is None:
            return self.send_error(404)
        try:
            length = int(self.headers["Content-Length"])
            if length < 0:
                raise ValueError
        except (TypeError, ValueError):
            return self.send_error(411)
        if length > UPLOAD_MAX:
            self.close_connection = True
            return self.send_error(413)
        body = self.rfile.read(length)
        db = inbox.connect(self.db_path)
        try:
            status, out = handle_upload(db, owner, self.headers.get("Content-Type", ""), body)
        finally:
            db.close()
        self._send(status, "application/json", json.dumps(out).encode())


RANGE = re.compile(r"bytes=(\d*)-(\d*)")


def byte_range(header: str | None, total: int) -> tuple[int, int] | None:
    """One `bytes=a-b`, `a-` or `-n` Range as inclusive (start, end). None serves the whole file: no Range,
    a malformed one, or several (RFC 9110 lets a server ignore it). ValueError: no byte of it exists (416)."""
    m = RANGE.fullmatch((header or "").strip())
    if not m or m.groups() == ("", ""):
        return None
    a, b = m.groups()
    if not a:
        if int(b) == 0 or total == 0:
            raise ValueError
        return max(total - int(b), 0), total - 1
    if b and int(b) < int(a):
        return None
    if int(a) >= total:
        raise ValueError
    return int(a), min(int(b), total - 1) if b else total - 1


def files_server(db_path: str, host: str, port: int) -> ThreadingHTTPServer:
    """The LAN file server (NFR-A2): GET or HEAD /f/<cap>/<name> serves a stored file, with one byte Range
    for seeking video; GET and POST /up/<token> take uploads onto a Draft or reply. Anything else, a bad capability, or an expired token is 404 (AS-A11)."""
    db = inbox.connect(db_path)
    try:
        secret = files.files_secret(db)
    finally:
        db.close()
    # ponytail: whole file in memory per upload (<=100 MB); stream to disk if uploads grow
    return ThreadingHTTPServer((host, port), type("FilesHandler", (_FilesHandler,),
                                                  {"db_path": db_path, "secret": secret}))


def wake_loop(db_path: str, stop: threading.Event, every: float = 60, kick: threading.Event | None = None,
              get: dict | None = None, senders: Senders | None = None) -> None:
    """wake() each minute and send the scheduled Drafts that are due; reconcile durable receipts before slow file
    downloads. Hook `kick` also wakes this pass (after acknowledgement, NFR-A5); old files expire once a day (NFR-A6)."""
    import logging
    kick, expired = kick or threading.Event(), None
    while True:
        kick.wait(every)
        if stop.is_set():
            return
        kick.clear()
        db = inbox.connect(db_path)
        try:
            now = datetime.now(timezone.utc)
            inbox.wake(db, now)
            if senders is not None:
                send_due(db, senders, now)
            inbox.reconcile_resend_receipts(db)
            fetch_pending(db, get or {})
            if expired is None or now - expired >= timedelta(days=1):
                expired = now
                inbox.expire_attachments(db, now)
        except Exception as e:  # noqa: BLE001 - one failed pass must not stop the loop
            logging.getLogger("comms_terminal.wake").info("wake pass failed: %s", e)
        finally:
            db.close()


def probe_all(db, shared: dict, cred=credential) -> None:
    """One health check per Connection through its adapter's `probe` (decision #119). Fastmail has none:
    its 10-minute Sent poll records the same result."""
    import logging
    for c in inbox.connections(db):
        adapter = importlib.import_module(f".{platforms.PLATFORMS[c['platform']].adapter}", __package__)
        if (probe := getattr(adapter, "probe", None)) is None:
            continue
        try:
            error = probe(c, cred, shared)
        except Exception as e:  # noqa: BLE001 - one bad check must not stop the others or the loop
            error = f"health check crashed: {e}"
        inbox.set_probe(db, c["id"], error)
        logging.getLogger("comms_terminal.health").info("health check %s: %s", c["platform"], error or "ok")


def probe_loop(db_path: str, shared: dict, stop: threading.Event, every: float = 1800) -> None:
    # ponytail: one failed check shows BROKEN for 30 minutes; retry once inside probe_all if blips annoy
    while True:
        db = inbox.connect(db_path)
        try:
            probe_all(db, shared)
        finally:
            db.close()
        if stop.wait(every):
            return


def calendar_loop(db_path: str, url: str, stop: threading.Event, every: float = 30) -> None:
    """Read the Event provider every 30 seconds (ADR 0005). Writes happen only on a change or a failure run, so a
    quiet calendar sends the laptop console nothing."""
    import logging
    log, ok_at = logging.getLogger("comms_terminal.calendar"), None
    while True:
        db = inbox.connect(db_path)
        try:
            if calendar.sync(db, calendar.fetch(url)):
                log.info("calendar changed")
            ok_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            calendar.record_ok(db)
        except Exception as e:  # noqa: BLE001 - one failed fetch must not stop the loop
            error = (str(e) or type(e).__name__).replace(url, "<secret address>")[:300]
            calendar.record_failure(db, error, ok_at)
            log.info("calendar fetch failed: %s", error)
        finally:
            db.close()
        if stop.wait(every):
            return


def run(db_path: str, socket_path: str, host: str, port: int, files_host: str | None = None,
        files_port: int = 8789) -> None:
    import logging
    logging.basicConfig(level=logging.INFO, format="%(message)s")  # journald adds the time
    db = inbox.connect(db_path)
    inbox.migrate(db)
    fm = fastmail_client(db)
    senders = Senders({"fastmail": fm} if fm is not None else {})
    contexts = {"tana": tana_context(db), "attio": attio_context(db), "linear": linear_context(db)}
    db.close()
    stop, kick = threading.Event(), threading.Event()
    servers = [hooks_server(db_path, host, port, kick=kick), reply_server(db_path, socket_path, senders, contexts)]
    if files_host:  # LAN address only (NFR-A2); without it, chips keep their Platform links
        servers.append(files_server(db_path, files_host, files_port))
    threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in servers]
    threads.append(threading.Thread(target=wake_loop, args=(db_path, stop, 60, kick,
                                                      files.getters(credential("slack-user-token")), senders), daemon=True))
    threads.append(threading.Thread(target=probe_loop, args=(db_path, senders.shared, stop), daemon=True))
    if feed := credential(calendar.CREDENTIAL):
        threads.append(threading.Thread(target=calendar_loop, args=(db_path, feed.decode(), stop), daemon=True))
    if fm is not None:
        threads.append(threading.Thread(target=fastmail.poll_loop, args=(db_path, fm, stop), daemon=True))
    if token := credential("slack-user-token"):
        def names():
            db = inbox.connect(db_path)
            try:
                slack.refresh_names(db, slack.SlackClient(token.decode()))
            except (OSError, ValueError, KeyError) as e:
                import logging
                logging.getLogger("comms_terminal.slack").info("slack channel names not refreshed: %s", e)
            finally:
                db.close()
        threads.append(threading.Thread(target=names, daemon=True))
    for t in threads:
        t.start()
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        stop.set()
        kick.set()
        for s in servers:
            s.shutdown()
