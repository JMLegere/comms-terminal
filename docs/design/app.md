# App design

Status: **Draft for Jeremy's review** (2026-10-31; Phases 3–5 built 2026-11-01; conformance target set 2026-11-02). Plan: `.agents/prd/2026-10-31-desktop-app.md` (FR-1, FR-6, FR-11); conformance plan `.agents/prd/2026-11-02-app-console-conformance.md`. Architecture: ADR 0013. **The running Console is the App's reference (ADR 0014):** the App matches `tui.py` and `docs/design/console.md` except for the accepted differences in §6. Where this document and the Console disagree outside §6, the Console wins. The sections below describe the target after conformance.

## 1. Form

- A native GPU-drawn window built the way `herdr-app` is: a small Rust host (`app/`) on GPUI (`gpui-pre` 0.3.8) and herdr-app's vendored gpui-kit 0.7.1 (`app/vendor/`). Every screen is a gpui-shell script (`views/<name>/main.js`), the same kind of script a Panel is; the host draws only the layout, the banners and the escape key. Started from `comms-terminal-app.desktop` on Mani (niri, Wayland), which runs `deploy/comms-terminal-app`: it installs the Local core and the App at the Service's build and opens the window again after an update (exit 75).
- The App is one Client of the Local core (ADR 0013). It starts `comms-terminal core` when the socket is absent. Closing the window stops nothing.
- Everything Jeremy or an agent may change lives in Interface files under `~/.config/comms-terminal-app/`, mirroring `herdr-app`'s layout:

| File | Decides | Applied |
|---|---|---|
| `layouts/<name>.json` | blocks, Panels, sizes; a block's `pages` map picks its view per page (`shared("page")`, and `"<page>:<detail>"` for Today's rows) | live |
| `keys.json` | every key | live |
| `settings.json` | `theme`, `ui_font`, `core` (the command that starts the Local core) | live |
| `themes/<name>.json` | colour tokens (§2) | live |
| `views/<name>/` | the shipped screens; rewritten on every start | start |
| `panels/<name>/` | Panel scripts | live |
| `AGENTS.md`, type files | rewritten on every start; explain the above to agents | start |

  An invalid file keeps the last good version and writes `<name>.error`. `ctrl-alt-shift-escape` always restores the default layout and keys. `comms-terminal-app check <panel>` loads a Panel once; `shot [file.png]` saves the last frame; `status` lists views and errors; `do <view>.<command>` runs a command; `probe [moves] [view]` measures Thread moves.

## 2. Look

References, in Jeremy's words "slick, sexy and beautiful": Linear (dense, quiet chrome, keyboard first), Plain (the Status model, the Thread layout), Superhuman (speed, one thing at a time), and `herdr-app` (sidebar plus work area, no title bar).

- **Structure.** Herdr's organisation and hierarchy (Jeremy, 2026-10-31; kept by L0 and D18, "i like the app shell"): a 248 px `chrome` sidebar (brand 46 px, then the rail with section labels), a 42 px top bar, and the work area on the `canvas` with 8 px padding and gap holding the page's cards, each with a 30 px header; a 28 px status bar. The bars carry what the Console's one bottom bar carries (D9): the mode block (`TODO`, `TODAY › THREADS`, `… › PERSON`), `READ-ONLY`, the page's keys grouped move · reply · triage · organize · general and clickable, the `now`/`next` Event, Todo and Snoozed counts, each Connection's and Context source's health, and the installed build. Which bar holds which item is settled at the Phase 2 screenshot gate.
- **Theme.** One ships: `gruvbox-material-light`, matching the Console's palette, as Jeremy asked ("the same light colour theme that the tui has"): background `#f9f5d7`, canvas `#f5edca`, chrome `#f3eac7`, selected `#eee0b7`, border `#ebdbb2`, text `#654735`, accent `#45707a`. The dark `gruvbox` theme is removed (D19).
- **Semantic tokens.** Views use tokens only, never raw colours: `background`, `surface`, `surface-raised`, `border`, `text`, `text-muted`, `accent`, `selection`, `focus`, and one token per Status detail (`status-needs-first`, `status-needs-next`, `status-investigating`, `status-reply-draft`, `status-waiting`, `status-done`, `status-ignored`). They use the Console's Status colours (`docs/design/console.md` §2), and colour never carries meaning alone.
- **Type.** The UI font is `settings.json` `ui_font` (shipped: `JetBrainsMono Nerd Font Mono`) at 13 px for lists and 14 px for message bodies; section labels are 11.5 px in `dim`. Sizes: 11.5, 12, 13, 14 and 18 (the Thread title).
- **Icons.** Lucide SVGs in `icons/` (ISC licence, `icons/LICENSE-Lucide.txt`).
- **Rows.** Every list row is one line with the Console row's columns, glyphs and order (D18, D26 and the per-list items in the plan's Appendix A): for a Thread, the Status glyph, who, `· where`, the title, the pencil when a ready Reply Draft exists, and the age in the Console's format. The App draws them in its own font, 30 px tall with 8 px rounding, `selected` behind the current row, on an 8 px grid. Chrome is 1 px borders, with no shadows except on overlays.
- **Message bodies.** Rendered by litehtml in a light card in both themes, because mail is designed for light backgrounds. Links keep their colour and underline, and paragraphs keep their margins. The email stylesheet must not reset `a` or `p` (Phase 0 found that `litehtml`'s email preset does). Long URLs wrap.

## 3. Screens

The default layout is `main`: a sidebar (rail) on the left and the work area on the right, as in `herdr-app`.

- **Rail**: the Console's sections, order and counts (D4–D7): Today and Calendar; Todo, Snoozed, Done, Ignored and Drafts with the Console's counts; Cases as the four Case Status views with Case counts (no individual Cases, D148); Tasks and Sequences; the three Pipelines with the Console's figures; People, Organizations and Channels; each Connection as a clickable row opening its Connection page. A pick resets the page stack to that section, as the Console's `go` does (`tui.py` `go`).
- **Page stack** (D1): the Console's stack. A rail section opens its list page; Enter opens the selected item's full page (Person, Organization, Case, Connection, Event, and a Task's Draft) on the stack; links to a Person, Organization, Case or Thread push that page; `esc` and `u` go back one page. The App opens on the Console's start page (D2). Each page keeps the Console's panes and pane focus (D3): list and detail side by side where the Console shows them so, and the detail pane focusable by the Console's keys.
- **Pages** (list · detail, as the Console lays them out): Threads (`threads` · `thread`), Today (`today` · the row's own page), Calendar (`calendar` · `event`, laid out as the Console's Calendar, D91–D97), Drafts (`drafts` · `draft`), Cases (`cases` · `case`), Tasks (`tasks` · `task`), Sequences (`sequences` · `sequence`), a Pipeline (`pipeline` · `organization`), People (`people` · `person`), Organizations (`organizations` · `organization`), Channels (`channels` · `connection`). Lists have no filter fields (D130, D146). Moving the selection shows the next item at once, as Jeremy chose on 2026-10-19 (ADR 0004 consequences).
- **Threads list**: Status-detail groups (D23, accepted), and inside them the Console's Case header rows: "No case" first, then each Case by its oldest Thread, with its title and per-Status counts. Clicking or pressing Enter on a Case header opens that Case. Headers never fold (D150). A saved reply counts as a Reply Draft only when it is ready to send (D25).
- **Thread**: the Console's single column with its properties pane: header (platform icon, person, `· where`, Status, open or join link, respond hint), title, "N messages · <Case>", messages collapsed as the Console collapses them, reply box; properties, related Threads and Context sources on the right (D34–D38). Context comes from a Local core query that reads the same Attio, Tana and Linear sources the Console reads.
- **Message**: the Console's header (name, time in the Console's format, edited mark, per-person colour, D39–D40), then the body. An email with an HTML part shows it in `MessageBody` (litehtml on a worker thread, painted in 512 px tiles on a white card; `app/src/body.rs`); other messages show their text. Bodies load once the selection rests for 150 ms. Remote images show their alt text until "Load images" (`message.load_images`, or `thread.images`). **Links in a body open with Ctrl+click** (http, https and mailto only; D44, Jeremy chose Ctrl over the Console's Ctrl+Shift); a plain click selects. `o` opens the message in its app at once (D52). Attachments open with one click through their signed link (D45).
- **Reply box**: under the messages, with the Console's wording, From line, mode switch and hints (D56–D59, D62). Idle it shows the saved Reply Draft's first line, or "Leave a reply…"; r or a opens it with From, To, Cc and Subject (email) and the text, which saves 800 ms after typing stops. ctrl-enter offers Send now, tomorrow, next Monday, Unschedule and Keep editing; ctrl-s saves; Escape closes and keeps the Draft. Files attach three ways (D63, D80): **ctrl+o opens the system file picker**, a pasted file path attaches that file, and a file dropped on the window is staged on the shown Draft, else on the selected Thread's reply (`upload`); delete removes one. Send and schedule are view-only (Origin `view`).
- **Draft editor**: New Thread Drafts only. A Reply Draft opens in its Thread's reply box (D69). Fields are saved with the revision they read (ADR 0012); an agent's newer save replaces the fields while nothing here is unsaved. **A Send outcome unknown Draft is read-only** until Jeremy chooses Keep locked, Send again, or Mark as sent; the red state line stays (D74).
- **Commands act on what is shown.** A key acts only on the object of the selected row or open page. On Today, Thread keys reach a Thread only when a Thread row is selected; on a Task, Draft, Paused Enrollment or Unattended Organization row they do nothing or act on that row's own object, as the Console does (D87).
- **Edits survive leaving.** Leaving a Step editor by Escape or by moving away saves it, as the Console does (D119). A Person merge keeps the Person the Console keeps and shows the Console's preview before it merges (D137).
- **Undo**: the Console's one app-wide undo (D10–D12): every change the Console offers to undo is undoable in the App, its toast ends with `z undo`, and `z` undoes it from any page. Deletes happen at once and offer undo, with no confirmation picker (D71, D149).
- **Overlay**: one picker and prompt drawn over the window for every view (the Console's Pick and Ask): type to filter, ↑/↓, Enter, Escape. `?` opens it as a key palette for the page.
- **Status bar**: online, or read-only with the reason (`core.status`); toasts from the Local core's events.

## 4. Keys

The default `keys.json` gives every Console binding the same key where it can (`docs/design/console.md` §4), in sections: `global`, `page:<page>` (plain keys, quiet while a text field has the keyboard) and `always`. Keys that move a detail pane's own cursor are shifted (J/K for a Case's Threads, a Person's Identities, a Sequence's Steps, a Connection's rules). `tests/test_app_parity.py` maps each Console binding to an App command its view handles, a key read inside a text field, or a reason (FR-6, AS-11). After conformance, a reason is allowed only for an accepted difference in §6; "no page stack" and "wheel only" are no longer reasons (D1, D55). The page stack's `esc`/`u`, pane focus and the Console's scroll keys are bound. Mouse: Ctrl+click opens a body link; one click opens an Attachment; a click on a row selects it and keeps list focus, as in the Console (D33); drag and drop attaches files.

## 5. States to check (visual QA)

Check each state at 1100×700 and at full screen, with `comms-terminal-app shot`, beside the Console in the same state:

1. Online, Today with Todo Threads, Due Tasks and Drafts.
2. Read-only: offline, and the Service on another build.
3. A Thread with a plain-text email, an HTML newsletter with remote images held back, the same after "Load images", a forwarded chain, and an Outlook table mail.
4. A Slack Thread with reactions; an SMS Thread; a Meeting Thread with its invite card.
5. The reply box empty, with a Draft, with a staged file, and with Needs edit and Send outcome unknown.
6. Toast: a write refused by the Service, and a Panel call refused.
7. An invalid `keys.json` with its `.error`, and the state after the escape key.
8. A heavy newsletter while it lays out (up to about 0.5 s, accepted by Jeremy 2026-10-31).
9. The page stack three pages deep (Threads › Person › Organization), then `esc` back to each.
10. Today with a Task, Draft, Paused and Unattended row selected after a Thread row, with Thread keys pressed.
11. A Send outcome unknown Draft, read-only.

## 6. Accepted differences from the Console

Jeremy accepted these in the App-versus-Console review (`App-Console-Review-Decisions.md`, 2026-11-02). Each stays as the App does it; everything else follows the Console (ADR 0014). A new entry needs Jeremy's explicit approval and its reason.

| ID | Accepted difference |
|---|---|
| L0 | The App's look and feel: Herdr-style shell, sidebar, cards, light theme. |
| D13 | `?` opens the picker as a key palette for the page. |
| D14 | One overlay for every picker and prompt, with type-to-filter. |
| D15 | Read-only shows in the status bar, with no toast. |
| D17 | "Update ready" read-only reason; the App relaunches itself after an update. |
| D20 | Interface files, Panels, live reload, `.error` files and the escape-key reset. |
| D21 | The brand row. |
| D22 | The status bar names the Service, its build and the writes still saving. |
| D23 | Threads grouped by Status detail in every view. Jeremy's 2026-11-02 qualification: the Console's Case header rows stay inside the groups, and headers do not fold. |
| D30 | "Nothing in Todo." |
| D43 | HTML bodies rendered natively, remote images held back behind "Load images". |
| D45 | Attachment chips show the name only and open with one click. |
| D46 | Inline `cid:` images in HTML bodies; other image Attachments as chips. |
| D47 | No invitation card in the Thread. |
| D49 | `v` reacts to the newest Slack message from a three-reaction picker. |
| D50 | Respond picker for invitations and series. |
| D51 | The idle reply box always shows; `r` on a Thread that takes no replies says so. |
| D52 | `o` opens the message in its app at once. |
| D53 | Copying a Thread for an agent omits Context sources. |
| D58 | Reply text saves 800 ms after typing stops. |
| D60 | A click on the idle reply box opens reply all. |
| D61 | Sending, sent and send-error toasts. |
| D64 | `delete` picks which staged file to remove. |
| D70 | `n` asks which Connection first; a Draft's Connection is fixed afterwards. |
| D74 | The red "Send outcome unknown · locked until you choose" line. Jeremy's 2026-11-02 qualification: the Draft is read-only while locked. |
| D76 | Draft save and send toasts. |
| D79 | Drafts empty states. |
| D80 | A file dropped on the window attaches to the shown Draft. |
| D87 | Today's rows and keys as laid out. Jeremy's 2026-11-02 qualification: Thread keys act only on a selected Thread row. |
| D108 | "No Tasks due." |
| D116 | Sequence Step keys `N`, `X`, `J`/`K`, `ctrl-j`/`ctrl-k`. |
| D125 | Pipeline: `j`/`k` through one flat list. |
| D135 | Organization commands also on the Organizations list. |
| D138 | Person commands on the People list, acting on the selected Person's first Identity, with that target shown. |
| D140 | Block and Person toasts. |
| D143 | Channels lists every Connection with its health; the right pane shows its rules. |
| D147 | The key palette's help content. |

Native equivalents Jeremy chose for terminal-only Console behaviours (2026-11-02): body links open with Ctrl+click instead of Ctrl+Shift+click; `ctrl+o` opens the system file picker instead of the Workbox file tree or upload link.
