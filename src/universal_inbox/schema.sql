CREATE TABLE connections (
  id                INTEGER PRIMARY KEY,
  platform          TEXT NOT NULL CHECK (platform IN ('gmail', 'fastmail', 'slack', 'quo')),  -- platforms.py
  external_id       TEXT NOT NULL UNIQUE,  -- email address, Slack team ID, or Quo Work number
  self_id           TEXT NOT NULL,         -- Jeremy's address, Slack user ID, or Work number
  label             TEXT NOT NULL,
  connected_at      TEXT NOT NULL,
  auth_ok           INTEGER NOT NULL DEFAULT 1 CHECK (auth_ok IN (0, 1)),
  last_error        TEXT
) STRICT;

CREATE TABLE channels (
  id            INTEGER PRIMARY KEY,
  connection_id INTEGER NOT NULL REFERENCES connections (id),
  native_id     TEXT NOT NULL,
  name          TEXT,
  kind          TEXT NOT NULL
    CHECK (kind IN ('mailbox', 'channel', 'private', 'im', 'mpim')),
  UNIQUE (connection_id, native_id)
) STRICT;

CREATE TABLE channel_rules (
  id            INTEGER PRIMARY KEY,
  connection_id INTEGER NOT NULL REFERENCES connections (id),
  kind          TEXT NOT NULL CHECK (kind IN ('channel', 'prefix', 'suffix')),
  value         TEXT NOT NULL,
  UNIQUE (connection_id, kind, value)
) STRICT;

CREATE TABLE cases (
  id          INTEGER PRIMARY KEY,
  title       TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',  -- free text shown above the Case's activity (decision #98)
  created_at  TEXT NOT NULL
) STRICT;

CREATE TABLE threads (
  id                INTEGER PRIMARY KEY,
  channel_id        INTEGER NOT NULL REFERENCES channels (id),
  native_thread_id  TEXT NOT NULL,   -- first message's native_id
  title             TEXT NOT NULL,
  status            TEXT NOT NULL,
  status_detail     TEXT NOT NULL,
  status_changed_at TEXT NOT NULL,
  snoozed_until     TEXT,
  detail_before_snooze TEXT
    CHECK (detail_before_snooze IN ('CREATED', 'NEW_REPLY', 'IN_PROGRESS')),
  case_id           INTEGER REFERENCES cases (id),
  created_at        TEXT NOT NULL,
  follow_up_at      TEXT,            -- Waiting for them: back to Todo at this time with no reply (#116)
  merged_into       INTEGER REFERENCES threads (id) CHECK (merged_into < id), -- stable alias, NULL = active
  UNIQUE (channel_id, native_thread_id),
  CHECK (
    (status = 'TODO'
      AND status_detail IN ('CREATED', 'NEW_REPLY', 'IN_PROGRESS'))
    OR (status = 'SNOOZED'
      AND status_detail IN ('WAITING_FOR_CUSTOMER',
                            'WAITING_FOR_DURATION',
                            'WAITING_INDEFINITELY'))
    OR (status = 'DONE'
      AND status_detail IN ('DONE_MANUALLY_SET', 'IGNORED'))
  ),
  CHECK ((status_detail = 'WAITING_FOR_DURATION') = (snoozed_until IS NOT NULL))
) STRICT;
CREATE INDEX threads_queue ON threads (status, status_changed_at);
CREATE INDEX threads_case ON threads (case_id);

CREATE TRIGGER threads_alias_insert BEFORE INSERT ON threads
WHEN NEW.merged_into IS NOT NULL AND NOT EXISTS (
  SELECT 1 FROM threads t JOIN channels ch ON ch.id = t.channel_id
  WHERE t.id = NEW.merged_into AND t.merged_into IS NULL
    AND t.channel_id = NEW.channel_id AND ch.kind = 'mailbox'
)
BEGIN
  SELECT RAISE(ABORT, 'thread aliases require an active older thread in the same mailbox');
END;

CREATE TRIGGER threads_alias_update BEFORE UPDATE OF merged_into, channel_id ON threads
WHEN (NEW.merged_into IS NOT NULL AND NOT EXISTS (
  SELECT 1 FROM threads t JOIN channels ch ON ch.id = t.channel_id
  WHERE t.id = NEW.merged_into AND t.merged_into IS NULL
    AND t.channel_id = NEW.channel_id AND ch.kind = 'mailbox'
)) OR (EXISTS (SELECT 1 FROM threads t WHERE t.merged_into = OLD.id)
       AND (NEW.merged_into IS NOT NULL OR NEW.channel_id IS NOT OLD.channel_id))
BEGIN
  SELECT RAISE(ABORT, 'thread aliases require an active older thread in the same mailbox');
END;

CREATE TABLE persons (
  id           INTEGER PRIMARY KEY,
  display_name TEXT NOT NULL,
  created_at   TEXT NOT NULL
) STRICT;

CREATE TABLE identities (
  id           INTEGER PRIMARY KEY,
  person_id    INTEGER REFERENCES persons (id),  -- NULL = detached
  kind         TEXT NOT NULL CHECK (kind IN ('email', 'slack_user', 'phone')),
  value        TEXT NOT NULL,
  blocked      INTEGER NOT NULL DEFAULT 0 CHECK (blocked IN (0, 1)),
  UNIQUE (kind, value)
) STRICT;
CREATE INDEX identities_person ON identities (person_id);

CREATE TABLE organizations (
  id         INTEGER PRIMARY KEY,
  domain     TEXT NOT NULL UNIQUE,
  name       TEXT NOT NULL,
  created_at TEXT NOT NULL
) STRICT;

CREATE TABLE activities (
  id          INTEGER PRIMARY KEY,
  thread_id   INTEGER NOT NULL REFERENCES threads (id),
  kind        TEXT NOT NULL CHECK (kind IN ('MESSAGE', 'EDIT', 'DELETE')),
  occurred_at TEXT NOT NULL,
  identity_id INTEGER REFERENCES identities (id),  -- NULL = Jeremy
  native_id   TEXT NOT NULL,
  target_id   INTEGER REFERENCES activities (id),
  content     TEXT,
  data        TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(data)),
  UNIQUE (kind, native_id),
  CHECK ((kind = 'MESSAGE') = (target_id IS NULL))
) STRICT;
CREATE INDEX activities_thread ON activities (thread_id, occurred_at);
CREATE INDEX activities_identity ON activities (identity_id);

CREATE TABLE email_refs (
  activity_id INTEGER NOT NULL REFERENCES activities (id),
  native_id   TEXT NOT NULL,
  PRIMARY KEY (activity_id, native_id)
) STRICT;
CREATE INDEX email_refs_native ON email_refs (native_id);

CREATE VIEW thread_activities AS
SELECT a.id, COALESCE(t.merged_into, a.thread_id) AS thread_id,
       a.kind, a.occurred_at, a.identity_id, a.native_id, a.target_id, a.content, a.data
FROM activities a JOIN threads t ON t.id = a.thread_id;

CREATE TRIGGER activities_no_delete BEFORE DELETE ON activities
BEGIN
  SELECT RAISE(ABORT, 'activities are append-only');
END;

CREATE TRIGGER activities_no_update BEFORE UPDATE ON activities
WHEN NOT (
  NEW.content IS NULL
  AND NEW.id IS OLD.id AND NEW.thread_id IS OLD.thread_id
  AND NEW.kind IS OLD.kind AND NEW.occurred_at IS OLD.occurred_at
  AND NEW.identity_id IS OLD.identity_id
  AND NEW.native_id IS OLD.native_id AND NEW.target_id IS OLD.target_id
  AND NEW.data IS OLD.data
  AND EXISTS (
    SELECT 1 FROM activities d
    WHERE d.kind = 'DELETE'
      AND d.target_id = COALESCE(OLD.target_id, OLD.id)
  )
)
BEGIN
  SELECT RAISE(ABORT, 'activities are append-only');
END;
