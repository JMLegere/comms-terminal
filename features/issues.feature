Feature: Issues
  Jeremy's Linear Issues live in Comms Terminal as a copy; Linear stays the source of truth (ADR 0018; requirements
  FR-IS-1–13 in .agents/prd/2026-11-07-issues-and-action-queue.md). Full Read (linear.full_read) and the Linear
  hook (linear.handle_hook) keep the copy (issues.upsert). Plan (issues.plan) sets or clears a Planned day, which
  never goes to Linear. Day Capacity (issues.capacity) is 9 hours less Jeremy's busy calendar time; an Issue's
  Size is its Estimate times 30 minutes, else 1 hour. Warnings (issues.warnings) flag deadline misses, Issues
  planned too far out for their urgency, oversized Issues, and Unplanned Issues due soon. Rollover
  (issues.rollover) runs when Jeremy's day changes. Linear here is a fake that records each request; nothing
  reaches Linear. Times are Jeremy's (Halifax); "Monday" is 2026-11-09, "Tuesday" 2026-11-10, "Friday"
  2026-11-13, and "next Monday" 2026-11-16.

  Background:
    Given an Issue connection to Linear for Jeremy
    And the team "Business" has statuses "Todo", "Making It Happen", "Blocked" and "Done"

  @wip @AS-IQ-1 @FR-IS-5 @FR-IS-6 @FR-IS-7 @FR-UI-1
  Scenario: A planned Issue counts against its day's capacity
    Given an Issue "BIZ-1 Draft the NBIF reply" assigned to Jeremy with no Estimate
    And Jeremy has a Meeting on Tuesday from 10:00 to 13:00 with Invite state "Accepted"
    When Jeremy Plans "BIZ-1" for Tuesday
    Then "BIZ-1" has Planned day "2026-11-10"
    And Day Capacity for Tuesday is "1 h of 6 h"
    And Linear received no request

  @wip @AS-IQ-2 @FR-IS-7
  Scenario: An Estimate in points sets an Issue's size
    Given an Issue "BIZ-2 Closing packages" assigned to Jeremy with Estimate 4
    When Jeremy Plans "BIZ-2" for Monday
    Then the Size of "BIZ-2" is "2 h"

  @AS-IQ-3 @FR-IS-2 @FR-IS-4
  Scenario: A change made in Linear arrives by hook and keeps the Planned day
    Given an Issue "BIZ-3 Upload bank letters" assigned to Jeremy with status "Todo" and Planned day "2026-11-10"
    When the Linear hook receives a signed update setting "BIZ-3" to status "Done"
    Then "BIZ-3" has status "Done"
    And "BIZ-3" has Planned day "2026-11-10"

  @AS-IQ-3 @FR-IS-2
  Scenario: An unsigned hook is refused and marks the Issue connection
    When the Linear hook receives an update with a wrong signature
    Then the hook answers 401
    And the Issue connection has a Last Error

  @AS-IQ-4 @FR-IS-3 @FR-IS-12
  Scenario: An Issue reassigned in Linear leaves the copy
    Given an Issue "BIZ-4 Give Tony tool access" assigned to Jeremy
    When Full Read finds "BIZ-4" assigned to "Logan"
    Then the copy has no Issue "BIZ-4"

  @wip @AS-IQ-5 @FR-IS-10
  Scenario: Rollover moves Friday's unfinished Issue to Monday and sets its Slip status
    Given an Issue "BIZ-5 Accept NBIF term sheet" assigned to Jeremy with status "Making It Happen" and Planned day "2026-11-13"
    When Jeremy's day changes to Saturday 2026-11-14
    Then "BIZ-5" has Planned day "2026-11-16"
    And an Action sets "BIZ-5" to status "Blocked"

  @wip @AS-IQ-5 @FR-IS-10
  Scenario: Rollover leaves finished Issues alone
    Given an Issue "BIZ-6 Stop End-session alerts" assigned to Jeremy with status "Done" and Planned day "2026-11-13"
    When Jeremy's day changes to Saturday 2026-11-14
    Then "BIZ-6" has Planned day "2026-11-13"
    And no Action was made

  @wip @AS-IQ-6 @FR-IS-9
  Scenario: An Issue planned after its due date is flagged
    Given an Issue "BIZ-7 Vision Coaching invoice" assigned to Jeremy due "2026-11-10"
    When Jeremy Plans "BIZ-7" for "2026-11-12"
    Then "BIZ-7" has the warning "Planned after its due date"

  @wip @AS-IQ-11 @FR-IS-13
  Scenario: An Unplanned Issue due within 7 days is flagged
    Given an Issue "BIZ-8 Chestnut agreement" assigned to Jeremy due "2026-11-14" with no Planned day
    And an Issue "BIZ-9 Board update" assigned to Jeremy due "2026-11-18" with no Planned day
    When Warnings are read on Monday
    Then "BIZ-8" has the warning "Due soon and Unplanned"
    And "BIZ-9" has no warning
