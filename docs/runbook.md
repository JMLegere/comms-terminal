# Runbook

Sections 1–7 cover Workbox (Debian 12, systemd 252), Phase 6 of `docs/prd.md`. The managed Oracle installation uses the separate container and event-driven update path in section 8; reserve Workbox's package/reinstall/rollback commands for Workbox. Steps marked **You** need Jeremy (a browser, an account, or `sudo`); the agent can run the rest.

Secrets are staged as 0600 files in `~/.config/comms-terminal/`, read in-process, and never printed. `deploy/install.sh` encrypts each into systemd's credential store (`LoadCredentialEncrypted=`) and shreds the clear copy (NFR-5).

## 1. Package

```
uv tool install --force --reinstall /home/jerry/workspace/universal-inbox
```

Puts `comms-terminal` in `~/.local/bin`, which the Workbox unit runs. Re-run after pulling changes, then `sudo systemctl restart comms-terminal`. Keep `--reinstall`: the version stays 0.1.0, and without it uv reuses its cached build of the old code. On managed Oracle, use section 8 to preserve the update launcher and locking.

Deploy a release from a clean checkout of it, since the main checkout often holds other agents' uncommitted work: `uv tool install --force --reinstall <clean checkout>` (for example a worktree at `origin/dev` with no local changes).

## 2. Connect email (Slack later)

D3 (decision #102): Cloudflare Tunnel publishes `https://hooks.agentmaple.com/hooks/{email,slack}`; Cloudflare Email Routing receives `inbox.agentmaple.com` and the Email Worker (`deploy/email-worker/`) posts each message, signed, to `/hooks/email`; Resend sends replies. `agentmaple.com` mail stays on Google.

1. **You:** create a Cloudflare API token (dash.cloudflare.com → My Profile → API Tokens → Create Custom Token):
   - Account permissions: Cloudflare Tunnel Edit; Workers Scripts Edit.
   - Zone permissions, zone `agentmaple.com` only: Zone Read; DNS Edit; Zone Settings Edit; Email Routing Rules Edit.

   Save it on Workbox from your own terminal: `mkdir -p ~/.config/comms-terminal; and umask 077; and nano ~/.config/comms-terminal/cloudflare-token` (fish; in bash use `&&`).
2. **You:** in Resend, create an API key with sending access and save it the same way as `~/.config/comms-terminal/resend-api-key`. `agentmaple.com` must be a verified Resend domain (done 2026-10-12): Resend → Domains → Add domain → `agentmaple.com` (North Virginia, return path `send`, tracking off) → Manual setup; its three records are a DKIM TXT at `resend._domainkey` and DNS-only CNAMEs `rsend` and `send` to `*.forge.rmta.net`. The apex MX and SPF are untouched.
3. Generate the inbound mail secret and create the email Connection:
   ```
   bash -c 'umask 077; openssl rand -hex 32 > ~/.config/comms-terminal/email-hook-secret'
   comms-terminal init --gmail jeremy@agentmaple.com
   ```
4. Cloudflare: plan, then apply (the tunnel, `hooks.agentmaple.com`, Email Routing for `inbox.agentmaple.com`, the Worker and its secret, the routing rule):
   ```
   uv run python scripts/cloudflare_setup.py --token-file ~/.config/comms-terminal/cloudflare-token
   uv run python scripts/cloudflare_setup.py --token-file ~/.config/comms-terminal/cloudflare-token \
       --email-secret-file ~/.config/comms-terminal/email-hook-secret --apply
   ```
5. **You:** `sudo bash /home/jerry/workspace/universal-inbox/deploy/install.sh`. It stores the credentials, installs and starts `comms-terminal.service`, and installs the `cloudflared` connector with the tunnel token.
6. Check:
   - `systemctl is-active comms-terminal cloudflared` prints `active` twice.
   - `curl -s -o /dev/null -w '%{http_code}\n' -X POST https://hooks.agentmaple.com/hooks/slack` prints `503` (Slack not connected yet; the request reached the service).
   - `curl -s -o /dev/null -w '%{http_code}\n' https://hooks.agentmaple.com/` prints `404`: only the hook paths are public.
   - `dig +short MX agentmaple.com` still prints `smtp.google.com.`
7. **You:** Gmail forwarding. Gmail → Settings → Forwarding and POP/IMAP → Add a forwarding address: `jeremy@inbox.agentmaple.com`. Google's confirmation code arrives in the console as a Todo Thread; enter it in Gmail, then choose "Forward a copy … and keep Gmail's copy in the Inbox". If Workspace blocks forwarding to an external address, an admin adds Admin console → Gmail → Routing → "Also deliver to" `jeremy@inbox.agentmaple.com` instead.
8. Live check: send yourself an email from another account; it appears as a Todo Thread within seconds. Reply from the console; the reply arrives in the other account's thread.

## 2a. Resend sent receipts — separately authorized rollout

**Source implementation: 2026-10-16.** Signed receipt handling, migration 13, optional credential installation, and the exact tunnel allowlist are implemented in source. Verification, package release, live migration, secret provisioning, endpoint subscription, and tunnel changes require their own recorded gates. This section makes no claim that `https://hooks.agentmaple.com/hooks/resend` is configured or receiving events.

Gmail sends retain Resend's accepted email UUID as an immutable `<uuid@resend>` Activity ID. An authenticated `email.sent` receipt supplies its SMTP Message-ID. The service commits this minimal mapping before acknowledgement, then the existing wake loop reconciles exact references before attachment downloads. Receipt, outgoing Activity, and incoming reply can arrive in any order; periodic wake recovers a lost kick or restart. A receipt alone creates no Thread or Activity. Temporary split Threads can remain until the receipt arrives.

New outgoing Gmail Activities may also store internal `data.seen_through_activity_id`: the global Activity high-water mark seen before calling the provider. Delayed/restarted exact receipt reconciliation uses that durable boundary to preserve complete current Todo/Investigating work from inbound Activities recorded afterward; later own sends and newer Done/Paused/Ignored triage keep normal precedence. This needs no operator setting. Legacy rows without a valid checkpoint retain ordinary triage/manual-repair limits; never rewrite existing Activities to add one.

Replying to a latest message whose SMTP identity remains pending is refused before dispatch; text and staged files stay available. Earlier unresolved IDs are omitted from References. Acceptance with a missing usable UUID is an unknown outcome; retain work and investigate delivery, **never automatically resend**. Keep the sender API key **sending-only**: this flow needs no email retrieval permission.

### Admission and setup

Obtain Jeremy's separate approval for the exact live target and operations before each provider, credential, tunnel, migration, service, or real-email action:

1. Review the clean release and current schema. Take and verify an approved consistent database backup before migration 13; preserve raw mail, attachment storage, aliases, and saved drafts. Confirm each Gmail Connection has usable `self_id` and `external_id`; other Platforms can coexist. With several, a receipt binds to the one whose address is its `from`; no Gmail Connection, or no match, returns 503. Ownership comes from the local Connections; `from` only picks among them.
   Upgrade both the service and console code. Preserve any unsent reply text before restarting an existing console: open reply text is held in that process's memory, so a database backup alone does not capture it. Keep staged-file ownership with its selected original draft; do not silently restart an active editor.
2. In the authorized Resend account, create the endpoint for `https://hooks.agentmaple.com/hooks/resend`, subscribed to **`email.sent`**. Stage its separate `whsec_…` signing secret as `~/.config/comms-terminal/resend-webhook-secret` (0600), keeping values out of arguments, history, and logs. Preserve the existing forwarding secret and sending-only key.
3. Review all staged credentials, then use the approved installer/package steps. `deploy/install.sh` encrypts the optional secret, shreds its staged copy, and installs `comms-terminal.service.d/resend.conf` only when its encrypted credential exists. The installer also restarts services and processes other staged credentials: authorize that exact scope. Missing/invalid receipt configuration leaves `/hooks/resend` at 503 while existing hooks and other Platforms retain their behavior.
4. Refresh the **exact current** Cloudflare tunnel configuration. Preview a scoped replacement changing only the intended hook rule to `^/hooks/(email|slack|quo|resend)$`, preserving all other fields, ingress entries, and private-route protections. Do **not** run `scripts/cloudflare_setup.py --apply` to add this route to an existing deployment: it replaces ingress and also changes DNS, Worker, secrets, and routing rules. The source allowlist is a setup default, not evidence of live state.
5. Verify the exact public URL after authorized setup: unsigned traffic returns 401; a valid signed receipt returns 204 only after its mapping survives a new database connection. Replay an identical receipt, exercise restart/lost-kick recovery, and confirm conflicting mappings return 409 while preserving the first value. Verify provider retry/deadline/replay behavior in this account, including fresh delivery signatures inside the 300-second window. A permanently missing event needs operator recovery.
6. Under separate real-email approval, send a synthetic initial Draft, receive its signed receipt, and obtain a controlled reply; repeat for an ordinary reply and reordered delivery. Inspect actual outbound threading headers and the rendered console: one canonical conversation, both old Thread IDs usable, immutable messages/files present, latest triage retained, and selected original reply draft's text/files preserved or cleared only by its own successful send. Keep private reply/file/upload routes inaccessible publicly.

The receipt reader requires one decimal Content-Length, rejects Transfer-Encoding and duplicate lengths, caps bodies at 64 KiB, and imposes a five-second total read deadline. Statuses: missing length 411; invalid/truncated framing or authenticated payload 400; oversized 413; timeout 408; invalid signature 401; missing/invalid config, ambiguous binding, or storage failure 503; conflicting mapping 409; committed new/identical receipt or unrelated authenticated event 204. Unauthenticated receipt traffic changes neither Connection health nor stored mail. Responses/logs contain no receipt payload or signing material.

### Recovery and receipt-specific rollback

- Historical repair needs authoritative UUID↔SMTP-ID proof and approval for the **exact current** mapping and affected Threads. The recorded incident concerns Threads 299/300, but refresh their triage, Cases, aliases, drafts, and files before previewing any repair. Subject/body similarity supplies no proof. Older replies whose provider UUID was discarded require separate manual evidence. A stored orphan receipt cannot establish Draft ownership after a crash between provider acceptance and local recording.
- A receipt rollback should disable only the new provider subscription/routing and optional receipt drop-in under explicit approval; preserve unrelated hook routes, credentials, and settings. Keep mappings, Activity/raw history, aliases, and drafts intact. Use a rollback build that accepts schema 13: the older schema-12 build rejects a migrated database.
- Never lower `user_version`, delete receipt rows, or automatically unmerge to force an older build to start. Restoring an earlier consistent backup requires separate approval, an explicit account of concurrent changes it would lose, and matching code/data/storage. Full uninstall in §7 is broader than receipt rollback.

## 2b. Connect another email address

Any number of Google Workspace mailboxes can be email Connections (schema 23). Each one has an Inbound address, `<its address with @ as .>@inbox.agentmaple.com`; the hook files mail by the envelope recipient (`X-Inbox-To`), and the first Gmail Connection takes anything no Connection claims. Replies and Sequence Drafts send through the same Resend key, from the Connection's own address. Example for `jeremy@agentmaple.ai`:

1. **You:** Resend → Domains → Add domain → `agentmaple.ai`, set up like `agentmaple.com` (§2 step 2); add its DKIM TXT and the `send`/`rsend` records at the domain's DNS host and wait for Verified. Resend refuses to send from an unverified domain.
2. On Workbox: `ssh oracle 'sudo docker exec comms-terminal comms-terminal init --gmail jeremy@agentmaple.ai'`. It prints the Inbound address, `jeremy.agentmaple.ai@inbox.agentmaple.com`.
3. Cloudflare: add one Email Routing rule on `agentmaple.com`, that Inbound address → the `comms-terminal-email` Worker. Use a scoped `POST /zones/<zone>/email/routing/rules`; `scripts/cloudflare_setup.py --apply` changes much more (§2a step 4).
4. **You:** in that mailbox, forward a copy of all mail to the Inbound address (§2 step 7). Google's confirmation arrives as a Todo Thread under the new Connection.
5. Point a Sequence at it: Sequences page, select the Sequence, `f` (sends from). New Drafts send from it; existing Drafts keep their Connection.

## 3a. Connect Fastmail

Fastmail is a second email Connection (decisions #111, #113). Incoming mail is event-driven like Gmail: a Fastmail rule sends a copy of every message to `fastmail@inbox.agentmaple.com`, Email Routing hands it to the same Worker, and the hook files it under Fastmail by that address. Jeremy's Basic plan refuses JMAP, IMAP, and API tokens, so replies (and a 10-minute poll of Sent for replies written in Fastmail itself) go through Fastmail's MCP server, signed in as a connected app the way omp is.

1. **You**, on Mani (browser): `python3 scripts/oauth_mcp.py --resource mcp --out ~/.config/comms-terminal/fastmail-oauth.json`. On Fastmail's consent page tick read, write, **and** send (the scope must list `mcp-read mcp-action mcp-send`). Copy the file to Workbox's `~/.config/comms-terminal/` (0600) and shred the Mani copy.
2. On Workbox: `comms-terminal init --fastmail legere@fastmail.com`. Optional one-time import: `comms-terminal fastmail-import --days 30` (Inbox and Sent, every Thread left in Todo).
3. **You:** `sudo bash deploy/install.sh`: it stores `fastmail-oauth` and adds `comms-terminal.service.d/fastmail.conf`.
4. Cloudflare: `scripts/cloudflare_setup.py` adds the routing rule `fastmail@inbox.agentmaple.com` → Worker (plan, then `--apply`).
5. **You**, Fastmail → Settings → Filters & Rules → Create rule: *All messages* → *Send a copy to* `fastmail@inbox.agentmaple.com`, *Continue to apply other rules*; drag it to the top (done 2026-10-12, rule "Comms Terminal"). Spam and blocked senders are not copied.

The refresh token rotates, and Fastmail revokes the sign-in when an old one is reused. Exactly one process may hold it: the service keeps the current one in `~/.local/share/comms-terminal/fastmail-oauth.json` (0600), which wins over the stored credential. Never run a second copy against the same token; if it is revoked, the Connection shows the error and step 1 is repeated.

## 3b. Connect Quo (SMS)

Quo is the SMS Platform (`.agents/discovery/2026-10-12-sms.md`): Jeremy's Work number (506) 832-4154 (Quo phone-number ID `PN2ppcGyMM`). Texts arrive through a signed Quo webhook (Standard Webhooks, `Quo-Api-Version: 2026-03-30`) at `/hooks/quo`; replies from the console go out through Quo's send API; texts Jeremy sends in the Quo app arrive as `message.delivered` and answer the Thread. Group texts are dropped (Quo fires no event for a group text sent from the app). Done 2026-10-12.

1. **You:** save the Quo API key without echoing it: `install -m 600 /dev/null ~/.config/comms-terminal/quo-api-key; and read -s -P 'Quo API key: ' k; and printf %s $k > ~/.config/comms-terminal/quo-api-key; and set -e k`.
2. Create the webhook through the API (`POST https://api.quo.com/webhooks`, header `Quo-Api-Version: 2026-03-30`): URL `https://hooks.agentmaple.com/hooks/quo`, events `message.received`, `message.delivered`, `message.failed`, `message.undelivered`, `call.completed`, `call.transcript.completed`, `call.voicemail.completed`, `resourceIds: ["PN2ppcGyMM"]`. Calls become Phone calls (`.agents/discovery/2026-10-15-phone-calls.md`); on the live webhook, send the full seven-event list with `PATCH https://api.quo.com/webhooks/43687` (it updates only the fields sent). Write the returned `data.key` (`whsec_…`) straight to `~/.config/comms-terminal/quo-webhook-secret` (0600); it is shown once (rotate with `POST /webhooks/{id}/rotate`). The live webhook is ID `43687`, "Comms Terminal".
3. Cloudflare: Quo originally used `^/hooks/(email|slack|quo)$`. The current setup-source default includes Resend as `^/hooks/(email|slack|quo|resend)$`; refresh live configuration before a scoped change (§2a).
4. **You:** `sudo bash deploy/install.sh`: it stores `quo-webhook-secret` and `quo-api-key` and adds `comms-terminal.service.d/quo.conf`.
5. On Workbox: `comms-terminal init --quo +15068324154`. The service makes the Quo sender on first reply; no restart is needed.

Quo restricts the number from texting US numbers until US texting registration (10DLC) is done; Canadian numbers work.

## 3c. Connect Tana (context)

Tana is a read-only Context source (FR-13a, decision #122): Thread, Person, Organization, and Case pages show what Tana knows about their people and Organizations. The service signs in to Tana's MCP server (`https://home.tana.inc/mcp`) and the console asks it over the reply socket (`POST /tana`).

1. On Workbox: `python3 scripts/oauth_mcp.py --resource tana --out ~/.config/comms-terminal/tana-oauth.json`. **You** open the printed link in Mani's browser and approve "Comms Terminal". The browser then fails to load `http://127.0.0.1:8733/callback?state=…&code=…`; deliver that exact URL to the waiting script on Workbox (`curl` it there), or run `ssh -N -L 8733:127.0.0.1:8733 workbox` on Mani first so the redirect lands by itself. Copy the long link from a file (`xdg-open` it), not the terminal: a wrapped copy gets "invalid parameters".
2. **You:** `sudo bash deploy/install.sh`: it stores `tana-oauth`, adds `comms-terminal.service.d/tana.conf`, and restarts the service.
3. Check: open a Thread with someone in Tana; its details column ends with a Tana section, and the bottom bar shows the Tana icon with a check. A failed fetch shows "Tana unavailable: …" and "Tana broken".

As with Fastmail, the service keeps a rotated refresh token in `~/.local/share/comms-terminal/tana-oauth.json` (0600); only the service may hold it.

Tana links in the console open with Ctrl+Shift+click (the console catches a plain click). To land in the Tana desktop app instead of the browser, choose **Open always** on Tana's "Open in Tana" chooser the first time; Tana's Settings → Preferences → Tana links resets it.

## 3d. Connect Attio (context)

Attio is a read-only Context source (FR-13b, decision #123): the same pages show what Attio knows about their people and Organizations, above Tana. The service reads Attio's REST API with a read-only workspace access token and the console asks it over the reply socket (`POST /attio`).

1. **You** (or the agent, in your browser): Attio → Settings → Developers → New access token "Comms Terminal", every scope Disabled except Read on User Management, Records, Object Configuration, List Entries, List Configuration, and Tasks.
2. Save it on Workbox as `~/.config/comms-terminal/attio-token` (0600), keeping it out of shells' history: `read -s -P 'Attio token: ' t; and printf %s $t > ~/.config/comms-terminal/attio-token; and chmod 600 ~/.config/comms-terminal/attio-token; set -e t`.
3. **You:** `sudo bash deploy/install.sh`: it stores `attio-token`, adds `comms-terminal.service.d/attio.conf`, and restarts the service.
4. Check: the rail's context section lists Attio with a check, and a Thread with someone in Attio shows an Attio section above Tana's.

## 3e. Connect Linear (context, later authorization)

Linear is a read-only Context source (FR-13c, decision #125), after Attio and Tana on Thread, Person, Organization, and Case pages. The service holds the key and queries Linear's GraphQL API; the console sends only email/domain arrays to the private Unix socket (`POST /linear`). The service derives unique full Person names and Organization names from the local database for each request. Names shared by local records, address/domain placeholders, and partial Person names fall back to exact email/domain evidence.

**Separately authorized connection step:** code publication and existing Workbox package/service deployment are complete under Jeremy's "ship it" ([receipt](design/linear-context.md#deployment-receipt--2026-10-15)). Linear was connected on 2026-10-16 ([receipt](design/linear-context.md#connection-receipt--2026-10-16)); use these steps to rotate or replace the key. Obtain Jeremy's approval for the exact workspace, required teams, key creation/connection, and credential installation before performing these steps. Workspace policy, effective restricted-key Customer Request access, and expiration behavior still require live verification.

1. **You**, in the approved Linear workspace: create a Jeremy-owned personal API key named "Comms Terminal", with **Read** as its only permission and access restricted to the approved required teams ([Linear key permissions](https://linear.app/docs/api-and-webhooks)). Stop if workspace policy prevents that scope.
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
   path = Path.home() / ".config/comms-terminal/linear-token"
   path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
   fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
   os.fchmod(fd, 0o600)
   with os.fdopen(fd, "w") as staged:
       staged.write(token)
   PY
   ```
3. Install the approved build using §1, then **You:** `sudo bash deploy/install.sh`. The installer encrypts `linear-token`, shreds its staged copy, adds `comms-terminal.service.d/linear.conf`, and restarts the service. Review other staged credentials first: this installer also processes them.
4. Verify workspace identity and effective Read/team scope with the authorized key, including Customer Request coverage. Open representative Thread, Person, Organization, and Case pages: Linear follows Tana, shows every matching nonterminal issue (including backlog and triage), match reasons, relevant Request excerpts, and working **Open in Linear** links. Check separate rail/footer Linear health; test five-minute cache freshness.

The local route defaults omitted arrays to `[]`; explicit `null`, non-array values, and non-string entries return 400. An unconnected source returns 503, connected empty keys return `{"issues":[]}` without a provider call, and adapter failures return a safe 502 error. Linear reads leave Connections, Threads, Activities, and Inbox Status unchanged. The socket remains private; public hook routes are unchanged.

## 3f. Attachments and the file server

Attachments (#127–#137): received files are copied into `~/.local/share/comms-terminal/files/<sha256>` (0600). Gmail parts are stored at ingest; Slack and Quo files download on the service's wake loop, up to 5 tries, after which the chip says "not copied" and keeps its Platform link. Files over 100 MB stay link-only. A file expires a year after its newest Attachment unless it is staged (checked daily).

- **File server**: on Oracle the container runs `serve … --files-host 0.0.0.0` (`--files-port` defaults to 8789), published on the host's loopback only (`127.0.0.1:8789`, `deploy/container_runtime.py`); the tunnel carries the hook paths alone. The laptop console's SSH master forwards its own `127.0.0.1:8789` there (`remote.FILES_FORWARD`), so file links are `http://127.0.0.1:8789/f/<sha>.<mac>/<name>` and open only while the console runs. They never expire; upload pages `/up/…` expire after 1 hour. The console builds links with `--files-host`/`--files-port`, defaulting to 127.0.0.1:8789. Without `--files-host` on `serve`, the listener stays off and chips fall back to Platform links.
- **Away from home**: links reach Workbox wherever Mani's WireGuard connects to the home network; Jeremy chose that over a public route (2026-10-15). A public `/f` and `/up` route needs Cloudflare Zero Trust Access enabled first (the account reports "Access is not enabled"), then an Access app for Jeremy's address and a tunnel path.
- **Secret**: `~/.local/share/comms-terminal/files-secret` (0600), made on first use. Rotate it with `rm ~/.local/share/comms-terminal/files-secret` and `sudo systemctl restart comms-terminal`; every earlier file and upload link then answers 404, and the console builds new links from the new secret.
- **Backfill**: after the first deploy, `comms-terminal attachments-backfill` copies the Attachments of raw emails stored before this release. It repeats safely and prints counts only.
- Check: on Oracle, `curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8789/f/x/y` prints `404`; on Mani the same command prints `404` while the console runs; a chip opens its file on Mani with Ctrl+Shift+click.

## 3g. Connect Google Calendar (Event provider)

The Calendar (FR-13d, ADR 0005) reads the primary calendar's **Secret address in iCal format** (Google Calendar → Settings → `jeremy@agentmaple.com` → Integrate calendar). The address is a bearer secret: anyone holding it reads the calendar. It lives only as the optional `google-calendar-ics` credential; never paste it in chat, logs, or the database.

- **Oracle**: the unit's `LoadCredentialEncrypted=google-calendar-ics:comms-terminal.google-calendar-ics` and `container_runtime.py`'s optional list admit it. Install the address with `systemd-creds encrypt --name=google-calendar-ics - /etc/credstore.encrypted/comms-terminal.google-calendar-ics` (stdin, mode 0600), then `systemctl daemon-reload` and `systemctl restart comms-terminal`. Without the credential the service runs without the Calendar loop and the Calendar says no Event provider is connected.
- **Check**: the journal shows `calendar changed` once; `comms-terminal events` lists this week. Healthy fetches write nothing, so a quiet calendar sends the laptop console no new copy.
- **Rotate**: Reset the secret address in Google, encrypt the new one over the old file, and restart. Until then fetches fail (404) and the Calendar shows Google Calendar broken after three tries.
- **Meetings** (ADR 0007): with the credential, the service creates the `gcal` Connection (`Google Calendar` under connections) the first time it starts, and that moment is go-live: only Events first seen after it become Meeting Threads, so nothing is backfilled. A Meeting Thread needs an attendee outside Agent Maple. The journal shows `meetings recorded: N` after a calendar change that booked, cancelled, or declined one; held occurrences are recorded by the minute's wake pass. Removing the credential stops new Meeting Activities; the Connection and its Threads stay.

## 3. Connect Slack

1. **You:** https://api.slack.com/apps → Create New App → From a manifest → paste `deploy/slack-app-manifest.yml`. Save the signing secret (Basic Information) as `~/.config/comms-terminal/slack-signing-secret`.
2. **You:** install: `SLACK_CLIENT_SECRET=… uv run python scripts/oauth_slack.py --client-id <id> --redirect-uri https://hooks.agentmaple.com/slack/oauth --out ~/.config/comms-terminal/slack-tokens.json`. It prints the team and user IDs and never prints a token.
3. Stage the user token and create the Connection:
   ```
   python3 -c 'import json, os, pathlib; os.umask(0o077); p = pathlib.Path.home() / ".config/comms-terminal"; (p / "slack-user-token").write_text(json.load(open(p / "slack-tokens.json"))["user_token"])'
   shred -u ~/.config/comms-terminal/slack-tokens.json
   comms-terminal init --slack-team <T…> --slack-user <U…>
   ```
   User scopes include `files:read` and `files:write` for Attachments (re-consented 2026-10-15). The 2026-10-25 source declarations additionally request only `reactions:read` and `reactions:write` as user scopes; existing user/bot scopes and event subscriptions are preserved. These declarations establish no installed grant. Any reinstall (Slack app → OAuth & Permissions → Reinstall), interactive consent, credential staging or step-4 installation requires separate explicit approval for the intended Connection and host.
4. **You:** `sudo bash deploy/install.sh` again: it stores both Slack secrets and adds `comms-terminal.service.d/slack.conf`.
5. **You:** in the Slack app's Event Subscriptions, retry the request URL (it verifies now). Invite the bot to each channel to cover whole (`/invite @Comms Terminal`) and add a Channel rule in the console (`9`, then `a`). DMs and group DMs need no rule. From every other channel Jeremy is in, only messages that mention him and replies in threads he has posted in (or that are already in the inbox) come in (#114).
6. **You:** turn off Slack's email notifications (Slack → Preferences → Notifications → email), or they arrive through Gmail as duplicate email threads.

### Slack reaction access and recovery

**Local source implementation in progress, 2026-10-25; integrated behavior/rendered evidence and real reaction access remain unverified.** `scripts/oauth_slack.py` and `deploy/slack-app-manifest.yml` declare `reactions:read`/`reactions:write` for Jeremy's user identity. Editing those files neither grants permission nor authorizes a source request. Keep every bot scope and event subscription unchanged; reaction snapshots do not require reaction events, message backfill, or polling.

**Separate admission gates:**

1. Finish synthetic fake-provider/private-socket/keyboard checks and inspect the implemented Thread/Case UI at 187×50 and 80×58. Six accepted design previews establish their reviewed pixels; final behavior and the unavailable/refused/unknown/failed-count-refresh presentations still require their own proof and visual acceptance.
2. Before any real identity/reaction read, obtain separately approved access for the exact intended Slack Connection/workspace/user. Verify effective installed grants independently of source declarations. The service must match authenticated Slack team/user to `Connection.external_id`/`self_id`; the existing global token is not evidence that an arbitrary Connection is safe. Missing/mismatched identity, unavailable grants, or missing captured target keep reactions unavailable.
3. Obtain Jeremy's interactive approval for any app reinstall/scope grant, credential change or login, with provider safety checks at point of risk. Use the approved host-specific credential/release path; source work does not authorize running OAuth, staging tokens, invoking the broad installer, restarting Oracle, or changing public ingress. Preserve the existing app, credentials, unrelated scopes and subscriptions.
4. Separately authorize each exact live message/Connection, operation and emoji before a controlled source change. Read back its exact current membership afterward; any correction/removal is a separately chosen action, never an automatic rollback. Source grants/access, exact live actions, deployment/release, and Jeremy's five-opportunity usefulness trial remain separate approvals.

The private service capability is `POST /slack-reactions` (**Slack Reactions**; `docs/design/api-and-database.md` § Slack reaction interface). It uses captured original MESSAGE Activity IDs and a derived immutable-Activity revision, not caller-supplied Slack channel/timestamp or a default account. The console invokes it off the event loop over the current socket/SSH relay; it remains outside public hooks and the remote optimistic `/call` queue. Offline/code mismatch or an older service without the route disables the capability.

**Use/recovery contract** (`docs/design/console.md` § Slack reactions): `v` selects in the active Thread/selected Case card; `j/k` or arrows move through expanded messages; a fresh own check enables `1/2/3`. Checked choices remove only Jeremy's membership. Entry/Esc write nothing; early digits are discarded. F5 explicitly refreshes snapshots; ordinary repaint/timer/replica updates do not. Counts cover all participants, while ✓ and the blue outline mark own membership separately.

- **Unavailable read**: membership is unknown, never assumed absent or zero; `c Check` and the existing `o Source link` are read-only recovery/fallback. Scope gaps require approved human consent rather than automatic sign-in.
- **Refused change**: show safe `Not added`/`Not removed` reason. Fix the cause before a new gesture; no automatic retry, including rate-limit refusal.
- **Unknown change**: retain the original message/Connection pair. Re-enter React and explicitly select that original message for a read-only check; reading another initial/default target leaves the unknown result unresolved. Missing original target or changed identity fails closed. A successful read establishes current membership, not causality, and never resends the earlier change.
- **Confirmed change with failed count refresh**: retain the confirmed own result and expose unavailable/stale totals. Never turn that write into unknown, display a misleading zero, or resend.
- **Known edit/deletion or stale completion**: require `Message changed — review` for an edited revision; disable deleted/lost-access targets. Check returned target/revision/generation before applying results. Restart clears session readiness and requires a fresh check/new gesture; no reaction is replayed.

Reaction operations change neither Thread Status/detail, Case membership, Task/Enrollment state nor the message ledger. The narrow FR-15 exception covers Jeremy's explicit own Slack add/remove only; message editing/deletion, conversation archiving, read-marker changes and automated/agent reactions remain prohibited.

## 4. Service operations

- Status: `systemctl status comms-terminal`; logs: `journalctl -u comms-terminal -n 50` (no request bodies or tokens are logged).
- Restart after an update or a credential change: `sudo systemctl restart comms-terminal`.
- Rotate a secret: stage the new value in `~/.config/comms-terminal/<name>` and re-run `sudo bash deploy/install.sh`.
- Until Slack is connected, `/hooks/slack` answers 503 and Slack replies are refused.

## 5. The console

```
ssh workbox -t comms-terminal
```

It finds the service's socket at `/run/comms-terminal/inbox.sock` without flags. Press `?` for keys.

## 6. Demo on synthetic data (Phase 6.3)

```
cd /home/jerry/workspace/universal-inbox
uv run python scripts/demo.py --step --keep
```

In a second pane, run the console command it prints. The demo runs the real hooks server and reply socket with demo secrets, sends signed fixture mail and Slack events over HTTP, and records replies at fake Resend and Slack endpoints; nothing leaves Workbox. It walks AS-1, AS-3, AS-11, AS-4, AS-6, AS-13, and AS-17 and prints PASS or FAIL for each.

## 7. Rollback

For the additive receipt release, follow schema-aware, receipt-specific rollback in §2a. Commands below are the broader full-service uninstall and require separate approval.

```
sudo systemctl disable --now comms-terminal cloudflared
sudo rm -r /etc/systemd/system/comms-terminal.service /etc/systemd/system/comms-terminal.service.d
sudo systemctl daemon-reload
```

Then turn off Gmail forwarding, delete the Email Routing rule for `jeremy@inbox.agentmaple.com`, and turn off the Slack app's event subscriptions. For Quo, delete webhook `43687` in Quo (or `DELETE /webhooks/43687`). Gmail and Slack keep every message; the database stays at `~/.local/share/comms-terminal/inbox.db`. Remove the secrets with `sudo rm /etc/credstore.encrypted/comms-terminal.*` and the package with `uv tool uninstall comms-terminal`.

## 8. Oracle automatic updates

Oracle runs Comms Terminal as UID/GID10001 in a Docker container with durable home `/var/lib/comms-terminal/home`, a private reply socket, and loopback-published HTTP8787. GitHub pushes to this repository's `main` reach an independent signed-webhook receiver through a dedicated Cloudflare Tunnel. A persisted notification triggers `comms-terminal-update.path` and its fixed updater; GitHub delivery is the sole source of update work, with no timer, polling fallback, unconditional startup fetch, or GitHub Actions dependency.

- Push committed application changes to `main`; the updater fetches the authoritative branch head, builds an immutable image with frozen dependencies, validates it against a disposable database backup, then coordinates the version switch.
- Use the installed `comms-terminal` launcher for Oracle. It starts an independent managed frontend holding the shared update lock. Before switching the backend, the updater blocks frontend launches, asks open consoles to save drafts and release database ownership, and waits up to 60 seconds for acknowledgment. Successful quiescence leaves the old frontend read-only with a restart-required message; reopen the app to load the new UI. A failed draft save retains its lock and makes the update fail explicitly.
- The installed app unit is `comms-terminal.service`, using the `deploy/comms-terminal-container.service` template. Its eight required credentials and three optional bindings (independent Tana, Linear, Resend receipts) enter as read-only runtime files. The updater preserves daemon activity state and accepted notifications.
- Status: `systemctl status comms-terminal-deploy comms-terminal-update.path comms-terminal-update`. Private root-only phase/checkpoint records are under `/var/lib/comms-terminal-deploy/status/`; logs contain fixed failure labels.
- If GitHub failed to deliver, use repository Settings → Webhooks → Recent Deliveries → Redeliver. A fresh delivery supplies the update trigger.
- Keep the receiver/updater independent of the app's release environment. Runtime/image code is in `Dockerfile` and `deploy/{container_runtime,github_deploy}.py`; updater templates are `deploy/comms-terminal-{deploy.service,update.service,update.path}`. The two connector units are `deploy/cloudflared-comms-terminal-{deploy,ingest}.service`. The native updater's launcher and `release.conf` remain available for native installations.
- Routine Oracle releases use the managed updater. Reserve section 1's `uv tool install` and `deploy/install.sh` for Workbox; preserve Oracle's managed launcher. Host-specific configuration stays in root-owned `/etc/comms-terminal-deploy/config.json`, with credential files kept private.
- Recovery restores a SQLite checkpoint only while writers remain blocked and before the first new writer is admitted; after admission, preserve current data and resolve the release forward. Separately authorized live database restoration/migration must use the same native quiesce and writer-gate protocol. Incomplete recovery retains the console launch barrier.

## 9. Comms Terminal on the laptop (local console)

Comms Terminal runs the console on Jeremy's laptop against a copy of the Oracle ARM inbox, so keys never wait on the network ([ADR 0004](adr/0004-local-console-over-server-replica.md)). The ARM service stays the only writer.

- Install the launcher: `install -m 755 deploy/comms-terminal-local ~/.local/bin/comms-terminal-local`. The desktop entry `~/.local/share/applications/comms-terminal.desktop` runs it in Ghostty (`-e ~/.local/bin/comms-terminal-local`).
- The copy lives in `~/.local/share/comms-terminal/`: `inbox.db` and `raw/` (about 30 MB). Attachment files and `files-secret` stay on the service; Attachment links come signed in each snapshot.
- Each launch starts one SSH master connection (the `oracle-dev` key, strict host key) and closes it on quit. Each request runs a short relay under the administrator's `sudo` into the container's socket. The service answers `POST /call` (one allowlisted write), `/sync` (a database snapshot once it changes), and `/raw` (raw messages by name); see `src/comms_terminal/remote.py`.
- Writes paint at once and go to the service in order; a refused write reverts the copy to the service's state with a toast. Writes that create rows (new Case, Draft, Sequence step, staged file, Task completion) wait for the service's id, about 150 ms.
- Offline, or while the copy's schema or the service's build differ from the installed console, the status bar shows READ-ONLY and writes are refused; an open reply's text stays in the console and is saved when the service answers again.
- Slack reactions (local implementation in progress) bypass that optimistic write queue: one explicit request uses private `/slack-reactions`, with honest confirmed/refused/unknown feedback. Offline/build mismatch prevents a change; reconnect never replays it. Snapshots and recovery require separately approved real access as in §3, Slack reaction access and recovery.
- Updates: the service updates from `main` as in section 8. When a sync reports a new build, the console quits with exit code 75 once no write is on its way and no read-only reply text is held; the launcher installs that commit from an export of the local checkout (`COMMS_TERMINAL_REPO`, default `~/Workspace/comms-terminal`) with `uv tool install` into `~/.local/share/comms-terminal/`, then opens the console again. A held reply keeps the console open and READ-ONLY until it is saved or closed.
- Back to the SSH console: restore the desktop entry's previous `Exec` (kept as a comment in the file). The service routes are additive; the SSH console needs nothing else.
