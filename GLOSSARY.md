# Comms Terminal

A single-user console that tracks who owes whom what across Jeremy's professional messaging Platforms, modelled on Plain's thread queue.

## Communication

**Thread**:
The unit of work, as in Plain: an email thread; in a Slack channel, the top-level messages until the channel has been quiet for 24 hours, or the replies to one top-level message; in a Slack DM or group DM, the messages until it has been quiet for 24 hours; or, in an SMS conversation, the messages until it has been quiet for 7 days. Messages Jeremy sends, from the console or elsewhere, group by the same rules.
_Avoid_: Conversation, Ticket, Loop

**Thread alias**:
A retired Thread ID that resolves to the surviving Thread after exact-reference email reconciliation. Old links still reach the same work and its combined history.
_Avoid_: Deleted thread, Duplicate activity

**Draft**:
An unsent email, SMS, or Slack composition with its own Connection, recipients, subject and body; it sends as the Connection's own address, number, or Slack user. A Slack Draft has no subject and goes to one existing Slack Channel (a channel, DM, or group DM Jeremy is in) instead of recipients. A Draft is a Reply Draft or a New Thread Draft. A Draft is neither a Thread nor an Activity. Sending it removes it and records the message: an email Draft starts a new Thread unless it is a Reply Draft; an SMS or Slack Draft joins its Channel's Thread by the ordinary Thread rules, starting a new one only when the Channel has been quiet long enough.

**Reply Draft**:
A Draft that belongs to one Thread, at most one per Thread, and sends as a reply in it. It is written in the Composer, inline in its Thread, and saves and sends like any Draft. A Todo Thread with a ready Reply Draft shows the Status detail Reply Draft.
_Avoid_: Reply box text, Response draft

**New Thread Draft**:
A Draft that belongs to no Thread: an email to new recipients, or an SMS or Slack message to a Channel. It is written in the Composer. Sending an email one starts a new Thread; an SMS or Slack one joins its Channel's Thread by the ordinary Thread rules.
_Avoid_: Compose (as the name of this kind), Outbound draft, Standalone draft

**Composer**:
The one editor for every Draft, of either kind and on every Platform, in the Console and the App. It has the same keys everywhere: ctrl+enter sends (or schedules), ctrl+s saves, ctrl+o attaches a file, esc closes and keeps the Draft. A Reply Draft's Composer opens inline in its Thread with Reply and Reply all and a fixed "Re:" Subject; a New Thread Draft's has a Connection and its own Subject. Every save is a new Draft revision, and every send sends the saved revision.
_Avoid_: Reply box (for the editor as a whole), Draft editor

**Send outcome unknown**:
A send that was started but never confirmed or refused, so the message may or may not have gone out. The Draft, a Reply Draft included, stays locked until Jeremy marks it sent, which removes it without recording a message, or explicitly sends it again. Nothing is recorded, and its text and staged files stay.
_Avoid_: Failed send, Pending

**Needs edit**:
A Draft that cannot be sent until it is changed. A Draft made from a Sequence Step starts this way unless the Step says otherwise, and Jeremy or an agent can mark any Draft this way. Any saved change to its text clears it.
_Avoid_: Locked (that is Send outcome unknown), Draft lock, Hold

**Draft revision**:
The monotonically increasing version of a saved Draft, checked before a later save may replace it.

**Captured history**:
Stored Threads matching an exact email or phone destination, whether or not it is linked to a Person; it makes no claim about external messages.

**Activity**:
A timestamped, never-rewritten record of a message received or sent in a Thread, a Phone call, a Meeting, or an edit or deletion of one.
_Avoid_: Event, Interaction, Message (when meaning the record)

**Acknowledgement**:
A signal that a message was seen or appreciated, carrying no automatic work-completion meaning.

**Source message**:
The individual Slack message to which an acknowledgement belongs, including a reply.

**Reaction**:
An emoji associated with one source message and a reacting Slack identity.

**Own reaction**:
A reaction attributed to Jeremy’s identity for the relevant Connection.

**Own-reaction state**:
Jeremy’s current reaction memberships on a particular source message.

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
A file that arrives or is sent with a message, or is added to a Draft to go with it, listed beside the message rather than placed in its body. A file added for a Thread's reply belongs to its Reply Draft, as a New Thread Draft's belong to it. A Slack file or an SMS photo is an Attachment.
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
Where a Thread stands, as in Plain: Todo, Snoozed, or Done. Every Thread also has one Status detail. A Case's Status is the roll-up of its Threads' Statuses. An Organization's or Person's place in a Pipeline is its Stage, not a Status.
_Avoid_: State

**Status detail**:
The reason within a Status, as in Plain: Needs first response, Needs next response, Investigating, or Reply Draft (Todo); Waiting for them or Paused for later (Snoozed, always until a date: a follow-up date or a wake time; never indefinite); Done or Ignored (Done).
_Avoid_: Substatus, Reason

**Reply Draft** (Status detail):
The last Todo Status detail: a Todo Thread whose Reply Draft has text, is not Needs edit, and is not sending. It is shown, never stored: the Thread keeps its own Status detail underneath, and returns to it when the Draft is deleted or marked Needs edit. Sending the Draft moves the Thread to Waiting for them as any reply does. Jeremy never sets it.
_Avoid_: Draft ready, Drafted, Ready to send, Pending

**Ignored**:
A Done Status for noise that does not move when new messages arrive, until Jeremy un-ignores it.
_Avoid_: Dismissed, Archived

**Blocked**:
A flag on one Identity that makes new Threads from it start as Ignored and keeps its ordinary inbound messages, edits, and deletions from moving an existing Thread's Status. Activity stays in history. Phone calls, independent timers, and email Thread reconciliation keep their own rules.
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
A company or group that Persons belong to. A Person a customer deal is made with on their own account gets a Sole proprietorship.
_Avoid_: Account, Company, Tenant

**Sole proprietorship**:
An Organization that stands for one Person acting on their own account, so their place in the Revenue Pipeline and their Enrollments are kept apart from their employer's.
_Avoid_: Personal organization, Individual account

**Membership**:
A Person's place in an Organization. A Person can belong to several Organizations. It follows the Person's company email domain, never a free-mail domain such as gmail.com; Jeremy's own additions and removals always win.
_Avoid_: Affiliation, Employment

**Unattended**:
An Organization or Person in a Pipeline, in an open or paying Stage, with no Todo or Snoozed Thread among its People (a Person's own Threads), no Active Enrollment for it, and none of its People on an upcoming Event, so nothing will bring it back to Jeremy. An Event counts until it ends, unless Jeremy or that Person declined it or it was deleted.
_Avoid_: Stalled, Dropped, No next step

## Outreach

**Sequence**:
An ordered set of Steps, each due a number of business days after the previous one is done, that Jeremy runs for one Person at a time. It opens with On start and every path through it ends with its own On completion; its paths never rejoin.
_Avoid_: Campaign, Cadence, Workflow

**On start**:
The Steps a Sequence runs the moment an Enrollment begins.
_Avoid_: Trigger, Entry step

**On completion**:
The Steps at the end of one path through a Sequence, run when an Enrollment on that path finishes; they usually check how it finished to choose what follows.
_Avoid_: Exit step, Goal, Finally

**Step**:
One item in a Sequence, of one kind: a touch (email, call, LinkedIn, or letter) that Jeremy does, an Action Step, or an If that chooses a path. A Step is the template; each Enrollment works it as a Task.
_Avoid_: Touch (for an Action Step or If), Task (for one Person's copy)

**Action Step**:
A Step whose work is an Action, such as setting a Stage or enrolling the Person in another Sequence. It checks no condition. An enrolling Action Step waits for Jeremy when the Person responded during this Enrollment or already went through that Sequence, unless the Sequence is enrolling them in itself to repeat.
_Avoid_: Automation, Update, Enrollment Step

**If**:
A Step that checks one condition and sends the Enrollment down its true path or its false path. It checks how the Enrollment finished, whether the Person has a way to be reached, the Organization's Stage, or asks Jeremy to pick.
_Avoid_: Condition, Branch, Switch, Filter

**Enrollment**:
One Person's run through one Sequence, for one Organization. It is Active, Paused, Finishing, or Finished. Anything inbound from the Person, or a connected call, pauses it for Jeremy to resume or finish; a Paused Enrollment needs Jeremy's attention until he does.
_Avoid_: Lead, Prospect, Membership

**Finished**:
An Enrollment that has ended, as Completed (its last Task is done), Goal met (Jeremy saw what the Sequence was for), Not Right Now (the Person wants to come back to it later), Lost (the Person said no; an Action then sets the Organization's Stage, which is a separate thing), Unsubscribed, or Canceled (Jeremy stopped it, or the Sequence or Person was tidied away). A Canceled Enrollment runs no On completion.
_Avoid_: Exited, Unenrolled, Done, Other

**Finishing**:
An Enrollment whose end reason is known while its On completion Steps are still being worked; it becomes Finished when they are done. A reply pauses it as usual.
_Avoid_: Closing, Wrapping up

**Unsubscribed**:
A Person who asked not to be contacted; they cannot be enrolled in any Sequence again.
_Avoid_: Opted out, Suppressed

**Task**:
One Step for one Enrollment: the to-do item itself. It is Upcoming, Due, Done, or Skipped. An email Task is done when its Draft is sent, and other Tasks are marked done. Attio and Tana tasks shown as context are separate items.
_Avoid_: Step (for one Person's copy), Step instance, To-do

**Due Task**:
A Task whose day has come and which is not yet done.
_Avoid_: Due Step, Overdue item

**Start date**:
The day an Enrollment's first Task counts from: the day Jeremy enrolls the Person, unless he sets a later one. Until then the Enrollment is Active and its first Task is upcoming.
_Avoid_: Delay, Scheduled enrollment, Snooze

**Stalled signature**:
A customer who has said they intend to sign but has not signed, and has gone quiet or kept moving the date.
_Avoid_: Flake, Timing objection, Slipped deal

**Deferred start**:
A customer who has named a later date or event to start from, such as "January" or "after budget approval".
_Avoid_: Timing objection, Delay, Pushed deal

**Reopening trigger**:
The specific evidence that brings a parked customer back into active pursuit, such as their named event passing or a reply naming a current need.
_Avoid_: Revisit date, Follow-up reminder

**Lever**:
A commercial change Jeremy has approved in advance for offering to a customer, such as a later start date or a smaller scope, usually in exchange for a commitment from them.
_Avoid_: Concession, Discount, Incentive, Sweetener

**Play**:
One deliberate use of a Lever, Sequence, or sales recipe on one deal, recorded with its result.
_Avoid_: Tactic, Attempt

**Mainline**:
The chain of Sequences, one for each open or paying Stage of a Pipeline, that carries a Relationship from first contact to the end of that Pipeline. Each one sets its own Stage in On start, and a Goal met hands the Relationship to the next.
_Avoid_: Main sequence, Funnel, Track

**Off-ramp**:
A Sequence for a situation outside the Mainline, such as Stalled signature, Deferred start or Nurture. It sets no Stage.
_Avoid_: Side sequence, Recipe Sequence

**Exit evidence**:
The observable fact that shows a Stage's purpose is met, such as an agreed first conversation or an executed agreement. Finishing a Mainline Sequence as Goal met records that Jeremy saw it.
_Avoid_: Exit criteria, Definition of done, Qualification

**Cold**:
A Relationship with no conversation yet with this Person: no reply and no meeting.
_Avoid_: New, Unwarmed

**Warm**:
A Relationship in which a conversation with this Person exists.
_Avoid_: Engaged, Hot

**Close-out**:
The last touch to a Cold or silent Person: an easy way to say "not now", with a dated reopening trigger.
_Avoid_: Breakup email, Final nudge, Last chance

**Agreed next step**:
A next step with a date and an owner that both sides accepted. A Warm Sequence starts on its date.
_Avoid_: Follow-up, Next action, Nudge

## Pipeline

**Pipeline**:
One staged flow toward an outcome: Revenue holds Organizations (customers, toward revenue); Fundraising (investors, toward a round) and Talent (candidates, toward joining) hold People, since a person decides to invest or to join, and a Person's Organizations come from their Memberships. Each has its own Stages; Revenue and Fundraising also have a Target. Where the terms below say Organization, read Person for Fundraising and Talent.
_Avoid_: Funnel, Deal list, Bowtie (when meaning the Revenue Pipeline itself)

**Stage**:
Where an Organization stands in the Revenue Pipeline, or a Person in Fundraising or Talent. Revenue Stages follow the Bowtie: Awareness, Education, Prioritization, Selection, Commit, Onboarding, Impact, Expansion, then Lost or Churned. An Organization enters Impact when its first payment arrives; Onboarding means signed but not yet paying. Fundraising Stages: Research, Outreach, Meeting, Diligence, Soft commit, Closed, Passed. Talent Stages: Explore, Role fit, Offer, Start, then Joined or Lost. An Organization or Person with no Stage is in no Pipeline; a Person may hold a Stage in Fundraising and Talent at once. An Action in a Sequence sets a Stage, and Jeremy may set one himself at any time; the latest change stands.
_Avoid_: Status, Account Stage (Attio's name for it), Deal stage

**Stage history**:
The record of every Stage an Organization or Person has held in a Pipeline and when it entered and left each one.

**Value**:
What one Organization (Revenue) or Person (Fundraising) is worth in a Pipeline: its Revenue Value or their Fundraising Value. Value belongs to that place in the Pipeline, not to its Stage; Jeremy sets it by hand.
_Avoid_: Deal value, ACV, ARR, MRR

**Revenue Value**:
The revenue expected from an Organization over the next twelve months, one-off fees included (FARR).
_Avoid_: ARR, ACV, Contract value

**Fundraising Value**:
The cheque an investor has committed or is expected to commit, whether from their own pocket or from the fund or company they belong to.
_Avoid_: Ticket, Check size

**Target**:
The outcome a Pipeline works backwards from, with a date: revenue over the trailing twelve months for Revenue, a round size for Fundraising.
_Avoid_: Goal (when meaning this number), Quota

**Conversion to Impact**:
The share of Organizations that held a Stage and later reached Impact, measured from Stage history. Each Stage has its own.
_Avoid_: Win rate, Close rate, Stage conversion (that is one Stage to the next)

**Weighted pipeline**:
The revenue the open Organizations are expected to bring: each one's Value weighted by its Stage's Conversion to Impact.
_Avoid_: Forecast, Expected pipeline

**Conversion rate**:
The share of Organizations in a Stage that move on to the next Stage, measured from Stage history.
_Avoid_: CR (in prose), Stage conversion

**Time in Stage**:
How long Organizations stay in a Stage before moving on, measured from Stage history.
_Avoid_: Velocity, Δt (in prose), Cycle time (that spans several Stages)

**Required volume**:
How many Organizations must enter a Stage each week to reach the Target, worked backwards from the Target through each later Stage's Conversion rate and Time in Stage.
_Avoid_: Quota, Input quota

**Pace**:
How far a Stage's actual weekly entries sit above or below its Required volume, as a signed number: -5 means five fewer Organizations a week than the Target needs.
_Avoid_: Shortfall, Health

**On track**:
Whether the Pipeline will reach its Target in time, judged by the top Stage's Pace: on pace when new Organizations enter it as fast as the Target needs, behind pace when slower, on track when customers and open Organizations already cover the Target, and off track when new Organizations can no longer reach the paying Stage by the deadline.
_Avoid_: Health, Status

**Coverage**:
The Weighted pipeline as a share of the revenue that new customers still have to add to reach the Target.
_Avoid_: Pipeline ratio

**Constraint**:
The lever with the largest multiplier on what reaches the paying Stage: a rate from the last 90 days back at its Benchmark, or new Organizations at the top Stage up to their Required volume. Rates multiply, so the Constraint is the biggest lift on offer, not the lowest rate. When no lever falls short, there is no Constraint.
_Avoid_: Bottleneck

**Benchmark**:
The same Conversion rate, Retention rate, or Expansion rate measured over the last 2 years of Agent Maple's own history, set beside the rate from the last 90 days. It stands in where the last 90 days have no data, and is always shown as a benchmark. No external table supplies it.
_Avoid_: Planning prior, Prior, Default rate, Table 6.2

**Retention rate**:
The share of the Value that customers paying at the start of the window still hold today, after churn and downgrades, scaled to a year (GRR).
_Avoid_: Logo retention, Renewal rate

**Expansion rate**:
How much the retained customers' Value grew over the same year, as a share of what they kept.
_Avoid_: Upsell rate

**Net retention**:
Retention rate times one plus Expansion rate: how a year changes the Value of today's customers (NRR).
_Avoid_: Net dollar retention

**Pipeline view**:
The console page that shows one Pipeline's math against its Target. Each Pipeline has its own. It sets no Tasks.
_Avoid_: Dashboard, Execution view

## Sources

**Platform**:
An outside service Comms Terminal connects to: Gmail, Fastmail, Slack, Quo, Google Calendar, Linear, Tana, or Attio; later Jira, WhatsApp, or LinkedIn. Each Platform has one adapter.
_Avoid_: Medium, Channel (when meaning the service), Email (Gmail and Fastmail are separate Platforms), Tool

**Message format**:
The shape of a message that Platforms can share: email (Gmail, Fastmail), Slack message, or SMS (Quo). Email addresses, mailbox threading by Message-ID and References, and reply-all belong to the email format, so one address is one Identity on every email Platform.
_Avoid_: Platform (when meaning the format), Protocol

**Channel**:
A container of Threads on a Platform, such as a Slack channel or DM.
_Avoid_: Space, Room, Inbox

**Connection**:
One connected account on a Platform, of one kind: an Email, SMS, Slack, Calendar, Context, or Issue connection. One account can back two Connections, such as Linear's Context connection and Issue connection. Jeremy can connect any number of email addresses; each Email connection has its own Inbound address, the `inbox.agentmaple.com` address its forward reaches, which files its mail under it.
_Avoid_: Source, Account, Integration

**Action**:
One thing Comms Terminal carries out itself, on Jeremy's word or on a schedule: a send, a Respond, a reaction, an Issue change, or an Action Step's effect. Each is either safe to repeat or not; a send is not, so one whose outcome is unknown is never repeated by itself.
_Avoid_: Job, Operation, Action item (that is a Task Jeremy does)

**Action Queue**:
Every Action that is scheduled, running, refused, or of unknown outcome, by day and by Connection, so Jeremy sees what is about to leave and what did not.
_Avoid_: Outbox, Outreach queue, Send queue

**Context connection**:
A Connection that Comms Terminal only reads, to show context beside Threads, Persons, Organizations, and Cases: Tana, Attio, or Linear. It holds no Threads.
_Avoid_: Context source, Integration

## Issues

**Issue**:
A work item assigned to Jeremy on a Platform that has an Issue connection, such as a Linear issue. That Platform owns it and every field on it; Comms Terminal keeps a copy and passes Jeremy's changes through.
_Avoid_: Task (that is a Sequence Task), Case, Ticket, To-do

**Issue connection**:
A Connection to a tracker that is the source of truth for Issues, with changes going both ways: Linear now, Jira later. Each has its own Slip status and estimate unit.
_Avoid_: Issue provider, Context connection (read-only), Integration

**Planned day**:
The day Jeremy intends to work an Issue. It lives only in Comms Terminal, is never sent to the Issue's Platform, and is never the Issue's due date.
_Avoid_: Scheduled date, Do date, Due date (for this), Day plan (there is none: a day's Issues are just the Issues with that Planned day)

**Day capacity**:
How much Issue work one workday fits: 9 hours less the hours Jeremy is busy on his calendar. An Issue counts 1 hour unless its estimate says otherwise; weekends hold none unless Jeremy plans there.
_Avoid_: Load, Bandwidth, WIP limit

**Unplanned**:
An open Issue with no Planned day.
_Avoid_: Backlog (that is a Linear status), Unscheduled

**Rollover**:
The automatic move of an unfinished Issue from a passed Planned day to the next workday, which also sets its Slip status.
_Avoid_: Carry-over, Reschedule, Slip (for the move)

**Slip status**:
The status a Rollover sets on the Issue's Platform, chosen per Issue connection: Blocked in Linear.
_Avoid_: Blocked (alone; that flags an Identity)

## Calendar

**Calendar**:
The page that lays Calendar Items out over time, read from Jeremy's work calendar. Jeremy reads it here, responds to invites from their Threads, and changes Events in Google Calendar.
_Avoid_: Agenda, Schedule

**Event**:
Any entry with a date or a time on Jeremy's work calendar. Every Event is a Meeting's booking, a Block, or an All-day Event. It is never a Thread.
_Avoid_: Appointment, Calendar entry

**Meeting**:
One synchronous conversation booked as an Event with attendees besides Jeremy, held on Google Meet, Zoom, Microsoft Teams, by phone, or in person. The Meeting is an Activity on a Thread: the Thread of its Event, or of its repeating series. Internal Meetings count the same as Meetings with someone outside agentmaple.com, and a Block has a Thread the same way: every Event does. A new such Thread starts in Todo, and the Thread goes to Todo when the Meeting ends or is cancelled or declined. Jeremy's own Response is an Activity too, and leaves Status as it is. Tana adds its summary and linked documents, which never change the Thread's Status. Only Meetings booked after Meetings became Threads join Threads, unless Jeremy Responds to one, which starts its Thread.
_Avoid_: Call (for a meeting), Appointment

**Block**:
A timed Event with no attendees besides Jeremy, such as focus time, lunch, or a reminder to himself. Like a Meeting, it has a Thread that goes to Todo when it ends.
_Avoid_: Focus time, Hold

**All-day Event**:
An Event with a date and no time, such as time off or a holiday.
_Avoid_: OOO (alone)

**Invite state**:
Jeremy's answer to an Event he was invited to: Accepted, Tentative, Unanswered, or Declined.
_Avoid_: RSVP, Response status

**Respond**:
To set Jeremy's Invite state on an Event he was invited to, from its Meeting Thread or invitation email Thread: Accept (Accepted), Maybe (Tentative), or Decline (Declined). Google Calendar records it and tells the organizer, as Gmail's invitation buttons do. A Response on a repeating series answers the whole series. Only Jeremy responds; agents never do.
_Avoid_: RSVP, Reply (for an invite)

**Calendar Item**:
Anything placed on the Calendar: an Event, a Task on its day, a Snoozed Thread at the time it wakes, a Draft at the time it is set to send, or an Enrollment on the day it starts.
_Avoid_: Entry, Event (for anything but an Event)

**Deleted Event**:
An Event that Google Calendar no longer has, including one removed occurrence of a repeating Event. Comms Terminal keeps it, marked deleted.
_Avoid_: Cancelled Event, Removed Event

## Laptop sync

**Change log**:
Oracle's record of every committed row change: the table, the row's id, and whether it was added, changed or removed, in order. It keeps 7 days (ADR 0012).
_Avoid_: Audit log (that one records Status and Case changes with their cause), Event log

**Change set**:
What the laptop console receives from Oracle when something changed: each row changed since its place in the Change log, once, as it now stands (or gone), with the raw messages and Attachment links those rows need. A new install, a place older than 7 days, or another schema gets a whole Snapshot instead.
_Avoid_: Diff, Delta, Patch

**Base copy**:
The laptop's copy of Oracle's state as last answered, kept in memory and saved as `base.db`. After each answer the console's copy becomes the Base copy again, with its Mutations not yet confirmed replayed on top.
_Avoid_: Snapshot (the whole-database download), Cache

**Mutation**:
One write the laptop console queued for Oracle, numbered in that console's order and stamped with the moment it was made. Oracle applies each Mutation once; a resent one gets its first answer.
_Avoid_: Transaction, Request, Call

## Clients

**Service**:
The process that is the only writer of one user's live database and holds its secrets. It ingests, sends, and answers the laptop's sync. Jeremy's Service runs on Oracle; each other user has their own.
_Avoid_: Server, Backend, Daemon

**Client**:
Any program that works on Comms Terminal's data on Jeremy's behalf: the Console, the App, and the CLI.
_Avoid_: Frontend, UI

**Console**:
The terminal Client (Textual). It keeps its own laptop copy and stays as the plain-terminal way in.
_Avoid_: TUI, Terminal app

**App**:
The native desktop Client: a GPU-drawn window that works through the Local core (ADR 0013).
_Avoid_: Desktop console, GUI, Frontend

**Local core**:
The process on Jeremy's machine that holds the laptop copy, the write queue and the sync with the Service, and serves the Local API. It keeps running when the App closes.
_Avoid_: Daemon, Agent, Sidecar

**Local API**:
The Local core's published contract: JSON-RPC on a user-only socket, described by an OpenRPC document that only grows.
_Avoid_: Service API (the Service's own routes), Protocol

**Origin**:
Who made a Local API call: a view (the App's built-in screens, on Jeremy's input) or a Panel. Panels cannot send, complete Tasks, undo or Respond.
_Avoid_: Caller, Source

**Palette**:
The App's one search input, opened with `/` or ctrl-shift-p: a single ranked list of the commands the page takes, by name with their key, and the laptop copy's People, Organizations, Cases, Sequences, Connections, Threads, Drafts, message text and Events, with typos forgiven.
_Avoid_: Omnisearch, Command menu, Command palette

**Breadcrumb**:
The App's top bar path: the rail page (with Today's pane or the Status view), then each full page opened on top, by name. Earlier levels click back.
_Avoid_: Title, Tab, Mode block

**Problem**:
A state shown on the left of the App's bottom bar while it lasts: read-only or offline, a broken Connection, Google Calendar broken, writes still saving.
_Avoid_: Alert, Error, Status (Status is a Thread's)

**Interface files**:
The files that set the App's layout, keys, settings, themes and Panels, with an `AGENTS.md` that explains them to agents. Edits apply while the App runs.
_Avoid_: Config, Preferences

**Panel**:
A small script, kept in the Interface files, that draws one part of the App and acts through the Local API with the Panel Origin.
_Avoid_: Mod, Plugin, Widget, Extension

## Modules

**Module**:
A named part of Comms Terminal that a Service's user can turn on or off for that Service: Pipelines, Sequences (with their Tasks and Enrollments), or Cases. An off Module is hidden in the Console and the App, its CLI commands say it is disabled, and its data is kept.
_Avoid_: Feature, Plugin, Extension, Section, Toggle

**Core**:
The parts of Comms Terminal that are always on and are not Modules: Connections (Context connections and Issue connections among them), Issues, the Action Queue, Threads, People, Organizations and Channels, with Today, Calendar and Drafts.
_Avoid_: Base, Required modules

**Module setting**:
Whether one Module is on or off for one Service. Every Client of that Service reads the same value.
_Avoid_: Flag, Toggle, Preference

## Undo

**Command**:
One change made through Comms Terminal: one call to a writer, from the Console, the App, the CLI, or the Service's own work. It records when, its Source, what it did in a few words, and its Inverse. A writer that another writer calls belongs to the outer Command.
_Avoid_: Action (that one is carried out on a Platform), Mutation (the laptop's queued write), Activity, Event, Operation

**Source**:
Where a Command came from: Console, App, CLI, or Service.
_Avoid_: User, Agent, Origin (the Local API's view or Panel)

**Inverse**:
What puts one Command back: the writer calls that undo it, with the state its records must still be in. A Command whose change cannot be put back cleanly every time (a sent message, a completed Task, a new Case, a new Linear Issue) has no Inverse, only the reason.
_Avoid_: Rollback, Revert

**Undo**:
Running the Inverse of the newest Command from the Console, the App or the CLI, from any of them. It is a Command itself that points at the one it undid; it has no Inverse, so there is no redo. It is refused when the records changed since, when its Module is off, or when a newer Command exists.
_Avoid_: Revert, Rollback

**Command log**:
Every Command, kept forever on the Service and copied to each laptop like any other table. Never rewritten.
_Avoid_: Change log (the laptop sync's record of row changes, ADR 0012), Audit log, Activity

**History**:
The Command log as Jeremy reads it: on the Action Queue page, below the Action Queue, by day, with each Command's time, Source and words, and whether it was undone. Service Commands can be hidden.
_Avoid_: Activity, Timeline, Feed

**Undo window**:
How long Send now waits before the message leaves, so Undo can call it back; a Setting in seconds, 10 by default, 0 to send at once. It uses the send-later time.
_Avoid_: Delay, Grace period, Unsend
