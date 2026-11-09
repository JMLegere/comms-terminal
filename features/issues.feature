Feature: Issues
  Jeremy's Linear Issues live in Comms Terminal as a copy; Linear stays the source of truth (ADR 0019; requirements
  FR-IS-1–13 in .agents/prd/2026-11-07-issues-and-action-queue.md). Full Read (linear.full_read) and the Linear
  hook (linear.handle_hook) keep the copy (issues.upsert). Plan (issues.plan) sets or clears a Planned day, which
  never goes to Linear. Day Capacity (issues.capacity) is 9 hours less Jeremy's busy calendar time; an Issue's
  Size is its Estimate times 30 minutes, else 1 hour. Warnings (issues.warnings) flag deadline misses, Issues
  planned too far out for their urgency, oversized Issues, and Unplanned Issues due soon. Plan Issues
  (issues.plan_all, ADR 0023; FR-PL-1–18 in .agents/prd/2026-11-09-plan-issues.md) gives every open Issue that is
  not Pinned a Planned day, most urgent first, filling each workday up to its Day Capacity; today's capacity ends
  at 18:00. Today (issues.view with today) lists Overdue Issues and today's Issues. Linear here is a fake that
  records each request; nothing reaches Linear. Times are Jeremy's (Halifax); "Monday" is 2026-11-09 and it is
  10:00 unless a step says otherwise, "Tuesday" 2026-11-10, "Wednesday" 2026-11-11, "Friday" 2026-11-13, "last
  Friday" 2026-11-06.

  Background:
    Given an Issue connection to Linear for Jeremy
    And the team "Business" has statuses "Todo", "Making It Happen", "Blocked" and "Done"

  @AS-IQ-1 @FR-IS-5 @FR-IS-6 @FR-IS-7 @FR-UI-1
  Scenario: A planned Issue counts against its day's capacity
    Given an Issue "BIZ-1 Draft the NBIF reply" assigned to Jeremy with no Estimate
    And Jeremy has a Meeting on Tuesday from 10:00 to 13:00 with Invite state "Accepted"
    When Jeremy Plans "BIZ-1" for Tuesday
    Then "BIZ-1" has Planned day "2026-11-10"
    And Day Capacity for Tuesday is "1 h of 6 h"
    And Linear received no request

  @AS-IQ-2 @FR-IS-7
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

  @AS-PL-1 @FR-PL-1 @FR-PL-4 @FR-PL-10
  Scenario: Plan Issues fills Monday by urgency up to its capacity, then Tuesday
    Given Jeremy has a Meeting on Monday from 11:00 to 14:00 with Invite state "Accepted"
    And an Issue "BIZ-10 Accept NBIF term sheet" assigned to Jeremy with priority "Urgent"
    And an Issue "BIZ-11 Certify share registers" assigned to Jeremy with priority "High"
    And an Issue "BIZ-12 Build Vetra runtime" assigned to Jeremy with priority "Medium" and Estimate 8
    And an Issue "BIZ-13 Upload logo" assigned to Jeremy with priority "Low"
    When Jeremy runs Plan Issues
    Then "BIZ-10" has Planned day "2026-11-09"
    And "BIZ-11" has Planned day "2026-11-09"
    And "BIZ-13" has Planned day "2026-11-09"
    And "BIZ-12" has Planned day "2026-11-10"
    And Linear received no request

  @AS-PL-2 @AS-PL-3 @FR-PL-1 @FR-PL-9
  Scenario: Plan Issues leaves Pinned Issues alone
    Given an Issue "BIZ-20 Present WMI webinar" assigned to Jeremy with status "Todo" and Planned day "2026-11-11"
    And an Issue "BIZ-21 Investor kit" assigned to Jeremy
    And "BIZ-20" is Pinned
    And "BIZ-21" is Pinned
    When Jeremy runs Plan Issues
    Then "BIZ-20" has Planned day "2026-11-11"
    And "BIZ-21" has no Planned day

  @AS-PL-5 @FR-PL-5
  Scenario: An Issue larger than a workday goes on today
    Given an Issue "BIZ-40 Data room" assigned to Jeremy with Estimate 40
    When Jeremy runs Plan Issues
    Then "BIZ-40" has Planned day "2026-11-09"
    And "BIZ-40" has the warning "Larger than a day"

  @AS-PL-6 @FR-PL-10
  Scenario: After 18:00 Plan Issues puts nothing new on today
    Given an Issue "BIZ-50 Dallas status check" assigned to Jeremy
    When Jeremy runs Plan Issues on Monday at 19:00
    Then "BIZ-50" has Planned day "2026-11-10"

  @AS-PL-7 @FR-PL-8
  Scenario: Undo puts back every Planned day a run changed
    Given an Issue "BIZ-60 Org progress report" assigned to Jeremy
    And an Issue "BIZ-61 Org engagement view" assigned to Jeremy with status "Todo" and Planned day "2026-11-12"
    When Jeremy runs Plan Issues
    And Jeremy presses Undo
    Then "BIZ-60" has no Planned day
    And "BIZ-61" has Planned day "2026-11-12"

  @AS-PL-8 @FR-PL-11 @FR-PL-12 @FR-PL-16
  Scenario: An unfinished Issue keeps its passed day and is Overdue on Today
    Given an Issue "BIZ-5 Accept NBIF term sheet" assigned to Jeremy with status "Making It Happen" and Planned day "2026-11-06"
    When Today is read on Monday
    Then "BIZ-5" has Planned day "2026-11-06"
    And Today lists "BIZ-5" as Overdue
    And Linear received no request

  @AS-PL-9 @FR-PL-11 @FR-PL-12 @FR-PL-13
  Scenario: Today counts Issues due or planned from yesterday to tomorrow as due now, and older ones as Overdue
    Given an Issue "BIZ-70 Tell NBIF the date" assigned to Jeremy, due "2026-11-08" and planned for "2026-11-12"
    And an Issue "BIZ-71 Batched reply to NBIF" assigned to Jeremy, due "2026-11-06" and planned for "2026-11-13"
    And an Issue "BIZ-72 Warm-up outreach" assigned to Jeremy with status "Todo" and Planned day "2026-11-10"
    And an Issue "BIZ-73 Board resolution" assigned to Jeremy, due "2026-11-02" and planned for "2026-11-09"
    And an Issue "BIZ-74 Investor list" assigned to Jeremy with status "Todo" and Planned day "2026-11-11"
    When Today is read on Monday
    Then Today lists "BIZ-70" for today
    And Today lists "BIZ-72" for today
    And Today lists "BIZ-73" for today
    And Today lists "BIZ-71" as Overdue
    And Today does not list "BIZ-74"
    And Today's figures are "3 h of 8 h" and "1 h" Overdue

  @AS-IQ-6 @FR-IS-9
  Scenario: An Issue planned after its due date is flagged
    Given an Issue "BIZ-7 Vision Coaching invoice" assigned to Jeremy due "2026-11-10"
    When Jeremy Plans "BIZ-7" for "2026-11-12"
    Then "BIZ-7" has the warning "Planned after its due date"

  @AS-IQ-11 @FR-IS-13
  Scenario: An Unplanned Issue due within 7 days is flagged
    Given an Issue "BIZ-8 Chestnut agreement" assigned to Jeremy due "2026-11-14" with no Planned day
    And an Issue "BIZ-9 Board update" assigned to Jeremy due "2026-11-18" with no Planned day
    When Warnings are read on Monday
    Then "BIZ-8" has the warning "Due soon and Unplanned"
    And "BIZ-9" has no warning
