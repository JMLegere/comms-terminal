# Sequences set Stage; Stage lags the work

Status: accepted (2026-10-26). Amends [ADR 0006](0006-console-owns-organization-stage.md).

In ADR 0006, Jeremy set every Stage change himself. Five days later, 4 of 373 Organizations had a Stage, Fundraising had none, and the Revenue Sequences built for each journey purpose had no Enrollments. Jeremy chose to work Sequence-first: every piece of proactive work runs in a Sequence, and Stage is a lagging roll-up of that work ("stages are just a lagging roll up of the sequences working. it should work without stages at all in comms terminal"). So an Action Step in a Sequence sets the Stage of the Organization the Enrollment is for. By convention, mainline Sequences set Stage in On start, and Lost or Churned is set in a path's On completion. Jeremy can still set a Stage by hand at any time, and the latest change stands: Actions set Stage but do not lock it. The append-only log, Value and the Pipeline math from ADR 0006 stay as they are.

## Considered Options

- **Stage drives Sequences** (entering a Stage enrolls, or proposes, that Stage's Sequence). Rejected: it puts a per-Organization Stage in charge of per-Person Enrollments, and makes hand-kept Stage data load-bearing when it was barely kept.
- **Stage derived from Sequence history** (the most advanced mainline Sequence started). Rejected for explicit Actions: "the pipeline numbers should be managed through actions".
- **Stage on the Person or on a new Relationship.** Rejected: the Organization keeps the Stage, an Enrollment names the Organization it is for, and an angel or candidate gets a Sole proprietorship.

## Consequences

- The Pipeline math is only as good as where each Sequence places its Stage Actions.
- The Organizations staged by hand under ADR 0006 (Chestnut, LCL, St Mary's, Iron Maple) are enrolled in fitting mainline Sequences, so Actions take over from there.
- Work done outside Sequences moves no Stage unless Jeremy sets it.
- Discovery: `.agents/discovery/2026-10-25-sequence-driven-pipeline.md`; design: `.agents/design/2026-10-26-sequence-flow.md` (both local, gitignored).
