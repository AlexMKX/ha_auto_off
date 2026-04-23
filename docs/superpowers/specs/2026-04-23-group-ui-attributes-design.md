# Design: Expose group config on the device page + remove template targets

## Objective

Make the composition of an `auto_off` group visible from the Home Assistant
device page at `/config/devices/device/<entry_id>`. Users want to see, without
consulting YAML or service calls, three things:

1. Which entities the group turns off (`targets`).
2. Which binary sensors drive the auto-off timer (`sensors`).
3. Which Jinja templates act as virtual sensors (`sensor_templates`).

Home Assistant does not render device-level attributes natively, but it does
render any entity's `extra_state_attributes` inside the more-info dialog
reachable from the device page. The existing `DeadlineSensorEntity` is the
natural carrier: one per group, already attached to the group device, already
updates on every state change.

In the same breaking release we simplify `GroupConfig.targets` to accept only
concrete entity ids. Jinja templates inside `targets` stop being a supported
contract. This removes a large slice of `Target` (`_is_template`,
`_current_entity_ids`, template tracking) and turns the attribute list into an
unambiguous, flat `list[str]`.

## Non-goals

- No changes to `DelayTextEntity`.
- No separate "members" / "config" sensor entity (explicitly rejected; the
  previous `GroupConfigSensorEntity` was removed and stays removed).
- No `device_id` support in `targets` (entity ids only).
- No periodic reconcile loop for targets.
- No config entry version bump, no `async_migrate_entry` step: legacy configs
  that contained Jinja strings inside `targets` are handled naturally by the
  new validation path (see "Backward compatibility").
- No resolution of `sensor_templates` to their current boolean value in the
  attribute; raw template text only.

## Architecture

The change touches three layers:

1. **Data model (`GroupConfig`, `Target`)** — tighten the contract.
2. **Integration manager** — expose a read accessor for the current group
   config and force a state write on the deadline sensor when config changes.
3. **Deadline sensor entity** — surface three new attributes pulled from the
   current `GroupConfig`.

No new platforms, no new entities, no new services.

### Data model

**`GroupConfig`** (`custom_components/auto_off/auto_off.py`)

`targets: list[str]` keeps its type but the semantic contract is now "entity
ids only". A Pydantic `@field_validator("targets")` walks the list and, for
every element that does not pass `homeassistant.core.valid_entity_id`, emits a
`WARNING` log entry. The element is **kept** in the list. We keep invalid
elements for two reasons:

- The `targets` attribute on the deadline sensor must accurately reflect the
  configured value so the user can spot their own typo.
- Silent filtering would obscure the root cause when a group misbehaves.

The validator cannot know the group name from inside Pydantic, so warnings at
load time are emitted from the caller (`SensorGroup` construction or
`update_group_config`), which has the group name in scope.

`sensors`, `sensor_templates`, `delay` are unchanged.

**`Target`** (`custom_components/auto_off/auto_off.py`)

The class becomes a single-entity wrapper:

- Removed: `_detect_template`, `_is_template`, `_current_entity_ids`, all
  template tracking (`async_track_template` subscriptions and render logic).
- Retained: `entity_id: str`, `on_state_change` callback, `is_on()`,
  `turn_off()`, `start_tracking()`, `stop_tracking()`.
- Constructor runs `valid_entity_id(entity_id)` once. On failure, sets an
  internal `_skip = True` flag; `start_tracking` is a no-op and `turn_off` is
  a no-op. The warning is emitted by the caller at config-load time, not
  again here.
- `is_on()` calls `hass.states.get(self.entity_id)`. If `None`, returns
  `False` without logging. This is the normal late-binding case and must not
  be noisy.
- `turn_off()` checks `hass.states.get(self.entity_id)` before calling the
  service. If `None`, logs `WARNING` (with group name supplied by the caller)
  and returns without raising. The surrounding `SensorGroup.turn_off` loop is
  already tolerant to individual target failures.
- `start_tracking()` uses `async_track_state_change_event` on a single entity
  id.

### Integration manager

**`IntegrationManager`** (`custom_components/auto_off/integration_manager.py`)

New public method:

```python
def get_group_config(self, group_name: str) -> GroupConfig | None:
    """Return the active GroupConfig for a group, or None during teardown."""
```

Implementation reads `self.auto_off._groups[group_name]._config`. Returns
`None` if the group was removed (the deadline entity may still be in HA's
entity registry during teardown).

Existing `update_group_config` gains one extra step at the end: after the new
`SensorGroup` is installed, it calls `async_write_ha_state()` on the group's
`DeadlineSensorEntity` so the new attributes appear in the UI immediately
rather than waiting for the next deadline change. This is the only hook
needed; attribute values are pulled lazily by HA through
`extra_state_attributes` on every write.

### Deadline sensor entity

**`DeadlineSensorEntity`** (`custom_components/auto_off/sensor.py`)

Constructor gets a reference to the `IntegrationManager`. `extra_state_attributes`
becomes:

```python
@property
def extra_state_attributes(self) -> dict[str, Any]:
    config = self._manager.get_group_config(self._group_name)
    if config is None:
        return {"deadline_iso": self._deadline_iso}
    return {
        "deadline_iso": self._deadline_iso,
        "targets": list(config.targets),
        "sensors": list(config.sensors),
        "sensor_templates": list(config.sensor_templates),
    }
```

Contract:

- All four keys present when the group exists; empty lists are empty lists.
- `targets` and `sensors` are raw `list[str]` (including any syntactically
  invalid element — see Data model above).
- `sensor_templates` is the raw Jinja source text, not the rendered value.
- During teardown (`get_group_config` returns `None`), only `deadline_iso` is
  exposed; no warning, this is expected.

The attribute list is deliberately not named `entity_id` (that would rely on
the unofficial `group.*` rendering convention, which does not apply to plain
sensor entities).

## Data flow

```
set_group service call
  ─► IntegrationManager.update_group_config
       ─► GroupConfig(...)  # validator warns on bad entity ids
       ─► SensorGroup.reinit
       ─► DeadlineSensorEntity.async_write_ha_state
            ─► extra_state_attributes read
                 ─► get_group_config returns fresh GroupConfig
                 ─► dict with targets / sensors / sensor_templates / deadline_iso
```

Timer path (unchanged in shape, simplified internally):

```
sensor state change
  ─► SensorGroup._on_sensor_change
       ─► if all sensors off and any Target.is_on(): schedule deadline
       ─► deadline expiry:
            for target in targets:
              target.turn_off()  # no-op if _skip or state missing
```

## Error handling and logging

Single module logger (`logging.getLogger(__name__)` per module, as used
elsewhere in the package).

- **Load time (`GroupConfig` construction / `update_group_config`)** —
  for each `target` failing `valid_entity_id`:
  `WARNING "Group %s: target %r is not a valid entity_id, it will be skipped at turn_off"`.
- **`Target.turn_off` when `hass.states.get(entity_id) is None`** —
  `WARNING "Group %s: target %s not found in state machine, skipping turn_off"`.
  Does not raise; the outer loop continues with other targets.
- **`Target` skip flag** — no log on the skip path itself; the user has
  already seen the load-time warning.
- **`DeadlineSensorEntity.extra_state_attributes` with `get_group_config is None`**
  — no log, fallback to `{"deadline_iso": ...}`.

No exceptions introduced. No raise-on-validation. No `async_migrate_entry`.

## Backward compatibility

Existing config entries carrying Jinja templates in `targets` are loaded
without error. Each template string fails `valid_entity_id`, produces a
`WARNING` at load time, and is skipped at every `turn_off`. The template is
preserved verbatim in `GroupConfig.targets` and therefore visible in the new
UI attribute. Users fix their configuration through the existing `set_group`
service.

No version bump. No migration file. No forced re-setup.

## Testing

All tests live in `custom_components/auto_off/tests/`.

### Unit tests

1. `test_group_config_target_syntax_warning` — build `GroupConfig` with one
   valid and one syntactically invalid `targets` entry; assert both remain in
   the list and a warning is logged for the invalid one.
2. `test_group_config_keeps_template_in_targets` — a Jinja-like string
   (`"{{ states('light.x') }}"`) fails `valid_entity_id`, stays in the list,
   warning emitted.
3. `test_target_skip_when_entity_id_invalid` — `Target(hass, "not-an-id")`
   constructs with `_skip=True`; calling `turn_off()` issues no service call
   and does not raise.
4. `test_target_turn_off_skips_when_not_in_state_machine` — `Target(hass, "light.future")`
   with no state registered; `turn_off()` logs a warning and issues no service
   call.
5. `test_deadline_sensor_attributes_expose_group_config` — attribute dict
   contains `deadline_iso`, `targets`, `sensors`, `sensor_templates` with
   values matching the current `GroupConfig`.
6. `test_deadline_sensor_attributes_update_after_set_group` — after
   `update_group_config` with new fields, the attributes reflect the new
   values and `async_write_ha_state` was called.

### End-to-end test

`test_late_binding_target` in `tests/test_integration_e2e.py`:

```
Given auto_off integration loaded and a sensor `sensor.trigger_motion = off`,
with `light.late_target` NOT registered in hass.states.

When:
  1. Call service auto_off.set_group with
       targets=["light.late_target"],
       sensors=["sensor.trigger_motion"],
       delay=1.
  2. Register light.late_target with state "on".
  3. Set sensor.trigger_motion = "on".
  4. Set sensor.trigger_motion = "off".  # timer starts
  5. Advance time by 1 minute + 1 second (async_fire_time_changed).

Then:
  - light.turn_off service was called with entity_id "light.late_target".
  - No WARNING "not found in state machine" was logged during this
    turn_off (the state was present at fire time).
```

Existing e2e scenarios (`tests/test_integration_e2e.py`,
`tests/ha_packages/auto_off_test.yaml`) are updated to:

- Remove any Jinja-template `targets` (if present) in favour of flat entity
  ids.
- Add an assertion scenario for warning+skip when a target entity id is
  syntactically invalid.

## Files touched

Modified:

- `custom_components/auto_off/auto_off.py`
- `custom_components/auto_off/sensor.py`
- `custom_components/auto_off/integration_manager.py`
- `custom_components/auto_off/tests/test_auto_off.py`
- `custom_components/auto_off/tests/test_sensor.py` (or equivalent)
- `custom_components/auto_off/tests/test_integration_e2e.py`
- `custom_components/auto_off/tests/ha_packages/auto_off_test.yaml`
- `README.md` (auto_off section)

No new files. No deletions beyond template-related code inside `Target`.
