Feature: Dashboard loading, error, and empty states
  As a fleet operator
  I want clear feedback while data loads, on error, or when no data exists
  So that I understand the current dashboard state instead of seeing a blank page

  Scenario: Loading state is shown while snapshots are fetched
    Given the dashboard has mounted
    And the initial REST snapshots have not yet returned
    When the dashboard renders
    Then a loading indicator is visible
    And the loading indicator communicates that vehicle and zone data is being loaded

  Scenario: Error state is shown when snapshot fetch fails
    Given the dashboard has mounted
    When the initial REST snapshots return an error
    Then an error message is visible
    And the error message indicates that fleet data could not be loaded

  Scenario: Empty state is shown when no vehicles exist
    Given the dashboard has loaded
    And the vehicles snapshot returns an empty list
    When the vehicles section renders
    Then an empty-state message is visible
    And the message indicates that no vehicles are currently available

  Scenario: Empty state is shown when no zones exist
    Given the dashboard has loaded
    And the zones snapshot returns an empty list
    When the zones section renders
    Then an empty-state message is visible
    And the message indicates that no zones are currently configured
