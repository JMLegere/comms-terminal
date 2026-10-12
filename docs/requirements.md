# Universal Inbox — Requirements

Status: MVP confirmed 2026-10-10; revised 2026-10-12 to follow Plain's status model and integration architecture and to add replying from the console. Behavior follows Plain unless a deviation is stated with its reason. The detailed, traceable version is `docs/prd.md`. Builds on `.agents/discovery/2026-10-10-universal-inbox.md`, `GLOSSARY.md`, ADR 0001, and ADR 0002.

## MVP

- **Platforms**: email and Slack, so the model is multi-platform from the start.
- **Channels**: the build delivers an Email channel and a Slack channel. Jeremy connects `jeremy@agentmaple.com` and the Agent Maple workspace himself after the build. Other mailboxes (agentmaple.ai, jambr.ca) are out of scope.
- **History**: no backfill. Ingestion starts at connect, as in Plain.
- **Email** (Plain):
  - Receiving: a Google Workspace routing rule forwards a copy of each inbound message to the inbox; Gmail keeps delivering it.
  - Every new inbound email creates a Thread; replies that reference it join it.
  - Auto-replies are filtered by Plain's header list; a subject pattern counts only with a header.
  - Sending: console replies go out through an outbound mail provider from `jeremy@agentmaple.com`, threaded with `In-Reply-To` / `References`.
  - Deviation: replies default to reply-all, because Jeremy's conversations often include several people and Plain drops CC.
  - Accepted limit: replies sent from Gmail directly are not seen.
- **Slack** (Plain):
  - A bot joins selected channels, chosen by hand or by name prefix/suffix rules; messages arrive as events.
  - Messages group by time: the same user within 2 hours, or different users within 1 hour, form one Thread.
  - Deviation: a top-level message Jeremy posts starts a Thread in Waiting for them, because he is waiting on an answer.
  - Console replies post from Jeremy's Slack profile, in the reply-thread when there is one.
  - Deviation: DMs and group DMs also sync, through events on Jeremy's behalf, because they carry most of Jeremy's Slack comms.
- **Status** (Plain): a Status and a Status detail.
  - Todo: Needs first response, Needs next response, Investigating.
  - Snoozed: Waiting for them, Paused for later.
  - Done: Done, Ignored.
  - Jeremy's reply moves a Thread to Waiting for them.
  - Any activity from the other side, including edits and deletions, returns a Snoozed or Done Thread to Todo / Needs next response.
  - Ignored does not move.
  - Rules Plain does not state (confirmed): a second message before Jeremy's first reply keeps Needs first response; a message on Investigating gives Needs next response; un-ignore gives Needs next response if Jeremy has replied, else Needs first response; a timed snooze returns to the Todo detail it had before.
- **Ignore and Block**:
  - Ignore is the Done / Ignored Status detail, and the Ignored view is a filter.
  - Block applies to one email address or Slack user.
  - New Threads from blocked senders are created as Done / Ignored.
  - Every new sender reaches the queue by default.
- **People**:
  - A Person is a Slack user and/or a set of email addresses, auto-created on first contact.
  - Organizations are auto-created from email domains; free-mail domains never form one.
- **Cases**: an optional grouping of Threads; a Thread is in at most one Case; a Case has no Status of its own.
- **Records**: Person and Organization records list Threads (with Status) and Cases.
- **Replying**: plain-text replies from the console on both channels. The console never edits, deletes, archives, or marks read anything in Gmail or Slack.
- **Sync health** (Plain): a Connection shows broken when Slack revokes the token or uninstalls the app, a reply fails to send, or an inbound email request is rejected. No polling or reconciliation; completeness relies on event delivery.
- **Console**: a dark terminal app (Textual) run over SSH on Workbox; replies are sent by the service, which holds the credentials.
- **Notifications**: none; pull only.
- **Keyboard**:
  - Plain keys: J / K, E Done, Z Pause for later, W Wait for them, S change status.
  - R opens the inline reply box; Ctrl+S sends.
  - Deviations: I Ignore and C add to Case; O links to the Thread in its native app.

## Deferred (after MVP)

- Other Platforms: tool comments, WhatsApp, LinkedIn.
- Other mailboxes and Slack workspaces.
- Rules by domain, category, or content.
- Starting new conversations, attachments, rich text, drafts, undo-send, signatures.
- Cmd+K command palette.
- CRM, Tana, and Linear context on records.
- Plain's "Close the loop" status detail and auto-resolution.
