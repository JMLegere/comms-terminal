Feature: Undo
  Every change is a Command (GLOSSARY) with a Source and an Inverse, kept in the Command log
  (`.agents/prd/2026-11-08-service-undo.md`, ADR 0021). Undo (commands.undo) runs the Inverse of the newest Command
  from the Console, the App or the CLI. History (commands.history) lists the Command log. Act (inbox.act) changes a
  Thread's Status. Tags name the PRD's acceptance scenarios (AS-UN-n) and requirements (FR-UN-n, NFR-UN-n).

  @AS-UN-1 @FR-UN-1 @FR-UN-2 @FR-UN-5 @FR-UN-9 @FR-UN-14
  Scenario: A Thread marked Done in the App is undone from the Console
    Given an email Thread 7 "Intro call" in Status "TODO"
    When the App marks Thread 7 Done
    And the Console presses Undo
    Then Thread 7 has Status "TODO"
    And History lists "Done: Intro call" from "App" marked undone
    And History lists "Undo: Done: Intro call" from "Console"

  @AS-UN-2 @FR-UN-5 @FR-UN-8
  Scenario: An agent's Draft edit is undone from the Console
    Given a Draft "Q4 pricing" with body "First version"
    When the CLI sets the body of Draft "Q4 pricing" to "Agent version"
    And the Console presses Undo
    Then Draft "Q4 pricing" has body "First version"

  @AS-UN-3 @FR-UN-5 @FR-UN-8
  Scenario: Undo is refused when a reply arrived since
    Given an email Thread 7 "Intro call" in Status "TODO"
    When the Console marks Thread 7 Done
    And Ingest receives a reply in Thread 7
    And the Console presses Undo
    Then Undo is refused with "changed since"
    When the Console presses Undo
    Then Undo is refused with "changed since"

  @AS-UN-4 @FR-UN-9
  Scenario: Undo after Undo has nothing to undo
    Given an email Thread 7 "Intro call" in Status "TODO"
    When the Console marks Thread 7 Done
    And the Console presses Undo
    And the Console presses Undo
    Then Undo says "Nothing to undo."

  @AS-UN-5 @FR-UN-10 @FR-UN-16
  Scenario: Send now waits the Undo window and Undo calls it back
    Given the Undo window is 10 seconds
    And a Draft "Q4 pricing" ready to send
    When the Console sends Draft "Q4 pricing" now
    And the Console presses Undo
    Then nothing has been sent
    And Draft "Q4 pricing" has no send time

  @AS-UN-6 @FR-UN-17
  Scenario: An Undo window of 0 sends at once
    Given the Undo window is 0 seconds
    And a Draft "Q4 pricing" ready to send
    When the Console sends Draft "Q4 pricing" now
    Then Draft "Q4 pricing" has been sent

  @AS-UN-7 @FR-UN-7
  Scenario: Undo into an off Module is refused and kept
    Given Pipelines is on
    And the Organization "acme.com" at Revenue Stage "Selection"
    When the Console sets the Revenue Stage of "acme.com" to "Commit"
    And Set Module turns Pipelines off
    And the Console presses Undo
    Then Undo is refused with "Pipelines is disabled"
    When Set Module turns Pipelines on
    And the Console presses Undo
    Then "acme.com" is at Revenue Stage "Selection"

  @AS-UN-8 @FR-UN-6
  Scenario: The same Command undone from two Clients
    Given an email Thread 7 "Intro call" in Status "TODO"
    When the Console marks Thread 7 Done
    And the App presses Undo on that Command
    And the Console presses Undo on that Command
    Then the second Undo says "Nothing to undo."

  @AS-UN-9 @FR-UN-18 @FR-UN-19
  Scenario: Service work shows in History and can be hidden
    Given an email Thread 7 "Intro call" in Status "DONE"
    When a Service pass ingests a reply in Thread 7
    Then History lists one Command from "Service"
    And History without Service Commands lists none

  @AS-UN-10 @FR-UN-15
  Scenario: The CLI shows and undoes the newest Command
    Given an email Thread 7 "Intro call" in Status "TODO"
    When the Console marks Thread 7 Done
    And the CLI runs "undo --show"
    Then the CLI prints "Done: Intro call"
    When the CLI runs "undo"
    Then Thread 7 has Status "TODO"
