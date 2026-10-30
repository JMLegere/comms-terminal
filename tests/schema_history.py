"""Fake an older database from a fresh one: undo the migrations after `version`, newest first."""

import re
import sqlite3
from pathlib import Path

from comms_terminal import inbox


def _integer_keys_sql() -> str:
    """Undo migration 33: rebuild each table from schema 33 (as migration_33.sql froze it) with integer ids, numbering
    each table's rows in id order and following every foreign key through that numbering."""
    frozen = Path(inbox.__file__).with_name("migration_33.sql").read_text()
    v33 = frozen[frozen.index("DROP TABLE IF EXISTS changes;") + len("DROP TABLE IF EXISTS changes;"):
                 frozen.index('INSERT INTO "')]
    v32 = re.sub(r"TEXT( NOT NULL)? REFERENCES", r"INTEGER\1 REFERENCES",
                 v33.replace("TEXT PRIMARY KEY NOT NULL DEFAULT (new_id())", "INTEGER PRIMARY KEY")
                 .replace("row_id TEXT NOT NULL", "row_id INTEGER NOT NULL"))
    shape = sqlite3.connect(":memory:")
    shape.create_function("new_id", 0, inbox.new_id)
    shape.executescript(v33)
    tables = [r[0] for r in shape.execute("SELECT name FROM sqlite_schema WHERE type = 'table'"
                                           " AND name NOT LIKE 'sqlite_%' AND name != 'changes'")]
    keyed = {t for t in tables if any(c[1] == "id" and c[2] == "TEXT" for c in shape.execute(f'PRAGMA table_info("{t}")'))}
    out = ["PRAGMA foreign_keys = OFF; PRAGMA legacy_alter_table = ON"]
    out += [f'CREATE TEMP TABLE "map_{t}" AS SELECT id AS old, row_number() OVER (ORDER BY id) AS new FROM "{t}"'
            for t in sorted(keyed)]
    out += [f'DROP {k.upper()} IF EXISTS "{n}"' for k, n in shape.execute(
        "SELECT type, name FROM sqlite_schema WHERE type IN ('index', 'trigger', 'view') AND sql IS NOT NULL")]
    out += [f'DROP TRIGGER IF EXISTS "changes_{t}_{e}"' for t in tables for e in ("insert", "update", "delete")]
    out += [f'ALTER TABLE "{t}" RENAME TO "{t}__new"' for t in tables] + ["DROP TABLE changes", v32.strip().rstrip(";")]
    for t in tables:
        refs = {r[3]: r[2] for r in shape.execute(f'PRAGMA foreign_key_list("{t}")') if r[2] in keyed}
        cols = [c[1] for c in shape.execute(f'PRAGMA table_info("{t}")')]
        conv = [f'(SELECT new FROM "map_{refs[c]}" WHERE old = "{c}")' if c in refs else
                f'(SELECT new FROM "map_{t}" WHERE old = "{c}")' if c == "id" and t in keyed else f'"{c}"' for c in cols]
        out += [f'INSERT INTO "{t}" ({", ".join(chr(34) + c + chr(34) for c in cols)}) SELECT {", ".join(conv)}'
                f' FROM "{t}__new"', f'DROP TABLE "{t}__new"']
    out += [f'DROP TABLE "map_{t}"' for t in sorted(keyed)]
    return ";\n".join(out) + ";"


UNDO = {
    34: "DROP TABLE applied_mutations;",
    33: _integer_keys_sql(),
    32: "".join(f"DROP TRIGGER IF EXISTS changes_{t}_{e};" for t in re.findall(
        r"^CREATE TABLE (\w+)", Path(inbox.__file__).with_name("schema.sql").read_text(), re.M)
        for e in ("insert", "update", "delete")) + "DROP TABLE changes;",
    31: "ALTER TABLE enrollments DROP COLUMN identity_id;",
    30: "ALTER TABLE composition_drafts DROP COLUMN needs_edit; ALTER TABLE sequence_steps DROP COLUMN needs_edit;"
        " ALTER TABLE sequences DROP COLUMN description;",
    29: """PRAGMA foreign_keys = OFF; PRAGMA legacy_alter_table = ON;
CREATE TABLE sequence_steps_old (
  id               INTEGER PRIMARY KEY,
  sequence_id      INTEGER NOT NULL REFERENCES sequences (id),
  position         INTEGER CHECK (position > 0),
  kind             TEXT NOT NULL CHECK (kind IN ('email', 'call', 'linkedin', 'letter', 'enrollment')),
  gap_days         INTEGER NOT NULL DEFAULT 0 CHECK (gap_days >= 0),
  at_time          TEXT CHECK (at_time GLOB '[0-2][0-9]:[0-5][0-9]'),
  subject          TEXT NOT NULL DEFAULT '',
  body             TEXT NOT NULL DEFAULT '',
  voicemail        TEXT NOT NULL DEFAULT '',
  action           TEXT CHECK (action IN ('connect', 'message', 'engage')),
  reply_to_step_id INTEGER REFERENCES sequence_steps (id),
  enroll_sequence_id INTEGER REFERENCES sequences (id),
  UNIQUE (sequence_id, position)
) STRICT;
INSERT INTO sequence_steps_old SELECT id, sequence_id, position, CASE kind WHEN 'action' THEN 'enrollment' ELSE kind END,
  gap_days, at_time, subject, body, voicemail, linkedin_move, reply_to_step_id, enroll_sequence_id
FROM sequence_steps WHERE kind != 'if';
DROP TABLE sequence_steps; ALTER TABLE sequence_steps_old RENAME TO sequence_steps;
CREATE TABLE enrollments_old (
  id           INTEGER PRIMARY KEY,
  person_id    INTEGER NOT NULL REFERENCES persons (id),
  sequence_id  INTEGER NOT NULL REFERENCES sequences (id),
  state        TEXT NOT NULL CHECK (state IN ('ACTIVE', 'PAUSED', 'FINISHED')),
  reason       TEXT CHECK (reason IN ('COMPLETED', 'UNSUBSCRIBED', 'CANCELED')),
  paused_at    TEXT,
  pause_reason TEXT,
  enrolled_at  TEXT NOT NULL,
  finished_at  TEXT,
  starts_at    TEXT,
  responded_at TEXT,
  CHECK ((state = 'FINISHED') = (reason IS NOT NULL))
) STRICT;
INSERT INTO enrollments_old SELECT id, person_id, sequence_id, state, reason, paused_at, pause_reason, enrolled_at,
  finished_at, starts_at, responded_at FROM enrollments;
DROP TABLE enrollments; ALTER TABLE enrollments_old RENAME TO enrollments;
CREATE UNIQUE INDEX enrollments_live ON enrollments (person_id, sequence_id) WHERE state != 'FINISHED';
CREATE TABLE tasks_old (
  id            INTEGER PRIMARY KEY,
  enrollment_id INTEGER NOT NULL REFERENCES enrollments (id),
  step_id       INTEGER NOT NULL REFERENCES sequence_steps (id),
  due_at        TEXT NOT NULL,
  state         TEXT NOT NULL CHECK (state IN ('DUE', 'DONE', 'SKIPPED')),
  done_at       TEXT,
  thread_id     INTEGER REFERENCES threads (id),
  flag          TEXT,
  UNIQUE (enrollment_id, step_id),
  CHECK ((state = 'DUE') = (done_at IS NULL))
) STRICT;
INSERT INTO tasks_old SELECT id, enrollment_id, step_id, due_at, state, done_at, thread_id, flag FROM tasks;
DROP TABLE tasks; ALTER TABLE tasks_old RENAME TO tasks;
CREATE INDEX tasks_due ON tasks (state, due_at);
CREATE TABLE pipeline_log_old (
  id              INTEGER PRIMARY KEY,
  organization_id INTEGER NOT NULL REFERENCES organizations (id),
  pipeline        TEXT NOT NULL CHECK (pipeline IN ('REVENUE', 'FUNDRAISING')),
  kind            TEXT NOT NULL CHECK (kind IN ('STAGE', 'VALUE', 'UNDO')),
  stage           TEXT,
  value           INTEGER,
  undoes          INTEGER REFERENCES pipeline_log (id),
  effective_at    TEXT NOT NULL,
  recorded_at     TEXT NOT NULL,
  CHECK ((kind = 'STAGE') = (stage IS NOT NULL) AND (kind = 'VALUE') = (value IS NOT NULL)
         AND (kind = 'UNDO') = (undoes IS NOT NULL))
) STRICT;
INSERT INTO pipeline_log_old SELECT * FROM pipeline_log;
DROP TABLE pipeline_log; ALTER TABLE pipeline_log_old RENAME TO pipeline_log;
CREATE INDEX pipeline_log_org ON pipeline_log (organization_id, pipeline, effective_at);
CREATE TRIGGER pipeline_log_no_delete BEFORE DELETE ON pipeline_log
BEGIN
  SELECT RAISE(ABORT, 'the pipeline log is append-only');
END;
CREATE TRIGGER pipeline_log_no_update BEFORE UPDATE ON pipeline_log
BEGIN
  SELECT RAISE(ABORT, 'the pipeline log is append-only');
END;
PRAGMA legacy_alter_table = OFF; PRAGMA foreign_keys = ON;""",  # rebuilds the four tables to their schema-28 shape
    28: "",  # widens three CHECKs (gcal, meeting); the rebuilt tables keep their columns
    27: "DROP TABLE memberships;",
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


LEGACY = re.compile(r"00000000-0000-7000-8000-([0-9a-f]{12})")


def legacy(n: int) -> str:
    """Integer id n as migration 33 leaves it."""
    return inbox.LEGACY_ID % n


def renumbered(db, table: str, *ids: str) -> list[str]:
    """The ids these rows of `table` get from downgrade() past 33 and back: the legacy id of their rank in id order.
    Read them before the downgrade."""
    order = [r[0] for r in db.execute(f'SELECT id FROM "{table}" ORDER BY id')]
    return [legacy(order.index(i) + 1) for i in ids]


def unmigrated(value):
    """`value` read after migration 33 with each legacy id back as its integer, to compare with what was read before."""
    if isinstance(value, str) and (m := LEGACY.fullmatch(value)):
        return int(m[1], 16)
    if isinstance(value, (list, tuple, set, frozenset)):
        return type(value)(unmigrated(v) for v in value)
    if isinstance(value, dict):
        return {unmigrated(k): unmigrated(v) for k, v in value.items()}
    return value


def changed_since(version: int) -> tuple[str, ...]:
    """The tables a migration after `version` alters, rebuilds, or drops: their stored SQL may change across the
    upgrade; every other table's must not."""
    found = {t.removesuffix("_new") for n, sql in inbox.MIGRATIONS.items() if n > version
             for t in re.findall(r'(?:ALTER|DROP) TABLE (?:IF EXISTS )?"?(\w+)', sql, re.I)}
    return tuple(sorted(found))
