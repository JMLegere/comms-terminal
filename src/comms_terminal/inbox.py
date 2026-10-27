"""The inbox module: Threads, Status, Activities, Persons, Organizations, Blocks, Cases, and Drafts.

Interface: ingest, act, reply, wake, reopen_imported, reconcile_email_threads; people operations block, add_identity, remove_identity; save_draft, save_reply_draft, schedule_draft, schedule_reply, claim_due_drafts, reply_drafts, get_draft, delete_draft, restore_draft, list_drafts, recipient_names, recipient_threads.
Design: docs/design/api-and-database.md.
"""

import hashlib
import json
import os
import random
import re
from collections import Counter
import sqlite3
import urllib.parse
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from email import policy
from email.parser import BytesHeaderParser
from email.utils import getaddresses
from typing import Protocol, Sequence

from . import clock, files, platforms
from .writers import writer

SCHEMA_VERSION = 30
WAIT = timedelta(days=4)  # Waiting for them ends after this with no reply: back to Todo to follow up (#116)
EMAIL_CONFIRMATION_PENDING = "Email confirmation pending; wait for its sent receipt before replying."
MIGRATIONS = {  # version -> statement that upgrades an existing database to it
    2: "ALTER TABLE cases ADD COLUMN description TEXT NOT NULL DEFAULT ''",
    3: "ALTER TABLE threads ADD COLUMN follow_up_at TEXT;"
       " UPDATE threads SET follow_up_at = strftime('%Y-%m-%dT%H:%M:%f', status_changed_at, '+5 days') || '000+00:00'"
       " WHERE status_detail = 'WAITING_FOR_CUSTOMER';",
    4: """ALTER TABLE threads ADD COLUMN merged_into INTEGER REFERENCES threads (id) CHECK (merged_into < id);
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
CREATE TABLE email_refs (
  activity_id INTEGER NOT NULL REFERENCES activities (id),
  native_id TEXT NOT NULL,
  PRIMARY KEY (activity_id, native_id)
) STRICT;
CREATE INDEX email_refs_native ON email_refs (native_id);
CREATE VIEW thread_activities AS
SELECT a.id, COALESCE(t.merged_into, a.thread_id) AS thread_id,
       a.kind, a.occurred_at, a.identity_id, a.native_id, a.target_id, a.content, a.data
FROM activities a JOIN threads t ON t.id = a.thread_id;""",
    # SMS Connection (Quo): widen two CHECKs by SQLite's table rebuild; migrate() turns foreign keys off around it.
    # Nothing references the *_new names, so the renames need no schema-wide rewrite (legacy_alter_table).
    5: """PRAGMA legacy_alter_table = ON;
CREATE TABLE connections_new (
  id INTEGER PRIMARY KEY,
  platform TEXT NOT NULL CHECK (platform IN ('email', 'slack', 'sms')),
  external_id TEXT NOT NULL UNIQUE, self_id TEXT NOT NULL, label TEXT NOT NULL, connected_at TEXT NOT NULL,
  auth_ok INTEGER NOT NULL DEFAULT 1 CHECK (auth_ok IN (0, 1)), last_error TEXT
) STRICT;
INSERT INTO connections_new SELECT id, platform, external_id, self_id, label, connected_at, auth_ok, last_error
FROM connections;
DROP TABLE connections;
ALTER TABLE connections_new RENAME TO connections;
CREATE TABLE identities_new (
  id INTEGER PRIMARY KEY,
  person_id INTEGER REFERENCES persons (id),
  kind TEXT NOT NULL CHECK (kind IN ('email', 'slack_user', 'phone')),
  value TEXT NOT NULL,
  blocked INTEGER NOT NULL DEFAULT 0 CHECK (blocked IN (0, 1)),
  UNIQUE (kind, value)
) STRICT;
INSERT INTO identities_new SELECT id, person_id, kind, value, blocked FROM identities;
DROP TABLE identities;
ALTER TABLE identities_new RENAME TO identities;
CREATE INDEX identities_person ON identities (person_id);
PRAGMA legacy_alter_table = OFF;""",
    # Platforms are services (GLOSSARY): the email Platform splits into gmail and fastmail; sms is quo.
    6: """PRAGMA legacy_alter_table = ON;
CREATE TABLE connections_new (
  id INTEGER PRIMARY KEY,
  platform TEXT NOT NULL CHECK (platform IN ('gmail', 'fastmail', 'slack', 'quo')),
  external_id TEXT NOT NULL UNIQUE, self_id TEXT NOT NULL, label TEXT NOT NULL, connected_at TEXT NOT NULL,
  auth_ok INTEGER NOT NULL DEFAULT 1 CHECK (auth_ok IN (0, 1)), last_error TEXT
) STRICT;
INSERT INTO connections_new SELECT id,
  CASE WHEN platform = 'email' AND label = 'Fastmail' THEN 'fastmail' WHEN platform = 'email' THEN 'gmail'
       WHEN platform = 'sms' THEN 'quo' ELSE platform END,
  external_id, self_id, label, connected_at, auth_ok, last_error FROM connections;
DROP TABLE connections;
ALTER TABLE connections_new RENAME TO connections;
PRAGMA legacy_alter_table = OFF;""",
    # One Organization key (_org): subdomain rows fold into their registrable domain, keeping a name Jeremy set.
    7: """INSERT OR IGNORE INTO organizations (domain, name, created_at)
SELECT org_key(domain), CASE WHEN max(name != domain) THEN max(CASE WHEN name != domain THEN name END)
                             ELSE org_key(domain) END, min(created_at)
FROM organizations WHERE org_key(domain) != domain GROUP BY org_key(domain);
DELETE FROM organizations WHERE org_key(domain) != domain;""",
    # Health checks every 30 minutes (decision #119): their result apart from send and inbound errors.
    8: "ALTER TABLE connections ADD COLUMN probe_error TEXT; ALTER TABLE connections ADD COLUMN probed_at TEXT;",
    9: """CREATE TABLE composition_drafts (
  id INTEGER PRIMARY KEY,
  revision INTEGER NOT NULL CHECK (revision > 0),
  connection_id INTEGER REFERENCES connections (id),
  kind TEXT NOT NULL CHECK (kind IN ('email', 'sms')),
  sender TEXT NOT NULL,
  "to" TEXT NOT NULL,
  cc TEXT NOT NULL,
  subject TEXT,
  body TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  CHECK ((kind = 'email' AND subject IS NOT NULL)
         OR (kind = 'sms' AND subject IS NULL AND cc = ''))
) STRICT;""",
    10: "ALTER TABLE composition_drafts ADD COLUMN sending_at TEXT;",
    # Slack Drafts: a third kind, posted into one existing Slack conversation (no subject, no Cc).
    11: """PRAGMA legacy_alter_table = ON;
CREATE TABLE composition_drafts_new (
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
  CHECK ((kind = 'email' AND subject IS NOT NULL)
         OR (kind IN ('sms', 'slack') AND subject IS NULL AND cc = ''))
) STRICT;
INSERT INTO composition_drafts_new SELECT id, revision, connection_id, kind, sender, "to", cc, subject, body,
  created_at, updated_at, sending_at FROM composition_drafts;
DROP TABLE composition_drafts;
ALTER TABLE composition_drafts_new RENAME TO composition_drafts;
PRAGMA legacy_alter_table = OFF;""",
    # Attachments: received files copied on arrival, and files staged on a Draft or reply.
    12: """CREATE TABLE attachments (
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
) STRICT;""",
    13: """CREATE TABLE resend_receipts (
  email_id TEXT PRIMARY KEY,
  connection_id INTEGER NOT NULL REFERENCES connections (id),
  message_id TEXT NOT NULL
) STRICT;""",
    # Reply Drafts (.agents/prd/2026-10-16-sequencing.md P1): a reply box's text is a Draft of its Thread.
    14: """ALTER TABLE composition_drafts ADD COLUMN thread_id INTEGER REFERENCES threads (id);
CREATE UNIQUE INDEX composition_drafts_thread ON composition_drafts (thread_id) WHERE thread_id IS NOT NULL;""",
    15: """CREATE TABLE audit_log (
  id          INTEGER PRIMARY KEY,
  thread_id   INTEGER NOT NULL REFERENCES threads (id),
  occurred_at TEXT NOT NULL,
  kind        TEXT NOT NULL CHECK (kind IN ('STATUS', 'CASE')),
  cause       TEXT NOT NULL CHECK (cause IN ('action', 'reply', 'message', 'edit', 'delete', 'call',
                                             'woke', 'reopen', 'blocked', 'undo')),
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
END;""",
    16: """CREATE TABLE case_log (
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
END;""",
    # Sequences (same PRD, P2-P4): Sequences, Steps, Enrollments, Tasks; Task Drafts; Unsubscribed People.
    17: """ALTER TABLE persons ADD COLUMN unsubscribed_at TEXT;
ALTER TABLE composition_drafts ADD COLUMN task_id INTEGER REFERENCES tasks (id);
CREATE UNIQUE INDEX composition_drafts_task ON composition_drafts (task_id) WHERE task_id IS NOT NULL;
CREATE TABLE sequences (
  id            INTEGER PRIMARY KEY,
  name          TEXT NOT NULL,
  connection_id INTEGER REFERENCES connections (id),
  archived_at   TEXT,
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
) STRICT;

CREATE TABLE sequence_steps (
  id               INTEGER PRIMARY KEY,
  sequence_id      INTEGER NOT NULL REFERENCES sequences (id),
  position         INTEGER CHECK (position > 0),
  kind             TEXT NOT NULL CHECK (kind IN ('email', 'call', 'linkedin', 'letter')),
  gap_days         INTEGER NOT NULL DEFAULT 0 CHECK (gap_days >= 0),
  at_time          TEXT CHECK (at_time GLOB '[0-2][0-9]:[0-5][0-9]'),
  subject          TEXT NOT NULL DEFAULT '',
  body             TEXT NOT NULL DEFAULT '',
  voicemail        TEXT NOT NULL DEFAULT '',
  action           TEXT CHECK (action IN ('connect', 'message', 'engage')),
  reply_to_step_id INTEGER REFERENCES sequence_steps (id),
  UNIQUE (sequence_id, position)
) STRICT;

CREATE TABLE enrollments (
  id           INTEGER PRIMARY KEY,
  person_id    INTEGER NOT NULL REFERENCES persons (id),
  sequence_id  INTEGER NOT NULL REFERENCES sequences (id),
  state        TEXT NOT NULL CHECK (state IN ('ACTIVE', 'PAUSED', 'FINISHED')),
  reason       TEXT CHECK (reason IN ('COMPLETED', 'UNSUBSCRIBED', 'CANCELED')),
  paused_at    TEXT,
  pause_reason TEXT,
  enrolled_at  TEXT NOT NULL,
  finished_at  TEXT,
  CHECK ((state = 'FINISHED') = (reason IS NOT NULL))
) STRICT;
CREATE UNIQUE INDEX enrollments_live ON enrollments (person_id, sequence_id) WHERE state != 'FINISHED';

CREATE TABLE tasks (
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
CREATE INDEX tasks_due ON tasks (state, due_at);""",
    # A reply Draft's files are its Thread's reply files (stage); move any staged on the reply Draft itself.
    18: """UPDATE staged_attachments SET draft_id = NULL, thread_id = (
  SELECT d.thread_id FROM composition_drafts d WHERE d.id = staged_attachments.draft_id)
WHERE draft_id IN (SELECT id FROM composition_drafts WHERE thread_id IS NOT NULL);""",
    # Send later: a Draft's scheduled send time, and why a scheduled send did not go out.
    19: """ALTER TABLE composition_drafts ADD COLUMN send_at TEXT;
ALTER TABLE composition_drafts ADD COLUMN send_error TEXT;""",
    20: "ALTER TABLE enrollments ADD COLUMN starts_at TEXT;",
    # Enroll Steps chain Sequences: a fifth Step kind (a CHECK, so a table rebuild) and its target Sequence;
    # an Enrollment remembers that its Person responded, which survives Resume. Paused ones have responded.
    21: """PRAGMA legacy_alter_table = ON;
CREATE TABLE sequence_steps_new (
  id                 INTEGER PRIMARY KEY,
  sequence_id        INTEGER NOT NULL REFERENCES sequences (id),
  position           INTEGER CHECK (position > 0),
  kind               TEXT NOT NULL CHECK (kind IN ('email', 'call', 'linkedin', 'letter', 'sequence')),
  gap_days           INTEGER NOT NULL DEFAULT 0 CHECK (gap_days >= 0),
  at_time            TEXT CHECK (at_time GLOB '[0-2][0-9]:[0-5][0-9]'),
  subject            TEXT NOT NULL DEFAULT '',
  body               TEXT NOT NULL DEFAULT '',
  voicemail          TEXT NOT NULL DEFAULT '',
  action             TEXT CHECK (action IN ('connect', 'message', 'engage')),
  reply_to_step_id   INTEGER REFERENCES sequence_steps (id),
  enroll_sequence_id INTEGER REFERENCES sequences (id),
  UNIQUE (sequence_id, position)
) STRICT;
INSERT INTO sequence_steps_new (id, sequence_id, position, kind, gap_days, at_time, subject, body, voicemail, action,
                                reply_to_step_id)
SELECT id, sequence_id, position, kind, gap_days, at_time, subject, body, voicemail, action, reply_to_step_id
FROM sequence_steps;
DROP TABLE sequence_steps;
ALTER TABLE sequence_steps_new RENAME TO sequence_steps;
PRAGMA legacy_alter_table = OFF;
ALTER TABLE enrollments ADD COLUMN responded_at TEXT;
UPDATE enrollments SET responded_at = paused_at WHERE paused_at IS NOT NULL;""",
    # That Step kind is named for what it makes: 'sequence' becomes 'enrollment' (the CHECK again, so a rebuild).
    22: """PRAGMA legacy_alter_table = ON;
CREATE TABLE sequence_steps_new (
  id                 INTEGER PRIMARY KEY,
  sequence_id        INTEGER NOT NULL REFERENCES sequences (id),
  position           INTEGER CHECK (position > 0),
  kind               TEXT NOT NULL CHECK (kind IN ('email', 'call', 'linkedin', 'letter', 'enrollment')),
  gap_days           INTEGER NOT NULL DEFAULT 0 CHECK (gap_days >= 0),
  at_time            TEXT CHECK (at_time GLOB '[0-2][0-9]:[0-5][0-9]'),
  subject            TEXT NOT NULL DEFAULT '',
  body               TEXT NOT NULL DEFAULT '',
  voicemail          TEXT NOT NULL DEFAULT '',
  action             TEXT CHECK (action IN ('connect', 'message', 'engage')),
  reply_to_step_id   INTEGER REFERENCES sequence_steps (id),
  enroll_sequence_id INTEGER REFERENCES sequences (id),
  UNIQUE (sequence_id, position)
) STRICT;
INSERT INTO sequence_steps_new
SELECT id, sequence_id, position, CASE kind WHEN 'sequence' THEN 'enrollment' ELSE kind END, gap_days, at_time,
       subject, body, voicemail, action, reply_to_step_id, enroll_sequence_id
FROM sequence_steps;
DROP TABLE sequence_steps;
ALTER TABLE sequence_steps_new RENAME TO sequence_steps;
PRAGMA legacy_alter_table = OFF;""",
    # Any number of email Connections: each one's Inbound address (the Worker's X-Inbox-To) picks it. The
    # existing ones keep the addresses their forwards already reach (runbook §1 step 7, #113).
    23: """ALTER TABLE connections ADD COLUMN inbound TEXT;
CREATE UNIQUE INDEX connections_inbound ON connections (inbound);
UPDATE connections SET inbound = 'fastmail@inbox.agentmaple.com' WHERE platform = 'fastmail';
UPDATE connections SET inbound = lower(substr(self_id, 1, instr(self_id, '@'))) || 'inbox.agentmaple.com'
WHERE platform = 'gmail' AND instr(self_id, '@') > 1;""",
    # Calendar (ADR 0005): Events from the Event provider and its fetch health.
    24: """CREATE TABLE events (
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
) STRICT;""",
    25: """CREATE TABLE pipeline_log (
  id              INTEGER PRIMARY KEY,
  organization_id INTEGER NOT NULL REFERENCES organizations (id),
  pipeline        TEXT NOT NULL CHECK (pipeline IN ('REVENUE', 'FUNDRAISING')),
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
CREATE TABLE planning_priors (
  pipeline TEXT NOT NULL CHECK (pipeline IN ('REVENUE', 'FUNDRAISING')),
  stage    TEXT NOT NULL,
  rate     REAL NOT NULL CHECK (rate > 0 AND rate <= 1),
  PRIMARY KEY (pipeline, stage)
) STRICT;""",
    26: "DROP TABLE planning_priors;",  # Table 6.2 benchmarks replace Planning priors
    27: """CREATE TABLE memberships (  -- Jeremy's own Membership changes; email domain decides the rest (GLOSSARY)
  person_id       INTEGER NOT NULL REFERENCES persons (id),
  organization_id INTEGER NOT NULL REFERENCES organizations (id),
  member          INTEGER NOT NULL CHECK (member IN (0, 1)),  -- 1 added, 0 removed
  changed_at      TEXT NOT NULL,
  PRIMARY KEY (person_id, organization_id)
) STRICT;""",  # Membership (ADR 0007 discovery, FR-OA-1)
    # Meetings are Threads (ADR 0007): Google Calendar is a Platform, and each Event UID with an outside attendee
    # is a Channel of kind meeting, and a Meeting is a Status change's cause. All CHECKs, so a rebuild of each
    # table (as in 5 and 6).
    28: """PRAGMA legacy_alter_table = ON;
CREATE TABLE connections_new (
  id INTEGER PRIMARY KEY,
  platform TEXT NOT NULL CHECK (platform IN ('gmail', 'fastmail', 'slack', 'quo', 'gcal')),
  external_id TEXT NOT NULL UNIQUE, self_id TEXT NOT NULL, label TEXT NOT NULL, connected_at TEXT NOT NULL,
  auth_ok INTEGER NOT NULL DEFAULT 1 CHECK (auth_ok IN (0, 1)), last_error TEXT, probe_error TEXT, probed_at TEXT,
  inbound TEXT
) STRICT;
INSERT INTO connections_new SELECT id, platform, external_id, self_id, label, connected_at, auth_ok, last_error,
  probe_error, probed_at, inbound FROM connections;
DROP TABLE connections;
ALTER TABLE connections_new RENAME TO connections;
CREATE UNIQUE INDEX connections_inbound ON connections (inbound);
CREATE TABLE channels_new (
  id INTEGER PRIMARY KEY,
  connection_id INTEGER NOT NULL REFERENCES connections (id),
  native_id TEXT NOT NULL,
  name TEXT,
  kind TEXT NOT NULL CHECK (kind IN ('mailbox', 'channel', 'private', 'im', 'mpim', 'meeting')),
  UNIQUE (connection_id, native_id)
) STRICT;
INSERT INTO channels_new SELECT id, connection_id, native_id, name, kind FROM channels;
DROP TABLE channels;
ALTER TABLE channels_new RENAME TO channels;
CREATE TABLE audit_log_new (
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
INSERT INTO audit_log_new SELECT id, thread_id, occurred_at, kind, cause, old_detail, new_detail, case_id, data
FROM audit_log;
DROP TABLE audit_log;
ALTER TABLE audit_log_new RENAME TO audit_log;
CREATE INDEX audit_log_thread ON audit_log (thread_id, occurred_at);
CREATE TRIGGER audit_log_no_delete BEFORE DELETE ON audit_log
BEGIN
  SELECT RAISE(ABORT, 'the audit log is append-only');
END;
CREATE TRIGGER audit_log_no_update BEFORE UPDATE ON audit_log
BEGIN
  SELECT RAISE(ABORT, 'the audit log is append-only');
END;
PRAGMA legacy_alter_table = OFF;""",
    # Sequence flow (.agents/prd/2026-10-26-sequence-flow.md, ADR 0008): Steps form a tree of paths (section,
    # parent If, branch), Action and If kinds (an Enrollment Step becomes an enroll Action), the LinkedIn Step's
    # action becomes linkedin_move, Enrollments get an Organization, Finishing and three end reasons, Tasks keep
    # Jeremy's pick, and a Talent Pipeline. CHECKs change, so each table is rebuilt (as in 22 and 28). An existing
    # Enrollment gets its Person's Organization only when exactly one fits (FR-SEQ-46).
    29: """PRAGMA legacy_alter_table = ON;
CREATE TABLE sequence_steps_new (
  id                 INTEGER PRIMARY KEY,
  sequence_id        INTEGER NOT NULL REFERENCES sequences (id),
  position           INTEGER CHECK (position > 0),
  section            TEXT NOT NULL DEFAULT 'body' CHECK (section IN ('start', 'body', 'completion')),
  parent_step_id     INTEGER REFERENCES sequence_steps (id),
  branch             INTEGER CHECK (branch IN (0, 1)),
  kind               TEXT NOT NULL CHECK (kind IN ('email', 'call', 'linkedin', 'letter', 'action', 'if')),
  gap_days           INTEGER NOT NULL DEFAULT 0 CHECK (gap_days >= 0),
  at_time            TEXT CHECK (at_time GLOB '[0-2][0-9]:[0-5][0-9]'),
  subject            TEXT NOT NULL DEFAULT '',
  body               TEXT NOT NULL DEFAULT '',
  voicemail          TEXT NOT NULL DEFAULT '',
  linkedin_move      TEXT CHECK (linkedin_move IN ('connect', 'message', 'engage')),
  reply_to_step_id   INTEGER REFERENCES sequence_steps (id),
  enroll_sequence_id INTEGER REFERENCES sequences (id),
  action_kind        TEXT CHECK (action_kind IN ('set_stage', 'enroll', 'end_for_others', 'set_value')),
  pipeline           TEXT CHECK (pipeline IN ('REVENUE', 'FUNDRAISING', 'TALENT')),
  stage              TEXT,
  value              INTEGER,
  condition          TEXT CHECK (condition IN ('end_reason', 'has_channel', 'stage', 'jeremy_picks')),
  operand            TEXT NOT NULL DEFAULT '',
  true_label         TEXT NOT NULL DEFAULT '',
  false_label        TEXT NOT NULL DEFAULT '',
  UNIQUE (sequence_id, position),
  CHECK ((parent_step_id IS NULL) = (branch IS NULL)),
  CHECK ((kind = 'action') = (action_kind IS NOT NULL)),
  CHECK ((kind = 'if') = (condition IS NOT NULL))
) STRICT;
INSERT INTO sequence_steps_new (id, sequence_id, position, kind, gap_days, at_time, subject, body, voicemail,
  linkedin_move, reply_to_step_id, enroll_sequence_id, action_kind)
SELECT id, sequence_id, position, CASE kind WHEN 'enrollment' THEN 'action' ELSE kind END, gap_days, at_time,
       subject, body, voicemail, action, reply_to_step_id, enroll_sequence_id,
       CASE kind WHEN 'enrollment' THEN 'enroll' END
FROM sequence_steps;
DROP TABLE sequence_steps;
ALTER TABLE sequence_steps_new RENAME TO sequence_steps;
CREATE TABLE enrollments_new (
  id              INTEGER PRIMARY KEY,
  person_id       INTEGER NOT NULL REFERENCES persons (id),
  sequence_id     INTEGER NOT NULL REFERENCES sequences (id),
  state           TEXT NOT NULL CHECK (state IN ('ACTIVE', 'PAUSED', 'FINISHING', 'FINISHED')),
  reason          TEXT CHECK (reason IN ('COMPLETED', 'GOAL_MET', 'NOT_RIGHT_NOW', 'LOST', 'UNSUBSCRIBED', 'CANCELED')),
  paused_at       TEXT,
  pause_reason    TEXT,
  enrolled_at     TEXT NOT NULL,
  finished_at     TEXT,
  starts_at       TEXT,
  responded_at    TEXT,
  organization_id INTEGER REFERENCES organizations (id),
  CHECK (state = 'PAUSED' OR (state IN ('FINISHING', 'FINISHED')) = (reason IS NOT NULL))
) STRICT;
INSERT INTO enrollments_new (id, person_id, sequence_id, state, reason, paused_at, pause_reason, enrolled_at,
  finished_at, starts_at, responded_at)
SELECT id, person_id, sequence_id, state, reason, paused_at, pause_reason, enrolled_at, finished_at, starts_at,
       responded_at FROM enrollments;
DROP TABLE enrollments;
ALTER TABLE enrollments_new RENAME TO enrollments;
CREATE UNIQUE INDEX enrollments_live ON enrollments (person_id, sequence_id) WHERE state != 'FINISHED';
WITH person_orgs (person_id, organization_id) AS (
  SELECT i.person_id, o.id FROM identities i
  JOIN organizations o ON o.domain = org_key(substr(i.value, instr(i.value, '@') + 1))
  WHERE i.kind = 'email' AND i.person_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM memberships m WHERE m.person_id = i.person_id AND m.organization_id = o.id AND m.member = 0)
  UNION
  SELECT person_id, organization_id FROM memberships WHERE member = 1)
UPDATE enrollments SET organization_id = (SELECT max(organization_id) FROM person_orgs p
                                          WHERE p.person_id = enrollments.person_id)
WHERE (SELECT count(*) FROM person_orgs p WHERE p.person_id = enrollments.person_id) = 1;
CREATE TABLE tasks_new (
  id            INTEGER PRIMARY KEY,
  enrollment_id INTEGER NOT NULL REFERENCES enrollments (id),
  step_id       INTEGER NOT NULL REFERENCES sequence_steps (id),
  due_at        TEXT NOT NULL,
  state         TEXT NOT NULL CHECK (state IN ('DUE', 'DONE', 'SKIPPED')),
  done_at       TEXT,
  thread_id     INTEGER REFERENCES threads (id),
  flag          TEXT,
  choice        INTEGER CHECK (choice IN (0, 1)),
  UNIQUE (enrollment_id, step_id),
  CHECK ((state = 'DUE') = (done_at IS NULL))
) STRICT;
INSERT INTO tasks_new (id, enrollment_id, step_id, due_at, state, done_at, thread_id, flag)
SELECT id, enrollment_id, step_id, due_at, state, done_at, thread_id, flag FROM tasks;
DROP TABLE tasks;
ALTER TABLE tasks_new RENAME TO tasks;
CREATE INDEX tasks_due ON tasks (state, due_at);
CREATE TABLE pipeline_log_new (
  id              INTEGER PRIMARY KEY,
  organization_id INTEGER NOT NULL REFERENCES organizations (id),
  pipeline        TEXT NOT NULL CHECK (pipeline IN ('REVENUE', 'FUNDRAISING', 'TALENT')),
  kind            TEXT NOT NULL CHECK (kind IN ('STAGE', 'VALUE', 'UNDO')),
  stage           TEXT,
  value           INTEGER,
  undoes          INTEGER REFERENCES pipeline_log (id),
  effective_at    TEXT NOT NULL,
  recorded_at     TEXT NOT NULL,
  CHECK ((kind = 'STAGE') = (stage IS NOT NULL) AND (kind = 'VALUE') = (value IS NOT NULL)
         AND (kind = 'UNDO') = (undoes IS NOT NULL))
) STRICT;
INSERT INTO pipeline_log_new SELECT id, organization_id, pipeline, kind, stage, value, undoes, effective_at,
  recorded_at FROM pipeline_log;
DROP TABLE pipeline_log;
ALTER TABLE pipeline_log_new RENAME TO pipeline_log;
CREATE INDEX pipeline_log_org ON pipeline_log (organization_id, pipeline, effective_at);
CREATE TRIGGER pipeline_log_no_delete BEFORE DELETE ON pipeline_log
BEGIN
  SELECT RAISE(ABORT, 'the pipeline log is append-only');
END;
CREATE TRIGGER pipeline_log_no_update BEFORE UPDATE ON pipeline_log
BEGIN
  SELECT RAISE(ABORT, 'the pipeline log is append-only');
END;
PRAGMA legacy_alter_table = OFF;""",
    # Mainline Sequences (.agents/design/2026-10-27-mainline-sequences.md): a Sequence's "use when" line, and Needs
    # edit: a Draft from a Step starts unsendable until changed. Steps that existed before keep sending as they did.
    30: """ALTER TABLE sequences ADD COLUMN description TEXT NOT NULL DEFAULT '';
ALTER TABLE sequence_steps ADD COLUMN needs_edit INTEGER NOT NULL DEFAULT 1 CHECK (needs_edit IN (0, 1));
UPDATE sequence_steps SET needs_edit = 0;
ALTER TABLE composition_drafts ADD COLUMN needs_edit INTEGER NOT NULL DEFAULT 0 CHECK (needs_edit IN (0, 1));""",
}

# ponytail: fixed list; extend when a free-mail sender forms an Organization by mistake.
FREE_MAIL = frozenset({
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "msn.com",
    "yahoo.com", "ymail.com", "icloud.com", "me.com", "mac.com", "aol.com", "proton.me",
    "protonmail.com", "gmx.com", "gmx.net", "mail.com", "yandex.com", "zoho.com",
    "fastmail.com", "hey.com",
})

class SendFailed(Exception):
    """Raised by a Sender when a reply could not be sent."""


class SendUnknown(SendFailed):
    """The provider may or may not have sent the message (timeout, lost response, crash before recording)."""


class ResendReceiptConflict(ValueError):
    """A provider email UUID already has a different authoritative receipt."""


class IdentityTaken(Exception):
    """Raised when an Identity already belongs to another Person (I-6)."""


class StaleThreadSnapshot(ValueError):
    """The snapshot predates a merge of its Thread; restoring it would overwrite another triage state."""


class DraftConflict(ValueError):
    """A newer saved Draft revision exists; stale edits must not overwrite it."""


@dataclass(frozen=True)
class Identity:
    kind: str  # "email", "slack_user", or "phone"
    value: str
    display_name: str | None = None
    profile_email: str | None = None


@dataclass(frozen=True)
class Incoming:
    connection_id: int
    channel: str
    channel_kind: str  # mailbox, channel, private, im, mpim
    kind: str  # MESSAGE, EDIT, DELETE
    native_id: str
    sender: Identity | None  # None = Jeremy
    occurred_at: datetime
    content: str | None
    thread_ref: list[str] = field(default_factory=list)
    title: str | None = None
    data: dict = field(default_factory=dict)
    channel_name: str | None = None  # a friendly name for the Channel (Slack #name, DM partner); kept current
    synchronous: str | None = None  # "call" or "meeting": a Phone call or Meeting puts its Thread in Todo whoever
    # started it (FR-OA-13, FR-OA-14); its details stay in data
    participants: tuple[Identity, ...] = ()  # others in it besides the sender (Meeting attendees), each kept as an
    # Identity so Membership and the roll-up see them (FR-OA-15)


@dataclass(frozen=True)
class Thread:
    id: int
    channel_id: int
    title: str
    status: str
    status_detail: str
    snoozed_until: str | None
    case_id: int | None
    follow_up_at: str | None = None  # Waiting for them: back to Todo then with no reply (#116)


@dataclass(frozen=True)
class Done: ...


@dataclass(frozen=True)
class Snooze:
    until: datetime | None = None


@dataclass(frozen=True)
class Wait: ...


@dataclass(frozen=True)
class Investigate: ...


@dataclass(frozen=True)
class MarkTodo:
    detail: str  # CREATED, NEW_REPLY, or IN_PROGRESS (Plain markThreadAsTodo; S key)


@dataclass(frozen=True)
class Ignore: ...


@dataclass(frozen=True)
class Unignore: ...


@dataclass(frozen=True)
class SetCase:
    case_id: int | None = None
    new_title: str | None = None


Action = Done | Snooze | Wait | Investigate | MarkTodo | Ignore | Unignore | SetCase


@dataclass(frozen=True)
class Arrived:
    """An Activity reaching a Thread: a message, edit, deletion, or Phone call."""
    kind: str = "MESSAGE"
    from_jeremy: bool = False  # a Phone call never counts as Jeremy's: every one puts its Thread in Todo
    blocked: bool = False  # the sender's Identity is Blocked


@dataclass(frozen=True)
class Woke: ...  # a timed snooze came due, or a wait ran out (#116)


@dataclass(frozen=True)
class Reopen: ...  # a one-time import touched the Thread


@dataclass(frozen=True)
class Staged:
    """A staged Attachment as a Sender sends it: its stored file and the name and type to give it."""
    path: Path
    name: str
    type: str
    size: int
    sha: str = ""


class Sender(Protocol):
    def send(self, thread: Thread, text: str, to: list[str], cc: list[str],
             history: list[dict], files: Sequence[Staged] = ()) -> str:
        """Send a reply with `files` attached; return its native_id. history: the Thread's messages, oldest
        first, as {"native_id", "data"}. Raise SendFailed when the provider refused it, SendUnknown when it may
        have gone out."""

    def start(self, to: list[str], cc: list[str], subject: str | None, body: str, key: str,
              files: Sequence[Staged] = ()) -> str:
        """Send a new message (no Thread); return its native_id. `key` is unique per send attempt.
        Raise SendFailed when the provider refused it, SendUnknown when it may have gone out."""

    # Slack's Sender also has conversation(channel) -> (Channel kind, name), which send_draft asks for a Slack Draft.


# --- storage ---------------------------------------------------------------

def connect(path: str = ":memory:") -> sqlite3.Connection:
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("PRAGMA busy_timeout = 5000")  # the service and the console share the file
    db.create_function("org_key", 1, _org, deterministic=True)
    if path != ":memory:":
        db.execute("PRAGMA journal_mode = WAL")
    return db


@contextmanager
def _read_snapshot(db):
    """One coherent read snapshot, nested without committing caller-owned work."""
    db.execute("SAVEPOINT inbox_read_snapshot")
    try:
        yield
    finally:
        db.execute("RELEASE inbox_read_snapshot")


@contextmanager
def _write_snapshot(db):
    """Atomic writes; an existing caller transaction retains ownership of its commit."""
    outer = db.in_transaction
    if not outer:
        db.execute("BEGIN IMMEDIATE")
    db.execute("SAVEPOINT inbox_write_snapshot")
    try:
        yield
    except BaseException:
        db.execute("ROLLBACK TO inbox_write_snapshot")
        db.execute("RELEASE inbox_write_snapshot")
        if not outer:
            db.rollback()
        raise
    else:
        db.execute("RELEASE inbox_write_snapshot")
        if not outer:
            try:
                db.commit()
            except BaseException:
                db.rollback()
                raise


def migrate(db: sqlite3.Connection) -> None:
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if version > SCHEMA_VERSION:
        raise ValueError(f"database schema {version} is newer than supported schema {SCHEMA_VERSION}")
    if version == SCHEMA_VERSION:
        return
    sql = (Path(__file__).with_name("schema.sql")).read_text() if version == 0 else ";\n".join(
        MIGRATIONS[v] for v in range(version + 1, SCHEMA_VERSION + 1))
    db.create_function("org_key", 1, _org, deterministic=True)  # migration 7 (connect() also registers it)
    fk = db.execute("PRAGMA foreign_keys").fetchone()[0]
    db.execute("PRAGMA foreign_keys = OFF")  # table rebuilds (5) drop and rename parents; checked before commit
    # A rebuild's statement journal spills to a temp file past 64 KiB; the update's migration check runs in a
    # read-only container with no writable temp directory ("disk I/O error" on 28 with the live audit log).
    db.execute("PRAGMA temp_store = MEMORY")
    try:
        db.executescript(f"BEGIN IMMEDIATE;\n{sql}\nPRAGMA user_version = {SCHEMA_VERSION};")
        if db.execute("PRAGMA foreign_key_check").fetchone():
            raise ValueError("migration would leave a broken foreign key")
        db.execute("COMMIT")
    except Exception:
        db.rollback()
        raise
    finally:
        db.execute("PRAGMA legacy_alter_table = OFF")
        db.execute(f"PRAGMA foreign_keys = {fk}")




def get_thread(db, thread_id: int) -> Thread:
    r = db.execute(
        "SELECT id, channel_id, title, status, status_detail, snoozed_until, case_id, follow_up_at"
        " FROM threads WHERE id = (SELECT COALESCE(merged_into, id) FROM threads WHERE id = ?)",
        (thread_id,)).fetchone()
    if r is None:
        raise KeyError(f"thread {thread_id}")
    return Thread(*r)



# --- unsent composition ------------------------------------------------------

@writer("write")
def save_draft(db, values: dict, *, draft_id: int | None = None,
               expected_revision: int | None = None, thread_id: int | None = None, task_id: int | None = None) -> dict:
    """Persist the complete edited envelope, including incomplete text; never send or create work.
    `thread_id` makes a new Draft that Thread's reply Draft (save_reply_draft) and `task_id` an email Task's
    Draft (sequences); an edit keeps both."""
    if not isinstance(values, dict) or set(values) != {
            "connection_id", "kind", "sender", "to", "cc", "subject", "body"}:
        raise ValueError("draft values must contain exactly connection_id, kind, sender, to, cc, subject, body")
    if type(values["kind"]) is not str or values["kind"] not in ("email", "sms", "slack"):
        raise ValueError("draft kind must be email, sms, or slack")
    if any(type(values[k]) is not str for k in ("sender", "to", "cc", "body")):
        raise ValueError("draft sender, to, cc, and body must be strings")
    if values["kind"] == "email":
        if type(values["subject"]) is not str:
            raise ValueError("email draft subject must be a string")
    elif values["subject"] is not None or values["cc"] != "":
        raise ValueError("SMS and Slack draft subject must be None and cc must be empty")
    connection_id = values["connection_id"]
    if connection_id is not None and (type(connection_id) is not int or connection_id <= 0):
        raise ValueError("draft connection_id must be a positive integer or None")
    if (draft_id, expected_revision) != (None, None) and (
            type(draft_id) is not int or draft_id <= 0
            or type(expected_revision) is not int or expected_revision <= 0):
        raise ValueError("existing draft requires positive integer draft_id and expected_revision")
    with db:
        if not db.in_transaction:
            db.execute("BEGIN IMMEDIATE")
        if connection_id is not None:
            c = db.execute("SELECT platform FROM connections WHERE id = ?", (connection_id,)).fetchone()
            platform = platforms.PLATFORMS.get(c["platform"]) if c else None
            if platform is None or platform.format != values["kind"]:
                raise ValueError("draft Connection must support its message format")
        at = clock.iso(clock.now())
        envelope = tuple(values[k] for k in ("connection_id", "kind", "sender", "to", "cc", "subject", "body"))
        if draft_id is None:
            draft_id = db.execute(
                'INSERT INTO composition_drafts (connection_id, kind, sender, "to", cc, subject, body,'
                " revision, created_at, updated_at, thread_id, task_id) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)",
                (*envelope, at, at, thread_id, task_id)).lastrowid
        else:
            saved = db.execute(
                'UPDATE composition_drafts SET connection_id = ?, kind = ?, sender = ?, "to" = ?,'
                " cc = ?, subject = ?, body = ?, revision = revision + 1, updated_at = ?,"
                " needs_edit = CASE WHEN subject IS ? AND body = ? THEN needs_edit ELSE 0 END"  # a changed text clears it
                " WHERE id = ? AND revision = ?",
                (*envelope, at, values["subject"], values["body"], draft_id, expected_revision)).rowcount
            if not saved:
                get_draft(db, draft_id)  # a missing row is distinct from a stale writer
                raise DraftConflict("Draft changed since this revision; reload before saving.")
        return get_draft(db, draft_id)


@writer("write")
def mark_needs_edit(db, draft_id: int) -> dict:
    """Needs edit: the Draft cannot send until its text is changed (GLOSSARY). The revision stays."""
    with db:
        get_draft(db, draft_id)
        db.execute("UPDATE composition_drafts SET needs_edit = 1 WHERE id = ?", (draft_id,))
    return get_draft(db, draft_id)


@writer("write")
def save_reply_draft(db, thread_id: int, body: str, task_id: int | None = None) -> dict | None:
    """The reply Draft kept under one Thread id (the one its reply box opened on, so an alias keeps its own):
    text replaces it, empty text deletes it. The envelope comes from the Thread as the reply box shows it; the
    box still sends its own To and Cc. ponytail: last writer wins here; revisions guard the agent's edits."""
    with db:
        if not db.in_transaction:
            db.execute("BEGIN IMMEDIATE")
        row = db.execute('SELECT id, revision, task_id, send_at, "to", cc FROM composition_drafts WHERE thread_id = ?',
                         (thread_id,)).fetchone()
        if not body.strip():
            if row:
                db.execute("DELETE FROM composition_drafts WHERE id = ?", (row["id"],))
                from . import sequences
                sequences.task_draft_deleted(db, dict(row))  # an email Task's reply cleared: the Task is skipped
            return None
        v = thread_view(db, thread_id)
        ch, title = v["channel"], v["thread"].title
        kind = platforms.PLATFORMS[ch["platform"]].format
        if kind is None:
            raise ValueError(NO_REPLY)
        values = dict(connection_id=ch["connection_id"], kind=kind, sender=ch["external_id"], cc="", subject=None,
                      to=v["where"], body=body)
        if kind == "email":
            from .email import reply_all
            to, cc = reply_all([{"native_id": m["native_id"], "data": m["data"]} for m in v["messages"]],
                               ch["external_id"])
            values |= dict(to=", ".join(to), cc=", ".join(cc), subject=title if title.lower().startswith("re:")
                           else f"Re: {title}")
        if row and row["send_at"]:  # a scheduled reply keeps the To and Cc its reply box scheduled it with
            values |= dict(to=row["to"], cc=row["cc"])
        if row:
            return save_draft(db, values, draft_id=row["id"], expected_revision=row["revision"])
        return save_draft(db, values, thread_id=thread_id, task_id=task_id)


def send_time(choice: str, now: datetime, rand=random.random) -> datetime:
    """Send later: "tomorrow", or "monday" (the next one; a week out on a Monday), at a random minute from 7 to
    9 am in Jeremy's time, so scheduled mail does not all land on the hour."""
    from .sequences import LOCAL
    day = now.astimezone(LOCAL).date()
    day += timedelta(days=1 if choice == "tomorrow" else 7 - day.weekday())
    return (datetime.combine(day, time(7), LOCAL) + timedelta(minutes=int(rand() * 120))).astimezone(timezone.utc)


@writer("write")
def schedule_draft(db, draft_id: int, send_at: datetime | None) -> dict:
    """Set when the service sends this Draft (None: only when Jeremy sends it); clears any failed send. The
    revision stays, so an open editor's next save does not conflict."""
    with db:
        if not db.in_transaction:
            db.execute("BEGIN IMMEDIATE")
        if get_draft(db, draft_id)["sending_at"]:
            raise SendUnknown("Send outcome unknown; check before scheduling it.")
        db.execute("UPDATE composition_drafts SET send_at = ?, send_error = NULL WHERE id = ?",
                   (clock.iso(send_at) if send_at else None, draft_id))
    return get_draft(db, draft_id)


@writer("write")
def schedule_reply(db, thread_id: int, body: str, to: list[str], cc: list[str], send_at: datetime | None) -> dict:
    """Save a reply box as its Thread's reply Draft, with the box's To and Cc, to send at `send_at`."""
    with db:
        if not db.in_transaction:
            db.execute("BEGIN IMMEDIATE")
        d = save_reply_draft(db, thread_id, body)
        if d is None:
            raise ValueError("Nothing to send.")
        db.execute('UPDATE composition_drafts SET "to" = ?, cc = ? WHERE id = ?', (", ".join(to), ", ".join(cc), d["id"]))
        return schedule_draft(db, d["id"], send_at)


def claim_due_drafts(db, now: datetime) -> list[dict]:
    """The Drafts whose send time has come, with that time cleared so each is tried once."""
    # ponytail: a service crash between this claim and the send leaves an unscheduled Draft with no send_error
    with db:
        if not db.in_transaction:
            db.execute("BEGIN IMMEDIATE")
        due = [dict(r) for r in db.execute("SELECT * FROM composition_drafts WHERE send_at <= ? AND sending_at IS NULL"
                                           " ORDER BY send_at, id", (clock.iso(now),))]
        db.executemany("UPDATE composition_drafts SET send_at = NULL WHERE id = ?", [(d["id"],) for d in due])
    return due


def set_send_error(db, draft_id: int, error: str) -> None:
    with db:
        db.execute("UPDATE composition_drafts SET send_error = ? WHERE id = ?", (error, draft_id))


def reply_drafts(db) -> dict[int, str]:
    """Every reply Draft's text, by the Thread id it was saved under."""
    return {r[0]: r[1] for r in db.execute("SELECT thread_id, body FROM composition_drafts WHERE thread_id IS NOT NULL")}


def reply_draft(db, thread_id: int) -> dict | None:
    """The reply Draft saved under this Thread id, or None."""
    r = db.execute("SELECT * FROM composition_drafts WHERE thread_id = ?", (thread_id,)).fetchone()
    return dict(r) if r else None


def get_draft(db, draft_id: int) -> dict:
    if type(draft_id) is not int or draft_id <= 0:
        raise ValueError("draft_id must be a positive integer")
    r = db.execute("SELECT * FROM composition_drafts WHERE id = ?", (draft_id,)).fetchone()
    if r is None:
        raise KeyError(f"draft {draft_id}")
    return dict(r)


@writer("write")
def delete_draft(db, draft_id: int) -> dict:
    """Remove one unsent Draft; return its row so `restore_draft` can put it back."""
    from . import sequences
    with db:
        draft = get_draft(db, draft_id)
        db.execute("DELETE FROM composition_drafts WHERE id = ?", (draft_id,))
        sequences.task_draft_deleted(db, draft)  # deleting an email Task's Draft skips the Task
    return draft


@writer("write")
def restore_draft(db, draft: dict) -> None:
    from . import sequences
    columns = ", ".join(f'"{c}"' for c in draft)
    with db:
        sequences.task_draft_restored(db, draft)
        db.execute(f"INSERT INTO composition_drafts ({columns}) VALUES ({', '.join('?' * len(draft))})",
                   tuple(draft.values()))


def list_drafts(db) -> list[dict]:
    return [dict(r) for r in db.execute("SELECT * FROM composition_drafts ORDER BY updated_at DESC, id DESC")]


def recipient_names(db, to: str, kind: str) -> str:
    """Draft recipients as People names; addresses no Person owns stay as typed. Slack: the conversation's name."""
    if kind == "slack":
        return re.sub(r"\s*<[A-Z0-9]+>", "", to).strip()
    names = []
    for typed, value in ([(n, n) for n in platforms.destinations(to, "sms")] if kind == "sms"
                         else [(person_name(n) or a, a.lower()) for n, a in getaddresses([to]) if a]):
        row = db.execute("SELECT p.display_name FROM identities i JOIN persons p ON p.id = i.person_id"
                         " WHERE i.kind = ? AND i.value = ?", (platforms.FORMATS[kind].identity, value)).fetchone()
        names.append(row[0] if row else typed)
    return ", ".join(names)


def draft_problem(values: dict, files: Sequence[Staged] = ()) -> str | None:
    """What a Draft still needs before it can send; None when complete. Providers judge the addresses."""
    rules = platforms.FORMATS[values["kind"]]
    if rules.size_problem and (problem := rules.size_problem(files)):
        return problem
    if values["connection_id"] is None:
        return "Choose a Connection"
    if values["kind"] == "slack" and len(platforms.destinations(values["to"], "slack")) != 1:
        return "Choose one conversation"
    if not values["to"].strip():
        return "Add a recipient"
    if values["kind"] == "sms" and len(platforms.destinations(values["to"], "sms")) != 1:
        return "SMS needs one number"
    if rules.cc_subject and not values["subject"].strip():
        return "Add a subject"
    if not values["body"].strip():
        return "Write a message"
    return None


def _send_checkpoint(db, connection_id):
    high_water = db.execute("SELECT coalesce(max(id), 0) FROM activities").fetchone()[0]
    states = {r["id"]: tuple(r)[1:] for r in db.execute(
        "SELECT t.id, t.status, t.status_detail, t.status_changed_at, t.snoozed_until,"
        " t.detail_before_snooze, t.follow_up_at, t.case_id FROM threads t"
        " JOIN channels ch ON ch.id = t.channel_id"
        " WHERE ch.connection_id = ? AND t.merged_into IS NULL", (connection_id,))}
    return high_water, states


def _send_changes(db, connection_id, checkpoint):
    """Triage or incoming work observed during the provider call, including unchanged Todo detail."""
    high_water, before = checkpoint
    _, states = _send_checkpoint(db, connection_id)
    incoming = {r[0] for r in db.execute(
        "SELECT DISTINCT thread_id FROM thread_activities WHERE id > ? AND identity_id IS NOT NULL",
        (high_water,))}
    previous = {}
    for old, state in before.items():
        previous.setdefault(get_thread(db, old).id, set()).add(state)
    changes = {}
    for tid, state in states.items():
        if tid in incoming or state not in previous.get(tid, set()):
            changes[tid] = state
    return changes


def _preserve_send_changes(db, thread_id, changes):
    candidates = [(tid, state) for tid, state in changes.items() if get_thread(db, tid).id == thread_id]
    if not candidates:
        return False
    _, state = max(candidates, key=lambda item: (datetime.fromisoformat(item[1][2]), -item[0]))
    db.execute(
        "UPDATE threads SET status = ?, status_detail = ?, status_changed_at = ?, snoozed_until = ?,"
        " detail_before_snooze = ?, follow_up_at = ? WHERE id = ?", (*state[:6], thread_id))
    return True


def _send(db, connection_id: int, fmt: str, files: Sequence[Staged], call, record, *, settle=None,
          draft_id: int | None = None, thread_id: int | None = None) -> Thread:
    """The send sequence a reply and a Draft share: check the files against the format's size limit
    (ValueError, nothing sent), make the provider call, then record what went out and clear the staged files
    (`draft_id=` or `thread_id=`). `call()` returns the native_id; `record(native_id, now)` runs inside the
    recording transaction and returns (the new MESSAGE Activity's id, or None when it is already stored; its
    Thread's id). `settle(thread_id)`, when given, runs last in that transaction (after the Attachments are
    stored) and returns the Thread's id after any reconciliation.

    SendFailed: refused, nothing recorded. SendUnknown: it may have gone out, nothing recorded (decision #59);
    any other exception once the call has started counts as SendUnknown. Either sets the Connection's Last
    Error, since a timeout is as much a sign of trouble as a refusal; a send that goes through clears it."""
    rules = platforms.FORMATS[fmt]
    if rules.size_problem and (problem := rules.size_problem(files)):
        raise ValueError(problem)
    try:
        native_id = call()
    except SendFailed as e:
        set_connection_error(db, connection_id, str(e))
        raise
    except Exception as e:
        set_connection_error(db, connection_id, f"Send outcome unknown: {e}")
        raise SendUnknown(f"Send outcome unknown: {e}") from e
    set_connection_error(db, connection_id, None)
    column, owner = _owner(db, draft_id, thread_id)
    with db:
        if not db.in_transaction:
            db.execute("BEGIN IMMEDIATE")
        activity_id, sent_in = record(native_id, clock.now())
        if activity_id:
            _add_sent(db, activity_id, files)
        db.execute(f"DELETE FROM staged_attachments WHERE {column} = ?", (owner,))
        if settle:
            sent_in = settle(sent_in)
    return get_thread(db, sent_in)


def send_draft(db, draft_id: int, revision: int, sender: Sender, *, again: bool = False) -> Thread:
    """Send one saved Draft revision, record it, then remove the Draft. An email starts a new Thread; an SMS or
    Slack message is recorded like any message Jeremy sends there, so it joins the conversation's Thread unless
    the conversation has been quiet long enough (FR-33).

    `sending_at` is set before the provider call and cleared only on a refusal, so a send whose outcome is
    unknown leaves the Draft locked; `again` is Jeremy's explicit choice to send it once more anyway.
    """
    with db:
        if not db.in_transaction:
            db.execute("BEGIN IMMEDIATE")
        d = get_draft(db, draft_id)
        if d["thread_id"] is not None:
            raise ValueError("A reply Draft sends from its Thread's reply box.")
        if d["revision"] != revision:
            raise DraftConflict("Draft changed since this revision; reload before sending.")
        if d["sending_at"] and not again:
            raise SendUnknown("Send outcome unknown; check before sending again.")
        files = staged_files(db, draft_id=draft_id)
        from . import sequences
        problem = draft_problem(d, files) or sequences.send_problem(db, d)  # size check included, so _send's cannot fail after the claim
        if problem:
            raise ValueError(problem)
        connection = db.execute("SELECT * FROM connections WHERE id = ?", (d["connection_id"],)).fetchone()
        started = clock.now()
        at = clock.iso(started)
        gmail = platforms.PLATFORMS[connection["platform"]].receipts
        checkpoint = _send_checkpoint(db, connection["id"]) if gmail else None
        db.execute("UPDATE composition_drafts SET sending_at = ? WHERE id = ?", (at, draft_id))
    sms = d["kind"] == "sms"
    to, cc = platforms.destinations(d["to"], d["kind"]), platforms.destinations(d["cc"], d["kind"])
    changes = {}

    def record(native_id, now):
        if gmail:
            now = started
            changes.update(_send_changes(db, connection["id"], checkpoint))
        db.execute("DELETE FROM composition_drafts WHERE id = ?", (draft_id,))
        echo = db.execute("SELECT thread_id FROM activities WHERE kind = 'MESSAGE' AND native_id = ?",
                          (native_id,)).fetchone()
        if echo:  # the provider's own copy arrived first (Fastmail Sent poll, Quo webhook, Slack event)
            return None, get_thread(db, echo["thread_id"]).id
        if d["kind"] in ("sms", "slack"):
            title = next((ln.strip() for ln in d["body"].splitlines() if ln.strip()), "Text")[:80]
            if sms:
                inc = Incoming(connection["id"], to[0], "im", "MESSAGE", native_id, None, now, d["body"],
                               title=title, data={"peer": to[0]}, channel_name=to[0])
            else:
                kind, label = sender.conversation(to[0])
                inc = Incoming(connection["id"], to[0], kind, "MESSAGE", native_id, None, now, d["body"],
                               title=title, data={"channel": to[0], "ts": native_id.split(":", 1)[1], "kind": kind},
                               channel_name=label)
            thread_id = _ingest(db, inc).id
            return db.execute("SELECT id FROM activities WHERE kind = 'MESSAGE' AND native_id = ?",
                              (native_id,)).fetchone()["id"], thread_id
        mailbox = db.execute("SELECT native_id FROM channels WHERE connection_id = ? AND kind = 'mailbox'",
                             (connection["id"],)).fetchone()
        channel = mailbox["native_id"] if mailbox else connection["self_id"]
        channel_id = _channel_id(db, connection["id"], channel, "mailbox")
        thread_id = _new_thread(db, channel_id, native_id, d["subject"].strip()[:80],
                                transition(None, Arrived(from_jeremy=True)), now)
        data = {"from": connection["self_id"], "to": to, "cc": cc, "subject": d["subject"]}
        if checkpoint is not None:
            data["seen_through_activity_id"] = checkpoint[0]
        return db.execute(
            "INSERT INTO activities (thread_id, kind, occurred_at, native_id, content, data)"
            " VALUES (?, 'MESSAGE', ?, ?, ?, ?)", (thread_id, clock.iso(now), native_id, d["body"], json.dumps(data))).lastrowid, thread_id

    def settle(thread_id):
        if gmail:
            _reconcile_email_threads(db, True, get_thread(db, thread_id).channel_id)
            thread_id = get_thread(db, thread_id).id
            _preserve_send_changes(db, thread_id, changes)
        sequences.task_sent(db, d, thread_id, clock.now())  # an email Task's Draft: the Task is done
        return thread_id

    try:
        return _send(db, connection["id"], d["kind"], files,
                     lambda: sender.start(to, cc, d["subject"], d["body"], f"draft-{draft_id}-{revision}-{at}", files=files),
                     record, settle=settle, draft_id=draft_id)
    except SendUnknown:
        raise
    except SendFailed:
        with db:
            db.execute("UPDATE composition_drafts SET sending_at = NULL WHERE id = ?", (draft_id,))
        raise


def _new_thread(db, channel_id, native_id, title, status, at) -> int:
    return db.execute(
        "INSERT INTO threads (channel_id, native_thread_id, title, status, status_detail,"
        " status_changed_at, created_at, follow_up_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (channel_id, native_id, title, *status, clock.iso(at), clock.iso(at), _follow_up(status[1], at))).lastrowid


def _set_status(db, thread_id, at, status, detail, snoozed_until=None, before=None, *, cause):
    old = db.execute("SELECT status_detail FROM threads WHERE id = ?", (thread_id,)).fetchone()[0]
    follow_up = _follow_up(detail, at)
    db.execute(
        "UPDATE threads SET status = ?, status_detail = ?, status_changed_at = ?,"
        " snoozed_until = ?, detail_before_snooze = ?, follow_up_at = ? WHERE id = ?",
        (status, detail, clock.iso(at), snoozed_until, before, follow_up, thread_id))
    if detail != old or snoozed_until:  # same detail is no change, except a snooze to a new time
        _audit(db, thread_id, at, cause, old, detail, data={"until": snoozed_until} if snoozed_until else {})


def _left(case_id) -> dict:
    """A CASE entry's data: the Case the Thread left, so that Case's feed shows it going."""
    return {"from": case_id} if case_id else {}


def _audit(db, thread_id, at, cause, old=None, new=None, case_id=None, data=None):
    """One audit log entry: a Status change (new is its detail) or, with new None, a Case change."""
    db.execute("INSERT INTO audit_log (thread_id, occurred_at, kind, cause, old_detail, new_detail, case_id, data)"
               " VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (thread_id, clock.iso(at), "CASE" if new is None else "STATUS",
                                                   cause, old, new, case_id, json.dumps(data or {})))


def _has_replied(db, thread_id) -> bool:
    return db.execute(
        "SELECT 1 FROM thread_activities WHERE thread_id = ? AND kind = 'MESSAGE'"
        " AND identity_id IS NULL", (thread_id,)).fetchone() is not None


# --- status rules (design §4) -------------------------------------------------

def transition(current: tuple[str, str] | None, event, *, has_replied: bool = False,
               before: str | None = None) -> tuple | None:
    """Every Status change: the next (status, detail) — plus (snoozed_until, detail_before_snooze) for a
    Snooze — of a Thread at `current` (None for a new Thread) after an Action, Arrived, Woke, or Reopen;
    None for unchanged. `before` is the Thread's detail_before_snooze. Pure."""
    status, detail = current or (None, None)
    fresh = ("TODO", "NEW_REPLY" if has_replied else "CREATED")  # by reply history [C]
    match event:
        case Arrived(from_jeremy=True) if current is None:
            return ("SNOOZED", "WAITING_FOR_CUSTOMER")  # #61
        case Arrived(blocked=blocked) if current is None:
            return ("DONE", "IGNORED") if blocked else ("TODO", "CREATED")  # FR-7a, FR-6a
        case Arrived() if detail == "IGNORED":
            return None  # FR-6e
        case Arrived(from_jeremy=True, kind=kind):
            return ("SNOOZED", "WAITING_FOR_CUSTOMER") if kind == "MESSAGE" else None  # FR-6b
        case Arrived():
            return None if current == ("TODO", "CREATED") else ("TODO", "NEW_REPLY")  # FR-6c [C]
        case Woke() if detail == "WAITING_FOR_CUSTOMER":
            return ("TODO", "NEW_REPLY")  # #116
        case Woke():
            return ("TODO", before) if before else fresh  # FR-6d [C]
        case Reopen():
            return None if status == "TODO" else fresh
        case Done():
            return ("DONE", "DONE_MANUALLY_SET")
        case Snooze(until=None):
            raise SnoozeNeedsDate()  # WAITING_INDEFINITELY stays readable for old rows (0 live)
        case Snooze(until=until):
            return ("SNOOZED", "WAITING_FOR_DURATION", clock.iso(until), detail if status == "TODO" else None)
        case Wait():
            return ("SNOOZED", "WAITING_FOR_CUSTOMER")
        case Investigate():
            return ("TODO", "IN_PROGRESS")
        case MarkTodo(detail=todo):
            return ("TODO", todo)
        case Ignore():
            return ("DONE", "IGNORED")
        case Unignore():
            return fresh if detail == "IGNORED" else None  # [C]
    return None  # SetCase


def _follow_up(detail, at) -> str | None:
    """Waiting for them ends after WAIT with no reply (#116)."""
    return clock.iso(at + WAIT) if detail == "WAITING_FOR_CUSTOMER" else None


def reopen_imported(db, thread_id: int) -> Thread:
    """A one-time import leaves every touched Thread in Todo: Needs next response once Jeremy has replied,
    else Needs first response."""
    return act(db, thread_id, Reopen())


# --- people ----------------------------------------------------------------

def _ensure_org(db, kind, value):
    if kind != "email" or "@" not in value:
        return
    domain = value.rsplit("@", 1)[1]
    if domain not in FREE_MAIL:
        db.execute("INSERT OR IGNORE INTO organizations (domain, name, created_at)"
                   " VALUES (?, ?, ?)", (_org(domain), _org(domain), clock.iso(clock.now())))


AUTOMATED = re.compile(r"^(no-?reply|do-?not-?reply|donotreply|noreply|service|notifications?|notify|alerts?|"
                       r"mailer(-daemon)?|bounces?|support|team|info|hello|news(letter)?|updates?|billing|"
                       r"invoices?|receipts?|accounts?|calendar|esignature[-\w]*|[a-z]-[0-9a-f]{20,})@", re.I)


def person_name(name: str) -> str:
    """How a Person is named: "First Last". Outlook's "Last, First" is turned around (Jeremy, 2026-10-12)."""
    name = re.sub(r"\s+", " ", (name or "").strip().strip('"'))
    m = re.fullmatch(r"([^,]+),\s*([^,]+)", name)
    return f"{m.group(2)} {m.group(1)}" if m and "@" not in name else name


GENERIC = re.compile(AUTOMATED.pattern.replace(r"|[a-z]-[0-9a-f]{20,}", ""), re.I)  # shared role addresses


def sender_name(name: str | None, address: str) -> str:
    """The Person's name for a sender (#117). A shared service address (service@, noreply@, …) is named after
    the service, not the human it writes for: "Jeremy Legere (via Google Workspace eSignatures)" is "Google
    Workspace eSignatures", and a TidyCal booking "from" Tony Sheehan is "Tidycal". Everyone else keeps their
    own name, in First Last order."""
    name = person_name(name or "") or address
    if "@" not in address or not GENERIC.match(address):
        return name
    via = re.search(r"\(via ([^)]+)\)", name)
    if via:
        return via.group(1).strip()
    org = _org(address.rsplit("@", 1)[1]).split(".")[0]
    return name if org in _name_key(name).replace(" ", "") else org.capitalize()


def _name_key(name: str) -> str:
    """Comparable form of a full name: First Last order, no accents, no "(via …)", lower case."""
    import unicodedata
    name = re.sub(r"\(.*?\)", "", person_name(name))
    name = "".join(c for c in unicodedata.normalize("NFKD", name) if not unicodedata.combining(c))
    return " ".join(name.lower().split())


# Public suffixes of two labels that Jeremy's correspondents use (Canadian provinces, UK, AU, NZ, …).
# ponytail: hand list, not the full Public Suffix List; add tldextract if an Organization splits or merges wrongly
TWO_LABEL_SUFFIXES = frozenset(
    f"{p}.ca" for p in ("ab", "bc", "mb", "nb", "nf", "nl", "ns", "nt", "nu", "on", "pe", "qc", "sk", "yk", "gc")
) | frozenset({"co.uk", "org.uk", "ac.uk", "gov.uk", "com.au", "net.au", "org.au", "gov.au", "co.nz",
               "org.nz", "co.jp", "com.br", "co.in", "com.mx", "co.za"})


def _org(domain: str) -> str:
    """The Organization key of a domain: the domain its company registers. accounts.google.com -> google.com,
    ws-ts.nb.ca stays whole (nb.ca is a public suffix). Used to store, match, and join Organizations."""
    labels = domain.lower().split(".")
    return ".".join(labels[-3:] if ".".join(labels[-2:]) in TWO_LABEL_SUFFIXES else labels[-2:])


def _company_domains(db, person_id, automated: bool = False) -> set[str]:
    """The organizations a Person writes from: humans' company domains, or (automated=True) the domains of
    automated senders, which only ever match other automated senders."""
    return {_org(r[0].rsplit("@", 1)[1]) for r in db.execute(
        "SELECT value FROM identities WHERE person_id = ? AND kind = 'email'", (person_id,))
        if bool(AUTOMATED.match(r[0])) == automated and r[0].rsplit("@", 1)[1] not in FREE_MAIL}


def _matches(key: str, mine: set[str], theirs: set[str], automated: bool) -> bool:
    """The same-person rule (#117) once a name key matches: people need a full name (two words or more) and a
    shared company organization; automated senders a shared sender organization."""
    return bool(key) and (automated or len(key.split()) >= 2) and bool(mine & theirs)


def _same_person(db, name: str, value: str, kind: str) -> int | None:
    """The existing Person this sender is (#117): the same name and the same organization. For people, a full
    name (two words or more) and a company domain; for automated senders (noreply, notifications, …) the same
    sender name from the same organization, so Google's no-reply addresses are one "Google Takeout". Never on
    free-mail domains, and never between a person and an automated relay (a TidyCal booking "from" Tony)."""
    key = _name_key(name or "")
    if kind != "email" or not key:
        return None
    automated = bool(AUTOMATED.match(value))
    domain = value.rsplit("@", 1)[1]
    if domain in FREE_MAIL:
        return None
    for p in db.execute("SELECT id, display_name FROM persons").fetchall():
        if _name_key(p["display_name"]) == key and _matches(key, {_org(domain)}, _company_domains(db, p["id"], automated),
                                                            automated):
            return p["id"]
    return None


@writer("write")
def rename_person(db, person_id: int, name: str) -> None:
    """Jeremy's own name for a Person; senders' display names only replace a name that is still an address."""
    with db:
        db.execute("UPDATE persons SET display_name = ? WHERE id = ?", (name.strip(), person_id))


@writer("write")
def merge_persons(db, keep: int, drop: int) -> None:
    """One Person from two: `drop`'s Identities (and so its Threads) move to `keep`, which keeps the
    fuller name."""
    if keep == drop:
        return
    with db:
        name = _merged_name(db, keep, drop)
        db.execute("UPDATE identities SET person_id = ? WHERE person_id = ?", (keep, drop))
        db.execute("UPDATE OR IGNORE memberships SET person_id = ? WHERE person_id = ?", (keep, drop))
        db.execute("DELETE FROM memberships WHERE person_id = ?", (drop,))  # keep's own change wins
        from . import sequences
        sequences.merge_people(db, keep, drop)
        db.execute("DELETE FROM persons WHERE id = ?", (drop,))
        db.execute("UPDATE persons SET display_name = ? WHERE id = ?", (name, keep))


def _merged_name(db, keep: int, drop: int) -> str:
    names = [r[0] for r in db.execute("SELECT display_name FROM persons WHERE id IN (?, ?) ORDER BY id = ?",
                                      (keep, drop, drop))]
    return person_name(max(names, key=lambda n: ("@" not in n, len(_name_key(n).split()), len(n))))


def merge_preview(db, keep: int, drop: int) -> dict:
    """What merge_persons(keep, drop) would make, without changing anything: name, Identities,
    Organizations, and Thread counts."""
    with _read_snapshot(db):
        identities = [dict(r) for r in db.execute(
            "SELECT kind, value FROM identities WHERE person_id IN (?, ?) ORDER BY kind, value", (keep, drop))]
        every = person_thread_ids(db)
        threads = {t.id: t for pid in (keep, drop) for t in person_threads(db, pid, every)}
        orgs = sorted({o for pid in (keep, drop) for o in person_organizations(db, pid)})
        return {"name": _merged_name(db, keep, drop), "identities": identities, "organizations": orgs,
                "threads": len(threads), "todo": sum(t.status == "TODO" for t in threads.values())}


def merge_duplicates(db) -> list[tuple[str, str]]:
    """Apply the same-person rule to everyone stored, and turn names to First Last; returns (kept, merged)."""
    merged = []
    with db:
        for p in db.execute("SELECT id, display_name FROM persons").fetchall():
            addrs = [r[0] for r in db.execute("SELECT value FROM identities WHERE person_id = ?", (p["id"],))]
            # a Person whose only address is a shared service address is named after the service
            name = sender_name(p["display_name"], addrs[0]) if len(addrs) == 1 else person_name(p["display_name"])
            if name != p["display_name"]:
                db.execute("UPDATE persons SET display_name = ? WHERE id = ?", (name, p["id"]))
    people = db.execute("SELECT id, display_name FROM persons ORDER BY id").fetchall()
    gone = set()
    for i, a in enumerate(people):
        for b in people[i + 1:]:
            key = _name_key(a["display_name"])
            if a["id"] in gone or b["id"] in gone or key != _name_key(b["display_name"]):
                continue
            if any(_matches(key, _company_domains(db, a["id"], auto), _company_domains(db, b["id"], auto), auto)
                   for auto in (False, True)):
                merge_persons(db, a["id"], b["id"])
                gone.add(b["id"])
                merged.append((a["display_name"], b["display_name"]))
    return merged


def import_people(db, people: list[dict]) -> dict:
    """Attio People into local Persons (Jeremy, 2026-10-14). One Attio person is one Person: local Persons that
    own any of their addresses or numbers are merged, and the rest are added. Attio's name wins unless the
    local one is fuller ("Kevin McFarlane" over "Kevin"). Their Company names the Organizations of its domains."""
    report = {"created": [], "merged": [], "renamed": [], "organizations": []}
    mine = {r[0].lower() for r in db.execute("SELECT self_id FROM connections") if r[0]}
    own_domains = {m.rsplit("@", 1)[1] for m in mine if "@" in m}

    def usable(kind, value):  # an address that is one human's own
        if kind != "email":
            return True
        local, _, domain = value.partition("@")
        return not (AUTOMATED.match(value) or local.startswith(("prvs=", "postmaster", "unsubscribe"))
                    or domain.startswith(("noreply.", "reply.")) or ("+" in local and domain in own_domains))

    def vendor(p):  # a brand writing as itself: "Cloudflare" from cloudflare.com, "The Quo Team", "NoReply"
        name = p["name"].strip()
        if re.search(r"\b(team|support|no-?reply)\b", name, re.I):
            return True
        return " " not in name and any(name.lower() in _org(e.rsplit("@", 1)[-1]) for e in p["emails"] if name)

    every = [[("email", e.lower()) for e in p["emails"]] + [("phone", n) for n in p["phones"]] for p in people]
    claims = Counter(i for ids in every for i in set(ids))
    for p, ids in zip(people, every):
        if any(value in mine for _, value in ids) or vendor(p):  # Jeremy himself is never a Person
            continue
        # a company's main line or a shared inbox listed on several Attio People belongs to none of them
        ids = [i for i in ids if claims[i] == 1 and usable(*i)]
        if not ids:
            continue
        owners = sorted({r[0] for kind, value in ids for r in db.execute(
            "SELECT person_id FROM identities WHERE kind = ? AND value = ? AND person_id IS NOT NULL", (kind, value))})
        name = clean_name(p["name"])
        if not owners:
            name = name or address_name(ids[0][1]) or ids[0][1]
            with db:
                keep = _new_person(db, name)
            report["created"].append(name)
        else:
            keep = owners[0]
            for drop in owners[1:]:
                names = [display_name(db, keep), display_name(db, drop)]
                merge_persons(db, keep, drop)
                report["merged"].append(names)
        current = display_name(db, keep)
        new, old = _name_key(name).split(), _name_key(current).split()
        fuller = len(old) > len(new) and old[:len(new)] == new and "@" not in current
        if name and new != old and not fuller:
            rename_person(db, keep, name)
            report["renamed"].append([current, name])
        for kind, value in ids:
            add_identity(db, keep, kind, value)
        company = p["company"]["name"].strip()
        for domain in p["company"]["domains"] if company else []:
            with db:
                if db.execute("UPDATE organizations SET name = ? WHERE domain = ? AND name != ?",
                              (company, _org(domain), company)).rowcount:
                    report["organizations"].append([_org(domain), company])
    return report


def address_name(value: str) -> str:
    """A name read from an address like leslie.magee@… ("Leslie Magee"); "" when it holds no first and last name."""
    parts = re.split(r"[._]", re.sub(r"\d+", "", value.partition("@")[0])) if "@" in value else []
    return " ".join(x.capitalize() for x in parts) if len(parts) >= 2 and all(
        len(x) >= 2 and x.isalpha() for x in parts) else ""


def clean_name(name: str) -> str:
    """A CRM name as a Person's name: First Last, without notes in brackets or a P.Eng credential."""
    name = re.sub(r"\([^)]*\)", " ", name or "")
    name = re.sub(r"(?:^|[\s,-]+)P\.\s?Eng\.?(?=\s|,|$)", " ", name, flags=re.I)
    return person_name(re.sub(r"\s+", " ", name).strip(" ,-"))


def display_name(db, person_id: int) -> str:
    return db.execute("SELECT display_name FROM persons WHERE id = ?", (person_id,)).fetchone()[0]


def _new_person(db, name) -> int:
    return db.execute("INSERT INTO persons (display_name, created_at) VALUES (?, ?)",
                      (name, clock.iso(clock.now()))).lastrowid


def _identity_for(db, sender: Identity) -> sqlite3.Row:
    """Find or create the sender's Identity and attach it to a Person (FR-10)."""
    value = sender.value.lower() if sender.kind == "email" else sender.value
    row = db.execute("SELECT * FROM identities WHERE kind = ? AND value = ?",
                     (sender.kind, value)).fetchone()
    if row is not None and row["person_id"] is not None:
        if sender.display_name:  # a Person still named by its address or user ID gets the real name
            db.execute("UPDATE persons SET display_name = ? WHERE id = ? AND display_name = ?",
                       (sender_name(sender.display_name, value), row["person_id"], value))
        return row
    person_id = None
    if sender.kind == "slack_user" and sender.profile_email:
        owner = db.execute(
            "SELECT person_id FROM identities WHERE kind = 'email' AND value = ?"
            " AND person_id IS NOT NULL", (sender.profile_email.lower(),)).fetchone()
        person_id = owner and owner["person_id"]
    if person_id is None and sender.display_name:
        person_id = _same_person(db, sender.display_name, value, sender.kind)
    if person_id is None:
        person_id = _new_person(db, sender_name(sender.display_name, value))
    if row is None:
        db.execute("INSERT INTO identities (person_id, kind, value) VALUES (?, ?, ?)",
                   (person_id, sender.kind, value))
    else:
        db.execute("UPDATE identities SET person_id = ? WHERE id = ?", (person_id, row["id"]))
    _ensure_org(db, sender.kind, value)
    return db.execute("SELECT * FROM identities WHERE kind = ? AND value = ?",
                      (sender.kind, value)).fetchone()


@writer("create")
def block(db, kind: str, value: str, blocked: bool = True) -> None:
    value = value.lower() if kind == "email" else value
    with db:
        if db.execute("SELECT 1 FROM identities WHERE kind = ? AND value = ?",
                      (kind, value)).fetchone() is None:
            _identity_for(db, Identity(kind, value))
        db.execute("UPDATE identities SET blocked = ? WHERE kind = ? AND value = ?",
                   (int(blocked), kind, value))


def thread_senders(db, thread_id: int) -> list[dict]:
    """The Identities that wrote in a Thread, latest first: label (Person name, else the address), kind, value,
    blocked. Jeremy's own messages have no Identity, so he is never one."""
    rows = db.execute("SELECT identity_id FROM thread_activities WHERE thread_id = ? AND kind = 'MESSAGE'"
                      " AND identity_id IS NOT NULL GROUP BY identity_id ORDER BY MAX(occurred_at) DESC, MAX(id) DESC",
                      (get_thread(db, thread_id).id,)).fetchall()
    senders = []
    for r in rows:
        label, i = _sender(db, r[0])
        senders.append({"label": label, "kind": i["kind"], "value": i["value"], "blocked": bool(i["blocked"])})
    return senders


@writer("create")
def add_identity(db, person_id: int, kind: str, value: str) -> None:
    value = value.lower() if kind == "email" else value
    with db:
        row = db.execute("SELECT person_id FROM identities WHERE kind = ? AND value = ?",
                         (kind, value)).fetchone()
        if row is None:
            db.execute("INSERT INTO identities (person_id, kind, value) VALUES (?, ?, ?)",
                       (person_id, kind, value))
        elif row["person_id"] is None:
            db.execute("UPDATE identities SET person_id = ? WHERE kind = ? AND value = ?",
                       (person_id, kind, value))
        elif row["person_id"] != person_id:
            raise IdentityTaken(value)
        _ensure_org(db, kind, value)


@writer("write")
def remove_identity(db, identity_id: int) -> None:
    with db:
        db.execute("UPDATE identities SET person_id = NULL WHERE id = ?", (identity_id,))


# --- ingest ----------------------------------------------------------------

def _channel_id(db, connection_id: int, native_id: str, kind: str, name: str | None = None) -> int:
    db.execute("INSERT OR IGNORE INTO channels (connection_id, native_id, kind) VALUES (?, ?, ?)",
               (connection_id, native_id, kind))
    if name:
        db.execute("UPDATE channels SET name = ? WHERE connection_id = ? AND native_id = ? AND name IS NOT ?",
                   (name, connection_id, native_id, name))
    return db.execute("SELECT id FROM channels WHERE connection_id = ? AND native_id = ?",
                      (connection_id, native_id)).fetchone()["id"]


def _thread_for_message(db, channel_id, inc: Incoming, identity_id):
    """Existing Thread this message joins, or None (design §4 Threading)."""
    if inc.channel_kind == "meeting":  # one Thread per Event UID, so a series is one Thread (FR-OA-12)
        r = db.execute("SELECT id FROM threads WHERE channel_id = ? AND merged_into IS NULL", (channel_id,)).fetchone()
        return r and r["id"]
    if inc.channel_kind == "mailbox":
        rows, own, refs, known = _email_evidence(db)
        declared = _email_ids(inc.thread_ref, ordered=True)
        ids = set(declared) | _email_ids([inc.native_id], single=True)
        ids = {n for n in ids if not (known.get(n, set()) - {channel_id})}
        for native_id in declared:
            if native_id not in ids:
                continue
            for a in rows:
                if a["channel_id"] == channel_id and native_id in own[a["id"]]:
                    return a["thread_id"]
        return min((a["thread_id"] for a in rows if a["channel_id"] == channel_id
                    and ids & (own[a["id"]] | refs[a["id"]])), default=None)
    for ref in inc.thread_ref:
        r = db.execute("SELECT a.thread_id FROM thread_activities a JOIN threads t ON t.id = a.thread_id"
                       " WHERE t.channel_id = ? AND a.kind = 'MESSAGE' AND a.native_id = ?",
                       (channel_id, ref)).fetchone()
        if r:
            return r["thread_id"]
    if inc.data.get("thread_ts") not in (None, inc.data.get("ts")):
        return None  # the first reply to a top-level Slack message starts its own Thread (FR-4b)
    if inc.channel_kind in ("channel", "private"):
        # A top-level message joins the channel's latest top-level conversation unless the channel has been
        # quiet for a day (FR-4b, FR-38).
        last = db.execute("SELECT max(a.occurred_at) FROM thread_activities a JOIN threads t ON t.id = a.thread_id"
                          " WHERE t.channel_id = ? AND a.kind = 'MESSAGE'", (channel_id,)).fetchone()[0]
        if last is None or inc.occurred_at - datetime.fromisoformat(last) > platforms.FORMATS["slack"].quiet:
            return None
        r = db.execute(
            "SELECT a.thread_id FROM thread_activities a JOIN threads t ON t.id = a.thread_id"
            " WHERE t.channel_id = ? AND a.kind = 'MESSAGE' AND json_extract(a.data, '$.ts') IS NOT NULL"
            " AND coalesce(json_extract(a.data, '$.thread_ts'), json_extract(a.data, '$.ts')) = json_extract(a.data, '$.ts')"
            " ORDER BY a.occurred_at DESC, a.id DESC LIMIT 1", (channel_id,)).fetchone()
        return r["thread_id"] if r else None
    last = db.execute(
        "SELECT a.thread_id, a.identity_id, a.occurred_at FROM thread_activities a"
        " JOIN threads t ON t.id = a.thread_id"
        " WHERE t.channel_id = ? AND a.kind = 'MESSAGE'"
        " ORDER BY a.occurred_at DESC, a.id DESC LIMIT 1", (channel_id,)).fetchone()
    if last is None:
        return None
    gap = inc.occurred_at - datetime.fromisoformat(last["occurred_at"])
    platform = db.execute("SELECT platform FROM connections WHERE id = ?", (inc.connection_id,)).fetchone()
    window = (platform and platforms.FORMATS[platforms.PLATFORMS[platform[0]].format].quiet
              or platforms.FORMATS["slack"].quiet)  # a Channel whose Connection is gone groups like Slack
    return last["thread_id"] if timedelta(0) <= gap <= window else None


ECHO_WINDOW = timedelta(days=1)


def _echo_of_sent(db, thread_id: int, inc: Incoming) -> bool:
    """A copy of Jeremy's own reply coming back in (he is copied, or a recipient forwards to him).

    Resend sends through Amazon SES, which replaces the Message-ID we set, so the copy's native_id never
    matches the stored reply; match on the same text from Jeremy in the same Thread within a day instead.
    """
    text = (inc.content or "").strip()
    if not text:
        return False
    since = clock.iso(inc.occurred_at - ECHO_WINDOW)
    # The copy is Jeremy's text, possibly followed by the quote his reply included (decision #109).
    norm = lambda x: (x or "").replace("\r\n", "\n").strip()  # noqa: E731
    text = norm(text)

    def same(sent):
        sent = norm(sent)
        return bool(sent) and (text == sent or text.startswith(sent)
                               and re.match(r"\s*On .{0,300}?wrote:", text[len(sent):], re.S) is not None)
    return any(same(r["content"]) for r in db.execute(
        "SELECT content FROM thread_activities WHERE thread_id = ? AND kind = 'MESSAGE' AND identity_id IS NULL"
        " AND occurred_at >= ?", (thread_id, since)))


# --- attachments (.agents/prd/2026-10-15-attachments.md) -------------------

ATTACHMENT_TRIES = 5  # downloads given up after this many failures


def file_type(db, sha: str) -> str | None:
    """A served file's type, or None unless a stored Attachment or a staged file holds that sha (I-A4)."""
    r = db.execute("SELECT type FROM attachments WHERE sha = ? AND state = 'stored'"
                   " UNION ALL SELECT type FROM staged_attachments WHERE sha = ? LIMIT 1", (sha, sha)).fetchone()
    return r and r["type"]


def add_attachments(db, activity_id: int, items: list[dict]) -> list[int]:
    """Record a message's Attachments in order. Each item: name, type, size, and optional inline, content_id,
    source (dict), sha, and position (default: its place in items). An item with a sha is stored; one without
    waits for a download."""
    with db:
        return _add_attachments(db, activity_id, items)


def _add_attachments(db, activity_id: int, items: list[dict]) -> list[int]:
    return [db.execute(
        "INSERT INTO attachments (activity_id, position, name, type, size, inline, content_id, source, sha, state)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (activity_id, it.get("position", k), it["name"], it["type"], it["size"], int(bool(it.get("inline"))),
         it.get("content_id"), json.dumps(it.get("source") or {}), it.get("sha"),
         "stored" if it.get("sha") else "pending")).lastrowid
        for k, it in enumerate(items)]


def _add_sent(db, activity_id: int, files: Sequence[Staged]) -> None:
    """Jeremy's sent message keeps the files it carried, stored, in send order (its echo is a duplicate)."""
    _add_attachments(db, activity_id, [{"name": f.name, "type": f.type, "size": f.size, "sha": f.sha,
                                        "source": {"sent": True}} for f in files])


def _downloads(data: dict) -> list[dict]:
    """A new message's files to fetch: Slack's carry {id, url} as a 4th element (FR-A3), Quo's are its media
    URLs (FR-A4). Slack data from before has no source and stays link-only."""
    media = data.get("media") or []
    out = []
    for k, a in enumerate(data.get("attachments") or []):
        source = a[3] if len(a) > 3 else {"url": media[k][1]} if k < len(media) else None
        if source and source.get("url"):
            out.append({"position": k, "name": a[0], "type": a[1], "size": a[2], "source": source})
    return out


def attachments(db, activity_id: int) -> list[dict]:
    return [dict(r) for r in db.execute(
        "SELECT * FROM attachments WHERE activity_id = ? ORDER BY position", (activity_id,))]


def pending_attachments(db, limit: int = 50) -> list[dict]:
    """Attachments waiting for a download, with the Platform to fetch each from."""
    return [dict(r) for r in db.execute(
        "SELECT a.*, c.platform FROM attachments a JOIN activities v ON v.id = a.activity_id"
        " JOIN threads t ON t.id = v.thread_id JOIN channels ch ON ch.id = t.channel_id"
        " JOIN connections c ON c.id = ch.connection_id WHERE a.state = 'pending' ORDER BY a.id LIMIT ?", (limit,))]


def mark_stored(db, attachment_id: int, sha: str, size: int | None = None) -> None:
    """A downloaded file is in the store; `size` replaces an unknown one (Quo media arrive without one)."""
    with db:
        db.execute("UPDATE attachments SET sha = ?, state = 'stored', size = coalesce(?, size) WHERE id = ?",
                   (sha, size, attachment_id))


def expire_attachments(db, now: datetime) -> int:
    """NFR-A6: delete a stored file once every Attachment using it is on an Activity over 365 days old and no
    staged Attachment uses it; those rows become expired (chips fall back to the Platform link). Returns files deleted."""
    with db:
        shas = [r[0] for r in db.execute(
            "SELECT a.sha FROM attachments a JOIN activities v ON v.id = a.activity_id WHERE a.sha IS NOT NULL"
            " AND a.sha NOT IN (SELECT sha FROM staged_attachments) GROUP BY a.sha HAVING max(v.occurred_at) < ?",
            (clock.iso(now - timedelta(days=365)),))]
        db.executemany("UPDATE attachments SET state = 'expired', sha = NULL WHERE sha = ?", [(s,) for s in shas])
    for sha in shas:
        if (path := files.attachment_path(db, sha)) is not None:
            path.unlink(missing_ok=True)
    return len(shas)


def mark_failed(db, attachment_id: int) -> None:
    """Count one failed download; after ATTACHMENT_TRIES the Attachment stays failed (its chip keeps the Platform link)."""
    with db:
        db.execute("UPDATE attachments SET tries = tries + 1,"
                   " state = CASE WHEN tries + 1 >= ? THEN 'failed' ELSE 'pending' END WHERE id = ?",
                   (ATTACHMENT_TRIES, attachment_id))


def _owner(db, draft_id, thread_id) -> tuple[str, int]:
    """The staged_attachments column and id that own these files. A reply Draft's files are its Thread's
    reply files, the ones `reply` sends, so staging on a reply Draft stages on its Thread."""
    if (draft_id is None) == (thread_id is None):
        raise ValueError("stage on exactly one of draft_id or thread_id")
    if thread_id is not None:
        return "thread_id", thread_id
    r = db.execute("SELECT thread_id FROM composition_drafts WHERE id = ?", (draft_id,)).fetchone()
    return ("thread_id", r[0]) if r and r[0] is not None else ("draft_id", draft_id)


@writer("create")
def stage(db, *, sha: str, name: str, type: str, size: int,
          draft_id: int | None = None, thread_id: int | None = None) -> int:
    """Attach a stored file to an unsent Draft or to one Thread's open reply."""
    column, owner = _owner(db, draft_id, thread_id)
    path = files.attachment_path(db, sha)
    if files.files_dir(db) is not None and (path is None or not path.is_file()):
        raise ValueError("stage needs the sha256 of a stored file")
    with db:
        return db.execute(f"INSERT INTO staged_attachments ({column}, sha, name, type, size, added_at)"
                          " VALUES (?, ?, ?, ?, ?, ?)", (owner, sha, name, type, size, clock.iso(clock.now()))).lastrowid


def staged(db, *, draft_id: int | None = None, thread_id: int | None = None) -> list[dict]:
    column, owner = _owner(db, draft_id, thread_id)
    return [dict(r) for r in db.execute(
        f"SELECT * FROM staged_attachments WHERE {column} = ? ORDER BY id", (owner,))]


def staged_files(db, *, draft_id: int | None = None, thread_id: int | None = None) -> list[Staged]:
    """A Draft's or reply's staged Attachments as files to send; ValueError when a stored file is gone."""
    out = []
    for s in staged(db, draft_id=draft_id, thread_id=thread_id):
        path = files.attachment_path(db, s["sha"])
        if path is None or not path.is_file():
            raise ValueError(f"Attachment {s['name']} is missing from the file store; remove it and attach it again")
        out.append(Staged(path, s["name"], s["type"], s["size"], s["sha"]))
    return out


@writer("write")
def unstage(db, staged_id: int) -> None:
    with db:
        db.execute("DELETE FROM staged_attachments WHERE id = ?", (staged_id,))


EMAIL_ATOM = r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+"
EMAIL_DOT_ATOM = EMAIL_ATOM + r"(?:\." + EMAIL_ATOM + r")*"
EMAIL_ID = re.compile(r"(?<!<)<" + EMAIL_DOT_ATOM + r"@(?:" + EMAIL_DOT_ATOM
                      + r"|\[[A-Za-z0-9:.%-]+\])>(?!>)")


def _email_ids(values, single=False, ordered=False) -> set[str] | list[str]:
    """Exact bracketed Message-ID sequences; skip quoted prose and CFWS, optionally retain declared order."""
    text = values if isinstance(values, str) else " ".join(v for v in values if isinstance(v, str))
    clean, depth, quoted, escaped = [], 0, False, False
    for c in text:
        if escaped:
            escaped = False
        elif quoted:
            if c == "\\":
                escaped = True
            elif c == '"':
                quoted = False
        elif depth:
            if c == "\\":
                escaped = True
            elif c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
        elif c == "(":
            clean.append(" ")
            depth = 1
        elif c == '"':
            clean.append(" ")
            quoted = True
        elif c == "\\":
            clean.append(" ")
            escaped = True
        else:
            clean.append(c)
    text = "".join(clean).strip()
    ids = []
    if not (depth or quoted or escaped):
        if single:
            ids = [text] if EMAIL_ID.fullmatch(text) else []
        elif not EMAIL_ID.sub("", text).strip():
            ids = [m.group() for m in EMAIL_ID.finditer(text)]
    return ids if ordered else set(ids)


def resend_native_id(email_id: str) -> str:
    """The immutable local identity of one accepted Resend email, not its SMTP identity."""
    if not isinstance(email_id, str) or re.fullmatch(
            r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", email_id) is None:
        raise ValueError("invalid Resend email ID")
    return f"<{uuid.UUID(email_id)}@resend>"


def record_resend_receipt(db, connection_id: int, email_id: str, message_id: str) -> bool:
    """Durably store verified receipt metadata. Requires an idle connection; never commits caller work."""
    email_id = resend_native_id(email_id)[1:-8]
    if type(connection_id) is not int or connection_id <= 0:
        raise ValueError("invalid Gmail Connection")
    if not isinstance(message_id, str) or EMAIL_ID.fullmatch(message_id) is None:
        raise ValueError("invalid SMTP Message-ID")
    if message_id.endswith("@resend>"):
        raise ValueError("invalid SMTP Message-ID")
    if db.in_transaction:
        raise ValueError("receipt persistence requires an idle connection")
    with _write_snapshot(db):
        connection = db.execute("SELECT platform, self_id, external_id FROM connections WHERE id = ?",
                                (connection_id,)).fetchone()
        if (connection is None or connection["platform"] != "gmail"
                or not connection["self_id"].strip() or not connection["external_id"].strip()):
            raise ValueError("invalid Gmail Connection")
        previous = db.execute("SELECT connection_id, message_id FROM resend_receipts WHERE email_id = ?",
                              (email_id,)).fetchone()
        if previous:
            if tuple(previous) != (connection_id, message_id):
                raise ResendReceiptConflict("conflicting Resend receipt")
            return False
        db.execute("INSERT INTO resend_receipts (email_id, connection_id, message_id) VALUES (?, ?, ?)",
                   (email_id, connection_id, message_id))
        return True


def _gmail_wire_id(db, native_id, connection_id, identity_id):
    """Project receipt evidence only for an exact local outbound-self provider marker."""
    if identity_id is not None:
        return native_id
    match = re.fullmatch(r"<([0-9a-fA-F-]{36})@resend>", native_id)
    if not match:
        return None if native_id.endswith("@resend>") else native_id
    try:
        canonical = resend_native_id(match[1])
    except ValueError:
        return None
    receipt = db.execute("SELECT message_id FROM resend_receipts WHERE email_id = ? AND connection_id = ?",
                         (canonical[1:-8], connection_id)).fetchone()
    return receipt["message_id"] if receipt else None


def _email_evidence(db):
    # ponytail: scan mailbox evidence per ingest; incremental components if mailbox volume makes this slow.
    rows = db.execute(
        "SELECT a.id, a.thread_id, a.native_id, a.data, a.identity_id, a.occurred_at, t.channel_id,"
        " ch.connection_id, c.platform FROM thread_activities a"
        " JOIN threads t ON t.id = a.thread_id JOIN channels ch ON ch.id = t.channel_id"
        " JOIN connections c ON c.id = ch.connection_id"
        " WHERE a.kind = 'MESSAGE' AND ch.kind = 'mailbox' ORDER BY a.id").fetchall()
    own, refs, known = {}, {a["id"]: set() for a in rows}, {}
    for a in rows:
        own[a["id"]] = _email_ids([a["native_id"]], single=True)
        if platforms.PLATFORMS[a["platform"]].receipts:
            wire_id = _gmail_wire_id(db, a["native_id"], a["connection_id"], a["identity_id"])
            if wire_id:
                own[a["id"]] |= _email_ids([wire_id], single=True)
        for n in own[a["id"]]:
            known.setdefault(n, set()).add(a["channel_id"])
    for r in db.execute("SELECT activity_id, native_id FROM email_refs"):
        if r["activity_id"] in refs:
            refs[r["activity_id"]] |= _email_ids([r["native_id"]])
    return rows, own, refs, known


def _reconcile_email_threads(db, apply, channel_id, recover=False) -> dict:
    if channel_id is not None:
        ch = db.execute("SELECT kind FROM channels WHERE id = ?", (channel_id,)).fetchone()
        if ch is None or ch["kind"] != "mailbox":
            raise ValueError(f"channel {channel_id} is not a mailbox")
    rows, own, refs, known = _email_evidence(db)
    rows = [a for a in rows if channel_id is None or a["channel_id"] == channel_id]
    result = {"groups": [], "conflicts": [], "merged": 0, "references_added": 0,
              "messages": len(rows), "raw_headers": 0, "raw_missing": 0, "raw_invalid": 0,
              "held_references": 0}
    added = []
    if recover:
        d = files.raw_dir(db)
        for a in rows:
            data = json.loads(a["data"])
            sha = data.get("raw") if isinstance(data, dict) else None
            if not sha or d is None:
                result["raw_missing"] += 1
                continue
            if not isinstance(sha, str) or re.fullmatch(r"[0-9a-f]{64}", sha) is None or d.is_symlink():
                result["raw_invalid"] += 1
                continue
            path = d / f"{sha}.eml"
            try:
                if not path.is_file():
                    result["raw_missing"] += 1
                    continue
                with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as f:
                    raw = f.read()
                if hashlib.sha256(raw).hexdigest() != sha:
                    raise ValueError("raw hash mismatch")
                headers = BytesHeaderParser(policy=policy.default).parsebytes(raw)
                mids = headers.get_all("Message-ID", [])
                ids = _email_ids([str(v) for v in mids], single=True)
                if not ((len(mids) == 1 and len(ids) == 1
                         and ids == _email_ids([a["native_id"]], single=True))
                        or (not mids and a["native_id"] == f"<sha256-{sha}@inbox>")):
                    raise ValueError("raw Message-ID mismatch")
                recovered = _email_ids([str(v) for h in ("In-Reply-To", "References")
                                        for v in headers.get_all(h, [])])
            except (OSError, ValueError, TypeError, UnicodeError):
                result["raw_invalid"] += 1
                continue
            result["raw_headers"] += 1
            added += [(a["id"], n) for n in sorted(recovered - refs[a["id"]])]
            refs[a["id"]] |= recovered

    threads = {t["id"]: t for t in db.execute(
        "SELECT t.* FROM threads t JOIN channels ch ON ch.id = t.channel_id"
        " WHERE t.merged_into IS NULL AND ch.kind = 'mailbox'"
        + (" AND t.channel_id = ?" if channel_id is not None else ""),
        (channel_id,) if channel_id is not None else ())}
    parent, tokens = {tid: tid for tid in threads}, {}

    def root(tid):
        while parent[tid] != tid:
            parent[tid] = parent[parent[tid]]
            tid = parent[tid]
        return tid

    valid_refs = {}
    for a in rows:
        valid = {n for n in refs[a["id"]] if not (known.get(n, set()) - {a["channel_id"]})}
        valid_refs[a["id"]] = valid
        result["held_references"] += len(refs[a["id"]] - valid)
        for n in sorted(own[a["id"]] | valid):
            key = a["channel_id"], n
            other = tokens.setdefault(key, a["thread_id"])
            left, right = root(a["thread_id"]), root(other)
            parent[max(left, right)] = min(left, right)
    components = {}
    for tid in sorted(threads):
        components.setdefault(root(tid), []).append(tid)
    for ids in components.values():
        if len(ids) < 2:
            continue
        case_ids = {threads[tid]["case_id"] for tid in ids} - {None}
        if len(case_ids) > 1:
            result["conflicts"].append(ids)
            continue
        source = max(ids, key=lambda tid: (datetime.fromisoformat(threads[tid]["status_changed_at"]), -tid))
        messages = [a for a in rows if a["thread_id"] in ids]
        latest_own = max((a for a in messages if a["identity_id"] is None),
                         key=lambda a: (datetime.fromisoformat(a["occurred_at"]), a["id"]), default=None)
        state = threads[source]
        data = json.loads(latest_own["data"]) if latest_own is not None else {}
        seen = data.get("seen_through_activity_id") if isinstance(data, dict) else None
        aliases = {n for a in messages for n in own[a["id"]] - _email_ids([a["native_id"]], single=True)}
        receipt_linked = any(a["identity_id"] is not None and valid_refs[a["id"]] & aliases for a in messages)
        if (state["status_detail"] == "WAITING_FOR_CUSTOMER" and receipt_linked
                and type(seen) is int and 0 <= seen < latest_own["id"]
                and datetime.fromisoformat(state["status_changed_at"]) <= datetime.fromisoformat(latest_own["occurred_at"])):
            # The latest send's durable snapshot distinguishes unseen arrivals from already answered work.
            unseen = {a["thread_id"] for a in messages if a["identity_id"] is not None
                      and a["id"] > seen and threads[a["thread_id"]]["status"] == "TODO"}
            if unseen:
                source = max(unseen, key=lambda tid: (
                    datetime.fromisoformat(threads[tid]["status_changed_at"]), -tid))
        result["groups"].append({"keep": ids[0], "merge": ids[1:], "status_source": source,
                                 "case_id": next(iter(case_ids), None)})
    if apply:
        result["references_added"] = db.executemany(
            "INSERT OR IGNORE INTO email_refs (activity_id, native_id) VALUES (?, ?)", added).rowcount
        for g in result["groups"]:
            state = threads[g["status_source"]]
            db.execute(
                "UPDATE threads SET status = ?, status_detail = ?, status_changed_at = ?, snoozed_until = ?,"
                " detail_before_snooze = ?, follow_up_at = ?, case_id = ? WHERE id = ?",
                (state["status"], state["status_detail"], state["status_changed_at"], state["snoozed_until"],
                 state["detail_before_snooze"], state["follow_up_at"], g["case_id"], g["keep"]))
            marks = ",".join("?" * len(g["merge"]))
            db.execute(f"UPDATE threads SET merged_into = ? WHERE merged_into IN ({marks})",
                       (g["keep"], *g["merge"]))
            result["merged"] += db.execute(f"UPDATE threads SET merged_into = ? WHERE id IN ({marks})",
                                           (g["keep"], *g["merge"])).rowcount
    return result


def reconcile_email_threads(db, apply: bool = False, channel_id: int | None = None) -> dict:
    """Preview exact mailbox reference groups; explicit apply atomically indexes raw headers and aliases."""
    if not isinstance(apply, bool):
        raise ValueError("apply must be True or False")
    if channel_id is not None and type(channel_id) is not int:
        raise ValueError("channel_id must be a mailbox ID or None")
    if not apply:
        with _read_snapshot(db):
            return _reconcile_email_threads(db, False, channel_id, recover=True)
    with _write_snapshot(db):
        return _reconcile_email_threads(db, True, channel_id, recover=True)


def reconcile_resend_receipts(db) -> None:
    """Retry durable receipt evidence without raw recovery; caller owns any outer transaction."""
    with _write_snapshot(db):
        channels = db.execute(
            "SELECT DISTINCT ch.id FROM channels ch JOIN connections c ON c.id = ch.connection_id"
            " JOIN resend_receipts r ON r.connection_id = c.id"
            " WHERE c.platform = 'gmail' AND ch.kind = 'mailbox'").fetchall()
        for channel in channels:
            _reconcile_email_threads(db, True, channel["id"])


def ingest(db, inc: Incoming) -> Thread | None:
    """Store one message, Phone call, Meeting, edit, or deletion, with its participants; returns its Thread, or None
    for a duplicate or an edit/deletion of a message that was never stored."""
    with db:
        return _ingest(db, inc)


def _ingest(db, inc: Incoming) -> Thread | None:
    """`ingest` inside the caller's transaction."""
    duplicate = db.execute(
        "SELECT a.id, t.channel_id FROM activities a JOIN threads t ON t.id = a.thread_id"
        " WHERE a.kind = ? AND a.native_id = ?", (inc.kind, inc.native_id)).fetchone()
    if duplicate:
        if inc.kind == "MESSAGE" and inc.channel_kind == "mailbox":
            ch = db.execute("SELECT id FROM channels WHERE connection_id = ? AND native_id = ?"
                            " AND kind = 'mailbox'", (inc.connection_id, inc.channel)).fetchone()
            if ch and ch["id"] == duplicate["channel_id"]:
                added = db.executemany(
                    "INSERT OR IGNORE INTO email_refs (activity_id, native_id) VALUES (?, ?)",
                    [(duplicate["id"], n) for n in sorted(_email_ids(inc.thread_ref))]).rowcount
                if added:
                    _reconcile_email_threads(db, True, ch["id"])
        return None
    channel_id = _channel_id(db, inc.connection_id, inc.channel, inc.channel_kind, inc.channel_name)
    sender = inc.sender
    if sender and sender.kind == "email" and db.execute(  # Jeremy's own address on any Connection (#117)
            f"SELECT 1 FROM connections WHERE platform IN ({','.join('?' * len(platforms.EMAIL))})"
            " AND lower(self_id) = ?",
            (*platforms.EMAIL, sender.value.lower())).fetchone():
        sender = None
    identity = _identity_for(db, sender) if sender else None
    identity_id = identity["id"] if identity else None
    for p in inc.participants:
        _identity_for(db, p)
    at = inc.occurred_at

    target_id = None
    meeting = inc.kind == "MESSAGE" and inc.synchronous == "meeting"
    call = inc.kind == "MESSAGE" and inc.synchronous is not None  # a Phone call or a Meeting
    if inc.kind == "MESSAGE":
        thread_id = _thread_for_message(db, channel_id, inc, identity_id)
    else:
        target_native = inc.native_id if inc.kind == "DELETE" else inc.native_id.rsplit(":", 1)[0]
        target = db.execute("SELECT a.id, a.thread_id FROM thread_activities a"
                            " JOIN threads t ON t.id = a.thread_id WHERE t.channel_id = ?"
                            " AND a.kind = 'MESSAGE' AND a.native_id = ?",
                            (channel_id, target_native)).fetchone()
        if target is None:
            return None
        target_id, thread_id = target["id"], target["thread_id"]

    created = thread_id is None
    event = Arrived(inc.kind, identity is None and not call, bool(identity and identity["blocked"]))
    if thread_id is None:
        title = inc.title or (inc.content or "").strip()[:80] or "(no subject)"
        thread_id = _new_thread(db, channel_id, inc.native_id, title, status := transition(None, event), at)
        if status[1] == "IGNORED":  # Block, at Thread creation (I-4)
            _audit(db, thread_id, at, "blocked", None, "IGNORED")
    elif inc.kind == "MESSAGE" and identity is None and not call and _echo_of_sent(db, thread_id, inc):
        return None

    activity_id = db.execute(
        "INSERT INTO activities (thread_id, kind, occurred_at, identity_id, native_id,"
        " target_id, content, data) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (thread_id, inc.kind, clock.iso(at), identity_id, inc.native_id, target_id,
         None if inc.kind == "DELETE" else inc.content, json.dumps(inc.data))).lastrowid
    if inc.kind == "MESSAGE":
        _add_attachments(db, activity_id, _downloads(inc.data))
    if inc.kind == "MESSAGE" and inc.channel_kind == "mailbox":
        db.executemany("INSERT OR IGNORE INTO email_refs (activity_id, native_id) VALUES (?, ?)",
                       [(activity_id, n) for n in sorted(_email_ids(inc.thread_ref))])
        _reconcile_email_threads(db, True, channel_id)
    t = get_thread(db, thread_id)
    if not created or t.id != thread_id:
        if (not event.blocked or call) and (nxt := transition((t.status, t.status_detail), event)):
            _set_status(db, t.id, at, *nxt, cause="reply" if event.from_jeremy else "meeting" if meeting else "call" if call else inc.kind.lower())
    if inc.kind == "DELETE":  # FR-2a
        db.execute("UPDATE activities SET content = NULL WHERE id = ?"
                   " OR (kind = 'EDIT' AND target_id = ?)", (target_id, target_id))
    if inc.kind == "MESSAGE":  # Sequences: anything from the Person, or a connected call, pauses (FR-SEQ-12)
        from . import sequences
        person = identity["person_id"] if identity else None
        if identity is None and call and not meeting and inc.data["call"]["outcome"] == "connected" and inc.data.get("peer"):
            row = db.execute("SELECT person_id FROM identities WHERE kind = 'phone' AND value = ?", (inc.data["peer"],)).fetchone()
            person = row[0] if row else None
        if person is not None:
            sequences.pause_person(db, person, at, "call" if call and not meeting else "replied")
    return get_thread(db, thread_id)


# --- act, reply, wake ------------------------------------------------------

@writer("write")
def act(db, thread_id: int, action: Action | Reopen) -> Thread:
    with db:
        if not db.in_transaction:
            db.execute("BEGIN IMMEDIATE")
        t = get_thread(db, thread_id)
        thread_id = t.id
        now = clock.now()
        if isinstance(action, SetCase):
            case_id = action.case_id
            if action.new_title is not None:
                case_id = db.execute("INSERT INTO cases (title, created_at) VALUES (?, ?)",
                                     (action.new_title, clock.iso(now))).lastrowid
            db.execute("UPDATE threads SET case_id = ? WHERE id = ?", (case_id, thread_id))
            if case_id != t.case_id:
                _audit(db, thread_id, now, "action", case_id=case_id, data=_left(t.case_id))
        elif nxt := transition((t.status, t.status_detail), action, has_replied=_has_replied(db, thread_id)):
            _set_status(db, thread_id, now, *nxt, cause="reopen" if isinstance(action, Reopen) else "action")
        return get_thread(db, thread_id)


def reply(db, thread_id: int, text: str, sender: Sender,
          to: list[str] = (), cc: list[str] = ()) -> Thread:
    """Send first, with the reply's staged Attachments; store the outbound Activity and clear them only on
    success (decision #59). SendFailed (refused) or SendUnknown (it may have gone out) propagates, nothing is
    stored, and they stay staged. ValueError: an Attachment problem found before sending (FR-A15)."""
    staged_on = thread_id
    with _read_snapshot(db):
        t = get_thread(db, thread_id)
        thread_id = t.id
        records = db.execute(
            "SELECT native_id, data, content, occurred_at, identity_id FROM thread_activities"
            " WHERE thread_id = ? AND kind = 'MESSAGE' ORDER BY occurred_at, id", (thread_id,)).fetchall()
        history = [{"native_id": r["native_id"], "data": json.loads(r["data"]), "content": r["content"],
                    "at": r["occurred_at"]} for r in records]
        connection_id, platform = db.execute("SELECT c.id, c.platform FROM channels ch JOIN connections c"
                                             " ON c.id = ch.connection_id WHERE ch.id = ?", (t.channel_id,)).fetchone()
        if not platforms.PLATFORMS[platform].takes_replies:
            raise ValueError(NO_REPLY)
        files = staged_files(db, thread_id=staged_on)
        from . import sequences
        draft = db.execute("SELECT * FROM composition_drafts WHERE thread_id = ?", (staged_on,)).fetchone()
        draft = dict(draft) if draft else {}
        if problem := sequences.send_problem(db, draft, text):
            raise ValueError(problem)
        checkpoint = _send_checkpoint(db, connection_id)
        receipts = platforms.PLATFORMS[platform].receipts
        if receipts:
            for message, record in zip(history, records):
                message["native_id"] = _gmail_wire_id(
                    db, record["native_id"], connection_id, record["identity_id"])
                message["wire_id"] = message["native_id"]
            if history and history[-1]["native_id"] is None:
                raise ValueError(EMAIL_CONFIRMATION_PENDING)
    started = clock.now()
    changes, recorded_at = {}, []

    def record(native_id, now):
        if receipts:
            now = started
        recorded_at.append(now)
        t = get_thread(db, thread_id)  # reconciliation may have happened while the Sender was in flight
        changes.update(_send_changes(db, connection_id, checkpoint))
        data = {"to": list(to), "cc": list(cc)}
        if receipts:
            data["seen_through_activity_id"] = checkpoint[0]
        cur = db.execute(
            "INSERT OR IGNORE INTO activities (thread_id, kind, occurred_at, native_id, content, data)"
            " VALUES (?, 'MESSAGE', ?, ?, ?, ?)", (t.id, clock.iso(now), native_id, text, json.dumps(data)))
        db.execute("DELETE FROM composition_drafts WHERE thread_id = ?", (staged_on,))  # the reply Draft went out
        sequences.task_sent(db, draft, t.id, now)
        return cur.lastrowid if cur.rowcount else None, t.id

    def settle(tid):
        t = get_thread(db, tid)
        if receipts:
            _reconcile_email_threads(db, True, t.channel_id)
            t = get_thread(db, tid)
        if not _preserve_send_changes(db, t.id, changes):
            if nxt := transition((t.status, t.status_detail), Arrived(from_jeremy=True)):
                _set_status(db, t.id, recorded_at[0], *nxt, cause="reply")
        return t.id

    return _send(db, connection_id, platforms.PLATFORMS[platform].format, files,
                 lambda: sender.send(t, text, list(to), list(cc), history, files=files), record, settle=settle,
                 thread_id=staged_on)


def wake(db, now: datetime) -> int:
    """End timed snoozes that are due (FR-6d), and waits with no reply after WAIT (#116: back to Todo as
    Needs next response, to follow up); returns how many woke."""
    with db:
        if not db.in_transaction:
            db.execute("BEGIN IMMEDIATE")
        due = db.execute("SELECT id, status, status_detail, detail_before_snooze FROM threads"
                         " WHERE merged_into IS NULL AND"
                         " ((status_detail = 'WAITING_FOR_DURATION' AND snoozed_until <= ?)"
                         " OR (status_detail = 'WAITING_FOR_CUSTOMER' AND follow_up_at <= ?))",
                         (clock.iso(now), clock.iso(now))).fetchall()
        for r in due:
            _set_status(db, r["id"], now, *transition((r["status"], r["status_detail"]), Woke(),
                                                      has_replied=_has_replied(db, r["id"]),
                                                      before=r["detail_before_snooze"]), cause="woke")
    from . import sequences
    sequences.materialize(db, now)  # Sequences: each Enrollment's next Task, and email Tasks' Drafts (NFR-SEQ-1)
    return len(due)


# --- connection health inputs (NFR-2) ----------------------------------------

def set_connection_error(db, connection_id: int, error: str | None) -> None:
    with db:
        db.execute("UPDATE connections SET last_error = ? WHERE id = ?", (error, connection_id))


def set_auth(db, connection_id: int, ok: bool) -> None:
    with db:
        db.execute("UPDATE connections SET auth_ok = ? WHERE id = ?", (int(ok), connection_id))


def set_probe(db, connection_id: int, error: str | None) -> None:
    """Records a health check: its failure (None when it passed) and when it ran."""
    with db:
        db.execute("UPDATE connections SET probe_error = ?, probed_at = ? WHERE id = ?",
                   (error, clock.iso(clock.now()), connection_id))


# --- read side (records) ---------------------------------------------------

# (Person, Thread) for each Person who wrote in a Thread or was addressed in it (To/Cc, or a phone peer), as
# _participants counts them (FR-OA-4). The CROSS JOINs keep SQLite reading messages first: left to itself it
# scans every Identity for each message (5 s on the live data, against 6 ms).
PERSON_THREADS = (
    "SELECT i.person_id, a.thread_id FROM thread_activities a JOIN identities i ON i.id = a.identity_id"
    " WHERE i.person_id IS NOT NULL"
    + "".join(f" UNION SELECT i.person_id, a.thread_id FROM thread_activities a CROSS JOIN json_each(a.data, '$.{k}') j"
              " CROSS JOIN identities i ON i.kind = 'email' AND i.value = lower(j.value)"
              " WHERE a.kind = 'MESSAGE' AND i.person_id IS NOT NULL" for k in ("to", "cc"))
    + " UNION SELECT i.person_id, a.thread_id FROM thread_activities a CROSS JOIN identities i"
    " ON i.kind = 'phone' AND i.value = json_extract(a.data, '$.peer') WHERE a.kind = 'MESSAGE' AND i.person_id IS NOT NULL")


def person_thread_ids(db) -> dict[int, set[int]]:
    """Every Person's Threads, as PERSON_THREADS counts them: person id -> Thread ids."""
    out: dict[int, set[int]] = {}
    for pid, tid in db.execute(PERSON_THREADS):
        out.setdefault(pid, set()).add(tid)
    return out


def person_threads(db, person_id: int, every: dict | None = None) -> list[Thread]:
    """The Threads a Person wrote in or was addressed in; `every` is person_thread_ids, when already read."""
    with _read_snapshot(db):
        ids = (every if every is not None else person_thread_ids(db)).get(person_id, set())
        return [get_thread(db, i) for i in sorted(ids)]


def channel_threads(db, connection_id: int, native_id: str) -> list[Thread]:
    """Stored Threads of one Channel, newest first: a Slack Draft's captured history."""
    return [get_thread(db, r[0]) for r in db.execute(
        "SELECT t.id FROM threads t JOIN channels ch ON ch.id = t.channel_id"
        " WHERE ch.connection_id = ? AND ch.native_id = ? AND t.merged_into IS NULL ORDER BY t.created_at DESC",
        (connection_id, native_id))]


def recipient_threads(db, kind: str, value: str) -> list[Thread]:
    """Local captured history for one exact destination, not complete external contact history."""
    if type(kind) is not str or kind not in ("email", "phone") or type(value) is not str:
        raise ValueError("recipient history requires email or phone kind and a string value")
    if kind == "email":
        addresses = [a.lower() for _, a in getaddresses([value.strip()]) if a]
        if len(addresses) != 1 or "@" not in addresses[0]:
            return []
        value = addresses[0]
    elif platforms.E164.fullmatch(value) is None:
        return []
    formats = platforms.EMAIL if kind == "email" else tuple(
        n for n, p in platforms.PLATFORMS.items() if p.format == "sms")
    with _read_snapshot(db):
        # ponytail: scan captured envelopes; indexed recipient relation if history volume makes this slow.
        rows = db.execute(
            "SELECT a.thread_id, a.data, a.identity_id, i.kind AS identity_kind, i.value AS identity_value,"
            " ch.native_id AS channel_value, ch.kind AS channel_kind FROM thread_activities a"
            " JOIN threads t ON t.id = a.thread_id JOIN channels ch ON ch.id = t.channel_id"
            " JOIN connections c ON c.id = ch.connection_id LEFT JOIN identities i ON i.id = a.identity_id"
            f" WHERE a.kind = 'MESSAGE' AND c.platform IN ({','.join('?' * len(formats))})",
            formats)
        ids = set()
        for a in rows:
            candidates = [a["identity_value"]] if a["identity_kind"] == kind else []
            data = json.loads(a["data"])
            if isinstance(data, dict):
                keys = (("to", "cc") if a["identity_id"] is None else ("from",)) if kind == "email" else (
                    ("peer", "to") if a["identity_id"] is None else ("peer", "from"))
                for k in keys:
                    v = data.get(k)
                    if isinstance(v, str):
                        candidates.append(v)
                    elif isinstance(v, list):
                        candidates.extend(s for s in v if isinstance(s, str))
            if kind == "email":
                candidates = [addr.lower() for _, addr in getaddresses(candidates) if addr]
            elif a["channel_kind"] == "im":
                candidates.append(a["channel_value"])  # existing SMS replies retain the peer on the Channel
            if value in candidates:
                ids.add(a["thread_id"])
        return [get_thread(db, tid) for tid in sorted(ids)]


def person_organizations(db, person_id: int) -> list[str]:
    """A Person's Organizations (Membership): those of their company email domains, plus Jeremy's additions,
    less his removals."""
    return [r[0] for r in db.execute(
        "SELECT o.domain FROM organizations o WHERE COALESCE("
        " (SELECT member FROM memberships WHERE person_id = :p AND organization_id = o.id),"
        " EXISTS (SELECT 1 FROM identities i WHERE i.person_id = :p AND i.kind = 'email'"
        "  AND org_key(substr(i.value, instr(i.value, '@') + 1)) = o.domain))"
        " ORDER BY o.domain", {"p": person_id})]


def _members(db) -> dict[str, set[int]]:
    """Every Organization's Persons by Membership: domain -> Person ids."""
    members: dict[str, set[int]] = {}
    for domain, pid in db.execute(
            "SELECT DISTINCT org_key(substr(value, instr(value, '@') + 1)), person_id FROM identities"
            " WHERE kind = 'email' AND person_id IS NOT NULL"):
        members.setdefault(domain, set()).add(pid)
    for domain, pid, member in db.execute(
            "SELECT o.domain, m.person_id, m.member FROM memberships m JOIN organizations o ON o.id = m.organization_id"):
        (members.setdefault(domain, set()).add if member else members.setdefault(domain, set()).discard)(pid)
    return members


NO_REPLY = "A Meeting Thread takes no replies. Write to its people in a new Draft (n)."


class SnoozeNeedsDate(ValueError):
    """A Snooze always runs until a date (GLOSSARY Snoozed; FR-OA-10)."""

    def __init__(self, message: str = "a Snooze needs a date"):  # the remote copy rebuilds it from its message
        super().__init__(message)


class FreeMail(ValueError):
    """A free-mail domain (gmail.com, …) is never an Organization."""


def membership(db, person_id: int, domain: str) -> bool | None:
    """Jeremy's own Membership change for this Person and Organization: True added, False removed, None none."""
    r = db.execute("SELECT m.member FROM memberships m JOIN organizations o ON o.id = m.organization_id"
                   " WHERE m.person_id = ? AND o.domain = ?", (person_id, _org(domain))).fetchone()
    return None if r is None else bool(r[0])


@writer("create")
def set_membership(db, person_id: int, domain: str, member: bool | None) -> bool | None:
    """Add (True) or remove (False) a Person from an Organization, or drop Jeremy's change (None) so the email
    domain decides again. Creates the Organization for a new company domain. Returns the previous change, for
    undo."""
    domain = _org(domain.strip().lower().lstrip("@"))
    if domain in FREE_MAIL or "." not in domain:
        raise FreeMail(domain)
    with db:
        before = membership(db, person_id, domain)
        db.execute("INSERT OR IGNORE INTO organizations (domain, name, created_at) VALUES (?, ?, ?)",
                   (domain, domain, clock.iso(clock.now())))
        org = db.execute("SELECT id FROM organizations WHERE domain = ?", (domain,)).fetchone()[0]
        if member is None:
            db.execute("DELETE FROM memberships WHERE person_id = ? AND organization_id = ?", (person_id, org))
        else:
            db.execute("INSERT INTO memberships (person_id, organization_id, member, changed_at) VALUES (?, ?, ?, ?)"
                       " ON CONFLICT DO UPDATE SET member = excluded.member, changed_at = excluded.changed_at",
                       (person_id, org, int(member), clock.iso(clock.now())))
    return before


@writer("create")
def create_sole_proprietorship(db, person_id: int) -> int:
    """A Sole proprietorship for a Person acting on their own account (GLOSSARY; FR-SEQ-37): an Organization named
    for them, keyed `person-<id>.sole`, which no email domain produces, with their Membership. Returns its id; a
    second call returns the same one."""
    name = db.execute("SELECT display_name FROM persons WHERE id = ?", (person_id,)).fetchone()
    if name is None:
        raise KeyError(f"person {person_id}")
    domain = sole_key(person_id)
    with db:
        db.execute("INSERT OR IGNORE INTO organizations (domain, name, created_at) VALUES (?, ?, ?)",
                   (domain, name[0], clock.iso(clock.now())))
        org = db.execute("SELECT id FROM organizations WHERE domain = ?", (domain,)).fetchone()[0]
        db.execute("INSERT INTO memberships (person_id, organization_id, member, changed_at) VALUES (?, ?, 1, ?)"
                   " ON CONFLICT DO UPDATE SET member = 1, changed_at = excluded.changed_at",
                   (person_id, org, clock.iso(clock.now())))
    return org


def sole_key(person_id: int) -> str:
    return f"person-{person_id}.sole"


@writer("write")
def set_case_description(db, case_id: int, description: str) -> None:
    with db:
        old = db.execute("SELECT description FROM cases WHERE id = ?", (case_id,)).fetchone()[0]
        if description != old:
            db.execute("UPDATE cases SET description = ? WHERE id = ?", (description, case_id))
            db.execute("INSERT INTO case_log (case_id, occurred_at, kind, old) VALUES (?, ?, 'DESCRIPTION', ?)",
                       (case_id, clock.iso(clock.now()), old))


def case_feed(db, case_id: int) -> list[dict]:
    """A Case's Activity, oldest first: created, each Thread added or removed (with its cause from the audit
    log), and each description edit."""
    with _read_snapshot(db):
        created = db.execute("SELECT created_at FROM cases WHERE id = ?", (case_id,)).fetchone()[0]
        feed = [{"at": created, "kind": "created"}]
        feed += [{"at": r["occurred_at"], "kind": "added" if r["case_id"] == case_id else "removed",
                  "thread_id": r["thread_id"], "title": r["title"], "cause": r["cause"]} for r in db.execute(
            "SELECT l.occurred_at, l.case_id, l.cause, COALESCE(t.merged_into, t.id) AS thread_id, t.title"
            " FROM audit_log l JOIN threads t ON t.id = l.thread_id WHERE l.kind = 'CASE'"
            " AND (l.case_id = ? OR json_extract(l.data, '$.from') = ?)", (case_id, case_id))]
        feed += [{"at": r[0], "kind": "description"} for r in db.execute(
            "SELECT occurred_at FROM case_log WHERE case_id = ? AND kind = 'DESCRIPTION'", (case_id,))]
    return sorted(feed, key=lambda e: datetime.fromisoformat(e["at"]))


def case_threads(db, case_id: int) -> list[Thread]:
    with _read_snapshot(db):
        return [get_thread(db, r[0]) for r in db.execute(
            "SELECT id FROM threads WHERE merged_into IS NULL AND case_id = ? ORDER BY id", (case_id,))]


# --- console read models (docs/design/console.md) ---------------------------

VIEWS = {
    "Todo": ("CREATED", "NEW_REPLY", "IN_PROGRESS"),
    "Snoozed": ("WAITING_FOR_CUSTOMER", "WAITING_FOR_DURATION", "WAITING_INDEFINITELY"),
    "Done": ("DONE_MANUALLY_SET",),
    "Ignored": ("IGNORED",),
}
VIEWS["All"] = tuple(d for details in VIEWS.values() for d in details)


def view_counts(db) -> dict[str, int]:
    rows = dict(db.execute("SELECT status_detail, count(*) FROM threads WHERE merged_into IS NULL"
                           " GROUP BY status_detail").fetchall())
    return {name: sum(rows.get(d, 0) for d in details) for name, details in VIEWS.items()}


DETAIL_LABEL = {  # Status detail -> how Jeremy reads it (GLOSSARY)
    "CREATED": "Needs first response",
    "NEW_REPLY": "Needs next response",
    "IN_PROGRESS": "Investigating",
    "WAITING_FOR_CUSTOMER": "Waiting for them",
    "WAITING_FOR_DURATION": "Paused for later",
    "WAITING_INDEFINITELY": "Paused for later",
    "DONE_MANUALLY_SET": "Done",
    "IGNORED": "Ignored",
}
CASE_VIEWS = ("Todo", "Snoozed", "Done", "Ignored")  # a Case's Status, earliest first (#120)


def rollup(details, order=CASE_VIEWS, empty="Todo") -> str:
    """The earliest Status view in `order` holding any of these Status details, `empty` if none does. A Case rolls
    up its Threads (#120); an Organization its People's Threads, Ignored ones left out (FR-OA-4)."""
    return next((v for v in order if any(d in VIEWS[v] for d in details)), empty)


@writer("create")
def create_case(db, title: str) -> int:
    """A Case with no Threads yet (#120)."""
    with db:
        return db.execute("INSERT INTO cases (title, created_at) VALUES (?, ?)", (title, clock.iso(clock.now()))).lastrowid


def case_queue(db, view: str | None = None) -> list[dict]:
    """Cases with their rolled-up Status and Thread counts per view; only `view`'s Cases when given.
    Todo: longest-waiting first (when its oldest Todo Thread got there); other views: most recently changed
    first; no view: by title."""
    found = {}
    for r in db.execute("SELECT c.id, c.title, c.created_at, t.status_detail, t.status_changed_at FROM cases c"
                        " LEFT JOIN threads t ON t.case_id = c.id AND t.merged_into IS NULL"):
        c = found.setdefault(r["id"], {"id": r["id"], "title": r["title"], "created_at": r["created_at"], "threads": []})
        if r["status_detail"]:
            c["threads"].append((r["status_detail"], r["status_changed_at"]))
    out = []
    for c in found.values():
        details = [d for d, _ in c["threads"]]
        status = rollup(details)
        times = [at for d, at in c["threads"] if d in VIEWS[status]] or [c["created_at"]]
        out.append({"id": c["id"], "title": c["title"], "status": status,
                    "counts": {v: sum(d in VIEWS[v] for d in details) for v in CASE_VIEWS},
                    "since": min(times) if status == "Todo" else max(times)})
    if view is None:
        return sorted(out, key=lambda c: c["title"])
    return sorted((c for c in out if c["status"] == view), key=lambda c: c["since"], reverse=view != "Todo")


def _sender(db, identity_id):
    """(label, identity row) for an identity: Person name, else the address."""
    i = db.execute("SELECT i.*, p.display_name FROM identities i LEFT JOIN persons p ON p.id = i.person_id"
                   " WHERE i.id = ?", (identity_id,)).fetchone()
    if i is None:
        return "You", None
    name = i["display_name"]
    return (name if name and name != i["value"] else i["value"]), i


def _where(channel, identity) -> str:
    kind = channel["kind"]
    if kind == "meeting":
        return "Meeting"
    dm = platforms.PLATFORMS.get(dict(channel).get("platform"))
    if kind == "im" and dm and dm.dm_where:  # the queue already shows the sender's number
        return dm.dm_where if identity else f"{dm.dm_where} · {channel['name'] or channel['native_id']}"
    if kind == "im":
        return f"DM · {channel['name']}" if channel["name"] else "Slack DM"
    if kind == "mpim":
        return channel["name"] or "Slack group"
    if kind in ("channel", "private"):
        return "#" + (channel["name"] or channel["native_id"])
    domain = identity["value"].rsplit("@", 1)[-1] if identity and identity["kind"] == "email" else ""
    return _org(domain) if domain and domain not in FREE_MAIL else "email"


def today_draft(db, draft: dict) -> bool:
    """Whether Today lists this Draft itself: not a Task's (it shows through its Task once due), and not a reply
    on a Todo Thread (it shows through that Thread)."""
    if draft["task_id"] is not None:
        return False
    return draft["thread_id"] is None or get_thread(db, draft["thread_id"]).status_detail not in VIEWS["Todo"]


def today(db, now: datetime | None = None) -> dict:
    """Today (GLOSSARY): Todo Threads, Due Tasks, the Drafts not shown through them, each once, Paused
    Enrollments (FR-SEQ-39), and Unattended Organizations; and, for an empty Today, when the next Snoozed Thread wakes and the next Task comes due (None
    when nothing is coming)."""
    from . import pipeline, sequences
    now = now or clock.now()
    snoozed = ",".join("?" * len(VIEWS["Snoozed"]))
    wake = db.execute(
        f"SELECT min(at) FROM (SELECT snoozed_until AS at FROM threads WHERE merged_into IS NULL"
        f" AND status_detail IN ({snoozed}) UNION ALL SELECT follow_up_at FROM threads WHERE merged_into IS NULL"
        f" AND status_detail IN ({snoozed})) WHERE at IS NOT NULL", VIEWS["Snoozed"] * 2).fetchone()[0]
    day = now.astimezone(sequences.LOCAL).date()
    later = [r[0] for r in db.execute(
        "SELECT t.due_at FROM tasks t JOIN enrollments e ON e.id = t.enrollment_id"
        " WHERE t.state = 'DUE' AND e.state IN ('ACTIVE', 'FINISHING') ORDER BY t.due_at")
        if datetime.fromisoformat(r[0]).astimezone(sequences.LOCAL).date() > day]
    return {"threads": queue(db, "Todo"), "tasks": sequences.tasks_due(db, now),
            "drafts": [d for d in list_drafts(db) if today_draft(db, d)], "paused": sequences.paused(db),
            "unattended": pipeline.unattended(db),
            "next_wake": wake, "next_task": later[0] if later else None}


def queue(db, view: str) -> list[dict]:
    """Rows for a Status view. Todo: longest-waiting first; others: most recent first."""
    details = VIEWS[view]
    rows = db.execute(
        "SELECT t.id, t.title, t.status, t.status_detail, t.case_id, t.follow_up_at, t.snoozed_until,"
        " ch.kind, ch.name, ch.native_id, c.platform,"
        " (SELECT max(occurred_at) FROM thread_activities a WHERE a.thread_id = t.id) AS last_at,"
        " (SELECT max(occurred_at) FROM thread_activities a WHERE a.thread_id = t.id AND a.identity_id IS NOT NULL) AS last_in,"
        " (SELECT identity_id FROM thread_activities a WHERE a.thread_id = t.id AND a.identity_id IS NOT NULL"
        "   ORDER BY occurred_at, id LIMIT 1) AS first_identity,"
        " EXISTS (SELECT 1 FROM composition_drafts d JOIN threads m ON m.id = d.thread_id"
        "   WHERE m.id = t.id OR m.merged_into = t.id) AS drafted"
        f" FROM threads t JOIN channels ch ON ch.id = t.channel_id JOIN connections c ON c.id = ch.connection_id"
        f" WHERE t.merged_into IS NULL AND t.status_detail IN ({','.join('?' * len(details))})", details).fetchall()
    out = []
    for r in rows:
        label, identity = _sender(db, r["first_identity"]) if r["first_identity"] else ("You", None)
        out.append({"id": r["id"], "title": r["title"], "status": r["status"], "detail": r["status_detail"],
                    "case_id": r["case_id"], "until": r["follow_up_at"] or r["snoozed_until"], "who": label, "where": _where(r, identity), "last_at": r["last_at"],
                    "waiting_since": r["last_in"] or r["last_at"], "drafted": bool(r["drafted"])})
    if view == "Todo":
        return sorted(out, key=lambda x: x["waiting_since"])
    return sorted(out, key=lambda x: x["last_at"], reverse=True)


def _shaped(db, m: dict, platform, channel, lan) -> dict:
    """A message as the console shows it, whatever its Platform: raw source (email), recipients, a link that
    opens it in the service, and attachments; `data` stays for email reply-all.

    Each attachment: name, type, size, inline and content_id, state (None when the Platform only lists it), sha
    and path (the stored copy, else None), and url: the LAN file link once stored and `lan` = (host, port) is
    given (FR-A5), else the Platform link."""
    d = m["data"]
    m["link"] = platform.link(m, channel)
    urls = [u for _, u in d.get("media") or []]  # Quo media: each photo has its own signed URL
    link = lambda k: urls[k] if k < len(urls) else m["link"]  # noqa: E731
    rows = attachments(db, m["id"])
    m["attachments"] = [
        {"name": r["name"], "type": r["type"], "size": r["size"], "inline": bool(r["inline"]), "state": r["state"],
         "content_id": r["content_id"], "sha": r["sha"],
         "path": str(files.attachment_path(db, r["sha"])) if r["sha"] else None,
         "url": files.file_url(db, r["sha"], r["name"], *lan) if r["sha"] and lan else link(r["position"])}
        for r in rows] or [
        {"name": a[0], "type": a[1], "size": a[2], "inline": False, "state": None, "sha": None, "path": None,
         "url": link(k)}
        for k, a in enumerate(d.get("attachments") or [])]
    m["raw"], m["to"], m["cc"], m["from"] = d.get("raw"), d.get("to") or [], d.get("cc") or [], d.get("from")
    return m


def thread_view(db, thread_id: int, files: tuple[str, int] | None = None) -> dict:
    """Timeline (latest edit wins; deleted messages have no content) and the sender's records. `files` is the
    file server's (host, port), for stored Attachments' links."""
    with _read_snapshot(db):
        t = get_thread(db, thread_id)
        thread_id = t.id
        ch = db.execute("SELECT ch.*, c.platform, c.external_id FROM channels ch"
                        " JOIN connections c ON c.id = ch.connection_id WHERE ch.id = ?", (t.channel_id,)).fetchone()
        acts = db.execute("SELECT * FROM thread_activities WHERE thread_id = ? ORDER BY occurred_at, id", (thread_id,)).fetchall()
        msgs, by_id = [], {}
        for a in acts:
            if a["kind"] == "MESSAGE":
                who, _ = _sender(db, a["identity_id"]) if a["identity_id"] else ("You", None)
                m = {"id": a["id"], "who": who, "at": a["occurred_at"], "content": a["content"], "edited": False,
                     "deleted": False, "native_id": a["native_id"], "data": json.loads(a["data"])}
                m["revision"] = a["id"]
                if platforms.PLATFORMS[ch["platform"]].receipts:
                    m["wire_id"] = _gmail_wire_id(db, a["native_id"], ch["connection_id"], a["identity_id"])
                by_id[a["id"]] = m
                msgs.append(m)
            elif a["target_id"] in by_id:
                m = by_id[a["target_id"]]
                m["revision"] = a["id"]
                if a["kind"] == "EDIT" and "call" in m["data"]:  # a Call transcript goes under the call's summary
                    m["content"] = m["content"].partition("\n")[0] + "\n\n" + (a["content"] or "")
                elif a["kind"] == "EDIT":
                    m["edited"], m["content"] = True, a["content"]
                else:
                    m["deleted"], m["content"] = True, None
        first = next((a["identity_id"] for a in acts if a["identity_id"]), None)
        person = None
        if first:
            pid = db.execute("SELECT person_id FROM identities WHERE id = ?", (first,)).fetchone()[0]
            person = person_view(db, pid) if pid else None
        people, others, orgs = _participants(db, acts)
        case = db.execute("SELECT title FROM cases WHERE id = ?", (t.case_id,)).fetchone() if t.case_id else None
        platform = platforms.PLATFORMS[ch["platform"]]
        msgs = [_shaped(db, m, platform, ch, files) for m in msgs]
        audit = [dict(r) for r in db.execute(
            "SELECT l.*, c.title AS case_title FROM audit_log l JOIN threads t ON t.id = l.thread_id"
            " LEFT JOIN cases c ON c.id = l.case_id WHERE COALESCE(t.merged_into, t.id) = ?"
            " ORDER BY l.occurred_at, l.id", (thread_id,))]
        return {"thread": t, "channel": dict(ch), "messages": msgs, "audit": audit, "person": person, "people": people,
                "others": others, "organizations": orgs, "members": snapshot(db, thread_id)["_members"],
                "link": next((m["link"] for m in reversed(msgs) if m["link"]), None),
                "case": case["title"] if case else None, "where": _where(ch, None) if ch["kind"] != "mailbox" else "email"}


def _participants(db, acts) -> tuple[list[dict], list[str], list[str]]:
    """Everyone involved in a Thread, in order of appearance (Jeremy, 2026-10-12): Persons who wrote or were
    addressed (To/Cc, or a phone peer), addresses that belong to no Person yet, and all their Organizations. Jeremy's own
    addresses, and his own company's domain, are left out."""
    mine = {r[0].lower() for r in db.execute("SELECT self_id FROM connections")}
    pids, others = [], []
    for a in acts:
        if a["kind"] != "MESSAGE":
            continue
        found = []
        if a["identity_id"]:
            found.append(db.execute("SELECT person_id FROM identities WHERE id = ?", (a["identity_id"],)).fetchone()[0])
        data = json.loads(a["data"])
        peer = data.get("peer")  # a phone Thread's other party, set even when Jeremy sent every message
        addressed = [("email", a.lower()) for a in [*(data.get("to") or []), *(data.get("cc") or [])]]
        for kind, addr in addressed + ([("phone", peer)] if isinstance(peer, str) else []):
            if addr in mine:
                continue
            r = db.execute("SELECT person_id FROM identities WHERE kind = ? AND value = ?", (kind, addr)).fetchone()
            if r and r[0]:
                found.append(r[0])
            elif addr not in others:
                others.append(addr)
        pids += [p for p in found if p and p not in pids]
    people = [person_view(db, p) for p in pids]
    known = {d for p in people for d in p["organizations"]}
    extra = {_org(d) for a in others if "@" in a and (d := a.rsplit("@", 1)[1]) not in FREE_MAIL}
    own = {_org(m.rsplit("@", 1)[1]) for m in mine if "@" in m}  # Agent Maple is not a counterparty
    return people, others, sorted((known | extra) - own)


def person_view(db, person_id: int, every: dict | None = None) -> dict:
    with _read_snapshot(db):
        p = db.execute("SELECT * FROM persons WHERE id = ?", (person_id,)).fetchone()
        identities = [dict(r) for r in db.execute(
            "SELECT id, kind, value, blocked FROM identities WHERE person_id = ? ORDER BY kind, value", (person_id,))]
        threads = person_threads(db, person_id, every)
        cases = sorted({r[0] for r in db.execute(
            "SELECT c.title FROM cases c JOIN threads t ON t.case_id = c.id WHERE t.id IN"
            f" ({','.join(str(t.id) for t in threads) or 'NULL'})")})
        return {"id": person_id, "name": p["display_name"], "identities": identities,
                "organizations": person_organizations(db, person_id), "threads": threads, "cases": cases}


def cases(db) -> list[dict]:
    return [dict(r) for r in db.execute(
        "SELECT c.id, c.title, count(t.id) AS threads FROM cases c LEFT JOIN threads t"
        " ON t.case_id = c.id AND t.merged_into IS NULL"
        " GROUP BY c.id ORDER BY c.title")]


def connections(db) -> list[dict]:
    return [dict(r) for r in db.execute("SELECT * FROM connections ORDER BY id")]


def channel_rules(db, connection_id: int) -> list[dict]:
    return [dict(r) for r in db.execute(
        "SELECT id, kind, value FROM channel_rules WHERE connection_id = ? ORDER BY kind, value", (connection_id,))]


@writer("create")
def add_channel_rule(db, connection_id: int, kind: str, value: str) -> None:
    with db:
        db.execute("INSERT OR IGNORE INTO channel_rules (connection_id, kind, value) VALUES (?, ?, ?)",
                   (connection_id, kind, value))


@writer("write")
def delete_channel_rule(db, rule_id: int) -> None:
    with db:
        db.execute("DELETE FROM channel_rules WHERE id = ?", (rule_id,))


def people(db) -> list[dict]:
    """Persons with at least one Identity: name, first address, Organizations, open (Todo) Thread count, and
    how many of their Identities are blocked."""
    with _read_snapshot(db):
        out, every = [], person_thread_ids(db)
        todo = {r[0] for r in db.execute("SELECT id FROM threads WHERE status = 'TODO'")}
        known, orgs = {r[0] for r in db.execute("SELECT domain FROM organizations")}, {}
        for domain, pids in _members(db).items():  # person_organizations for everyone at once
            for pid in pids if domain in known else ():
                orgs.setdefault(pid, []).append(domain)
        for p in db.execute("SELECT p.id, p.display_name FROM persons p WHERE EXISTS"
                            " (SELECT 1 FROM identities i WHERE i.person_id = p.id) ORDER BY lower(p.display_name)"):
            ids = db.execute("SELECT value, blocked FROM identities WHERE person_id = ? ORDER BY kind, value",
                             (p["id"],)).fetchall()
            threads = every.get(p["id"], set())
            out.append({"id": p["id"], "name": p["display_name"], "address": ids[0]["value"],
                        "organizations": sorted(orgs.get(p["id"], [])),
                        "todo": len(threads & todo), "threads": len(threads),
                        "identities": len(ids), "blocked": sum(r["blocked"] for r in ids)})
        return out


def organizations(db) -> list[dict]:
    """Each Organization's Person, Thread, and open (Todo) Thread counts, as organization_view counts them, in
    three queries rather than a person_view per member (the Organizations list and rail)."""
    with _read_snapshot(db):
        members, threads = _members(db), person_thread_ids(db)
        todo = {r[0] for r in db.execute("SELECT id FROM threads WHERE status = 'TODO'")}
        out = []
        for o in db.execute("SELECT id, domain FROM organizations ORDER BY domain"):
            pids = members.get(o["domain"], set())
            tids = set().union(*(threads.get(p, set()) for p in pids))
            out.append({"id": o["id"], "domain": o["domain"], "people": len(pids), "todo": len(tids & todo),
                        "threads": len(tids)})
        return out


def find_people(db, text: str, limit: int = 20) -> list[tuple[int, str]]:
    """Persons whose Identity is exactly `text` (an address, number, or Slack ID), else whose name contains it."""
    text = text.strip()
    exact = db.execute("SELECT DISTINCT p.id, p.display_name FROM persons p JOIN identities i ON i.person_id = p.id"
                       " WHERE lower(i.value) = lower(?)", (text,)).fetchall()
    if exact or not text:
        return [tuple(r) for r in exact]
    return [tuple(r) for r in db.execute(
        "SELECT p.id, p.display_name FROM persons p WHERE instr(lower(p.display_name), lower(?)) > 0"
        " AND EXISTS (SELECT 1 FROM identities i WHERE i.person_id = p.id) ORDER BY p.display_name LIMIT ?",
        (text, limit))]


def people_count(db) -> int:
    """len(people(db)), without building each Person (the rail)."""
    return db.execute("SELECT COUNT(*) FROM persons p WHERE EXISTS"
                      " (SELECT 1 FROM identities i WHERE i.person_id = p.id)").fetchone()[0]


def organization_view(db, domain: str) -> dict:
    """An Organization's Persons (by Membership) and their Threads."""
    with _read_snapshot(db):
        pids = [r[0] for r in db.execute(
            "SELECT DISTINCT person_id FROM identities WHERE kind = 'email' AND person_id IS NOT NULL"
            " AND org_key(substr(value, instr(value, '@') + 1)) = ?", (domain,))]
        for pid, member in db.execute("SELECT m.person_id, m.member FROM memberships m JOIN organizations o"
                                      " ON o.id = m.organization_id WHERE o.domain = ? ORDER BY m.changed_at",
                                      (domain,)):
            pids = [p for p in pids if p != pid] + ([pid] if member else [])
        every = person_thread_ids(db)
        persons = [person_view(db, pid, every) for pid in pids]
        threads = {t.id: t for p in persons for t in p["threads"]}
        return {"domain": domain, "people": persons, "threads": sorted(threads.values(), key=lambda t: t.id)}


# --- undo (console: u) -------------------------------------------------------

def snapshot(db, thread_id: int) -> dict:
    """Current canonical triage state and Thread membership, so an action can be safely undone."""
    r = db.execute("SELECT id, status, status_detail, status_changed_at, snoozed_until, detail_before_snooze,"
                   " case_id, follow_up_at, (SELECT json_group_array(id) FROM"
                   " (SELECT id FROM threads WHERE id = t.id OR merged_into = t.id ORDER BY id)) AS _members"
                   " FROM threads t WHERE id ="
                   " (SELECT COALESCE(merged_into, id) FROM threads WHERE id = ?)", (thread_id,)).fetchone()
    if r is None:
        raise KeyError(f"thread {thread_id}")
    snap = dict(r)
    snap["_members"] = json.loads(snap["_members"])
    return snap


@writer("write")
def restore(db, snap: dict) -> Thread:
    with db:
        if not db.in_transaction:
            db.execute("BEGIN IMMEDIATE")
        current = snapshot(db, snap["id"])
        thread_id = current["id"]
        if thread_id != snap["id"] or current["_members"] != snap.get("_members", [snap["id"]]):
            raise StaleThreadSnapshot("Thread merged since this undo snapshot; refresh before changing it.")
        db.execute("UPDATE threads SET status = ?, status_detail = ?, status_changed_at = ?, snoozed_until = ?,"
                   " detail_before_snooze = ?, case_id = ?, follow_up_at = ? WHERE id = ?",
                   (snap["status"], snap["status_detail"], snap["status_changed_at"], snap["snoozed_until"],
                    snap["detail_before_snooze"], snap["case_id"], snap["follow_up_at"], thread_id))
        now = clock.now()
        if snap["status_detail"] != current["status_detail"]:
            _audit(db, thread_id, now, "undo", current["status_detail"], snap["status_detail"],
                   data={"until": snap["snoozed_until"]} if snap["snoozed_until"] else {})
        if snap["case_id"] != current["case_id"]:
            _audit(db, thread_id, now, "undo", case_id=snap["case_id"], data=_left(current["case_id"]))
    return get_thread(db, thread_id)
