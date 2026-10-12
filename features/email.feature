Feature: Email
  Email follows Plain (ADR 0002): inbound mail is forwarded to /hooks/email, and replies go out through the outbound mail provider.
  Email Verify (email.verify) checks the HMAC signature. Email Parse (email.parse) turns a raw message into an Incoming,
  or nothing for an auto-reply. Ingest (inbox.ingest) threads it by its Thread Ref (In-Reply-To and References).
  The Email Sender (email.EmailSender) sends Jeremy's replies.

  @AS-4 @FR-3
  Scenario: Ingest adds a reply to the Thread its Thread Ref names
    Given a Thread whose first message has Message-ID "<a1@newco.io>"
    When Ingest receives an email from "prospect@newco.io" with In-Reply-To "<a1@newco.io>"
    Then no new Thread is created
    And that Thread has 2 Activities

  @AS-3 @FR-3b @wip
  Scenario: The Email Sender sends a threaded reply-all
    Given a Thread whose last message "<a1@newco.io>" came from "prospect@newco.io" with CC "ops@newco.io"
    When Reply is called with "Thanks, booked for Tuesday." through the Email Sender
    Then the outbound mail provider receives a message from "jeremy@agentmaple.com" to "prospect@newco.io" with CC "ops@newco.io"
    And the message has In-Reply-To "<a1@newco.io>"

  @AS-11 @FR-3a @wip
  Scenario Outline: Email Parse returns nothing for each Plain auto-reply header
    When Email Parse reads a message with header "<name>: <value>"
    Then Email Parse returns nothing

    Examples:
      | name                                   | value               |
      | Auto-Submitted                         | auto-replied        |
      | Precedence                             | auto_reply          |
      | Precedence                             | bulk                |
      | X-Autoreply                            | yes                 |
      | X-Autorespond                          | yes                 |
      | X-Amazon-Auto-Reply                    | yes                 |
      | X-amzn-vacation                        | yes                 |
      | X-SFDC-AutoResponse                    | yes                 |
      | X-ServiceNow-Generated                 | true                |
      | X-Front-Autoreply                      | yes                 |
      | X-C2AutoRespond                        | yes                 |
      | X-MDAutoResponse                       | yes                 |
      | X-QQ-Auto-Reply                        | yes                 |
      | X-MS-Exchange-Generated-Message-Source | Mailbox Rules Agent |

  @AS-11 @FR-3a @wip
  Scenario: Email Parse keeps a message with Auto-Submitted no
    When Email Parse reads a message with header "Auto-Submitted: no"
    Then Email Parse returns an Incoming

  @AS-11 @FR-3a @wip
  Scenario: Email Parse keeps an out-of-office subject without a header signal
    When Email Parse reads a message with subject "Out of office: back Monday" and no auto-reply header
    Then Email Parse returns an Incoming

  @AS-19 @NFR-4 @wip
  Scenario: Email Verify rejects a request without a valid signature
    When a request reaches /hooks/email with a wrong X-Inbox-Signature
    Then the response is 401
    And no Activity or Thread is created
    And the email Connection has a Last Error
