Feature: Promises
  What specific People expect Jeremy to do by a time (ADR 0024, 0027; requirements FR-PR-1–20 in
  .agents/prd/2026-11-18-promises.md). Record (promises.record), Firm (promises.firm), Change (promises.change),
  Fulfil (promises.fulfil), Cancel (promises.cancel) and Link (promises.link) are an agent's CLI writers; each
  cites an Origin. Advance Capture (promises.advance_capture) moves "captured up to". The View (promises.view)
  lists owed Promises by section. Times are Jeremy's (Halifax); it is Monday 2026-11-16 10:00 unless a step says
  otherwise.

  Background:
    Given "Anna Ciprick" wrote to Jeremy from "anna@example.com"
    And Jeremy wrote "I'll send the traction update by Friday" to "anna@example.com"

  @AS-PR-1 @FR-PR-1 @FR-PR-4
  Scenario: An agent records a Promise from Jeremy's message
    When the agent Records "Send the traction update" owed to "Anna Ciprick" due "2026-11-20" citing that message
    Then the Promise "Send the traction update" has State "OPEN"
    And it is owed to "Anna Ciprick"
    And its history has 1 change "recorded" citing that message

  @AS-PR-2 @FR-PR-4
  Scenario: A change without an Origin is refused
    When the agent Records "Send the traction update" owed to "Anna Ciprick" due "2026-11-20" citing nothing
    Then it is refused with "cites its Origin"
    And there are no Promises

  @AS-PR-3 @FR-PR-3
  Scenario: Jeremy cannot change a Promise from the Console
    Given the agent Recorded "Send the traction update" owed to "Anna Ciprick" due "2026-11-20"
    When the Console Fulfils "Send the traction update" in Jeremy's words "sent it"
    Then it is refused with "only an agent changes Promises"
    And the Promise "Send the traction update" has State "OPEN"

  @AS-PR-4 @FR-PR-2
  Scenario: A Promise kept after its time is Fulfilled late
    Given the agent Recorded "Send the traction update" owed to "Anna Ciprick" due "2026-11-13"
    When the agent Fulfils "Send the traction update" citing that message
    Then the Promise "Send the traction update" has State "FULFILLED"
    And its last change is late

  @AS-PR-5 @FR-PR-5
  Scenario: A Tentative promise that was never owed is Cancelled with its evidence
    Given the agent Recorded a Tentative "Send a Meet invite" owed to "Anna Ciprick" due "2026-11-18"
    When the agent Cancels "Send a Meet invite" in Jeremy's words "she booked it herself"
    Then the Promise "Send a Meet invite" has State "CANCELLED"
    And its history has 1 change "cancelled" citing Jeremy's words "she booked it herself"

  @AS-PR-6 @FR-PR-6
  Scenario: Links are context and leave the State alone
    Given the agent Recorded "Send the traction update" owed to "Anna Ciprick" due "2026-11-20"
    When the agent Links "Send the traction update" to url "https://example.com/deck"
    Then the Promise "Send the traction update" has State "OPEN"
    And it links url "https://example.com/deck"

  @AS-PR-7 @FR-PR-2 @FR-PR-7 @FR-PR-8
  Scenario: The View puts owed Promises in sections by Slack-time
    Given the agent Recorded "Send the deck" owed to "Anna Ciprick" due "2026-11-16 14:00" needing 180 minutes of work
    And the agent Recorded "Write the memo" owed to "Anna Ciprick" due "2026-11-17 10:00" needing 180 minutes of work
    And the agent Recorded "Call back" owed to "Anna Ciprick" due "2026-11-13"
    And the agent Recorded "Send the term sheet" owed to "Anna Ciprick" due "2026-11-30"
    Then the View lists "Send the deck" under "urgent"
    And the View lists "Write the memo" under "at_risk"
    And the View lists "Call back" under "overdue"
    And the View lists "Send the term sheet" under "later"

  @AS-PR-18 @FR-PR-9
  Scenario: Today leaves Promises in Later on the Promises page
    Given the agent Recorded "Send the term sheet" owed to "Anna Ciprick" due "2026-11-30"
    And the agent Recorded "Send the update" owed to "Anna Ciprick" due "2026-11-20"
    And the agent Recorded "Write the report" owed to "Anna Ciprick" due "2026-11-30" needing 2000 minutes of work
    Then Today lists only the Promises "Write the report" and "Send the update"
    And the View lists "Send the term sheet" under "later"

  @AS-PR-8 @FR-PR-10 @NFR-PR-5
  Scenario: Capture resumes after the last Activity it finished
    When the agent Advances Capture to that message
    Then nothing is pending after the mark
    When "anna@example.com" writes "Any update?"
    Then 1 Activity is pending after the mark
    And Advancing Capture to that message again is refused

  @AS-PR-9 @FR-PR-14
  Scenario: A Promise's time starts the Warm Enrollment it is linked to
    Given "Anna Ciprick" is in a running Enrollment
    And the agent Recorded "Book the kickoff call" owed to "Anna Ciprick" due "2026-11-24"
    When the agent Links "Book the kickoff call" to her Enrollment
    Then her Enrollment's Due Task comes due "2026-11-24"
    When the agent Changes the time of "Book the kickoff call" to "2026-11-26"
    Then her Enrollment's Due Task comes due "2026-11-26"

  @AS-PR-10 @FR-PR-21
  Scenario: A kept Promise records how long it actually took, for the next estimate
    Given the agent Recorded "Send the traction update" owed to "Anna Ciprick" due "2026-11-20" needing 30 minutes of work
    When the agent Fulfils "Send the traction update" citing that message, taking 45 minutes
    Then the Kept list shows "Send the traction update" with work 30 and actual 45

  @AS-PR-11 @FR-PR-22 @NFR-PR-7
  Scenario Outline: Enter opens a Promise on Today without opening its Thread
    Given Today in the App shows a "<kind>" Promise
    When Jeremy presses Enter on that Promise
    Then its read-only Promise Detail has focus on Today and shows its history
    And Escape returns to the same Promise row

    Examples:
      | kind      |
      | linked    |
      | unlinked  |
      | Tentative |

  @AS-PR-11 @FR-PR-22 @NFR-PR-7
  Scenario: The Thread link in Promise Detail opens the linked Thread
    Given Today in the App shows a "linked" Promise
    When Jeremy presses Enter on that Promise
    And Jeremy activates the Thread link in its Promise Detail
    Then the linked Thread opens without a write

  @AS-PR-12 @FR-PR-22 @NFR-PR-7
  Scenario: Prep opens its own Detail and a removed Prep leaves no obsolete Detail
    Given Today in the App shows Prep for a Meeting
    When Jeremy presses Enter on that Prep
    Then the Meeting's read-only Prep Detail has focus on Today
    And Escape returns to the same Prep row
    When that Prep disappears while its Detail has focus
    Then Escape returns to the next valid Today row without obsolete Prep Detail

  @AS-PR-13 @FR-PR-23
  Scenario: Today panes stay expanded and scroll together while Detail stays independent
    Given Today in the App shows Promise groups, Threads and an Issue
    When Jeremy presses Enter on a Promise
    Then every row is in one scrollable Today column
    And the same nonempty Today panes stay in order
    And after Escape, arrows and Tab still select Today rows
    And only a refresh that moves the selected row brings it back into view

  @AS-PR-15 @FR-PR-24 @NFR-PR-7 @NFR-PR-8
  Scenario: Only opening Prep Detail reads its Brief
    Given Today in the App shows Prep with local metadata and a link in Tana
    When Jeremy hovers over and highlights Prep and focuses a Promise's Detail
    Then the App has not requested a Brief
    When Jeremy opens that Prep's Detail
    Then its metadata and Tana link are visible before its one Brief read returns
    When its Brief returns with markup and outside addresses in the text
    Then the full Brief appears as plain text without loading or opening outside content
    And leaving Prep Detail discards its Brief

  @AS-PR-16 @FR-PR-24 @NFR-PR-7 @NFR-PR-8
  Scenario Outline: Prep Detail distinguishes Brief read outcomes
    Given Today in the App shows Prep with local metadata and a link in Tana
    When the Brief read returns "<status>"
    Then Prep Detail shows "<message>" without a Brief body or an invented time

    Examples:
      | status      | message              |
      | missing     | No Brief yet         |
      | unavailable | Brief unavailable    |
      | timeout     | Could not load Brief |

  @AS-PR-16 @FR-PR-24 @NFR-PR-7 @NFR-PR-8
  Scenario: A disconnect discards the Brief but leaves last-known Prep
    Given Today in the App shows Prep with local metadata and a link in Tana
    When the App goes offline or loses its connection while Prep Detail is open
    Then the last-known metadata and link remain without a Brief
    And reopening Prep after reconnecting makes one new read

  @AS-PR-17 @FR-PR-24 @NFR-PR-8
  Scenario: Only the current open of a Meeting can show its Brief
    Given Today in the App shows Prep with local metadata and a link in Tana
    When Jeremy opens two Meetings' Prep Details in order and returns to the first
    Then the original reads cannot replace the current Brief
    When a Brief read receives no reply
    Then it times out locally after 8 seconds
    And reopening Prep or leaving Detail ignores any late replies

  @AS-PR-19 @FR-PR-25
  Scenario: Copying a Promise gives an agent its source and a path to fulfilment
    Given "Ana" wrote to Jeremy from "ana@example.test"
    And Jeremy wrote "I'll send the checklist tomorrow" to "ana@example.test"
    When the agent Records "Send the checklist" owed to "Ana" due "2026-11-17" citing that message
    And Jeremy presses "y" on that Promise in the Console
    Then the clipboard names the Promise, its People and its time
    And it gives the read commands for the Promise and its Activity's Thread
    And it asks for a fulfilment plan without copying the message body or changing the Promise
