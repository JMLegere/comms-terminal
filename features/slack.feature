Feature: Slack
  Slack follows Plain (ADR 0002): a bot in selected channels and Jeremy's own DMs deliver events to /hooks/slack,
  and replies post from Jeremy's profile. Slack Verify (slack.verify) checks the signing secret. Slack Parse (slack.parse)
  turns an event into an Incoming, or nothing when the channel is not selected by Channel Rules (channel_rules);
  DMs and group DMs are always selected. Replies to a top-level channel message form their own Thread (Plain's
  one-to-one ingestion mode, #115); otherwise Ingest (inbox.ingest) groups a conversation's messages into one Thread
  until it has been quiet for 24 hours, whoever sent them (decision #126). The Slack Sender (slack.SlackSender)
  posts replies inside the Slack thread.

  @AS-13 @FR-4a @FR-4b
  Scenario: Ingest groups a Slack DM into one Thread until it has been quiet for 24 hours
    Given Ingest has received a Slack DM from "U0LOGAN" in "D0LOGAN" at "10:00"
    When Ingest receives a Slack DM from "U0LOGAN" in "D0LOGAN" at "23:30"
    Then both messages are in one Thread
    When Ingest receives a Slack DM from "U0LOGAN" in "D0LOGAN" at "2026-10-13 23:31"
    Then that message is in a new Thread

  @AS-14 @FR-4b
  Scenario: Jeremy's own top-level post creates a waiting Thread
    When Ingest receives a top-level Slack message from Jeremy in "C0EXTACME" at "10:00"
    Then a Thread is created with Status "SNOOZED" and Status Detail "WAITING_FOR_CUSTOMER"
    When Ingest receives a Slack reply from "U0ANA" in that Slack thread at "10:40"
    Then that Thread has Status "TODO" and Status Detail "NEW_REPLY"
    When Ingest receives a Slack message from "U0ANA" in "C0EXTACME" at "10:50"
    Then that message is in a new Thread

  @AS-15 @FR-4 @FR-4a
  Scenario: Slack Parse applies Channel Rules and always selects DMs
    Given Channel Rules:
      | Kind    | Value   |
      | channel | general |
      | prefix  | ext-    |
    Then Slack Parse returns an Incoming for messages in:
      | Channel   | Name     | Kind    |
      | C0GENERAL | general  | channel |
      | C0EXTACME | ext-acme | channel |
      | D0LOGAN   |          | im      |
      | G0FOUNDER |          | mpim    |
    And Slack Parse returns nothing for a message in "C0RANDOM" named "random"

  @AS-17 @FR-4c
  Scenario: The Slack Sender posts in the reply-thread from Jeremy's profile
    Given a Thread in "C0EXTACME" that began in the Slack reply-thread "1728700000.000100"
    When Reply is called with "Fixed, deploying now." through the Slack Sender
    Then Slack receives chat.postMessage with Jeremy's user token, channel "C0EXTACME", and thread_ts "1728700000.000100"

  @AS-20 @FR-2a @FR-6c
  Scenario: Ingest of a deletion stores a Delete Activity and clears the deleted message's content
    Given a Thread with Status "DONE" and Status Detail "DONE_MANUALLY_SET"
    And the Thread has a message from "U0LOGAN" with Native Id "C0EXTACME:1728700000.000100" and content "Can you send the contract?"
    When Ingest receives a deletion from "U0LOGAN" of "C0EXTACME:1728700000.000100"
    Then the Thread has a Delete Activity targeting that message
    And that message has no content
    And the Thread has Status "TODO" and Status Detail "NEW_REPLY"

  @NFR-4
  Scenario: Slack Verify rejects an event with a bad signature
    When a request reaches /hooks/slack with a wrong X-Slack-Signature
    Then the response is 401
    And no Activity or Thread is created
    And the Slack Connection has a Last Error
