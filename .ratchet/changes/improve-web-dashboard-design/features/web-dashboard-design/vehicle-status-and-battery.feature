Feature: Vehicle status and battery visualization
  As a fleet operator
  I want each vehicle's status and battery level to be easy to read at a glance
  So that I can spot operational state and charge level without effort

  Background:
    Given the dashboard has loaded vehicle data

  Scenario Outline: Vehicle status is rendered as a styled badge
    Given a vehicle with status "<status>"
    When its row is rendered
    Then the status value is displayed inside a styled badge element
    And the badge text matches "<status>"

    Examples:
      | status   |
      | moving   |
      | idle     |
      | fault    |
      | offline  |

  Scenario: Battery percentage is rendered with a progress bar
    Given a vehicle with battery level 67%
    When its row is rendered
    Then the battery text "67%" is visible
    And a progress bar representing 67% battery is visible
    And the progress bar has an accessible label describing the battery percentage

  Scenario: Low battery is visually distinct
    Given a vehicle with battery level 12%
    When its row is rendered
    Then the battery bar is styled as a low-battery state
    And the low-battery state is accompanied by visible text so color is not the only signal
