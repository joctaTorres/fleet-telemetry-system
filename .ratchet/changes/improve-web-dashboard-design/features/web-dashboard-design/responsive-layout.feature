Feature: Dashboard responsive layout and visual hierarchy
  As a fleet operator
  I want the dashboard to use a clear, responsive layout with proper spacing
  So that I can scan vehicles and zones quickly on any screen size

  Background:
    Given the dashboard has loaded vehicle and zone data

  Scenario: Zones and vehicles stack vertically on narrow viewports
    Given the viewport is narrower than 640px
    When the dashboard renders the vehicles section and zones section
    Then the zones section appears below the vehicles section
    And both sections fill the full width of the viewport

  Scenario: Zones and vehicles appear side-by-side on wide viewports
    Given the viewport is at least 1024px wide
    When the dashboard renders the vehicles section and zones section
    Then the vehicles section and zones section appear in a single horizontal row
    And there is visible whitespace between the two sections

  Scenario: Sections have clear labels and visual separation
    When the dashboard renders the vehicles section and zones section
    Then each section has a visible heading
    And each section is visually grouped with consistent internal spacing
