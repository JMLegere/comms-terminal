@wip
Feature: Threads
  The inbox module (docs/design/api-and-database.md) owns Thread Status through four calls:
  Ingest (inbox.ingest) stores an incoming message, edit, or deletion; Act (inbox.act) applies Jeremy's keys;
  Reply (inbox.reply) sends Jeremy's reply through a Sender; Wake (inbox.wake) ends snoozes whose time has passed.
  Status and Status Detail values are Plain's enum names (TODO / CREATED is "Needs first response").

  @AS-7 @FR-6a @FR-8
  Scenario: Ingest of a new message from an unblocked sender creates a TODO / CREATED Thread
    When Ingest receives a new email from "prospect@newco.io"
    Then a Thread is created with Status "TODO" and Status Detail "CREATED"

  @AS-6 @FR-7a
  Scenario: Ingest of a new message from a blocked sender creates a DONE / IGNORED Thread
    Given "alerts@signoz.io" is blocked
    When Ingest receives a new email from "alerts@signoz.io"
    Then a Thread is created with Status "DONE" and Status Detail "IGNORED"

  @AS-4 @FR-6c
  Scenario: Ingest of the other side's reply moves a waiting Thread to TODO / NEW_REPLY
    Given an email Thread from "ana@acme.com" with Status "SNOOZED" and Status Detail "WAITING_FOR_CUSTOMER"
    When Ingest receives a reply from "ana@acme.com" on that Thread
    Then the Thread has Status "TODO" and Status Detail "NEW_REPLY"

  @AS-5 @FR-6c
  Scenario: Ingest of the other side's edit moves a done Thread to TODO / NEW_REPLY
    Given a Slack Thread from "U0LOGAN" with Status "DONE" and Status Detail "DONE_MANUALLY_SET"
    When Ingest receives an edit from "U0LOGAN" of a message on that Thread
    Then the Thread has Status "TODO" and Status Detail "NEW_REPLY"

  @FR-6e
  Scenario: Ingest leaves an ignored Thread unchanged
    Given an email Thread from "ana@acme.com" with Status "DONE" and Status Detail "IGNORED"
    When Ingest receives a reply from "ana@acme.com" on that Thread
    Then the Thread has Status "DONE" and Status Detail "IGNORED"

  @AS-3 @AS-17 @FR-6b
  Scenario: Reply sends through the Sender and moves the Thread to SNOOZED / WAITING_FOR_CUSTOMER
    Given an email Thread from "ana@acme.com" with Status "TODO" and Status Detail "CREATED"
    When Reply is called with "Thanks, booked for Tuesday." and the Sender succeeds
    Then the Sender received "Thanks, booked for Tuesday."
    And the Thread has one new Activity from Jeremy
    And the Thread has Status "SNOOZED" and Status Detail "WAITING_FOR_CUSTOMER"

  @FR-3b @FR-4c
  Scenario: Reply stores nothing when the Sender fails
    Given an email Thread from "ana@acme.com" with Status "TODO" and Status Detail "CREATED"
    When Reply is called with "Thanks, booked for Tuesday." and the Sender fails
    Then Reply raises Send Failed
    And the Thread has no new Activity
    And the Thread has Status "TODO" and Status Detail "CREATED"

  @AS-16 @FR-6d
  Scenario: Act Snooze with no time, then the other side writes
    Given an email Thread from "ana@acme.com" with Status "TODO" and Status Detail "CREATED"
    When Act is called with Snooze and no time
    Then the Thread has Status "SNOOZED" and Status Detail "WAITING_INDEFINITELY"
    When Ingest receives a reply from "ana@acme.com" on that Thread
    Then the Thread has Status "TODO" and Status Detail "NEW_REPLY"

  @FR-6c
  Scenario: Ingest of a second message before Jeremy's first reply keeps TODO / CREATED
    Given an email Thread from "ana@acme.com" with Status "TODO" and Status Detail "CREATED"
    When Ingest receives a reply from "ana@acme.com" on that Thread
    Then the Thread has Status "TODO" and Status Detail "CREATED"

  @FR-6c
  Scenario: Ingest of the other side's message on an investigating Thread gives TODO / NEW_REPLY
    Given an email Thread from "ana@acme.com" with Status "TODO" and Status Detail "IN_PROGRESS"
    When Ingest receives a reply from "ana@acme.com" on that Thread
    Then the Thread has Status "TODO" and Status Detail "NEW_REPLY"

  @FR-7
  Scenario Outline: Act Unignore returns the Thread to TODO by reply history
    Given an email Thread from "ana@acme.com" with Status "DONE" and Status Detail "IGNORED"
    And Jeremy <history> on that Thread
    When Act is called with Unignore
    Then the Thread has Status "TODO" and Status Detail "<detail>"

    Examples:
      | history          | detail    |
      | has replied      | NEW_REPLY |
      | has never replied | CREATED  |

  @FR-6d
  Scenario: Wake returns a timed snooze to the Todo detail it had before
    Given an email Thread from "ana@acme.com" with Status "TODO" and Status Detail "CREATED"
    When Act is called with Snooze until "2026-10-12T09:00:00Z"
    Then the Thread has Status "SNOOZED" and Status Detail "WAITING_FOR_DURATION"
    When Wake is called at "2026-10-12T09:01:00Z"
    Then the Thread has Status "TODO" and Status Detail "CREATED"
