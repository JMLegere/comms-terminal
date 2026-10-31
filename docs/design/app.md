# App design

Status: **Draft for Jeremy's review** (2026-10-31). Plan: `.agents/prd/2026-10-31-desktop-app.md` (FR-1, FR-6, FR-11). Architecture: ADR 0013. The Console's contract (`docs/design/console.md`) still governs the Console.

## 1. Form

- A native GPU-drawn window built on gpui-kit (GPUI), started from `comms-terminal-app.desktop` on Mani (niri, Wayland). It has no title bar, as `herdr-app` on Mani has (`layouts/main.json`, `"titlebar": "none"`).
- The App is one Client of the Local core (ADR 0013). It starts `comms-terminal core` when the socket is absent. Closing the window stops nothing.
- Everything Jeremy or an agent may change lives in Interface files under `~/.config/comms-terminal-app/`, mirroring `herdr-app`'s layout:

| File | Decides | Applied |
|---|---|---|
| `layouts/<name>.json` | blocks, Panels, sizes, the theme and fonts per layout | live |
| `keys.json` | every key | live |
| `settings.json` | zoom, fonts, theme, appearance | live |
| `themes/<name>.json` | colour tokens (§2) | live |
| `panels/<name>/` | Panel scripts | live |
| `AGENTS.md`, type files | rewritten on every start; explain the above to agents | start |

  An invalid file keeps the last good version and writes `<name>.error`. `ctrl-alt-shift-escape` always restores the default layout and keys. `comms-terminal-app check <panel>` loads a Panel once; `comms-terminal-app shot [file.png]` saves the last frame.

## 2. Look

References, in Jeremy's words "slick, sexy and beautiful": Linear (dense, quiet chrome, keyboard first), Plain (the Status model, the Thread layout), Superhuman (speed, one thing at a time), and `herdr-app` (sidebar plus work area, no title bar).

- **Themes.** Two ship. `gruvbox` (dark) matches Jeremy's `herdr-app` (`themes/gruvbox.json` on Mani: background `#282828`, foreground `#ebdbb2`, selection `#4b3f27`, accent `#d79921`). `gruvbox-material-light` matches the Console's palette (decision #88: bg0 `#f9f5d7`, fg0 `#654735`). The default follows `settings.json` `appearance`.
- **Semantic tokens.** Views use tokens only, never raw colours: `background`, `surface`, `surface-raised`, `border`, `text`, `text-muted`, `accent`, `selection`, `focus`, and one token per Status detail (`status-needs-first`, `status-needs-next`, `status-investigating`, `status-reply-draft`, `status-waiting`, `status-done`, `status-ignored`). They use the Console's Status colours (`docs/design/console.md` §2), and colour never carries meaning alone.
- **Type.** The UI font is the system sans at 13 px for lists and 14 px for message bodies. Code and IDs use `VictorMono Nerd Font`, the font Jeremy set in `herdr-app`. There are 4 sizes in all: 12, 13, 14 and 18 (the Thread title).
- **Density.** Rows are 30 px (the Phase 0 probe used 30 px) on an 8 px grid. Chrome is 1 px borders, with no shadows except on overlays.
- **Message bodies.** Rendered by litehtml in a light card in both themes, because mail is designed for light backgrounds. Links keep their colour and underline, and paragraphs keep their margins. The email stylesheet must not reset `a` or `p` (Phase 0 found that `litehtml`'s email preset does). Long URLs wrap.

## 3. Screens

The default layout is `main`: a sidebar (rail) on the left and the work area on the right, as in `herdr-app`.

- **Rail**: Today, Todo, Snoozed, Done, Ignored with counts (`rail.summary`); Cases, Drafts, Tasks, Calendar, Sequences, Pipelines, People, Organizations, Channels.
- **List + Thread**: a list of the current view (`threads.queue`) beside the selected Thread (`thread.view`), laid out like the Console's Thread (conversation and reply box on the left, properties on the right). Moving the selection shows the next Thread at once, as Jeremy chose on 2026-10-19 (ADR 0004 consequences).
- **Message**: the header (name, absolute and relative time, to and cc), then the body (`message.body`). Remote images show their alt text until "Load images" (`message.load_images`). Trimmed content folds behind `•••` as in the Console.
- **Reply box and Drafts**: as the Console's composer (#92, #99). Files dropped on it are staged (`upload`). Send and schedule are view-only (Origin `view`).
- **Status bar**: online, or read-only with the reason (`core.status`); toasts from the Local core's events.

## 4. Keys

The default `keys.json` gives every Console binding the same key (`docs/design/console.md` §4). Each Console binding is either mapped or listed with a reason, and `tests/test_app_parity.py` checks this (FR-6). Mouse: one click opens links and Attachments; drag and drop attaches files.

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
