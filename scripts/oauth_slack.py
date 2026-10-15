"""Slack consent helper for Jeremy's post-build connect (PRD 3.1).

Run on a machine with a browser. Prints the authorize URL, takes the `code` from the
redirect URL, exchanges it, and writes the tokens to --out (mode 0600). Never prints a token.

    SLACK_CLIENT_SECRET=... uv run python scripts/oauth_slack.py \
        --client-id 123.456 --redirect-uri https://example.com/slack/callback --out slack-tokens.json
"""

import argparse
import base64
import getpass
import json
import os
import urllib.parse
import urllib.request

# Bot: read selected channels. User: Jeremy's DMs and group DMs, channel names, profiles, replies as him,
# and Attachments: download received files and upload sent ones (.agents/prd/2026-10-15-attachments.md).
BOT_SCOPES = ["channels:history", "groups:history", "channels:read", "groups:read",
              "users:read", "users:read.email"]
USER_SCOPES = ["channels:history", "groups:history", "im:history", "mpim:history", "im:read", "mpim:read", "channels:read", "groups:read",
               "chat:write", "users:read", "users:read.email", "files:read", "files:write"]


def authorize_url(client_id: str, redirect_uri: str) -> str:
    return "https://slack.com/oauth/v2/authorize?" + urllib.parse.urlencode({
        "client_id": client_id, "scope": ",".join(BOT_SCOPES),
        "user_scope": ",".join(USER_SCOPES), "redirect_uri": redirect_uri})


def exchange(client_id, client_secret, code, redirect_uri, urlopen=urllib.request.urlopen) -> dict:
    auth = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    req = urllib.request.Request(
        "https://slack.com/api/oauth.v2.access",
        urllib.parse.urlencode({"code": code, "redirect_uri": redirect_uri}).encode(),
        {"Authorization": f"Basic {auth}", "Content-Type": "application/x-www-form-urlencoded"})
    with urlopen(req, timeout=15) as r:
        body = json.loads(r.read())
    if not body.get("ok"):
        raise SystemExit(f"oauth.v2.access failed: {body.get('error', 'unknown')}")
    return {"team_id": body["team"]["id"], "bot_token": body["access_token"],
            "user_id": body["authed_user"]["id"], "user_token": body["authed_user"]["access_token"]}


def write_tokens(path: str, tokens: dict) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(tokens, f)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--client-id", required=True)
    p.add_argument("--redirect-uri", required=True)
    p.add_argument("--out", required=True)
    a = p.parse_args()
    secret = os.environ.get("SLACK_CLIENT_SECRET") or getpass.getpass("Slack client secret: ")
    print("Open this URL, approve, then copy the `code` parameter from the redirect URL:")
    print(authorize_url(a.client_id, a.redirect_uri))
    code = input("code: ").strip()
    tokens = exchange(a.client_id, secret, code, a.redirect_uri)
    write_tokens(a.out, tokens)
    print(f"Wrote tokens for team {tokens['team_id']} (user {tokens['user_id']}) to {a.out}")


if __name__ == "__main__":
    main()
