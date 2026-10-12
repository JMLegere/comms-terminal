# Universal Inbox

A single-user console that tracks who owes whom what across Jeremy's professional messaging Platforms, modelled on Plain's thread queue.

## Communication

**Thread**:
The unit of work, as in Plain: an email thread; a Slack thread in a channel (a top-level message and its replies); or, in a Slack DM or group DM, a time-based group of messages (same sender within 2 hours, different senders within 1 hour).
_Avoid_: Conversation, Ticket, Loop

**Thread alias**:
A retired Thread ID that resolves to the surviving Thread after exact-reference email reconciliation. Old links still reach the same work and its combined history.
_Avoid_: Deleted thread, Duplicate activity

**Activity**:
A timestamped, never-rewritten record of a message received or sent in a Thread, or of an edit or deletion of one.
_Avoid_: Event, Interaction, Message (when meaning the record)

**Attachment**:
A file sent with a message. It is seen in the console and fetched from the Platform.
_Avoid_: File (alone), Part

**Trimmed content**:
The parts of a message body that repeat or decorate rather than say something new: quoted earlier messages, signatures, and boilerplate footers. They are folded behind `•••`, as in Gmail.
_Avoid_: Quote (when meaning all of it), Fluff

**Case**:
An optional grouping of Threads that serve one need across Platforms; it has no Status of its own.
_Avoid_: Commitment, Issue, Project

## Triage

**Status**:
Where a Thread stands, as in Plain: Todo, Snoozed, or Done. Every Thread also has one Status detail.
_Avoid_: State, Stage

**Status detail**:
The reason within a Status, as in Plain: Needs first response, Needs next response, or Investigating (Todo); Waiting for them or Paused for later (Snoozed); Done or Ignored (Done).
_Avoid_: Substatus, Reason

**Ignored**:
A Done Status for noise that does not move when new messages arrive, until Jeremy un-ignores it.
_Avoid_: Dismissed, Archived

**Blocked**:
A flag on one Identity that makes new Threads from it start as Ignored.
_Avoid_: Spam, Blacklisted, Screened out, Ignored (for senders)

**Triage**:
Working the Todo queue.
_Avoid_: Inbox zero, Processing

## People

**Person**:
An individual Jeremy exchanges messages with: a set of Identities: email addresses, Slack users, and phone numbers.
_Avoid_: Contact, Customer, User


**Identity**:
One address a Person sends from: an email address, a Slack user, or a phone number.
_Avoid_: Address (when it may be a Slack user), Account, Handle

**Organization**:
A company or group that Persons belong to.
_Avoid_: Account, Company, Tenant

## Sources

**Platform**:
The service a Connection runs through: Gmail, Fastmail, Slack, or Quo; later WhatsApp, LinkedIn, or tool comments. Each Platform has one adapter.
_Avoid_: Medium, Channel (when meaning the service), Email (Gmail and Fastmail are separate Platforms)

**Message format**:
The shape of a message that Platforms can share: email (Gmail, Fastmail), Slack message, or SMS (Quo). Email addresses, mailbox threading by Message-ID and References, and reply-all belong to the email format, so one address is one Identity on every email Platform.
_Avoid_: Platform (when meaning the format), Protocol

**Channel**:
A container of Threads on a Platform, such as a Slack channel or DM.
_Avoid_: Space, Room, Inbox

**Connection**:
One connected account on a Platform, such as one forwarded Gmail address, one Slack workspace, or one Quo number.
_Avoid_: Source, Account, Integration
