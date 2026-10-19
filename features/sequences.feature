Feature: Sequences
  A Sequence is an ordered set of Steps; an Enrollment is one Person's run through it, worked as Tasks.
  Create Sequence (sequences.create_sequence) and Add Step (sequences.add_step) build one; Enroll
  (sequences.enroll) starts an Enrollment; Due Tasks (sequences.tasks_due) lists what is due; Wake
  (inbox.wake) makes Tasks and their Drafts; Ingest (inbox.ingest) receives the Person's messages;
  Send Draft (inbox.send_draft) sends a Task's Draft; Finish (sequences.finish) ends an Enrollment.
  Today (inbox.today) lists Todo Threads, Due Tasks, and the Drafts not shown through them; Save Draft
  (inbox.save_draft) and Save Reply Draft (inbox.save_reply_draft) keep Drafts; Act (inbox.act) snoozes.
  Kind maps to a Step's kind, Gap to gap_days, Subject and Body to its text. Times are Jeremy's (Halifax);
  "Monday" is 2026-10-19 at 10:00 and "November 2" is 2026-11-02 at 10:00.

  Background:
    Given a Sequence "Secret Sequence" with Steps:
      | Kind  | Gap | Subject                | Body                 |
      | email | 0   | Pilot with {company}   | Hi {first_name}      |
      | call  | 1   |                        | Script               |
    And a Person "Dana Roy" with email "dana@harbourline.example" at Organization "Harbourline"

  @AS-SEQ-1 @FR-SEQ-3 @FR-SEQ-6 @FR-SEQ-7 @FR-SEQ-8 @FR-SEQ-15
  Scenario: Enrolling makes the first Task due today with its Draft
    When Enroll is called for "Dana Roy" in "Secret Sequence" on Monday
    Then Due Tasks on Monday are:
      | Person   | Kind  | Group     | Day |
      | Dana Roy | email | due today | 1   |
    And the email Task's Draft has Subject "Pilot with Harbourline" and Body "Hi Dana"

  @AS-SEQ-2 @AS-SEQ-3 @FR-SEQ-7 @FR-SEQ-8
  Scenario: Sending the Draft completes the Task and the next one is due a business day later
    Given "Dana Roy" is enrolled in "Secret Sequence" on Monday
    When Send Draft sends the email Task's Draft on Monday
    Then Due Tasks on Monday are empty
    And Due Tasks on Wednesday are:
      | Person   | Kind | Group   | Day |
      | Dana Roy | call | overdue | 2   |

  @AS-SEQ-4 @FR-SEQ-12 @FR-SEQ-13
  Scenario: A reply from the Person pauses the Enrollment and refuses its Draft
    Given "Dana Roy" is enrolled in "Secret Sequence" on Monday
    When Ingest receives a new email from "dana@harbourline.example" on Monday
    Then the Enrollment has State "PAUSED"
    And Due Tasks on Monday are empty
    And Send Draft refuses the email Task's Draft with "Enrollment is Paused"

  @AS-SEQ-8 @FR-SEQ-6 @FR-SEQ-14
  Scenario: An Unsubscribed Person cannot be enrolled again
    Given "Dana Roy" is enrolled in "Secret Sequence" on Monday
    When Finish is called with reason "UNSUBSCRIBED"
    Then the Enrollment has State "FINISHED" and reason "UNSUBSCRIBED"
    And Enroll for "Dana Roy" in "Secret Sequence" is refused with "unsubscribed"

  @AS-SEQ-14 @NFR-SEQ-1
  Scenario: Wake twice makes each Task and Draft once
    Given "Dana Roy" is enrolled in "Secret Sequence" on Monday
    When Wake runs twice on Monday
    Then there is 1 Task and 1 Draft

  @AS-T1 @AS-T9 @AS-T10 @FR-T5
  Scenario: Today lists each Draft once, a Task's Draft only through its Task
    Given "Dana Roy" is enrolled in "Secret Sequence" on Monday
    And Save Reply Draft keeps Body "Thanks" on the Thread "Hello"
    And Save Draft keeps a new email Draft with Body "Hi there"
    Then Today on Monday lists 1 Todo Thread, 1 Due Task, and Drafts with Body:
      | Body     |
      | Hi there |
    When Act snoozes the Thread "Hello" until Wednesday
    Then Today on Monday lists 0 Todo Threads, 1 Due Task, and Drafts with Body:
      | Body     |
      | Hi there |
      | Thanks   |

  @AS-SEQ-18 @FR-SEQ-25
  Scenario: Enrolling with a Start date makes the first Task due on that day
    When Enroll with Start date "2026-11-02" is called for "Dana Roy" in "Secret Sequence" on Monday
    Then Due Tasks on Monday are empty
    And there is 1 Task and 0 Draft
    When Wake runs twice on November 2
    Then Due Tasks on November 2 are:
      | Person   | Kind  | Group     | Day |
      | Dana Roy | email | due today | 1   |
    And the email Task's Draft has Subject "Pilot with Harbourline" and Body "Hi Dana"
