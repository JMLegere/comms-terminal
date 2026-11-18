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
  Scenario: The View puts owed Promises in sections by Slack
    Given the agent Recorded "Send the deck" owed to "Anna Ciprick" due "2026-11-16 14:00" needing 180 minutes of work
    And the agent Recorded "Call back" owed to "Anna Ciprick" due "2026-11-13"
    And the agent Recorded "Send the term sheet" owed to "Anna Ciprick" due "2026-11-30"
    Then the View lists "Send the deck" under "at_risk"
    And the View lists "Call back" under "overdue"
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
