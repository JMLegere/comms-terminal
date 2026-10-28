# Console design

Status: **Approved** by Jeremy, 2026-10-12 (decisions #71–#75). Replaces the web console in `docs/design/api-and-database.md` §6. Mock: a Textual prototype Jeremy tried on 2026-10-12 (session scratchpad, not in the repo).

## 1. Form

A terminal app (Textual) that Jeremy runs over SSH on Workbox: `ssh workbox -t comms-terminal`.

- **Decision #71**: terminal UI instead of the web console. Jeremy works in terminals (herdr); SSH replaces the private web port, so D2 is resolved as "SSH to Workbox".
- **Decision #72**: Textual as the toolkit. It is the one runtime dependency beyond the standard library; it gives panes, key bindings, and a headless test driver.

### Processes

```
Workbox
  comms-terminal serve   systemd service; holds the credentials
    /hooks/email, /hooks/slack   public, verified
    wake() every minute
    local socket: POST /reply    jerry-only Unix socket
  comms-terminal         Textual app, run by Jeremy over SSH
    reads SQLite; calls inbox.act, block, add_identity, remove_identity directly
    replies through the service's local socket
```

- **Decision #73**: replies go through the service. The service keeps the Resend key and Slack token as encrypted systemd credentials; the app never holds a token (NFR-5).
  - Socket: `$XDG_RUNTIME_DIR/comms-terminal.sock` (mode 0600, owned by `jerry`).
  - `POST /reply` with JSON `{"thread_id", "text", "to", "cc"}` → `200 {"status", "status_detail"}`, or `502 {"error"}` on `SendFailed`.
- Both processes open the same SQLite file in WAL mode with a 5-second busy timeout.
- The app refreshes when `PRAGMA data_version` changes (checked every 2 seconds), so new mail and Slack messages appear without a keypress. If the service has migrated the database past the console's code (`user_version` above `SCHEMA_VERSION`), one error toast says to quit and start the console again.

## 2. Look

**Decision #88**: colours follow gruvbox-material light with the hard background (sainnhe/gruvbox-material), from Jeremy's reference screenshot and palette. This replaces the dark-only rule (#74) and the NvChad onedark palette (#82). The NvChad structure stays: pill panel titles, statusline blocks, key blocks in the footer.

- **Palette**: bg0 `#f9f5d7` (background), bg1 `#f5edca` (rail, properties, reply fields), bg2 `#f3eac7` (statusline, footer), bg4 `#eee0b7` (selected row, key blocks), bg5 `#ebdbb2` (card borders); fg0 `#654735` (text), fg1 `#4f3829` (selected text); grey0 `#a89984` (panel borders, dim), grey1 `#928374` (muted), grey2 `#7c6f64` (statusline mode block); red `#c14a4a`, orange `#c35e0a`, yellow `#b47109`, green `#6c782e`, aqua `#4c7a5d`, blue `#45707a`, purple `#945e80`.
- **Panel titles**: bg0 text on an accent block: inbox red, main blue, thread green.
- **Focus highlight**: the focused pane's border turns blue. The focused rail's keyboard highlight uses bg4 and bold text; the selected main row and the rail's current-section `▌` marker stay unchanged when focus moves.
- **Bottom bar** (#94, #97): one line, flat like the reference vim/tmux bars: a grey2 mode block (with `› PAGE` when deeper than the rail section), then the page's keys grouped move · reply · triage · organize · general (key bold blue, description grey2, clickable), then Todo, Snoozed, and each Connection as thin text segments separated by `/`; a red block only when a Connection is broken. The last segment is the installed version, `YYYY-MM-DD-HHMM-commit` (the commit's time in local time and its short SHA), baked in when the package is built; a source run shows none. Keys drop from the busiest group when the terminal is narrow.
- **Status detail colours**: Needs first response blue, Needs next response yellow, Investigating purple, waiting and paused grey, Done green, Ignored dim. Icons come from the icon mapper; colour never carries meaning alone.

## 3. Screens

**Revision 2026-10-12 (decisions #89–#92)** supersedes the preview, full-thread page, and details container below (#79):

- **One cohesive view (#90, updated for pane arrows)**: the console opens on Today and focus starts on its first list. `←`/`→` move between the rail, main page, and displayed Thread timeline, stopping at the edges; they keep the current section, row, Thread, and page stack unchanged. In the rail, `↑`/`↓` move the highlight; only `enter` or a click opens an item. Informational side panes are skipped. Text inputs keep their native cursor keys and modal screens keep their own navigation. Reply fields take focus while you type; `esc` returns to the list. `1` opens Today, `2`–`5` switch Status views, `6` Drafts, and `7`–`0` open People, Organizations, Cases, Channels from anywhere.
- **Thread pane is the thread view (#89)**: in a Status view the selected Thread always shows beside the list, laid out like a Linear issue: conversation and reply box on the left, properties (status, person, organization, case, channel) on the right in the same panel. There is no separate full-page Thread. `space` / `pgdn` / `pgup` scroll it. Below 84 columns the properties fold into one line under the title. Links elsewhere (a Person's Threads, an Organization's Threads) select the Thread in its Status view.
- **Related Threads (#95)**: below the properties, "other threads with <Person>" and "other threads with <Organization>" (six each, then "N more…" to the Person or Organization page); clicking one selects it in its Status view.
- **Channels (#94)**: rules on the left; the channels seen, grouped Syncing / Not syncing, in the right pane, the same structure as the Threads page.
- **Cases like Linear issues (#91)**: title, then Activity: "Case created", each Thread added or removed and by what (you, undone), and each description edit, oldest first, with one card per Thread woven into that timeline right after the line that last added it (a Thread with no such line sits by when it started), mirroring how Linear shows a synced Slack thread. A Thread's messages stay inside its card. Every Thread shows its messages, whatever its Status; `enter` folds one to a line. Each card ends with "Leave a reply… (r)". Properties on the right: Status counts, People, Organizations, Channels. `↑`/`↓` or `j`/`k` select a card (a darker border); Thread keys act on it.
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
- **Today** (FR-14b; `.agents/prd/2026-10-17-today-view.md`): the rail's first line, above every section, with the count of what it lists. The page stacks five bordered lists: Todo Threads (grouped by Case), Due Tasks (title in red with the overdue count), the Drafts not shown through them, Paused Enrollments (FR-SEQ-39), and Unattended Organizations (title in red: Organizations in an open or paying Stage with no Todo or Snoozed Thread among their People and no Active or Finishing Enrollment for them, FR-OA-6 as amended by FR-SEQ-38; `enter` opens the Organization, and its Pipeline section fills the right pane); a list with nothing in it hides, and when all are empty it says All clear with the next wake and the next Task. Each list fits its rows (Threads up to half the height, Tasks and Drafts up to a third), and the bottom list shown takes the spare height. `↓`/`↑` run on from one list into the next, `tab` jumps; the list holding the cursor fills the right pane, and the statusline reads `TODAY › THREADS` (or TASKS, DRAFTS, ORGANIZATIONS) with that list's keys.
- **Rail** sections: `threads` (Todo, Snoozed, Done, Ignored, All, with counts), `cases` (Todo, Snoozed, Done, Ignored, with counts of Cases by rolled-up Status, #120), `outreach` (Tasks, Sequences), `pipeline` (Revenue, Fundraising, with % of Target; Talent, with its open count), `browse` (People, Organizations, Channels), `connections` (one line per Connection with its health icon). The current item has an accent bar. Status views live in the rail; there are no tabs above the list.
- **Main panel**: the selected rail item's list, or a full Thread, Person, Organization, Case, or Connection page.
- **Preview panel**: a single click on a Thread (or `space`) opens it read-only beside the list; `j`/`k` keep the preview in step; a second click, a double click, or `enter` opens it full; `esc` closes the preview.
- **Details panel**: with a full Thread open, the Person, Organization, Case, and Channel sit in their own bordered panel on the right.

### Mouse

Everything clickable: rail items, list rows (preview, then open), menu options, footer keys, and links in the details panel (Person, Organization, Case), the Person page (Threads, Organizations), and the Thread header (back, open in Gmail/Slack).

### Icons

`src/comms_terminal/icons.py` maps every entity, Status detail, action, and term to a Nerd Font Material Design glyph (Nerd Fonts 3.5.1). Ghostty ships the symbols. Footer actions, rail items, panel titles, list rows, and details use the mapper; the Status detail glyphs replace the earlier Unicode glyphs.

### Thread

The side rail stays visible on every screen (Thread, Person, Cases, Channels); `1` opens Today and `2`–`5` switch Status views.


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

### Message body (decisions #105–#110, #127–#136)

Text-first, like a terminal mail client with Gmail's habits. Images show in the terminal (#133); `o` opens the message in Gmail or Slack.

- Source: the raw message kept on Workbox (`raw/<sha256>.eml` beside the database); the HTML part converted to terminal text by `render.py` (standard library, chosen over `lynx -dump` in a bake-off, #110), plain as fallback. Messages from before raw storage render their stored text, with bare URLs shortened to `↗ domain` links.
- Links: `View all tasks ↗ app.attio.com`, label a terminal hyperlink (OSC 8) opened with Ctrl+Shift+click in Ghostty (the console captures plain clicks; Shift hands the click to the terminal), domain from the real destination (known tracking redirects unwrapped); the click opens the original link.
- Images: image Attachments and stored Inline images are shown in the message card through `textual-image`: Kitty graphics under Herdr, half-block where the terminal paints no graphics, at most 20 rows tall. Remote email images are never fetched and read `[image: alt]` (#131).
- Attachments (#130): a chip under the message (name, type, size) that links to the copy on Workbox's LAN file server; Ctrl+Shift+click opens it in Mani's browser. A chip whose file is not copied yet (or failed) says `not copied` and keeps its Platform link; Fastmail chips open Fastmail.
- Layout: a left gutter of initials badges (one colour per person, blue for Jeremy, grey when collapsed) to scan who said what; each open message is a card (header, blank line, body) at most ~100 columns wide; shown Trimmed content is grey under a left rule. Long link labels (whole task cards) are coloured, not underlined.
- Collapse: messages after Jeremy's last reply, and the newest, are expanded; older ones are one faded line (the gutter badge, then age and snippet, cut with …), and a long run shows as `N older messages`. Click toggles; `;` expands all, `:` collapses all.
- Trimmed content (quoted text, signatures, boilerplate footers) folds behind `•••`; click to show.
- Header: name, `Mon Oct 12, 10:15 · 2h ago` (relative only within 7 days), and `to …, cc …`; full addresses on expand.
- Calendar invites: an event card (title, time in Jeremy's zone, organizer, attendee count). Forwarded mail: a nested message card, untrimmed.
- No clipping; newsletters follow the same rules.
- Replies quote the earlier message Gmail-style and target the latest inbound message.

### Slack reactions

**Local implementation in progress, 2026-10-25; keyboard dispatch, integrated behavior and final rendered states are not yet verified.** The approved D1–D15 flow below preserves the native palette/message layout. Six accepted synthetic previews cover ready/checking at both widths and the selected reacted-message views; failure/recovery states still need visual acceptance.

- **Enter and target**: `v` outside text entry enters temporary React mode in the active Thread or selected Case Thread card. Prefer the last deliberately expanded eligible Slack message if visible, otherwise the newest visible expanded one. Entry keeps the viewport and performs zero mutations; it may check source state. Automatic expansion/new arrivals never count as deliberate opening. With no visible eligible target, select none; if none are expanded, explain that a Slack message must be expanded first.
- **Move**: `j/k` or `↑/↓` traverse already-expanded, non-deleted captured Slack messages in that Thread/card, including off-screen ones. Explicit movement scrolls the selected message into view. Skip collapsed/ineligible messages, stop at either end, and never wrap, cross cards, auto-expand or fetch more messages.
- **Check and choose**: selecting a target performs a fresh own-membership read. Only that message shows `1 👍 Like · 2 🙏 Thanks · 3 👀 Looking` below its body, with its selection border and readable disabled choices while `Checking reactions…`. Early digits are consumed and discarded, never buffered or passed through to page shortcuts. A fresh ready key adds an unchecked choice or removes a checked own choice; another participant's matching reaction never checks it. Show ✓ as well as colour, without repeated add/remove suffixes. Stack intact key/emoji/name groups at narrow widths.
- **One change, then reading**: a ready key dispatches one explicit add/remove and immediately restores reading controls without viewport movement. Pending/final feedback stays on the original message/Connection across navigation or widget rebuilds. Esc before activation performs zero mutations; leaving after dispatch does not cancel the change. Another action requires a new React gesture and fresh check; prevent overlapping changes on the same target.
- **Keyboard-only scope**: chips, inline choices and React hints have no pointer reaction action, including the otherwise-clickable footer. Preserve ordinary message clicks, expand/collapse, forwarding, text entry, page keys, and Organization/Pipeline `v` outside React mode. Typing `v`, digits or arrows in an editor keeps normal input behavior. The React footer carries navigation/mode only, without duplicate quick choices or a “React target” label.
- **Reading chips**: show every returned emoji group's all-participant source total, including Jeremy when present. Derive totals from source `count`, not partial reactor-list length. Mark own membership separately with neutral interior, blue outline and non-colour ✓; another person's same emoji remains an unmarked group. Use safe shortcode/name fallback for unknown/custom emoji. Wrap whole chips and preserve textual names.
- **Snapshots**: initial Thread display, settled user-driven auto-selection of a different Thread, explicit reopen and `F5` refresh read eligible captured Slack messages only in the active Thread/selected Case card. Keep existing auto-selection; no new Enter-only gate. After a confirmed own change, refresh only its message. Resize, timer ticks, unchanged-owner repaint, database/replica refresh and incidental painting of other Case cards add zero reaction reads. Duplicate in-flight reads coalesce; source work runs off the UI event loop with bounded background work, so reading remains usable. Other participants' changes await the next explicit trigger; there is no live-feed or “Last checked” row.
- **Partial snapshots**: passive opens/refreshes cover at most the 20 most-recent non-deleted messages, with at most 20 passive read requests in a 60-second window per console session across Thread changes and F5. Repeated opens replace queued passive work; capped/unqueried totals show unavailable, preserving original unknown/confirmed/pending write feedback. A later explicit snapshot after the window elapses resets the budget; there is no automatic refill or retry. Explicit selected-target/recovery checks and post-confirmation reads stay fresh and priority, outside this passive budget. This per-console ceiling is not a provider-global rate guarantee; other clients and explicit checks also use Slack's allowance.

| State | Message-bound feedback / recovery |
|---|---|
| Successful empty snapshot | No chips; own choices can become ready after their fresh check |
| Read unavailable | `Couldn’t check reactions`; actions stay disabled; `c Check` reads only, `o Source link` keeps the existing fallback |
| Definitive refusal | `Not added` / `Not removed` plus safe reason; fix the cause before a new explicit gesture |
| Outcome unknown | `Outcome unknown — use React to check` on the original message; explicitly select that same message/Connection for read-only recovery |
| Known edit / deletion | `Message changed — review` / disabled deleted-message action; never substitute another target |
| Confirmed change, count refresh unavailable | Keep confirmed own result; show totals unavailable/stale rather than zero or current-looking counts |

A successful read proves current membership, not which request caused it. If React initially selects Alex while Maya has the unknown result, reading Alex leaves Maya unresolved. Select Maya explicitly and check the captured Maya/Connection pair; missing Maya or changed identity fails closed. Late results must pass target/revision/generation checks and cannot overwrite newer state or another message's feedback. No automatic resend, queued reaction key, offline optimistic write, or Status/Case/Task/Enrollment/Activity change. Session restart requires a fresh check and new gesture.

### Reply (inline)

**Decision #75**: inline reply box, chosen after trying both inline and `$EDITOR` in the mock.

- `r` opens the box under the timeline; the conversation stays visible.
- Email: `To` and `Cc` are prefilled reply-all (FR-3b) and editable with `tab` into a one-line field. Slack: no recipient line; the box shows the channel or DM.
- `ctrl+enter` sends (#100; `ctrl+s` still works where a terminal cannot report `ctrl+enter`); `esc` closes the box and keeps the text as the Thread's Reply Draft, which survives a restart and is listed on the Drafts page with a "reply" label; `enter` there opens the Thread's reply box (FR-SEQ-11).
- Attachments (#134), email and Slack only (SMS and Fastmail have no attach): `ctrl+o` opens the attach menu, "File on Workbox…" (a file tree from `~`) or "Upload from Mani" (a link to a 1-hour upload page, opened with Ctrl+Shift+click). Pasting an image path, which is what Herdr's Ctrl+V sends, stages the image. Staged files list under the message; `delete` removes the highlighted one. They are kept with the Thread across restarts, sent with the reply, and cleared once it is sent. Email refuses to send over 25 MB in total (#135).
- Drafts take the same attach menu, paste, and file list.
- While sending: the box shows `sending…`. On success the app returns to the queue with a toast `Sent → Waiting for them` (back to Todo when they reply, or after 4 days to follow up, #116). On failure the box stays open with the text and shows the error in red.

### Status, Case, and Person pickers

- `s`: a small menu of Todo details (Needs first response, Needs next response, Investigating) and `Unignore` when the Thread is Ignored.
- `g` (snooze): a menu of `1 hour`, `tomorrow 9:00`, `next Monday 9:00`, or a typed time. No indefinite option (#96); a snoozed Thread also returns when they write.
- `c`: a filterable list of Cases, `new case…`, and `remove from case`.
- `b` (block sender, #138): blocks the Identity that wrote, or unblocks it when it is already blocked. With one sender it acts at once and also Ignores the Thread; with several it offers a menu, latest sender first, and blocks only. `z` undoes both.
- `p`: the Person record: Identities (with `b` block/unblock, `a` add Identity, `x` remove Identity), Organizations, Threads with their glyphs, Cases.

### Cases, Channels, Health

- **Merge people** (#121): on People, drag one Person onto another (or `g` and pick the other). A preview shows the merged Person (name by the fuller-name rule, every Identity, Organizations, Thread counts) with Accept and Cancel; nothing changes until Accept. The Person dropped onto is kept.

- **Cases** (#120): the rail's `cases` views list Cases by rolled-up Status (Todo oldest first, others most recently changed first), each with its Thread counts per Status; `n` creates a Case with no Threads yet; `enter` opens it. Key `8` opens Cases › Todo.
- **Time column**: a Thread's age since its last message; a Snoozed Thread instead shows the time left on its snooze or follow-up to the second, ticking (`5m24s`, `3h32m12s`), `—` when paused with no end.
- **Grouped Thread views** (#120): a Status view that holds more than one Status (Todo, Snoozed, All) groups rows under a Status header per Status, in the legend's order, each with its Thread count. Inside each Status (or the whole view, for Done and Ignored), when any listed Thread is in a Case, rows group under Case header rows: "No case" first, then each Case by its oldest listed Thread. A Case header shows the Case title and its Thread counts per Status; `enter` on it opens the Case.
- **Channels**: Slack Channel rules (`a` add `channel` / `prefix` / `suffix` rule, `x` delete) and the Channels seen so far with whether each is selected.
- **Health**: one line per Connection in the sidebar; `h` shows each Connection's last error.

### Context sources

Thread, Person, Organization, and Case pages show separate details-column sections in this order: Attio, Tana, Linear. Linear capability is published and deployed under Jeremy's "ship it"; Linear was connected on 2026-10-16 and the service returns real-provider matches; live console rendering and human acceptance remain separate. Existing pages and keyboard bindings stay unchanged.

- **Linear**: compact cards for every matching issue except **Done, Completed, Canceled, or Archived**, including backlog, triage, and other nonterminal states: identifier, title, state, priority, assignee, project, and updated time, with why-matched evidence, relevant Customer Request excerpts inspected only within matched Customer/issue candidates, and an **Open in Linear** link. No global search over unassociated Requests, issue comments, or closed-history feed.
- **Inherited context**: preserve existing Person/Organization context inheritance on all four page types. Cases inherit Linear context through their People and Organizations; no direct Case-to-issue field or copied issue data. Inbox Status stays unchanged.
- **Fetch and health**: read on page open through the service, with the same five-minute cache as Attio and Tana. Each source has separate health in the rail's `context` section and bottom bar; a failure appears in its own section.

### Calendar (FR-13d, ADR 0005)

- **Rail**: Calendar sits directly under Today.
- **Grid**: one month grid of week rows that scrolls without pages; a separator row starts each month, and a week that spans two months splits at it. It opens with two weeks above this week and the cursor on today. A cell shows the date (`today` on today), up to four Events as start hour and title (`?` unanswered, `~` maybe, declined dimmed, Deleted struck through, All-day in blue), `+N more`, and a count line: Tasks, Snoozed Threads waking, scheduled Draft sends, Enrollment starts.
- **Day pane**: every Calendar Item of the cursor's day in time order. Enter opens it: an Event's details page, the Thread, the Task, the Draft, or the Person.
- **Event details**: title and Invite state, time and repeat, location, join link, organizer and attendees with their answers (linked to People, with open Threads and Cases), Organizations, the description with Google's Meet/Zoom footer folded, and the Context sections for its people.
- **Bottom bar**: `now` Event and minutes left, then `next` Event and when; clicking it opens the Calendar. A failing Google Calendar feed shows Google Calendar broken; the Calendar shows its copy "as of" the last good fetch.
- **Meetings** (ADR 0007, FR-OA-11..16): an Event with someone besides Jeremy (inside or outside Agent Maple, decision #142), first seen after go-live, is a Thread in Todo, one per Event UID, so a series is one Thread; it lists under "Meeting" in the queue. Its timeline shows each Meeting Activity as a message from the organizer or first other attendee: Booked, Held (after each occurrence ends), Cancelled, or Declined (by another attendee), then where (Google Meet, Zoom, Microsoft Teams, phone, in person), the time, the join link, and who. Each one puts the Thread in Todo; a reschedule only moves the Event. The header's link joins the meeting (`o`); the reply box is hidden, and `r` says a Meeting Thread takes no replies. Attendees are its People, so Membership, the roll-up, and the Context sections (Tana's summary) see them.
- **Responses** (FR-13e, ADR 0009): on a Meeting Thread or an invitation email Thread, `i` opens a one-line picker "Respond: y yes · m maybe · n no"; the second key answers, Esc cancels. The invite card shows `i respond`. The bottom bar shows the outcome: Accepted, Maybe, or Declined when Google confirms; the refusal reason ("You organized this Event."); "Response outcome unknown" when Google did not answer, never retried; or "Responses unavailable" without a sign-in, with Google Calendar writes broken beside the feed's health. A confirmed Response adds a Meeting Activity from Jeremy; Status does not change; the Invite state shown comes from the next feed read.
- **Keys**: grid `h`/`l` day, `j`/`k` week, `{`/`}` month, `t` today, `tab` or `enter` the day pane; day pane `j`/`k`, `enter` open, `o` join link, `tab`/`esc` back to the grid; Event details `o` join link, `esc` back. `←`/`→` move between grid and day pane.

### Sequence flow (FR-SEQ-26–46, ADR 0008)

- **Steps pane**: an outline. Headings `On start`, `Steps`, each If's `✓ <label>` and `✗ <label>` paths indented beneath it, and `On completion` under each path that does not end in an If. `n` on a heading adds a Step last on that path; `n` on a Step adds after it (refused on an If: add to one of its paths). An Action asks what it does (Set Stage, Enroll, End for others, Set Value); an If asks what it checks (How it finished, only in an On completion; Has channel; Stage; You pick). `J`/`K` move within the path; `x` on an If deletes its paths too, and `z` restores them.
- **Step editor**: Action fields (Gap, Time, Pipeline, Stage, Value, Sequence) and If fields (Gap, Is, Question, Path 1, Path 2) by what it does or checks.
- **Enrolling** (`s`): Sequence, then the Organization it is for (the Person's Organizations, the one their latest message came from first, and `New sole proprietorship: <name>`), then, when the Person has more than one email address, the address its emails go to (the one at that Organization first, else the one added last), then the Start date.
- **Finishing** (`f`): Goal met, Not Right Now, Lost (each runs the path's On completion), Canceled, Unsubscribed. A Finishing Enrollment offers only Canceled and Unsubscribed.
- **Picks**: an If that asks Jeremy shows its question and `1 <label>  2 <label>` on Tasks and on the Person page; `1` or `2` answers, and `z` undoes.
- **Use when** (`w` on the Sequences list): a one-line "use when", shown in the list (wide), the side pane, and the enroll picker under each name.
- **Needs edit**: a Draft from a Step whose text is still a brief shows `edit me` in the Drafts list; sending it says to change its text first.
- **Today**: a yellow `paused · N` list after Drafts: Person, Sequence (with "finishing as …"), and when it paused; `r` resumes, `f` finishes, `enter` opens the Person. The rail count includes it; the pause toast points to it.
- **Talent**: the Pipeline page shows only the board, with Stage names and counts and no rates, Constraint, Value, This week, Progress, or Weekly review; `y` copies `comms-terminal pipeline talent`.

### Pipeline (ADR 0006, approved 2026-10-23)

Terms follow `GLOSSARY.md` (Pipeline, Stage, Value, Target, Conversion rate, Time in Stage, Conversion to Impact, Weighted pipeline, Required volume, Pace, Constraint, Benchmark, Retention rate, Expansion rate, Net retention). The math is Winning by Design's Bowtie Standard v1.0. The page shows the math only; it sets no Tasks.

- **Rail**: a `pipeline` group right below `outreach` (Tasks, Sequences), with one line per Pipeline: Revenue and Fundraising. Each line shows projected ÷ Target as a percentage (`Revenue 32%`), or `—` when the Pipeline has no Target yet.
- **Page** (revised 2026-10-24, `.agents/discovery/2026-10-24-pipeline-page-layout.md`; boxes after Jeremy's "formatting and visual hierarchy - hard to scan and parse"): one full-width page with no right pane, action first. Each section is a titled, rounded box in the console's panel style, so the page reads as a grid. The page scrolls vertically, top to bottom:
  1. **THIS WEEK**, beside **PROGRESS** (stacked below 140 columns).
     - This week: numbered actions in bold, each with its reason in grey under it. The math produces them; they are never Tasks. In order:
       - the Constraint's lever ("Move St Mary's Entertainment Centre / Onboarding → Impact, the constraint", or "Find Organizations for Onboarding" when the Stage is empty);
       - the input at the top Stage ("Add 2 Organizations to Awareness / need 1.8 a week, getting 0.1");
       - Organizations with no Value ("Set a Value for …");
       - "Set a Target" when there is none.
     - Progress: the On-track verdict first (On track, On pace, Behind pace, Off track, or Pace unknown), coloured, with its reason: the top Stage's need a week against what it gets. Then the share of the Target projected, with a gauge (█ customers today, ▓ likely from open Organizations, ░ gap). Then aligned label–value lines: Target, Projected, Today, Coverage, Gap (new customers at the average Value), Deadline, and Customers (keep, grow, net). The rail shows the verdict in short: on track, on pace, behind, late, or —.
  2. **CONSTRAINT**: a red-bordered box with one line, for example "Onboarding → Impact   67% move on   benchmark 94%".
  3. **ORGANIZATIONS**: the board, one column per Stage in Bowtie order, all of them, including Lost and Churned.
     - Each column header carries the Bowtie: the Stage, then its count and what moves on from it ("1  75% →", "2  keep 96%b"). The Constraint's header is red and marked ◀.
     - Cards show the Organization, its Value (red "no Value" when unset), days in the Stage, its roll-up (Todo, "Snoozed until Oct 26", or Unattended in red; FR-OA-7), and the last message's age over every identity kind of its People.
     - Below about 18 columns each, the board scrolls sideways.
  4. **WEEKLY REVIEW**: a table with plain headers: Stage, Organizations, Convert, Benchmark, Days in Stage, Reach Impact, Need a week, Getting a week, Short by. Impact and Expansion rows show "keep" and "grow"; the Constraint's row is red. One line below explains the columns.
- **Keys**: on the board, `j`/`k` move between cards and `←`/`→` move between Stages (at the left edge, to the rail); `enter` opens the Organization; `s` sets its Stage, `v` its Value; `y` copies the page as Markdown; `space`/`pgdn`/`pgup` scroll the page.
- **Membership**: `o` on a Person page adds them to an Organization (a company domain; free-mail is refused) or removes them from their email domain's; `p` on an Organization page adds a Person (by address, number, or name) or removes one. `z` undoes. Jeremy's own changes win over the email domain.
- **Organization page**: a `Pipeline` section in the properties column, above Attio: each Pipeline's Stage and days in it, its Revenue Value or Fundraising Value, a `Now` line with the roll-up (as on the board), then the Stage and Value changes, newest first, with `(s stage · v value)`.
- **Setting a Stage** (`s`, on an Organization page or row): for an Organization in exactly one Pipeline, the picker lists that Pipeline's Stages with the current one marked, plus `Fundraising…` (or `Revenue…`) to switch; in neither or both, it asks for the Pipeline first. A toast confirms (`Chestnut → Commit. z undo`).
- **Setting Value** (`v`): for an Organization in exactly one Pipeline, a prompt for that Pipeline's Value (`Revenue Value · Chestnut — CAD, next 12 months`, or `Fundraising Value · … — CAD cheque`) that accepts `90k` or `90000`; in neither or both, it asks for the Pipeline first. Value belongs to the Pipeline, not to the Stage; an Organization with a Value and no Stage stays out of the math.
- **Organizations list**: adds Stage and Value columns. Stage shows the Revenue Stage, or the Fundraising Stage when the Organization has no Revenue Stage.
- **Empty states**: no Target: the table still shows counts and rates, and the summary reads `No Target yet: comms-terminal pipeline target fundraising 1500000 --by 2027-06-30`. No Organizations: `No Organizations in Fundraising yet. Press s on an Organization to add one.`
- **CLI**: `comms-terminal pipeline revenue` (or `fundraising`) prints the page as Markdown, This week first.
- **Refresh**: live, like every other page.

## 4. Keys

**Decision #93**: Gmail-style where Gmail has an equivalent.

- Move: `←`/`→` focus the previous/next pane without opening an item or changing contents; `↑`/`↓` or `j`/`k` move in the main page. In the rail, `↑`/`↓` highlight and `enter` opens; in the thread pane, `↑`/`↓` scroll. `space`/`pgdn`/`pgup` scroll the thread, `u` or `esc` back, `1` Today, `2`–`5` Status views, `6` Drafts, `7`–`0` People, Organizations, Cases, Channels. On a list row, `enter` moves into the item beside it (a Case header opens the Case; a reply Draft opens its Thread's reply box); in pickers and single-line fields it submits; in a reply or Draft body it types a newline and `ctrl+enter` sends. `x` drops the row's item: a Thread is Ignored (as `m`), a Task skipped, a Draft deleted; `z` undoes. On Tasks, `/` filters.
- Thread (main list, focused thread pane, or selected Case card): `r` reply to the sender, `a` reply all (FR-3b's reply-all stays one key away), `e` Done (Gmail archive), `g` snooze, `b` block or unblock the sender (#138), `m` or `x` Ignore (Gmail mute), `l` Case (Gmail label), `z` undo (Gmail undo), `s` change status, `p` Person, `o` open in Gmail or Slack, `f` forward the newest message as an email Draft with its Attachments (#134), `ctrl+enter` send (#100).
- Invite Responses: `i` on a Meeting Thread or invitation email Thread, then `y` yes, `m` maybe, or `n` no; Esc cancels (FR-13e).
- Slack reactions (local implementation in progress): `v` React and `F5` snapshot refresh in the active Thread/selected Case card, outside editors. In React mode, `j/k` or `↑/↓` select an expanded message, ready `1/2/3` choose Like/Thanks/Looking, `c` checks read-only after failure, and Esc cancels before activation. These keys are scoped exceptions to ordinary digit navigation; Organization/Pipeline `v` keeps its Value action.
- Reply and Draft: `ctrl+o` attach, `delete` remove the highlighted staged file, paste an image path to attach it.
- Copy for an agent: `y` on a Thread (or its Case card) or a Draft copies it to the clipboard (OSC 52) as Markdown to paste into a terminal agent. A Thread gives its properties, every message in full with link URLs, Trimmed content, and each Attachment's Workbox path, and the Attio, Tana, and Linear answers as JSON. A Draft gives its envelope, current unsaved body, and staged files with their paths, then each Thread in its Captured history.
- The same Markdown comes from the command line, so an agent can fetch it fresh: `comms-terminal threads [view]`, `comms-terminal thread ID`, `comms-terminal drafts`, `comms-terminal draft ID`. `comms-terminal draft ID --revision N --body -` (or `--to`, `--cc`, `--subject`) changes a Draft only at the revision the agent read, `comms-terminal draft new --connection ID` starts one for a new Thread, and `comms-terminal draft new --thread ID --body -` starts a Thread's reply Draft, which its reply box opens on. Nothing on the command line sends; Jeremy sends from the console.
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
8. Revenue Pipeline at 236×57 and 100×40: This week bullets, Progress, the Constraint, the Bowtie aligned over the board, all ten board columns (scrolling sideways when narrow), and the Weekly review.
9. Fundraising Pipeline with no Target, and a Pipeline with no Organizations.
10. Organization page Pipeline section with history; the Stage picker for an Organization in one Pipeline and in none.
11. Today with the Unattended list in use, its Organization's Pipeline section in the right pane.
12. The board's roll-up line: Todo, Snoozed until a date, and Unattended.
13. A Meeting Thread: Booked and Held cards, the join link, no reply box; the Google Calendar Connection page.

Reaction execution checks, **pending**: actual ready/checking/chips, unavailable read, refusal, original-target unknown recovery, and confirmed-write/failed-count-refresh states at 187×50 and 80×58; inspect keyboard behavior, wrapping, non-colour own cue, neutral interior/blue outline, and message-bound feedback. Preserve the six accepted reference images. Synthetic design previews alone prove neither implemented behavior nor live access. Failure-state acceptance, actual terminal glyphs/dispatch, live actions/trial, and release remain independent gates.

## 6. Development

Hot reload: the console restarts on every source change.

```
uv run python scripts/seed_demo.py --db /tmp/demo.db
.venv/bin/watchfiles --filter python '.venv/bin/comms-terminal --db /tmp/demo.db' src/comms_terminal
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
