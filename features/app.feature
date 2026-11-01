Feature: App
  The App is a native desktop Client that works through the Local core (ADR 0013,
  `.agents/prd/2026-10-31-desktop-app.md`). The Local core (core.Core) holds the laptop copy and the write queue
  and serves the Local API (core.serve). Message Body (core query "message.body") is a message's HTML with every
  remote fetch removed (render.safe_html). Tags name the feature PRD's acceptance scenarios (AS-APP-n) and
  requirements (FR-APP-n, NFR-APP-n), numbered as AS-n, FR-n and NFR-n in that PRD.

  @wip @AS-APP-1 @FR-APP-7 @FR-APP-8 @NFR-APP-4
  Scenario: A newsletter shows its inline images and holds back remote ones
    Given an email whose HTML has an inline image "logo@x" and a remote image with alt "Weekly deals"
    When the App opens its Message Body
    Then the inline image shows
    And "Weekly deals" shows where the remote image was
    And no network request leaves the App
    When Jeremy chooses Load images
    Then the remote image shows

  @AS-APP-2 @FR-APP-7 @NFR-APP-4
  Scenario: A Message Body makes no request for any remote reference
    Given an email whose HTML uses a stylesheet link, CSS url(), srcset, an iframe and a script
    When the App opens its Message Body
    Then a logging HTTP server receives no request

  @wip @AS-APP-3 @FR-APP-9
  Scenario: One click opens an Attachment and one click opens a link
    Given a Thread whose email has a PDF Attachment and a link
    When Jeremy clicks the Attachment
    Then the PDF opens through its signed link
    When Jeremy clicks the link
    Then the default browser opens it

  @wip @AS-APP-4 @FR-APP-10 @NFR-APP-2
  Scenario: A file dropped on a reply is staged on it
    Given a Thread with a Reply Draft open in the App
    When Jeremy drops "contract.pdf" from Mani's disk on the reply
    Then the Reply Draft shows "contract.pdf" within 2 seconds

  @wip @AS-APP-5 @FR-APP-2 @FR-APP-5
  Scenario: A write made just before the App quits still reaches the Service
    Given a Thread in Status "TODO"
    When Jeremy marks it Done in the App and quits the App at once
    Then the Service's Thread has Status "DONE"

  @wip @AS-APP-6 @NFR-APP-2
  Scenario: An agent's Draft edit shows in the open App
    Given a Draft open in the App
    When an agent saves new text for that Draft through the CLI
    Then the App shows the agent's text within 2 seconds

  @wip @AS-APP-7 @FR-APP-11
  Scenario: A broken key file keeps the last good keys
    Given the App running with its default keys
    When an agent writes an invalid "keys.json"
    Then the App keeps its previous keys and writes "keys.error"
    When Jeremy presses the escape key combination
    Then the App restores its default layout and keys

  @AS-APP-8 @FR-APP-13
  Scenario: A Panel cannot send
    Given a Panel that calls the send command for a Draft
    When the Panel runs
    Then the Local core refuses the call for the Panel Origin
    And the Draft is not sent

  @wip @AS-APP-9 @FR-APP-14 @FR-APP-15
  Scenario: A new Service build makes the App read-only until it updates
    Given the App and the Local core on the Service's build
    When the Service reports a different build
    Then the App shows read-only and refuses writes
    And the update installs the Service's build

  @wip @AS-APP-10 @FR-APP-14
  Scenario: Offline, reads work and writes are refused
    Given the Local core cannot reach the Service
    When Jeremy opens a Thread and then marks it Done
    Then the Thread shows
    And the write is refused with the read-only banner

  @AS-APP-11 @FR-APP-6
  Scenario: Every Console key has an App key or a reason
    Given the Console's key bindings
    Then each one maps to a key in the App's default "keys.json" or is listed with a reason

  @AS-APP-12 @FR-APP-4 @NFR-APP-6
  Scenario: A breaking change to the Local API fails its check
    Given the released Local API document
    When a released method loses a parameter
    Then the compatibility check fails
