# The Sequence module owns complete replacement

Status: accepted (2026-10-28).

`sequences.replace_sequence` owns reconciliation, reply-link resolution, validation and atomic persistence of a complete Sequence. `sequence_md` remains the Markdown syntax adapter. The previous trial-copy validation followed by independently committed Step writes could leave a partial replacement after a later failure; validation now checks the exact changed rows before their transaction commits.

Reuse `inbox._write_snapshot` for the participating Sequence, Draft and Pipeline writes so live Task/Draft rematerialization rolls back with the replacement and an existing caller keeps commit ownership. A dry run executes the same domain operation on an isolated copy, including pending caller changes. Keep stable Step IDs, existing Due Task content, the stored-Task model (ADR 0003) and Sequence-driven Stage Actions (ADR 0008); add no schema or transaction framework.
