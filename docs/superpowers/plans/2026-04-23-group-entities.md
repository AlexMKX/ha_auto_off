# Auto-Off Group Entities Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose the members of every `auto_off` group as first-class Home Assistant group entities (native `BinarySensorGroup` for sensors, one `*Group` per target domain) bound to the auto-off device, so they render on the device page the same way Magic Areas aggregates do.

**Architecture:** Groups are UI-only and turn-off convenience wrappers. The auto-off state machine continues to subscribe to individual members (as it does today). A new module `group_entities.py` defines a thin factory that produces the correct HA-stdlib group entity given a domain and member list. `IntegrationManager` tracks the live group entities per group-name/domain and adds/updates/removes them in lockstep with `set_group` / `delete_group`. `SensorGroup.turn_off` delegates to the newly created group entities via `hass.services.async_call(<domain>, "turn_off", {entity_id: <group_entity_id>})`, with a per-entity fallback for targets whose domain has no HA group platform.

**Tech Stack:** Home Assistant custom component, Python 3.13, pydantic v2, pytest + pytest-asyncio, ruff, vulture. HA stdlib group classes: `homeassistant.components.group.binary_sensor.BinarySensorGroup`, `.light.LightGroup`, `.switch.SwitchGroup`, `.fan.FanGroup`, `.cover.CoverGroup`, `.media_player.MediaPlayerGroup`, `.lock.LockGroup`, `.valve.ValveGroup`.

---

## File Structure

**New files:**
- `custom_components/auto_off/group_entities.py` — factory + per-domain subclasses with `DeviceInfo` attached; sensors-subclass exposes `sensor_templates` attribute.
- `custom_components/auto_off/tests/test_group_entities.py` — unit tests for the factory and subclass overrides.

**Modified:**
- `custom_components/auto_off/const.py` — add `GROUPABLE_DOMAINS` and `PLATFORMS` (adds binary_sensor + light + switch + fan + cover + media_player + lock + valve to forwarded platforms).
- `custom_components/auto_off/integration_manager.py` — add `get_group_config`, `get_group_targets_by_domain`, `get_group_member_group_entity_ids`; lifecycle of group entities in `set_group`/`delete_group`; new `*_platform_ready` hooks to receive `AddEntitiesCallback` for each forwarded domain.
- `custom_components/auto_off/auto_off.py` — `SensorGroup.turn_off` delegates to group entities first, falls back to per-target `turn_off` for non-groupable domains.
- `custom_components/auto_off/__init__.py` — forward the expanded `PLATFORMS`.
- `custom_components/auto_off/sensor.py` — strip `targets` / `sensors` / `sensor_templates` from `extra_state_attributes`; keep only `deadline_iso`.
- `custom_components/auto_off/translations/en.json` — translation keys for the new group entity names.
- One new platform module per HA domain — `binary_sensor.py`, `light.py`, `switch.py`, `fan.py`, `cover.py`, `media_player.py`, `lock.py`, `valve.py` — each tiny and identical in shape: an `async_setup_entry` that hands the `AddEntitiesCallback` to the manager. (`sensor.py` and `text.py` already exist; we keep them.)
- `custom_components/auto_off/tests/test_integration_manager.py` — tests for new manager methods.
- `custom_components/auto_off/tests/test_auto_off.py` — tests for group-based turn-off path.
- `custom_components/auto_off/tests/test_integration_e2e.py` — e2e scenario asserting new entities appear and get turned off via group calls.
- `README.md` — describe the new entities in the auto_off section.

---

## Task 1: Add `GROUPABLE_DOMAINS` constant and expand `PLATFORMS`

**Files:**
- Modify: `custom_components/auto_off/const.py`

- [ ] **Step 1: Edit const.py**

Replace the current `PLATFORMS = ["sensor", "text"]` line and add the new constant. Full updated content:

```python
"""Constants for the Auto Off integration."""

import json
from pathlib import Path

DOMAIN = "auto_off"


def _read_manifest_version() -> str:
    """Read version from manifest.json at import time.

    The manifest sits next to this file; read it synchronously once so
    entities can advertise the integration version as DeviceInfo.sw_version
    without doing async work in property getters.
    """
    try:
        manifest_path = Path(__file__).parent / "manifest.json"
        return json.loads(manifest_path.read_text())["version"]
    except Exception:
        return "unknown"


VERSION = _read_manifest_version()

# Config entry storage keys
CONF_GROUPS = "groups"
CONF_POLL_INTERVAL = "poll_interval"

# Service names and field names
SERVICE_SET_GROUP = "set_group"
SERVICE_DELETE_GROUP = "delete_group"
CONF_GROUP_NAME = "group_name"
CONF_TARGETS = "targets"
CONF_SENSORS = "sensors"
CONF_SENSOR_TEMPLATES = "sensor_templates"
CONF_DELAY = "delay"

# Platforms forwarded by async_setup_entry.
# - sensor: deadline sensor (existing)
# - text: delay text entity (existing)
# - binary_sensor: SensorsGroup member aggregation
# - light / switch / fan / cover / media_player / lock / valve: per-domain
#   target group entities
PLATFORMS = [
    "sensor",
    "text",
    "binary_sensor",
    "light",
    "switch",
    "fan",
    "cover",
    "media_player",
    "lock",
    "valve",
]

# Domains for which HA ships a group platform that we can drive.
# Keys map GroupConfig.targets entity-id prefix to the HA group domain.
GROUPABLE_DOMAINS = frozenset(
    {"light", "switch", "fan", "cover", "media_player", "lock", "valve"}
)

__all__ = [
    "DOMAIN",
    "VERSION",
    "CONF_GROUPS",
    "CONF_POLL_INTERVAL",
    "SERVICE_SET_GROUP",
    "SERVICE_DELETE_GROUP",
    "CONF_GROUP_NAME",
    "CONF_TARGETS",
    "CONF_SENSORS",
    "CONF_SENSOR_TEMPLATES",
    "CONF_DELAY",
    "PLATFORMS",
    "GROUPABLE_DOMAINS",
]
```

- [ ] **Step 2: Run lint**

Run: `source venv/bin/activate && python -m ruff check custom_components/auto_off/const.py`
Expected: `All checks passed!`

- [ ] **Step 3: Commit**

```bash
git add custom_components/auto_off/const.py
git commit -m "feat(auto_off): add GROUPABLE_DOMAINS and expand PLATFORMS"
```

---

## Task 2: Create `group_entities.py` with subclasses that attach DeviceInfo

**Files:**
- Create: `custom_components/auto_off/group_entities.py`

- [ ] **Step 1: Write the file**

```python
"""HA-stdlib group entity subclasses bound to the auto_off device.

These entities exist purely for UI and convenience turn-off.  They do
NOT participate in the auto-off state machine: the deadline logic in
SensorGroup continues to subscribe to individual member entities.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.group.binary_sensor import BinarySensorGroup
from homeassistant.components.group.cover import CoverGroup
from homeassistant.components.group.fan import FanGroup
from homeassistant.components.group.light import LightGroup
from homeassistant.components.group.lock import LockGroup
from homeassistant.components.group.media_player import MediaPlayerGroup
from homeassistant.components.group.switch import SwitchGroup
from homeassistant.components.group.valve import ValveGroup
from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN, VERSION

_LOGGER = logging.getLogger(__name__)

# Map HA domain -> HA stdlib *Group class.  Keep in sync with GROUPABLE_DOMAINS.
_TARGET_GROUP_CLASSES: dict[str, type] = {
    "light": LightGroup,
    "switch": SwitchGroup,
    "fan": FanGroup,
    "cover": CoverGroup,
    "media_player": MediaPlayerGroup,
    "lock": LockGroup,
    "valve": ValveGroup,
}


def _device_info(group_name: str) -> DeviceInfo:
    """DeviceInfo shared by every auto_off entity for a given group."""
    return DeviceInfo(
        identifiers={(DOMAIN, group_name)},
        name=f"Auto Off: {group_name}",
        manufacturer="Auto Off",
        model="Sensor Group",
        sw_version=VERSION,
    )


class AutoOffSensorsGroup(BinarySensorGroup):
    """binary_sensor group aggregating GroupConfig.sensors.

    Attaches DeviceInfo for the auto_off group, exposes sensor_templates
    as an extra_state_attribute, and uses OR semantics (any on → on).
    """

    _attr_has_entity_name = True
    _attr_translation_key = "sensors"
    _attr_should_poll = False

    def __init__(
        self,
        *,
        group_name: str,
        entity_ids: list[str],
        sensor_templates: list[str],
    ) -> None:
        unique_id = f"{DOMAIN}_{group_name}_sensors"
        super().__init__(
            unique_id=unique_id,
            name=None,  # translation_key drives the rendered name
            device_class=BinarySensorDeviceClass.OCCUPANCY,
            entity_ids=list(entity_ids),
            mode=False,  # any-on = on
        )
        self._group_name = group_name
        self._sensor_templates = list(sensor_templates)
        self._attr_device_info = _device_info(group_name)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        base = dict(self._attr_extra_state_attributes or {})
        base["sensor_templates"] = list(self._sensor_templates)
        return base

    def update_members(
        self, entity_ids: list[str], sensor_templates: list[str]
    ) -> None:
        """Update members and templates after a set_group call.

        Caller must ensure async_write_ha_state is invoked afterwards.
        """
        self._entity_ids = list(entity_ids)
        self._attr_extra_state_attributes = {"entity_id": list(entity_ids)}
        self._sensor_templates = list(sensor_templates)


def _make_targets_group_class(domain: str, base: type) -> type:
    """Build a per-domain subclass of a HA *Group class.

    The subclass attaches DeviceInfo, has_entity_name, translation_key,
    and exposes an update_members() hook.  We build classes dynamically
    to avoid seven near-identical hand-written copies.
    """

    class _AutoOffTargetsGroup(base):  # type: ignore[valid-type,misc]
        _attr_has_entity_name = True
        _attr_translation_key = f"targets_{domain}"
        _attr_should_poll = False

        def __init__(self, *, group_name: str, entity_ids: list[str]) -> None:
            unique_id = f"{DOMAIN}_{group_name}_targets_{domain}"
            super().__init__(
                unique_id=unique_id,
                name=None,
                entity_ids=list(entity_ids),
                mode=False,  # any-on = on
            )
            self._group_name = group_name
            self._attr_device_info = _device_info(group_name)

        def update_members(self, entity_ids: list[str]) -> None:
            """Replace the tracked member list.

            Caller must ensure async_write_ha_state is invoked afterwards.
            """
            self._entity_ids = list(entity_ids)
            self._attr_extra_state_attributes = {"entity_id": list(entity_ids)}

    _AutoOffTargetsGroup.__name__ = f"AutoOffTargets{domain.title().replace('_', '')}Group"
    _AutoOffTargetsGroup.__qualname__ = _AutoOffTargetsGroup.__name__
    return _AutoOffTargetsGroup


# Pre-build one class per groupable domain.
TARGET_GROUP_ENTITY_CLASSES: dict[str, type] = {
    domain: _make_targets_group_class(domain, base)
    for domain, base in _TARGET_GROUP_CLASSES.items()
}


def split_targets_by_domain(targets: list[str]) -> dict[str, list[str]]:
    """Bucket targets by domain, skipping non-groupable domains.

    Targets whose domain is not in TARGET_GROUP_ENTITY_CLASSES are
    omitted from the returned dict (their turn-off is handled by the
    per-target fallback in SensorGroup.turn_off).  Invalid entity ids
    (no domain prefix) are also omitted.
    """
    buckets: dict[str, list[str]] = {}
    for entity_id in targets:
        if "." not in entity_id:
            continue
        domain = entity_id.split(".", 1)[0]
        if domain not in TARGET_GROUP_ENTITY_CLASSES:
            continue
        buckets.setdefault(domain, []).append(entity_id)
    return buckets


def targets_group_entity_id(domain: str, group_name: str) -> str:
    """Return the stable entity_id of the targets-group for (domain, group)."""
    return f"{domain}.auto_off_{group_name}_targets_{domain}"


def sensors_group_entity_id(group_name: str) -> str:
    """Return the stable entity_id of the sensors-group for a group."""
    return f"binary_sensor.auto_off_{group_name}_sensors"
```

- [ ] **Step 2: Run lint**

Run: `source venv/bin/activate && python -m ruff check custom_components/auto_off/group_entities.py`
Expected: `All checks passed!`

- [ ] **Step 3: Smoke import**

Run: `source venv/bin/activate && python -c "from custom_components.auto_off.group_entities import AutoOffSensorsGroup, TARGET_GROUP_ENTITY_CLASSES, split_targets_by_domain; print(sorted(TARGET_GROUP_ENTITY_CLASSES.keys()))"`
Expected: `['cover', 'fan', 'light', 'lock', 'media_player', 'switch', 'valve']`

- [ ] **Step 4: Commit**

```bash
git add custom_components/auto_off/group_entities.py
git commit -m "feat(auto_off): add group_entities module with per-domain subclasses"
```

---

## Task 3: Unit tests for `split_targets_by_domain` and entity id helpers

**Files:**
- Create: `custom_components/auto_off/tests/test_group_entities.py`

- [ ] **Step 1: Write failing tests**

```python
"""Unit tests for group_entities helpers."""

from __future__ import annotations

from custom_components.auto_off.group_entities import (
    sensors_group_entity_id,
    split_targets_by_domain,
    targets_group_entity_id,
)


class TestSplitTargetsByDomain:
    def test_mixed_domains_are_bucketed(self):
        result = split_targets_by_domain(
            ["light.kitchen", "switch.fan", "light.hallway"]
        )
        assert result == {
            "light": ["light.kitchen", "light.hallway"],
            "switch": ["switch.fan"],
        }

    def test_non_groupable_domain_is_skipped(self):
        result = split_targets_by_domain(["scene.evening", "light.kitchen"])
        assert result == {"light": ["light.kitchen"]}

    def test_invalid_entity_id_is_skipped(self):
        result = split_targets_by_domain(["not-an-id", "light.kitchen"])
        assert result == {"light": ["light.kitchen"]}

    def test_empty_input(self):
        assert split_targets_by_domain([]) == {}


class TestEntityIdHelpers:
    def test_targets_group_entity_id(self):
        assert (
            targets_group_entity_id("light", "kitchen_auto_off")
            == "light.auto_off_kitchen_auto_off_targets_light"
        )

    def test_sensors_group_entity_id(self):
        assert (
            sensors_group_entity_id("kitchen_auto_off")
            == "binary_sensor.auto_off_kitchen_auto_off_sensors"
        )
```

- [ ] **Step 2: Run tests**

Run: `source venv/bin/activate && AUTOQA_MODE=unit python -m pytest custom_components/auto_off/tests/test_group_entities.py -v`
Expected: 6 passed.

- [ ] **Step 3: Commit**

```bash
git add custom_components/auto_off/tests/test_group_entities.py
git commit -m "test(auto_off): cover split_targets_by_domain and entity id helpers"
```

---

## Task 4: Add translations for new group entity names

**Files:**
- Modify: `custom_components/auto_off/translations/en.json`

- [ ] **Step 1: Read current translations file**

Run: `cat custom_components/auto_off/translations/en.json`

Note the existing structure so the new entries merge correctly. If the file does not yet have an `entity` key, add the new block at the same level as `config` / `options`.

- [ ] **Step 2: Add entity translation keys**

Merge these entries into `entity.binary_sensor` and `entity.<domain>`:

```json
{
  "entity": {
    "binary_sensor": {
      "sensors": { "name": "Sensors" }
    },
    "light": {
      "targets_light": { "name": "Targets (Light)" }
    },
    "switch": {
      "targets_switch": { "name": "Targets (Switch)" }
    },
    "fan": {
      "targets_fan": { "name": "Targets (Fan)" }
    },
    "cover": {
      "targets_cover": { "name": "Targets (Cover)" }
    },
    "media_player": {
      "targets_media_player": { "name": "Targets (Media Player)" }
    },
    "lock": {
      "targets_lock": { "name": "Targets (Lock)" }
    },
    "valve": {
      "targets_valve": { "name": "Targets (Valve)" }
    }
  }
}
```

Preserve any existing keys; do NOT overwrite unrelated sections. If the file already has an `entity` key, deep-merge into it.

- [ ] **Step 3: Validate JSON**

Run: `source venv/bin/activate && python -c "import json; json.load(open('custom_components/auto_off/translations/en.json'))"`
Expected: no output (valid JSON).

- [ ] **Step 4: Commit**

```bash
git add custom_components/auto_off/translations/en.json
git commit -m "feat(auto_off): add translations for group entity names"
```

---

## Task 5: Add empty platform modules for each new domain

Each platform module is ~15 lines and identical in shape: an `async_setup_entry` that asks the manager for the entities it should add on that platform.

**Files:**
- Create: `custom_components/auto_off/binary_sensor.py`
- Create: `custom_components/auto_off/light.py`
- Create: `custom_components/auto_off/switch.py`
- Create: `custom_components/auto_off/fan.py`
- Create: `custom_components/auto_off/cover.py`
- Create: `custom_components/auto_off/media_player.py`
- Create: `custom_components/auto_off/lock.py`
- Create: `custom_components/auto_off/valve.py`

- [ ] **Step 1: Create each platform module**

Each file has identical body except for the `_PLATFORM` constant. Template for `binary_sensor.py`:

```python
"""binary_sensor platform for Auto Off — hosts SensorsGroup entities."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN

_PLATFORM = "binary_sensor"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Register the binary_sensor platform with the auto_off manager."""
    manager = hass.data.get(DOMAIN)
    if manager is None:
        return
    manager.register_platform_callback(_PLATFORM, async_add_entities)
```

For the other seven files, change `_PLATFORM` to the corresponding domain name (`"light"`, `"switch"`, ..., `"valve"`) and update the docstring.

- [ ] **Step 2: Run lint**

Run: `source venv/bin/activate && python -m ruff check custom_components/auto_off/`
Expected: `All checks passed!`

- [ ] **Step 3: Commit**

```bash
git add custom_components/auto_off/{binary_sensor,light,switch,fan,cover,media_player,lock,valve}.py
git commit -m "feat(auto_off): add empty platform modules for group entity domains"
```

Note: running HA with the expanded `PLATFORMS` before Task 6 wires `register_platform_callback` in the manager will cause an `AttributeError`. Task 6 adds that method; do not attempt to load the integration between Task 5 and Task 6.

---

## Task 6: Extend `IntegrationManager` with platform callbacks and group-entity lifecycle

**Files:**
- Modify: `custom_components/auto_off/integration_manager.py`

- [ ] **Step 1: Add imports**

At the top of `integration_manager.py`, add these imports below the existing ones:

```python
from homeassistant.core import split_entity_id
from homeassistant.helpers import entity_registry as er_helper

from .auto_off import GroupConfig
from .group_entities import (
    TARGET_GROUP_ENTITY_CLASSES,
    AutoOffSensorsGroup,
    sensors_group_entity_id,
    split_targets_by_domain,
    targets_group_entity_id,
)
```

(`er_helper` aliases `entity_registry` only if the existing `entity_registry as er` import is already present; otherwise just use `er`. Read the file first to see which alias is used.)

- [ ] **Step 2: Add instance state in `__init__`**

In `IntegrationManager.__init__`, below the existing deadline/text entity dicts, add:

```python
# Per-platform AddEntitiesCallback captured from each platform's async_setup_entry
self._platform_callbacks: dict[str, Any] = {}
# Live sensors-group entities, keyed by group_name
self._sensors_group_entities: dict[str, AutoOffSensorsGroup] = {}
# Live per-domain targets-group entities: (group_name, domain) -> entity instance
self._targets_group_entities: dict[tuple[str, str], Any] = {}
```

- [ ] **Step 3: Add `register_platform_callback`**

Add as a new method on the class:

```python
def register_platform_callback(
    self, platform: str, async_add_entities: AddEntitiesCallback
) -> None:
    """Store the AddEntitiesCallback for a forwarded platform.

    Called once per platform from that platform's async_setup_entry.
    On first-time registration we retroactively emit all already-known
    group entities for that platform so they appear immediately instead
    of waiting for the next set_group call.
    """
    self._platform_callbacks[platform] = async_add_entities
    self._emit_initial_entities_for_platform(platform)

def _emit_initial_entities_for_platform(self, platform: str) -> None:
    """Create entities on this platform for every already-configured group."""
    async_add_entities = self._platform_callbacks.get(platform)
    if async_add_entities is None:
        return
    new_entities = []
    for group_name, config_dict in self._groups_data.items():
        config = GroupConfig.model_validate(config_dict)
        if platform == "binary_sensor":
            if group_name in self._sensors_group_entities:
                continue
            entity = AutoOffSensorsGroup(
                group_name=group_name,
                entity_ids=list(config.sensors),
                sensor_templates=list(config.sensor_templates),
            )
            self._sensors_group_entities[group_name] = entity
            new_entities.append(entity)
        elif platform in TARGET_GROUP_ENTITY_CLASSES:
            buckets = split_targets_by_domain(list(config.targets))
            ids = buckets.get(platform, [])
            if not ids:
                continue
            key = (group_name, platform)
            if key in self._targets_group_entities:
                continue
            cls = TARGET_GROUP_ENTITY_CLASSES[platform]
            entity = cls(group_name=group_name, entity_ids=ids)
            self._targets_group_entities[key] = entity
            new_entities.append(entity)
    if new_entities:
        async_add_entities(new_entities)
```

- [ ] **Step 4: Add read accessors**

Add these methods on the class:

```python
def get_group_config(self, group_name: str) -> GroupConfig | None:
    """Return the active GroupConfig for a group, or None if absent."""
    config_dict = self._groups_data.get(group_name)
    if config_dict is None:
        return None
    try:
        return GroupConfig.model_validate(config_dict)
    except Exception as exc:  # noqa: BLE001 — defensive; manager must not crash
        _LOGGER.warning("Invalid stored config for group '%s': %s", group_name, exc)
        return None

def get_group_targets_by_domain(self, group_name: str) -> dict[str, list[str]]:
    """Return {domain: [entity_id, ...]} for groupable targets of a group."""
    config = self.get_group_config(group_name)
    if config is None:
        return {}
    return split_targets_by_domain(list(config.targets))

def get_group_member_group_entity_ids(self, group_name: str) -> list[str]:
    """Return the entity_ids of all live targets-group entities for a group.

    Used by SensorGroup.turn_off to dispatch one <domain>.turn_off call
    per domain-group.
    """
    return [
        targets_group_entity_id(domain, group_name)
        for (name, domain) in self._targets_group_entities.keys()
        if name == group_name
    ]
```

- [ ] **Step 5: Extend `set_group`**

Locate the existing `set_group` method. After the existing code that updates the AutoOffManager and deadline/text entities, append a new block that synchronises group entities:

```python
    # --- sync group entities ---
    await self._sync_group_entities(group_name, config_dict, is_new)
```

Then add `_sync_group_entities` as a new method:

```python
async def _sync_group_entities(
    self, group_name: str, config_dict: dict, is_new: bool
) -> None:
    """Create/update/remove group entities to match config_dict.

    Safe to call whether or not the platform callbacks are registered —
    missing callbacks mean we skip emission and will retry on
    register_platform_callback.
    """
    config = GroupConfig.model_validate(config_dict)

    # Sensors-group
    sensors_cb = self._platform_callbacks.get("binary_sensor")
    sensors_entity = self._sensors_group_entities.get(group_name)
    if sensors_entity is None:
        entity = AutoOffSensorsGroup(
            group_name=group_name,
            entity_ids=list(config.sensors),
            sensor_templates=list(config.sensor_templates),
        )
        self._sensors_group_entities[group_name] = entity
        if sensors_cb is not None:
            sensors_cb([entity])
    else:
        sensors_entity.update_members(
            entity_ids=list(config.sensors),
            sensor_templates=list(config.sensor_templates),
        )
        if sensors_entity.hass is not None:
            sensors_entity.async_write_ha_state()

    # Targets-groups per domain
    desired = split_targets_by_domain(list(config.targets))
    current_domains = {
        domain for (gname, domain) in self._targets_group_entities if gname == group_name
    }
    desired_domains = set(desired.keys())

    # Remove domains that are no longer present
    ent_reg = er.async_get(self.hass)
    for gone_domain in current_domains - desired_domains:
        key = (group_name, gone_domain)
        entity = self._targets_group_entities.pop(key, None)
        if entity is None:
            continue
        entity_id = targets_group_entity_id(gone_domain, group_name)
        ent_reg.async_remove(entity_id)

    # Add / update domains that should exist
    for domain, ids in desired.items():
        key = (group_name, domain)
        existing = self._targets_group_entities.get(key)
        cb = self._platform_callbacks.get(domain)
        if existing is None:
            cls = TARGET_GROUP_ENTITY_CLASSES[domain]
            entity = cls(group_name=group_name, entity_ids=ids)
            self._targets_group_entities[key] = entity
            if cb is not None:
                cb([entity])
        else:
            existing.update_members(entity_ids=ids)
            if existing.hass is not None:
                existing.async_write_ha_state()
```

- [ ] **Step 6: Extend `delete_group`**

In the existing `delete_group` method, after the deadline/text entity removal, add:

```python
    # Remove sensors-group
    entity = self._sensors_group_entities.pop(group_name, None)
    if entity is not None:
        ent_reg.async_remove(sensors_group_entity_id(group_name))

    # Remove every per-domain targets-group
    ent_reg = er.async_get(self.hass)
    for key in [k for k in self._targets_group_entities if k[0] == group_name]:
        self._targets_group_entities.pop(key, None)
        _, domain = key
        ent_reg.async_remove(targets_group_entity_id(domain, group_name))
```

(If `ent_reg` is already bound earlier in `delete_group`, drop the redundant assignment.)

- [ ] **Step 7: Run lint**

Run: `source venv/bin/activate && python -m ruff check custom_components/auto_off/integration_manager.py`
Expected: `All checks passed!`

- [ ] **Step 8: Commit**

```bash
git add custom_components/auto_off/integration_manager.py
git commit -m "feat(auto_off): manage group entities lifecycle in IntegrationManager"
```

---

## Task 7: Unit tests for `IntegrationManager` group-entity helpers

**Files:**
- Modify: `custom_components/auto_off/tests/test_integration_manager.py`

- [ ] **Step 1: Add the tests**

Append (or merge into the existing `TestIntegrationManager` class) these tests:

```python
class TestGroupEntityHelpers:
    async def test_get_group_config_returns_parsed(self, manager_factory):
        """get_group_config materialises stored dict as GroupConfig."""
        manager = manager_factory(
            groups={
                "k": {
                    "targets": ["light.a", "switch.b"],
                    "sensors": ["binary_sensor.m"],
                    "sensor_templates": [],
                    "delay": 5,
                }
            }
        )
        cfg = manager.get_group_config("k")
        assert cfg is not None
        assert cfg.targets == ["light.a", "switch.b"]
        assert cfg.sensors == ["binary_sensor.m"]

    async def test_get_group_config_unknown(self, manager_factory):
        manager = manager_factory(groups={})
        assert manager.get_group_config("nope") is None

    async def test_get_group_targets_by_domain_buckets(self, manager_factory):
        manager = manager_factory(
            groups={
                "k": {
                    "targets": [
                        "light.a",
                        "switch.b",
                        "light.c",
                        "scene.evening",  # non-groupable, dropped
                    ],
                    "sensors": ["binary_sensor.m"],
                    "sensor_templates": [],
                    "delay": 0,
                }
            }
        )
        result = manager.get_group_targets_by_domain("k")
        assert result == {
            "light": ["light.a", "light.c"],
            "switch": ["switch.b"],
        }
```

The `manager_factory` fixture should already exist in `conftest_unit.py` from earlier work. If not, the simplest shape is:

```python
@pytest.fixture
def manager_factory(hass):
    def _make(groups):
        entry = MagicMock()
        entry.data = {"groups": groups, "poll_interval": 15}
        entry.entry_id = "test_entry"
        return IntegrationManager(hass, entry)
    return _make
```

Add the fixture to `conftest_unit.py` only if missing.

- [ ] **Step 2: Run tests**

Run: `source venv/bin/activate && AUTOQA_MODE=unit python -m pytest custom_components/auto_off/tests/test_integration_manager.py -v`
Expected: all tests pass (existing + 3 new).

- [ ] **Step 3: Commit**

```bash
git add custom_components/auto_off/tests/test_integration_manager.py custom_components/auto_off/tests/conftest_unit.py
git commit -m "test(auto_off): cover group entity accessors on IntegrationManager"
```

---

## Task 8: Route `SensorGroup.turn_off` through group entities with per-target fallback

**Files:**
- Modify: `custom_components/auto_off/auto_off.py`

- [ ] **Step 1: Extend `SensorGroup.__init__` signature**

Locate `class SensorGroup` (around line 338) and add a `manager` keyword to the constructor. Also pass `manager` through from `AutoOffManager` to `SensorGroup`.

```python
class SensorGroup:
    def __init__(
        self,
        hass: HomeAssistant,
        group_id: str,
        config: GroupConfig,
        on_deadline_change: Callable[[str, str | None], None] | None = None,
        *,
        manager: "IntegrationManager | None" = None,
    ):
        self.hass = hass
        self.group_id = group_id
        self._config = config
        self._on_deadline_change = on_deadline_change
        self._manager = manager
        ...  # rest unchanged
```

(The `"IntegrationManager | None"` string avoids a circular import; do not add an actual `from .integration_manager import IntegrationManager`.)

- [ ] **Step 2: Thread `manager` through `AutoOffManager`**

In `AutoOffManager.__init__`, accept an optional manager argument:

```python
def __init__(
    self,
    hass: HomeAssistant,
    config: dict[str, GroupConfig],
    *,
    on_deadline_change: Callable[[str, str | None], None] | None = None,
    integration_manager: Any | None = None,
) -> None:
    self.hass = hass
    self.config = config
    self._on_deadline_change = on_deadline_change
    self._integration_manager = integration_manager
    ...
```

And in `async_init_groups`, pass it to each new `SensorGroup`:

```python
self._groups[group_id] = SensorGroup(
    self.hass,
    group_id,
    group_config,
    on_deadline_change=self._on_deadline_change,
    manager=self._integration_manager,
)
```

Then in `IntegrationManager.__init__`, when constructing `AutoOffManager`:

```python
self.auto_off = AutoOffManager(
    hass,
    group_configs,
    on_deadline_change=self._on_deadline_change,
    integration_manager=self,
)
```

- [ ] **Step 3: Rewrite `_turn_off_targets`**

Replace the existing method body with:

```python
async def _turn_off_targets(self):
    # Clear timer state BEFORE turning off - timer has fired
    self._timer = None
    self._timer_deadline = None
    self._notify_deadline_change()

    # Primary path: one <domain>.turn_off call per groupable domain.
    dispatched_domains: set[str] = set()
    if self._manager is not None:
        for entity_id in self._manager.get_group_member_group_entity_ids(
            self.group_id
        ):
            domain = entity_id.split(".", 1)[0]
            dispatched_domains.add(domain)
            try:
                await self.hass.services.async_call(
                    domain,
                    "turn_off",
                    {"entity_id": entity_id},
                    blocking=False,
                )
            except Exception as exc:  # noqa: BLE001 - never fail the whole expiry
                _LOGGER.warning(
                    "[Group %s] Group turn_off on %s failed: %s",
                    self.group_id,
                    entity_id,
                    exc,
                )

    # Fallback: per-entity for targets whose domain has no group platform.
    from .const import GROUPABLE_DOMAINS  # local import to avoid cycle

    tasks = []
    for target in self._targets:
        entity_id = getattr(target, "entity_id", "")
        if "." not in entity_id:
            continue
        domain = entity_id.split(".", 1)[0]
        if domain in dispatched_domains:
            continue  # handled by group turn_off
        if domain in GROUPABLE_DOMAINS:
            continue  # should already be covered; skip defensively
        tasks.append(target.turn_off())
    if tasks:
        await asyncio.gather(*tasks)
    _LOGGER.info("All targets turned off after deadline.")
```

- [ ] **Step 4: Run lint and existing tests**

Run: `source venv/bin/activate && python -m ruff check custom_components/auto_off/auto_off.py custom_components/auto_off/integration_manager.py`
Expected: `All checks passed!`

Run: `source venv/bin/activate && AUTOQA_MODE=unit python -m pytest custom_components/auto_off/tests/ --ignore=custom_components/auto_off/tests/test_integration_e2e.py --ignore=custom_components/auto_off/tests/test_e2e_playwright.py -q`
Expected: all unit tests pass.

- [ ] **Step 5: Commit**

```bash
git add custom_components/auto_off/auto_off.py custom_components/auto_off/integration_manager.py
git commit -m "feat(auto_off): delegate turn_off to group entities with fallback"
```

---

## Task 9: Unit test for `SensorGroup.turn_off` dispatching group calls

**Files:**
- Modify: `custom_components/auto_off/tests/test_auto_off.py` (or create if missing)

- [ ] **Step 1: Write the test**

Add this test class (adjust imports to match existing style in the file):

```python
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.auto_off.auto_off import GroupConfig, SensorGroup


class TestTurnOffRoutingThroughGroups:
    async def test_turn_off_dispatches_group_service_per_domain(self, hass):
        hass.services.async_call = AsyncMock()

        manager = MagicMock()
        manager.get_group_member_group_entity_ids.return_value = [
            "light.auto_off_k_targets_light",
            "switch.auto_off_k_targets_switch",
        ]

        config = GroupConfig(
            targets=["light.a", "switch.b"],
            sensors=["binary_sensor.m"],
            sensor_templates=[],
            delay=0,
        )
        group = SensorGroup(hass, "k", config, manager=manager)

        await group._turn_off_targets()

        # Two calls, one per domain, with the group entity id
        calls = hass.services.async_call.await_args_list
        assert len(calls) == 2
        domains = {c.args[0] for c in calls}
        assert domains == {"light", "switch"}
        for c in calls:
            assert c.args[1] == "turn_off"
            assert c.args[2]["entity_id"].startswith(c.args[0] + ".auto_off_k_targets_")

    async def test_turn_off_falls_back_for_non_groupable_target(self, hass):
        hass.services.async_call = AsyncMock()

        manager = MagicMock()
        # No groupable targets → empty list
        manager.get_group_member_group_entity_ids.return_value = []

        config = GroupConfig(
            targets=["scene.evening"],
            sensors=["binary_sensor.m"],
            sensor_templates=[],
            delay=0,
        )
        group = SensorGroup(hass, "k", config, manager=manager)

        # Replace Target.turn_off with a spy
        target_spy = AsyncMock()
        for target in group._targets:
            target.turn_off = target_spy

        await group._turn_off_targets()

        # No group service call was dispatched
        assert hass.services.async_call.await_count == 0
        # The per-target fallback was invoked
        assert target_spy.await_count == 1
```

- [ ] **Step 2: Run tests**

Run: `source venv/bin/activate && AUTOQA_MODE=unit python -m pytest custom_components/auto_off/tests/test_auto_off.py -v -k "TurnOffRouting"`
Expected: 2 passed.

- [ ] **Step 3: Commit**

```bash
git add custom_components/auto_off/tests/test_auto_off.py
git commit -m "test(auto_off): cover group-dispatch and fallback in _turn_off_targets"
```

---

## Task 10: Strip extra attributes from deadline sensor

**Files:**
- Modify: `custom_components/auto_off/sensor.py`

- [ ] **Step 1: Simplify `extra_state_attributes`**

Replace the current implementation with:

```python
@property
def extra_state_attributes(self) -> dict[str, Any]:
    """Return extra state attributes."""
    return {"deadline_iso": self._deadline_iso}
```

Remove the unused `_manager` reference if it is no longer read (check the file; `_manager` is still needed for future use if any. If `_manager` is only used inside the deleted attribute code, remove the parameter from `__init__` and the corresponding argument in `IntegrationManager.sensor_platform_ready`).

- [ ] **Step 2: Run lint and tests**

Run: `source venv/bin/activate && python -m ruff check custom_components/auto_off/sensor.py`
Expected: `All checks passed!`

Run: `source venv/bin/activate && AUTOQA_MODE=unit python -m pytest custom_components/auto_off/tests/test_integration_manager.py -v`
Expected: all pass (existing tests that referenced the deleted attributes will be updated in Task 11).

- [ ] **Step 3: Commit**

```bash
git add custom_components/auto_off/sensor.py
git commit -m "refactor(auto_off): remove legacy attrs from deadline sensor"
```

---

## Task 11: Update existing tests that asserted deleted attributes

**Files:**
- Modify: `custom_components/auto_off/tests/test_integration_manager.py`
- Modify (if present): any other test referencing `deadline sensor.targets / sensors / sensor_templates`

- [ ] **Step 1: Find and rewrite stale assertions**

Run: `source venv/bin/activate && python -m pytest custom_components/auto_off/tests/ --ignore=custom_components/auto_off/tests/test_integration_e2e.py --ignore=custom_components/auto_off/tests/test_e2e_playwright.py -q 2>&1 | grep -E "FAIL|assert" | head -20`

For each failing assertion that relied on the deleted deadline-sensor attributes, rewrite it to assert on the new group entities instead. Example pattern:

```python
# BEFORE
assert entity.extra_state_attributes["targets"] == ["light.a"]

# AFTER
group_entity = manager._targets_group_entities[(group_name, "light")]
assert group_entity._entity_ids == ["light.a"]
```

- [ ] **Step 2: Run tests**

Run: `source venv/bin/activate && AUTOQA_MODE=unit python -m pytest custom_components/auto_off/tests/ --ignore=custom_components/auto_off/tests/test_integration_e2e.py --ignore=custom_components/auto_off/tests/test_e2e_playwright.py -v`
Expected: all pass.

- [ ] **Step 3: Commit**

```bash
git add custom_components/auto_off/tests/
git commit -m "test(auto_off): move attribute assertions from deadline sensor to group entities"
```

---

## Task 12: E2E scenario — new entities on device page + group-level turn_off

**Files:**
- Modify: `custom_components/auto_off/tests/test_integration_e2e.py`

- [ ] **Step 1: Add a test**

Append a new test method to the existing e2e class (do not break existing tests):

```python
@pytest.mark.docker_e2e
async def test_group_entities_are_created_per_domain(self, ha_instance):
    """After set_group, expect sensors-group + one targets-group per domain."""
    await ha_instance.call_service(
        "auto_off",
        "set_group",
        {
            "group_name": "e2e_groups",
            "targets": [
                "light.e2e_target_light",
                "switch.e2e_target_switch",
            ],
            "sensors": ["binary_sensor.e2e_trigger"],
            "delay": 1,
        },
    )

    # Poll up to ~5s for registry propagation
    for _ in range(50):
        entities = await ha_instance.get_states()
        ids = {e["entity_id"] for e in entities}
        if {
            "binary_sensor.auto_off_e2e_groups_sensors",
            "light.auto_off_e2e_groups_targets_light",
            "switch.auto_off_e2e_groups_targets_switch",
        }.issubset(ids):
            break
        await asyncio.sleep(0.1)
    else:
        pytest.fail(
            "Group entities did not appear in states: "
            f"{sorted(i for i in ids if 'auto_off_e2e_groups' in i)}"
        )
```

If `ha_instance.get_states` / `call_service` helpers have different signatures in `conftest_e2e.py`, adapt to the local API. Do not invent helpers.

- [ ] **Step 2: Run e2e locally if docker is available**

Run: `./ha-test-kit/run_e2e.sh -k test_group_entities_are_created_per_domain`
Expected: PASS. (If docker is not available, skip this step and rely on CI.)

- [ ] **Step 3: Commit**

```bash
git add custom_components/auto_off/tests/test_integration_e2e.py
git commit -m "test(auto_off): e2e check for group entities on device page"
```

---

## Task 13: Update README

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Replace the device-entities paragraph**

Find the section in `README.md` that describes entities created per group (currently mentions `deadline` and `delay_minutes`). Replace it with:

```markdown
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
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: describe per-domain group entities in README"
```

---

## Task 14: Final verification — full test suite + lint

- [ ] **Step 1: Run ruff**

Run: `source venv/bin/activate && python -m ruff check custom_components/`
Expected: `All checks passed!`

- [ ] **Step 2: Run the full unit test suite**

Run: `source venv/bin/activate && AUTOQA_MODE=unit python -m pytest custom_components/auto_off/tests/ --ignore=custom_components/auto_off/tests/test_integration_e2e.py --ignore=custom_components/auto_off/tests/test_e2e_playwright.py -q`
Expected: all pass.

- [ ] **Step 3: Bump version and push**

```bash
NEW_VERSION=$(date +"%y%m%d%H%M")
sed -i "s/\"version\": \"[0-9]*\"/\"version\": \"$NEW_VERSION\"/" custom_components/auto_off/manifest.json
git add custom_components/auto_off/manifest.json
git commit -m "chore: bump version to $NEW_VERSION"
git push origin master
```

Expected: push succeeds. HACS is configured to track the default branch (no releases), so the new version becomes available on the next HACS refresh.

- [ ] **Step 4: Verify in HA**

Open Home Assistant → HACS → Auto Off → Update (or Reload data if the update banner isn't visible). After restart, open the `Auto Off: <group>` device page and confirm the new entities appear.

---
