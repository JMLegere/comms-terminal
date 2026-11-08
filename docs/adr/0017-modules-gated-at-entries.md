# Modules are stored per Service and gated where Client calls enter

Status: accepted (2026-11-07). Discovery: `.agents/discovery/2026-11-07-module-toggles.md`.

An Agent Maple teammate will run their own Service for inbox work only, so Pipelines, Sequences (with their Tasks and Enrollments) and Cases become Modules that a Service's user can turn off. Jeremy decided that an off Module is hidden in the Console and the App, that the CLI says it is disabled, and that its data is kept. A Module setting is a row in the Service's database, so the existing change triggers carry it to every laptop copy and both Clients show the same value. A missing row means off: a fresh Service starts inbox-only, and the migration that adds the table turns all three on for Jeremy's existing Service.

Writes are refused where a Client call enters the Service by writer name: CLI dispatch, the Service's Mutation apply (the laptop Console, the App through the Local core, and both undos), and the Console's local-mode actions. Each writer declares its Module with `@writer(module=…)`, and one `admit()` check refuses a call into an off Module with "<Module> is disabled". The Service's own code calls writers directly and is not gated, so a reply from an enrolled Person still pauses their Enrollment while Sequences is off. The one exception is the Sequence engine (`sequences.materialize`), which does nothing while Sequences is off, so no Tasks or Drafts appear for a hidden Module; when Sequences is turned back on, Steps that fell due meanwhile come due as overdue.

Hiding works by tags where each surface is defined (pages, rail rows, view commands, key bindings, CLI subcommands) and by the shared reads (`inbox.today`, `inbox.queue`, calendar reads, palette search) dropping off-Module parts, so both Clients' existing empty states render the result.

## Considered Options

- **Wrap every writer so an off Module is frozen.** Rejected: the Service would also miss pauses on inbound replies, and could later email someone who had already answered. No surveyed product freezes a disabled module.
- **Gate only in the Clients (read shaping, no write check).** Rejected: the CLI must refuse, and a stale undo or queued Mutation could still write.
- **Checks at each surface with no tags.** Rejected: about thirty surfaces per Client; GitLab's left-nav drift (epic 3719) shows such checks fall behind. Tags give the parity tests one list to enumerate.
- **Per-Client or deployment configuration.** Rejected: values drift between machines, or need a redeploy to change.

## Consequences

- A Mutation made on the laptop before the setting arrived is refused; the laptop's rebase drops it and says so.
- An undo into an off Module is refused and kept, so it works again once the Module is back on.
- App Settings › Modules writes the Service's database, an exception to the App Settings rule that Settings writes only `settings.json`.
- Every new page, command or key that belongs to a Module must declare it; the parity tests fail otherwise.

## Addendum (2026-11-08): Issues is a Module

Issues (ADR 0019) is a fourth Module. While it is off the Issues page, its rail row, Today's issues pane, planned Issues on the Calendar, the Issue keys and the `issues`, `issue` and `urgency` commands are hidden or refused; the Action Queue and every Connection, Linear's Issue connection among them, stay Core. The Service keeps its copy current (the full read and the Linear webhook go on), but Rollover does nothing while Issues is off, as the Sequence engine does for Sequences, so no Slip status reaches Linear for a hidden Module. Migration 43 widens the `modules` table to the new id and turns Issues on for an existing Service; a new Service starts with it off.
