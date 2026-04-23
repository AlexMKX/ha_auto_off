# Split `auto_off` into `auto_off` + `door_occupancy` (Design)

Date: 2026-04-22
Status: proposed

## Problem

The `auto_off` custom integration currently bundles two unrelated responsibilities:

1. Turning off entities after a configurable inactivity delay (group model:
   `sensors` + `targets` + `delay`).
2. Creating `binary_sensor.*_occupancy` for doors / locks / covers that emit a
   short activity pulse on every state change.

They share nothing except a polling tick and the `DOMAIN` namespace. Mixing them
violates single-responsibility, makes configuration noisy, and makes the service
schema for groups awkward (one `config` field carrying a YAML string instead of
structured values).

Additionally:

- The group service `auto_off.set_group` accepts `config: str` (YAML) rather
  than structured fields, which hurts UI discoverability and validation.
- `sensors` accepts both entity ids and Jinja templates in the same list,
  decided at runtime by presence of `{{` — a "magic default" that the global
  rules explicitly discourage.
- `DoorOccupancyBinarySensor` implements a hand-rolled auto-reset timer via
  `loop.call_later` and silently drops the first real state change because
  `old_state` is `None` when the listener first fires.

## Goals

- Split the code into two independent custom integrations shipped from one
  repository: `custom_components/auto_off/` and `custom_components/door_occupancy/`.
- Replace the stringified YAML config of `auto_off.set_group` with explicit
  fields: `targets`, `sensors`, `sensor_templates`, `delay`.
- Replace the hand-rolled auto-reset timer in door occupancy with a small,
  reusable `AutoResetBinarySensor` base class built on public HA helpers
  (`async_call_later`). Fix the missed-first-event bug as part of the rewrite.
- Keep the existing auto-off timer mechanism (`asyncio.TimerHandle` +
  `DeadlineSensorEntity` + `auto_off_deadline` attribute). No HA `timer` entity
  and no wall-clock rewrite: out of scope.
- Breaking change: no runtime backward compatibility for existing config
  entries. Users reinstall per README migration notes.

## Non-goals

- Splitting `auto_off.py` into multiple modules (YAGNI: ~740 lines, working,
  tested; split would be refactor-for-its-own-sake).
- Migrating the internal timer to wall-clock / `async_track_point_in_utc_time`.
- Introducing a real HA `timer` entity (investigated; private storage API, no
  device binding, no net benefit given we already have `DeadlineSensorEntity`
  and attribute-based recovery).
- Sharing `AutoResetBinarySensor` with `auto_off` package (no consumer there).
- Any unrelated refactoring of sensor/target detection, logging formatting, or
  error recovery beyond what the split directly touches.

## Repository layout

```
custom_components/
  auto_off/
    __init__.py          # async_setup_entry / async_migrate_entry / services
    const.py
    config_flow.py       # unchanged behavior, domain stays "auto_off"
    manifest.json        # domain: auto_off, no pyyaml requirement
    services.yaml        # new structured schema for set_group
    strings.json
    translations/
    icon.svg
    auto_off.py          # GroupConfig, Sensor, Target, SensorGroup, AutoOffManager
    sensor.py            # DeadlineSensorEntity
    text.py              # DelayTextEntity
    integration_manager.py  # IntegrationManager, now only auto_off concerns
    tests/               # unit + integration tests for auto_off only
  door_occupancy/
    __init__.py          # async_setup_entry / async_unload_entry
    const.py
    config_flow.py       # single-instance, poll_interval + occupancy_timeout
    manifest.json        # domain: door_occupancy
    strings.json
    translations/
    icon.svg
    auto_reset.py        # AutoResetBinarySensor base class (helper)
    discovery.py         # DoorOccupancyManager: periodic scan of door/lock/cover
    binary_sensor.py     # DoorOccupancyBinarySensor(AutoResetBinarySensor)
    tests/               # unit tests for auto_reset, discovery, binary_sensor
hacs.json                # references both packages
README.md                # split usage + migration notes
```

Reasoning: HACS can ship multiple `custom_components/*` packages from one
repository; users add each integration independently via the HA UI.

## `auto_off` changes

### `const.py`

```python
DOMAIN = "auto_off"
PLATFORMS = ["sensor", "text"]    # binary_sensor removed

CONF_POLL_INTERVAL = "poll_interval"
CONF_GROUPS = "groups"

# Group fields
CONF_SENSORS = "sensors"
CONF_SENSOR_TEMPLATES = "sensor_templates"
CONF_TARGETS = "targets"
CONF_DELAY = "delay"

CONF_GROUP_NAME = "group_name"

SERVICE_SET_GROUP = "set_group"
SERVICE_DELETE_GROUP = "delete_group"

CONFIG_VERSION = 3
```

Removed: `CONF_CONFIG`.

### `GroupConfig` (pydantic)

```python
class GroupConfig(BaseModel):
    targets: list[str]
    sensors: list[str] = []
    sensor_templates: list[str] = []
    delay: int | str = 0

    @model_validator(mode="after")
    def require_sensor_source_and_targets(self) -> "GroupConfig":
        if not self.targets:
            raise ValueError("'targets' must be non-empty")
        if not self.sensors and not self.sensor_templates:
            raise ValueError(
                "At least one of 'sensors' or 'sensor_templates' must be non-empty"
            )
        return self
```

`delay` keeps `int | str` to allow Jinja templates (rendered by
`SensorGroup.get_delay`). No other semantic change.

### Internal `Sensor` construction

`SensorGroup._init_from_config` is updated:

- Iterate `config.sensors` — create `Sensor(hass, raw=<entity_id>, kind="entity", ...)`.
- Iterate `config.sensor_templates` — create `Sensor(hass, raw=<template_str>, kind="template", ...)`.
- `Sensor.__init__` takes an explicit `kind` argument; `_detect_template` is
  removed. This removes the `'{{' in raw` heuristic and makes the intent
  explicit at the call site.

Behavior for the rest of `SensorGroup`, `Target`, `AutoOffManager` is unchanged.

### `services.yaml`

```yaml
set_group:
  name: Set Group
  description: Create or update an auto-off group.
  fields:
    group_name:
      name: Group Name
      required: true
      selector: { text: }
    targets:
      name: Targets
      description: Entities to turn off (switch, light, fan, etc.).
      required: true
      selector:
        entity:
          multiple: true
    sensors:
      name: Sensors
      description: Binary sensors whose OFF state starts the turn-off timer.
      required: false
      selector:
        entity:
          multiple: true
          domain: binary_sensor
    sensor_templates:
      name: Sensor templates
      description: >
        Jinja templates rendered to bool. Treated identically to binary sensors.
      required: false
      selector:
        object:
    delay:
      name: Delay (minutes)
      description: Integer or Jinja template rendering to minutes.
      required: false
      default: 0
      example: 5
      selector:
        text:

delete_group:
  name: Delete Group
  description: Delete an auto-off group.
  fields:
    group_name:
      required: true
      selector: { text: }
```

Validation:

- Handler in `__init__.py` calls `GroupConfig.model_validate({...})`.
- On `pydantic.ValidationError`: log the specific field errors, do not raise
  to HA (service call returns a proper failure logged via `_LOGGER.error`).
- Any other exception propagates (fail-fast per global rules).

### Config entry migration

- `CONFIG_VERSION = 3`. Old entries (`version < 3`) are detected in
  `async_migrate_entry`.
- Old entries must be removed by the user and recreated — per the user-approved
  "breaking cut" decision. `async_migrate_entry` logs a clear error with a link
  to the README migration section and returns `False` so HA surfaces a
  reconfiguration prompt.

### `manifest.json`

Remove `pyyaml` from `requirements`. Keep `pydantic`. Remove `jinja2` (HA
already provides it); keep if already imported directly (re-verified during
implementation).

### `integration_manager.py`

- Drop the `DoorOccupancyManager` wiring.
- `PLATFORMS = ["sensor", "text"]`; binary_sensor platform no longer forwarded.
- `async_setup_integration` entry-point (currently called from the removed
  `binary_sensor.py`) becomes plain `async_setup_entry` in `auto_off/__init__.py`,
  creating the `IntegrationManager` directly. The global `default_manager`
  hack and the "initialize-from-binary_sensor-platform" indirection are removed.

### Remove `GroupConfigSensorEntity`

The "config summary" sensor per group is removed. It is a pure UI mirror of
static configuration — once `set_group` is the documented management surface,
this entity adds no behavior and costs one extra platform registration per
group. Each group's device keeps `DeadlineSensorEntity` (live deadline) and
`DelayTextEntity` (editable delay). `IntegrationManager` loses
`_sensor_entities` / `sensor_platform_ready` responsibilities for config
sensors (deadline entities remain, using the same `sensor` platform).

## `door_occupancy` changes

### `const.py`

```python
DOMAIN = "door_occupancy"
PLATFORMS = ["binary_sensor"]

CONF_POLL_INTERVAL = "poll_interval"
CONF_OCCUPANCY_TIMEOUT = "occupancy_timeout"

DEFAULT_POLL_INTERVAL = 30
DEFAULT_OCCUPANCY_TIMEOUT = 15

CONFIG_VERSION = 1
```

### Config flow

Single-instance integration (`async_set_unique_id(DOMAIN)` +
`_abort_if_unique_id_configured`). Form fields:

- `poll_interval` (int, 5..300, default 30).
- `occupancy_timeout` (int, 1..600, default 15).

Options flow allows changing both post-install.

### `auto_reset.py` — `AutoResetBinarySensor`

```python
class AutoResetBinarySensor(BinarySensorEntity):
    """Binary sensor that turns on via pulse() and auto-resets to off.

    The reset timer is scheduled on every pulse() call, cancelling any
    previous pending reset. Subclasses are responsible for deciding when
    to call pulse(); this class owns only the on/off state and the timer.
    """

    def __init__(self, hass: HomeAssistant, reset_timeout: float) -> None:
        self.hass = hass
        self._reset_timeout = reset_timeout
        self._attr_is_on = False
        self._cancel_reset: Callable[[], None] | None = None

    @callback
    def pulse(self) -> None:
        self._attr_is_on = True
        if self._cancel_reset is not None:
            self._cancel_reset()
        self._cancel_reset = async_call_later(
            self.hass, self._reset_timeout, self._on_reset
        )
        self.async_write_ha_state()

    @callback
    def _on_reset(self, _now: datetime) -> None:
        self._cancel_reset = None
        self._attr_is_on = False
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        if self._cancel_reset is not None:
            self._cancel_reset()
            self._cancel_reset = None
```

`async_call_later` is public HA API; no private `loop.call_later`. The
cancel-on-remove path closes the current leak where `_timer` is not cancelled
when HA removes the entity (currently only cancelled if `async_will_remove_from_hass`
is invoked on unload — which is fine — but the old version cancelled via
raw `_timer.cancel()` without clearing the reference).

### `discovery.py` — `DoorOccupancyManager`

- Same discovery rules as today:
  - `binary_sensor.*` with `device_class == "door"`
  - all `lock.*`
  - all `cover.*`
- Owns its own `async_track_time_interval` based on `CONF_POLL_INTERVAL`. No
  longer tied to `auto_off` polling.
- Creates one `DoorOccupancyBinarySensor` per discovered entity, idempotent
  (map keyed by source entity id).
- Does not remove sensors for entities that disappear from `hass.states` — same
  as today; out of scope.

### `binary_sensor.py` — `DoorOccupancyBinarySensor`

- Inherits `AutoResetBinarySensor`.
- Subscribes to `async_track_state_change_event` on the source entity in
  `async_added_to_hass`.
- Initializes `_prev_state` from the current source state inside
  `async_added_to_hass` (before subscribing). Then in the event handler:
  - Ignore `new_state.state in ("unknown", "unavailable")`.
  - If `new_state.state != self._prev_state` → update `_prev_state` and call
    `self.pulse()`.
  - Do not require `old_state` to be present (fixes the first-event regression).
- Device binding unchanged: attach to the device owning the source entity via
  `add_config_entry_id`, and resolve `device_info` from the source's device.

### `manifest.json`

- `domain: door_occupancy`, no `pydantic`/`pyyaml` requirements needed.
- `iot_class: local_push`. Our binary sensors react to HA state change
  events of the source door/lock/cover entity (push from the HA state
  machine, not polling of a physical device). The periodic discovery scan
  is integration-internal bookkeeping, not device polling.

## Service / config contracts summary

| Contract                          | Before                                  | After                                                              |
| --------------------------------- | --------------------------------------- | ------------------------------------------------------------------ |
| `auto_off.set_group`              | `group_name`, `config` (YAML string)    | `group_name`, `targets`, `sensors`, `sensor_templates`, `delay`    |
| `Sensor` kind detection           | heuristic on `'{{'`                     | explicit `kind` argument at construction                           |
| `binary_sensor.*_occupancy` owner | domain `auto_off`                       | domain `door_occupancy`                                            |
| Occupancy timeout                 | hardcoded 15s                           | `CONF_OCCUPANCY_TIMEOUT`, default 15s                              |
| Auto-reset implementation         | `loop.call_later`, ad-hoc               | `AutoResetBinarySensor` + `async_call_later`                       |
| Missed first state change         | yes (bug)                               | fixed                                                              |

## Migration (for users)

This is a breaking release.

1. Remove the existing `Auto Off` integration from Home Assistant (Settings →
   Devices & services → Auto Off → Delete). Entities created by it
   (`sensor.auto_off_*_config`, `sensor.auto_off_*_deadline`,
   `text.*_delay_minutes`, `binary_sensor.*_occupancy`) will be removed.
   The new Auto Off does not recreate the `*_config` sensor.
2. Update the HACS custom repository. After restart, install both integrations
   via Settings → Devices & services → Add integration:
   - `Auto Off` — same flow as before (poll interval), plus recreate groups
     via the new `auto_off.set_group` service.
   - `Door Occupancy` — new integration; it recreates occupancy binary
     sensors on first discovery tick.
3. Group service calls must be migrated to the new field layout. Example:

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

Old occupancy entities (`binary_sensor.<source>_occupancy`) owned by the
`auto_off` config entry are removed by HA when the `auto_off` entry is
deleted (their registry records are wiped). The new `door_occupancy`
integration, on first discovery tick, creates fresh binary sensors. The new
entities use the same `unique_id` format as before
(`<source_entity_id_with_dots_to_underscores>_occupancy`), which means HA
will assign the same default `entity_id` string — unless a user had renamed
the old entity, in which case the rename is lost (registry record gone).
Document this explicitly in the README migration section.

## Testing plan

All tests use project conventions (pytest, auto-discovery). Tests live next to
the code they exercise.

### `custom_components/auto_off/tests/`

- `test_services.py` (update):
  - `test_set_group_creates_group_with_structured_fields`.
  - `test_set_group_rejects_empty_targets`.
  - `test_set_group_rejects_all_sensor_lists_empty`.
  - `test_set_group_accepts_sensor_templates_only`.
  - `test_set_group_accepts_delay_as_int_and_template_string`.
  - Remove any assertions on the old `config` string format.
- `test_integration_manager.py` (update):
  - Delete door_occupancy expectations.
  - Delete assertions that require `GroupConfigSensorEntity` creation.
  - Keep group lifecycle tests (create, update, delete).
- `test_text_entity.py`: unchanged.
- `test_migration.py` (new):
  - `test_old_entry_triggers_migration_failure`: an entry with `version < 3`
    leads to `async_migrate_entry` returning `False` and a logged error that
    mentions the README migration section.
- `test_config_flow.py`: keep.

### `custom_components/door_occupancy/tests/`

- `test_auto_reset.py` (new):
  - `test_pulse_turns_on_and_resets_after_timeout`: pulse → is_on; advance
    clock by timeout → is_on is False.
  - `test_pulse_restarts_timer`: pulse; advance by timeout/2; pulse again;
    advance by timeout/2 again; is_on still True.
  - `test_remove_cancels_pending_reset`: pulse; `async_will_remove_from_hass`;
    advance past timeout; no state write errors, `_attr_is_on` stays True
    (entity is being removed, state is irrelevant, but cancel must not raise).
- `test_discovery.py` (new):
  - `test_discovers_door_binary_sensors_and_locks_and_covers`.
  - `test_discovery_is_idempotent_for_repeated_ticks`.
- `test_binary_sensor.py` (new):
  - `test_first_state_change_triggers_pulse` (regression for the
    `old_state is None` bug).
  - `test_unavailable_state_is_ignored`.
  - `test_same_state_reported_twice_is_not_a_pulse`.
  - `test_device_info_mirrors_source_device`.

### `ha-test-kit/`

- Adjust the e2e harness fixtures to register both integrations.
- Update e2e scenarios using old `config:` service payload to the new fields.
- Update e2e scenarios that assert on `sensor.auto_off_*_config`
  (`test_e2e_playwright.py` delay-persistence / update scenarios) to use
  `text.auto_off_*_delay_minutes` and `sensor.auto_off_*_deadline` instead.
- Verify a door state change produces an occupancy pulse end-to-end.

All tests should assert **behavior** (state transitions, emitted events, side
effects), not existence of methods or fields, per
`global-rules/testing.md`.

## Documentation plan

- Top-level `README.md`: split into two primary sections (`Auto Off`,
  `Door Occupancy`), add `Migration from 2512.x` section with the steps above
  and the old-vs-new service example. Update the "Entities created for a
  group" subsection: only `Deadline` sensor and `Delay` text remain; the
  `Config` sensor is removed (managed entirely through services now).
- `custom_components/auto_off/README.md` (new, short): purpose + link to
  top-level README.
- `custom_components/door_occupancy/README.md` (new, short): purpose + link.
- `doc/deadline_logic.md`: update references that mention occupancy in the
  same integration; remove or reword.
- Docstrings: all new/moved classes and public methods get English docstrings
  in the style of the existing codebase (short, behavioral).

## Out of scope / explicit deferrals

- Monotonic → wall-clock timer: deferred. Documented as known limitation: on
  system suspend, `SensorGroup._timer_deadline` drifts; recovery happens on
  next periodic tick via `auto_off_deadline` attribute comparison.
- Splitting `auto_off.py` into submodules: deferred.
- HA `timer` entity integration: rejected after investigation.
- Shared `AutoResetBinarySensor` between the two integrations: deferred; no
  consumer in `auto_off`.

## Implementation checklist (high-level)

1. Create `custom_components/door_occupancy/` package with manifest, const,
   config_flow, auto_reset, discovery, binary_sensor, strings/translations,
   icon, tests scaffolding.
2. Strip door occupancy code from `custom_components/auto_off/`:
   - Delete `door_occupancy.py`.
   - Delete `binary_sensor.py` (remove from `PLATFORMS`).
   - Remove door occupancy wiring from `integration_manager.py`.
   - Remove `pyyaml` from `manifest.json`.
   - Remove `GroupConfigSensorEntity` class and all references to it
     (`sensor_platform_ready`/`_sensor_entities` bookkeeping in
     `integration_manager.py`; any tests asserting on it).
3. Replace the `set_group` service schema and handler in
   `auto_off/__init__.py`; update `services.yaml` and `strings.json`.
4. Update `GroupConfig` and `Sensor.__init__` for explicit `kind`; adjust
   `SensorGroup._init_from_config` to feed entities and templates separately.
5. Implement `async_migrate_entry` for `auto_off` that fails on
   `version < 3` with a README link.
6. Update `hacs.json` to reflect two packages in the repository.
7. Rewrite tests per the Testing plan.
8. Update documentation per the Documentation plan.
9. Manually smoke-test against a running HA (install both integrations, create
   a group, trigger a door state change).
