@wip
Feature: Organizations
  Ingest (inbox.ingest) creates an Organization (organizations, unique Domain) for a new sender's email domain,
  except free-mail domains. A Person's Organizations are those whose Domain matches one of the Person's email addresses.

  @AS-12 @FR-11
  Scenario: Ingest creates an Organization for a company domain
    Given no Organization has Domain "acme.com"
    When Ingest receives a new email from "ana@acme.com"
    Then one Organization exists with Domain "acme.com"
    And the Person with address "ana@acme.com" belongs to the Organization "acme.com"

  @AS-12 @FR-11
  Scenario: Ingest creates no Organization for a free-mail domain
    When Ingest receives a new email from "bob@gmail.com"
    Then a Person with address "bob@gmail.com" exists
    And no Organization has Domain "gmail.com"
