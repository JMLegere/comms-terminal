"""Fake an older database from a fresh one: undo the migrations after `version`, newest first."""

from comms_terminal import inbox

UNDO = {
    26: """CREATE TABLE planning_priors (
  pipeline TEXT NOT NULL CHECK (pipeline IN ('REVENUE', 'FUNDRAISING')),
  stage    TEXT NOT NULL,
  rate     REAL NOT NULL CHECK (rate > 0 AND rate <= 1),
  PRIMARY KEY (pipeline, stage)
) STRICT;""",
    25: "DROP TABLE planning_priors; DROP TABLE pipeline_targets; DROP TABLE pipeline_log;",
    24: "DROP TABLE events; DROP TABLE event_provider;",
    23: "DROP INDEX connections_inbound; ALTER TABLE connections DROP COLUMN inbound;",
    22: "",  # renames a Step kind; the rebuilt table keeps its columns
    21: "ALTER TABLE enrollments DROP COLUMN responded_at; ALTER TABLE sequence_steps DROP COLUMN enroll_sequence_id;",
    20: "ALTER TABLE enrollments DROP COLUMN starts_at;",
    19: "ALTER TABLE composition_drafts DROP COLUMN send_at; ALTER TABLE composition_drafts DROP COLUMN send_error;",
    18: "",  # moves reply-Draft files to their Thread; no DDL to undo
    17: "DROP INDEX composition_drafts_task; ALTER TABLE composition_drafts DROP COLUMN task_id;"
        " ALTER TABLE persons DROP COLUMN unsubscribed_at;"
        " DROP TABLE tasks; DROP TABLE enrollments; DROP TABLE sequence_steps; DROP TABLE sequences;",
    16: "DROP TABLE case_log;",
    15: "DROP TABLE audit_log;",
    14: "DROP INDEX composition_drafts_thread; ALTER TABLE composition_drafts DROP COLUMN thread_id;",
    13: "DROP TABLE resend_receipts;",
}


def downgrade(db, version: int, extra: str = "") -> None:
    db.executescript("".join(UNDO[v] for v in range(inbox.SCHEMA_VERSION, version, -1))
                     + extra + f" PRAGMA user_version = {version};")
