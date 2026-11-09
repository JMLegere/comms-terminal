# Plan Issues gives every unpinned Issue its day; Rollover and the Slip status are gone

Status: accepted (2026-11-09). Supersedes the Rollover bullet, the Slip status and the first Consequence of ADR 0019. Discovery: `.agents/discovery/2026-11-09-issue-auto-planning.md`.

Under ADR 0019 an Issue got a Planned day only by hand. Jeremy will never schedule Issues by hand, so none of 58 had a day and Today showed no Issues. He wants planning to work as his SuperPlan plugin did in Super Productivity.

Decision:

- **Plan Issues** is one action (`p` on the Issues page and Today in Console and App; `comms-terminal issues plan` for agents). It takes every open Issue that is not Pinned, scores it with the stored urgency plus SuperPlan's feasibility boost (a due-dated Issue with 0, 1 or 2–3 days of room before its due date gains 100 %, 75 % or 40 % of the `feasibility` coefficient, default 5), and fills workdays from today on: each day repeatedly takes the most urgent remaining Issue that still fits. An Issue larger than a whole workday goes on today without using capacity. Weekends get nothing. There is no horizon. Ties break by identifier, so the same inputs give the same plan.
- **Each run starts afresh.** It moves any unpinned Issue, including ones placed by hand (`w`) and ones already on today.
- **Pinned** (`f`) Issues are left alone, with or without a day. A Pinned Issue's day still uses that day's capacity; a Pinned Overdue Issue uses today's.
- **Today's capacity** is the hours left before 18:00, less busy time, at most 9 hours.
- **One run is one Command**; Undo puts back every Planned day it changed (ADR 0021). Undo refuses once a Linear read has rewritten any of those Issues, at most 30 minutes later.
- **Rollover is removed**, and with it the Slip status. An unfinished Issue keeps its passed Planned day, stays on Today (Due now for a day, then Overdue), and moves on the next Plan Issues. Nothing about planning is ever written to Linear.
- **Today** lists Issues that are Due now (due date or Planned day from yesterday to tomorrow; their hours count as today's) and Overdue Issues (older; their hours shown apart). Jeremy, 2026-11-09, after the first live run read "0 of 4 h · 4 h overdue" for a full day: "next 24h to last 24h counts as due now", "due or planned in the window".

## Considered Options

- **Keep Rollover, without Blocked.** A midnight move adds nothing once each run replans every unpinned Issue, and it would hide the passed day that marks an Issue Overdue.
- **Keep Rollover writing Blocked (ADR 0019).** The team read Blocked as blocked when it meant slipped; Jeremy dropped it.
- **Reserve a day before each due date, then fill by urgency.** Protects due dates but adds a second placement rule; SuperPlan's urgency boost plus the "Planned after its due date" warning was preferred.
- **Compute Today's Issues on each render, store no days.** No button and nothing stored, but no future days on the Calendar or the Issues page.
- **Keep Planned day and Pinned in their own table so Linear reads never block Undo.** Cleaner ownership, but a migration and a join for every Issue reader; the 30-minute Undo window was accepted.

## Consequences

- A press can move many Issues at once. Pin what must stay.
- New Issues stay Unplanned until the next press; they show on Today only if due today or earlier. An automatic run is a later option.
- FR-IS-10 and the Rollover scenarios in `features/issues.feature` and `features/modules.feature` are retired.
