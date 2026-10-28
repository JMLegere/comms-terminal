# Meetings are Activities on Threads, read from Google Calendar

Status: accepted (2026-10-24). Supersedes the 2026-10-13 decision "meetings aren't threads" (PRD decision on Tana as a Context source) and the term Event provider. Amended 2026-10-28 by PRD decision #142: every Event is a Meeting Thread under the same rules, internal Events and Jeremy's own Blocks included, and Jeremy's Response to an Event booked before go-live starts its Thread.

Jeremy's Organization actions were scattered across Attio, Tana, Linear, and the console, and Organizations went quiet unseen (`.agents/discovery/2026-10-24-pipeline-account-actions.md`). Every GTM action ends in a message or a meeting, and "a meeting is just synchronous comms", like the Phone calls that already sit on Threads. So Google Calendar becomes a read-only Platform: an Event with an attendee outside agentmaple.com puts a Meeting Activity (where: Google Meet, Zoom, Microsoft Teams, phone, or in person) on a Thread, one Thread per Event or repeating series. A new such Thread starts in Todo like every new Thread; the Thread goes to Todo when the Meeting ends or is cancelled or declined; Tana's summary attaches like a Call transcript and never changes Status. Only Events booked after this ships become Threads, including invites Jeremy declined or has not answered. With meetings on Threads, an Organization needs no separate next-step or task object: it is Unattended when its People have no Todo or Snoozed Thread and no unfinished Enrollment.

## Considered Options

- **Meetings stay calendar context only** (the 2026-10-13 decision). Rejected: meeting prep and follow-up then need a second to-do object beside Threads, which is the scattered-actions problem again.
- **A Meeting joins the attendees' existing email or SMS Thread.** Rejected: meetings booked by SMS or Slack have no email Thread, and one Thread would mix many meetings.
- **Each meeting tool (Google Meet, Zoom, Microsoft Teams) is a Platform.** Rejected: the console connects only to Google Calendar, so those tools have no Connection or adapter; they are the Meeting's "where".
- **Slack huddles as Meetings.** Deferred: Slack exposes them only through an undocumented message shape.

## Consequences

- A meeting Jeremy books himself costs one triage (snooze it to the prep day).
- A phone meeting booked in the calendar and then placed on Quo yields two Threads; accepted until it proves common.
- Past Meetings stay on the Calendar only; Organization pages show Meetings from go-live on.
