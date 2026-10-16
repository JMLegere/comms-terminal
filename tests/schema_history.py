"""Fake an older database from a fresh one: undo the migrations after `version`, newest first."""

from universal_inbox import inbox

UNDO = {
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
