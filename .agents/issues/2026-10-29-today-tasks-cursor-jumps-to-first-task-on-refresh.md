# Issue Fix Report — Today Tasks cursor jumps to the first Task on refresh

- **Status:** Complete (implementation and shipping authorized separately by Jeremy: "ship the fix", 2026-10-29)
- **Mode:** Autonomous
- **Issue key:** None
- **Created:** 2026-10-29
- **Last updated:** 2026-10-29
- **Scope:** On Today, with a later Due Task selected (Evan Boerchers' email Task), each refresh moves the Tasks cursor and the right pane back to the first Due Task (Nic Robichaud's overdue call). Non-goals: the thread-pane flash on no-change refreshes (2026-10-20 report), Draft autosave timing, other lists' cursors.

## Executive Summary
Validated: on the live build (`dcb9b57`, origin/main), any rebuild of the Tasks list moves the cursor from the selected later Task to the first Task under the group header, and the right pane follows. It reproduces for call and email Tasks and for all three triggers (sync snapshot `refresh_all`, the 60 s `minute` tick, a direct `TasksPage.refresh_data`).

Root cause (high confidence, traced): `TasksPage.refresh_data` restores the right row, but its `table.clear()` first resets the cursor to row 0 and queues a `RowHighlighted(0)` message. That message is handled after the refresh. Row 0 is the OVERDUE group header, so the header-skip in `TasksPage.on_data_table_row_highlighted` steps the cursor to row 1, the first Task. While Jeremy types in a Task Draft, the Draft autosaves 2 s after the last keystroke, the write produces a sync snapshot, and `apply_snapshot` calls `refresh_all`; so every pause in typing jumps the cursor. The 60 s tick on Today adds more jumps. This violates FR-T11 ("The cursor stays on the same item across a rebuild").

Solution: ignore stale highlight messages in the Tasks list's handler (act only when the message's row is the table's current cursor row). One guard at the one handler covers every trigger and both the Today and standalone Tasks pages.

## Issue Seed
- **Reported facts:** Jeremy, 2026-10-29: "im on 'today' on comms terminal. i am filling out Evan's email and the app keeps refreshing and moving me back to nic robichaud's task."
- **Inferences validated:** a periodic or sync-triggered refresh resets the Tasks cursor; Evan's Task remained in the list.
- **Affected surface, conditions, and impact:** CommsTerminal on Mani (client build `dcb9b57`) over Oracle. Any Due Task other than the first in its list. Jeremy loses his place and editor focus every few seconds while writing a Task Draft. Typed text is saved before the pane switches (`view.save(refresh=False)`), so no text loss was observed.
- **Constraints and non-goals:** read-only investigation of live data; repository writes only after Jeremy's ship request.

## Validation
- **Question:** Does a Tasks-list rebuild move the cursor off a selected later Task?
- **Method and resources used:** Live read-only DB probe on Oracle (Tasks, Enrollments, Drafts, audit log); headless Textual pilot on an extracted `dcb9b57` tree with seeded data, two Due Tasks in one group, cursor on the second, then one refresh.
- **Evidence:** Live: Nic Robichaud's call Task (overdue) and Evan Boerchers' email Task (overdue, done 13:41:46 UTC when its Draft sent) were both Due in the same Enrollment-active set; Nic sorts first. Pilot: 6/6 scenarios (call/email × `refresh_all`/`minute`/`refresh_data`) end on the first Task, `showing` going from `task:2:…` to `task:1:…`.
- **Observed behavior, conditions, scope, and impact:** deterministic on every refresh when the selected Task is not the first row after a group header.
- **Disconfirming evidence / reproduction limits:** headless driver on seeded data; not reproduced in Jeremy's Ghostty window. Evan's Task had already sent before the live probe, so its position during editing is inferred from ordering (`due_at`, `id`).
- **Conclusion:** Validated
- **Confidence:** High

## Root Cause
- **Question:** What moves the cursor after `refresh_data` restores it?
- **Evidence chain:** instrumented pilot trace (scratch): `refresh_data` keeps id 2 → `move_cursor(row=2)` → refresh done with `cursor_row 2` → queued `RowHighlighted(0)` handled → header-skip `move_cursor(row=1)` → `RowHighlighted(2)` handled with `selected()` already on row 1 → `RowHighlighted(1)` shows Task 1. Textual `DataTable.clear()` sets `cursor_coordinate = Coordinate(0, 0)`.
- **Causal mechanism and responsible boundary:** stale `RowHighlighted` from `clear()` reaching `TasksPage.on_data_table_row_highlighted` (`src/comms_terminal/tui.py`), whose group-header skip (added 2026-10-16, `47a9079`) treats it as a user move onto the header. Triggers: Draft autosave (`AUTOSAVE_GAP = 2.0`) → service write → `/sync` snapshot → `apply_snapshot` → `refresh_all`; `Console.minute` on Today; `check_changes` in local mode.
- **Alternatives ruled out or remaining:** Evan's Task leaving `tasks_due` (ruled out: Enrollment Active, no scheduled send); `refresh_data` choosing the wrong row (ruled out by trace). Other lists have no header-skip handler, so they do not show this jump.
- **Conclusion and confidence:** High.

## Solution
- **Current behavior and desired end state:** a rebuild leaves the cursor on the first Task; FR-T11 requires the same Task.
- **Candidate directions and tradeoffs:**
  1. Ignore stale highlights in the handler (row ≠ current cursor row). Smallest; covers every trigger and stale message source.
  2. Wrap the rebuild in `table.prevent(DataTable.RowHighlighted)`. Covers rebuilds only; relies on watcher timing inside the context.
  3. Rebuild rows without `clear()`. Larger change; also addresses flicker, out of scope.
- **Selected direction and rationale:** 1, a one-line guard at the shared handler.
- **Non-goals and deferred work:** refresh frequency while typing; repaint cost of `clear()`.
- **Update conditions:** a Textual change that posts highlight messages synchronously would make the guard unnecessary but harmless.

## Architecture Plan
### System and boundary map
Service write → `/sync` snapshot → `Console.apply_snapshot` → `MainScreen.refresh_all` → `TodayPage.refresh_data` → `TasksPage.refresh_data` (clear, add rows, `move_cursor(keep)`) → queued `RowHighlighted` messages → `TasksPage.on_data_table_row_highlighted` → `show_selected` (right pane).

### Implementation plan
1. **File:** `src/comms_terminal/tui.py`, `TasksPage.on_data_table_row_highlighted`
   - **Change:** return early when `event.cursor_row != table.cursor_row`.
   - **Contract/invariant:** user moves onto a group header still step onto the adjacent Task; `refresh_data` still shows the selected Task itself.
   - **Why:** drops the stale header highlight that causes the jump.

### Tests and verification
- **Automated tests:** `tests/test_today_ui.py::test_a_refresh_keeps_the_cursor_on_a_later_task[call|email]` (red before, green after); existing `tests/test_today_ui.py`, `tests/test_sequences_ui.py`, `tests/test_console.py` pass with the guard (61 passed in the scratch run); full suite before push.
- **Manual/smoke verification:** on Today with two or more Due Tasks, select a later one, type in its Draft, pause 3 s; the cursor stays.
- **Operational/delivery checks:** push to `main` deploys to Oracle; watch `/var/lib/comms-terminal-deploy/status/deployment.json`; no `docker exec` during deploy. The laptop console picks up the new build on restart.

### Delivery boundaries
- **Requires separate implementation authorization:** granted ("ship the fix").
- **Requires separate external/production authorization:** push to `main` (granted by the same request).
- **Dependencies and rollout risks:** none; client-only change, no schema change.

## Evidence Log
| Evidence | Source / access path | Phase | Supports / contradicts | Confidence / limitation |
|---|---|---|---|---|
| Live Tasks: Nic call (Due, overdue), Evan email (Done 13:41:46 UTC), Enrollment 24 Active | Oracle live DB, read-only probe | Validation | Supports ordering | High; Evan already sent at probe time |
| Pilot 6/6 jumps on build `dcb9b57` | Scratch pilot `test_probe.py` | Validation | Supports | High; headless |
| Trace of `RowHighlighted(0)` after refresh | Scratch pilot `test_trace.py` | Root cause | Supports mechanism | High |
| Autosave 2 s → snapshot → `refresh_all`; `minute` on Today | `tui.py` `AUTOSAVE_GAP`, `apply_snapshot`, `minute` | Root cause | Supports trigger frequency | High |
| Guard fixes 6/6; 61 existing console tests pass | Scratch monkeypatch probe | Solution | Supports | High |

## Open Questions and Blockers
- **Blocking:** none.
- **Non-blocking:** whether every autosave should trigger a full Today rebuild (perf/flicker).

## Phase History and Reopenings
| Phase | Outcome | Approval / mode decision | Evidence basis | Superseded by |
|---|---|---|---|---|
| Seed | Confirmed | Jeremy | Report + live probe | — |
| Validation | Validated | Autonomous | Pilot repro | — |
| Root cause | Stale header highlight | Autonomous | Trace | — |
| Solution | Stale-highlight guard | Autonomous | Probe | — |
| Architecture | One-line change + test | Autonomous; then "ship the fix" | — | — |

## Final Consistency Check
- [x] Validation describes observable behavior without claiming a cause.
- [x] Root cause is an evidenced mechanism, not a correlation.
- [x] Solution addresses the validated root cause without becoming a patch list.
- [x] Architecture plan implements the selected solution and includes tests, verification, and delivery boundaries.
- [x] Contradictory evidence and unresolved limits are visible.
- [x] Implementation and shipping followed Jeremy's separate explicit request.

## Stop Point
Investigation complete. Implementation shipped under Jeremy's separate request: commit `d5e823d` (branch `jeremy/tasks-cursor-refresh`) pushed to `main`; Oracle deployment `phase: complete` at that SHA. Full suite: 1654 passed, 1 failure in `tests/test_reactions_ui.py` that passes 3/3 in isolation on both the branch and the `dcb9b57` baseline (load-dependent flake). The laptop console reinstalls the service's build on its next sync (exit 75 relaunch); Mani was unreachable over SSH at ship time, so the client update is unverified.
