# Promises are written only by an agent, and every change cites an Origin

Status: accepted (2026-11-18), not yet built. Discovery: `.agents/discovery/2026-11-12-today-orient-fanout.md (2026-11-18 reopening)`.

Jeremy wants to stay ahead of everything people expect of him, and says his working context resets every few hours. A Promise is something a Person expects him to do by a time. If Promises needed his input to exist or stay current, the list would decay exactly when his context resets.

Decision:

- Only an agent creates or changes a Promise, through `comms-terminal` commands. The console and the App show Promises read-only and have no Promise controls.
- Every Promise and every change cites an Origin: a message, Phone call, Call transcript, Meeting note, Customer request, or Jeremy's own words recorded when he tells an agent to correct something. A command without an Origin is refused.
- An uncertain Promise is Tentative; Jeremy never confirms or dismisses anything. He corrects by telling an agent.
- A Promise is Cancelled only once the Person knows; until then it stays owed.
- Comms Terminal stays free of LLM calls (NFR-8): the agent runs outside it as a skill, by hand or on an hourly Workbox timer, and resumes from a "captured up to" mark Comms Terminal keeps.

## Considered Options

- **Jeremy confirms each suggested Promise.** Catches false Promises early, but recreates the manual step that fails when his context resets.
- **Agent and Jeremy both edit.** Two writers mean edit conflicts and changes with no recorded reason.
- **Promises as Linear Issues.** Reuses planning, but Linear is edited by people, and agent writes there need approval each time.

## Consequences

- A wrong Promise stays until Jeremy says so; corrections per week is the measure to watch.
- A full history of why each Promise exists and changed is always available.
- The CLI parity test gains Promise writers that have no console counterpart on purpose.
