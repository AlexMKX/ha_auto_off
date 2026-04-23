# Auto Off (Home Assistant custom integration)

**Auto Off** turns off selected entities after a configurable inactivity
delay when a group of activity sensors goes off. See
[custom_components/auto_off/README.md](custom_components/auto_off/README.md)
for details.

## Companion integration

Occupancy sensors used to live in this repository as `door_occupancy`.
Because HACS cannot install two integrations from a single repository, it
now has its own repo:

- **Door Occupancy** — auto-discovers doors, locks, and covers and creates
  `binary_sensor.*_occupancy` entities. Repo:
  [AlexMKX/ha_door_occupancy](https://github.com/AlexMKX/ha_door_occupancy).

The two integrations are fully independent (separate domains, config
flows, services, and config entries). Install whichever ones you need.

## Installation

### HACS

1. Add this repo (`https://github.com/AlexMKX/ha_auto_off`) to HACS as a
   Custom Repository (category: Integration).
2. *(Optional, for occupancy sensors)* Add
   `https://github.com/AlexMKX/ha_door_occupancy` as a second Custom
   Repository (category: Integration).
3. Install **Auto Off** (and **Door Occupancy** if added).
4. Restart Home Assistant.
5. Settings → Devices & services → Add integration → **Auto Off**
   (and/or **Door Occupancy**).

### Manual

Copy `custom_components/auto_off/` into `<config>/custom_components/` and
restart Home Assistant. For Door Occupancy, clone the companion repo and
copy `custom_components/door_occupancy/` similarly.

## Migration from the unified 2512.x release

Starting with release `2604231655`, the integration is split. This is a
**breaking change** for users of the old unified `auto_off`.

1. Update HACS to the latest Auto Off release (auto_off v2 config entries
   are now migrated to v3 automatically; legacy `*_occupancy` entities
   that used to be owned by auto_off are cleaned from the registry on
   next restart).
2. *(Optional)* Install **Door Occupancy** from the companion repo if you
   want auto-discovered occupancy sensors.
3. If you recreated groups from scratch, use the new `auto_off.set_group`
   payload shape:

   ```yaml
   # before (unified 2512.x)
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

   If you previously embedded Jinja templates in the sensor list
   (detected by the presence of `{{`), move those into the new
   `sensor_templates` field.

   Note: the `targets` field accepts **only concrete entity ids**
   (`domain.object_id`). Jinja templates in `targets` are no longer
   supported. If you previously used template targets, replace them with
   the resolved entity ids and call `auto_off.set_group` with the updated
   payload.
