# Build a purpose-built Thread ledger instead of adopting a support platform

Comms Terminal is built as a narrow system that owns exactly the domain in `GLOSSARY.md` (Thread, Activity, Status, Case, Person, Organization, Connection). It does not adopt an open-source support platform such as Chatwoot or extend the open-source "Universal Inbox" project. The core of the product is trust: provable completeness against each source and visible sync health. No researched product provides that core, so every adopt-or-extend option would bolt it onto a model built for something else. The cost is that every Connection becomes code we own and maintain.

## Considered Options

- **Chatwoot plus custom glue**: its conversation statuses and reopen-on-reply behaviour match Plain closely. It lost for three reasons. Its email inbox reads only INBOX, so mail Jeremy sends from Gmail would never be imported. It brings support-desk concepts the domain does not have (agents, inboxes, CSAT). And it needs a heavy multi-service runtime, with the trust core sitting as glue on a large codebase we do not control.
- **Extend the open-source "Universal Inbox" (universal-inbox.com)**: it already has Gmail and Slack connectors, snooze, and a console. It lost because it captures selectively (only starred Gmail threads and Slack messages marked with a reaction), which is the opposite of our coverage rule. Its notification/task model would need a rework in an unfamiliar stack, and it effectively has a single maintainer.
- **Matrix/Beeper chat hub as the substrate**: it gives one event stream for chat Platforms. It lost because no maintained bridge reads an existing Gmail mailbox, so email (an MVP Platform) would need a second ingestion path anyway. The idea survives as an optional chat relay for WhatsApp and LinkedIn in later releases.
- **Extend Donna's Company Model**: it lost because Donna misses threads today, and it would mix company goal control with personal triage.

## Consequences

- Gmail and Slack connect through official APIs as internal, single-user apps. Later chat Platforms come in through bridges.
- Scope ships in complete increments, starting with Gmail and Slack together so the model is multi-platform from the first release (`docs/requirements.md`); later Platforms follow one at a time, so the tool is never half-finished in daily use.
- Full evidence and the option comparison are in `.agents/discovery/2026-10-10-comms-terminal.md`.
