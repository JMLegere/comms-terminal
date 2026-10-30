CREATE TABLE connections (
  id                INTEGER PRIMARY KEY,
  platform          TEXT NOT NULL CHECK (platform IN ('gmail', 'fastmail', 'slack', 'quo', 'gcal')),  -- platforms.py
  external_id       TEXT NOT NULL UNIQUE,  -- email address, Slack team ID, or Quo Work number
  self_id           TEXT NOT NULL,         -- Jeremy's address, Slack user ID, or Work number
  label             TEXT NOT NULL,
  connected_at      TEXT NOT NULL,
  auth_ok           INTEGER NOT NULL DEFAULT 1 CHECK (auth_ok IN (0, 1)),
  last_error        TEXT,
  probe_error       TEXT,                  -- the last health check's failure (decision #119)
  probed_at         TEXT,                  -- when the last health check ran
  inbound           TEXT                   -- email: the address its forward reaches, the Worker's X-Inbox-To
) STRICT;
CREATE UNIQUE INDEX connections_inbound ON connections (inbound);

CREATE TABLE channels (
  id            INTEGER PRIMARY KEY,
  connection_id INTEGER NOT NULL REFERENCES connections (id),
  native_id     TEXT NOT NULL,
  name          TEXT,
  kind          TEXT NOT NULL
    CHECK (kind IN ('mailbox', 'channel', 'private', 'im', 'mpim', 'meeting')),  -- meeting: one Event UID (ADR 0007)
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
  created_at   TEXT NOT NULL,
  unsubscribed_at TEXT  -- asked not to be contacted: no Sequence enrolls them again
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
CREATE TABLE memberships (  -- Jeremy's own Membership changes; email domain decides the rest (GLOSSARY)
  person_id       INTEGER NOT NULL REFERENCES persons (id),
  organization_id INTEGER NOT NULL REFERENCES organizations (id),
  member          INTEGER NOT NULL CHECK (member IN (0, 1)),  -- 1 added, 0 removed
  changed_at      TEXT NOT NULL,
  PRIMARY KEY (person_id, organization_id)
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

CREATE TABLE resend_receipts (
  email_id TEXT PRIMARY KEY,
  connection_id INTEGER NOT NULL REFERENCES connections (id),
  message_id TEXT NOT NULL
) STRICT;

-- Audit log: each change to a Thread's Status or Case and its cause (action = Jeremy's key; message/edit/
-- delete/call = the other side's Activity; reply = Jeremy's own message). old/new_detail are Status details;
-- a CASE entry's case_id is the Case joined, NULL = removed, and data.from the Case it left; data holds a timed snooze's until.
CREATE TABLE audit_log (
  id          INTEGER PRIMARY KEY,
  thread_id   INTEGER NOT NULL REFERENCES threads (id),
  occurred_at TEXT NOT NULL,
  kind        TEXT NOT NULL CHECK (kind IN ('STATUS', 'CASE')),
  cause       TEXT NOT NULL CHECK (cause IN ('action', 'reply', 'message', 'edit', 'delete', 'call',
                                             'woke', 'reopen', 'blocked', 'undo', 'meeting')),
  old_detail  TEXT,
  new_detail  TEXT,
  case_id     INTEGER REFERENCES cases (id),
  data        TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(data)),
  CHECK ((kind = 'STATUS') = (new_detail IS NOT NULL))
) STRICT;
CREATE INDEX audit_log_thread ON audit_log (thread_id, occurred_at);
CREATE TRIGGER audit_log_no_delete BEFORE DELETE ON audit_log
BEGIN
  SELECT RAISE(ABORT, 'the audit log is append-only');
END;
CREATE TRIGGER audit_log_no_update BEFORE UPDATE ON audit_log
BEGIN
  SELECT RAISE(ABORT, 'the audit log is append-only');
END;

-- Case log: changes to a Case itself, for its Activity feed; old is the description before the edit.
CREATE TABLE case_log (
  id          INTEGER PRIMARY KEY,
  case_id     INTEGER NOT NULL REFERENCES cases (id),
  occurred_at TEXT NOT NULL,
  kind        TEXT NOT NULL CHECK (kind IN ('DESCRIPTION')),
  old         TEXT NOT NULL
) STRICT;
CREATE INDEX case_log_case ON case_log (case_id, occurred_at);
CREATE TRIGGER case_log_no_delete BEFORE DELETE ON case_log
BEGIN
  SELECT RAISE(ABORT, 'the case log is append-only');
END;
CREATE TRIGGER case_log_no_update BEFORE UPDATE ON case_log
BEGIN
  SELECT RAISE(ABORT, 'the case log is append-only');
END;

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

CREATE TABLE composition_drafts (
  id INTEGER PRIMARY KEY,
  revision INTEGER NOT NULL CHECK (revision > 0),
  connection_id INTEGER REFERENCES connections (id),
  kind TEXT NOT NULL CHECK (kind IN ('email', 'sms', 'slack')),
  sender TEXT NOT NULL,
  "to" TEXT NOT NULL,
  cc TEXT NOT NULL,
  subject TEXT,
  body TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  sending_at TEXT,
  thread_id INTEGER REFERENCES threads (id),  -- a reply Draft: its Thread's reply box text (FR-SEQ-11)
  task_id INTEGER REFERENCES tasks (id),  -- an email Task's Draft (Sequences)
  send_at TEXT,  -- send later: when the service sends it
  send_error TEXT,  -- why a scheduled send did not go out
  needs_edit INTEGER NOT NULL DEFAULT 0 CHECK (needs_edit IN (0, 1)),  -- unsendable until its text changes
  CHECK ((kind = 'email' AND subject IS NOT NULL)
         OR (kind IN ('sms', 'slack') AND subject IS NULL AND cc = ''))
) STRICT;
CREATE UNIQUE INDEX composition_drafts_thread ON composition_drafts (thread_id) WHERE thread_id IS NOT NULL;
CREATE UNIQUE INDEX composition_drafts_task ON composition_drafts (task_id) WHERE task_id IS NOT NULL;

-- Attachments (.agents/prd/2026-10-15-attachments.md): files copied on arrival, content-addressed in files/
-- beside the database. Their own table, since activities.data never changes after ingest.
CREATE TABLE attachments (
  id INTEGER PRIMARY KEY,
  activity_id INTEGER NOT NULL REFERENCES activities (id),
  position INTEGER NOT NULL CHECK (position >= 0),
  name TEXT NOT NULL,
  type TEXT NOT NULL,
  size INTEGER NOT NULL CHECK (size >= 0),
  inline INTEGER NOT NULL DEFAULT 0 CHECK (inline IN (0, 1)),
  content_id TEXT,
  source TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(source)),
  sha TEXT,
  state TEXT NOT NULL CHECK (state IN ('pending', 'stored', 'failed', 'expired')),
  tries INTEGER NOT NULL DEFAULT 0,
  UNIQUE (activity_id, position),
  CHECK ((state = 'stored') = (sha IS NOT NULL))
) STRICT;
CREATE INDEX attachments_sha ON attachments (sha);
CREATE INDEX attachments_pending ON attachments (state) WHERE state = 'pending';

-- Files attached to an unsent Draft or to the open reply of one Thread (a reply Draft's files are its Thread's).
CREATE TABLE staged_attachments (
  id INTEGER PRIMARY KEY,
  draft_id INTEGER REFERENCES composition_drafts (id) ON DELETE CASCADE,
  thread_id INTEGER REFERENCES threads (id),
  sha TEXT NOT NULL,
  name TEXT NOT NULL,
  type TEXT NOT NULL,
  size INTEGER NOT NULL CHECK (size >= 0),
  added_at TEXT NOT NULL,
  CHECK ((draft_id IS NULL) <> (thread_id IS NULL))
) STRICT;

-- Sequences (.agents/prd/2026-10-16-sequencing.md P2-P4, ADR 0003): Jeremy's outreach, run one Person at a time.
CREATE TABLE sequences (
  id            INTEGER PRIMARY KEY,
  name          TEXT NOT NULL,
  connection_id INTEGER REFERENCES connections (id),  -- the email Connection its email Tasks send from
  archived_at   TEXT,
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL,
  description   TEXT NOT NULL DEFAULT ''  -- its "use when" line
) STRICT;

-- A Step is the template; position NULL marks a deleted Step, kept because Tasks name it. Positions run in outline
-- order through the whole Sequence; a Step's path is its section plus the If (parent_step_id) and branch it sits
-- under, and the next Step on a path is the next position on the same path (.agents/prd/2026-10-26-sequence-flow.md).
CREATE TABLE sequence_steps (
  id                 INTEGER PRIMARY KEY,
  sequence_id        INTEGER NOT NULL REFERENCES sequences (id),
  position           INTEGER CHECK (position > 0),
  section            TEXT NOT NULL DEFAULT 'body' CHECK (section IN ('start', 'body', 'completion')),
  parent_step_id     INTEGER REFERENCES sequence_steps (id),  -- the If whose path this Step is on
  branch             INTEGER CHECK (branch IN (0, 1)),        -- 1 its true path, 0 its false path
  kind               TEXT NOT NULL CHECK (kind IN ('email', 'call', 'linkedin', 'letter', 'action', 'if')),
  gap_days           INTEGER NOT NULL DEFAULT 0 CHECK (gap_days >= 0),
  at_time            TEXT CHECK (at_time GLOB '[0-2][0-9]:[0-5][0-9]'),
  subject            TEXT NOT NULL DEFAULT '',  -- an email's subject; an If's question when Jeremy picks
  body               TEXT NOT NULL DEFAULT '',
  voicemail          TEXT NOT NULL DEFAULT '',
  linkedin_move      TEXT CHECK (linkedin_move IN ('connect', 'message', 'engage')),
  reply_to_step_id   INTEGER REFERENCES sequence_steps (id),
  enroll_sequence_id INTEGER REFERENCES sequences (id),  -- an enroll Action: the Sequence it enrolls them in
  action_kind        TEXT CHECK (action_kind IN ('set_stage', 'enroll', 'end_for_others', 'set_value')),
  pipeline           TEXT CHECK (pipeline IN ('REVENUE', 'FUNDRAISING', 'TALENT')),
  stage              TEXT,
  value              INTEGER,
  condition          TEXT CHECK (condition IN ('end_reason', 'has_channel', 'stage', 'jeremy_picks')),
  operand            TEXT NOT NULL DEFAULT '',  -- end reasons (comma-separated) or a channel (email, phone, linkedin)
  true_label         TEXT NOT NULL DEFAULT '',
  false_label        TEXT NOT NULL DEFAULT '',
  needs_edit         INTEGER NOT NULL DEFAULT 1 CHECK (needs_edit IN (0, 1)),  -- its Drafts start as Needs edit
  UNIQUE (sequence_id, position),
  CHECK ((parent_step_id IS NULL) = (branch IS NULL)),
  CHECK ((kind = 'action') = (action_kind IS NOT NULL)),
  CHECK ((kind = 'if') = (condition IS NOT NULL))
) STRICT;

CREATE TABLE enrollments (
  id              INTEGER PRIMARY KEY,
  person_id       INTEGER NOT NULL REFERENCES persons (id),
  sequence_id     INTEGER NOT NULL REFERENCES sequences (id),
  state           TEXT NOT NULL CHECK (state IN ('ACTIVE', 'PAUSED', 'FINISHING', 'FINISHED')),
  reason          TEXT CHECK (reason IN ('COMPLETED', 'GOAL_MET', 'NOT_RIGHT_NOW', 'LOST', 'UNSUBSCRIBED', 'CANCELED')),
  paused_at       TEXT,
  pause_reason    TEXT,
  enrolled_at     TEXT NOT NULL,
  finished_at     TEXT,
  starts_at       TEXT,  -- a later Start date (FR-SEQ-25): the first Task counts from it; NULL is the enroll day
  responded_at    TEXT,  -- first reply or connected call; kept through Resume, so enroll Actions can tell
  organization_id INTEGER REFERENCES organizations (id),  -- the Organization it is for (FR-SEQ-36)
  identity_id     INTEGER REFERENCES identities (id),  -- the email address it sends to; NULL: the default
  CHECK (state = 'PAUSED' OR (state IN ('FINISHING', 'FINISHED')) = (reason IS NOT NULL))  -- Paused keeps a Finishing reason
) STRICT;
CREATE UNIQUE INDEX enrollments_live ON enrollments (person_id, sequence_id) WHERE state != 'FINISHED';

-- One Step for one Enrollment; created when the previous Task is done, due at due_at.
CREATE TABLE tasks (
  id            INTEGER PRIMARY KEY,
  enrollment_id INTEGER NOT NULL REFERENCES enrollments (id),
  step_id       INTEGER NOT NULL REFERENCES sequence_steps (id),
  due_at        TEXT NOT NULL,
  state         TEXT NOT NULL CHECK (state IN ('DUE', 'DONE', 'SKIPPED')),
  done_at       TEXT,
  thread_id     INTEGER REFERENCES threads (id),  -- the Thread its email went out in, for later reply Steps
  flag          TEXT,  -- why it waits for Jeremy: a reply target skipped, or a reply Draft already there
  choice        INTEGER CHECK (choice IN (0, 1)),  -- Jeremy's pick on an If: 1 its true path, 0 its false path
  UNIQUE (enrollment_id, step_id),
  CHECK ((state = 'DUE') = (done_at IS NULL))
) STRICT;
CREATE INDEX tasks_due ON tasks (state, due_at);

-- Calendar (ADR 0005): Events read from the Event provider, kept forever; calendar.py.
CREATE TABLE events (
  uid           TEXT NOT NULL,
  recurrence_id TEXT NOT NULL DEFAULT '',  -- '' = the Event or series itself; else one occurrence (UTC or date)
  ics           TEXT NOT NULL,             -- the VEVENT as Google gives it, less DTSTAMP
  first_seen_at TEXT NOT NULL,
  changed_at    TEXT NOT NULL,
  deleted_at    TEXT,                      -- a Deleted Event: gone from the Event provider, kept
  PRIMARY KEY (uid, recurrence_id)
) STRICT;
CREATE TABLE event_provider (
  id        INTEGER PRIMARY KEY CHECK (id = 1),
  owner     TEXT NOT NULL,                 -- the calendar's address (X-WR-CALNAME)
  ok_at     TEXT,                          -- last good fetch, written when fetches start failing
  failures  INTEGER NOT NULL DEFAULT 0,    -- failed fetches in a row
  error     TEXT
) STRICT;

-- Pipeline (ADR 0006): Stage and Value per Organization, append-only
CREATE TABLE pipeline_log (
  id              INTEGER PRIMARY KEY,
  organization_id INTEGER NOT NULL REFERENCES organizations (id),
  pipeline        TEXT NOT NULL CHECK (pipeline IN ('REVENUE', 'FUNDRAISING', 'TALENT')),
  kind            TEXT NOT NULL CHECK (kind IN ('STAGE', 'VALUE', 'UNDO')),
  stage           TEXT,
  value           INTEGER,
  undoes          INTEGER REFERENCES pipeline_log (id),
  effective_at    TEXT NOT NULL,             -- when it happened (backdated when seeding)
  recorded_at     TEXT NOT NULL,
  CHECK ((kind = 'STAGE') = (stage IS NOT NULL) AND (kind = 'VALUE') = (value IS NOT NULL)
         AND (kind = 'UNDO') = (undoes IS NOT NULL))
) STRICT;
CREATE INDEX pipeline_log_org ON pipeline_log (organization_id, pipeline, effective_at);
CREATE TRIGGER pipeline_log_no_delete BEFORE DELETE ON pipeline_log
BEGIN
  SELECT RAISE(ABORT, 'the pipeline log is append-only');
END;
CREATE TRIGGER pipeline_log_no_update BEFORE UPDATE ON pipeline_log
BEGIN
  SELECT RAISE(ABORT, 'the pipeline log is append-only');
END;
CREATE TABLE pipeline_targets (
  pipeline TEXT PRIMARY KEY CHECK (pipeline IN ('REVENUE', 'FUNDRAISING')),
  amount   INTEGER NOT NULL CHECK (amount > 0),
  due      TEXT NOT NULL                     -- YYYY-MM-DD
) STRICT;

CREATE TABLE changes (  -- every committed row change, for the laptop's Change sets (ADR 0012); kept 7 days
  seq    INTEGER PRIMARY KEY AUTOINCREMENT,
  tbl    TEXT NOT NULL,
  row_id INTEGER NOT NULL,  -- the row's rowid until rows carry UUIDs (ADR 0012)
  op     TEXT NOT NULL CHECK (op IN ('I', 'U', 'D')),
  at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now') || '000+00:00')
) STRICT;
CREATE INDEX changes_at ON changes (at);
-- Its triggers on every other table come from inbox.change_triggers(), so a new table cannot miss them.
