# One optimistic Action Queue for everything Comms Terminal carries out

Status: accepted (2026-11-07). Design: `.agents/design/2026-11-07-issues-and-action-queue.md`. Builds on ADR 0004, 0012, 0013 and 0015.

Clients already apply their own writes at once and queue them for the Service (Mutations, ADR 0012). Writes that leave for a Platform do not: a send waits for its provider and locks on an unknown outcome (ADR 0015), Respond waits for Google Calendar (ADR 0009), reactions wait for Slack. Issue changes would add a fourth path, each about half a second behind the 100 ms target.

Decision: every **Action** (anything Comms Terminal carries out itself: a send, a Respond, a reaction, an Issue change, an Action Step's effect) goes through one **Action Queue** on the Service.

- **Optimistic.** A Client shows the Action's result at once; the Service carries it out in order per Connection.
- **Outcomes.** Confirmed: the copy already matches. Refused: the Platform's value comes back and the record shows "Not saved" with the reason. Broken sign-in: the Connection is marked broken (a Problem) and its Actions wait. Unknown: an Action that is safe to repeat is sent again; one that is not (a send) keeps the Send outcome unknown lock until Jeremy resolves it.
- **Safe to repeat.** Issue field changes are field sets; Issue creates carry a Client-chosen id, which Linear's `IssueCreateInput` accepts (checked live, 2026-11-07), so a repeated create cannot duplicate.
- **Conflicts.** A newer save wins over an older one (ADR 0012), whether it came from Jeremy or the Platform.
- **Undo** of an Action is a new Action that sets the old value back.
- **Visible.** The Action Queue page lists Actions scheduled, running, refused or unknown, grouped by day and Connection, as Lemlist's Outreach queue lists outgoing activities. Scheduled Drafts, coming Tasks and Enrollment starts show there too.

## Considered Options

- **Wait for the Platform, as Respond does**: never snaps back, but every Issue change waits about half a second.
- **Optimistic for safe Actions only, sends as today**: two mechanisms to keep, and no single place to see what is leaving.
- **Name it Outbound queue and rename Sequence Actions**: rejected; automation tools (HubSpot workflows, Zapier, Attio) already call a step the system carries out an action, so widening Action gives one meaning.

## Consequences

- The Sequence Step kind is renamed in the glossary to **Action Step**; stored kind `action` and the Sequence Markdown keyword stay.
- `inbox.send_draft`, Respond and reactions move onto the queue; the Local API stays grow-only (ADR 0013).
- A rare refused Action snaps back after it was shown.
