Feature: Actions
  Everything Comms Terminal carries out itself goes through the Action Queue (ADR 0019; requirements FR-AQ-1–7 in
  .agents/prd/2026-11-07-issues-and-action-queue.md). Update Issue (issues.update) and Create Issue (issues.create)
  change the copy at once and Enqueue (actions.enqueue) an Action; Run (actions.run) carries Actions out in order
  per Connection. An Action is confirmed, refused, or of unknown outcome; one that is safe to repeat is sent again,
  and a send is not. Send Draft (inbox.send_draft) keeps the Send outcome unknown lock (ADR 0015). Linear and the
  email provider here are fakes that record each request; nothing leaves the test.

  Background:
    Given an Issue connection to Linear for Jeremy
    And the team "Business" has statuses "Todo", "Making It Happen", "Blocked" and "Done"
    And an Issue "BIZ-10 Send the data room link" assigned to Jeremy with status "Todo"

  @AS-IQ-7 @FR-AQ-1 @FR-AQ-2 @NFR-1
  Scenario: A refused status change snaps back with its reason
    Given Linear will refuse the next request with "invalid state for team"
    When Jeremy Updates "BIZ-10" to status "Making It Happen"
    Then "BIZ-10" has status "Making It Happen" before the Action runs
    When the Action Queue runs
    Then "BIZ-10" has status "Todo"
    And "BIZ-10" shows "Not saved: invalid state for team"

  @AS-IQ-7 @FR-AQ-1
  Scenario: A confirmed status change stays
    When Jeremy Updates "BIZ-10" to status "Done"
    And the Action Queue runs
    Then Linear received one update setting "BIZ-10" to status "Done"
    And the Action is "confirmed"

  @AS-IQ-8 @FR-AQ-3 @FR-CK-3
  Scenario: A refused sign-in marks the Issue connection broken and holds its Actions
    Given Linear will refuse every request with HTTP 401
    When Jeremy Updates "BIZ-10" to status "Done"
    And the Action Queue runs
    Then the Issue connection has a Last Error
    And the Action is "scheduled"
    When Linear accepts requests again
    And the Action Queue runs
    Then the Action is "confirmed"
    And the Issue connection has no Last Error

  @AS-IQ-9 @FR-AQ-4 @FR-AQ-5
  Scenario: A create whose outcome is unknown is looked up by its id before it is sent again, and makes one Issue
    Given Linear will time out on the next request after creating the Issue
    When Jeremy Creates an Issue "Book the closing call" in team "Business"
    And the Action Queue runs twice
    Then Linear received one create and then a lookup by the same id
    And Linear holds one Issue "Book the closing call"
    And the Action is "confirmed"

  @AS-IQ-9 @FR-AQ-6
  Scenario: Undo is a new Action that puts the old value back
    Given Jeremy Updated "BIZ-10" to status "Done" and the Action was confirmed
    When Jeremy undoes that change
    Then an Action sets "BIZ-10" to status "Todo"

  @AS-IQ-10 @FR-AQ-4 @FR-AQ-7
  Scenario: A send whose outcome is unknown stays locked and is not sent again
    Given a New Thread Draft "Data room" to "ana@acme.com"
    And the email provider will time out
    When Jeremy sends the Draft
    And the Action Queue runs twice
    Then the email provider received one send
    And the Draft is locked with "Send outcome unknown"
    And the Action Queue lists the send with outcome "unknown"
