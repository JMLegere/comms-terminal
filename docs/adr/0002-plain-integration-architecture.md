# Follow Plain's integration architecture, including for email

Comms Terminal receives and sends messages the way Plain does. Email arrives by forwarding: a Google Workspace routing rule copies each inbound message for `jeremy@agentmaple.com` to an inbound address, and replies written in the console go out through an outbound mail provider with SPF/DKIM records on `agentmaple.com`. Slack uses a bot in selected channels, delivers messages as events, and posts replies from Jeremy's own profile; Jeremy's DMs arrive as events on his behalf. Ingestion is event-driven, and Thread Status keeps Plain's Status plus Status detail.

The agent recommended reading the Gmail mailbox through Google's API (push notifications and API sending), because that sees replies Jeremy sends from Gmail and lets the system reconcile email against the source. Jeremy chose Plain's model. The accepted costs are:

- Replies sent from Gmail directly are invisible, so their Threads stay Todo until marked.
- Email completeness cannot be reconciled; a broken routing rule looks like a quiet inbox. This weakens the discovery trust core ("provable completeness") for email. Slack keeps reconciliation.
- The system needs a public HTTPS ingress, an inbound mail service, and an outbound mail provider, and changes DNS on the company domain.

## Considered Options

- **Gmail API (push via Pub/Sub, send via `users.messages.send`)**: sees every sent reply, keeps Gmail Sent complete, and allows reconciliation with no public endpoint. Rejected in favour of matching Plain exactly.
- **Gmail API polling**: the previous design (PRD decision #47). Rejected with the move to event-driven ingestion.

## Consequences

- PRD revision 3 (decisions #51–#56) replaces the read-only Gmail mirror. ADR 0001's note that Gmail connects through its official API no longer holds for email.
- Choosing the ingress and mail services is decision D3 in the PRD, within the $10/month ceiling.
