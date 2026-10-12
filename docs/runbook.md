# Runbook

Operating Universal Inbox on Workbox (Debian 12, systemd 252). Phase 6 of `docs/prd.md`. Commands that need `sudo` are run by Jeremy; they work in fish or bash.

## 1. Install or update the package

```
uv tool install --force /home/jerry/workspace/universal-inbox
```

This puts `universal-inbox` in `~/.local/bin`, which the service unit runs. Run it again after pulling changes, then restart the service (§4).

## 2. Create the Connections

```
universal-inbox init --email jeremy@agentmaple.com --slack-team <T…> --slack-user <U…>
```

The database is `~/.local/share/universal-inbox/inbox.db`. The Slack team and user IDs come from the OAuth step in §3.

## 3. Credentials

The service reads four secrets from systemd's encrypted credential store (`LoadCredentialEncrypted=`). They are never in the environment, the repository, or the logs (NFR-5). For each one, run the command, paste the value, then press `ctrl+d` on an empty line:

```
sudo systemd-creds encrypt --name=<name> - /etc/credstore.encrypted/universal-inbox.<name>
```

| `<name>` | Value | Where it comes from |
|---|---|---|
| `email-hook-secret` | 64 hex characters | Generated in §6, which also stores it; the Email Worker signs each request with it |
| `slack-signing-secret` | Slack signing secret | Slack app → Basic Information → App Credentials |
| `slack-user-token` | `xoxp-…` | `uv run python scripts/oauth_slack.py --client-id <id> --redirect-uri <url> --out slack-tokens.json` writes the tokens to a 0600 file and never prints them; it also prints the team and user IDs for §2. Encrypt the user token straight from the file: `python3 -c 'import json; print(json.load(open("slack-tokens.json"))["user_token"], end="")' \| sudo systemd-creds encrypt --name=slack-user-token - /etc/credstore.encrypted/universal-inbox.slack-user-token`, then `shred -u slack-tokens.json` |
| `resend-api-key` | `re_…` | Resend → API Keys (sending access, the agentmaple.com domain) |

Check: `sudo ls /etc/credstore.encrypted/` lists the four `universal-inbox.*` files.

To rotate a secret, run its `encrypt` command again and restart the service.

## 4. Install and start the service

```
sudo install -m 0644 /home/jerry/workspace/universal-inbox/deploy/universal-inbox.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now universal-inbox
```

Check:

- `systemctl status universal-inbox` shows `active (running)`.
- `journalctl -u universal-inbox -n 50` shows no errors. Request bodies and tokens are never logged.
- `curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:8787/hooks/slack` prints `401`: an unsigned request is rejected.
- `ls -l /run/universal-inbox/inbox.sock` shows `srw-------` owned by `jerry`.

Restart after an update or a credential change: `sudo systemctl restart universal-inbox`.

## 5. The console

```
ssh workbox -t universal-inbox
```

The console finds the service's socket at `/run/universal-inbox/inbox.sock` without flags. Press `?` for keys.

## 6. Public ingress and inbound mail (Phase 6.2)

D3 (decision #102): Cloudflare Tunnel publishes `https://hooks.agentmaple.com/hooks/{email,slack}`; Cloudflare Email Routing receives mail for `inbox.agentmaple.com` and an Email Worker posts it, signed, to `/hooks/email`; Resend sends replies. `agentmaple.com` mail stays on Google (apex MX unchanged). Each step below creates an external account setting or DNS record; do them in order.

Generate the inbound mail secret once and keep it until §6.3 and §3 have both used it:

```
openssl rand -hex 32 > ~/email-hook-secret && chmod 600 ~/email-hook-secret
sudo systemd-creds encrypt --name=email-hook-secret ~/email-hook-secret /etc/credstore.encrypted/universal-inbox.email-hook-secret
```

### 6.1 Tunnel (`hooks.agentmaple.com`)

1. Install `cloudflared` from Cloudflare's Debian repository (https://pkg.cloudflare.com/index.html, "bookworm").
2. `cloudflared tunnel login`: open the printed URL and choose `agentmaple.com`.
3. `cloudflared tunnel create universal-inbox`: note the tunnel ID it prints.
4. `cloudflared tunnel route dns universal-inbox hooks.agentmaple.com`: creates the CNAME.
5. Install the config and the tunnel's credentials, then the service:
   ```
   sudo mkdir -p /etc/cloudflared
   sudo cp ~/.cloudflared/<TUNNEL_ID>.json /etc/cloudflared/
   sed 's/<TUNNEL_ID>/<the ID>/g' /home/jerry/workspace/universal-inbox/deploy/cloudflared/config.yml | sudo tee /etc/cloudflared/config.yml
   sudo cloudflared tunnel ingress validate --config /etc/cloudflared/config.yml
   sudo cloudflared service install
   ```
6. Check (with the Universal Inbox service running, §4):
   - `curl -s -o /dev/null -w '%{http_code}\n' -X POST https://hooks.agentmaple.com/hooks/slack` prints `401`.
   - `curl -s -o /dev/null -w '%{http_code}\n' https://hooks.agentmaple.com/` prints `404`: only the hook paths are published.

### 6.2 Email Routing (`inbox.agentmaple.com`)

1. Cloudflare dashboard → `agentmaple.com` → Email → Email Routing → Settings → Subdomains: add `inbox`. Cloudflare adds MX and SPF records for `inbox.agentmaple.com` only.
2. Check the apex is unchanged: `dig +short MX agentmaple.com` still prints `smtp.google.com.`

### 6.3 Email Worker

```
cd /home/jerry/workspace/universal-inbox/deploy/email-worker
npx wrangler login
npx wrangler deploy
npx wrangler secret put EMAIL_HOOK_SECRET    # paste the contents of ~/email-hook-secret
shred -u ~/email-hook-secret
```

Then Email Routing → Routing rules → Create address: `jeremy@inbox.agentmaple.com`, action "Send to a Worker", `universal-inbox-email`.

`tests/test_email_worker.py` runs this Worker under Node against the real hook, so the signing format is checked on every test run.

### 6.4 Gmail forwarding

1. Gmail (jeremy@agentmaple.com) → Settings → Forwarding and POP/IMAP → Add a forwarding address: `jeremy@inbox.agentmaple.com`.
2. Google sends a confirmation code to that address. It arrives in the console as a Todo Thread; open it, read the code, enter it in Gmail.
3. Choose "Forward a copy of incoming mail to jeremy@inbox.agentmaple.com" and "keep Gmail's copy in the Inbox".
4. If Workspace blocks forwarding to an external address, an admin adds a routing rule instead: Admin console → Apps → Google Workspace → Gmail → Routing → "Also deliver to" `jeremy@inbox.agentmaple.com`.

### 6.5 Resend (sending)

Resend → Domains → Add `agentmaple.com`. Add the DNS records Resend shows (DKIM `resend._domainkey`, and MX and SPF on its `send` subdomain) in Cloudflare, then Verify. The apex SPF and MX stay as they are. Create an API key with sending access and store it as `resend-api-key` (§3).

### 6.6 Slack

1. https://api.slack.com/apps → Create New App → From a manifest → paste `deploy/slack-app-manifest.yml`. Slack verifies the request URL against the running service (§4 and §6.1 first).
2. Store the signing secret as `slack-signing-secret` (§3), restart the service.
3. Install with `scripts/oauth_slack.py` (§3); it prints the team and user IDs for `universal-inbox init` (§2).
4. Invite the bot to each channel to cover (`/invite @Universal Inbox`) and add a Channel rule for it in the console (`9`, then `a`). DMs and group DMs need no rule.

### 6.7 Live check (Phase 6.4)

- The console's bottom bar shows `email ✓` and `slack ✓`.
- Send yourself a test email from another account and a test DM from a teammate; each appears as a Todo Thread within seconds.
- Reply to each from the console; the email reply appears in the other account's thread, and the Slack reply appears under your name.
## 7. Demo on synthetic data (Phase 6.3)

```
cd /home/jerry/workspace/universal-inbox
uv run python scripts/demo.py --step --keep
```

In a second pane, run the console command it prints. The demo runs the real hooks server and reply socket with demo secrets, sends signed fixture mail and Slack events over HTTP, and records replies at fake Resend and Slack endpoints; nothing leaves Workbox. It walks AS-1, AS-3, AS-11, AS-4, AS-6, AS-13, and AS-17 and prints PASS or FAIL for each. Without `--step --keep` it runs straight through, as `tests/test_demo.py` does.

## 8. Rollback

```
sudo systemctl disable --now universal-inbox
sudo rm /etc/systemd/system/universal-inbox.service
sudo systemctl daemon-reload
```

Then turn off the Gmail forwarding rule, the Email Routing rule, and the Slack app's event subscriptions, and stop the tunnel (`sudo systemctl disable --now cloudflared`). Gmail and Slack keep every message; the database stays at `~/.local/share/universal-inbox/inbox.db`. Remove the secrets with `sudo rm /etc/credstore.encrypted/universal-inbox.*` and the package with `uv tool uninstall universal-inbox`.
