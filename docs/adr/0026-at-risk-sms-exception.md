# One SMS when a Promise or Prep goes At risk (narrow exception to "Pull only")

Status: accepted (2026-11-18); built for Promises (`promises.alert`), off until `comms-terminal alerts --mobile` is set. Narrows PRD NFR-3. Discovery: `.agents/discovery/2026-11-12-today-orient-fanout.md (2026-11-18 reopening)`.

NFR-3 says Comms Terminal is pull only, with no notifications. Jeremy's last dropped ball was found by remembering late; a list he has to look at cannot catch that.

Decision:

- When a Promise or a Meeting's Prep first goes At risk, Comms Terminal sends one SMS from Jeremy's Quo number to his own cell, with what, for whom, by when, and a link. Once only per Promise or Prep.
- Nothing else notifies. NFR-3 holds for everything else.
- This is Comms Terminal messaging Jeremy himself; it never messages anyone else.

## Considered Options

- **Stay pull only.** Simplest, but depends on Jeremy looking at the right moment.
- **A push app (for example ntfy).** Real push, but a new service and app (NFR-8).
- **Slack DM.** Needs a bot user to DM him.
- **Repeat until handled.** More reliable attention, but noise; Jeremy chose once.

## Consequences

- At risk is measured by Slack-time (time left less work and travel still needed), per Promise, chosen 2026-11-18 over hours-to-due, shared capacity planning, and buffer burn. It ignores other work competing for the same hours, so a crowded day can still surprise.
