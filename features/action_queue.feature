Feature: Action Queue
  The Action Queue (GLOSSARY, ADR 0022; requirements FR-AQT-1–8 in .agents/prd/2026-11-08-action-queue.md) lists
  every Action that waits for a time and will happen with no act from Jeremy, and each from the last 7 days, as one
  timeline: Upcoming furthest ahead first, then In flight, then Sent. The Queue (actions.queue) reads the records
  that already hold each Action's time and outcome. Schedule (inbox.schedule_draft) gives a Draft its send time;
  Snooze and Wait (inbox.act) give a Thread a wake time; Wake (inbox.wake) carries them out.

  Background:
    Given the demo inbox at "2026-11-09 10:00"

  @AS-AQT-1 @FR-AQT-1 @FR-AQT-4 @FR-AQT-5
  Scenario: Scheduled Actions list furthest ahead first, each naming its target
    Given the Draft "Q4 pricing" to "ana@acme.com" is scheduled for "2026-11-10 09:00"
    And the Thread "Intro call" is snoozed until "2026-11-09 15:00"
    And the Thread "Pricing for 40 seats?" waits for a reply
    When Jeremy opens the Action Queue
    Then Upcoming lists, in order:
      | kind  | what        | target                          |
      | wake  | Follow-up   | Pricing for 40 seats?           |
      | email | Send        | Q4 pricing · ana@acme.com       |
      | wake  | Snooze ends | Intro call                      |

  @AS-AQT-2 @FR-AQT-2
  Scenario: Tasks Jeremy does and a started Enrollment stay off
    Given a call Task due yesterday for a Person
    When Jeremy opens the Action Queue
    Then the Action Queue lists nothing new

  @AS-AQT-4 @FR-AQT-6
  Scenario: A Snooze that woke shows in Sent
    Given the Thread "Intro call" is snoozed until "2026-11-09 11:00"
    When the Service wakes Threads at "2026-11-09 12:00"
    And Jeremy opens the Action Queue at "2026-11-09 12:00"
    Then Sent lists "Snooze ends" for "Intro call" as confirmed
