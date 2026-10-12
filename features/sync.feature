Feature: Sync
  As in Plain, there is no polling or reconciliation; completeness relies on event delivery.
  Connection Health (health.connection_health) is OK or BROKEN for a Connection (connections):
  BROKEN when its auth is not OK (Slack revoked the token or uninstalled the app) or it has a Last Error
  (a failed send or a rejected inbound request).

  @AS-8 @NFR-2
  Scenario Outline: A Slack revocation event makes Connection Health BROKEN
    Given a Slack Connection with auth OK
    When a "<event>" event reaches /hooks/slack with a valid signature
    Then the Connection's auth is not OK
    And Connection Health is "BROKEN"

    Examples:
      | event           |
      | tokens_revoked  |
      | app_uninstalled |

  @NFR-2
  Scenario: A failed Slack post makes Connection Health BROKEN
    Given a Slack Connection with no Last Error
    When Reply through the Slack Sender fails because Slack returns "invalid_auth"
    Then the Slack Connection has a Last Error
    And Connection Health is "BROKEN"

  @NFR-2
  Scenario: A failed email send makes Connection Health BROKEN
    Given an email Connection with no Last Error
    When Reply through the Email Sender fails because the provider returns HTTP 401
    Then the email Connection has a Last Error
    And Connection Health is "BROKEN"
