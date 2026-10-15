# Universal Inbox

A single-user console that tracks who owes whom what across Jeremy's professional messaging Platforms, modelled on Plain's thread queue.

## Communication

**Thread**:
The unit of work, as in Plain: an email thread; in a Slack channel, the top-level messages until the channel has been quiet for 24 hours, or the replies to one top-level message; in a Slack DM or group DM, the messages until it has been quiet for 24 hours; or, in an SMS conversation, the messages until it has been quiet for 7 days. Messages Jeremy sends, from the console or elsewhere, group by the same rules.
_Avoid_: Conversation, Ticket, Loop

**Thread alias**:
A retired Thread ID that resolves to the surviving Thread after exact-reference email reconciliation. Old links still reach the same work and its combined history.
_Avoid_: Deleted thread, Duplicate activity

**Draft**:
An unsent email, SMS, or Slack composition with its own Connection, recipients, subject and body; it sends as the Connection's own address, number, or Slack user. A Slack Draft has no subject and goes to one existing Slack Channel (a channel, DM, or group DM Jeremy is in) instead of recipients. It is neither a Thread nor an Activity. Sending it removes it and records the message: an email Draft starts a new Thread; an SMS or Slack Draft joins its Channel's Thread by the ordinary Thread rules, starting a new one only when the Channel has been quiet long enough.

**Send outcome unknown**:
A Draft whose send was started but never confirmed or refused, so the message may or may not have gone out. It stays locked until Jeremy marks it sent, which removes it without making a Thread, or explicitly sends it again.
_Avoid_: Failed send, Pending

**Draft revision**:
The monotonically increasing version of a saved Draft, checked before a later save may replace it.

**Captured history**:
Stored Threads matching an exact email or phone destination, whether or not it is linked to a Person; it makes no claim about external messages.

**Activity**:
A timestamped, never-rewritten record of a message received or sent in a Thread, or of an edit or deletion of one.
_Avoid_: Event, Interaction, Message (when meaning the record)

**Attachment**:
A file that arrives or is sent with a message, or is added to a Draft to go with it, listed beside the message rather than placed in its body. A Slack file or an SMS photo is an Attachment.
_Avoid_: File (alone), Part, Upload

**Inline image**:
An image placed inside an email body where it appears, rather than listed beside the message. It is part of the body, not an Attachment.
_Avoid_: Embedded image, Attachment (for an image in the body)

**Trimmed content**:
The parts of a message body that repeat or decorate rather than say something new: quoted earlier messages, signatures, and boilerplate footers. They are folded behind `•••`, as in Gmail.
_Avoid_: Quote (when meaning all of it), Fluff

**Case**:
One goal with an end that Jeremy drives through one or more Threads across Platforms, such as getting someone to respond, fixing a customer's problem, or winning a deal. Its Status is the earliest of its Threads' Statuses, in the order Todo, Snoozed, Done, Ignored (Ignored counting after Done); Jeremy never sets it directly. A Case with no Threads yet is Todo. It may match one CRM record, such as a deal in Attio, but holds none of its data; funnel stage stays in the CRM.
_Avoid_: Commitment, Issue, Project, Ticket

## Triage

**Status**:
Where a Thread stands, as in Plain: Todo, Snoozed, or Done. Every Thread also has one Status detail. A Case's Status is the roll-up of its Threads' Statuses.
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

**Context source**:
An outside tool that Universal Inbox reads to show context beside Threads, Persons, Organizations, and Cases, such as Tana, Attio, or Linear. It holds no Threads and is not a Platform.
_Avoid_: Integration, Connection, Platform (for Tana, Attio, or Linear)

**Meeting**:
A calendar event read from a Context source, with its summary and linked documents. It is context for the Persons and Organizations among its attendees. It is never a Thread or an Activity.
_Avoid_: Call, Event (alone), Thread (for a meeting)
