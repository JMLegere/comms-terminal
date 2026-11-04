Feature: Pipeline
  The Revenue Pipeline holds Organizations; the Fundraising and Talent Pipelines hold People (ADR 0016). Set Stage
  (pipeline.set_stage) and Set Value (pipeline.set_value) append to the Pipeline log; the Pipeline math
  (pipeline.math) lays each one out under its Stage, and a Person's card names their Organizations (Memberships).

  @FR-PL @FR-OA
  Scenario: A Person who works for a fund sits in the Fundraising Pipeline under their own name
    Given a Person "Laïla Thériault" with address "laila@nbif.ca"
    When Jeremy sets the Fundraising Stage of "laila@nbif.ca" to "Soft commit" and the Value to "100k"
    Then the Fundraising Pipeline shows "Laïla Thériault" at "Soft commit" with Value 100000 and Organizations "nbif.ca"
    And the Organization "nbif.ca" holds no Fundraising Stage

  @FR-PL
  Scenario: The Revenue Pipeline still holds Organizations
    Given a Person "Ana" with address "ana@acme.com"
    When Jeremy sets the Revenue Stage of the Organization "acme.com" to "Commit"
    Then the Revenue Pipeline shows "acme.com" at "Commit"
