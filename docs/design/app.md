# App design

Status: **Draft for Jeremy's review** (2026-10-31; Phases 3–5 built 2026-11-01). Plan: `.agents/prd/2026-10-31-desktop-app.md` (FR-1, FR-6, FR-11). Architecture: ADR 0013. The Console's contract (`docs/design/console.md`) still governs the Console.

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

- **Structure.** Herdr's organisation and hierarchy (Jeremy, 2026-10-31): a 248 px `chrome` sidebar (brand 46 px, then the rail with section labels), a 42 px top bar with the view's tab and key hints, and the work area on the `canvas` with 8 px padding and gap holding two cards (list 460 px, Thread), each with a 30 px header; a 28 px status bar.
- **Themes.** Two ship. `gruvbox-material-light` is the default and matches the Console's palette, as Jeremy asked ("the same light colour theme that the tui has"): background `#f9f5d7`, canvas `#f5edca`, chrome `#f3eac7`, selected `#eee0b7`, border `#ebdbb2`, text `#654735`, accent `#45707a`. `gruvbox` (dark) matches Jeremy's `herdr-app` (background `#282828`, foreground `#ebdbb2`, accent `#d79921`).
- **Semantic tokens.** Views use tokens only, never raw colours: `background`, `surface`, `surface-raised`, `border`, `text`, `text-muted`, `accent`, `selection`, `focus`, and one token per Status detail (`status-needs-first`, `status-needs-next`, `status-investigating`, `status-reply-draft`, `status-waiting`, `status-done`, `status-ignored`). They use the Console's Status colours (`docs/design/console.md` §2), and colour never carries meaning alone.
- **Type.** The UI font is `settings.json` `ui_font` (shipped: `JetBrainsMono Nerd Font Mono`) at 13 px for lists and 14 px for message bodies; section labels are 11.5 px in `dim`. Sizes: 11.5, 12, 13, 14 and 18 (the Thread title).
- **Icons.** Lucide SVGs in `icons/` (ISC licence, `icons/LICENSE-Lucide.txt`).
- **Density.** Rows are 30 px with 8 px rounding, `selected` behind the current row, on an 8 px grid. Chrome is 1 px borders, with no shadows except on overlays.
- **Message bodies.** Rendered by litehtml in a light card in both themes, because mail is designed for light backgrounds. Links keep their colour and underline, and paragraphs keep their margins. The email stylesheet must not reset `a` or `p` (Phase 0 found that `litehtml`'s email preset does). Long URLs wrap.

## 3. Screens

The default layout is `main`: a sidebar (rail) on the left and the work area on the right, as in `herdr-app`.

- **Rail**: in the Console's order: Today and Calendar; Todo, Snoozed, Done, Ignored with counts, and Drafts; Cases (all, then each); Tasks and Sequences; the three Pipelines; People, Organizations and Channels; Connections with their health. A pick sets `shared("page")`, and the list and detail blocks show that page's views.
- **Pages** (list · detail): Threads (`threads` · `thread`), Today (`today` · the row's Thread, Task, Draft, Person or Organization), Calendar (`calendar`: a month grid and the day's Events · `event`), Drafts (`drafts` · `draft`, the editor), Cases (`cases` · `case`: description, Threads, Activity), Tasks (`tasks` · `task`), Sequences (`sequences` · `sequence`: the Steps outline and a Step editor), a Pipeline (`pipeline`: verdict, Bowtie rates and Organizations by Stage · `organization`), People (`people`, filterable · `person`), Organizations (`organizations` · `organization`), Channels (`channels`: Connections · `connection`: channel rules). Moving the selection shows the next item at once, as Jeremy chose on 2026-10-19 (ADR 0004 consequences).
- **Message**: the header (name, time), then the body. An email with an HTML part shows it in `MessageBody` (litehtml on a worker thread, painted in 512 px tiles on a white card; `app/src/body.rs`); other messages show their text. Bodies load once the selection rests for 150 ms. Remote images show their alt text until "Load images" (`message.load_images`, or `thread.images`). A plain click opens a link (http, https and mailto only); Attachments open with one click through their signed link. Trimmed content folding behind `•••` is not built yet.
- **Reply box**: under the messages. Idle it shows the saved Reply Draft's first line, or "Leave a reply…"; r or a opens it with From, To, Cc and Subject (email) and the text, which saves 800 ms after typing stops. ctrl-enter offers Send now, tomorrow, next Monday, Unschedule and Keep editing; ctrl-s saves; Escape closes and keeps the Draft. Files dropped on the window are staged on the shown Draft, else on the selected Thread's reply (`upload`); delete removes one. Send and schedule are view-only (Origin `view`).
- **Draft editor**: the same fields for any Draft, saved with the revision it read (ADR 0012); an agent's newer save replaces the fields while nothing here is unsaved. A Send outcome unknown Draft offers Keep locked, Send again, or Mark as sent.
- **Overlay**: one picker and prompt drawn over the window for every view (the Console's Pick and Ask): type to filter, ↑/↓, Enter, Escape. `?` opens it as a key palette for the page.
- **Status bar**: online, or read-only with the reason (`core.status`); toasts from the Local core's events.

## 4. Keys

The default `keys.json` gives every Console binding the same key where it can (`docs/design/console.md` §4), in sections: `global`, `page:<page>` (plain keys, quiet while a text field has the keyboard) and `always`. Keys that move a detail pane's own cursor are shifted (J/K for a Case's Threads, a Person's Identities, a Sequence's Steps, a Connection's rules). `tests/test_app_parity.py` maps each Console binding to an App command its view handles, a key read inside a text field, or a reason (FR-6, AS-11). Mouse: one click opens links and Attachments; drag and drop attaches files.

## 5. States to check (visual QA)

Check each state in both themes, at 1100×700 and at full screen, with `comms-terminal-app shot`:

1. Online, Today with Todo Threads, Due Tasks and Drafts.
2. Read-only: offline, and the Service on another build.
3. A Thread with a plain-text email, an HTML newsletter with remote images held back, the same after "Load images", a forwarded chain, and an Outlook table mail.
4. A Slack Thread with reactions; an SMS Thread; a Meeting Thread with its invite card.
5. The reply box empty, with a Draft, with a staged file, and with Needs edit and Send outcome unknown.
6. Toast: a write refused by the Service, and a Panel call refused.
7. An invalid `keys.json` with its `.error`, and the state after the escape key.
8. A heavy newsletter while it lays out (up to about 0.5 s, accepted by Jeremy 2026-10-31).
