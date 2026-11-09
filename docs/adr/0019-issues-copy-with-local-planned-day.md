# Issues are a copy of their Platform's records; the Planned day never leaves Comms Terminal

Status: accepted (2026-11-07); Rollover, the Slip status and the Blocked consequence superseded by ADR 0023 (2026-11-09). Discovery: `.agents/discovery/2026-11-07-linear-daily-scheduling.md`. Design: `.agents/design/2026-11-07-issues-and-action-queue.md`.

Jeremy works comms in Comms Terminal but his ~57 Linear issues only in Linear, where nothing says which day he will do each, whether a day is overbooked, or what slipped. He wants to manage Issues in Comms Terminal as Super Productivity (with his SuperPlan plugin) and Sunsama let him, while Linear stays the source of truth.

Decision:

- **Copy, not pull.** The Service keeps a copy of every Issue Linear's "assigned to me" filter returns, fed by a signed webhook at `/hooks/linear` and a full read every 30 minutes. The copy reaches Clients through Change sets like every other row (ADR 0012), so the Issues page renders from the laptop copy within the 100 ms target and works offline.
- **The Platform owns every Issue field.** Title, description, status, priority, estimate and due date change only by an Action that Linear confirms (ADR 0020). Console, App and CLI can create and update those fields; agents still follow Jeremy's Linear rules.
- **Comms Terminal owns the Planned day**, as it owns Stage (ADR 0006). It is never written to Linear and is never the due date. There is no stored day list: a day's Issues are the Issues with that Planned day.
- **Rollover.** When Jeremy's day (America/Halifax, Monday to Friday, as Sequences count business days) changes, every unfinished Issue planned before it moves to the next workday and its Slip status (Blocked in Linear) is written back.
- **Day capacity** is 9 hours less the rest of today's busy calendar time (Events not declined and not free); an Issue counts its estimate (Linear: 1 point = 30 minutes) or 1 hour.
- **Urgency** is a Taskwarrior-style sum (due, priority, started, blocking, blocked, age) with coefficients stored in the database. SuperPlan's warnings use it: planned after the due date, planned more than 100 − p days out at urgency percentile p, and larger than a day.

## Considered Options

- **Pull Issues from Linear on demand**: no copy to keep true, but every render waits laptop → Service → Linear (the key stays on the Service) and the page is blank offline. Capacity, warnings and Rollover each need every planned Issue anyway.
- **Plan inside Linear** (cycles, a Today label, saved views): no future day, no capacity against the calendar, and the plan becomes labels the team sees.
- **A separate planner** (Super Productivity again, Sunsama, Akiflow): a second app beside the console, which is why Jeremy left Super Productivity.
- **Store the Planned day in Linear**: Linear has no such field; a label or the due date would mislead the team.

## Consequences

- The team sees Blocked on Issues that slipped, written as Jeremy.
- A Platform without a Slip status cannot be rolled over until one is chosen.
- Jira (Serenity Vet) joins later as a second Issue connection with its own Slip status and estimate unit.
