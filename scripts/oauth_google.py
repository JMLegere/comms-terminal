"""Google Calendar sign-in for Comms Terminal's invite Responses (FR-13e, ADR 0009, runbook §3h).

Run on the machine with the browser (Mani). It reads the Desktop OAuth client Google gave you (--client, the
downloaded JSON), opens Google's consent page for the calendar scope, catches the redirect on 127.0.0.1 (PKCE),
exchanges the code, and writes {client_id, client_secret, refresh_token} to --out (mode 0600) for the
`google-calendar-oauth` credential. It then reads the primary calendar's metadata and prints only the HTTP
status, the calendar's id, and the access role. Never prints a secret or token.

    python3 scripts/oauth_google.py --out ~/.config/comms-terminal/google-calendar-oauth.json
"""

import argparse
import base64
import hashlib
import json
import os
import secrets
import urllib.parse
import webbrowser

from oauth_mcp import call, catch_code  # same folder: the shared request and loopback helpers

AUTHORIZE = "https://accounts.google.com/o/oauth2/v2/auth"
SCOPE = "https://www.googleapis.com/auth/calendar"  # full calendar access: Jeremy, 2026-10-27
CALENDAR = "https://www.googleapis.com/calendar/v3/calendars/primary"


def authorize_url(client_id: str, redirect: str, state: str, challenge: str, login: str) -> str:
    return f"{AUTHORIZE}?" + urllib.parse.urlencode({
        "response_type": "code", "client_id": client_id, "redirect_uri": redirect, "scope": SCOPE,
        "access_type": "offline", "prompt": "consent", "login_hint": login, "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256"})


def save(out: str, client: dict, refresh_token: str) -> None:
    out = os.path.expanduser(out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"client_id": client["client_id"], "client_secret": client["client_secret"],
                   "refresh_token": refresh_token}, f)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--client", default="~/.config/comms-terminal/google-oauth-client.json",
                    help="the Desktop client JSON downloaded from Google Cloud (kept 0600)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--login", default="jeremy@agentmaple.com")
    ap.add_argument("--port", type=int, default=8734)
    a = ap.parse_args(argv)
    with open(os.path.expanduser(a.client)) as f:
        client = json.load(f)["installed"]
    redirect = f"http://127.0.0.1:{a.port}/callback"

    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    url = authorize_url(client["client_id"], redirect, state, challenge, a.login)
    print(f"Opening Google's consent page. Sign in as {a.login} and allow calendar access.")
    webbrowser.open(url)
    code = catch_code(a.port, state)

    status, tok = call(client["token_uri"], {
        "grant_type": "authorization_code", "code": code, "redirect_uri": redirect,
        "client_id": client["client_id"], "client_secret": client["client_secret"], "code_verifier": verifier},
        form=True)
    if status >= 300 or "access_token" not in tok:
        raise SystemExit(f"token exchange failed: HTTP {status} {tok.get('error', '')}".strip())
    if not tok.get("refresh_token"):
        raise SystemExit("no refresh token returned; nothing saved")
    if SCOPE not in tok.get("scope", "").split():
        raise SystemExit(f"calendar scope not granted (granted: {tok.get('scope', '-')}); nothing saved")
    save(a.out, client, tok["refresh_token"])
    print(f"saved to {a.out} (0600)")

    status, cal = call(CALENDAR, headers={"Authorization": f"Bearer {tok['access_token']}"})
    print(f"primary calendar: HTTP {status}; id: {cal.get('id', '-')}; time zone: {cal.get('timeZone', '-')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
