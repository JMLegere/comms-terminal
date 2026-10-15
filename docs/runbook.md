# Runbook

Operating Universal Inbox on Workbox (Debian 12, systemd 252). Phase 6 of `docs/prd.md`. Steps marked **You** need Jeremy (a browser, an account, or `sudo`); the agent can run the rest.

Secrets are staged as 0600 files in `~/.config/universal-inbox/`, read in-process, and never printed. `deploy/install.sh` encrypts each into systemd's credential store (`LoadCredentialEncrypted=`) and shreds the clear copy (NFR-5).

## 1. Package

```
uv tool install --force --reinstall /home/jerry/workspace/universal-inbox
```

Puts `universal-inbox` in `~/.local/bin`, which the unit runs. Re-run after pulling changes, then `sudo systemctl restart universal-inbox`. Keep `--reinstall`: the version stays 0.1.0, and without it uv reuses its cached build of the old code.

## 2. Connect email (Slack later)

D3 (decision #102): Cloudflare Tunnel publishes `https://hooks.agentmaple.com/hooks/{email,slack}`; Cloudflare Email Routing receives `inbox.agentmaple.com` and the Email Worker (`deploy/email-worker/`) posts each message, signed, to `/hooks/email`; Resend sends replies. `agentmaple.com` mail stays on Google.

1. **You:** create a Cloudflare API token (dash.cloudflare.com → My Profile → API Tokens → Create Custom Token):
   - Account permissions: Cloudflare Tunnel Edit; Workers Scripts Edit.
   - Zone permissions, zone `agentmaple.com` only: Zone Read; DNS Edit; Zone Settings Edit; Email Routing Rules Edit.

   Save it on Workbox from your own terminal: `mkdir -p ~/.config/universal-inbox; and umask 077; and nano ~/.config/universal-inbox/cloudflare-token` (fish; in bash use `&&`).
2. **You:** in Resend, create an API key with sending access and save it the same way as `~/.config/universal-inbox/resend-api-key`. `agentmaple.com` must be a verified Resend domain (done 2026-10-12): Resend → Domains → Add domain → `agentmaple.com` (North Virginia, return path `send`, tracking off) → Manual setup; its three records are a DKIM TXT at `resend._domainkey` and DNS-only CNAMEs `rsend` and `send` to `*.forge.rmta.net`. The apex MX and SPF are untouched.
3. Generate the inbound mail secret and create the email Connection:
   ```
   bash -c 'umask 077; openssl rand -hex 32 > ~/.config/universal-inbox/email-hook-secret'
   universal-inbox init --gmail jeremy@agentmaple.com
   ```
4. Cloudflare: plan, then apply (the tunnel, `hooks.agentmaple.com`, Email Routing for `inbox.agentmaple.com`, the Worker and its secret, the routing rule):
   ```
   uv run python scripts/cloudflare_setup.py --token-file ~/.config/universal-inbox/cloudflare-token
   uv run python scripts/cloudflare_setup.py --token-file ~/.config/universal-inbox/cloudflare-token \
       --email-secret-file ~/.config/universal-inbox/email-hook-secret --apply
   ```
5. **You:** `sudo bash /home/jerry/workspace/universal-inbox/deploy/install.sh`. It stores the credentials, installs and starts `universal-inbox.service`, and installs the `cloudflared` connector with the tunnel token.
6. Check:
   - `systemctl is-active universal-inbox cloudflared` prints `active` twice.
   - `curl -s -o /dev/null -w '%{http_code}\n' -X POST https://hooks.agentmaple.com/hooks/slack` prints `503` (Slack not connected yet; the request reached the service).
   - `curl -s -o /dev/null -w '%{http_code}\n' https://hooks.agentmaple.com/` prints `404`: only the hook paths are public.
   - `dig +short MX agentmaple.com` still prints `smtp.google.com.`
7. **You:** Gmail forwarding. Gmail → Settings → Forwarding and POP/IMAP → Add a forwarding address: `jeremy@inbox.agentmaple.com`. Google's confirmation code arrives in the console as a Todo Thread; enter it in Gmail, then choose "Forward a copy … and keep Gmail's copy in the Inbox". If Workspace blocks forwarding to an external address, an admin adds Admin console → Gmail → Routing → "Also deliver to" `jeremy@inbox.agentmaple.com` instead.
8. Live check: send yourself an email from another account; it appears as a Todo Thread within seconds. Reply from the console; the reply arrives in the other account's thread.

## 3a. Connect Fastmail

Fastmail is a second email Connection (decisions #111, #113). Incoming mail is event-driven like Gmail: a Fastmail rule sends a copy of every message to `fastmail@inbox.agentmaple.com`, Email Routing hands it to the same Worker, and the hook files it under Fastmail by that address. Jeremy's Basic plan refuses JMAP, IMAP, and API tokens, so replies (and a 10-minute poll of Sent for replies written in Fastmail itself) go through Fastmail's MCP server, signed in as a connected app the way omp is.

1. **You**, on Mani (browser): `python3 scripts/oauth_mcp.py --resource mcp --out ~/.config/universal-inbox/fastmail-oauth.json`. On Fastmail's consent page tick read, write, **and** send (the scope must list `mcp-read mcp-action mcp-send`). Copy the file to Workbox's `~/.config/universal-inbox/` (0600) and shred the Mani copy.
2. On Workbox: `universal-inbox init --fastmail legere@fastmail.com`. Optional one-time import: `universal-inbox fastmail-import --days 30` (Inbox and Sent, every Thread left in Todo).
3. **You:** `sudo bash deploy/install.sh`: it stores `fastmail-oauth` and adds `universal-inbox.service.d/fastmail.conf`.
4. Cloudflare: `scripts/cloudflare_setup.py` adds the routing rule `fastmail@inbox.agentmaple.com` → Worker (plan, then `--apply`).
5. **You**, Fastmail → Settings → Filters & Rules → Create rule: *All messages* → *Send a copy to* `fastmail@inbox.agentmaple.com`, *Continue to apply other rules*; drag it to the top (done 2026-10-12, rule "Universal Inbox"). Spam and blocked senders are not copied.

The refresh token rotates, and Fastmail revokes the sign-in when an old one is reused. Exactly one process may hold it: the service keeps the current one in `~/.local/share/universal-inbox/fastmail-oauth.json` (0600), which wins over the stored credential. Never run a second copy against the same token; if it is revoked, the Connection shows the error and step 1 is repeated.

## 3b. Connect Quo (SMS)

Quo is the SMS Platform (`.agents/discovery/2026-10-12-sms.md`): Jeremy's Work number (506) 832-4154 (Quo phone-number ID `PN2ppcGyMM`). Texts arrive through a signed Quo webhook (Standard Webhooks, `Quo-Api-Version: 2026-03-30`) at `/hooks/quo`; replies from the console go out through Quo's send API; texts Jeremy sends in the Quo app arrive as `message.delivered` and answer the Thread. Group texts are dropped (Quo fires no event for a group text sent from the app). Done 2026-10-12.

1. **You:** save the Quo API key without echoing it: `install -m 600 /dev/null ~/.config/universal-inbox/quo-api-key; and read -s -P 'Quo API key: ' k; and printf %s $k > ~/.config/universal-inbox/quo-api-key; and set -e k`.
2. Create the webhook through the API (`POST https://api.quo.com/webhooks`, header `Quo-Api-Version: 2026-03-30`): URL `https://hooks.agentmaple.com/hooks/quo`, events `message.received`, `message.delivered`, `message.failed`, `message.undelivered`, `resourceIds: ["PN2ppcGyMM"]`. Write the returned `data.key` (`whsec_…`) straight to `~/.config/universal-inbox/quo-webhook-secret` (0600); it is shown once (rotate with `POST /webhooks/{id}/rotate`). The live webhook is ID `43687`, "Universal Inbox".
3. Cloudflare: the tunnel's public path is `^/hooks/(email|slack|quo)$` (`scripts/cloudflare_setup.py`).
4. **You:** `sudo bash deploy/install.sh`: it stores `quo-webhook-secret` and `quo-api-key` and adds `universal-inbox.service.d/quo.conf`.
5. On Workbox: `universal-inbox init --quo +15068324154`. The service makes the Quo sender on first reply; no restart is needed.

Quo restricts the number from texting US numbers until US texting registration (10DLC) is done; Canadian numbers work.

## 3c. Connect Tana (context)

Tana is a read-only Context source (FR-13a, decision #122): Thread, Person, Organization, and Case pages show what Tana knows about their people and Organizations. The service signs in to Tana's MCP server (`https://home.tana.inc/mcp`) and the console asks it over the reply socket (`POST /tana`).

1. On Workbox: `python3 scripts/oauth_mcp.py --resource tana --out ~/.config/universal-inbox/tana-oauth.json`. **You** open the printed link in Mani's browser and approve "Universal Inbox". The browser then fails to load `http://127.0.0.1:8733/callback?state=…&code=…`; deliver that exact URL to the waiting script on Workbox (`curl` it there), or run `ssh -N -L 8733:127.0.0.1:8733 workbox` on Mani first so the redirect lands by itself. Copy the long link from a file (`xdg-open` it), not the terminal: a wrapped copy gets "invalid parameters".
2. **You:** `sudo bash deploy/install.sh`: it stores `tana-oauth`, adds `universal-inbox.service.d/tana.conf`, and restarts the service.
3. Check: open a Thread with someone in Tana; its details column ends with a Tana section, and the bottom bar shows the Tana icon with a check. A failed fetch shows "Tana unavailable: …" and "Tana broken".

As with Fastmail, the service keeps a rotated refresh token in `~/.local/share/universal-inbox/tana-oauth.json` (0600); only the service may hold it.

Tana links in the console open with Ctrl+Shift+click (the console catches a plain click). To land in the Tana desktop app instead of the browser, choose **Open always** on Tana's "Open in Tana" chooser the first time; Tana's Settings → Preferences → Tana links resets it.

## 3d. Connect Attio (context)

Attio is a read-only Context source (FR-13b, decision #123): the same pages show what Attio knows about their people and Organizations, above Tana. The service reads Attio's REST API with a read-only workspace access token and the console asks it over the reply socket (`POST /attio`).

1. **You** (or the agent, in your browser): Attio → Settings → Developers → New access token "Universal Inbox", every scope Disabled except Read on User Management, Records, Object Configuration, List Entries, List Configuration, and Tasks.
2. Save it on Workbox as `~/.config/universal-inbox/attio-token` (0600), keeping it out of shells' history: `read -s -P 'Attio token: ' t; and printf %s $t > ~/.config/universal-inbox/attio-token; and chmod 600 ~/.config/universal-inbox/attio-token; set -e t`.
3. **You:** `sudo bash deploy/install.sh`: it stores `attio-token`, adds `universal-inbox.service.d/attio.conf`, and restarts the service.
4. Check: the rail's context section lists Attio with a check, and a Thread with someone in Attio shows an Attio section above Tana's.

## 3e. Connect Linear (context, later authorization)

Linear is a read-only Context source (FR-13c, decision #125), after Attio and Tana on Thread, Person, Organization, and Case pages. The service holds the key and queries Linear's GraphQL API; the console sends only email/domain arrays to the private Unix socket (`POST /linear`). The service derives unique full Person names and Organization names from the local database for each request. Names shared by local records, address/domain placeholders, and partial Person names fall back to exact email/domain evidence.

**Separately authorized later step:** local implementation approval covers source and synthetic checks only. Obtain Jeremy's approval for the exact workspace, required teams, key creation, credential installation, and deployment before performing these steps. Workspace policy, effective restricted-key Customer Request access, and expiration behavior still require live verification.

1. **You**, in the approved Linear workspace: create a Jeremy-owned personal API key named "Universal Inbox", with **Read** as its only permission and access restricted to the approved required teams ([Linear key permissions](https://linear.app/docs/api-and-webhooks)). Stop if workspace policy prevents that scope.
2. Stage the key on Workbox with the hidden prompt below. The value stays out of command arguments, shell history, and logs; use an interactive terminal and leave shell tracing disabled.
   ```bash
   python3 - <<'PY'
   import getpass
   import os
   import warnings
   from pathlib import Path

   warnings.simplefilter("error", getpass.GetPassWarning)
   token = getpass.getpass("Linear read-only API key: ").strip()
   if not token:
       raise SystemExit("Key file left unchanged")
   path = Path.home() / ".config/universal-inbox/linear-token"
   path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
   fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
   os.fchmod(fd, 0o600)
   with os.fdopen(fd, "w") as staged:
       staged.write(token)
   PY
   ```
3. Install the approved build using §1, then **You:** `sudo bash deploy/install.sh`. The installer encrypts `linear-token`, shreds its staged copy, adds `universal-inbox.service.d/linear.conf`, and restarts the service. Review other staged credentials first: this installer also processes them.
4. Verify workspace identity and effective Read/team scope with the authorized key, including Customer Request coverage. Open representative Thread, Person, Organization, and Case pages: Linear follows Tana, shows every matching nonterminal issue (including backlog and triage), match reasons, relevant Request excerpts, and working **Open in Linear** links. Check separate rail/footer Linear health; test five-minute cache freshness.

The local route defaults omitted arrays to `[]`; explicit `null`, non-array values, and non-string entries return 400. An unconnected source returns 503, connected empty keys return `{"issues":[]}` without a provider call, and adapter failures return a safe 502 error. Linear reads leave Connections, Threads, Activities, and Inbox Status unchanged. The socket remains private; public hook routes are unchanged.

## 3. Connect Slack

1. **You:** https://api.slack.com/apps → Create New App → From a manifest → paste `deploy/slack-app-manifest.yml`. Save the signing secret (Basic Information) as `~/.config/universal-inbox/slack-signing-secret`.
2. **You:** install: `SLACK_CLIENT_SECRET=… uv run python scripts/oauth_slack.py --client-id <id> --redirect-uri https://hooks.agentmaple.com/slack/oauth --out ~/.config/universal-inbox/slack-tokens.json`. It prints the team and user IDs and never prints a token.
3. Stage the user token and create the Connection:
   ```
   python3 -c 'import json, os, pathlib; os.umask(0o077); p = pathlib.Path.home() / ".config/universal-inbox"; (p / "slack-user-token").write_text(json.load(open(p / "slack-tokens.json"))["user_token"])'
   shred -u ~/.config/universal-inbox/slack-tokens.json
   universal-inbox init --slack-team <T…> --slack-user <U…>
   ```
4. **You:** `sudo bash deploy/install.sh` again: it stores both Slack secrets and adds `universal-inbox.service.d/slack.conf`.
5. **You:** in the Slack app's Event Subscriptions, retry the request URL (it verifies now). Invite the bot to each channel to cover whole (`/invite @Universal Inbox`) and add a Channel rule in the console (`9`, then `a`). DMs and group DMs need no rule. From every other channel Jeremy is in, only messages that mention him and replies in threads he has posted in (or that are already in the inbox) come in (#114).
6. **You:** turn off Slack's email notifications (Slack → Preferences → Notifications → email), or they arrive through Gmail as duplicate email threads.

## 4. Service operations

- Status: `systemctl status universal-inbox`; logs: `journalctl -u universal-inbox -n 50` (no request bodies or tokens are logged).
- Restart after an update or a credential change: `sudo systemctl restart universal-inbox`.
- Rotate a secret: stage the new value in `~/.config/universal-inbox/<name>` and re-run `sudo bash deploy/install.sh`.
- Until Slack is connected, `/hooks/slack` answers 503 and Slack replies are refused.

## 5. The console

```
ssh workbox -t universal-inbox
```

It finds the service's socket at `/run/universal-inbox/inbox.sock` without flags. Press `?` for keys.

## 6. Demo on synthetic data (Phase 6.3)

```
cd /home/jerry/workspace/universal-inbox
uv run python scripts/demo.py --step --keep
```

In a second pane, run the console command it prints. The demo runs the real hooks server and reply socket with demo secrets, sends signed fixture mail and Slack events over HTTP, and records replies at fake Resend and Slack endpoints; nothing leaves Workbox. It walks AS-1, AS-3, AS-11, AS-4, AS-6, AS-13, and AS-17 and prints PASS or FAIL for each.

## 7. Rollback

```
sudo systemctl disable --now universal-inbox cloudflared
sudo rm -r /etc/systemd/system/universal-inbox.service /etc/systemd/system/universal-inbox.service.d
sudo systemctl daemon-reload
```

Then turn off Gmail forwarding, delete the Email Routing rule for `jeremy@inbox.agentmaple.com`, and turn off the Slack app's event subscriptions. For Quo, delete webhook `43687` in Quo (or `DELETE /webhooks/43687`). Gmail and Slack keep every message; the database stays at `~/.local/share/universal-inbox/inbox.db`. Remove the secrets with `sudo rm /etc/credstore.encrypted/universal-inbox.*` and the package with `uv tool uninstall universal-inbox`.
