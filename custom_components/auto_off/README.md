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

At least one of `sensors` or `sensor_templates` must be non-empty.

The ensure-off retry behavior (described under "Key principles") is
governed by two module-level constants in
`custom_components/auto_off/auto_off.py`
(`ENSURE_WINDOW_SEC = 60`, `ENSURE_INTERVAL_SEC = 10`). They are not
exposed as per-group settings; production data showed a single value
works for every group.

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
```

### Target group expansion

Targets that are HA group entities (`light.*` groups, helper `group:`,
Magic Areas light groups, switch/cover/fan/media_player/lock/valve groups)
are expanded recursively to their leaves. Auto Off subscribes to and
operates on the actual end devices so:

- The deadline starts as soon as ANY leaf turns on (independent of the
  group's any-on / all-on mode).
- The ensure-off retry loop can tell which specific leaves failed to
  switch off.
- Editing the root group's membership at runtime (UI edit, helper group
  member changes) is picked up without a config-entry reload. Detected
  on the next periodic worker tick (default poll_interval=60s).

Limitation: only the root targets listed in the group config are watched
for membership changes. If a nested group inside a root changes its
members, that change is not picked up until the next periodic tick.
Reload the auto_off config entry (or restart HA) to force an immediate
re-expansion.

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

- **Inactivity timer**: the deadline is a single point in time that
  activity pushes forward. When activity stops, the deadline stops
  moving and eventually fires, turning off every target.
- **Activity = a target turning on, or presence detected**: a target
  switching on extends the deadline (even with no presence, giving a
  manually-switched light a full delay); while presence is detected, the
  periodic patrol re-extends the deadline every `poll_interval`.
- **Extend-only**: a new deadline replaces the current one only if it is
  later. Activity never shortens an existing deadline.
- **Cancel only when nothing is on**: the deadline is cancelled when no
  target is on. Presence does not cancel the deadline; it extends it.
- **delay must exceed poll_interval**: the patrol re-extends every
  `poll_interval` seconds (default 15; commonly 60). The group `delay`
  (in minutes) must be greater than `poll_interval`, otherwise the timer
  can fire between patrol ticks while occupied and flap the lights off.
  A WARNING is logged when a group's delay does not exceed
  `poll_interval`. Set `delay` to at least a couple of minutes.
- **Ensure-off retry**: at deadline expiry auto_off does an initial
  `turn_off` dispatch and then runs a bounded retry loop for
  `ENSURE_WINDOW_SEC` seconds (60s), re-issuing `turn_off` every
  `ENSURE_INTERVAL_SEC` seconds (10s) on any target still on while
  sensors stay off. The loop aborts the moment any sensor reports on
  again.
- **Recovery after restart**: on startup a target that is already on is
  granted a fresh full delay (the deadline is re-seeded from the restart
  moment). While presence is on, patrol begins extending immediately.

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
