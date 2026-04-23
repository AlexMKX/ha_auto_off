# Auto Off

Turns off selected entities (lights, switches, fans, media_players, etc.)
after a configurable inactivity delay when a group of activity sensors
goes off.

## Setup

Settings → Devices & services → Add integration → **Auto Off**. Set the
integration's `poll_interval` (seconds). Groups are managed via services.

## Services

### `auto_off.set_group`

Fields:

- `group_name` (string, required): unique name of the group.
- `targets` (list of entity ids, required): entities to turn off.
- `sensors` (list of `binary_sensor.*` entity ids, optional): activity sensors.
- `sensor_templates` (list of Jinja strings, optional): templates rendered
  to bool. Treated identically to `sensors`.
- `delay` (int or Jinja string, optional, default 0): delay in minutes.
  Integer is plain minutes; a string is rendered as a template whose result
  is cast to int minutes.

At least one of `sensors` or `sensor_templates` must be non-empty.

Example:

```yaml
service: auto_off.set_group
data:
  group_name: kitchen
  targets:
    - light.kitchen
  sensors:
    - binary_sensor.motion_kitchen
  delay: 5
```

### `auto_off.delete_group`

Fields:

- `group_name` (string, required).

## Entities created per group

Device `Auto Off: <group_name>` with:

- `sensor.auto_off_<group_name>_deadline` — current deadline (human-readable)
  with a `deadline_iso` attribute.
- `text.auto_off_<group_name>_delay_minutes` — editable delay (supports
  templates).

## `auto_off_deadline` attribute on targets

When a group has an active deadline and a target is on, the integration
sets the `auto_off_deadline` attribute (ISO 8601, timezone-aware) on each
target. The attribute is cleared when the deadline is cancelled.

## Key principles

- **Sensor group = OR**: the group is active while any sensor is on/true.
  It is inactive only when all sensors are off/false.
- **Deadline exists only in one state**: deadline is allowed only when any
  target is on and all sensors are off.
- **Activity cancels the deadline**: any sensor turning on cancels the deadline.
- **Delay extends, never shortens**: when a target turns on while a deadline
  exists, the deadline is extended only if the new deadline would be later.
- **Recovery from attributes**: if the timer is lost (e.g., HA restart), the
  integration periodically checks `auto_off_deadline` and retries turning
  off overdue entities.

## Configuration reference

- `poll_interval` (seconds, 5..300): integration periodic tick.
- Groups are stored inside the config entry; manage them via services.
