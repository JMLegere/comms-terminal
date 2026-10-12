@wip
Feature: Cases
  A Case (cases) groups Threads; a Thread points to at most one Case (threads.case_id). A Case has no Status.
  Act (inbox.act) with Set Case adds a Thread to an existing Case, to a new Case, or removes it.

  @AS-2 @FR-12 @FR-13
  Scenario: Act Set Case puts an email Thread and a Slack Thread in one new Case
    Given an email Thread 1 from "logan@agentmaple.com" with Status "TODO" and Status Detail "CREATED"
    And a Slack Thread 2 from "U0LOGAN" with Status "TODO" and Status Detail "CREATED"
    When Act is called on Thread 1 with Set Case and new Case title "Logan onboarding"
    And Act is called on Thread 2 with Set Case and that Case
    Then the Case "Logan onboarding" lists Threads:
      | Thread | Status | Status Detail |
      | 1      | TODO   | CREATED       |
      | 2      | TODO   | CREATED       |
