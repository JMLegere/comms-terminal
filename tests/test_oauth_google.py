"""Google Calendar sign-in helper (FR-13e): offline calendar consent with PKCE; credential written 0600."""

import json
import os
import stat
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import oauth_google  # noqa: E402


def test_the_consent_url_asks_for_offline_calendar_access_with_pkce():
    url = oauth_google.authorize_url("cid.apps.example", "http://127.0.0.1:8734/callback", "st", "ch",
                                     "jeremy@agentmaple.example")
    q = {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).items()}
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert q == {"response_type": "code", "client_id": "cid.apps.example",
                 "redirect_uri": "http://127.0.0.1:8734/callback",
                 "scope": "https://www.googleapis.com/auth/calendar", "access_type": "offline",
                 "prompt": "consent", "login_hint": "jeremy@agentmaple.example", "state": "st",
                 "code_challenge": "ch", "code_challenge_method": "S256"}


def test_the_credential_has_exactly_three_fields_and_is_0600(tmp_path):
    out = tmp_path / "cfg" / "google-calendar-oauth.json"
    oauth_google.save(str(out), {"client_id": "cid", "client_secret": "sec", "token_uri": "x", "project_id": "p"},
                      "rt")
    assert json.loads(out.read_text()) == {"client_id": "cid", "client_secret": "sec", "refresh_token": "rt"}
    assert stat.S_IMODE(os.stat(out).st_mode) == 0o600
