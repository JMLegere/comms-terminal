# Universal Inbox owns outreach Sequences

Status: accepted (2026-10-16)

Jeremy's cold outreach follows the Secret Sequence: 15 working days of manual emails, calls, LinkedIn touches, and a letter. It stopped when the Lemlist seat was dropped in June 2026. Universal Inbox now owns Sequences, Enrollments, and Steps; Attio stays a read-only Context source that supplies the People Jeremy enrolls. The deciding facts, recorded in `.agents/discovery/2026-10-15-attio-sequences.md`:

- No new subscription spend, and the Attio workspace is on Free or Plus.
- Jeremy works one queue: Due Steps sit beside Threads and Drafts.
- Every Due Step must be guaranteed by stored state, with none derived after the fact.
- Inbox already sees every inbound message and Quo call by Identity, which is what pauses an Enrollment.

## Considered Options

- **Attio Workflows** (Delay until, then an HTTP request to create Inbox Drafts, with Attio Tasks for the other Steps). Rejected: about 1,600 credits a month against 1,500 on Plus. It also splits the queue, needs Inbox to write to Attio, and cannot represent a two-dial call block.
- **Attio Sequences**. Rejected: they auto-send email only, need the Pro plan, and Attio's terms exclude cold outreach.
- **Rejoin Lemlist or Apollo**. Rejected on cost; it is what stopped outbound.
- **A daily agent that derives Due Steps from history**. Rejected: no stored Step state, so misses are silent. Agents still edit Step Drafts through the agent commands.

## Consequences

- Inbox grows from tracking messages into running outreach, which puts pressure on NFR-8 simplicity.
- Attio holds no Step history.
- LinkedIn replies are invisible to Inbox; Jeremy pauses or finishes those Enrollments by hand.
- Step email still needs a send path whose terms allow cold mail. Resend's terms restrict it (ADR 0002). Until a Connection is marked as allowed for cold mail, sending Step Drafts stays off.
