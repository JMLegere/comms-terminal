# Universal Inbox — Requirements

Status: MVP confirmed 2026-10-10, revised the same day by Jeremy's PRD review. Behavior follows Plain unless a deviation is stated with its reason. The detailed, traceable version is `docs/prd.md`. Builds on `.agents/discovery/2026-10-10-universal-inbox.md`, `GLOSSARY.md`, and ADR 0001.

## MVP

- **Platforms**: Gmail and Slack, so the model is multi-platform from the start.
- **Connectors**: the build delivers Gmail and Slack connectors. Jeremy connects `jeremy@agentmaple.com` and the Agent Maple workspace himself after the build. Other mailboxes (agentmaple.ai, jambr.ca) are out of scope.
- **History**: no backfill. Ingestion starts at connect, as in Plain.
- **Email** (Plain):
  - Every new inbound email creates a Thread; replies in the same Gmail thread join it.
  - Auto-replies, detected by headers, are filtered out and never reopen a Thread.
  - Deviation: Gmail Spam and Trash are not ingested, because they are the equivalent of mail never forwarded to Plain.
- **Slack** (Plain):
  - Only selected channels sync, chosen by hand or by name prefix/suffix rules.
  - Messages group by time: the same user within 2 hours, or different users within 1 hour, form one Thread.
  - A group started only by Jeremy becomes a Thread once someone else posts.
  - Deviation: DMs and group DMs also sync, because they carry most of Jeremy's Slack comms.
- **Status** (Plain):
  - Todo: Needs first response, Needs next response, Investigating.
  - Snoozed: Waiting for them, Paused for later.
  - Done: Done, Ignored.
  - Jeremy's reply moves a Thread to Waiting for them.
  - Any activity from the other side, including edits and deletions, returns a Snoozed or Done Thread to Todo.
  - Ignored does not move.
- **Ignore and Block**:
  - Ignore is a Thread Status, and the Ignored view is a filter.
  - Block applies to an email address or a Slack user; blocking a Person blocks all their addresses.
  - New Threads from blocked senders are created as Ignored.
  - Every new sender reaches the queue by default.
- **People**:
  - A Person is a Slack user and/or a set of email addresses, auto-created on first contact.
  - Organizations are auto-created from email domains; free-mail domains never form one.
- **Cases**: an optional grouping of Threads; a Thread is in at most one Case; a Case has no Status of its own.
- **Records**: Person and Organization records list Threads (with Status) and Cases.
- **Read-only mirror**: nothing in the console changes Gmail or Slack.
- **Sync health**: a warning after 15 minutes without a confirmed sync; broken on an auth failure or a reconciliation mismatch.
- **Notifications**: none; pull only.
- **Keyboard**:
  - Plain keys: J / K, E Done, Z Pause for later, W Wait for them, S change status.
  - Deviations: I Ignore and C add to Case; Enter opens the Thread in its native app.

## Deferred (after MVP)

- Other Platforms: tool comments, WhatsApp, LinkedIn.
- Other Gmail Connections and Slack workspaces.
- Rules by domain, Gmail category, or content.
- Write-back to Gmail or Slack.
- Cmd+K command palette.
- CRM, Tana, and Linear context on records.
- Plain's "Close the loop" status and auto-resolution.
