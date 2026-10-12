"""Fastmail sign-in for Universal Inbox: OAuth with dynamic client registration and PKCE.

Run on the machine with the browser (Mani). It registers a public client named "Universal Inbox" once
(or reuses --client-id), opens Fastmail's consent page, catches the redirect on 127.0.0.1, exchanges
the code, and writes {client_id, refresh_token, resource, scope} to --out (mode 0600). It then checks
the access token against the resource and prints only the HTTP status, scope, and capability names.
Never prints a token.

    python3 scripts/oauth_fastmail.py --resource jmap --out ~/.config/universal-inbox/fastmail-oauth.json
    python3 scripts/oauth_fastmail.py --resource mcp --out …   # fallback when the plan refuses JMAP
"""

import argparse
import base64
import hashlib
import http.server
import json
import os
import secrets
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

ISSUER = "https://api.fastmail.com"
RESOURCES = {
    "jmap": ("https://api.fastmail.com/jmap/session", "urn:ietf:params:oauth:scope:mail offline_access"),
    "mcp": ("https://api.fastmail.com/mcp", "https://www.fastmail.com/dev/mcp offline_access"),
}
USER_AGENT = "universal-inbox/0.1 (+https://github.com/JMLegere/universal-inbox)"


def call(url, data=None, headers=None, form=False):
    body = None
    if data is not None:
        body = urllib.parse.urlencode(data).encode() if form else json.dumps(data).encode()
    h = {"User-Agent": USER_AGENT, "Accept": "application/json",
         "Content-Type": "application/x-www-form-urlencoded" if form else "application/json", **(headers or {})}
    req = urllib.request.Request(url, body, h, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw or b"{}")
        except ValueError:
            return e.code, {"error": raw.decode(errors="replace")[:200]}


def catch_code(port: int, state: str) -> str:
    got = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if q.get("state", [""])[0] != state:
                self.send_response(400), self.end_headers()
                self.wfile.write(b"State mismatch; close this tab and try again.")
                return
            got["code"], got["error"] = q.get("code", [""])[0], q.get("error", [""])[0]
            got["desc"] = q.get("error_description", [""])[0]
            self.send_response(200), self.end_headers()
            if got["error"] or not got["code"]:
                desc = q.get("error_description", [""])[0]
                self.wfile.write(f"Fastmail did not grant access: {got['error'] or 'no code'} {desc}".encode())
            else:
                self.wfile.write(b"Fastmail approved Universal Inbox; finishing in the terminal. You can close this tab.")

        def log_message(self, *_):
            pass

    with http.server.HTTPServer(("127.0.0.1", port), Handler) as srv:
        while "code" not in got:
            srv.handle_request()
    if got["error"] or not got["code"]:
        raise SystemExit(f"Fastmail refused the sign-in: {got['error'] or 'no code'} {got.get('desc', '')}".rstrip())
    return got["code"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--resource", choices=RESOURCES, default="jmap")
    ap.add_argument("--out", required=True)
    ap.add_argument("--client-id", help="reuse a registered client instead of registering a new one")
    ap.add_argument("--port", type=int, default=8733)
    a = ap.parse_args(argv)
    resource, scope = RESOURCES[a.resource]
    redirect = f"http://127.0.0.1:{a.port}/callback"

    client_id = a.client_id
    if not client_id:
        status, reg = call(f"{ISSUER}/oauth/register", {
            "client_name": "Universal Inbox", "redirect_uris": [redirect], "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"], "scope": scope})
        if status >= 300 or "client_id" not in reg:
            raise SystemExit(f"client registration failed: HTTP {status} {reg.get('error', '')} "
                             f"{reg.get('error_description', '')}".strip())
        client_id = reg["client_id"]
        print(f"registered client 'Universal Inbox' ({client_id})")

    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    url = f"{ISSUER}/oauth/authorize?" + urllib.parse.urlencode({
        "response_type": "code", "client_id": client_id, "redirect_uri": redirect, "scope": scope,
        "resource": resource, "state": state, "code_challenge": challenge, "code_challenge_method": "S256"})
    print("Opening Fastmail's consent page. Approve it (tick sending) in the browser.")
    print(url)
    webbrowser.open(url)
    code = catch_code(a.port, state)

    status, tok = call(f"{ISSUER}/oauth/refresh", {
        "grant_type": "authorization_code", "code": code, "redirect_uri": redirect, "client_id": client_id,
        "code_verifier": verifier, "resource": resource}, form=True)
    if status >= 300 or "access_token" not in tok:
        raise SystemExit(f"token exchange failed: HTTP {status} {tok.get('error', '')} "
                         f"{tok.get('error_description', '')}".strip())
    if not tok.get("refresh_token"):
        raise SystemExit("no refresh token returned (offline_access not granted); nothing saved")
    out = os.path.expanduser(a.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"client_id": client_id, "refresh_token": tok["refresh_token"], "resource": resource,
                   "scope": tok.get("scope", scope)}, f)
    print(f"saved to {out} (0600); granted scope: {tok.get('scope', '(not reported)')}")

    if a.resource == "jmap":
        status, session = call(resource, headers={"Authorization": f"Bearer {tok['access_token']}"})
        caps = sorted(session.get("capabilities", {})) if status == 200 else []
        accounts = len(session.get("accounts", {})) if status == 200 else 0
        print(f"JMAP session: HTTP {status}; accounts: {accounts}; capabilities: {', '.join(caps) or '-'}")
        if status != 200:
            print(f"  {session.get('error', '')} {session.get('detail', session.get('error_description', ''))}".rstrip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
