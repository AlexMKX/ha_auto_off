# Design: Expose group members as native HA group entities

## Objective

Replace the current "attributes on deadline sensor" approach with first-class
HA group entities on the `auto_off` device page. Users want to see and
interact with group members the same way they do for any other HA group (e.g.
Magic Areas aggregates): each membership list shows up as a proper entity with
native HA rendering of the `entity_id` member list in the more-info dialog.

Concretely, for every `auto_off` group the integration will expose:

1. One `binary_sensor.*_sensors` group entity aggregating `GroupConfig.sensors`.
2. One group entity per distinct `targets` domain (e.g.
   `light.*_targets_light`, `switch.*_targets_switch`).
3. `sensor_templates` as an `extra_state_attribute` on the sensors-group entity.

The existing deadline sensor and delay text entities stay; only the
`targets` / `sensors` / `sensor_templates` attributes are removed from the
deadline sensor — they now live on their own entities.

## Non-goals

- No config entry schema change. `GroupConfig` stays as-is (targets,
  sensors, sensor_templates, delay).
- No new service calls. Group management still goes through
  `auto_off.set_group` / `auto_off.delete_group`.
- No attempt to make a "mixed-type super-group". One native HA group
  entity per distinct target domain is the contract.
- No migration entries. Existing users see the new entities appear after
  upgrade; the old deadline-sensor attributes disappear.

## Key design principle: members drive logic, groups drive UI

The auto-off state machine **must not** subscribe to the newly created
`*Group` entities for its deadline logic. `SensorGroup` continues to track
every individual sensor (`GroupConfig.sensors`) and individual target
(`GroupConfig.targets`) through `async_track_state_change_event`. The
`BinarySensorGroup.state` recalculation can lag or coalesce events; the
auto-off timing depends on precise per-entity transitions ("any sensor turned
on → cancel deadline", "all sensors off → start deadline").

The group entities exist **only for UI visibility and convenience turn-off**:

- UI: `hass.states.get(group_entity_id).attributes["entity_id"]` renders
  natively on the device page.
- Turn-off: instead of iterating per-target service calls,
  `SensorGroup.turn_off` calls `<domain>.turn_off` once per domain-group.

The existing `Target` class is retained for per-entity state tracking. Its
`turn_off()` method is replaced by group-based turn-off at the `SensorGroup`
level.

## Architecture

### Layer 1: data model (unchanged)

`GroupConfig` stays as-is. Validators (including the entity-id syntax
warning from the previous spec) are unchanged.

### Layer 2: integration manager

`IntegrationManager` gains methods:

```python
def get_group_targets_by_domain(self, group_name: str) -> dict[str, list[str]]:
    """Return {domain: [entity_id, ...]} for the targets of a group.

    Groups targets by their `domain.` prefix.  Invalid entity ids
    (those that fail valid_entity_id) are skipped and do not appear
    in any bucket.
    """
```

`set_group` / `update_group_config` are extended to:

1. Run the existing config validation + `SensorGroup` (re)init.
2. Compute the set of domains that need target-groups.
3. For each domain, add/update the corresponding `*TargetsGroupEntity`.
4. Add/update the `SensorsGroupEntity`.
5. Remove orphaned group entities (e.g. if a domain is no longer present
   in targets after update).

`delete_group` unregisters all group entities belonging to the group.

### Layer 3: entity classes

Three new entity classes live in `custom_components/auto_off/group_entities.py`:

#### `SensorsGroupEntity(BinarySensorGroup)`

Inherits from `homeassistant.components.group.binary_sensor.BinarySensorGroup`.

- `entity_ids = GroupConfig.sensors` (concrete binary_sensor entity ids).
- `mode = False` (OR: any on → on).
- `name = None`, `has_entity_name = True`, `translation_key = "sensors"`
  so HA renders the entity name as "Sensors".
- `unique_id = f"{DOMAIN}_{group_name}_sensors"`.
- `_attr_device_info = DeviceInfo(identifiers={(DOMAIN, group_name)}, ...)` —
  reuses the auto_off device.
- Overrides `extra_state_attributes` to:

  ```python
  @property
  def extra_state_attributes(self) -> dict[str, Any]:
      base = super().extra_state_attributes or {}
      config = self._manager.get_group_config(self._group_name)
      templates = list(config.sensor_templates) if config else []
      return {**base, "sensor_templates": templates}
  ```

  The `super().extra_state_attributes` already contains
  `{"entity_id": [...]}`.

#### Domain-specific targets groups

One class per supported HA domain. Each inherits from the corresponding
`*Group` in `homeassistant.components.group.<domain>`:

| Domain         | Base class         |
|----------------|--------------------|
| `light`        | `LightGroup`       |
| `switch`       | `SwitchGroup`      |
| `fan`          | `FanGroup`         |
| `cover`        | `CoverGroup`       |
| `media_player` | `MediaPlayerGroup` |
| `lock`         | `LockGroup`        |
| `valve`        | `ValveGroup`       |

Any entity id whose domain is not in this table is skipped with a
`WARNING` log at group creation time and does not appear in any group
entity; the auto-off state machine still tracks it through `Target` and
attempts a best-effort `<its_domain>.turn_off` service call at deadline
expiry (fallback path, see "Layer 4").

Naming for targets groups:
- `entity_id`: `<domain>.auto_off_<group>_targets_<domain>`
- `unique_id`: `auto_off_<group>_targets_<domain>`
- `_attr_name = None`, `has_entity_name = True`,
  `translation_key = f"targets_{domain}"` → UI reads "Targets: light" etc.
- Bound to the auto_off group device via `DeviceInfo`.

`mode` / `all` parameter is set to the `*Group`-specific default that means
"any on → on" (this is already the HA default for group platforms).

### Layer 4: SensorGroup turn-off path

`SensorGroup.turn_off()` changes from:

```python
for target in self._targets:
    await target.turn_off()
```

to:

```python
# Primary path: delegate to the per-domain group entities if present.
group_entity_ids = self._manager.get_group_target_group_entity_ids(self._name)
for group_entity_id in group_entity_ids:
    domain = split_entity_id(group_entity_id)[0]
    await hass.services.async_call(
        domain, "turn_off", {"entity_id": group_entity_id}, blocking=False
    )

# Fallback: any target whose domain wasn't groupable still gets a
# per-entity turn_off call.
for target in self._targets:
    if target.domain not in _GROUPABLE_DOMAINS:
        await target.turn_off()
```

The `Target.is_on()` / state-tracking paths stay unchanged; the turn-off
path is the only change.

### Layer 5: deadline sensor

`DeadlineSensorEntity.extra_state_attributes` is simplified:

```python
@property
def extra_state_attributes(self) -> dict[str, Any]:
    return {"deadline_iso": self._deadline_iso}
```

`targets`, `sensors`, `sensor_templates` are removed — they're now on the
group entities.

## Data flow

```
set_group service call
  ─► IntegrationManager.set_group
       ─► GroupConfig(…)                  # validators warn on bad ids
       ─► SensorGroup.reinit               # tracks members directly
       ─► compute domain buckets
       ─► for each domain in current - previous:
            create <Domain>TargetsGroupEntity  (async_add_entities)
       ─► for each domain in previous - current:
            remove group entity (entity_registry.async_remove)
       ─► for domains in intersection:
            update entity_ids on existing group entity
       ─► create/update SensorsGroupEntity similarly
       ─► DeadlineSensorEntity.async_write_ha_state
```

Timer path (auto-off expiry):

```
all sensors go off
  ─► SensorGroup._on_sensor_change → schedule deadline
       ─► on expiry:
            for each domain group entity:
              hass.services.async_call(domain, "turn_off",
                  {"entity_id": "<domain>.auto_off_<g>_targets_<domain>"})
            for each non-groupable Target:
              Target.turn_off()
```

## Error handling and logging

- Non-groupable domain at group creation: `WARNING "Group %s: target %s is
  in domain '%s' which has no HA group platform; falling back to
  per-entity turn_off."`. Called once per such target per set_group.
- Invalid entity id in targets: unchanged from previous spec (warning at
  load time, kept in `targets` attribute for visibility, skipped at
  turn-off).
- `hass.services.async_call` failures in group turn-off: caught and
  logged as `WARNING` per-domain; auto-off proceeds to next domain /
  fallback targets.

## Backward compatibility

On upgrade, existing auto_off config entries are not modified. The new
group entities are created on the next `async_setup_entry`. The old
`targets` / `sensors` / `sensor_templates` attributes on the deadline
sensor disappear; any automation that read them through
`state_attr("sensor.auto_off_*_deadline", "targets")` needs to switch to
reading the new group entity instead:

```
state_attr("binary_sensor.auto_off_<g>_sensors", "entity_id")
state_attr("<domain>.auto_off_<g>_targets_<domain>", "entity_id")
```

This is a breaking change for any user who scripted against the attribute
names, but the new shape is more idiomatic and discoverable.

## Testing

Unit tests (`custom_components/auto_off/tests/`):

1. `test_sensors_group_entity_builds` — given a `GroupConfig` with 2
   sensors and 1 template, the `SensorsGroupEntity` is constructed with
   `entity_ids = [<2 ids>]` and `extra_state_attributes["sensor_templates"]`
   contains the template string.
2. `test_targets_groups_split_by_domain` —
   `get_group_targets_by_domain` returns `{"light": [...], "switch": [...]}`
   for a mixed-domain config.
3. `test_targets_group_unsupported_domain_fallback` — a target entity in
   a domain without a `*Group` (e.g. `scene.foo`) is not put in any group
   entity, and `Target.turn_off()` is called for it during expiry.
4. `test_turn_off_calls_group_turn_off_service` — at deadline expiry,
   `hass.services.async_call` is invoked once per domain group entity
   with `entity_id=<group entity_id>`.
5. `test_set_group_removes_orphan_group_entities` — updating a group
   from `{lights, switches}` to `{lights}` removes the switch-group entity
   from the entity registry.
6. `test_deadline_sensor_attributes_are_minimal` — after the change,
   deadline sensor attributes contain only `deadline_iso`.

E2E tests (`tests/test_integration_e2e.py`):

1. `test_group_entities_appear_on_device_page` — create a group via
   `set_group` with lights + switches; assert `binary_sensor.*_sensors`,
   `light.*_targets_light`, `switch.*_targets_switch` exist in entity
   registry and are bound to the group device.
2. `test_group_turn_off_at_deadline` — spy on
   `hass.services.async_call`; assert group-level calls are made at
   deadline expiry and individual target states go to `off`.

## Files touched

New:
- `custom_components/auto_off/group_entities.py`
- `custom_components/auto_off/tests/test_group_entities.py`

Modified:
- `custom_components/auto_off/sensor.py`
  (strip extra attributes from deadline sensor)
- `custom_components/auto_off/auto_off.py`
  (replace per-target turn_off with group-level turn_off, add fallback)
- `custom_components/auto_off/integration_manager.py`
  (add `get_group_targets_by_domain`, manage group entity lifecycle)
- `custom_components/auto_off/__init__.py`
  (forward new entity platforms if the switch-from-deadline-only requires it)
- `custom_components/auto_off/const.py`
  (add `_GROUPABLE_DOMAINS` constant)
- `custom_components/auto_off/translations/en.json`
  (add `entity.binary_sensor.sensors.name = "Sensors"`,
   `entity.<domain>.targets_<domain>.name = "Targets (<Domain>)"`)
- `custom_components/auto_off/tests/test_integration_e2e.py`
- `custom_components/auto_off/tests/ha_packages/auto_off_test.yaml`
- `README.md` (auto_off section — describe the new entities)

Removed attributes (not files):
- `DeadlineSensorEntity.extra_state_attributes`: targets, sensors, sensor_templates.
