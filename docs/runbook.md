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
   universal-inbox init --email jeremy@agentmaple.com
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

1. **You**, on Mani (browser): `python3 scripts/oauth_fastmail.py --resource mcp --out ~/.config/universal-inbox/fastmail-oauth.json`. On Fastmail's consent page tick read, write, **and** send (the scope must list `mcp-read mcp-action mcp-send`). Copy the file to Workbox's `~/.config/universal-inbox/` (0600) and shred the Mani copy.
2. On Workbox: `universal-inbox init --fastmail legere@fastmail.com`. Optional one-time import: `universal-inbox fastmail-import --days 30` (Inbox and Sent, every Thread left in Todo).
3. **You:** `sudo bash deploy/install.sh`: it stores `fastmail-oauth` and adds `universal-inbox.service.d/fastmail.conf`.
4. Cloudflare: `scripts/cloudflare_setup.py` adds the routing rule `fastmail@inbox.agentmaple.com` → Worker (plan, then `--apply`).
5. **You**, Fastmail → Settings → Filters & Rules → Create rule: *All messages* → *Send a copy to* `fastmail@inbox.agentmaple.com`, *Continue to apply other rules*; drag it to the top (done 2026-10-12, rule "Universal Inbox"). Spam and blocked senders are not copied.

The refresh token rotates, and Fastmail revokes the sign-in when an old one is reused. Exactly one process may hold it: the service keeps the current one in `~/.local/share/universal-inbox/fastmail-oauth.json` (0600), which wins over the stored credential. Never run a second copy against the same token; if it is revoked, the Connection shows the error and step 1 is repeated.

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

Then turn off Gmail forwarding, delete the Email Routing rule for `jeremy@inbox.agentmaple.com`, and turn off the Slack app's event subscriptions. Gmail and Slack keep every message; the database stays at `~/.local/share/universal-inbox/inbox.db`. Remove the secrets with `sudo rm /etc/credstore.encrypted/universal-inbox.*` and the package with `uv tool uninstall universal-inbox`.
