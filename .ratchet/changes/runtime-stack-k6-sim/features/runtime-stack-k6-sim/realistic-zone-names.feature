Feature: Realistic warehouse zone ids replace the generic zone-NN ids
  As a floor manager reading the dashboard zone tiles
  I want the backend ZONES to be the real warehouse zone names rather than
  generic zone-01..zone-20 placeholders
  So that the dashboard, the seeded counts, and the k6 simulation all refer to
  meaningful, locatable zones

  Background:
    Given the backend ZONES constant in app/models.py is the single source of
      truth for zone ids
    And seed_zones() inserts one zone_counts row per id in ZONES

  Scenario: ZONES is the 20 realistic warehouse zone ids in the given order
    Given the ZONES constant
    When it is read
    Then it is exactly, in order: inbound_dock_a, inbound_dock_b,
      receiving_staging, aisle_a, aisle_b, aisle_c, high_bay_1, high_bay_2,
      bulk_storage, pick_zone_1, pick_zone_2, pack_station, sort_belt,
      outbound_dock_a, outbound_dock_b, shipping_staging, charging_bay_1,
      charging_bay_2, charging_bay_3, maintenance_bay
    And there are no generic zone-NN ids remaining in ZONES

  Scenario: Seeding flows automatically from the renamed constant
    Given the migrations and zone seeding run on startup
    When seed_zones() runs against a fresh database
    Then it inserts one zone_counts row for each realistic zone id, idempotently
    And GET /zones/counts returns all 20 realistic zones, each starting at 0

  Scenario: Every hardcoded zone-NN reference is updated to the new names
    Given the existing tests, fixtures, and helpers that previously referenced
      zone-NN ids (e.g. tests/integration/test_zone_counts.py,
      tests/integration/test_zone_increment.py and the other integration and
      web tests that assert on zone ids)
    When the suite runs after the rename
    Then those references use the realistic zone ids instead of zone-NN
    And the backend and frontend test suites stay green

  Scenario: The k6 simulation draws zone_entered from the realistic ids
    Given the k6 fleet simulation sets zone_entered on a crossing event
    When a vehicle crosses into a zone
    Then zone_entered is one of the realistic ZONES ids, so the increment lands
      on a seeded zone and the matching dashboard zone tile moves under load
