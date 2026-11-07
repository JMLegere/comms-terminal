# PRD — Issues and the Action Queue

## 1. Document control

| Field | Value |
|---|---|
| Status | **Approved** by Jeremy 2026-11-07 (/ask round: "Approve all"), with ADRs 0017–0019 and the glossary changes. Step detail stays Proposed until built. |
| Owner / authority | Jeremy. Authority chain: Jeremy's instructions, then `AGENTS.md` (repository), then accepted ADRs in `docs/adr/`, then `.agents/` discovery, design and PRDs. ADRs 0017–0019 **accepted** 2026-11-07. |
| Created / updated | 2026-11-07T12:51-04:00 / 2026-11-07T13:20-04:00 (America/Halifax) |
| Topic | Manage Linear Issues inside Comms Terminal (Planned day, capacity, warnings, edits) with one optimistic Action Queue for everything Comms Terminal carries out. |
| Sources | `.agents/discovery/2026-11-07-linear-daily-scheduling.md` (approved through Phase 4); `.agents/design/2026-11-07-issues-and-action-queue.md` (proposed); ADRs 0017, 0018, 0019 (proposed); `GLOSSARY.md` (Issues section, Action, Action Queue); code at `origin/main` `85b5540`. |
| History limits | The full conversation of 2026-11-07 was available. Earlier sessions are known only through the artifacts cited. |
| Implementation authorization | **The entire PRD** (Jeremy, 2026-11-07: "execute the entire prd", after P0–P3 gates). P7's push to `main`, Worker deploy, Linear key and webhook still need Jeremy's explicit go and hands (PRD §5 P7). |

## 2. Executive framing

**Problem.** Jeremy works comms in Comms Terminal but his ~57 active Linear Issues only in Linear's list. Nothing says which day he will do each, whether a day fits around his Meetings, or what slipped, so he picks in the moment and status lags (discovery §3: 0 of 57 estimated, 19 with a due date, 4 past due, 9 due within a week). Writes that leave Comms Terminal each have their own path and wait on the outside service.

**Audience.** Jeremy (Console and App); agents through the CLI.

**Current behavior (verified on `85b5540`).**
- Linear is a read-only Context source: the client refuses mutations (`src/comms_terminal/linear.py:41`), the key is Read-only (`docs/runbook.md`, Linear section), and the Service builds it at start (`src/comms_terminal/serve.py:60`, `:996`).
- `connections.platform` allows only `gmail, fastmail, slack, quo, gcal` (`src/comms_terminal/schema.sql:3`); `platforms.Platform` describes message services (`src/comms_terminal/platforms.py:131`).
- Public hooks route through `serve.HOOKS` and `serve.intake` (`serve.py:688`, `:698`); each adapter has `handle_hook` (`slack.py:532`, `quo.py:135`).
- The Service's one-minute loop is `serve.wake_loop` (`serve.py:900`); the health probe is `serve.probe_loop` every 30 minutes (`serve.py:947`).
- Calendar occurrences carry kind, start, end, all-day and Jeremy's `PARTSTAT` (`calendar.py:160`), but not `TRANSP` (free/busy). `calendar.items` lays out Calendar Items (`calendar.py:253`).
- Sends go through `inbox.send_draft` with the Send outcome unknown lock (`inbox.py:1318`, `:767`; ADR 0015). Respond and reactions are separate Local API commands (`core.py:1145–1151`).
- Every database writer is in `agent.COMMANDS` or `agent.CONSOLE_ONLY` (`agent.py:20`, `:64`), enforced by `tests/test_cli_parity.py`.

**Desired outcome.** Jeremy plans, sees and changes his Issues in Comms Terminal; every change shows at once; Linear stays true; one Action Queue shows what is leaving and what did not.

**Scope.** Connection kinds; Linear Issue connection; Issue copy; Planned day, Day capacity, urgency, warnings, Rollover; Issue create/update of core fields; Action Queue (Issue Actions first, then Respond, reactions, sends); Issues page, Today, Calendar, Action Queue page in Console and App; CLI and Local API.

**Non-goals.** Board view; Linear saved views; comments, labels, projects, assignee and cycle edits; Jira (later Issue connection); time blocks; a Day plan entity; sending limits on the Action Queue.

**Dependencies.** A Linear key with Read and Write (Jeremy); a Linear webhook on the Business and Engineering teams (Jeremy, Linear settings); deploy authorization (Jeremy's "ship it").

**Success measures** (discovery §3, approved).

| # | Measure | Baseline 2026-11-07 |
|---|---|---|
| S1 | Each workday has planned Issues within capacity, or a visible warning | No plan |
| S2 | Every Issue Jeremy intends to do has a Planned day; the rest are Unplanned | None planned |
| S3 | Unfinished work rolls forward; due dates get a warning before they pass | 4 past due |
| S4 | Linear status true the same day | Completions land in batches |
| S5 | Planning under 10 minutes a day | Not measured |
| S6 | Still used after 4 weeks | — |
| S7 | Every Action shows on screen within the console's 100 ms target | Respond and sends wait on the provider |

## 3. Requirements and coverage

Status: **C** confirmed by Jeremy (G-number from design §2), **P** proposed here.

### Functional

| ID | Requirement | Status | Phase | Verification |
|---|---|---|---|---|
| FR-CK-1 | A Connection has a kind: Email, SMS, Slack, Calendar, Context, Issue. Platforms include Linear, Tana, Attio. | C (G1) | P1 | `tests/test_platforms.py`, `tests/test_schema.py` |
| FR-CK-2 | Tana, Attio and Linear context run as Context connections with Connection health and a Problem when broken. | C (G1) | P1 | `tests/test_linear_service.py`, `tests/test_tana.py`, `tests/test_attio.py` |
| FR-CK-3 | Linear has one key backing two Connections, Context and Issue, each with its own health. | C (G2) | P1, P2 | `tests/test_platforms.py` |
| FR-IS-1 | The Service keeps a copy of every Issue Linear's "assigned to me" filter returns, with its team's statuses. | C (G3, ADR 0018) | P2 | `tests/test_issues.py` (new) |
| FR-IS-2 | A signed Linear webhook at `/hooks/linear` updates the copy within seconds; a bad signature or stale `webhookTimestamp` is refused (401) and marks the Issue connection. | C (G11) | P2 | `tests/test_issues.py`, `tests/test_serve.py` |
| FR-IS-3 | A full read every 30 minutes adds, updates and removes copied Issues. | C (G11) | P2 | `tests/test_issues.py` |
| FR-IS-4 | Linear fields in the copy always come from Linear; the Planned day is kept across updates and never sent. | C (ADR 0018) | P2, P4 | `tests/test_issues.py` |
| FR-IS-5 | Jeremy sets or clears a Planned day; it is instant and local. | C (G6, G12) | P4 | `tests/test_issues.py`, `features/issues.feature` |
| FR-IS-6 | Day capacity = 9 h minus busy time; today counts only the rest of today; busy = Event not declined and not `TRANSP:TRANSPARENT`; a busy All-day Event zeroes the day; weekends 0 unless planned. | C (G8) | P4 | `tests/test_issues.py` |
| FR-IS-7 | An Issue's size is its estimate × 30 min, else 1 h. | C (G8) | P4 | `tests/test_issues.py` |
| FR-IS-8 | Urgency = sum of coefficient × factor for due (ramp 0.2 at ≥14 days out to 1.0 at ≥7 days late), priority (Urgent 9, High 6, Medium 3.9, None 1.95, Low 0), started (4), blocking (8), blocked (−5), age (2 × days/365, max 2). Coefficients stored in the database and settable. | C (G10); defaults P | P4 | `tests/test_issues.py` |
| FR-IS-9 | Warnings: planned after due date; planned more than 100 − p days out at urgency percentile p among planned open Issues; size larger than that day's capacity. | C (G9) | P4 | `tests/test_issues.py` |
| FR-IS-10 | Rollover: when Jeremy's Halifax date changes, each open Issue planned before today moves to the next workday and gets a Slip status Action (Linear: Blocked). | C (G6, G7) | P4 | `tests/test_issues.py`, `features/issues.feature` |
| FR-IS-11 | Jeremy and the CLI create Issues and update title, description, status (team picker), priority, estimate, due date. | C (G4, G5) | P3, P5 | `tests/test_issues.py`, `tests/test_cli_parity.py` |
| FR-IS-12 | An Issue no longer assigned to Jeremy leaves the copy after its pending Actions finish. | C (discovery: follow Linear) | P2 | `tests/test_issues.py` |
| FR-IS-13 | An Unplanned open Issue due within 7 days shows a due-soon warning on Today and the Issues page. | C (/ask: "Warn at 7 days") | P4, P5 | `tests/test_issues.py`, `features/issues.feature` |
| FR-AQ-1 | Every Action applies to the copy at once and is carried out by the Service in order per Connection. | C (G12, ADR 0019) | P3 | `tests/test_actions.py` (new) |
| FR-AQ-2 | Refused: the Platform's value returns and the record shows "Not saved: reason". | C (ADR 0019) | P3 | `tests/test_actions.py` |
| FR-AQ-3 | Refused sign-in (401/403): the Connection is marked broken (Problem); its Actions wait until a later success clears it. | C (G12) | P3 | `tests/test_actions.py` |
| FR-AQ-4 | Unknown outcome: an Action safe to repeat is resent; one that is not (a send) keeps the Send outcome unknown lock. | C (ADR 0019) | P3, P6 | `tests/test_actions.py`, `tests/test_send_draft.py` |
| FR-AQ-5 | Issue creates carry a Client-chosen id (`IssueCreateInput.id`, verified live 2026-11-07). | C | P3 | `tests/test_actions.py` |
| FR-AQ-6 | Undo of an Action is a new Action restoring the old value. | C (G13) | P3 | `tests/test_actions.py` |
| FR-AQ-7 | Respond, Slack reactions and sends run through the Action Queue with their current outcomes unchanged. | C (G17, ADR 0019) | P6 | `tests/test_invite_responses.py`, `tests/test_reactions.py`, `tests/test_send_draft.py` |
| FR-UI-1 | Issues page in the rail: Linear's List with Display options (grouping, sub-grouping, ordering, completed window, sub-issues, empty groups, properties), plus Planned day (grouping with "used of capacity", ordering, property) and Urgency (ordering). Options saved locally. | C (G14) | P5 | `tests/test_issues_ui.py` (new), App view test |
| FR-UI-2 | Today lists today's Issues with "used of capacity" and warnings. | C (G15) | P5 | `tests/test_today_ui.py`, `tests/app_today_view.mjs` |
| FR-UI-3 | Calendar places each planned Issue on its day as a Calendar Item. | C (G15) | P5 | `tests/test_calendar.py` |
| FR-UI-4 | Action Queue page: Actions scheduled, running, refused or unknown, plus scheduled Drafts, coming Tasks and Enrollment starts, grouped Today/Week and by Connection. | C (G17) | P5 | `tests/test_actions_ui.py` (new) |
| FR-UI-5 | Console and App ship the same pages in one release. | C (G16) | P5 | `tests/test_app_parity.py` |

### Non-functional

| ID | Requirement | Status | Phase |
|---|---|---|---|
| NFR-1 | Planned day and Issue edits paint within the console's 100 ms target (optimistic). | C (G12) | P3–P5 |
| NFR-2 | The Linear key stays in the Service; nothing prints it (AGENTS.md "Secrets stay in the service"). | C | P2 |
| NFR-3 | Local API grows only (ADR 0013); `schema/local-api.openrpc.json` regenerated. | C | P5 |
| NFR-4 | Every new writer is in `agent.COMMANDS` or `agent.CONSOLE_ONLY`. | C | P3–P5 |
| NFR-5 | Schema changes bump `SCHEMA_VERSION`, add `MIGRATIONS[n]`, update `schema.sql` and `tests/schema_history.py`. | C (AGENTS.md) | P1–P4 |
| NFR-6 | Agents may not send, complete Tasks or Respond (unchanged). Agent Issue writes stay bound by Jeremy's Linear rules. | C (G5) | P5 |

### Acceptance scenarios (to write in `features/issues.feature` and `features/actions.feature` first)

- **AS-IQ-1** Plan an Issue for Tuesday; it shows under Tuesday with "1 h of 6 h" when Tuesday has 3 h of Meetings.
- **AS-IQ-2** An Issue with 4 points planned today counts 2 h.
- **AS-IQ-3** Logan closes an Issue in Linear; within seconds it shows done on its day.
- **AS-IQ-4** Reassigned to Logan; it leaves the Issues page.
- **AS-IQ-5** Friday's unfinished Issue moves to Monday at Halifax midnight and shows Blocked after Linear confirms.
- **AS-IQ-6** An Issue planned after its due date shows the deadline warning.
- **AS-IQ-7** Jeremy sets status "Making It Happen"; it shows at once; Linear refuses (status of another team); it snaps back with "Not saved: …".
- **AS-IQ-8** The Linear key is revoked; the next Action marks the Issue connection broken; the App's bottom bar shows the Problem; Actions wait.
- **AS-IQ-9** A create times out; before sending it again the Action looks the Issue up by its id, finds it, and confirms: one Issue. (Revised 2026-11-07, decision 48.)
- **AS-IQ-10** A Draft send with unknown outcome stays locked exactly as today, now listed on the Action Queue page.
- **AS-IQ-11** An Unplanned Issue due in 5 days shows the due-soon warning; one due in 9 days does not.

### States

- **Action:** scheduled → running → confirmed | refused | unknown; unknown → running (safe to repeat) or locked (not safe).
- **Issue connection:** healthy ↔ broken (401/403 sets; next success clears).
- **Issue:** planned (day) ↔ Unplanned; open → closed (from Linear); copied → removed (unassigned or gone).

### Invariants

- Linear fields in the copy change only from Linear (webhook, full read, confirmed Action) or from an optimistic Action that is later confirmed or reverted.
- The Planned day never appears in any request to Linear.
- A send is never resent automatically.

## 4. Architecture and solution shape

### L1 Context — Proposed

People: Jeremy, agents (CLI), Linear team. External: Linear (Issue and Context connections), Google Calendar (busy time), message Platforms (sends). Diagram: design §4 L1.

### L2 Container — Current → Proposed

| Container | Current | Proposed change |
|---|---|---|
| Service (Oracle) | Hooks, loops, ledger, Respond, sends | `/hooks/linear`; Issue full read in `probe_loop`'s 30-min cadence; Rollover and Action runner in `wake_loop`; tables `issues`, `issue_statuses`, `actions`, `urgency` |
| Local core + App | Copy, Mutations, Local API | New queries and commands for Issues and Actions; Issues and Action Queue views |
| Console | Own copy | Issues and Action Queue pages; Today and Calendar additions |
| CLI | `agent.COMMANDS` | `issues`, `issue`, `urgency`, `actions` commands |

### L3 Component — Proposed

`linear.py` (client gains writes, `handle_hook`, `full_read`); new `issues.py` (plan, capacity, urgency, warnings, Rollover, create/update); new `actions.py` (enqueue, run, outcomes); `calendar.py` (busy time, Calendar Items); `platforms.py` (kinds); `serve.py` (hook, loops); `core.py`, `agent.py`, `tui.py`, `app/defaults/views/` (surfaces). Diagram: design §4 L3.

### L4 Code map

| File / symbol | Current | Change |
|---|---|---|
| `src/comms_terminal/schema.sql` `connections` | platform CHECK of 5 (`:3`) | add Linear, Tana, Attio; add `kind` |
| `src/comms_terminal/platforms.py` `Platform`, `PLATFORMS` | message services (`:131`, `:158`) | add `kinds`; add Linear, Tana, Attio entries with `hook_secret` for Linear |
| `src/comms_terminal/serve.py` `linear_context`, `contexts` | key from `credential("linear-token")` (`:60`, `:996`) | read from the Context connection |
| `src/comms_terminal/serve.py` `HOOKS`, `intake` | `/hooks/email`, `/hooks/slack`, `/hooks/quo` (`:688`) | add `/hooks/linear` |
| `src/comms_terminal/linear.py` `Client.query` | refuses mutations (`:41`) | add `mutate` used only by `actions.py`; add `handle_hook`, `full_read` |
| `src/comms_terminal/issues.py` | — | Proposed module |
| `src/comms_terminal/actions.py` | — | Proposed module |
| `src/comms_terminal/calendar.py` `_occurrence`, `items` | no `TRANSP` (`:160`); items (`:253`) | add `busy`; add planned Issues |
| `src/comms_terminal/serve.py` `wake_loop`, `probe_loop` | (`:900`, `:947`) | Rollover + Action runner; Issue full read |
| `src/comms_terminal/inbox.py` `send_draft` | lock (`:1318`) | enqueue through `actions.py`, same lock (P6) |
| `src/comms_terminal/core.py` method table | send, respond, reactions (`:1145–1151`) | Issue and Action queries/commands |
| `src/comms_terminal/agent.py` `COMMANDS`, `CONSOLE_ONLY` | (`:20`, `:64`) | new commands |
| `src/comms_terminal/tui.py` `TodayPage`, `CalendarPage` | (`:6037`, `:6429`) | Issues section; planned Issues |
| `app/defaults/views/` | `today`, `calendar`, … | `issues`, `actions` views; `today`, `calendar` additions |

### Alternatives and complexity

Rejected in discovery and ADRs: pull on demand; plan in Linear; separate planner; Respond-style waiting writes; renaming Sequence Actions. Added complexity: two modules (`issues.py`, `actions.py`) and four tables. Justified: no existing module owns outside records with local fields or a per-Connection write queue; Mutations (ADR 0012) cover laptop → Service only.

## 5. Phased execution plan (all steps Proposed)

Each phase is its own PR from a worktree off `main`, focused tests per phase, full `uv run pytest` once at the end (repository memory). Feature scenarios first (`features/AGENTS.md`).

### P0 — Scenarios

0.1 `features/issues.feature`, `features/actions.feature` (Proposed): scenarios 1–10 above tagged `wip`. Verify: `uv run pytest -m wip` collects them. Exit: Jeremy approves the scenarios. Auth: implementation go.

### P1 — Connection kinds (FR-CK-1–3, ADR 0017)

1.1 `schema.sql` `connections` + `inbox.MIGRATIONS[37]`, `SCHEMA_VERSION` 37, `tests/schema_history.py` inverse: add `kind` (default from platform), widen platform CHECK. Verify: `uv run pytest tests/test_schema.py tests/test_schema_equivalence.py`. Rollback: inverse migration.
1.2 `platforms.py`: `Platform.kinds`; entries for Linear, Tana, Attio. Verify: `tests/test_platforms.py`.
1.3 `serve.py` `linear_context`, `tana_context`, `attio_context`: read keys through their Context connection; failures set its `last_error`. Verify: `tests/test_linear_service.py`, `tests/test_tana.py`, `tests/test_attio.py`. Compatibility: existing credentials keep working.
1.4 Connection page (Console and App) lists all kinds. Verify: `tests/test_app_parity.py`.

### P2 — Issue copy (FR-IS-1–4, 12; NFR-2)

2.1 `schema.sql` `issues` (Linear id, identifier, team, fields, `planned_day`), `issue_statuses`; migration 38. Verify: schema tests.
2.2 `linear.py` `full_read(client)` → Issues assigned to me + team statuses; `issues.upsert` keeps `planned_day`. Verify: `tests/test_issues.py` with recorded GraphQL fixtures.
2.3 `linear.py` `handle_hook` (HMAC-SHA256 hex of raw body in `Linear-Signature`; `webhookTimestamp` within 60 s) and `serve.HOOKS["/hooks/linear"]`. Verify: `tests/test_serve.py` signed and unsigned cases.
2.4 `serve.probe_loop` (30 min) runs the full read for the Issue connection. Verify: `tests/test_issues.py`.
Exit: copy matches Linear for fixtures. Auth: none beyond implementation; live key and webhook wait for P7.

### P3 — Action Queue, Issue Actions (FR-AQ-1–6, FR-IS-11)

3.1 `schema.sql` `actions` (id, connection, kind, target, payload, safe_to_repeat, state, error, timestamps); migration 39.
3.2 `actions.py` `enqueue(db, kind, target, payload, safe)` applies the optimistic change and records the Action; writer kind `write` so the laptop paints at once.
3.3 `actions.py` `run(db, clients, now)` in `wake_loop` and kicked after enqueue: per-Connection order; outcomes per FR-AQ-2–4.
3.4 `linear.py` `Client.mutate` for `issueUpdate`/`issueCreate` (with `id`); `issues.create`, `issues.update`, `issues.set_status`.
Verify: `tests/test_actions.py` (refused, broken, unknown + resend, create id, undo). `tests/test_cli_parity.py`. Exit: all pass.

### P4 — Planning (FR-IS-5–10)

4.1 `issues.plan(db, issue_id, day | None)` writer `write`.
4.2 `calendar._occurrence` adds `busy` from `TRANSP` and Jeremy's `PARTSTAT`; `issues.capacity(db, day, now)`.
4.3 `schema.sql` `urgency` (name, value) seeded with defaults; migration 40; `issues.urgency`, `issues.warnings`.
4.4 `issues.rollover(db, now)` in `wake_loop` on Halifax date change, using Sequences' business-day helper; enqueues Slip status Actions.
Verify: `tests/test_issues.py` (capacity cases incl. All-day, remaining today; percentile; Friday → Monday). Exit: scenarios 1, 2, 5, 6 pass.

### P5 — Surfaces (FR-UI-1–5, NFR-1, 3, 4)

5.1 `core.py` queries `issues.list`, `issues.day`, `actions.list`; commands per writer; regenerate `schema/local-api.openrpc.json`.
5.2 `agent.py`: `issues`, `issue`, `issue plan`, `issue new`, `urgency list|set`, `actions`.
5.3 `tui.py`: `IssuesPage`, `ActionQueuePage`, Today section, Calendar items; render states in `docs/design/console.md` §5.
5.4 `app/defaults/views/issues`, `actions`; `today`, `calendar` additions; `keys.json`.
Verify: `tests/test_issues_ui.py`, `tests/test_actions_ui.py`, `tests/test_today_ui.py`, App view tests, `tests/test_app_parity.py`; screenshots of each page. Exit: Jeremy reviews renders.

### P6 — Respond, reactions, sends onto the queue (FR-AQ-7)

6.1 Respond (`serve.handle_respond`, `serve.py:169`) → Action, safe to repeat.
6.2 Reactions → Action, safe to repeat.
6.3 `inbox.send_draft` → Action, not safe; lock unchanged (ADR 0015).
Verify: existing suites `tests/test_invite_responses.py`, `tests/test_reactions.py`, `tests/test_send_draft.py`, `tests/test_send_later.py` unchanged and passing. Rollback: per-kind switch back to the direct call.

### P7 — Release (needs Jeremy)

7.1 Jeremy creates a Linear key with Read and Write for Business and Engineering and installs it per runbook. 7.2 Jeremy adds the Linear webhook (Issues, both teams) to `/hooks/linear` with its signing secret. 7.3 Full `uv run pytest`; push to `main` only with "ship it"; watch `/var/lib/comms-terminal-deploy/status/deployment.json`. 7.4 Read back: Issue count matches `list_issues assignee=me`; one plan, one status change confirmed in Linear.

## 6. Risks, unknowns and approvals

| Item | Impact | Evidence / status | Next | Owner | Blocks |
|---|---|---|---|---|---|
| Approve this PRD and ADRs 0017–0019 | All phases | Approved 2026-11-07 | — | Jeremy | No |
| Scenario approval after P0 | P1+ | Taken as given with the P1 go (decision 39) | — | Jeremy | No |
| P7 release go ("ship it", key, webhook, Worker deploy) | Live | Not given | Ask after P6 | Jeremy | Yes |
| Live Linear writes unexercised | P3 confirm path | Mutations checked against Linear's live schema by introspection only; no write sent (key is Read-only, writes need P7) | First confirmed Action after P7 | Jeremy | No |
| App not rendered natively (P1 rail, P5 Issues, Issue pane, Action Queue, Today pane) | FR-CK-2, FR-UI-1–5 visual | View-kit runs pass; no App build on Workbox, and the dev App stays off Jeremy's display | Look on Mani after the App updates from `main` | Jeremy | No |
| Linear webhook creation needs admin rights | P7 | Resolved: Jeremy is a Linear admin (`whoami` isAdmin, 2026-11-07) | — | Jeremy | No |
| Linear webhook payload/signature details | P2 | Resolved: verified against linear.app/developers/webhooks (hex HMAC-SHA256 of raw body in `Linear-Signature`; `webhookTimestamp` ms within 60 s; non-200 retried after 1 min, 1 h, 6 h; admins create webhooks) | — | Implementer | No |
| Issue webhook `data` field names (`assigneeId`, `stateId`, `teamId`, `labels` list) | P2 hook | Docs give the envelope, not every Issue field; not seen in a live delivery | First live delivery after P7; `normal` also takes the read shape | Implementer | No |
| Slip status Blocked misleads the team | S4 trust | Accepted trade-off | A week of use | Jeremy | No |
| No capacity buffer | S1 | Planning fallacy | Two weeks planned vs done | Jeremy | No |
| Unplanned Issues near due date warn nothing | S3 | Resolved: FR-IS-13, 7 days | — | Jeremy | No |
| Moving sends (P6) could change lock behavior | Double sends | Mitigation accepted (/ask): P6 ships only if existing send tests pass unchanged; per-kind switch back | Gate in P6 | Implementer | P6 |
| `AGENTS.md`, runbook say "Context sources" and Read-only key | Agent confusion | Stale after P1/P2 | Update in P1/P7 | Implementer | No |
| Diagrams in design doc not rendered | Review | Renderer blocked in session | Render at review | Jeremy | No |

## 7. Discussion and decision ledger

| # | When | Request / decision | Source | Status | Supersedes |
|---|---|---|---|---|---|
| 1 | 2026-11-07 | Paste: Linear lacks a planned date; options Today label or Sunsama/Akiflow | Jeremy (/discovery) | evidence | — |
| 2 | 2026-11-07 | New topic separate from "Focus and execution in Linear"; include time blocks; "implement sunsama in comms terminal" | Jeremy | approved, later narrowed | — |
| 3 | 2026-11-07 | Day plan only, no time blocks | Jeremy | approved | #2 time blocks |
| 4 | 2026-11-07 | Linear stays source of truth, like Tana/Attio | Jeremy | approved | — |
| 5 | 2026-11-07 | Reframe: manage Linear issues in Comms Terminal like Sunsama and Super Productivity | Jeremy | approved | daily scheduling only |
| 6 | 2026-11-07 | Scope see/plan/status now; edit/create/comment later | Jeremy | approved, then #22 | — |
| 7 | 2026-11-07 | Capacity counts items | Jeremy | superseded same day | — |
| 8 | 2026-11-07 | Capacity 9 h minus busy; 1 h default; estimate overrides | Jeremy | approved | #7 |
| 9 | 2026-11-07 | Rollover auto to next day, Blocked; my claim "no Blocked status" was wrong, verified live | Jeremy; Linear | approved | my claim |
| 10 | 2026-11-07 | 1 point = 1 h, then 30 min | Jeremy | 30 min approved | 1 h |
| 11 | 2026-11-07 | Day plan on Today; Linear changes within seconds; no Shutdown | Jeremy | approved | Shutdown |
| 12 | 2026-11-07 | Keep a copy, not pull on demand | Jeremy on recommendation | approved | — |
| 13 | 2026-11-07 | Rollover to next workday; SuperPlan warnings | Jeremy | approved | — |
| 14 | 2026-11-07 | Generalize to Issues with providers; providers two-way, source of truth | Jeremy | approved | Linear-only |
| 15 | 2026-11-07 | Planned day local-only; slip still writes status | Jeremy | approved | — |
| 16 | 2026-11-07 | Discovery Phase 4 approved | Jeremy | approved | — |
| 17 | 2026-11-07 | Connections by kind (email, sms, calendar, slack, context, issue) | Jeremy (grill Q1) | approved | "Issue provider" term |
| 18 | 2026-11-07 | Personal key; Linear "assigned" filter; team status picker; "introduce as little as possible" | Jeremy (Q2–Q4, Q6) | approved | — |
| 19 | 2026-11-07 | One key, two Connections | Jeremy (Q5) | approved | — |
| 20 | 2026-11-07 | Busy = not declined, not free, remaining today; Issues page + Today + Calendar | Jeremy (Q7, Q8) | approved | — |
| 21 | 2026-11-07 | Rollover 03:00 UTC, then UTC day, then reuse Jeremy's days (Halifax, Mon–Fri) after code check | Jeremy (Q9, Q13, Q23) | Halifax approved | 03:00 UTC, UTC day |
| 22 | 2026-11-07 | Agents full create/read/update via CLI, pass-through subset of Linear; rules still bind agents; console same as CLI; core fields only | Jeremy (Q11, Q15, Q18, Q22) | approved | #6 "later" |
| 23 | 2026-11-07 | Failed write marks Issue connection broken | Jeremy (Q10) | approved | — |
| 24 | 2026-11-07 | Score from Taskwarrior/Obsidian urgency; coefficients settable, in the database; SuperPlan percentile | Jeremy (Q12, Q17, Q21, Q25) | approved | fixed priority rule |
| 25 | 2026-11-07 | "No day plan, just projections on issues" | Jeremy (Q30) | approved | Day plan term |
| 26 | 2026-11-07 | Everything optimistic, as one general system | Jeremy (Q24, Q27) | approved | Respond-style waiting |
| 27 | 2026-11-07 | Full read every 30 min | Jeremy (Q28) | approved | 5 min proposal |
| 28 | 2026-11-07 | Issues page = Linear List with Display options; local options, assigned only | Jeremy (Q29, Q33, Q34, screenshot) | approved | fixed day groups |
| 29 | 2026-11-07 | All writes on the queue; sends keep lock | Jeremy (Q31) | approved | — |
| 30 | 2026-11-07 | ADRs for kinds, Issue copy, queue; undo = new Action | Jeremy (Q35, assumption 2) | approved | — |
| 31 | 2026-11-07 | Queue doubles as Lemlist Outreach queue; named Outbound queue, then "Action Queue" with Action widened after research | Jeremy | Action Queue approved | Outbound queue |
| 32 | 2026-11-07 | Branch for design from `origin/main` (checkout was 100 commits behind) | agent, disclosed | done | stale checkout |
| 33 | 2026-11-07 | Approve PRD, ADRs 0017–0019, glossary | Jeremy (/ask) | approved | Draft status |
| 34 | 2026-11-07 | Unplanned Issue due within 7 days warns (FR-IS-13) | Jeremy (/ask) | approved | open gap |
| 35 | 2026-11-07 | P6 sends gated on unchanged send tests, per-kind switch back | Jeremy (/ask) | approved | — |
| 36 | 2026-11-07 | Defaults kept: urgency coefficients, webhook spec check in P2, diagrams checked at PR, docs update in P1/P7 | Jeremy (/ask) | approved | — |
| 37 | 2026-11-07 | Commit locally only; no push, no PR | Jeremy (/ask) | approved | — |
| 38 | 2026-11-07 | Implementation: P0 only, stop for scenario review | Jeremy (/ask) | approved | none authorized |
| 39 | 2026-11-07 | "execute the entire pd up to p1": P0 scenarios accepted, P1 authorized | Jeremy | approved | #38 stop |
| 40 | 2026-11-07 | P1 stores `kind` only where one account backs two (NULL = the Platform's only kind, `platforms.kind_of`), so the 69 existing Connection inserts stay unchanged; uniqueness moves to (external_id, kind) | agent, implementation | done | step 1.1 "default from platform" |
| 41 | 2026-11-07 | Context connection health = Last Error from its Service route answers (502 sets, 200 clears); the separate "context" rail and status-bar rows in Console and App are removed; the start-up check stays and feeds the Service | agent, implementation | done | D8 context rail section |
| 42 | 2026-11-07 | "execute the entire pd up to p2": P2 authorized | Jeremy | approved | #39 stop |
| 43 | 2026-11-07 | The copy holds open assigned Issues plus those closed in the last 30 days (`linear.CLOSED_KEPT_DAYS`), for the Issues page's completed window | agent, implementation | done | "every Issue the assigned filter returns" (812 with all history) |
| 44 | 2026-11-07 | Full read uses three plain filters (open, completed since, canceled since): live check showed Linear's `or` beside `assignee` returned all 812 assigned Issues and `assignee` inside each branch returned 2534 | agent, live read-only check on Oracle | done | one `or` filter |
| 45 | 2026-11-07 | A webhook arriving before the first full read (Jeremy's Linear id unknown) is answered 200 and skipped; the read at start copies the Issue | agent, implementation | done | — |
| 46 | 2026-11-07 | P2 adds `linear-webhook-secret` to the container's optional credentials and `/hooks/linear` to the Email Worker's routes; deploying the Worker and installing the secret stay in P7 | agent, implementation | done | — |
| 47 | 2026-11-07 | "execute the entire pd up to p3": P3 authorized | Jeremy | approved | #42 stop |
| 48 | 2026-11-07 | A create whose outcome was unknown is looked up by its id before any resend (a found Issue confirms it); AS-IQ-9 reworded from "two creates with the same id" | agent, implementation | done | resend-only create |
| 49 | 2026-11-07 | Linear's `estimate` is an Int (live introspection): estimates are whole points; the CLI and writers refuse fractions | agent, live read-only check | done | REAL estimate sent as is |
| 50 | 2026-11-07 | The CLI's `issues` and `issue ID / issue new` land in P3, since every writer needs a command or a console-only reason (`tests/test_cli_parity.py`); `issues.undo` is console-only | agent, implementation | done | CLI in P5 |
| 51 | 2026-11-07 | A laptop write wakes the Service's loop at once (`/call` sets the kick), so an Action runs in about a second, not a minute | agent, implementation | done | — |
| 52 | 2026-11-07 | "execute the entire prd": P4–P7 authorized; P7's release steps keep their own gates | Jeremy | approved | #47 stop |
| 53 | 2026-11-07 | Today's capacity: the hours left in Jeremy's day (at most 9) less busy time still to come; a weekend day counts like a weekday once an Issue is planned on it | agent, implementation (interprets G8) | done | — |
| 54 | 2026-11-07 | "Larger than a day" means larger than its Planned day's capacity; blocking and blocked come from Linear relations of type blocks to open Issues (read only; a webhook keeps the last read's counts) | agent, implementation | done | — |
| 55 | 2026-11-07 | Rollover runs every pass of the Service's loop and moves only Issues whose Planned day has passed, so it needs no record of the last day it ran | agent, implementation | done | "on Halifax day change" trigger |
| 56 | 2026-11-07 | CLI: Planned day is `issue ID --plan DATE|today|tomorrow|none`; coefficients are `urgency list|set` | agent, implementation | done | `issue plan ID DATE` |
| 57 | 2026-11-07 | Console Display options live in `~/.config/comms-terminal/issues.json`; the App keeps them in its view's localStorage; neither syncs | agent, implementation (G14 "saved locally") | done | — |
| 58 | 2026-11-07 | The App's Issue pane is its own detail view (`views/issue`) holding the field keys; the Issues list and Today's issues pane send p/s/i/e/d/t/o there | agent, implementation | done | — |
| 59 | 2026-11-07 | The demo data (`scripts/seed_demo.py`) gains three synthetic Issues (none planned for today) and one refused change, so every Local API query answers on it | agent, implementation | done | — |

## 8. Work log

| Time | Actor | Activity | Result | Evidence | Kind |
|---|---|---|---|---|---|
| 2026-11-07 (undated within day) | agent | Discovery run, Phases 1–4 | approved | discovery doc | source-observed |
| 2026-11-07 (undated within day) | agent | Grilling; glossary and ADRs 0017–0019; design doc | written, uncommitted | worktree `jeremy/issues-design` | source-observed |
| 2026-11-07 (undated within day) | agent | Live check `IssueCreateInput.id` on Oracle (read-only introspection) | `id` present | session output | source-observed |
| 2026-11-07T12:51-04:00 | agent | This PRD written | Draft | this file | source-observed |
| 2026-11-07T13:20-04:00 | agent | /ask round: approvals and decisions 33–38 recorded | Approved; P0 authorized | this file | source-observed |
| 2026-11-07T13:35-04:00 | agent | P0: `features/issues.feature` (9) and `features/actions.feature` (6) scenarios, all `@wip` | `uv run pytest tests/test_features.py`: 163 passed, 18 deselected; `-m wip`: 18 failed (15 new + 3 existing App) | the P0 commit on `jeremy/issues-design` | source-observed |
| 2026-11-07T14:10-04:00 | agent | P1: migration 37 (connections kind, widened Platforms, account unique per kind); `platforms` kinds and Linear/Tana/Attio; `inbox.ensure_connection`; `serve.record_context`; Console and App rails; CLI `drafts` lists only sending Connections; AGENTS.md | Focused suites: 234 + 110 + 584 passed; Console rail, status line and Linear Connection page rendered as text in a Textual test run | the P1 commit on `jeremy/issues-design` | source-observed |
| 2026-11-07T15:05-04:00 | agent | P2: migration 38 (`issues`, `issue_statuses`); `issues.py`; `linear.full_read`, `normal`, `verify`, `handle_hook`; `serve.read_issues` in the 30-minute probe loop, Issue connection at start, `/hooks/linear` (Context connections take no hooks); container credential and Worker route; 3 scenarios un-`@wip` (AS-IQ-3 ×2, AS-IQ-4) | `tests/test_issues.py` 13 passed; focused suites 665 passed; live read-only run of the new queries on Oracle: viewer found, 99 Issues (57 open, 30 completed and 12 canceled in 30 days), every state id among 26 team statuses | the P2 commit on `jeremy/issues-design` | source-observed |
| 2026-11-07T16:20-04:00 | agent | P3: migration 39 (`actions`); `actions.py` (enqueue, run, latest_refusal); `linear.mutate` and `Client.write` (confirmed, refused, broken, unknown); `issues.update`, `create`, `undo` writers with carriers, snap-back and "Not saved"; Service runs the queue in `wake_loop`, kicked by laptop writes; `remote.MODULES` and Local API (`command.issues.*`); CLI `issues`, `issue`; 5 scenarios un-`@wip` | `tests/test_actions.py` 18 passed; focused suites 575 passed; laptop and service mint the same Action id; live read-only introspection: `IssueUpdateInput`/`IssueCreateInput` fields, `id` on create, `estimate` Int | the P3 commit on `jeremy/issues-design` | source-observed |
| 2026-11-07T17:10-04:00 | agent | P4: migration 40 (`urgency`); `issues.plan`, `capacity`, `size_hours`, `urgency`, `warnings`, `rollover`, `set_urgency`; calendar `busy` and planned Issues as Calendar Items; Rollover in `wake_loop`; relations in the full read; CLI `--plan`, `urgency`; 6 scenarios un-`@wip` | `tests/test_planning.py` 9 passed; focused suites 337 passed; live read-only relations query: 57 open Issues, 4 blocking and 8 blocked | the P4 commit on `jeremy/issues-design` | source-observed |
| 2026-11-07T18:30-04:00 | agent | P5: Console `IssuesPage` (Display options, field keys, new, undo), `ActionQueuePage`, Today's issues pane with capacity, planned Issues on the Calendar, rail Issues and Action Queue, help; Local API `issues.view`, `issue.detail`, `actions.queue`, Today's `issues`; App views `issues`, `issue`, `actions`, Today pane, nav, day, layout and keys; CLI `actions` | Console rendered in a Textual run on demo data plus synthetic Issues (list with Today/Tomorrow/Unplanned groups and capacity, right pane, Today pane `2 of 9 h`, Action Queue row, rail counts); App views run in the view kit (`tests/test_app_issues.py` 3 passed); UI and parity suites 272 passed. The App's GPU window was not opened: no App build on Workbox | the P5 commit on `jeremy/issues-design` | source-observed |

## 9. Sources and coverage

- Inspected: `src/comms_terminal/{schema.sql, platforms.py, serve.py, linear.py, calendar.py, inbox.py, agent.py, core.py}` at `85b5540`; `tests/` and `features/` listings; `app/defaults/views` listing; ADRs 0004, 0006, 0009, 0012, 0013, 0015; discovery and design docs; `GLOSSARY.md`.
- Not inspected: `tui.py` page internals beyond class anchors; App view code; `remote.py` on main (assumed unchanged from ADR 0012); Linear's official webhook docs.
- Untraced: S5 (planning time) and S6 (four-week use) need observation after release, owner Jeremy. "Unplanned near due date" has no requirement until decided.
- The `/prd` invocation performed no implementation. P0 (feature files) follows under decision 38.
