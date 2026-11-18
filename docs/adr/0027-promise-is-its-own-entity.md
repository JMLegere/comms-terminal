# A Promise is its own entity; Cases, Issues and Customer requests are context

Status: accepted (2026-11-18), not yet built. Supersedes the Agreed next step term. Discovery: `.agents/discovery/2026-11-12-today-orient-fanout.md (2026-11-18 reopening)`.

Several existing things look like a Promise: a Case (a goal Jeremy drives), an Issue (work in Linear), a Linear Customer request (a customer's ask), a Sequence Task, and an Agreed next step (a date and owner both sides accepted).

Decision:

- A Promise is a separate entity: owed to one or more People, by a time, needing Jeremy's action, with its own states.
- A Case is Jeremy's goal; an Issue is work that may fulfil a Promise; a Customer request is an Origin only when Jeremy committed to it. A Promise holds any number of Promise links (Threads, Cases, Issues, Drafts, Tana nodes, outside addresses) as context. Links never change a Promise's state.
- Agreed next step is retired as a term. A next step Jeremy owns is a Promise, and its date starts the Warm Sequence's Enrollment. A next step the other side owns is the Thread's Waiting for them.
- Sequence Tasks stay Tasks: nobody is waiting on them.

## Considered Options

- **A Promise as a type of Case.** A Case's Status comes from its Threads; a Done Thread does not prove a Promise was kept, and many Cases are owed to nobody.
- **A Promise as an Issue.** Linear is a work tracker edited by people; many Promises have no Issue.
- **Every Customer request as a Promise.** Most are product feedback nobody was promised.

## Consequences

- Links and backlinks on every entity page are a separate discovery; Promise links are stored in a shape that a general link model can adopt.
- `enrollment start` takes its date from a Promise when Jeremy owns the next step.
