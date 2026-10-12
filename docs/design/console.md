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

**Decision #88**: colours follow gruvbox-material light with the hard background (sainnhe/gruvbox-material), from Jeremy's reference screenshot and palette. This replaces the dark-only rule (#74) and the NvChad onedark palette (#82). The NvChad structure stays: pill panel titles, statusline blocks, key blocks in the footer.

- **Palette**: bg0 `#f9f5d7` (background), bg1 `#f5edca` (rail, properties, reply fields), bg2 `#f3eac7` (statusline, footer), bg4 `#eee0b7` (selected row, key blocks), bg5 `#ebdbb2` (card borders); fg0 `#654735` (text), fg1 `#4f3829` (selected text); grey0 `#a89984` (panel borders, dim), grey1 `#928374` (muted), grey2 `#7c6f64` (statusline mode block); red `#c14a4a`, orange `#c35e0a`, yellow `#b47109`, green `#6c782e`, aqua `#4c7a5d`, blue `#45707a`, purple `#945e80`.
- **Panel titles**: bg0 text on an accent block: inbox red, main blue, thread green.
- **Focus highlight**: the focused pane's border turns blue. The focused rail's keyboard highlight uses bg4 and bold text; the selected main row and the rail's current-section `▌` marker stay unchanged when focus moves.
- **Bottom bar** (#94, #97): one line, flat like the reference vim/tmux bars: a grey2 mode block (with `› PAGE` when deeper than the rail section), then the page's keys grouped move · reply · triage · organize · general (key bold blue, description grey2, clickable), then Todo, Snoozed, and each Connection as thin text segments separated by `/`; a red block only when a Connection is broken. Keys drop from the busiest group when the terminal is narrow.
- **Status detail colours**: Needs first response blue, Needs next response yellow, Investigating purple, waiting and paused grey, Done green, Ignored dim. Icons come from the icon mapper; colour never carries meaning alone.

## 3. Screens

**Revision 2026-10-12 (decisions #89–#92)** supersedes the preview, full-thread page, and details container below (#79):

- **One cohesive view (#90, updated for pane arrows)**: focus starts on the main page. `←`/`→` move between the rail, main page, and displayed Thread timeline, stopping at the edges; they keep the current section, row, Thread, and page stack unchanged. In the rail, `↑`/`↓` move the highlight; only `enter` or a click opens an item. Informational side panes are skipped. Text inputs keep their native cursor keys and modal screens keep their own navigation. Reply fields take focus while you type; `esc` returns to the list. `1`–`5` switch Status views and `6`–`9` open People, Organizations, Cases, Channels from anywhere.
- **Thread pane is the thread view (#89)**: in a Status view the selected Thread always shows beside the list, laid out like a Linear issue: conversation and reply box on the left, properties (status, person, organization, case, channel) on the right in the same panel. There is no separate full-page Thread. `space` / `pgdn` / `pgup` scroll it. Below 84 columns the properties fold into one line under the title. Links elsewhere (a Person's Threads, an Organization's Threads) select the Thread in its Status view.
- **Related Threads (#95)**: below the properties, "other threads with <Person>" and "other threads with <Organization>" (six each, then "N more…" to the Person or Organization page); clicking one selects it in its Status view.
- **Channels (#94)**: rules on the left; the channels seen, grouped Syncing / Not syncing, in the right pane, the same structure as the Threads page.
- **Cases like Linear issues (#91)**: title, then Activity: "Case created", then one card per Thread ordered by when it started, mirroring how Linear shows a synced Slack thread. A Thread's messages stay inside its card; nothing is woven into the Case feed. Done and Ignored Threads collapse to one line like resolved Linear threads (`enter` shows them). Each card ends with "Leave a reply… (r)". Properties on the right: Status counts, People, Organizations, Channels. `↑`/`↓` or `j`/`k` select a card (a darker border); Thread keys act on it.
- **Composer always visible (#99)**: under the conversation, the reply box is its own bordered region (Gestalt: common region), set apart from the properties column (bg2 with a divider). Closed, it shows the saved draft or "Leave a reply…"; click it, or press `r` / `a`, to write. In a Case card it is a footer row under a divider, like Linear's reply row, instead of a box inside a box.
- **Case description (#98)**: a "Description" heading like "Activity", with its box always shown: the text, or placeholder guidance when empty. Click or `d` edits in place; `ctrl+enter` saves (undoable with `z`), `esc` cancels.
- **Reply composer (#92)**, following Plain's email reply: From (the mailbox), To, Cc (editable), Subject (`Re: …`), then the message. `r` replies to all (FR-3b), `R` replies to the sender only, and the Reply / Reply all switch in the composer refills To and Cc. Slack shows the destination read-only ("To  Slack DM · in thread"); Plain's Slack replies have no options either. Bcc waits for a change to the reply contract (socket, `inbox.reply`, both senders).

**Revision 2026-10-12 (decisions #76–#80)**: herdr-style bordered panels, Plain-style rail sections, preview pane, mouse, and Nerd Font icons.

### Layout

```
╭─ 󰚇 inbox ──────────────╮╭─ 󰝦 threads · todo ──────────────╮╭─ 󰠢 preview ─────────────╮
│ 󰠢 threads              ││ 󰙴 Ana Ruiz · acme.com  Pricing… ││ contract attached        │
│ ▌󰝦 Todo           6    ││ 󱜾 Logan · Slack DM     deploy…  ││ 󰀓 Raj Patel  Sun         │
│  󰒲 Snoozed        2    ││ …                               ││ …                        │
│  󰗡 Done           1    │╰─────────────────────────────────╯╰──────────────────────────╯
│  󰛑 Ignored        1    │
│  󰠢 All                 │   full Thread:  rail │ thread (timeline + reply) │ details
│ 󰚇 browse               │
│  󰀏 People        10    │
│  󰇗 Organizations  4    │
│  󰠔 Cases          1    │
│  󰐣 Channels       5    │
│ 󱘖 connections          │
│  󰇰 Email         󰗠     │
│  󰒱 Slack         󰗠     │
╰────────────────────────╯
```

- Every region is its own rounded, bordered panel with its title on the border (herdr); the focused panel's border turns accent blue.
- **Rail** sections: `threads` (Todo, Snoozed, Done, Ignored, All, with counts), `browse` (People, Organizations, Cases, Channels), `connections` (one line per Connection with its health icon). The current item has an accent bar. Status views live in the rail; there are no tabs above the list.
- **Main panel**: the selected rail item's list, or a full Thread, Person, Organization, Case, or Connection page.
- **Preview panel**: a single click on a Thread (or `space`) opens it read-only beside the list; `j`/`k` keep the preview in step; a second click, a double click, or `enter` opens it full; `esc` closes the preview.
- **Details panel**: with a full Thread open, the Person, Organization, Case, and Channel sit in their own bordered panel on the right.

### Mouse

Everything clickable: rail items, list rows (preview, then open), menu options, footer keys, and links in the details panel (Person, Organization, Case), the Person page (Threads, Organizations), and the Thread header (back, open in Gmail/Slack).

### Icons

`src/universal_inbox/icons.py` maps every entity, Status detail, action, and term to a Nerd Font Material Design glyph (Nerd Fonts 3.5.1). Ghostty ships the symbols. Footer actions, rail items, panel titles, list rows, and details use the mapper; the Status detail glyphs replace the earlier Unicode glyphs.

### Thread

The side rail stays visible on every screen (Thread, Person, Cases, Channels); on the Threads list, `1`–`5` switch Status views.


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
 esc back  r reply  e done  z pause  s status  i ignore  c case  o link  p person
```

- Timeline: messages oldest first, sender and time; Jeremy's messages labelled `You`. Edits show `(edited)`; a deleted message shows `message deleted` with no content (FR-2a).
- Right panel: the sender's Person (all Identities, blocked ones marked `⊘`), Organization, Case, Channel.
- `o` shows a Ctrl-clickable link (OSC 8) to the message in its app: a Gmail search for the `Message-ID`, or the Slack permalink.

### Message body (decisions #105–#110)

Text-first, like a terminal mail client with Gmail's habits. No images in the console; `o` opens visual mail in Gmail.

- Source: the raw message kept on Workbox (`raw/<sha256>.eml` beside the database); the HTML part converted to terminal text by `render.py` (standard library, chosen over `lynx -dump` in a bake-off, #110), plain as fallback. Messages from before raw storage render their stored text, with bare URLs shortened to `↗ domain` links.
- Links: `View all tasks ↗ app.attio.com`, label a terminal hyperlink (OSC 8) opened with Ctrl+Shift+click in Ghostty (the console captures plain clicks; Shift hands the click to the terminal), domain from the real destination (known tracking redirects unwrapped); the click opens the original link.
- Images: `[image: alt]`. Attachments: a chip under the message (name, type, size) that opens the message in Gmail.
- Layout: a left gutter of initials badges (one colour per person, blue for Jeremy, grey when collapsed) to scan who said what; each open message is a card (header, blank line, body) at most ~100 columns wide; shown Trimmed content is grey under a left rule. Long link labels (whole task cards) are coloured, not underlined.
- Collapse: messages after Jeremy's last reply, and the newest, are expanded; older ones are one faded line (the gutter badge, then age and snippet, cut with …), and a long run shows as `N older messages`. Click toggles; `;` expands all, `:` collapses all.
- Trimmed content (quoted text, signatures, boilerplate footers) folds behind `•••`; click to show.
- Header: name, `Mon Oct 12, 10:15 · 2h ago` (relative only within 7 days), and `to …, cc …`; full addresses on expand.
- Calendar invites: an event card (title, time in Jeremy's zone, organizer, attendee count). Forwarded mail: a nested message card, untrimmed.
- No clipping; newsletters follow the same rules.
- Replies quote the earlier message Gmail-style and target the latest inbound message.

### Reply (inline)

**Decision #75**: inline reply box, chosen after trying both inline and `$EDITOR` in the mock.

- `r` opens the box under the timeline; the conversation stays visible.
- Email: `To` and `Cc` are prefilled reply-all (FR-3b) and editable with `tab` into a one-line field. Slack: no recipient line; the box shows the channel or DM.
- `ctrl+enter` sends (#100; `ctrl+s` still works where a terminal cannot report `ctrl+enter`); `esc` closes the box and keeps the draft in memory for that Thread until the app exits.
- While sending: the box shows `sending…`. On success the app returns to the queue with a toast `Sent → Waiting for them` (back to Todo when they reply, or after 5 days to follow up, #116). On failure the box stays open with the text and shows the error in red.

### Status, Case, and Person pickers

- `s`: a small menu of Todo details (Needs first response, Needs next response, Investigating) and `Unignore` when the Thread is Ignored.
- `b` (snooze): a menu of `1 hour`, `tomorrow 9:00`, `next Monday 9:00`, or a typed time. No indefinite option (#96); a snoozed Thread also returns when they write.
- `c`: a filterable list of Cases, `new case…`, and `remove from case`.
- `p`: the Person record: Identities (with `b` block/unblock, `a` add Identity, `x` remove Identity), Organizations, Threads with their glyphs, Cases.

### Cases, Channels, Health

- **Cases**: list of Cases; opening one lists its Threads with glyphs (FR-12, FR-13).
- **Channels**: Slack Channel rules (`a` add `channel` / `prefix` / `suffix` rule, `x` delete) and the Channels seen so far with whether each is selected.
- **Health**: one line per Connection in the sidebar; `h` shows each Connection's last error.

## 4. Keys

**Decision #93**: Gmail-style where Gmail has an equivalent.

- Move: `←`/`→` focus the previous/next pane without opening an item or changing contents; `↑`/`↓` or `j`/`k` move in the main page. In the rail, `↑`/`↓` highlight and `enter` opens; in the thread pane, `↑`/`↓` scroll. `space`/`pgdn`/`pgup` scroll the thread, `u` or `esc` back, `1`–`5` Status views, `6`–`9` People, Organizations, Cases, Channels.
- Thread (main list, focused thread pane, or selected Case card): `r` reply to the sender, `a` reply all (FR-3b's reply-all stays one key away), `e` Done (Gmail archive), `b` snooze (Gmail snooze), `m` Ignore (Gmail mute), `l` Case (Gmail label), `z` undo (Gmail undo), `s` change status, `p` Person, `o` open in Gmail or Slack, `ctrl+enter` send (#100).
- General: `?` all keys, `q` quit.
- The footer lists every action for the page as plain text: key in blue, description in grey, no icons or blocks (#94).

## 5. States to check (visual QA)

Rendered with Textual's test driver (`run_test`, SVG screenshots) and once in a real herdr pane:

1. Queue with all seven glyphs present, narrow (80 columns) and wide (160 columns).
2. Empty Todo view.
3. Thread with a long email (scrolling), an edited message, and a deleted message.
4. Reply box open: email with To/Cc, Slack without.
5. Send failure: box stays open with the error.
6. Health BROKEN in the sidebar.
7. Person record with a blocked Identity.

## 6. Development

Hot reload: the console restarts on every source change.

```
uv run python scripts/seed_demo.py --db /tmp/demo.db
.venv/bin/watchfiles --filter python '.venv/bin/universal-inbox --db /tmp/demo.db' src/universal_inbox
```

Run the console binary directly, not through `uv run`: `uv run` does not pass the watcher's stop signal on, which leaves two consoles drawing over each other.

## 7. UX review (2026-10-12, decisions #83–#87)

A pass over every page and component against the UX Fundamentals guide: utility, usability, clarity, consistency, feedback, control, and accessibility; the gulfs of execution and evaluation; Hick's, Fitts's, and Jakob's laws; Von Restorff; and Gestalt grouping.

| Finding | Principle | Change |
|---|---|---|
| Actions could not be reversed; a mistaken `i` or `e` hid the Thread | Control, recovery | `u` undoes the last change: a Thread action, block or unblock, identity removal, or rule deletion. One level. It is cleared when the service writes, so a restore never hides new activity |
| Toasts named the new state (`→ Ignored`) without saying what it means | Feedback, gulf of evaluation | Each toast states the consequence and the way back: "Ignored. New messages will not bring it back. u undo" |
| The footer listed 10 keys on the queue | Hick's law, Von Restorff | `?` opens a grouped key overlay with the Status legend. The footer trim was reverted at Jeremy's request (#88–#92): it lists every action again |
| `esc back` showed on pages with nothing to go back to | Consistency, clarity | "back" appears only where it works; in the preview pane, `esc` closes the preview |
| List times mixed clock times and weekdays | Clarity of the user's goal (what has waited longest) | Lists show waiting age (`now`, `5m`, `3h`, `2d`, then a date); the timeline shows exact times |
| After an action the preview closed | Flow, usability | The thread pane shows the Thread that takes the row (always, since #89) |
| An empty view said "Nothing here." | Clarity, discoverability | Each view says what empty means and how Threads arrive there ("All caught up: nothing needs a reply.") |
| Browse tables had no column names | Clarity, Gestalt | Headers on People, Organizations, Cases, Channels, and Thread lists |
| Channel rules used internal words (`prefix`, `kind`) | Mental model over implementation model | The page explains what syncs; rules read "starts with …"; each seen channel says "syncs" or "not synced" |
| A broken Connection showed only an error string | Recovery | The page says what is affected and the step to fix it |
| A Thread with no Case showed "—" | Discoverability, Fitts's law | "none · add to a case (c)", clickable |
| Menus and prompts had no way-out hint | Control | Every menu and prompt shows "enter … · esc cancel" |
| Narrow panes pushed the age column off-screen | Usability | Rows fit the panel: "· where" drops first, then the name shortens |

Colour still never carries meaning alone: Status details have an icon and a label, the channel list says "syncs" or "not synced" in words, and broken Connections say "broken".
