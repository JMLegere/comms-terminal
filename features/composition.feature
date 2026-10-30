Feature: Composition
  Draft is unsent work, not a Thread or an Activity. Draft revision is the saved version.
  Save Draft (inbox.save_draft), Get Draft (inbox.get_draft), and List Drafts (inbox.list_drafts)
  persist and resume the complete edited fields. The latest save wins (ADR 0012): an edit made after the stored save applies over a newer revision, and Draft Conflict (inbox.DraftConflict) rejects one made before it, from the console, the CLI or an agent.
  Connection maps to connection_id, Message format to kind, Sender to sender, To to to,
  Cc to cc, Subject to subject, and Body to body. "none" means no Connection or no SMS Subject;
  every other field remains the exact edited string.
  Recipient History (inbox.recipient_threads) reads canonical Captured history by Identity Kind and Value,
  whether or not a Person is linked. Captured history makes no claim about external messages.
  Ingest (inbox.ingest) and Remove Identity (inbox.remove_identity) arrange captured records.

  @AS-22 @FR-18 @FR-20 @NFR-10 @NFR-12 @I-7 @I-12
  Scenario Outline: Edited partial Draft resumes after closing and reopening
    Given a new Draft has fields:
      | Field          | Value        |
      | Connection     | <connection> |
      | Message format | <format>     |
      | Sender         | <sender>     |
      | To             | <to>         |
      | Cc             |              |
      | Subject        | <subject>    |
      | Body           | first text   |
    When Save Draft is called
    And Save Draft is called with Body "edited text" at Draft revision 1
    And the Draft store closes and reopens
    And Get Draft is called for that Draft
    Then that Draft has Draft revision 2 and fields:
      | Field          | Value        |
      | Connection     | <connection> |
      | Message format | <format>     |
      | Sender         | <sender>     |
      | To             | <to>         |
      | Cc             |              |
      | Subject        | <subject>    |
      | Body           | edited text  |
    And List Drafts returns that Draft
    And no Thread or Activity was created

    Examples:
      | connection | format | sender       | to           | subject |
      | none       | email  |              | unfinished@  |         |
      | 2          | sms    | +15550001001 | +15550001002 | none    |

  @AS-22 @FR-20 @NFR-10 @I-7 @I-12
  Scenario: An edit made before newer saved work cannot overwrite it
    Given a saved email Draft with Body "first saved text"
    When another writer calls Save Draft with Body "newer saved text" at Draft revision 1
    And Save Draft is called with Body "unsaved edits" at Draft revision 1, made before that save
    Then Save Draft raises Draft Conflict
    And Get Draft reads Body "newer saved text" and Draft revision 2
    And the edited Draft Body remains "unsaved edits"
    And no Thread or Activity was created

  @AS-22 @FR-20
  Scenario: The latest save wins over a newer Draft revision
    Given a saved email Draft with Body "first saved text"
    When another writer calls Save Draft with Body "newer saved text" at Draft revision 1
    And Save Draft is called with Body "latest words" at Draft revision 1, made after that save
    Then Get Draft reads Body "latest words" and Draft revision 3
    And no Thread or Activity was created

  @AS-29 @FR-19 @NFR-12 @I-8
  Scenario Outline: Unlinked outgoing destination finds captured work without creating a Person
    Given a captured outgoing Thread for Kind "<kind>" and Value "<value>" without a Person
    When Recipient History is called for Kind "<kind>" and Value "<query>"
    Then Recipient History returns that canonical Thread
    And captured records and Person associations are unchanged
    And the captured outgoing Activity has no Identity

    Examples:
      | kind  | value         | query         |
      | email | raw@acme.test | RAW@ACME.TEST |
      | phone | +15550001002  | +15550001002  |

  @AS-29 @FR-19 @NFR-12
  Scenario Outline: Detached incoming Identity still finds captured work
    Given a captured incoming Thread for Kind "<kind>" and Value "<value>" with a detached Identity
    When Recipient History is called for Kind "<kind>" and Value "<value>"
    Then Recipient History returns that canonical Thread
    And captured records and Person associations are unchanged
    And that Identity remains detached

    Examples:
      | kind  | value         |
      | email | ana@acme.test |
      | phone | +15550001002  |
