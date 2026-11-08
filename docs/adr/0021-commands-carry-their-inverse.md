# Every change is a Command that carries its own Inverse, kept on the Service

Status: accepted (2026-11-08). Discovery: `.agents/discovery/2026-11-08-service-undo.md`. PRD: `.agents/prd/2026-11-08-service-undo.md`. Builds on ADR 0012, 0017 and 0020.

Undo lived in each Client: the Console kept a closure per action and the App rebuilt the same undo as lists of writer calls, so the two drifted, some changes had no undo, and nothing undid a change made in the other Client or by an agent. Jeremy wants undo on the Service, across every Client and the CLI, one level deep, with a kept history of every change beside the Action Queue.

Decision: each call to a writer that is not inside another writer call is a **Command**, recorded in the **Command log** in the same transaction as its changes. The writer declares, while it runs, its words (label), the records it changed (target), and its **Inverse**: the writer calls that put it back. A writer whose change cannot be put back cleanly every time declares the reason instead. **Undo** is itself a writer: given the Command a Client shows, it refuses unless that is the newest Command from the Console, the App or the CLI, unless its target records are as the Command left them, and unless its Module is on; then it runs the Inverse through `inbox.admit` and records itself as a Command pointing at the one it undid.

- **Sources.** Console, App, CLI and Service. The Service's own work is one Command per pass or webhook event, only when it changed something, and is never undone.
- **Same on both copies.** A laptop write mints its Command's id with its other new rows (ADR 0012), so the laptop's Command is the Service's. Undo is an ordinary write: the laptop applies it at once and the Service may refuse it.
- **Append-only ledgers stay so.** An Inverse adds a compensating row (an Audit entry with cause undo, a `pipeline_log` UNDO row) and never deletes one. The Command log is append-only too.
- **Platform effects.** An Inverse of a writer that queued an Action cancels it while it waits or queues a reverse Action (ADR 0020). Send now waits the Undo window through the send-later time, so Undo is clearing that time.
- **Labels are data.** The Service writes a Command's words once, as it writes an Audit entry's, and both Clients show them. Each Client still owns its layout (ADR 0014).

## Considered Options

- **Undo in each Client (as before).** Rejected: no undo across Clients or for agents, and two copies of every undo.
- **Save each row's old values from the change triggers and copy them back.** Rejected: the ledgers forbid changing their rows, so they need special cases (a second mechanism); the laptop and the Service would save different values; copying rows back ignores meaning.
- **Every write is an Action on the Action Queue.** Rejected: local writes have nothing to carry out; each key press would add Action rows and syncs; Action means something carried out on a Platform.
- **Writers return their Inverse.** Rejected for declaring it while they run: returning it changes 56 writers' results and every caller.
- **The database's changeset inversion.** Rejected: not in Python's standard library, and the ledger and laptop problems above.

## Consequences

- Every writer declares an Inverse or a reason; `tests/test_undo_roundtrip.py` runs each writer and its Undo and fails for a writer it does not know.
- The Console and the App build no undo; z calls Undo with the newest Command. The CLI gains `undo` and `history`.
- Restore writers that only undo (`inbox.restore`, `restore_draft`, `sequences.undelete_sequence`, `restore_step`, `undo_task`, `restore_finish`, `pipeline.undo`, `issues.undo`) are no longer Client calls.
- The Command log grows forever: about 40,000 Client Commands a year at today's volume, plus Service passes that change something.
- A refused send now shows as the Draft's send error after the Undo window, rather than at once.
