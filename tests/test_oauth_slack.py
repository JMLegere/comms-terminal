"""Slack consent helper: scopes in the authorize URL; tokens written 0600, never printed."""

import io
import json
import os
import stat
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import oauth_slack  # noqa: E402


def test_authorize_url_and_token_file(tmp_path, capsys):
    url = oauth_slack.authorize_url("123.456", "https://example.com/cb")
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    assert set(query["scope"][0].split(",")) == {
        "channels:history", "groups:history", "channels:read", "groups:read",
        "users:read", "users:read.email"}
    assert set(query["user_scope"][0].split(",")) == {
        "channels:history", "groups:history", "im:history", "mpim:history", "im:read", "mpim:read",
        "channels:read", "groups:read", "chat:write", "users:read", "users:read.email",
        "files:read", "files:write", "reactions:read", "reactions:write"}

    reply = {"ok": True, "team": {"id": "T0"}, "access_token": "xoxb-secret",
             "authed_user": {"id": "U0JEREMY", "access_token": "xoxp-secret"}}
    tokens = oauth_slack.exchange("123.456", "s", "code", "https://example.com/cb",
                                  urlopen=lambda req, timeout: io.BytesIO(json.dumps(reply).encode()))
    out = tmp_path / "tokens.json"
    oauth_slack.write_tokens(str(out), tokens)
    assert stat.S_IMODE(os.stat(out).st_mode) == 0o600
    assert json.loads(out.read_text())["user_token"] == "xoxp-secret"
    assert "secret" not in capsys.readouterr().out
