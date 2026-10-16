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
An unsent email, SMS, or Slack composition with its own Connection, recipients, subject and body; it sends as the Connection's own address, number, or Slack user. A Slack Draft has no subject and goes to one existing Slack Channel (a channel, DM, or group DM Jeremy is in) instead of recipients. A reply Draft belongs to one Thread, at most one per Thread, and sends as a reply in it. A Draft is neither a Thread nor an Activity. Sending it removes it and records the message: an email Draft starts a new Thread unless it is a reply Draft; an SMS or Slack Draft joins its Channel's Thread by the ordinary Thread rules, starting a new one only when the Channel has been quiet long enough.

**Send outcome unknown**:
A send that was started but never confirmed or refused, so the message may or may not have gone out. A Draft stays locked until Jeremy marks it sent, which removes it without making a Thread, or explicitly sends it again. A reply has no lock: nothing is recorded, its text and staged files stay, and Jeremy checks the Thread before sending again.
_Avoid_: Failed send, Pending

**Draft revision**:
The monotonically increasing version of a saved Draft, checked before a later save may replace it.

**Captured history**:
Stored Threads matching an exact email or phone destination, whether or not it is linked to a Person; it makes no claim about external messages.

**Activity**:
A timestamped, never-rewritten record of a message received or sent in a Thread, a Phone call, or an edit or deletion of one.
_Avoid_: Event, Interaction, Message (when meaning the record)

**Audit entry**:
A never-rewritten record that a Thread's Status or Case changed, when, and why: Jeremy's action, his own message, the other side's Activity, a snooze ending, an import, a Block, or an undo. It shows in the Thread's timeline between messages. It is not an Activity and never moves Status.
_Avoid_: Activity, Event, History (when meaning one entry)

**Phone call**:
An Activity recording one call on the Work number, incoming or outgoing, from any Quo app, with its direction, Call outcome, and length. It joins its number's Thread by the same 7-day rule as Texts, and every Phone call puts its Thread in Todo.
_Avoid_: Call log, Call event, Dial

**Call outcome**:
Connected, missed, voicemail, or no answer. Missed and voicemail are incoming only; no answer is outgoing only. Forwarded and AI-handled calls count as missed.

**Call transcript**:
A later record appended to a Phone call, holding Quo's transcript of the conversation or the voicemail. It never changes the Thread's Status.

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
_Avoid_: Contact, Customer, User, Lead, Prospect


**Identity**:
One address a Person sends from: an email address, a Slack user, or a phone number.
_Avoid_: Address (when it may be a Slack user), Account, Handle

**Organization**:
A company or group that Persons belong to.
_Avoid_: Account, Company, Tenant

## Outreach

**Sequence**:
An ordered set of Steps, each due a number of business days after the previous one is done, that Jeremy runs for one Person at a time.
_Avoid_: Campaign, Cadence, Workflow

**Step**:
One touch in a Sequence, of one kind (email, call, LinkedIn, or letter), holding everything needed to do it. A Step is the template; each Enrollment works it as a Task.
_Avoid_: Touch, Action, Task (for one Person's copy)

**Enrollment**:
One Person's run through one Sequence. It is Active, Paused, or Finished. Anything inbound from the Person, or a connected call, pauses it for Jeremy to resume or finish.
_Avoid_: Lead, Prospect, Membership

**Finished**:
An Enrollment that has ended, as Completed (its last Task is done), Unsubscribed, or Canceled (Jeremy stopped it).
_Avoid_: Exited, Unenrolled, Done, Other

**Unsubscribed**:
A Person who asked not to be contacted; they cannot be enrolled in any Sequence again.
_Avoid_: Opted out, Suppressed

**Task**:
One Step for one Enrollment: the to-do item itself. It is Upcoming, Due, Done, or Skipped. An email Task is done when its Draft is sent, and other Tasks are marked done. Attio and Tana tasks shown as context are separate items.
_Avoid_: Step (for one Person's copy), Step instance, To-do

**Due Task**:
A Task whose day has come and which is not yet done.
_Avoid_: Due Step, Overdue item

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
