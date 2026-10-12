# Console design

Status: **Approved** by Jeremy, 2026-10-12 (decisions #71–#75). Replaces the web console in `docs/design/api-and-database.md` §6. Mock: a Textual prototype Jeremy tried on 2026-10-12 (session scratchpad, not in the repo).

## 1. Form

A terminal app (Textual) that Jeremy runs over SSH on Workbox: `ssh workbox -t universal-inbox`.

- **Decision #71**: terminal UI instead of the web console. Jeremy works in terminals (herdr); SSH replaces the private web port, so D2 is resolved as "SSH to Workbox".
- **Decision #72**: Textual as the toolkit. It is the one runtime dependency beyond the standard library; it gives panes, key bindings, and a headless test driver.

### Processes

```
Workbox
  universal-inbox serve   systemd service; holds the credentials
    /hooks/email, /hooks/slack   public, verified
    wake() every minute
    local socket: POST /reply    jerry-only Unix socket
  universal-inbox         Textual app, run by Jeremy over SSH
    reads SQLite; calls inbox.act, block, add_identity, remove_identity directly
    replies through the service's local socket
```

- **Decision #73**: replies go through the service. The service keeps the Resend key and Slack token as encrypted systemd credentials; the app never holds a token (NFR-5).
  - Socket: `$XDG_RUNTIME_DIR/universal-inbox.sock` (mode 0600, owned by `jerry`).
  - `POST /reply` with JSON `{"thread_id", "text", "to", "cc"}` → `200 {"status", "status_detail"}`, or `502 {"error"}` on `SendFailed`.
- Both processes open the same SQLite file in WAL mode with a 5-second busy timeout.
- The app refreshes when `PRAGMA data_version` changes (checked every 2 seconds), so new mail and Slack messages appear without a keypress.

## 2. Look

**Decision #74**: dark only, matcha-like, with Plain's status details as glyphs.

- **Colors**:
  - background `#1e222a`
  - raised surface `#262b35`
  - rule `#3a404c`
  - text `#d6dae2`
  - muted `#7c8594`
  - accent `#2bd48f` (selection, titles, health OK)
  - error `#f7768e` (health BROKEN, send failure)
- **Type**: the terminal's monospace font. Bold for names and titles; muted for metadata.
- **Status glyphs** (Status detail → glyph, color):
  - `CREATED` → `✸` blue `#7aa2f7` (Needs first response)
  - `NEW_REPLY` → `↩` amber `#e0af68` (Needs next response)
  - `IN_PROGRESS` → `◔` violet `#bb9af7` (Investigating)
  - `WAITING_FOR_CUSTOMER` → `◷` muted (Waiting for them)
  - `WAITING_FOR_DURATION`, `WAITING_INDEFINITELY` → `⏸` muted (Paused for later)
  - `DONE_MANUALLY_SET` → `✓` accent (Done)
  - `IGNORED` → `⊘` dim (Ignored)
- Every glyph appears with its label in the Thread header and the legend line, so meaning never rests on color alone.

## 3. Screens

### Queue (home)

```
┌────────────────────┬──────────────────────────────────────────────────────────┐
│ ▸ 1 Todo       4   │ Todo · 4                                                 │
│   2 Snoozed    1   │  ✸  Ana Ruiz · acme.com     Pricing for 40 seats?  10:02 │
│   3 Done           │  ↩  Logan Carr · Slack DM   deploy now?            09:40 │
│   4 Ignored        │  ✸  bob@gmail.com · email   Invoice #2041           Mon  │
│                    │  ◔  Mia Chen · #ext-acme    spec review             Sun  │
│   Cases            │                                                          │
│   Channels         │                                                          │
│                    │                                                          │
│ ● Email   OK       │                                                          │
│ ● Slack   OK       │                                                          │
└────────────────────┴──────────────────────────────────────────────────────────┘
 ✸ first response  ↩ next response  ◔ investigating  ◷ waiting  ⏸ paused
 j/k move  enter open  1-4 views  e done  z pause  w wait  s status  i ignore  c case  q quit
```

- Sidebar: the four Status views with counts for Todo and Snoozed, then Cases, Channels, and one health line per Connection (`●` OK in accent, BROKEN in error color with the error text).
- Rows: glyph, sender (Person name, else address or Slack user) · where (Organization domain, `Slack DM`, `#channel`, or `email`), Thread title, last activity time (time today, weekday this week, else date).
- Order: Todo by oldest last inbound activity first (longest waiting at the top); other views by most recent activity.
- Queue keys act on the selected row without opening it.

### Thread

```
┌──────────────────────────────────────────────────────┬──────────────────────────┐
│ Pricing for 40 seats?   ✸ Needs first response       │ Ana Ruiz                 │
│                                                      │ ana@acme.com             │
│ Ana Ruiz  10:02                                      │                          │
│ Hi Jeremy, can we talk pricing for 40 seats?         │ Organization             │
│ We'd like to start in November.                     │ acme.com                 │
│                                                      │                          │
│                                                      │ Case                     │
│                                                      │ —                        │
├──────────────────────────────────────────────────────┴──────────────────────────┤
│ To: ana@acme.com   Cc: ops@acme.com        (ctrl+s send · esc cancel)            │
│ ▌                                                                                │
└──────────────────────────────────────────────────────────────────────────────────┘
 esc back  r reply  e done  z pause  w wait  s status  i ignore  c case  o link  p person
```

- Timeline: messages oldest first, sender and time; Jeremy's messages labelled `You`. Edits show `(edited)`; a deleted message shows `message deleted` with no content (FR-2a).
- Right panel: the sender's Person (all Identities, blocked ones marked `⊘`), Organization, Case, Channel.
- `o` shows a Ctrl-clickable link (OSC 8) to the message in its app: a Gmail search for the `Message-ID`, or the Slack permalink.

### Reply (inline)

**Decision #75**: inline reply box, chosen after trying both inline and `$EDITOR` in the mock.

- `r` opens the box under the timeline; the conversation stays visible.
- Email: `To` and `Cc` are prefilled reply-all (FR-3b) and editable with `tab` into a one-line field. Slack: no recipient line; the box shows the channel or DM.
- `ctrl+s` sends (most terminals cannot tell `ctrl+enter` from `enter`); `esc` closes the box and keeps the draft in memory for that Thread until the app exits.
- While sending: the box shows `sending…`. On success the app returns to the queue with a toast `Sent → Waiting for them`. On failure the box stays open with the text and shows the error in red.

### Status, Case, and Person pickers

- `s`: a small menu of Todo details (Needs first response, Needs next response, Investigating) and `Unignore` when the Thread is Ignored.
- `z`: a menu of `1 hour`, `tomorrow 9:00`, `next Monday 9:00`, `indefinitely`, or a typed time.
- `c`: a filterable list of Cases, `new case…`, and `remove from case`.
- `p`: the Person record: Identities (with `b` block/unblock, `a` add Identity, `x` remove Identity), Organizations, Threads with their glyphs, Cases.

### Cases, Channels, Health

- **Cases**: list of Cases; opening one lists its Threads with glyphs (FR-12, FR-13).
- **Channels**: Slack Channel rules (`a` add `channel` / `prefix` / `suffix` rule, `x` delete) and the Channels seen so far with whether each is selected.
- **Health**: one line per Connection in the sidebar; Enter on it shows the last error.

## 4. Keys

- Plain: `j`/`k` move, `e` Done, `z` Pause for later, `w` Wait for them, `s` change status.
- Deviations: `i` Ignore/Unignore, `c` Case, `r` reply, `ctrl+s` send, `o` link to the native app, `p` Person, `1`–`4` views, `esc` back, `q` quit.
- FR-14 change: Enter now opens the Thread in the console; `o` replaces "Enter opens the native app".

## 5. States to check (visual QA)

Rendered with Textual's test driver (`run_test`, SVG screenshots) and once in a real herdr pane:

1. Queue with all seven glyphs present, narrow (80 columns) and wide (160 columns).
2. Empty Todo view.
3. Thread with a long email (scrolling), an edited message, and a deleted message.
4. Reply box open: email with To/Cc, Slack without.
5. Send failure: box stays open with the error.
6. Health BROKEN in the sidebar.
7. Person record with a blocked Identity.
