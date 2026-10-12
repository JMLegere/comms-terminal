Feature: People
  Ingest (inbox.ingest) finds or creates the Person for each sender. A Person is a row in persons with Identities
  (identities), each with a Kind ("email" or "slack_user") and a Value. A Slack sender carries a Profile Email.
  Block (inbox.block) blocks one Identity. Add Identity (inbox.add_identity) adds an email address or Slack user to a Person;
  Remove Identity (inbox.remove_identity) detaches one from its Person.

  @AS-7 @FR-10
  Scenario: Ingest creates a Person for a first-time sender
    Given no Identity has Kind "email" and Value "prospect@newco.io"
    When Ingest receives a new email from "prospect@newco.io"
    Then one Person exists with an unblocked Identity of Kind "email" and Value "prospect@newco.io"

  @AS-2 @FR-10 @FR-13
  Scenario: A Slack sender joins the Person holding their Profile Email
    Given Ingest has received a new email from "logan@agentmaple.com"
    When Ingest receives a Slack DM from "U0LOGAN" with Profile Email "logan@agentmaple.com"
    Then one Person has Identities:
      | Kind       | Value                |
      | email      | logan@agentmaple.com |
      | slack_user | U0LOGAN              |
    And that Person's record lists 2 Threads with Status "TODO"

  @AS-6 @FR-7a
  Scenario: Block blocks one address
    Given a Person with an unblocked Identity of Kind "email" and Value "alerts@signoz.io"
    When Block is called for Kind "email" and Value "alerts@signoz.io"
    Then the Identity "alerts@signoz.io" is blocked

  @FR-10
  Scenario: Add Identity adds an email address to a Person
    Given a Person with an Identity of Kind "slack_user" and Value "U0ANA"
    When Add Identity is called with Kind "email" and Value "ana@acme.com"
    Then that Person has an Identity of Kind "email" and Value "ana@acme.com"

  @FR-10 @I-6
  Scenario: Add Identity refuses an Identity that belongs to another Person
    Given a Person "Ana" with an Identity of Kind "email" and Value "ana@acme.com"
    And a Person "Bob" with an Identity of Kind "email" and Value "bob@acme.com"
    When Add Identity is called for "Bob" with Kind "email" and Value "ana@acme.com"
    Then Add Identity raises Identity Taken
    And "ana@acme.com" still belongs to "Ana"

  @FR-10 @I-6
  Scenario: Remove Identity then Add Identity moves an address to another Person
    Given a Person "Ana" with an Identity of Kind "email" and Value "ana@acme.com"
    And a Person "Bob" with an Identity of Kind "email" and Value "bob@acme.com"
    When Remove Identity is called for "ana@acme.com"
    And Add Identity is called for "Bob" with Kind "email" and Value "ana@acme.com"
    Then "ana@acme.com" belongs to "Bob"
    And the Activities sent from "ana@acme.com" are kept
