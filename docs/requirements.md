# Comms Terminal — Requirements

Status: MVP confirmed 2026-10-10; revised 2026-10-12 to follow Plain's status model and integration architecture and to add replying from the console; revised 2026-10-15 for the read-only Linear Context design and implementation. Under Jeremy's "ship it", release `38e196e` is published to `main`/`dev` and deployed to the existing Workbox package/service. Linear was connected on 2026-10-16 with a Read-only key covering both workspace teams; verification results, limitations, and deployment receipt are in `docs/design/linear-context.md`. Behavior follows Plain unless a deviation is stated with its reason. The detailed, traceable version is `docs/prd.md`. Builds on `.agents/discovery/2026-10-10-comms-terminal.md`, `GLOSSARY.md`, ADR 0001, and ADR 0002.

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
  - Messages group by time: a conversation's messages form one Thread until it has been quiet for 24 hours (7 days for SMS), whoever sent them (decision #126).
  - Deviation: a top-level message Jeremy posts starts a Thread in Waiting for them, because he is waiting on an answer.
  - Console replies post from Jeremy's Slack profile: in a channel always inside a Slack reply-thread (the Thread's own, or under its latest top-level message); in a DM top-level unless the Thread is made of replies.
  - Deviation: DMs and group DMs also sync, through events on Jeremy's behalf, because they carry most of Jeremy's Slack comms.
- **Status** (Plain): a Status and a Status detail.
  - Todo: Needs first response, Needs next response, Investigating, Draft ready (shown when a reply Draft is ready; never stored).
  - Snoozed: Waiting for them, Paused for later.
  - Done: Done, Ignored.
  - Jeremy's reply moves a Thread to Waiting for them. Deviation (#116): with no reply in 4 days it returns to Todo (Needs next response) to follow up; there is no key to wait without replying.
  - Eligible activity from the other side, including edits and deletions, returns a Snoozed or Done Thread to Todo / Needs next response (Block exception below).
  - Ignored does not move.
  - Rules Plain does not state (confirmed): a second message before Jeremy's first reply keeps Needs first response; a message on Investigating gives Needs next response; un-ignore gives Needs next response if Jeremy has replied, else Needs first response; a timed snooze returns to the Todo detail it had before.
- **Ignore and Block**:
  - Ignore is the Done / Ignored Status detail, and the Ignored view is a filter.
  - Block applies to one Identity (email address, Slack user, or phone number), independently of its Person or domain.
  - New Threads from blocked senders are created as Done / Ignored.
  - Approved deviation (#138, 2026-10-20): blocked ordinary inbound messages, edits, and deletions stay in history and skip arrival-driven Status application on existing Threads. Other participants follow normal rules.
  - Phone call rules, independent timers, complete email merge triage, explicit actions, Jeremy's own messages, and Sequence pause hooks remain unchanged.
  - Unblocking applies normal rules to future activity and new Threads; current triage stays as it is, including Ignored until Jeremy un-ignores it.
  - Every new sender reaches the queue by default.
- **People**:
  - A Person is a Slack user and/or a set of email addresses, auto-created on first contact.
  - Organizations are auto-created from email domains; free-mail domains never form one.
- **Cases**: one goal driven through Threads; a Thread is in at most one Case; a Case's Status is the earliest of its Threads' Statuses (Todo, Snoozed, Done, Ignored), Todo when it has none (#120).
- **Records**: Person and Organization records list Threads (with Status) and Cases.
- **Context sources**: read-only Attio, Tana, and Linear context beside Threads and Person, Organization, and Case records. Cases inherit context through their People and Organizations. Linear is published, deployed, and connected (2026-10-16); live console rendering awaits human acceptance. Linear task management remains out of scope.
- **Replying**: plain-text replies from the console on both channels. The console never edits, deletes, archives, or marks read anything in Gmail or Slack.
- **Sync health** (Plain): a Connection shows broken when Slack revokes the token or uninstalls the app, a reply fails to send, or an inbound email request is rejected. No polling or reconciliation; completeness relies on event delivery.
- **Console**: a dark terminal app (Textual) run over SSH on Workbox; replies are sent by the service, which holds the credentials.
- **Notifications**: none; pull only.
- **Keyboard**:
  - Plain keys: J / K, E Done, Z Pause for later, W Wait for them, S change status.
  - R opens the inline reply box; Ctrl+S sends.
  - Deviations: I Ignore and C add to Case; O links to the Thread in its native app.


## Drafts-first expansion

Authorized local expansion: Drafts replaces All under Threads, with ordinary page-row styling and native active state. Entering creates nothing; `n` creates a Draft. Email, SMS, and Slack fields save with an expected Draft revision and resume after restart. A Slack Draft posts into one existing conversation Jeremy is in, chosen from his live Slack conversations. Exact captured recipient history does not require a Person link. Sending an email Draft starts a new Thread; a sent SMS or Slack Draft groups with its conversation like any message (decision #126).

## Attachments

Approved 2026-10-15 (decisions #127–#137; `.agents/discovery/2026-10-15-attachments.md`). Everything stays inside the inbox app; nothing is installed on Mani.

- **Receiving**: Gmail, Slack, and SMS Attachments and Inline images are copied on arrival into one store on Workbox. Each chip opens its copy on Mani through a LAN link; a file that could not be copied keeps its Platform link. Images show in the message card. Remote email images are never fetched.
- **Agents**: `y` gives each Attachment's Workbox path.
- **Sending**: email and Slack replies and Drafts attach a file on Workbox, a file uploaded from Mani through a 1-hour link, or a pasted image. Forward makes an email Draft with the message's Attachments. Email Attachments are capped at 25 MB before sending.
- **Retention**: copies expire after a year, unless staged or shared with a newer message.
- **Non-goals**: Fastmail Attachments in either direction, sending by SMS, Drive files (sent as links), rich text, and links away from the home network.

## Deferred (after MVP)

- Other Platforms: tool comments, WhatsApp, LinkedIn.
- Other mailboxes and Slack workspaces.
- Rules by domain, category, or content.
- Sending new conversations, rich text, undo-send, signatures. Persistent unsent Drafts are included in the Drafts-first expansion above.
- Cmd+K command palette.
- Plain's "Close the loop" status detail and auto-resolution.
