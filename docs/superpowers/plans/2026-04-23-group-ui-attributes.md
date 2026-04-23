# Group UI Attributes + Targets Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose `targets`, `sensors`, and `sensor_templates` of each `auto_off` group as attributes on `DeadlineSensorEntity` (visible from the device page in HA UI) and simultaneously simplify `Target` to accept only concrete entity ids (no Jinja).

**Architecture:** `GroupConfig.targets` gains a syntactic validator (`homeassistant.core.valid_entity_id`) that warns but keeps invalid items. `Target` drops template-render logic and becomes a single-entity wrapper with warn-and-skip on missing state. `IntegrationManager` gains `get_group_config(group_name)` and forces `async_write_ha_state()` on the deadline sensor after every `update_group_config`. `DeadlineSensorEntity.extra_state_attributes` reads the current `GroupConfig` through the manager.

**Tech Stack:** Python 3.13, Home Assistant 2025.12+, pydantic v2, pytest, pytest-asyncio, Docker-based test harness (`ha-test-kit`).

**Design spec:** `docs/superpowers/specs/2026-04-23-group-ui-attributes-design.md`.

---

## File structure

Modified (no new files created):

- `custom_components/auto_off/auto_off.py` — `GroupConfig` validator; `Target` simplified.
- `custom_components/auto_off/sensor.py` — `DeadlineSensorEntity` constructor and `extra_state_attributes`.
- `custom_components/auto_off/integration_manager.py` — `get_group_config`; `async_write_ha_state` after config update; pass `self` to `DeadlineSensorEntity`.
- `custom_components/auto_off/tests/test_group_config.py` — new tests for target syntax warnings.
- `custom_components/auto_off/tests/test_target.py` — new file, tests for `Target` skip behaviour.
- `custom_components/auto_off/tests/test_deadline_sensor.py` — new file, tests for attributes.
- `custom_components/auto_off/tests/test_integration_manager.py` — extend with `get_group_config` + `async_write_ha_state` tests.
- `custom_components/auto_off/tests/test_integration_e2e.py` — new scenario `test_late_binding_target`.
- `custom_components/auto_off/tests/ha_packages/auto_off_test.yaml` — add `late_target` fixtures if needed.
- `README.md` — update auto_off section.

---

## Task ordering rationale

Order is chosen so every commit leaves the working tree green:

1. **Tasks 1–3** — tighten `GroupConfig` (warn on bad targets) via TDD. Isolated pydantic change, no wiring yet.
2. **Tasks 4–6** — simplify `Target` (remove template logic, add skip flag and state-machine check) via TDD. Still isolated; existing `SensorGroup` usage works because the public API (`is_on`, `turn_off`, `start_tracking`, `stop_tracking`) is preserved.
3. **Task 7** — update `SensorGroup._init_from_config` comments/log and remove now-dead fallback paths that reference `_current_entity_ids > 1` for templates (optional cleanup — scoped precisely).
4. **Tasks 8–10** — surface the attributes: `IntegrationManager.get_group_config`, pass manager to `DeadlineSensorEntity`, extend `extra_state_attributes`, force `async_write_ha_state` on `update_group_config`. TDD throughout.
5. **Task 11** — e2e `test_late_binding_target`.
6. **Task 12** — update existing e2e fixtures if they contain Jinja targets; update README.
7. **Task 13** — final verification run.

Each task ends with a commit and the relevant pytest invocation.

---

### Task 1: Add `_LOGGER.warning` for invalid target syntax in `GroupConfig`

**Files:**
- Modify: `custom_components/auto_off/auto_off.py:15-34`
- Test: `custom_components/auto_off/tests/test_group_config.py`

- [ ] **Step 1: Write the failing test**

Append to `custom_components/auto_off/tests/test_group_config.py`:

```python
import logging

from custom_components.auto_off.auto_off import GroupConfig


class TestGroupConfigTargetSyntax:
    def test_warns_but_keeps_invalid_target_syntax(self, caplog):
        caplog.set_level(logging.WARNING, logger="custom_components.auto_off.auto_off")
        cfg = GroupConfig(
            targets=["light.good", "not-an-entity-id"],
            sensors=["binary_sensor.motion"],
        )
        assert cfg.targets == ["light.good", "not-an-entity-id"]
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("not-an-entity-id" in r.message for r in warnings)

    def test_warns_on_template_string_in_targets(self, caplog):
        caplog.set_level(logging.WARNING, logger="custom_components.auto_off.auto_off")
        template = "{{ states('light.x') }}"
        cfg = GroupConfig(
            targets=[template],
            sensors=["binary_sensor.motion"],
        )
        assert cfg.targets == [template]
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any(template in r.message for r in warnings)

    def test_no_warning_for_all_valid_targets(self, caplog):
        caplog.set_level(logging.WARNING, logger="custom_components.auto_off.auto_off")
        GroupConfig(
            targets=["light.a", "switch.b"],
            sensors=["binary_sensor.motion"],
        )
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warnings == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `AUTOQA_MODE=unit pytest custom_components/auto_off/tests/test_group_config.py::TestGroupConfigTargetSyntax -v`
Expected: FAIL — no warning is currently emitted.

- [ ] **Step 3: Add a field validator in `auto_off.py`**

Modify `custom_components/auto_off/auto_off.py`. Add import:

```python
from homeassistant.core import valid_entity_id
from pydantic import BaseModel, field_validator, model_validator
```

Add the validator inside the `GroupConfig` class, right after the field declarations (before `_require_targets_and_sensor_source`):

```python
@field_validator("targets")
@classmethod
def _warn_on_non_entity_targets(cls, value: list[str]) -> list[str]:
    """Warn on syntactically invalid entity ids in `targets`.

    Invalid items are kept in the list so they remain visible in the UI
    attribute and are skipped at turn_off time.
    """
    for item in value:
        if not isinstance(item, str) or not valid_entity_id(item):
            _LOGGER.warning(
                "GroupConfig: target %r is not a valid entity_id, "
                "it will be skipped at turn_off",
                item,
            )
    return value
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `AUTOQA_MODE=unit pytest custom_components/auto_off/tests/test_group_config.py -v`
Expected: all tests pass including the existing ones.

- [ ] **Step 5: Commit**

```bash
git add custom_components/auto_off/auto_off.py custom_components/auto_off/tests/test_group_config.py
git commit -m "feat(auto_off): warn on invalid entity_id syntax in GroupConfig.targets"
```

---

### Task 2: Preserve invalid targets on the UI path (no filtering)

**Files:**
- Test-only (sanity): `custom_components/auto_off/tests/test_group_config.py`

The previous task already keeps invalid items in the list (the validator returns `value` unchanged). Add one explicit regression test so future refactors cannot silently filter.

- [ ] **Step 1: Write the test**

Append to `test_group_config.py` under `TestGroupConfigTargetSyntax`:

```python
def test_invalid_targets_survive_model_dump(self):
    cfg = GroupConfig(
        targets=["light.good", "bad id"],
        sensors=["binary_sensor.motion"],
    )
    dumped = cfg.model_dump()
    assert dumped["targets"] == ["light.good", "bad id"]
```

- [ ] **Step 2: Run it — should already pass**

Run: `AUTOQA_MODE=unit pytest custom_components/auto_off/tests/test_group_config.py::TestGroupConfigTargetSyntax::test_invalid_targets_survive_model_dump -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add custom_components/auto_off/tests/test_group_config.py
git commit -m "test(auto_off): guard that invalid targets survive GroupConfig round-trip"
```

---

### Task 3: Rewrite `Target.__init__` to drop template detection

**Files:**
- Modify: `custom_components/auto_off/auto_off.py:215-228`
- Test: `custom_components/auto_off/tests/test_target.py` (new)

- [ ] **Step 1: Create the test file with the failing test**

File: `custom_components/auto_off/tests/test_target.py`

```python
"""Tests for the simplified Target class."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.auto_off.auto_off import Target


@pytest.fixture
def target_hass():
    hass = MagicMock()
    hass.states = MagicMock()
    hass.services = MagicMock()
    hass.services.async_call = AsyncMock()
    return hass


class TestTargetInit:
    def test_accepts_valid_entity_id(self, target_hass):
        t = Target(target_hass, "light.kitchen", AsyncMock())
        assert t.entity_id == "light.kitchen"
        assert t._skip is False

    def test_marks_invalid_entity_id_as_skip(self, target_hass, caplog):
        caplog.set_level(logging.DEBUG, logger="custom_components.auto_off.auto_off")
        t = Target(target_hass, "not-valid", AsyncMock())
        assert t.entity_id == "not-valid"
        assert t._skip is True

    def test_marks_template_string_as_skip(self, target_hass):
        t = Target(target_hass, "{{ states('light.x') }}", AsyncMock())
        assert t._skip is True
```

- [ ] **Step 2: Run test — verify failure**

Run: `AUTOQA_MODE=unit pytest custom_components/auto_off/tests/test_target.py::TestTargetInit -v`
Expected: FAIL — `Target` has no `_skip` attribute; `entity_id` is a `@property` that returns list element.

- [ ] **Step 3: Rewrite `Target.__init__` and drop template helpers**

Replace `class Target:` block in `custom_components/auto_off/auto_off.py` (lines 215-375) with the following simplified implementation:

```python
class Target:
    """Single-entity wrapper for turn-off targets.

    `entity_id` must be a syntactically valid Home Assistant entity id. If
    not, the Target is constructed with `_skip=True`; all subsequent
    operations are no-ops. Missing entities in the state machine are handled
    separately at turn_off time (warn + skip).
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entity_id: str,
        on_state_change_callback,
    ):
        self.hass = hass
        self.entity_id = entity_id
        self._on_change_callback = on_state_change_callback
        self._unsub = None
        self._last_known_good_state: bool | None = None
        self._skip = not (isinstance(entity_id, str) and valid_entity_id(entity_id))

    async def start_tracking(self):
        """Subscribe to state changes for this single entity."""
        if self._skip or self._unsub is not None:
            return

        if self.hass.states.get(self.entity_id) is None:
            _LOGGER.warning(
                "Target %s does not exist in state machine, skipping tracking",
                self.entity_id,
            )
            return

        try:
            self._last_known_good_state = await self.is_on()
            self._unsub = async_track_state_change_event(
                self.hass, [self.entity_id], self._handle_my_changes
            )
            _LOGGER.debug(
                "Target '%s' started tracking, initial state: %s",
                self.entity_id,
                self._last_known_good_state,
            )
        except Exception as e:
            _LOGGER.error("Failed to track target '%s': %s", self.entity_id, e)

    async def _handle_my_changes(self, event):
        new_state = event.data.get("new_state")
        if not new_state or new_state.state in ("unknown", "unavailable"):
            _LOGGER.debug(
                "Target %s state is invalid (%s), ignoring",
                self.entity_id,
                new_state.state if new_state else "None",
            )
            return

        current = await self.is_on()
        if self._last_known_good_state == current:
            _LOGGER.debug(
                "Target '%s' state unchanged (%s), ignoring", self.entity_id, current
            )
            return

        old = self._last_known_good_state
        _LOGGER.info(
            "Target '%s' state changed: %s -> %s", self.entity_id, old, current
        )
        self._last_known_good_state = current
        if self._on_change_callback:
            await self._on_change_callback(self, old, current)

    async def is_on(self) -> bool:
        if self._skip:
            return False
        state = self.hass.states.get(self.entity_id)
        if state is None:
            return False
        return state.state not in ("unavailable", "unknown", "off")

    async def turn_off(self):
        if self._skip:
            return
        state = self.hass.states.get(self.entity_id)
        if state is None:
            _LOGGER.warning(
                "Target %s not found in state machine, skipping turn_off",
                self.entity_id,
            )
            return

        domain = self.entity_id.split(".")[0]
        try:
            await self.hass.services.async_call(
                domain, "turn_off", {"entity_id": self.entity_id}, blocking=True
            )
            _LOGGER.info("Target '%s' turned OFF", self.entity_id)
        except Exception as e:
            _LOGGER.error("Failed to turn off target '%s': %s", self.entity_id, e)

    async def stop_tracking(self):
        if self._unsub:
            self._unsub()
            self._unsub = None
            _LOGGER.debug("Target '%s' stopped tracking", self.entity_id)

    @property
    def raw(self) -> str:
        """Back-compat alias: SensorGroup and log statements use `raw`."""
        return self.entity_id
```

Remove the top-level `Template` import if it is no longer used (check with grep; `SensorGroup.get_delay` still uses it, so keep it).

- [ ] **Step 4: Run Target tests**

Run: `AUTOQA_MODE=unit pytest custom_components/auto_off/tests/test_target.py::TestTargetInit -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add custom_components/auto_off/auto_off.py custom_components/auto_off/tests/test_target.py
git commit -m "refactor(auto_off): simplify Target to single entity_id with skip flag"
```

---

### Task 4: `Target.turn_off` skip-when-missing test + verify

**Files:**
- Test: `custom_components/auto_off/tests/test_target.py`

- [ ] **Step 1: Write the failing test**

Append to `test_target.py`:

```python
class TestTargetTurnOff:
    async def test_turn_off_no_op_when_skip(self, target_hass):
        t = Target(target_hass, "invalid id", AsyncMock())
        await t.turn_off()
        target_hass.services.async_call.assert_not_called()

    async def test_turn_off_skips_when_state_missing(self, target_hass, caplog):
        caplog.set_level(logging.WARNING, logger="custom_components.auto_off.auto_off")
        target_hass.states.get = MagicMock(return_value=None)
        t = Target(target_hass, "light.future", AsyncMock())
        await t.turn_off()
        target_hass.services.async_call.assert_not_called()
        assert any(
            "light.future" in r.message and "not found" in r.message
            for r in caplog.records
            if r.levelno == logging.WARNING
        )

    async def test_turn_off_calls_service_when_state_present(self, target_hass):
        state = MagicMock()
        state.state = "on"
        target_hass.states.get = MagicMock(return_value=state)
        t = Target(target_hass, "light.kitchen", AsyncMock())
        await t.turn_off()
        target_hass.services.async_call.assert_called_once_with(
            "light", "turn_off", {"entity_id": "light.kitchen"}, blocking=True
        )
```

Add `pytestmark = pytest.mark.asyncio` at module level if missing (check `conftest_unit.py` — `asyncio_mode` may already be `auto` in `pyproject.toml`; if yes, omit).

Check:
```bash
grep -E "asyncio_mode" ha-test-kit/pyproject.toml pyproject.toml
```

If `asyncio_mode = "auto"` is set, no decorator needed.

- [ ] **Step 2: Run — expect PASS (logic already in place from Task 3)**

Run: `AUTOQA_MODE=unit pytest custom_components/auto_off/tests/test_target.py::TestTargetTurnOff -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add custom_components/auto_off/tests/test_target.py
git commit -m "test(auto_off): cover Target.turn_off skip and state-missing paths"
```

---

### Task 5: Fix `SensorGroup` references to removed `Target._current_entity_ids`

**Files:**
- Modify: `custom_components/auto_off/auto_off.py:553-587`

Task 3 removed `_current_entity_ids` from `Target`. `SensorGroup._log_state_transitions` still references it via `hasattr`. Clean that up so logs remain sensible.

- [ ] **Step 1: Write an integration-ish test that exercises `check_and_set_deadline`**

Append to `custom_components/auto_off/tests/test_target.py` (same fixtures) — or create `test_sensor_group_smoke.py` (either; prefer the latter to keep files focused):

File: `custom_components/auto_off/tests/test_sensor_group_smoke.py`

```python
"""Smoke test that SensorGroup construction and state logging don't
reference attributes removed from Target."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.auto_off.auto_off import GroupConfig, SensorGroup


@pytest.fixture
def sg_hass():
    hass = MagicMock()
    hass.states.get = MagicMock(return_value=None)
    hass.loop = MagicMock()
    hass.loop.time = MagicMock(return_value=1000.0)
    return hass


async def test_sensor_group_state_logging_no_attribute_error(sg_hass):
    cfg = GroupConfig(
        targets=["light.kitchen"],
        sensors=["binary_sensor.motion"],
    )
    sg = SensorGroup(sg_hass, "g1", cfg, on_deadline_change=None)
    # Directly call the transition logger; should not raise AttributeError.
    await sg._log_state_transitions(
        {"target_on": False, "all_sensors_off": True, "human_deadline": "None"}
    )
```

- [ ] **Step 2: Run — expect FAIL with AttributeError**

Run: `AUTOQA_MODE=unit pytest custom_components/auto_off/tests/test_sensor_group_smoke.py -v`
Expected: FAIL (references `_current_entity_ids` on new Target).

- [ ] **Step 3: Simplify `_log_state_transitions`**

In `custom_components/auto_off/auto_off.py`, replace the body of `_log_state_transitions` (lines 553-587 of the pre-edit file; locate the method) with:

```python
async def _log_state_transitions(self, state: dict):
    """Log current sensor and target states."""
    sensor_statuses = []
    for s in self._sensors:
        try:
            status = await s.is_on()
        except Exception as e:
            status = f"error: {e}"
        sensor_statuses.append(f"{getattr(s, 'raw', str(s))}: {status}")

    target_statuses = []
    for t in self._targets:
        try:
            status = await t.is_on()
        except Exception as e:
            status = f"error: {e}"
        target_statuses.append(f"{t.entity_id}: {status}")

    _LOGGER.debug(
        f"[Group {self.group_id}] Sensors: {sensor_statuses} | Targets: {target_statuses}"
    )
    _LOGGER.debug(
        f"[Group {self.group_id}] State transition: "
        f"last_all_sensors_off={self._last_all_sensors_off} -> all_sensors_off={state['all_sensors_off']}, "
        f"last_any_target_on={self._last_any_target_on} -> any_target_on={state['target_on']}"
    )
```

- [ ] **Step 4: Run — expect PASS**

Run: `AUTOQA_MODE=unit pytest custom_components/auto_off/tests/test_sensor_group_smoke.py -v`
Expected: PASS.

- [ ] **Step 5: Run broader suite to make sure nothing else broke**

Run: `AUTOQA_MODE=unit pytest custom_components/auto_off/tests/ -v --ignore=custom_components/auto_off/tests/test_integration_e2e.py --ignore=custom_components/auto_off/tests/test_e2e_playwright.py`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add custom_components/auto_off/auto_off.py custom_components/auto_off/tests/test_sensor_group_smoke.py
git commit -m "refactor(auto_off): drop removed Target internals from state transition log"
```

---

### Task 6: Add `IntegrationManager.get_group_config`

**Files:**
- Modify: `custom_components/auto_off/integration_manager.py:33-61`
- Test: `custom_components/auto_off/tests/test_integration_manager.py`

- [ ] **Step 1: Write the failing test**

Append to `custom_components/auto_off/tests/test_integration_manager.py` (create file if absent — check first):

```bash
ls custom_components/auto_off/tests/test_integration_manager.py
```

If the file exists, append; otherwise create with imports. Test body:

```python
from custom_components.auto_off.auto_off import GroupConfig
from custom_components.auto_off.integration_manager import IntegrationManager


class TestGetGroupConfig:
    def test_returns_active_group_config(self, hass, config_entry):
        config_entry.data = {
            "poll_interval": 15,
            "groups": {
                "kitchen": {
                    "targets": ["light.kitchen"],
                    "sensors": ["binary_sensor.motion"],
                    "delay": 5,
                }
            },
        }
        mgr = IntegrationManager(hass, config_entry)
        # Inject a SensorGroup-like stub so get_group_config can read it.
        from unittest.mock import MagicMock

        stub_group = MagicMock()
        stub_group._config = GroupConfig(
            targets=["light.kitchen"],
            sensors=["binary_sensor.motion"],
            delay=5,
        )
        mgr.auto_off._groups["kitchen"] = stub_group
        assert mgr.get_group_config("kitchen").targets == ["light.kitchen"]

    def test_returns_none_for_unknown_group(self, hass, config_entry):
        mgr = IntegrationManager(hass, config_entry)
        assert mgr.get_group_config("missing") is None
```

- [ ] **Step 2: Run — expect FAIL**

Run: `AUTOQA_MODE=unit pytest custom_components/auto_off/tests/test_integration_manager.py::TestGetGroupConfig -v`
Expected: FAIL — `get_group_config` does not exist.

- [ ] **Step 3: Implement**

Add to `IntegrationManager` in `custom_components/auto_off/integration_manager.py`:

```python
def get_group_config(self, group_name: str) -> GroupConfig | None:
    """Return the active GroupConfig for a group, or None during teardown."""
    group = self.auto_off._groups.get(group_name)
    if group is None:
        return None
    return group._config
```

- [ ] **Step 4: Run — expect PASS**

Run: `AUTOQA_MODE=unit pytest custom_components/auto_off/tests/test_integration_manager.py::TestGetGroupConfig -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add custom_components/auto_off/integration_manager.py custom_components/auto_off/tests/test_integration_manager.py
git commit -m "feat(auto_off): expose IntegrationManager.get_group_config"
```

---

### Task 7: Pass manager reference into `DeadlineSensorEntity`

**Files:**
- Modify: `custom_components/auto_off/sensor.py:28-58`
- Modify: `custom_components/auto_off/integration_manager.py:66-80, 155-165`

- [ ] **Step 1: Write the failing test**

Create file: `custom_components/auto_off/tests/test_deadline_sensor.py`

```python
"""Tests for DeadlineSensorEntity attribute exposure."""

from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.auto_off.auto_off import GroupConfig
from custom_components.auto_off.sensor import DeadlineSensorEntity


def _make_entity(manager, group_name="kitchen"):
    hass = MagicMock()
    entry = MagicMock()
    return DeadlineSensorEntity(hass, entry, group_name, manager)


def test_entity_holds_manager_reference():
    manager = MagicMock()
    entity = _make_entity(manager)
    assert entity._manager is manager


def test_attributes_include_group_fields_when_config_available():
    manager = MagicMock()
    manager.get_group_config.return_value = GroupConfig(
        targets=["light.a", "switch.b"],
        sensors=["binary_sensor.motion"],
        sensor_templates=["{{ 1 }}"],
        delay=5,
    )
    entity = _make_entity(manager)
    attrs = entity.extra_state_attributes
    assert attrs["targets"] == ["light.a", "switch.b"]
    assert attrs["sensors"] == ["binary_sensor.motion"]
    assert attrs["sensor_templates"] == ["{{ 1 }}"]
    assert "deadline_iso" in attrs


def test_attributes_fall_back_when_group_missing():
    manager = MagicMock()
    manager.get_group_config.return_value = None
    entity = _make_entity(manager)
    attrs = entity.extra_state_attributes
    assert attrs == {"deadline_iso": None}
```

- [ ] **Step 2: Run — expect FAIL**

Run: `AUTOQA_MODE=unit pytest custom_components/auto_off/tests/test_deadline_sensor.py -v`
Expected: FAIL — constructor takes 3 args, not 4; no `_manager` attribute.

- [ ] **Step 3: Update the constructor signature**

Replace `DeadlineSensorEntity.__init__` in `custom_components/auto_off/sensor.py` with:

```python
def __init__(
    self,
    hass: HomeAssistant,
    entry: ConfigEntry,
    group_name: str,
    manager: "IntegrationManager",
) -> None:
    """Initialize the deadline sensor entity."""
    self.hass = hass
    self._entry = entry
    self._group_name = group_name
    self._manager = manager
    self._attr_unique_id = f"{DOMAIN}_{group_name}_deadline"
    self._attr_native_value = "—"
    self._deadline_iso: str | None = None
```

Add at top of file (under a `TYPE_CHECKING` guard to avoid import cycle):

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .integration_manager import IntegrationManager
```

- [ ] **Step 4: Update `extra_state_attributes`**

Replace the `extra_state_attributes` property in `sensor.py`:

```python
@property
def extra_state_attributes(self) -> dict[str, Any]:
    """Return extra state attributes."""
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

- [ ] **Step 5: Update every `DeadlineSensorEntity(...)` construction site**

Three call sites in `custom_components/auto_off/integration_manager.py`:

1. Line ~74 in `sensor_platform_ready`:
```python
deadline_entity = DeadlineSensorEntity(self.hass, self.entry, group_name, self)
```

2. Line ~159 in `set_group`:
```python
deadline_entity = DeadlineSensorEntity(self.hass, self.entry, group_name, self)
```

Search-and-fix:
```bash
grep -n "DeadlineSensorEntity(" custom_components/auto_off/
```

Update every call site to include `self` (the manager).

- [ ] **Step 6: Run the new test + existing tests**

Run: `AUTOQA_MODE=unit pytest custom_components/auto_off/tests/test_deadline_sensor.py custom_components/auto_off/tests/test_integration_manager.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add custom_components/auto_off/sensor.py custom_components/auto_off/integration_manager.py custom_components/auto_off/tests/test_deadline_sensor.py
git commit -m "feat(auto_off): expose targets/sensors/sensor_templates as deadline sensor attributes"
```

---

### Task 8: Force `async_write_ha_state` after `update_group_config`

**Files:**
- Modify: `custom_components/auto_off/integration_manager.py:137-190`
- Test: `custom_components/auto_off/tests/test_integration_manager.py`

- [ ] **Step 1: Write the failing test**

Append to `test_integration_manager.py`:

```python
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestUpdateGroupConfigWritesState:
    async def test_async_write_ha_state_called_after_update(self, hass, config_entry):
        config_entry.data = {
            "poll_interval": 15,
            "groups": {
                "kitchen": {
                    "targets": ["light.kitchen"],
                    "sensors": ["binary_sensor.motion"],
                    "delay": 5,
                }
            },
        }
        mgr = IntegrationManager(hass, config_entry)

        mock_entity = MagicMock()
        mock_entity.async_write_ha_state = MagicMock()
        mgr._deadline_entities["kitchen"] = mock_entity

        # Stub out auto_off.async_init_groups so set_group doesn't actually
        # try to build SensorGroup with a real hass.
        mgr.auto_off.async_init_groups = AsyncMock()

        await mgr.update_group_config(
            "kitchen",
            {
                "targets": ["light.kitchen", "light.extra"],
                "sensors": ["binary_sensor.motion"],
                "delay": 5,
            },
        )

        mock_entity.async_write_ha_state.assert_called()
```

- [ ] **Step 2: Run — expect FAIL**

Run: `AUTOQA_MODE=unit pytest custom_components/auto_off/tests/test_integration_manager.py::TestUpdateGroupConfigWritesState -v`
Expected: FAIL — no such call happens today.

- [ ] **Step 3: Implement**

Modify `IntegrationManager.update_group_config` in `integration_manager.py` to force a state write at the end:

```python
async def update_group_config(self, group_name: str, config_dict: dict) -> None:
    """Update group config from text entity edit or set_group service."""
    await self.set_group(group_name, config_dict, is_new=False)

    # Also update config entry
    current_groups = dict(self.entry.data.get(CONF_GROUPS, {}))
    current_groups[group_name] = config_dict
    new_data = dict(self.entry.data)
    new_data[CONF_GROUPS] = current_groups
    self.hass.config_entries.async_update_entry(self.entry, data=new_data)

    # Refresh UI attributes on the deadline sensor.
    deadline_entity = self._deadline_entities.get(group_name)
    if deadline_entity is not None:
        deadline_entity.async_write_ha_state()
```

Also call `async_write_ha_state` at the end of `set_group` for the `is_new=False` branch (to cover the direct-service path that does not go through `update_group_config`). Add at the end of `set_group` after the text entity update:

```python
        # Refresh attributes on the deadline sensor so the UI reflects the
        # new group config immediately.
        deadline_entity = self._deadline_entities.get(group_name)
        if deadline_entity is not None and not is_new:
            deadline_entity.async_write_ha_state()
```

(The `is_new=True` path already calls `_update_deadline_sensor_for_group` which internally writes state.)

- [ ] **Step 4: Run — expect PASS**

Run: `AUTOQA_MODE=unit pytest custom_components/auto_off/tests/test_integration_manager.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add custom_components/auto_off/integration_manager.py custom_components/auto_off/tests/test_integration_manager.py
git commit -m "feat(auto_off): refresh deadline sensor attributes after group config update"
```

---

### Task 9: Add late-binding e2e scenario

**Files:**
- Modify: `custom_components/auto_off/tests/test_integration_e2e.py`
- Modify: `custom_components/auto_off/tests/ha_packages/auto_off_test.yaml`

- [ ] **Step 1: Inspect the existing e2e harness**

Open `custom_components/auto_off/tests/test_integration_e2e.py` and `ha_packages/auto_off_test.yaml`. Find fixtures for `light.test_light*` and `binary_sensor.test_motion*` — these are realised via `input_boolean` + template bindings in the yaml package. Decide naming: reuse `input_boolean.late_target_state` + a template `light.late_target` if the yaml already has a pattern; otherwise pick a fresh pair.

- [ ] **Step 2: Add the yaml fixtures (if not present)**

Modify `custom_components/auto_off/tests/ha_packages/auto_off_test.yaml`. Add (mirroring the pattern used for `test_light_2`):

```yaml
input_boolean:
  late_target_state:
    name: Late target state
    initial: off

template:
  - binary_sensor:
      - name: "late_target_state_bs"
        state: "{{ is_state('input_boolean.late_target_state', 'on') }}"
```

Or, if the existing pattern already uses a light template, add `light.late_target` analogously. (Adapt to whatever pattern exists in the current file.)

**Note for implementer:** if the existing YAML already exposes a `light.<name>` backed by `input_boolean.<name>_state`, reuse that shape. Otherwise use a `switch` that the test can call `turn_off` on.

- [ ] **Step 3: Add the e2e test**

Append to `custom_components/auto_off/tests/test_integration_e2e.py` inside `TestAutoOffIntegrationE2E`:

```python
async def test_late_binding_target(self, ha_instance):
    """Target entity appears after set_group → it's picked up on first turn_off.

    Scenario from design spec: at set_group time the target entity is not
    yet in the state machine; later it appears in state "on"; then the
    sensor cycles on→off and the timer fires; turn_off must be called on
    the now-present target entity.
    """
    # Ensure integration present.
    entries = await ha_instance.get_config_entries("auto_off")
    if not entries:
        await ha_instance.add_integration("auto_off", {"poll_interval": 1})
        await asyncio.sleep(2)

    # 1) set_group with a target that currently does not exist.
    await ha_instance.call_service(
        "auto_off",
        "set_group",
        {
            "group_name": "late_binding_group",
            "targets": ["switch.late_target"],
            "sensors": ["binary_sensor.late_target_state_bs"],
            "delay": 0,
        },
    )
    await asyncio.sleep(2)

    # 2) Register the target entity "on" via its backing input_boolean.
    #    (Assumes ha_packages yaml ships `input_boolean.late_target_state`
    #     and a switch template bound to it.)
    await ha_instance.call_service(
        "input_boolean", "turn_on", {"entity_id": "input_boolean.late_target_state"}
    )
    await asyncio.sleep(2)

    # 3) Sensor (same template binary_sensor as above) flips on → off by
    #    toggling input_boolean off. Because target is_on becomes True
    #    after a moment, the deadline should fire on the next state cycle.
    await ha_instance.call_service(
        "input_boolean", "turn_off", {"entity_id": "input_boolean.late_target_state"}
    )
    await asyncio.sleep(3)

    # 4) Assert: the backing input_boolean ended up off — meaning
    #    turn_off was routed to the now-present target.
    state = await ha_instance.get_state("input_boolean.late_target_state")
    assert state is not None
    assert state["state"] == "off"
```

**Note for implementer:** the precise fixture wiring (switch vs light, yaml layout) depends on what the current `ha_packages/auto_off_test.yaml` uses. If that yaml is not amenable to a single-input-boolean toggle being both sensor and target, split into two input_booleans: one for sensor, one as backing for the target. Keep the test assertion on the target-backing entity state after the timer fires.

- [ ] **Step 4: Run the e2e scenario**

Run: `./ha-test-kit/run_e2e.sh`
Expected: the new test passes along with existing ones.

- [ ] **Step 5: Commit**

```bash
git add custom_components/auto_off/tests/test_integration_e2e.py custom_components/auto_off/tests/ha_packages/auto_off_test.yaml
git commit -m "test(auto_off): e2e late-binding target scenario"
```

---

### Task 10: Update existing e2e scenarios if they contain Jinja targets

**Files:**
- Modify (possibly): `custom_components/auto_off/tests/ha_packages/auto_off_test.yaml`
- Modify (possibly): `custom_components/auto_off/tests/test_integration_e2e.py`

- [ ] **Step 1: Search for template targets**

Run:
```bash
grep -n "targets" custom_components/auto_off/tests/ha_packages/auto_off_test.yaml
grep -rn "'targets'" custom_components/auto_off/tests/
```

- [ ] **Step 2: Replace any Jinja string inside a `targets:` list with a plain entity id**

If scenarios like `targets: ["{{ ... }}"]` exist, rewrite them to the flat entity-id form the new contract expects. If none exist, skip to the commit.

- [ ] **Step 3: Run e2e to confirm no regressions**

Run: `./ha-test-kit/run_e2e.sh`
Expected: all scenarios pass.

- [ ] **Step 4: Commit (or skip if no edits needed)**

```bash
git add custom_components/auto_off/tests/
git commit -m "test(auto_off): flatten template targets in legacy e2e fixtures" || echo "nothing to commit"
```

---

### Task 11: Update README

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update the `auto_off` section**

Open `README.md` and locate the section listing `auto_off` group configuration fields. Add / adjust:

- Under `targets`: note "must be concrete entity ids (`domain.object_id`). Jinja templates are no longer supported; invalid items produce a warning in the log and are skipped at turn_off."
- Add a subsection titled **Device page attributes**: "The `sensor.auto_off_<group>_deadline` entity exposes `targets`, `sensors`, and `sensor_templates` (raw Jinja text) as extra state attributes. Open the more-info dialog from the device page to see the list."
- If a migration note from a previous plan mentions "template targets", append a short clarification that the migration path for such configs is to edit them via `auto_off.set_group` after the upgrade.

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: describe new auto_off device-page attributes and targets rule"
```

---

### Task 12: Final verification run

- [ ] **Step 1: Run unit suite**

Run: `./ha-test-kit/run_unit.sh`
Expected: all green.

- [ ] **Step 2: Run e2e suite**

Run: `./ha-test-kit/run_e2e.sh`
Expected: all green.

- [ ] **Step 3: Smoke-test in a real HA instance (optional, manual)**

In a running HA install with this integration loaded:
1. Call `auto_off.set_group` with a mix of valid and invalid target strings.
2. Open `/config/devices/device/<entry_id>`, click the deadline sensor, inspect attributes — targets / sensors / sensor_templates should appear.
3. Confirm warning log entry for the invalid target.

- [ ] **Step 4: If everything is green, no commit needed (verification only).**

---

## Self-review checklist (run after drafting)

- [x] Every spec requirement maps to a task:
  - `GroupConfig.targets` validator → Tasks 1, 2
  - `Target` simplification → Tasks 3, 4, 5
  - `IntegrationManager.get_group_config` → Task 6
  - `DeadlineSensorEntity` new attributes → Task 7
  - `async_write_ha_state` after config change → Task 8
  - Late-binding e2e → Task 9
  - Legacy e2e fixtures cleanup → Task 10
  - Documentation → Task 11
- [x] No "TBD" / "implement later" placeholders.
- [x] Method signatures are consistent across tasks (`get_group_config`, `_manager`, `_skip`, `entity_id`).
- [x] Every step that changes code shows the code.
- [x] Commit messages are concrete and described per-task.
