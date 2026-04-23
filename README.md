# Auto Off + Door Occupancy (Home Assistant custom integrations)

This repository ships two independent custom integrations:

- **Auto Off** — turns off selected entities after a configurable inactivity
  delay when a group of activity sensors goes off. See
  [custom_components/auto_off/README.md](custom_components/auto_off/README.md).
- **Door Occupancy** — auto-discovers doors, locks, and covers, and creates
  `binary_sensor.*_occupancy` entities that pulse on every real state change.
  See [custom_components/door_occupancy/README.md](custom_components/door_occupancy/README.md).

The two integrations are fully independent: they have separate domains,
config flows, services, and config entries. Install whichever ones you need.

## Installation

- **HACS**: add this repo as a Custom Repository (Integration), install it,
  then restart Home Assistant. Both integrations become available under
  Settings → Devices & services → Add integration.
- **Manual**: copy `custom_components/auto_off/` and/or
  `custom_components/door_occupancy/` into `<config>/custom_components/`,
  then restart Home Assistant.

## Migration from the unified 2512.x release

Starting with the next release, the previous unified `auto_off` integration
is split. This is a **breaking change**. To upgrade:

1. Home Assistant → Settings → Devices & services → Auto Off → **Delete**.
   Existing entities from the old integration are removed:
   - `sensor.auto_off_*_deadline`, `text.auto_off_*_delay_minutes`
   - `sensor.auto_off_*_config` (no longer exists in the new version)
   - `binary_sensor.*_occupancy` (now owned by Door Occupancy)
2. Update the HACS repository. After HA restarts, add the integrations
   separately:
   - Settings → Devices & services → Add integration → **Auto Off**.
     Configure `poll_interval`, then recreate groups via the
     `auto_off.set_group` service (see below for the new payload).
   - Settings → Devices & services → Add integration → **Door Occupancy**.
     Configure `poll_interval` and `occupancy_timeout`. Occupancy sensors
     are created automatically on the first discovery tick.
3. Update any script or automation that called `auto_off.set_group` with
   a YAML string to the new structured shape:

   ```yaml
   # before
   service: auto_off.set_group
   data:
     group_name: kitchen
     config: |
       sensors:
         - binary_sensor.motion_kitchen
       targets:
         - light.kitchen
       delay: 5

   # after
   service: auto_off.set_group
   data:
     group_name: kitchen
     targets:
       - light.kitchen
     sensors:
       - binary_sensor.motion_kitchen
     delay: 5
   ```

   If you previously embedded Jinja templates in the sensor list (detected
   by the presence of `{{`), move those into the new `sensor_templates`
   field.

Old `binary_sensor.*_occupancy` entities from the previous integration are
removed when the old entry is deleted. The new Door Occupancy integration
recreates them with the same `unique_id` format, so HA assigns the same
default entity id string — unless you had renamed them, in which case the
custom name is not restored (registry record is gone).
