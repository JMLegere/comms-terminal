# A Platform is any outside service; a Connection is one account of one kind

Status: accepted (2026-11-07). Discovery: `.agents/discovery/2026-11-07-linear-daily-scheduling.md`. Design: `.agents/design/2026-11-07-issues-and-action-queue.md`.

Until now a Platform was a message service (Gmail, Fastmail, Slack, Quo, Google Calendar) and Tana, Attio and Linear were "Context sources", a separate idea with its own key handling and no Connection page. Issues make Linear something Comms Terminal both reads and writes, and Jira will follow, so a third special case would be needed. Instead, a **Platform** is any outside service Comms Terminal talks to, and a **Connection** is one account on a Platform of one kind: Email, SMS, Slack, Calendar, Context, or Issue. Every kind shares the Connection's sign-in state, last error, health probe and Connection page, and a broken one shows as a Problem.

One account can back two Connections. Linear has one Jeremy-owned key with Read and Write, behind a Context connection (searches the whole workspace beside Threads) and an Issue connection (Jeremy's assigned Issues), each with its own health.

## Considered Options

- **Keep Context sources apart and add "Issue providers"**: three ways to hold an outside account, each with its own errors and settings.
- **One Connection per account with several roles**: fewer rows, but a broken write key and a working read would share one health line.

## Consequences

- `connections.platform` widens to Linear, Tana and Attio, and gains a kind. Context sources' keys move onto Context connections.
- "Context source" leaves the glossary for "Context connection".
