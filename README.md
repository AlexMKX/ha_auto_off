# Auto Off (Home Assistant custom integration)

This integration solves two practical problems:

- **Auto Off**: automatically turns off selected devices (lights/switches/etc) after a configured delay when the “activity” sensor group becomes inactive.
- **Door Occupancy**: automatically creates an `occupancy` `binary_sensor` for doors/locks/covers, producing a short “activity pulse” on every state change.

## Rationale

- **[Auto-off without automation spaghetti]** Instead of many repetitive automations, you define groups once: “activity present → never turn off”, “no activity → start timer and turn off”.
- **[Timer transparency]** For each target entity, the integration sets an `auto_off_deadline` attribute with the real (wall-clock) planned turn-off time.
- **[Standardized door events]** Door/lock/cover state changes are often used as “someone came/left” triggers. This provides a ready-to-use occupancy pulse sensor.

## Installation

- **HACS**: add this repo as a Custom Repository (Integration), install it, then restart Home Assistant.
- **Manual**: copy `custom_components/auto_off` into `<config>/custom_components/auto_off`, then restart Home Assistant.

## Initial setup

1. `Settings` → `Devices & services` → `Add integration` → `Auto Off`.
2. Set `poll_interval` (seconds). It is used for periodic worker ticks/discovery (for both `auto_off` and `door_occupancy`).

Notes:

- The integration is designed as a **single instance**.
- YAML is supported as a legacy import path, but the current recommended approach is UI + services.

## Usage: Auto Off (groups)

At the moment, groups are created/updated via services.

- **Create/update a group**: service `auto_off.set_group`
  - `group_name` (string)
  - `sensors` (list of `entity_id`) — activity sensors
  - `targets` (list of `entity_id`) — entities to turn off
  - `delay` (int or template string) — delay **in minutes**

- **Delete a group**: service `auto_off.delete_group` (`group_name`)

Example call for `auto_off.set_group` (Developer Tools → Services):

```yaml
group_name: kitchen
sensors:
  - binary_sensor.motion_kitchen
targets:
  - light.kitchen
delay: 5
```

### Entities created for a group

For each group, a dedicated device `Auto Off: <group_name>` is created, with these entities:

- **[Config]** `sensor` — short summary (number of sensors/targets and delay)
- **[Deadline]** `sensor` — current deadline (human-readable) + `deadline_iso` attribute
- **[Delay (minutes)]** `text` — edit `delay` from UI (can remain a template)

### `auto_off_deadline` attribute on targets

When a group has an active deadline and a target is `on`, the integration sets/updates:

- `auto_off_deadline`: ISO 8601 timestamp (timezone-aware)

If a target is off, or there is no deadline, the attribute is cleared (`None`).

## Key principles: Auto Off

- **[Sensor group = OR]** The group is considered active if **at least one** sensor is `on/true`. It is inactive only if **all** sensors are `off/false`.
- **[Deadline exists only in one state]** A deadline is allowed only when:
  - targets are on (at least one is `on`)
  - and all sensors are off
- **[Activity cancels the deadline]** Any activity (any sensor becomes `on`) cancels the deadline.
- **[Delay is in minutes]** `delay` is configured in minutes (number or a Jinja template that must render to minutes). Internally it is converted to seconds.
- **[Deadline extension on target on]** If sensors are off, a deadline already exists, and a target turns on, the deadline is extended only if the new deadline would be later (never shortened).
- **[Recovery based on attributes]** If the timer is lost (e.g., after restart) or a `turn_off` call didn’t reach the device, the integration periodically checks `auto_off_deadline` and retries turning off overdue entities.

## Usage: Door Occupancy

The integration automatically discovers and creates occupancy sensors for:

- `binary_sensor.*` with `device_class: door`
- all `lock.*`
- all `cover.*`

For each discovered entity, it creates `binary_sensor` “`<source_entity_id> Occupancy`”.

## Key principles: Door Occupancy

- **[Activity pulse]** On each real state change of the source entity, the occupancy sensor turns `on` and stays on for **15 seconds**, then turns off.
- **[Timer restart on new events]** New events restart the timer.
- **[Device binding]** The occupancy sensor is bound to the same HA device as the source door/lock/cover.
- **[Auto-discovery]** New doors/locks/covers are picked up periodically with `poll_interval`.

## Configuration

- `poll_interval` (**seconds**, 5..300): integration periodic tick.
- Groups (`group_name`/`sensors`/`targets`/`delay`) are stored in the config entry and managed via services.
