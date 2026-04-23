# Door Occupancy

Auto-discovers doors, locks, and covers and creates occupancy
`binary_sensor` entities that pulse on every real state change.

## Setup

Settings → Devices & services → Add integration → **Door Occupancy**. Set:

- `poll_interval` (seconds, 5..300): how often the integration scans HA for
  new door-like sources.
- `occupancy_timeout` (seconds, 1..600): how long the occupancy sensor stays
  `on` after the source state changes.

## What gets discovered

The integration creates one `binary_sensor.*_occupancy` entity for each:

- `binary_sensor.*` entity with `device_class: door`
- `lock.*` entity (any lock)
- `cover.*` entity (any cover/garage)

Discovery runs at startup and every `poll_interval` seconds. New sources
added to HA after initial setup are picked up automatically.

## Occupancy sensor behavior

Each occupancy sensor is `off` by default. When the source entity changes
state (e.g., a door opens or closes), the sensor pulses `on` for
`occupancy_timeout` seconds, then resets to `off`. A new state change while
the sensor is `on` restarts the timeout (sliding window).

States `unknown` and `unavailable` are ignored. Consecutive identical states
are ignored (no pulse).

## Entities

Device binding: each occupancy sensor is bound to the same HA device as
its source entity (if the source entity belongs to a device).

Entity id format: `binary_sensor.<source_entity_id_with_underscores>_occupancy`

Example: source `binary_sensor.front_door` → `binary_sensor.binary_sensor_front_door_occupancy`
