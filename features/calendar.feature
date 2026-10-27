Feature: Calendar
  Jeremy Responds to invites from the console (FR-13e, ADR 0009; requirements FR-IR-1–11 in
  .agents/prd/2026-10-27-invite-responses.md). Respond (serve.handle_respond) finds the Event of a Meeting Thread
  by its Channel's Event UID, or of an invitation email Thread by the UID in the message's stored calendar part,
  and asks the Google Calendar Writer (gcal.Client) to set Jeremy's own response; Google tells the organizer.
  A confirmed Response adds a Meeting Activity from Jeremy (meetings.respond_recorded) and leaves Status unchanged.
  The Google Calendar Writer here is a fake that records each request; nothing reaches Google.

  @wip @AS-IR-1 @FR-13e @FR-IR-1 @FR-IR-4 @FR-IR-6 @FR-IR-7
  Scenario: Jeremy accepts an invite from its Meeting Thread
    Given a Meeting Thread for the Event "Pints" organized by "brendan.wood@snaptrade.com" with Jeremy's Invite state "Unanswered"
    And the Thread has Status "TODO" and Status Detail "CREATED"
    When Jeremy Responds "Accept" on that Thread
    Then the Google Calendar Writer sets Jeremy's response to "accepted" for the whole Event and notifies every guest
    And the outcome is "confirmed"
    And the Thread has a Meeting Activity from Jeremy saying "Accepted · Pints"
    And the Thread has Status "TODO" and Status Detail "CREATED"

  @wip @AS-IR-2 @FR-13e @FR-IR-2 @FR-IR-4
  Scenario: Jeremy declines an invite from its invitation email Thread
    Given an invitation email Thread whose stored message carries a calendar part for the Event "NBIF review" organized by "laila.theriault@nbif.ca"
    And a Meeting Thread for the same Event
    When Jeremy Responds "Decline" on the invitation email Thread
    Then the Google Calendar Writer sets Jeremy's response to "declined" for the Event with that UID
    And the outcome is "confirmed"
    And the Meeting Thread has a Meeting Activity from Jeremy saying "Declined · NBIF review"

  @wip @AS-IR-3 @FR-13e @FR-IR-5
  Scenario: A Response on a repeating series answers the whole series
    Given a Meeting Thread for the weekly Event "Serenity touch-in"
    When Jeremy Responds "Maybe" on that Thread
    Then the Google Calendar Writer sets Jeremy's response to "tentative" for the series, not one occurrence

  @wip @AS-IR-4 @FR-13e @FR-IR-8
  Scenario: Jeremy cannot Respond to an Event he organized
    Given a Meeting Thread for an Event organized by Jeremy
    When Jeremy Responds "Accept" on that Thread
    Then the outcome is "refused" with the reason "You organized this Event."
    And the Google Calendar Writer received no request
    And the Thread has no new Activity

  @wip @AS-IR-5 @FR-13e @FR-IR-9
  Scenario: A Response whose outcome is unknown is recorded nowhere and never retried
    Given a Meeting Thread for the Event "Pints" organized by "brendan.wood@snaptrade.com"
    And the Google Calendar Writer times out
    When Jeremy Responds "Accept" on that Thread
    Then the outcome is "unknown"
    And the Google Calendar Writer received exactly one request
    And the Thread has no new Activity

  @wip @AS-IR-6 @FR-13e @FR-IR-10
  Scenario: A revoked sign-in makes Responses unavailable
    Given a Meeting Thread for the Event "Pints" organized by "brendan.wood@snaptrade.com"
    And Google refuses the refresh token
    When Jeremy Responds "Accept" on that Thread
    Then the outcome is "unavailable"
    And Google Calendar writes health is broken
    And the Thread has no new Activity

  @wip @AS-IR-7 @FR-13e @FR-IR-1
  Scenario: Escape after opening the Response picker changes nothing
    Given a Meeting Thread for the Event "Pints" organized by "brendan.wood@snaptrade.com"
    When Jeremy opens the Response picker on that Thread and presses Escape
    Then the Google Calendar Writer received no request
    And the Thread has no new Activity
