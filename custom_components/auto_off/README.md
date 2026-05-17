# Auto Off

Turns off selected entities (lights, switches, fans, media_players, etc.)
after a configurable inactivity delay when a group of activity sensors
goes off.

## Setup

Settings → Devices & services → Add integration → **Auto Off**. Set the
integration's `poll_interval` (seconds). Groups are managed via actions
(see below) — every operation has a documented YAML payload, so the
whole integration is fully scriptable and idempotent without clicking
through the UI.

## Actions

> Home Assistant 2024.8+ renamed `service:` to `action:` in script /
> automation YAML. All payloads below use the modern `action:` key; the
> legacy `service:` alias also still works.

### `auto_off.set_group`

Create or update a group. Calling it for an existing `group_name`
replaces every field — there is no partial update. The action is fully
idempotent: repeated calls with the same payload converge on the same
configuration.

Fields:

- `group_name` (string, required): unique name of the group.
- `targets` (list of entity ids, required): entities to turn off.
  Only concrete entity ids in `domain.object_id` form are accepted.
  Jinja templates are **not** supported here; invalid items produce a
  WARNING in the HA log and are skipped at turn-off time.
- `sensors` (list of `binary_sensor.*` entity ids, optional): activity sensors.
- `sensor_templates` (list of Jinja strings, optional): templates rendered
  to bool. Treated identically to `sensors`.
- `delay` (int or Jinja string, optional, default 0): delay in **minutes**.
  Integer is plain minutes; a string is rendered as a template whose
  result is cast to int minutes.
- `ensure_window` (int, optional, default 60): post-deadline retry
  window in **seconds**. After the initial `turn_off` dispatch at
  deadline expiry, auto_off retries any target that is still on while
  the sensors stay off. Set to `0` to disable retries.
- `ensure_interval` (int, optional, default 10): pause in **seconds**
  between retry passes inside the ensure window. Must be `> 0`.

At least one of `sensors` or `sensor_templates` must be non-empty.

Example:

```yaml
action: auto_off.set_group
data:
  group_name: kitchen
  targets:
    - light.kitchen
  sensors:
    - binary_sensor.motion_kitchen
  delay: 5
  ensure_window: 60
  ensure_interval: 10
```

### `auto_off.delete_group`

Remove a group and every entity it spawned.

Fields:

- `group_name` (string, required).

### `auto_off.dump_group`

Return a ready-to-paste `action: auto_off.set_group` payload for an
existing group. The response is a native dict (not a wrapped YAML
string), so the HA UI renders it as a clean block under
`service_response` that copies straight into Developer Tools →
Actions or into an automation step.

Marked `supports_response: only`, so the caller must request response
data ("Show response" in Developer Tools).

Fields:

- `group_name` (string, required).

Example call:

```yaml
action: auto_off.dump_group
data:
  group_name: kitchen
response_variable: dumped
```

Example response (`dumped` in the example above):

```yaml
action: auto_off.set_group
data:
  group_name: kitchen
  targets:
    - light.kitchen
  sensors:
    - binary_sensor.motion_kitchen
  sensor_templates: []
  delay: '5'
  ensure_window: 60
  ensure_interval: 10
```

The dump always includes **every** configurable field, even when its
value matches the default, so you can edit any single field without
having to remember the rest.

#### Backup / restore / migrate workflow

The pair `dump_group` + `set_group` makes it easy to script group
maintenance from CLI / agents / other automations:

1. `dump_group` for every group you care about — store the responses.
2. To restore, edit, or migrate a group: feed the stored dict back
   into `set_group` (optionally with edits). No clicking required.

## Entities created per group

Device `Auto Off: <group_name>` with:

- `sensor.auto_off_<group_name>_deadline` — current deadline (human-
  readable) with a `deadline_iso` attribute.
- `text.auto_off_<group_name>_delay_minutes` — editable delay (supports
  templates).
- `binary_sensor.auto_off_<group_name>` — OR-group over the configured
  `sensors`. The `entity_id` attribute lists the member binary_sensors;
  the `sensor_templates` attribute lists any configured Jinja templates.
- `<domain>.auto_off_<group_name>` — one group entity per target domain
  (`light`, `switch`, `fan`, `cover`, `media_player`, `lock`, `valve`).
  Each aggregates the targets in that domain and turns them all off at
  deadline expiry.

Targets in domains without a HA group platform (e.g. `scene`) do not
get a group entity; they are turned off individually at deadline expiry.

## `auto_off_deadline` attribute on targets

When a group has an active deadline and a target is on, the integration
sets the `auto_off_deadline` attribute (ISO 8601, timezone-aware) on each
target. The attribute is cleared when the deadline is cancelled.

## Key principles

- **Sensor group = OR**: the group is active while any sensor is on/true.
  It is inactive only when all sensors are off/false.
- **Deadline exists only in one state**: deadline is allowed only when any
  target is on and all sensors are off.
- **Activity cancels the deadline**: any sensor turning on cancels the
  deadline.
- **Delay extends, never shortens**: when a target turns on while a
  deadline exists, the deadline is extended only if the new deadline
  would be later.
- **Ensure-off retry**: at deadline expiry auto_off does an initial
  `turn_off` dispatch and then runs a bounded retry loop for
  `ensure_window` seconds (default 60s), re-issuing `turn_off` every
  `ensure_interval` seconds (default 10s) on any target that is still
  on while sensors stay off. The loop aborts the moment any sensor
  reports on again. This makes the integration resilient to transient
  MQTT/Zigbee delivery failures and to brief races with other
  automations (e.g. Magic Areas Light Control), without overriding
  legitimate user / occupancy actions.
- **Recovery from attributes**: if the timer is lost (e.g. HA restart),
  the integration periodically checks `auto_off_deadline` and retries
  turning off overdue entities.

## Idempotency and scripting

Every configuration operation is exposed as an action with a stable
payload shape. There is no required UI step. Combined with
`dump_group`, this means you can:

- Back up your whole auto_off configuration into version control by
  scripting `dump_group` calls.
- Migrate or duplicate a group by feeding a `dump_group` response into
  `set_group` against another `group_name`.
- Drive auto_off from LLM agents, CI pipelines, or other automations
  that cannot click through a UI.

There is also nothing magical about the YAML format the actions accept
— it is the same payload the UI builds when you click through
Developer Tools → Actions. Pasting a `dump_group` response into the UI
in YAML mode produces a working action call.

## Configuration reference

- `poll_interval` (seconds, 5..300): integration periodic tick.
- Groups are stored inside the config entry; manage them via services.
