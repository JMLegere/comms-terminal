Feature: App
  The App is a native desktop Client that works through the Local core (ADR 0013,
  `.agents/prd/2026-10-31-desktop-app.md`). The Local core (core.Core) holds the laptop copy and the write queue
  and serves the Local API (core.serve). Message Body (core query "message.body") is a message's HTML with every
  remote fetch removed (render.safe_html). Tags name the feature PRD's acceptance scenarios (AS-APP-n) and
  requirements (FR-APP-n, NFR-APP-n), numbered as AS-n, FR-n and NFR-n in that PRD.

  @AS-APP-1 @FR-APP-7 @FR-APP-8 @NFR-APP-4
  Scenario: A newsletter shows its inline images and holds back remote ones
    Given an email whose HTML has an inline image "logo@x" and a remote image with alt "Weekly deals"
    When the App opens its Message Body
    Then the inline image shows
    And "Weekly deals" shows where the remote image was
    And no network request leaves the App
    When Jeremy chooses Load images
    Then the remote image shows

  @AS-APP-2 @FR-APP-7 @NFR-APP-4
  Scenario: A Message Body makes no request for any remote reference
    Given an email whose HTML uses a stylesheet link, CSS url(), srcset, an iframe and a script
    When the App opens its Message Body
    Then a logging HTTP server receives no request

  @wip @AS-APP-3 @FR-APP-9
  Scenario: One click opens an Attachment and one click opens a link
    Given a Thread whose email has a PDF Attachment and a link
    When Jeremy clicks the Attachment
    Then the PDF opens through its signed link
    When Jeremy clicks the link
    Then the default browser opens it

  @wip @AS-APP-4 @FR-APP-10 @NFR-APP-2
  Scenario: A file dropped on a reply is staged on it
    Given a Thread with a Reply Draft open in the App
    When Jeremy drops "contract.pdf" from Mani's disk on the reply
    Then the Reply Draft shows "contract.pdf" within 2 seconds

  @AS-APP-5 @FR-APP-2 @FR-APP-5
  Scenario: A write made just before the App quits still reaches the Service
    Given a Thread in Status "TODO"
    When Jeremy marks it Done in the App and quits the App at once
    Then the Service's Thread has Status "DONE"

  @AS-APP-6 @NFR-APP-2
  Scenario: An agent's Draft edit shows in the open App
    Given a Draft open in the App
    When an agent saves new text for that Draft through the CLI
    Then the App shows the agent's text within 2 seconds

  @NFR-APP-1
  Scenario: A Thread action is answered while a Context source is still looking
    Given a Thread in Status "TODO"
    And a Context source that takes 2 seconds to answer
    When the App asks for the Thread's context and then marks it Done, on one Local API connection
    Then the Done answer arrives within 0.5 seconds, before the context answer
    And the laptop copy's Thread has Status "DONE"

  @NFR-APP-1
  Scenario: The Views' reload after a write asks each query once
    Given a laptop copy with Threads, Tasks and Events
    When the Views reload after a write and two of them ask the same query
    Then the Local core computes that query once

  @NFR-APP-1
  Scenario: A reply key pressed while the Thread loads is not lost
    Given a Thread the App is still loading
    When Jeremy presses r before it arrives
    Then the reply box opens to the sender once the Thread arrives
    And a reply key left from a Thread he moved away from opens nothing

  @wip @AS-APP-7 @FR-APP-11
  Scenario: A broken key file keeps the last good keys
    Given the App running with its default keys
    When an agent writes an invalid "keys.json"
    Then the App keeps its previous keys and writes "keys.error"
    When Jeremy presses the escape key combination
    Then the App restores its default layout and keys

  @AS-APP-8 @FR-APP-13
  Scenario: A Panel cannot send
    Given a Panel that calls the send command for a Draft
    When the Panel runs
    Then the Local core refuses the call for the Panel Origin
    And the Draft is not sent

  @AS-APP-9 @FR-APP-14 @FR-APP-15
  Scenario: A new Service build makes the App read-only until it updates
    Given the App and the Local core on the Service's build
    When the Service reports a different build
    Then the App shows read-only and refuses writes
    And the update installs the Service's build

  @AS-APP-10 @FR-APP-14
  Scenario: Offline, reads work and writes are refused
    Given the Local core cannot reach the Service
    When Jeremy opens a Thread and then marks it Done
    Then the Thread shows
    And the write is refused with the read-only banner

  @AS-APP-11 @FR-APP-6
  Scenario: Every Console key has an App key or a reason
    Given the Console's key bindings
    Then each one maps to a key in the App's default "keys.json" or is listed with a reason

  @AS-APP-11 @FR-APP-6
  Scenario Outline: Ctrl Enter in the App uses the right send path for a Draft
    Given an App Draft editor containing a "<kind>" with edited text and recipients
    When Jeremy presses Ctrl Enter and chooses Send now in the Draft editor
    Then the App calls "<method>" with the edited Draft contents

    # A Reply Draft edits and sends in its Thread's reply box (D69).
    Examples:
      | kind             | method     |
      | Reply Draft      | send.reply |
      | New Thread Draft | send.draft |

  @AS-APP-11 @FR-APP-6
  Scenario Outline: A reply in the App refuses missing text or recipients
    Given an App reply box with empty "<field>"
    When Jeremy presses Ctrl Enter and chooses Send now in the Draft editor
    Then the App refuses the send with "<error>"

    Examples:
      | field | error                                      |
      | To    | Not sent: add at least one recipient in To. |
      | Body  | Nothing to send.                           |

  @AS-APP-12 @FR-APP-4 @NFR-APP-6
  Scenario: A breaking change to the Local API fails its check
    Given the released Local API document
    When a released method loses a parameter
    Then the compatibility check fails

  @AS-CONF-1 @FR-CONF-1
  Scenario Outline: On Today, a Thread key acts only on a selected Thread row
    Given Today in the App with a Thread row selected, then a "<row>" row
    When Jeremy presses the Thread key for "<command>"
    Then the App makes no call against the earlier Thread

    Examples:
      | row        | command      |
      | Task       | done         |
      | Task       | ignore       |
      | Draft      | ignore       |
      | Draft      | reply_sender |
      | Paused     | reply_sender |
      | Paused     | forward      |
      | Unattended | done         |

  @AS-CONF-1 @FR-CONF-1
  Scenario: On Today, a Thread key still acts on a selected Thread row
    Given Today in the App with a Thread row selected, then a "Thread" row
    When Jeremy presses the Thread key for "done"
    Then the App marks that Thread Done

  @AS-CONF-2 @FR-CONF-2
  Scenario: A Draft whose send outcome is unknown stays read-only
    Given an App Draft whose send outcome is unknown
    When Jeremy types in its body, then presses ctrl-s and esc
    Then the body still shows the stored text
    And the App saves nothing

  @AS-CONF-3 @FR-CONF-3
  Scenario: Leaving a Step's editor saves it, as the Console
    Given a Step open in the App's Step editor
    When Jeremy changes its subject and leaves the field, then presses esc with a bad gap, then with a good one
    Then leaving the field saves the new subject
    And the bad gap is refused with "Not saved: Gap is a whole number of business days, 0 or more." and the editor stays open
    And esc with a good gap saves it and closes the editor

  @AS-CONF-4 @FR-CONF-4
  Scenario: Merging People shows the merged Person before it merges
    Given Ana Ng selected on the App's People page
    When Jeremy presses g, picks Ben Ode, then accepts the preview
    Then the picker asks "Merge Ana Ng into"
    And the preview says "Ana Ng joins Ben Ode:" before any merge
    And the merge keeps Ben Ode and drops Ana Ng

  @AS-CONF-6 @FR-CONF-5
  Scenario: Deleting a Draft, a Sequence or a Step asks nothing and z puts it back
    Given a Draft, an unused Sequence and a Step in the App
    When Jeremy deletes each one and presses z after each
    Then no confirmation picker opens
    And each delete toast ends with "z undo" and z restores what was deleted

  @AS-CONF-5 @FR-CONF-6
  Scenario: The App keeps the Console's page stack
    Given the App on Threads › Todo with a Thread selected
    When Jeremy opens the Thread's Person, then goes back twice, then picks People in the rail
    Then the Person opens as a full page on top of Todo
    And going back returns to Todo with the same Thread, and goes no further
    And the rail pick starts a new stack

  @AS-CONF-6 @FR-CONF-7
  Scenario: One undo puts back the last change, once
    Given the App with a Thread selected
    When Jeremy marks it Done, then presses z twice
    Then the Done toast ends with "z undo"
    And the first z restores the Thread and says "Undone: back to how it was."
    And the second z says "Nothing to undo."

  @AS-CONF-7 @FR-CONF-11 @FR-CONF-9
  Scenario: The Threads list groups and marks Threads as the Console does
    Given Todo in the App with a ready Reply Draft, a saved reply that is not ready, and Threads with and without a Case
    When the Threads list shows them
    Then Status headers come in the legend's order, with "No case" and Case headers inside them
    And the saved reply that is not ready stays under "Needs next response"

  @AS-CONF-8 @FR-CONF-9
  Scenario: Enter on a Case header opens the Case
    Given a Case header selected in the App's Threads list
    When Jeremy presses Enter
    Then the Case opens as a full page on the stack

  @AS-CONF-7 @FR-CONF-9
  Scenario: After s, the selection moves on to the next Thread
    Given three Todo Threads in the App with the first selected
    When Jeremy changes its Status detail with s
    Then the selection moves to the Thread below it

  @AS-CONF-7 @FR-CONF-9
  Scenario: After s on Today, the selection moves on to the next Thread
    Given three Todo Threads on the App's Today with the first selected
    When Jeremy changes its Status detail with s on Today
    Then the selection moves to the Thread below it

  @AS-CONF-7 @FR-CONF-9
  Scenario: Ages and countdowns read as the Console's
    Given the App's Threads list
    When it shows ages and snooze countdowns
    Then ages read "now", "1m", "3h", "2d", "Oct 12" and countdowns read "5m24s", "3h0m5s", "now", "—"

  @AS-CONF-12 @FR-CONF-10
  Scenario: The Drafts list keeps the Console's order and status words
    Given App Drafts that failed to send, are unsent, need an edit and are scheduled
    When the Drafts list shows them
    Then failed sends come first, then unsent, then scheduled by send time
    And their status words read "failed", "saved", "edit me" and the weekday they send

  @AS-CONF-12 @FR-CONF-10
  Scenario: Enter on a Reply Draft opens its Thread's reply box
    Given a Reply Draft selected in the App's Drafts list
    When Jeremy presses Enter on it
    Then its Thread opens in its Status view with the reply box open
    And Ctrl Enter in the Draft pane sends nothing

  @AS-CONF-12 @FR-CONF-10
  Scenario: Ctrl Enter refuses an incomplete Draft before the send menu
    Given a New Thread Draft in the App with no subject
    When Jeremy presses Ctrl Enter
    Then the editor says "Not sent · Add a subject." and no send menu opens
    And a complete Draft's send menu lists its Connection, From, To and Subject

  @AS-CONF-13 @FR-CONF-10
  Scenario: A key on Today acts on its row's own list
    Given Today in the App with a Thread, a Task, a Draft and a Paused Enrollment
    When Jeremy presses x and r on each row and tab from the Thread
    Then x ignores the Thread, skips the Task and deletes the Draft, r resumes the Paused Enrollment, and tab moves to the Task

  @AS-CONF-13 @FR-CONF-10
  Scenario: Today's Unattended rows name their Stages, and All clear says what comes next
    Given Today in the App with an Unattended Organization in two Stages
    When Today shows it, and later shows nothing
    Then its row reads "Proposal · Intro" and All clear reads "Next wake · Mon 02 Nov 09:00"

  @AS-CONF-14 @FR-CONF-10
  Scenario: The Calendar is one scrolling grid of week rows split by month
    Given the App's Calendar around the turn of a month
    When it lays out its weeks
    Then a separator row starts each month and splits the week it falls in, and cells read "1 Nov" and "3 today"

  @AS-CONF-14 @FR-CONF-10
  Scenario: o on an Event toasts its join link
    Given an Event with a join link in the App's day pane and on its Event page
    When Jeremy presses o in each
    Then each toasts the link, which opens on Ctrl+click, and nothing opens at once

  @AS-CONF-15 @FR-CONF-10
  Scenario: Enter folds a Case card and the Thread keys act on the selected card
    Given a Case page in the App with two Thread cards
    When Jeremy moves to the second card and presses Enter twice
    Then the Thread keys act on the second card's Thread, and the card folds and unfolds

  @AS-CONF-15 @FR-CONF-10
  Scenario: r on a Case card opens its reply row in place
    Given a folded Case card whose Thread has a saved reply
    When Jeremy presses a on it
    Then the card unfolds with its reply row open on the saved words, To filled from the Thread

  @AS-CONF-16 @FR-CONF-10
  Scenario: Enter on an email Task opens its Draft where it is edited
    Given email Tasks in the App with a Reply Draft and with a New Thread Draft
    When Jeremy presses Enter on each
    Then the Reply Draft opens in its Thread's reply box and the New Thread Draft's editor takes the keyboard

  @AS-CONF-16 @FR-CONF-10
  Scenario: Skipping a Task can be undone and the next Task is selected
    Given two due Tasks in the App with the first selected
    When Jeremy skips it with x
    Then the toast offers z undo, z would restore it, and the next Task is selected

  @AS-CONF-7 @FR-CONF-9
  Scenario: → takes an address completion in the reply box
    Given a reply box whose Cc offers a completion
    When Jeremy presses → at the end of the field
    Then the Cc takes the completion, and Tab leaves it to element focus

  @AS-CONF-7 @FR-CONF-9
  Scenario: A Thread opens at its newest entries
    Given a Thread the App is about to open
    When it opens
    Then the Thread pane scrolls to its newest entries
    And once Jeremy scrolls, or the Thread reloads, it stays where he is

  @AS-CONF-7 @FR-CONF-9
  Scenario: A prompt shows its hint inside its box and keeps Tab
    Given the App's New case title prompt
    When Jeremy opens it and types a title
    Then "What is this about?" is the box's placeholder, as in the Console
    And Tab stays in the prompt
    And Enter creates the Case with the typed title

  @AS-CONF-16 @FR-CONF-10
  Scenario: A Task's Draft stays out of the pane once another row is selected
    Given Today with an email Task whose New Thread Draft was shown
    When Jeremy selects a Reply Draft row and then the inbox changes
    Then the pane keeps the Reply Draft he selected

  @AS-CONF-17 @FR-CONF-10
  Scenario: Archiving a used Sequence explains itself and can be undone
    Given a Sequence in the App with three live Enrollments
    When Jeremy presses a and cancels the live Enrollments
    Then the picker explains archiving and offers to cancel the 3 live Enrollments or let them finish
    And the toast says "Archived Warm intro: 3 Enrollments Canceled." and z restores it

  @AS-CONF-18 @FR-CONF-10
  Scenario: s on an Organization offers its Pipeline's Stages with the current one marked
    Given an Organization in the App at Commit in Revenue only
    When Jeremy presses s and picks Onboarding
    Then the picker marks "▸ Commit  (current)" and offers the other Pipelines
    And the toast says "Acme → Onboarding." and z undoes it

  @AS-CONF-19 @FR-CONF-10
  Scenario: Resume and finish act on the one Enrollment, or ask which
    Given a Person in the App with one Paused Enrollment, and a Person with an Active and a Finishing one
    When Jeremy presses r on the first and f on the second
    Then the Paused one resumes at once, f asks "Which Enrollment?", and a Finishing one offers only Canceled and Unsubscribed
    And finishing can be undone with z

