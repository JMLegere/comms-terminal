Feature: Modules
  A Module (GLOSSARY) is Pipelines, Sequences or Cases. Its Module setting (modules.enabled) says whether it is on
  for one Service; a Service with no Module setting has the Module off (`.agents/prd/2026-11-07-modules.md`,
  ADR 0017). Set Module (inbox.set_module) turns one on or off. A Client call into an off Module is refused with
  Module Disabled (inbox.ModuleDisabled); the Service's own calls are not. Materialize (sequences.materialize)
  makes each Enrollment's next Task. Tags name the Modules PRD's acceptance scenarios (AS-MOD-n) and requirements
  (FR-MOD-n, NFR-MOD-n), numbered as AS-n, FR-n and NFR-n in that PRD.

  @wip @AS-MOD-1 @FR-MOD-1 @FR-MOD-2 @FR-MOD-9
  Scenario: A new Service shows only Core in the rail
    Given a new Service with no Module setting
    When the Console opens
    Then the rail shows "Today", "Calendar", "Todo", "Snoozed", "Done", "Ignored", "Drafts", "People", "Organizations", "Channels" and "Settings"
    And the rail shows no "Cases", "Tasks", "Sequences" or Pipeline rows

  @wip @AS-MOD-2 @FR-MOD-3 @FR-MOD-4 @FR-MOD-7 @FR-MOD-9
  Scenario: With Cases off, Threads lose their Case headers and the CLI refuses Set Case
    Given Cases is on
    And an email Thread 12 in the Case "Logan onboarding"
    When Set Module turns Cases off
    Then the Todo view lists Thread 12 under its Status Detail header with no Case header
    And the Thread page offers no "case" key
    When the CLI runs "thread 12 --case 3"
    Then the CLI prints "Cases is disabled on this Service. Turn it on in Settings › Modules."
    And the CLI exits with code 3
    And Thread 12 is still in the Case "Logan onboarding"

  @wip @AS-MOD-3 @FR-MOD-5 @FR-MOD-7
  Scenario: With Sequences off, a reply still pauses the Enrollment and no Task is made
    Given Sequences is on
    And "ana@acme.com" has an Active Enrollment whose next Step is due tomorrow
    When Set Module turns Sequences off
    And Ingest receives a new email from "ana@acme.com"
    Then the Enrollment is "PAUSED"
    And no Enrollment-paused toast shows
    When Materialize runs the day after tomorrow
    Then no Task or Draft is made for the Enrollment

  @wip @AS-MOD-4 @FR-MOD-6
  Scenario: Turning Sequences back on brings Steps that fell due meanwhile as overdue
    Given Sequences is off
    And an Active Enrollment whose next Step fell due 3 days ago
    When Set Module turns Sequences on
    And Materialize runs
    Then the Enrollment has a Task with State "DUE" due 3 days ago
    And Today shows "1 overdue"

  @wip @AS-MOD-5 @FR-MOD-10
  Scenario: A Client showing a page of a Module turned off elsewhere goes back to Today
    Given Cases is on
    And the Console shows the Case "Logan onboarding"
    When the App turns Cases off
    And the Console applies the next Change set
    Then the Console shows Today
    And the Console shows the toast "Cases turned off"

  @wip @AS-MOD-6 @FR-MOD-11
  Scenario: Undo into an off Module is refused and kept
    Given Pipelines is on
    And Jeremy set the Stage of "Acme" in the Revenue Pipeline to "Proposal"
    When Set Module turns Pipelines off
    And Jeremy presses undo
    Then the toast "Pipelines is disabled" shows
    And "Acme" is at Stage "Proposal"
    When Set Module turns Pipelines on
    And Jeremy presses undo
    Then "Acme" is at its Stage before "Proposal"

  @wip @AS-MOD-7 @FR-MOD-1
  Scenario: Turning a Module off and on keeps its data
    Given Cases is on
    And 2 Cases holding 5 Threads
    When Set Module turns Cases off
    And Set Module turns Cases on
    Then there are 2 Cases holding 5 Threads

  @wip @AS-MOD-8 @FR-MOD-16
  Scenario: A Context source page shows its problem and what it adds context to
    Given the Context source "Tana" fails with "401 Unauthorized"
    When Jeremy opens "Tana" from the rail
    Then its Context source page shows "401 Unauthorized" and the time of its last check
    And it says Tana adds context to "Threads", "People", "Organizations" and "Cases"
