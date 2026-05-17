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

## Entities created per group

Each auto_off group creates a Home Assistant device `Auto Off: <group_name>` with the following entities:

- `sensor.auto_off_<group>_deadline` — current deadline (human-readable)
  with a `deadline_iso` attribute.
- `text.auto_off_<group>_delay_minutes` — editable delay (supports templates).
- `binary_sensor.auto_off_<group>_sensors` — OR-group over the configured
  `sensors`. Its `entity_id` attribute lists the member binary_sensors;
  `sensor_templates` attribute lists any configured Jinja templates.
- `<domain>.auto_off_<group>_targets_<domain>` — one group entity per
  target domain (e.g. `light`, `switch`, `fan`, `cover`, `media_player`,
  `lock`, `valve`). Each aggregates the targets in that domain and turns
  them all off at deadline expiry.

Targets in domains without a HA group platform (e.g. `scene`) do not get
a group entity; they are turned off individually at deadline expiry.

> **Breaking change (since group entities):** The `extra_state_attributes`
> of the deadline sensor no longer includes `targets`, `sensors`, or
> `sensor_templates`. If you have automations reading those attributes,
> switch to the new group entities listed above.

## Migration from the unified 2512.x release

Starting with release `2604231655`, the integration is split. This is a
**breaking change** for users of the old unified `auto_off`.

1. Update HACS to the latest Auto Off release. v2 config entries are
   migrated to v3 automatically on restart. Any legacy `*_occupancy`
   entities that used to be owned by auto_off will remain as orphaned
   entries in the entity registry — remove them manually via
   Settings → Devices & services → Entities, or install **Door Occupancy**
   which will recreate them with the same unique_ids and take ownership.
2. *(Optional)* Install **Door Occupancy** from the companion repo if you
   want auto-discovered occupancy sensors.
3. If you recreated groups from scratch, use the new `auto_off.set_group`
   payload shape:

   ```yaml
   # before (unified 2512.x)
   action: auto_off.set_group
   data:
     group_name: kitchen
     config: |
       sensors:
         - binary_sensor.motion_kitchen
       targets:
         - light.kitchen
       delay: 5

   # after
   action: auto_off.set_group
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

## Idempotent configuration via actions

Every operation on auto_off is exposed as an action with a stable YAML
payload (`auto_off.set_group`, `auto_off.delete_group`,
`auto_off.dump_group`). There is no required UI clicking — groups can
be created, edited, dumped, backed up, migrated, or driven by LLM
agents and CI pipelines.

See
[custom_components/auto_off/README.md](custom_components/auto_off/README.md#actions)
for the full action reference and the dump → edit → set workflow.
