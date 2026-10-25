"""MCP client for remote MCP servers over streamable HTTP, signed in with a refreshable OAuth token.

Fastmail (fastmail.py) and Tana (tana.py) both use it. `scripts/oauth_mcp.py` does the one-time sign-in
(dynamic client registration and PKCE) that produces the refresh token.
"""

import json
import os
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import clock

USER_AGENT = "comms-terminal/0.1 (+https://github.com/JMLegere/comms-terminal)"


class McpError(Exception):
    pass


class Retry(McpError):
    """Refused before running (token or session expired): safe to repeat, even for send_email."""


def _http(url, body: bytes, headers: dict, timeout=30):
    req = urllib.request.Request(url, body, {"User-Agent": USER_AGENT, **headers}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read()
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in e.headers.items()}, e.read()


class McpClient:
    """MCP over streamable HTTP with a refreshable OAuth token. `http` is swappable for tests.

    A subclass names its server: `mcp` (the MCP endpoint), `token_url` (the OAuth token endpoint), and `name`
    (for errors). The refresh token comes from the systemd credential (or a 0600 file); if the server rotates
    it, the new one is written to `state` (beside the database) and preferred from then on.
    """
    mcp = token_url = name = ""

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
        st, _, body = self.http(self.token_url, urllib.parse.urlencode({
            "grant_type": "refresh_token", "refresh_token": self.cred["refresh_token"],
            "client_id": self.cred["client_id"], "resource": self.cred.get("resource", self.mcp)}).encode(),
            {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"})
        tok = json.loads(body or b"{}")
        if st != 200 or "access_token" not in tok:
            raise McpError(f"{self.name} sign-in expired or revoked (HTTP {st} {tok.get('error', '')})".strip())
        self.access = tok["access_token"]
        self.expires = clock.now() + timedelta(seconds=int(tok.get("expires_in", 3600)) - 120)
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
        if self.access is None or clock.now() >= self.expires:
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
        st, h, body = self.http(self.mcp, json.dumps(msg).encode(), headers)
        if st == 401:
            self.access = None
            raise Retry(f"{self.name} refused the access token")
        if st == 404 and self.session:  # the server forgot the session: start a new one next call
            self.session = None
            raise Retry(f"{self.name} MCP session expired")
        if st >= 300:
            raise McpError(f"{self.name} MCP HTTP {st}")
        if method == "initialize":
            self.session = h.get("mcp-session-id") or ""
        if notify:
            return None
        text = body.decode()
        if "data:" in text and not text.lstrip().startswith("{"):
            text = "\n".join(ln[5:].strip() for ln in text.splitlines() if ln.startswith("data:"))
        out = json.loads(text)
        if "error" in out:
            raise McpError(f"{self.name} MCP error: {out['error'].get('message', out['error'])}")
        return out["result"]

    def _initialize(self):
        self._rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                 "clientInfo": {"name": "comms-terminal", "version": "0.1"}})
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
            raise McpError(f"{self.name} {name} failed: {msg[:200]}")
        if "structuredContent" in res:
            return res["structuredContent"]
        return json.loads(res["content"][0]["text"])
