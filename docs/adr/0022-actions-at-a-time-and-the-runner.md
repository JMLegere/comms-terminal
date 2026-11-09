# Actions that reach a Platform run on the Action runner; the Action Queue is a timeline of Actions at a time

Status: accepted (2026-11-08). Discovery: `.agents/discovery/2026-11-08-action-queue.md`. Amends ADR 0020; builds on ADR 0021.

The Action Queue page listed Jeremy's own Tasks and Enrollments that had already started beside real Actions, at the wrong times, and in the App it hid who or what each row was about. Meanwhile sends and Responds ran inline and were only noted on the queue (Issues PRD decision 60), so a send could not wait for a broken mailbox or be retried the way an Issue change is.

Decision:

- **Two kinds of Action.** An Action that reaches a Platform (a send, a Respond, an Issue change) is carried out by the **Action runner**, with outcomes confirmed, refused and unknown, held per Connection while the Connection is broken. Every other Action (a Snooze wake, a follow-up return, an Enrollment start, an Action Step's effect) only changes Comms Terminal: it stays a time on its own record and the Service's timed pass carries it out. It has no refused or unknown outcome.
- **Sends join the runner when they fall due.** A Draft's send time stays the only copy of when it goes; at that time the Service queues a send Action and the runner sends it. Cancel and Undo still clear the Draft's send time. A refused send gives the Draft its send error back; an unknown one keeps the send lock until Jeremy checks. A Respond is queued at Jeremy's word and is safe to repeat.
- **The Action Queue is a read model.** One Service function builds it from the records that already hold each Action's time and outcome: Draft send times and send Actions, Thread snooze and follow-up times and their Audit entries (cause `woke`), Enrollment start times and states, Action Step Tasks. Every Client and the laptop copy call it. It lists only Actions that wait for a time (sends, wakes, enrols, steps), as one timeline: Upcoming (furthest ahead first), In flight, Sent (last 7 days, newest first), with per-day counts by kind. Issue changes, Responds and reactions happen when Jeremy asks and stay off it; a refused one shows "Not saved" on its record.

## Considered Options

- **Keep running as today and only fix the page.** Rejected: three ways of carrying things out stay, and a send cannot wait for a broken mailbox or be retried.
- **Every Action, internal ones too, on the runner.** Rejected: wakes, enrols and steps would carry refused and unknown states they can never reach, and each wake would become a runner job.
- **Queue a send Action when the Draft is scheduled.** Rejected: the time would live on both the Draft and the Action, and a cancel or reschedule that changed only one could still send (HubSpot's "sent after unenroll").
- **Record each internal Action in the Action list as it happens.** Rejected: it copies what the Thread's Audit entries, the Enrollment and the Task already hold.
- **Show the Calendar for Upcoming and keep the queue for In flight and Sent.** Rejected: Jeremy wants the future on the queue.

## Consequences

- Decision 60 of the Issues PRD (sends and Responds run inline, journaled) is superseded once sends and Responds move onto the runner; the page and read model ship first.
- `actions.queue` is rewritten; the App shows the Action Queue with a detail pane of the record behind the selected row; History has no page in the Clients (Jeremy, 2026-11-08: undo needs only the Command log; `comms-terminal history` keeps it readable).
- Failures show only as the row's label in Sent ("just in the row for now"); a missed unknown send is the accepted risk.
- An Issue change or Respond that fails shows only on its own record, not on the queue.
