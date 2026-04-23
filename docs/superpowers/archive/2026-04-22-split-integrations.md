# Split auto_off + door_occupancy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the current `auto_off` custom integration into two independent
integrations (`auto_off` and `door_occupancy`) shipped from the same
repository, replace the YAML-string `set_group` service with structured
fields, rebuild door occupancy around a small `AutoResetBinarySensor` helper,
and remove the now-unneeded `GroupConfigSensorEntity`.

**Architecture:** `custom_components/auto_off/` keeps only the group timer
logic (domain `auto_off`, platforms `sensor` + `text`). `custom_components/door_occupancy/`
is a new package (domain `door_occupancy`, platform `binary_sensor`) with its
own config flow, its own periodic discovery tick, and a reusable
`AutoResetBinarySensor` base class built on `homeassistant.helpers.event.async_call_later`.
A single HACS repository ships both. Breaking change: `async_migrate_entry`
for `auto_off` fails on old entries and points users to the README migration
section.

**Tech Stack:** Python, Home Assistant custom component API (2025.12+),
pydantic v2, pytest, pytest-asyncio, Docker-based e2e harness (`ha-test-kit`).

**Design spec:** `docs/superpowers/specs/2026-04-22-split-integrations-design.md`.

---

## Task ordering rationale

Tasks are ordered so the working tree compiles and tests pass after each
commit. Door occupancy is built as a new package first (no interference with
the existing integration), then old door_occupancy code is removed from
`auto_off`, then the service schema is migrated, then `GroupConfigSensorEntity`
is removed, then documentation and packaging.

Each task ends with a commit and running the relevant test suite.

---

### Task 1: Create skeleton for `door_occupancy` package

**Files:**
- Create: `custom_components/door_occupancy/__init__.py`
- Create: `custom_components/door_occupancy/const.py`
- Create: `custom_components/door_occupancy/manifest.json`
- Create: `custom_components/door_occupancy/strings.json`
- Create: `custom_components/door_occupancy/translations/en.json`
- Create: `custom_components/door_occupancy/icon.svg`
- Create: `custom_components/door_occupancy/tests/__init__.py`

- [ ] **Step 1: Create `const.py`**

File: `custom_components/door_occupancy/const.py`

```python
"""Constants for the Door Occupancy integration."""

DOMAIN = "door_occupancy"
PLATFORMS = ["binary_sensor"]

CONF_POLL_INTERVAL = "poll_interval"
CONF_OCCUPANCY_TIMEOUT = "occupancy_timeout"

DEFAULT_POLL_INTERVAL = 30
DEFAULT_OCCUPANCY_TIMEOUT = 15

CONFIG_VERSION = 1
```

- [ ] **Step 2: Create `manifest.json`**

File: `custom_components/door_occupancy/manifest.json`

```json
{
  "domain": "door_occupancy",
  "name": "Door Occupancy",
  "version": "0.0.1",
  "documentation": "https://github.com/AlexMKX/auto_off",
  "requirements": [],
  "dependencies": [],
  "codeowners": [
    "@AlexMKX"
  ],
  "iot_class": "local_push",
  "config_flow": true
}
```

- [ ] **Step 3: Create `strings.json`**

File: `custom_components/door_occupancy/strings.json`

```json
{
  "config": {
    "step": {
      "user": {
        "title": "Door Occupancy Setup",
        "description": "Configure discovery polling and occupancy pulse duration.",
        "data": {
          "poll_interval": "Discovery interval (seconds)",
          "occupancy_timeout": "Occupancy pulse duration (seconds)"
        }
      }
    },
    "abort": {
      "already_configured": "Door Occupancy is already configured"
    }
  },
  "options": {
    "step": {
      "init": {
        "title": "Door Occupancy Options",
        "data": {
          "poll_interval": "Discovery interval (seconds)",
          "occupancy_timeout": "Occupancy pulse duration (seconds)"
        }
      }
    }
  }
}
```

- [ ] **Step 4: Create `translations/en.json` (copy of `strings.json`)**

File: `custom_components/door_occupancy/translations/en.json`

Use exactly the same content as `strings.json` in step 3.

- [ ] **Step 5: Create `icon.svg`**

Copy `custom_components/auto_off/icon.svg` to `custom_components/door_occupancy/icon.svg` verbatim (same visual brand; README will distinguish the two integrations).

```bash
cp custom_components/auto_off/icon.svg custom_components/door_occupancy/icon.svg
```

- [ ] **Step 6: Create empty package files**

File: `custom_components/door_occupancy/__init__.py`

```python
"""Door Occupancy integration for Home Assistant.

This is a stub populated in later tasks (config flow, discovery, sensors).
"""
```

File: `custom_components/door_occupancy/tests/__init__.py`

(empty file)

- [ ] **Step 7: Verify package is discoverable**

Run: `python -c "import custom_components.door_occupancy"` (from repo root with venv activated)

Expected: no output, exit code 0.

- [ ] **Step 8: Commit**

```bash
git add custom_components/door_occupancy/
git commit -m "feat(door_occupancy): scaffold new integration package

Adds manifest, const, strings/translations, icon, and empty __init__.py
for the new door_occupancy custom integration that will take over
occupancy-sensor responsibilities from auto_off."
```

---

### Task 2: Implement `AutoResetBinarySensor` helper (TDD)

**Files:**
- Create: `custom_components/door_occupancy/auto_reset.py`
- Create: `custom_components/door_occupancy/tests/conftest.py`
- Create: `custom_components/door_occupancy/tests/test_auto_reset.py`

Behavior under test: `pulse()` turns the sensor on and schedules a reset to
off after `reset_timeout` seconds; a new `pulse()` cancels and reschedules
the reset; `async_will_remove_from_hass()` cancels the pending reset.

- [ ] **Step 1: Create test conftest**

File: `custom_components/door_occupancy/tests/conftest.py`

```python
"""Pytest fixtures for door_occupancy tests."""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def hass():
    """Minimal HA mock sufficient for unit-level tests."""
    hass = MagicMock(spec=HomeAssistant)
    hass.data = {}
    hass.states = MagicMock()
    hass.services = MagicMock()
    hass.config_entries = MagicMock()
    hass.async_create_task = MagicMock()
    return hass
```

- [ ] **Step 2: Write failing test for pulse turning on and auto-resetting**

File: `custom_components/door_occupancy/tests/test_auto_reset.py`

```python
"""Unit tests for AutoResetBinarySensor.

Covers the contract: pulse() turns on, a scheduled reset turns off,
a new pulse() restarts the reset timer, and removal cancels the timer.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from custom_components.door_occupancy.auto_reset import AutoResetBinarySensor


class _Probe(AutoResetBinarySensor):
    """Concrete subclass used by the tests."""

    def __init__(self, hass, reset_timeout: float) -> None:
        super().__init__(hass, reset_timeout)
        self.state_writes = 0

    def async_write_ha_state(self) -> None:
        # Replace HA write with a counter so we can assert it was called.
        self.state_writes += 1


class TestAutoResetBinarySensor:
    """Behavior contract for AutoResetBinarySensor."""

    def test_pulse_turns_on_and_schedules_reset(self, hass):
        """pulse() must flip is_on to True and schedule a reset."""
        cancel = MagicMock()
        with patch(
            "custom_components.door_occupancy.auto_reset.async_call_later",
            return_value=cancel,
        ) as mock_call_later:
            sensor = _Probe(hass, reset_timeout=15)

            sensor.pulse()

            assert sensor.is_on is True
            mock_call_later.assert_called_once()
            # First positional arg is hass, second is the delay in seconds.
            assert mock_call_later.call_args[0][0] is hass
            assert mock_call_later.call_args[0][1] == 15
            assert sensor.state_writes == 1

    def test_second_pulse_cancels_and_reschedules(self, hass):
        """A second pulse() must cancel the prior reset and schedule a new one."""
        cancel_first = MagicMock()
        cancel_second = MagicMock()
        with patch(
            "custom_components.door_occupancy.auto_reset.async_call_later",
            side_effect=[cancel_first, cancel_second],
        ):
            sensor = _Probe(hass, reset_timeout=15)

            sensor.pulse()
            sensor.pulse()

            cancel_first.assert_called_once()
            cancel_second.assert_not_called()
            assert sensor.is_on is True

    def test_reset_callback_turns_off(self, hass):
        """The callback passed to async_call_later must turn is_on back to False."""
        cancel = MagicMock()
        captured = {}
        def fake_call_later(_hass, _delay, cb):
            captured["cb"] = cb
            return cancel
        with patch(
            "custom_components.door_occupancy.auto_reset.async_call_later",
            side_effect=fake_call_later,
        ):
            sensor = _Probe(hass, reset_timeout=15)
            sensor.pulse()

            assert "cb" in captured
            captured["cb"](None)  # simulate the scheduled reset firing

            assert sensor.is_on is False
            # One write for pulse(), one for the reset.
            assert sensor.state_writes == 2

    @pytest.mark.asyncio
    async def test_remove_cancels_pending_reset(self, hass):
        """async_will_remove_from_hass must cancel the pending reset callback."""
        cancel = MagicMock()
        with patch(
            "custom_components.door_occupancy.auto_reset.async_call_later",
            return_value=cancel,
        ):
            sensor = _Probe(hass, reset_timeout=15)
            sensor.pulse()

            await sensor.async_will_remove_from_hass()

            cancel.assert_called_once()
```

- [ ] **Step 3: Run tests and verify they fail**

Run: `pytest custom_components/door_occupancy/tests/test_auto_reset.py -v`

Expected: all tests fail with `ModuleNotFoundError: custom_components.door_occupancy.auto_reset`.

- [ ] **Step 4: Implement `AutoResetBinarySensor`**

File: `custom_components/door_occupancy/auto_reset.py`

```python
"""Auto-resetting binary sensor helper.

Subclasses call pulse() to turn the sensor on; it automatically resets
to off after a configured timeout. A new pulse() call cancels and
reschedules the reset, which gives a "sliding window" behavior.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_call_later


class AutoResetBinarySensor(BinarySensorEntity):
    """Binary sensor that pulses on and auto-resets off.

    This class owns only the on/off state and the reset timer. It is the
    subclass' responsibility to decide when to call pulse().
    """

    def __init__(self, hass: HomeAssistant, reset_timeout: float) -> None:
        self.hass = hass
        self._reset_timeout = float(reset_timeout)
        self._attr_is_on = False
        self._cancel_reset: Callable[[], None] | None = None

    @callback
    def pulse(self) -> None:
        """Turn on and (re)schedule the reset-to-off callback."""
        self._attr_is_on = True
        if self._cancel_reset is not None:
            self._cancel_reset()
        self._cancel_reset = async_call_later(
            self.hass, self._reset_timeout, self._on_reset
        )
        self.async_write_ha_state()

    @callback
    def _on_reset(self, _now: datetime | None) -> None:
        """Callback fired by async_call_later when the timeout elapses."""
        self._cancel_reset = None
        self._attr_is_on = False
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        """Cancel the pending reset callback, if any, on entity removal."""
        if self._cancel_reset is not None:
            self._cancel_reset()
            self._cancel_reset = None
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest custom_components/door_occupancy/tests/test_auto_reset.py -v`

Expected: all four tests pass.

- [ ] **Step 6: Commit**

```bash
git add custom_components/door_occupancy/auto_reset.py \
        custom_components/door_occupancy/tests/conftest.py \
        custom_components/door_occupancy/tests/test_auto_reset.py
git commit -m "feat(door_occupancy): add AutoResetBinarySensor helper

Reusable base class for binary sensors that pulse on and auto-reset
off after a timeout. Built on homeassistant.helpers.event.async_call_later
instead of raw loop.call_later. Tests cover the pulse/cancel/reschedule
contract and the remove-from-hass cleanup path."
```

---

### Task 3: Implement `door_occupancy` config flow (TDD)

**Files:**
- Create: `custom_components/door_occupancy/config_flow.py`
- Create: `custom_components/door_occupancy/tests/test_config_flow.py`

- [ ] **Step 1: Write failing tests for the config flow**

File: `custom_components/door_occupancy/tests/test_config_flow.py`

```python
"""Tests for the door_occupancy config flow."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.door_occupancy.config_flow import (
    DoorOccupancyConfigFlow,
    DoorOccupancyOptionsFlow,
)
from custom_components.door_occupancy.const import (
    CONF_OCCUPANCY_TIMEOUT,
    CONF_POLL_INTERVAL,
    DEFAULT_OCCUPANCY_TIMEOUT,
    DEFAULT_POLL_INTERVAL,
)


class TestDoorOccupancyConfigFlow:
    """Initial setup config flow."""

    @pytest.fixture
    def flow(self):
        flow = DoorOccupancyConfigFlow()
        flow.hass = MagicMock()
        flow.hass.config_entries = MagicMock()
        return flow

    @pytest.mark.asyncio
    async def test_step_user_shows_form_when_no_input(self, flow):
        with patch.object(flow, "async_set_unique_id", new_callable=AsyncMock):
            with patch.object(flow, "_abort_if_unique_id_configured"):
                result = await flow.async_step_user(user_input=None)

        assert result["type"] == "form"
        assert result["step_id"] == "user"
        assert CONF_POLL_INTERVAL in result["data_schema"].schema
        assert CONF_OCCUPANCY_TIMEOUT in result["data_schema"].schema

    @pytest.mark.asyncio
    async def test_step_user_creates_entry_with_defaults(self, flow):
        with patch.object(flow, "async_set_unique_id", new_callable=AsyncMock):
            with patch.object(flow, "_abort_if_unique_id_configured"):
                with patch.object(flow, "async_create_entry") as mock_create:
                    mock_create.return_value = {"type": "create_entry"}
                    await flow.async_step_user(user_input={})

        call_kwargs = mock_create.call_args[1]
        assert call_kwargs["title"] == "Door Occupancy"
        assert call_kwargs["data"][CONF_POLL_INTERVAL] == DEFAULT_POLL_INTERVAL
        assert call_kwargs["data"][CONF_OCCUPANCY_TIMEOUT] == DEFAULT_OCCUPANCY_TIMEOUT

    @pytest.mark.asyncio
    async def test_step_user_creates_entry_with_user_values(self, flow):
        with patch.object(flow, "async_set_unique_id", new_callable=AsyncMock):
            with patch.object(flow, "_abort_if_unique_id_configured"):
                with patch.object(flow, "async_create_entry") as mock_create:
                    mock_create.return_value = {"type": "create_entry"}
                    await flow.async_step_user(
                        user_input={
                            CONF_POLL_INTERVAL: 60,
                            CONF_OCCUPANCY_TIMEOUT: 30,
                        }
                    )

        call_kwargs = mock_create.call_args[1]
        assert call_kwargs["data"][CONF_POLL_INTERVAL] == 60
        assert call_kwargs["data"][CONF_OCCUPANCY_TIMEOUT] == 30


class TestDoorOccupancyOptionsFlow:
    """Options flow for reconfiguration."""

    @pytest.mark.skip(reason="Requires full HA test harness with frame helper")
    @pytest.mark.asyncio
    async def test_init_step_updates_entry(self):
        """Covered by e2e tests; mirrors AutoOffOptionsFlow pattern."""
        pass
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest custom_components/door_occupancy/tests/test_config_flow.py -v`

Expected: all tests fail with `ModuleNotFoundError` for the config_flow module.

- [ ] **Step 3: Implement the config flow**

File: `custom_components/door_occupancy/config_flow.py`

```python
"""Config flow for the Door Occupancy integration."""
from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback

from .const import (
    CONF_OCCUPANCY_TIMEOUT,
    CONF_POLL_INTERVAL,
    DEFAULT_OCCUPANCY_TIMEOUT,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
)


def _user_schema(
    current_poll: int = DEFAULT_POLL_INTERVAL,
    current_timeout: int = DEFAULT_OCCUPANCY_TIMEOUT,
) -> vol.Schema:
    return vol.Schema(
        {
            vol.Optional(CONF_POLL_INTERVAL, default=current_poll): vol.All(
                vol.Coerce(int), vol.Range(min=5, max=300)
            ),
            vol.Optional(CONF_OCCUPANCY_TIMEOUT, default=current_timeout): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=600)
            ),
        }
    )


class DoorOccupancyConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Door Occupancy."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Show the initial setup form and create the entry on submit."""
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        if user_input is not None:
            return self.async_create_entry(
                title="Door Occupancy",
                data={
                    CONF_POLL_INTERVAL: user_input.get(
                        CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL
                    ),
                    CONF_OCCUPANCY_TIMEOUT: user_input.get(
                        CONF_OCCUPANCY_TIMEOUT, DEFAULT_OCCUPANCY_TIMEOUT
                    ),
                },
            )

        return self.async_show_form(step_id="user", data_schema=_user_schema())

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return DoorOccupancyOptionsFlow()


class DoorOccupancyOptionsFlow(config_entries.OptionsFlow):
    """Options flow allowing both values to be reconfigured."""

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            new_data = dict(self.config_entry.data)
            new_data[CONF_POLL_INTERVAL] = user_input[CONF_POLL_INTERVAL]
            new_data[CONF_OCCUPANCY_TIMEOUT] = user_input[CONF_OCCUPANCY_TIMEOUT]
            self.hass.config_entries.async_update_entry(self.config_entry, data=new_data)
            return self.async_create_entry(title="", data={})

        return self.async_show_form(
            step_id="init",
            data_schema=_user_schema(
                current_poll=self.config_entry.data.get(
                    CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL
                ),
                current_timeout=self.config_entry.data.get(
                    CONF_OCCUPANCY_TIMEOUT, DEFAULT_OCCUPANCY_TIMEOUT
                ),
            ),
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest custom_components/door_occupancy/tests/test_config_flow.py -v`

Expected: three tests pass, one test is skipped.

- [ ] **Step 5: Commit**

```bash
git add custom_components/door_occupancy/config_flow.py \
        custom_components/door_occupancy/tests/test_config_flow.py
git commit -m "feat(door_occupancy): add config + options flow

Single-instance integration with poll_interval (5..300) and
occupancy_timeout (1..600) fields. OptionsFlow allows reconfiguring
both values post-install."
```

---

### Task 4: Implement `DoorOccupancyBinarySensor` (TDD)

**Files:**
- Create: `custom_components/door_occupancy/binary_sensor.py`
- Create: `custom_components/door_occupancy/tests/test_binary_sensor.py`

The sensor subscribes to the source entity and pulses on **every real state
change** (fixing the `old_state is None` bug where the first change was
dropped). It ignores `unknown`/`unavailable` and consecutive duplicates.

- [ ] **Step 1: Write failing tests**

File: `custom_components/door_occupancy/tests/test_binary_sensor.py`

```python
"""Tests for DoorOccupancyBinarySensor.

Covers: construction, state subscription, pulse on real state changes,
regression for the dropped-first-event bug, and invalid-state filtering.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from custom_components.door_occupancy.binary_sensor import (
    DoorOccupancyBinarySensor,
)


def _make_state(value: str):
    state = MagicMock()
    state.state = value
    return state


class TestDoorOccupancyBinarySensor:
    @pytest.fixture
    def source_state_closed(self):
        return _make_state("off")  # for binary_sensor door: off = closed

    @pytest.fixture
    def config_entry(self):
        entry = MagicMock()
        entry.entry_id = "test_entry"
        return entry

    @pytest.fixture
    def sensor(self, hass, config_entry, source_state_closed):
        hass.states.get.return_value = source_state_closed
        return DoorOccupancyBinarySensor(
            hass=hass,
            source_entity_id="binary_sensor.front_door",
            config_entry=config_entry,
            occupancy_timeout=15,
        )

    def test_init_sets_expected_attributes(self, sensor):
        assert sensor._attr_name == "binary_sensor.front_door Occupancy"
        assert sensor._attr_unique_id == "binary_sensor_front_door_occupancy"
        assert sensor._attr_is_on is False
        assert sensor._attr_should_poll is False

    @pytest.mark.asyncio
    async def test_added_to_hass_subscribes_and_reads_initial_state(
        self, hass, sensor, source_state_closed
    ):
        with patch(
            "custom_components.door_occupancy.binary_sensor.async_track_state_change_event"
        ) as mock_track:
            with patch(
                "custom_components.door_occupancy.binary_sensor.entity_registry"
            ):
                with patch(
                    "custom_components.door_occupancy.binary_sensor.device_registry"
                ):
                    mock_track.return_value = lambda: None
                    await sensor.async_added_to_hass()

        mock_track.assert_called_once()
        args, _ = mock_track.call_args
        assert args[1] == ["binary_sensor.front_door"]
        # Initial state was captured, not erroneously treated as a change.
        assert sensor._prev_state == "off"
        assert sensor._attr_is_on is False

    @pytest.mark.asyncio
    async def test_first_state_change_triggers_pulse(self, hass, sensor):
        """Regression: the first real state change must trigger pulse().

        The old implementation required both old_state and new_state to be
        non-None and silently dropped the initial change.
        """
        with patch(
            "custom_components.door_occupancy.binary_sensor.async_track_state_change_event",
            return_value=lambda: None,
        ):
            with patch(
                "custom_components.door_occupancy.binary_sensor.entity_registry"
            ):
                with patch(
                    "custom_components.door_occupancy.binary_sensor.device_registry"
                ):
                    await sensor.async_added_to_hass()

        event = MagicMock()
        event.data = {
            "entity_id": "binary_sensor.front_door",
            "new_state": _make_state("on"),
        }
        with patch.object(sensor, "pulse") as mock_pulse:
            sensor._handle_source_event(event)

        mock_pulse.assert_called_once()
        assert sensor._prev_state == "on"

    @pytest.mark.asyncio
    async def test_unavailable_state_is_ignored(self, hass, sensor):
        with patch(
            "custom_components.door_occupancy.binary_sensor.async_track_state_change_event",
            return_value=lambda: None,
        ):
            with patch(
                "custom_components.door_occupancy.binary_sensor.entity_registry"
            ):
                with patch(
                    "custom_components.door_occupancy.binary_sensor.device_registry"
                ):
                    await sensor.async_added_to_hass()

        event = MagicMock()
        event.data = {
            "entity_id": "binary_sensor.front_door",
            "new_state": _make_state("unavailable"),
        }
        with patch.object(sensor, "pulse") as mock_pulse:
            sensor._handle_source_event(event)

        mock_pulse.assert_not_called()

    @pytest.mark.asyncio
    async def test_same_state_is_not_a_pulse(self, hass, sensor):
        with patch(
            "custom_components.door_occupancy.binary_sensor.async_track_state_change_event",
            return_value=lambda: None,
        ):
            with patch(
                "custom_components.door_occupancy.binary_sensor.entity_registry"
            ):
                with patch(
                    "custom_components.door_occupancy.binary_sensor.device_registry"
                ):
                    await sensor.async_added_to_hass()

        # Initial state was "off"; "off" again must not pulse.
        event = MagicMock()
        event.data = {
            "entity_id": "binary_sensor.front_door",
            "new_state": _make_state("off"),
        }
        with patch.object(sensor, "pulse") as mock_pulse:
            sensor._handle_source_event(event)

        mock_pulse.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest custom_components/door_occupancy/tests/test_binary_sensor.py -v`

Expected: all tests fail (`ImportError` for `DoorOccupancyBinarySensor`).

- [ ] **Step 3: Implement `DoorOccupancyBinarySensor`**

File: `custom_components/door_occupancy/binary_sensor.py`

```python
"""Binary sensor entity producing occupancy pulses for door-like sources.

Subscribes to state changes of the source entity (a door binary_sensor,
a lock, or a cover) and calls pulse() on every real state change, which
turns the occupancy sensor on briefly. The on-to-off reset is owned by
AutoResetBinarySensor.
"""
from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry, entity_registry
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event

from .auto_reset import AutoResetBinarySensor
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

_IGNORED_STATES = {"unknown", "unavailable"}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Hand the platform's add-entities callback to the discovery manager."""
    manager = hass.data[DOMAIN][entry.entry_id]
    await manager.async_platform_ready(async_add_entities)


class DoorOccupancyBinarySensor(AutoResetBinarySensor):
    """Occupancy sensor derived from a door/lock/cover source entity."""

    _attr_device_class = BinarySensorDeviceClass.OCCUPANCY
    _attr_icon = "mdi:motion-sensor"
    _attr_should_poll = False

    def __init__(
        self,
        hass: HomeAssistant,
        source_entity_id: str,
        config_entry: ConfigEntry,
        occupancy_timeout: float,
    ) -> None:
        super().__init__(hass, reset_timeout=occupancy_timeout)
        self._source_entity_id = source_entity_id
        self._config_entry = config_entry
        self._attr_name = f"{source_entity_id} Occupancy"
        self._attr_unique_id = f"{source_entity_id.replace('.', '_')}_occupancy"
        self._prev_state: str | None = None
        self._unsub = None

    async def async_added_to_hass(self) -> None:
        # Bind this entity's config entry to the source entity's device so
        # the occupancy sensor shows up on the same HA device card.
        ent_reg = entity_registry.async_get(self.hass)
        dev_reg = device_registry.async_get(self.hass)
        entry = ent_reg.async_get(self._source_entity_id)
        if entry and entry.device_id:
            dev_reg.async_update_device(
                entry.device_id, add_config_entry_id=self._config_entry.entry_id
            )

        # Seed _prev_state from the current source state so the first *real*
        # change fires pulse(), while startup is not treated as a change.
        current = self.hass.states.get(self._source_entity_id)
        self._prev_state = current.state if current else None

        self._unsub = async_track_state_change_event(
            self.hass, [self._source_entity_id], self._handle_source_event
        )
        _LOGGER.info(
            "DoorOccupancyBinarySensor '%s' attached to %s",
            self._attr_name,
            self._source_entity_id,
        )

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
        await super().async_will_remove_from_hass()

    @callback
    def _handle_source_event(self, event) -> None:
        new_state = event.data.get("new_state")
        if new_state is None or new_state.state in _IGNORED_STATES:
            return
        if new_state.state == self._prev_state:
            return
        self._prev_state = new_state.state
        self.pulse()

    @property
    def device_info(self):
        ent_reg = entity_registry.async_get(self.hass)
        entry = ent_reg.async_get(self._source_entity_id)
        if not entry or not entry.device_id:
            return None
        dev_reg = device_registry.async_get(self.hass)
        device = dev_reg.async_get(entry.device_id)
        if not device:
            return None
        return DeviceInfo(identifiers=device.identifiers)

    @property
    def extra_state_attributes(self):
        return {"source_entity_id": self._source_entity_id}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest custom_components/door_occupancy/tests/test_binary_sensor.py -v`

Expected: all five tests pass.

- [ ] **Step 5: Commit**

```bash
git add custom_components/door_occupancy/binary_sensor.py \
        custom_components/door_occupancy/tests/test_binary_sensor.py
git commit -m "feat(door_occupancy): implement DoorOccupancyBinarySensor

Subscribes to the source entity, pulses on every real state change,
ignores unknown/unavailable, and deduplicates consecutive equal
states. Fixes a regression where the first real state change was
silently dropped because old_state was None."
```

---

### Task 5: Implement discovery manager (TDD)

**Files:**
- Create: `custom_components/door_occupancy/discovery.py`
- Create: `custom_components/door_occupancy/tests/test_discovery.py`

- [ ] **Step 1: Write failing tests**

File: `custom_components/door_occupancy/tests/test_discovery.py`

```python
"""Tests for DoorOccupancyManager discovery behavior."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.door_occupancy.discovery import DoorOccupancyManager


def _fake_state(entity_id: str, device_class: str | None = None):
    state = MagicMock()
    state.entity_id = entity_id
    state.attributes = {"device_class": device_class} if device_class else {}
    return state


class TestDoorOccupancyManager:
    @pytest.fixture
    def config_entry(self):
        entry = MagicMock()
        entry.entry_id = "test_entry"
        entry.data = {"poll_interval": 30, "occupancy_timeout": 15}
        return entry

    @pytest.fixture
    def manager(self, hass, config_entry):
        return DoorOccupancyManager(hass, config_entry, occupancy_timeout=15)

    @pytest.mark.asyncio
    async def test_finds_door_binary_sensors_locks_and_covers(
        self, manager, hass
    ):
        def fake_all(domains):
            if domains == ["binary_sensor"]:
                return [
                    _fake_state("binary_sensor.front_door", "door"),
                    _fake_state("binary_sensor.motion", "motion"),  # skip
                ]
            if set(domains) >= {"cover", "lock"}:
                return [
                    _fake_state("lock.front"),
                    _fake_state("cover.garage"),
                ]
            return []

        hass.states.async_all.side_effect = fake_all
        found = await manager._find_sources()

        assert set(found) == {
            "binary_sensor.front_door",
            "lock.front",
            "cover.garage",
        }

    @pytest.mark.asyncio
    async def test_discovery_adds_one_sensor_per_source(self, manager, hass):
        def fake_all(domains):
            if domains == ["binary_sensor"]:
                return [_fake_state("binary_sensor.front_door", "door")]
            return [_fake_state("lock.front")]

        hass.states.async_all.side_effect = fake_all
        add_entities = MagicMock()
        await manager.async_platform_ready(add_entities)

        # One discovery tick creates entities once for each new source.
        assert add_entities.call_count == 1
        sensors = add_entities.call_args[0][0]
        assert len(sensors) == 2

    @pytest.mark.asyncio
    async def test_discovery_is_idempotent(self, manager, hass):
        def fake_all(domains):
            if domains == ["binary_sensor"]:
                return [_fake_state("binary_sensor.front_door", "door")]
            return []

        hass.states.async_all.side_effect = fake_all
        add_entities = MagicMock()
        await manager.async_platform_ready(add_entities)

        # Running the periodic tick again must not re-add the same sensor.
        await manager._discover_and_add_sensors()
        assert add_entities.call_count == 1

    @pytest.mark.asyncio
    async def test_unload_cancels_periodic_listener(self, manager):
        fake_unsub = MagicMock()
        manager._remove_listener = fake_unsub

        await manager.async_unload()

        fake_unsub.assert_called_once()
        assert manager._remove_listener is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest custom_components/door_occupancy/tests/test_discovery.py -v`

Expected: fail with `ImportError` for the discovery module.

- [ ] **Step 3: Implement discovery manager**

File: `custom_components/door_occupancy/discovery.py`

```python
"""Periodic discovery of door/lock/cover sources and creation of
corresponding occupancy binary sensors."""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval

from .binary_sensor import DoorOccupancyBinarySensor
from .const import CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL

_LOGGER = logging.getLogger(__name__)


class DoorOccupancyManager:
    """Owns per-entry discovery state for Door Occupancy."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        occupancy_timeout: float,
    ) -> None:
        self.hass = hass
        self.entry = entry
        self._occupancy_timeout = occupancy_timeout
        self._sensors: dict[str, DoorOccupancyBinarySensor] = {}
        self._async_add_entities: AddEntitiesCallback | None = None
        self._remove_listener = None

    async def _find_sources(self) -> list[str]:
        entities: set[str] = set()
        for state in self.hass.states.async_all(["binary_sensor"]):
            if state.attributes.get("device_class") == "door":
                entities.add(state.entity_id)
        for state in self.hass.states.async_all(["cover", "lock"]):
            entities.add(state.entity_id)
        return sorted(entities)

    async def async_platform_ready(
        self, async_add_entities: AddEntitiesCallback
    ) -> None:
        """Called once by the binary_sensor platform setup."""
        self._async_add_entities = async_add_entities
        await self._discover_and_add_sensors()
        poll_interval = self.entry.data.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)
        self._remove_listener = async_track_time_interval(
            self.hass, self._on_tick, timedelta(seconds=poll_interval)
        )

    async def _on_tick(self, _now) -> None:
        await self._discover_and_add_sensors()

    async def _discover_and_add_sensors(self) -> None:
        if self._async_add_entities is None:
            return
        sources = await self._find_sources()
        new_sensors = []
        for source_id in sources:
            if source_id in self._sensors:
                continue
            sensor = DoorOccupancyBinarySensor(
                hass=self.hass,
                source_entity_id=source_id,
                config_entry=self.entry,
                occupancy_timeout=self._occupancy_timeout,
            )
            self._sensors[source_id] = sensor
            new_sensors.append(sensor)
        if new_sensors:
            _LOGGER.info("Adding %d new occupancy sensors", len(new_sensors))
            self._async_add_entities(new_sensors, update_before_add=True)

    async def async_unload(self) -> None:
        if self._remove_listener is not None:
            self._remove_listener()
            self._remove_listener = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest custom_components/door_occupancy/tests/test_discovery.py -v`

Expected: four tests pass.

- [ ] **Step 5: Commit**

```bash
git add custom_components/door_occupancy/discovery.py \
        custom_components/door_occupancy/tests/test_discovery.py
git commit -m "feat(door_occupancy): add DoorOccupancyManager

Periodic discovery of door binary_sensors, locks, and covers,
creating one DoorOccupancyBinarySensor per source. Idempotent
across ticks; owns its own async_track_time_interval listener."
```

---

### Task 6: Wire up `door_occupancy` entry setup and unload

**Files:**
- Modify: `custom_components/door_occupancy/__init__.py`

- [ ] **Step 1: Replace the stub `__init__.py`**

File: `custom_components/door_occupancy/__init__.py`

```python
"""Door Occupancy integration for Home Assistant."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_OCCUPANCY_TIMEOUT,
    DEFAULT_OCCUPANCY_TIMEOUT,
    DOMAIN,
    PLATFORMS,
)
from .discovery import DoorOccupancyManager

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a Door Occupancy config entry."""
    hass.data.setdefault(DOMAIN, {})
    manager = DoorOccupancyManager(
        hass,
        entry,
        occupancy_timeout=entry.data.get(
            CONF_OCCUPANCY_TIMEOUT, DEFAULT_OCCUPANCY_TIMEOUT
        ),
    )
    hass.data[DOMAIN][entry.entry_id] = manager
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a Door Occupancy config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        manager: DoorOccupancyManager | None = hass.data.get(DOMAIN, {}).pop(
            entry.entry_id, None
        )
        if manager is not None:
            await manager.async_unload()
    return unload_ok
```

- [ ] **Step 2: Sanity-check the module imports cleanly**

Run: `python -c "import custom_components.door_occupancy"` (from repo root)

Expected: no output, exit code 0.

- [ ] **Step 3: Run the full door_occupancy test suite**

Run: `pytest custom_components/door_occupancy/ -v`

Expected: tests from Tasks 2–5 all pass (13 tests; one skipped options-flow placeholder).

- [ ] **Step 4: Commit**

```bash
git add custom_components/door_occupancy/__init__.py
git commit -m "feat(door_occupancy): wire up entry setup and unload

async_setup_entry constructs a DoorOccupancyManager per config entry,
forwards the binary_sensor platform, and stores the manager in
hass.data for the platform to pick up. async_unload_entry cancels
the periodic discovery listener."
```

---

### Task 7: Remove door-occupancy code from `auto_off`

At this point `door_occupancy` is a working, tested new package. Now strip
its code from the `auto_off` package so the two are fully independent. This
task intentionally leaves `PLATFORMS` unchanged (still includes `binary_sensor`)
and leaves `IntegrationManager` accepting the `async_add_entities` callback
from binary_sensor — those indirections will be removed in Task 8.

**Files:**
- Delete: `custom_components/auto_off/door_occupancy.py`
- Modify: `custom_components/auto_off/integration_manager.py`
- Modify: `custom_components/auto_off/binary_sensor.py`
- Modify: `custom_components/auto_off/tests/test_integration_manager.py`

- [ ] **Step 1: Delete `custom_components/auto_off/door_occupancy.py`**

```bash
git rm custom_components/auto_off/door_occupancy.py
```

- [ ] **Step 2: Remove DoorOccupancyManager usage from `integration_manager.py`**

File: `custom_components/auto_off/integration_manager.py`

Make these edits:

1. Remove the import line:

```python
from .door_occupancy import DoorOccupancyManager
```

2. In `IntegrationManager.__init__`, delete:

```python
        self.door_occupancy = DoorOccupancyManager(hass, entry)
```

3. In `IntegrationManager.__init__`, delete the unused binary_sensor parameter
   storage but keep the `async_add_entities` parameter for source-compatibility
   with `binary_sensor.py` (removed in Task 8):

Leave the constructor signature `(self, hass, entry, async_add_entities)` but
store only what is still used. Concretely, the field
`self._binary_sensor_async_add_entities = async_add_entities` can stay until
Task 8.

4. In `async_initialize`, delete:

```python
        # Initialize door_occupancy with async_add_entities
        self.door_occupancy._async_add_entities = self._binary_sensor_async_add_entities
        await self.door_occupancy._discover_and_add_sensors()
```

5. In `_periodic_worker`, delete:

```python
            await self.door_occupancy.periodic_discovery()
```

6. In `async_unload`, delete:

```python
        await self.door_occupancy.async_unload()
```

- [ ] **Step 3: Simplify `binary_sensor.py` to a no-op platform entry point**

The auto_off integration no longer owns any binary_sensor entities, but it
still declares the platform in `PLATFORMS` until Task 8. Replace the file
with a minimal forward that creates the manager (same side-effect as before,
just without the door occupancy wiring).

File: `custom_components/auto_off/binary_sensor.py`

```python
"""Legacy binary_sensor platform for auto_off.

The auto_off integration no longer exposes any binary_sensor entities of
its own. This file is kept until PLATFORMS is reduced in the next task
so that async_forward_entry_setups does not complain about a missing
platform module.
"""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .integration_manager import async_setup_integration


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Compatibility shim; does not register any entities."""
    await async_setup_integration(hass, entry, async_add_entities)
```

- [ ] **Step 4: Update `test_integration_manager.py`**

Remove the `DoorOccupancyManager` patching and the door-occupancy assertions.

Edits to `custom_components/auto_off/tests/test_integration_manager.py`:

1. In the `manager` fixture, delete both the second nested `with patch(...DoorOccupancyManager...)` block and the `manager.door_occupancy = ...` assignments. Replace the fixture with:

```python
    @pytest.fixture
    def manager(self, hass, config_entry, async_add_entities):
        """Create an IntegrationManager instance."""
        with patch(
            "custom_components.auto_off.integration_manager.AutoOffManager"
        ) as mock_aom:
            mock_aom.return_value = MagicMock()
            manager = IntegrationManager(hass, config_entry, async_add_entities)
            manager.auto_off = MagicMock()
            manager.auto_off.config = {}
            manager.auto_off._groups = {}
            manager.auto_off.async_init_groups = AsyncMock()
            manager.auto_off.async_unload = AsyncMock()
            return manager
```

2. In `test_async_initialize`, delete:

```python
        manager.door_occupancy._discover_and_add_sensors.assert_called_once()
```

3. In `test_async_unload`, delete:

```python
        manager.door_occupancy.async_unload.assert_called_once()
```

- [ ] **Step 5: Run the auto_off unit tests**

Run: `pytest custom_components/auto_off/tests/test_integration_manager.py -v`

Expected: all tests pass.

- [ ] **Step 6: Run the full unit suite**

Run: `pytest custom_components/ -v -m "not docker_e2e"`

Expected: all unit tests pass across both `auto_off` and `door_occupancy`.

- [ ] **Step 7: Commit**

```bash
git add custom_components/auto_off/integration_manager.py \
        custom_components/auto_off/binary_sensor.py \
        custom_components/auto_off/tests/test_integration_manager.py
git commit -m "refactor(auto_off): remove door_occupancy code from auto_off

The door_occupancy package now owns occupancy binary_sensor discovery
and lifecycle. auto_off's binary_sensor.py is reduced to a compatibility
shim (removed entirely in the following commit when PLATFORMS changes)."
```

---

### Task 8: Move manager setup to `auto_off.__init__` and remove binary_sensor platform

The current `auto_off` integration has a quirky indirection: the
`IntegrationManager` is constructed inside `binary_sensor.async_setup_entry`
via `async_setup_integration`. After Task 7 the binary_sensor platform is
no longer useful. Move manager construction into `auto_off/__init__.async_setup_entry`
and drop the binary_sensor platform entirely.

**Files:**
- Modify: `custom_components/auto_off/__init__.py`
- Modify: `custom_components/auto_off/const.py`
- Modify: `custom_components/auto_off/integration_manager.py`
- Delete: `custom_components/auto_off/binary_sensor.py`
- Modify: `custom_components/auto_off/tests/test_integration_manager.py`

- [ ] **Step 1: Reduce PLATFORMS in `const.py`**

File: `custom_components/auto_off/const.py`

Change the line:

```python
PLATFORMS = ["binary_sensor", "sensor", "text"]
```

to:

```python
PLATFORMS = ["sensor", "text"]
```

- [ ] **Step 2: Delete the binary_sensor shim**

```bash
git rm custom_components/auto_off/binary_sensor.py
```

- [ ] **Step 3: Move manager construction into `auto_off/__init__.py`**

Edit `custom_components/auto_off/__init__.py` `async_setup_entry` to construct
the manager directly, then forward platforms. Replace the body of
`async_setup_entry` with:

```python
async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Auto Off from a config entry."""
    from .integration_manager import IntegrationManager

    manager = IntegrationManager(hass, entry)
    hass.data[DOMAIN] = manager
    await manager.async_initialize()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    await _async_register_services(hass, entry)
    return True
```

Notes:
- `IntegrationManager.__init__` no longer needs the `async_add_entities`
  parameter; update its signature to `(self, hass, entry)` in Step 4.
- `async_setup_integration` / `async_unload_integration` helpers in
  `integration_manager.py` become unnecessary; remove them in Step 4.

- [ ] **Step 4: Simplify `integration_manager.py`**

Make these edits to `custom_components/auto_off/integration_manager.py`:

1. Change the constructor signature:

```python
    def __init__(self, hass, entry):
        self.hass = hass
        self.entry = entry
        self._sensor_async_add_entities: AddEntitiesCallback | None = None
        self._sensor_entities: dict[str, Any] = {}
        self._deadline_entities: dict[str, Any] = {}
        self._text_async_add_entities: AddEntitiesCallback | None = None
        self._text_entities: dict[str, Any] = {}

        groups_data = entry.data.get(CONF_GROUPS, {})
        group_configs = parse_group_configs(groups_data)

        self.auto_off = AutoOffManager(
            hass,
            group_configs,
            on_deadline_change=self._on_deadline_change,
        )
        self._lock = asyncio.Lock()
        self._remove_listener = None
        self._groups_data: dict[str, dict] = dict(groups_data)
```

2. Delete the entire `async_setup_integration` function at the bottom of the file.

3. Delete `async_unload_integration` and the `global default_manager` pattern
   (`default_manager = None`, all assignments). Replace the `async_unload_integration`
   usage in `__init__.py` by inlining manager lookup and `async_unload()`
   (already done in Task 8, Step 5 below).

4. In `async_initialize`, remove the already-deleted door_occupancy block
   (carried from Task 7, should already be absent). The remaining code is:

```python
    async def async_initialize(self):
        """Initialize the integration manager."""
        await self.auto_off.async_init_groups()

        poll_interval = self.entry.data.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)
        self._remove_listener = async_track_time_interval(
            self.hass, self._periodic_worker, timedelta(seconds=poll_interval)
        )
        _LOGGER.info("IntegrationManager initialized with poll_interval %ds", poll_interval)
```

5. In `async_unload`, keep as-is; no door_occupancy reference remains.

- [ ] **Step 5: Update `auto_off/__init__.py` unload path**

Edit `async_unload_entry` to get the manager from `hass.data` and call its
`async_unload` directly, instead of `async_unload_integration`:

```python
async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        hass.services.async_remove(DOMAIN, SERVICE_SET_GROUP)
        hass.services.async_remove(DOMAIN, SERVICE_DELETE_GROUP)

        manager = hass.data.pop(DOMAIN, None)
        if manager is not None:
            await manager.async_unload()

    return unload_ok
```

Also remove the now-obsolete top-level import `from .integration_manager import async_unload_integration` and replace it with an in-function import if `IntegrationManager` is needed in `async_setup_entry` only (as in Step 3).

- [ ] **Step 6: Update `test_integration_manager.py`**

Remove the `TestAsyncSetupIntegration` and `TestAsyncUnloadIntegration`
classes (lines around 170–204); those helpers are gone. Also update the
`manager` fixture signature to match the new `IntegrationManager(hass, entry)`:

```python
    @pytest.fixture
    def manager(self, hass, config_entry):
        """Create an IntegrationManager instance."""
        with patch(
            "custom_components.auto_off.integration_manager.AutoOffManager"
        ) as mock_aom:
            mock_aom.return_value = MagicMock()
            manager = IntegrationManager(hass, config_entry)
            manager.auto_off = MagicMock()
            manager.auto_off.config = {}
            manager.auto_off._groups = {}
            manager.auto_off.async_init_groups = AsyncMock()
            manager.auto_off.async_unload = AsyncMock()
            return manager
```

And remove the `async_add_entities` fixture argument from individual test
methods if present (it was only used by the deleted classes).

Delete the imports that no longer exist:

```python
from custom_components.auto_off.integration_manager import (
    IntegrationManager,
    parse_group_configs,
    async_setup_integration,      # DELETE
    async_unload_integration,     # DELETE
)
```

- [ ] **Step 7: Run the unit suite**

Run: `pytest custom_components/ -v -m "not docker_e2e"`

Expected: all unit tests pass.

- [ ] **Step 8: Commit**

```bash
git add custom_components/auto_off/__init__.py \
        custom_components/auto_off/const.py \
        custom_components/auto_off/integration_manager.py \
        custom_components/auto_off/tests/test_integration_manager.py
git add -u   # picks up the binary_sensor.py deletion from Step 2
git commit -m "refactor(auto_off): drop binary_sensor platform and indirection

Moves IntegrationManager construction into async_setup_entry, removes
the 'setup-via-binary_sensor-platform' indirection and the global
default_manager. PLATFORMS is reduced to sensor + text."
```

---

### Task 9: Update `GroupConfig` and `Sensor` for explicit kind (TDD)

Replace the runtime `'{{' in raw` heuristic in `Sensor._detect_template` with
an explicit constructor argument, and extend `GroupConfig` with the new
`sensor_templates` field. Group behavior is unchanged apart from the
validator that requires at least one of `sensors` / `sensor_templates`
plus at least one target.

**Files:**
- Modify: `custom_components/auto_off/auto_off.py`
- Modify: `custom_components/auto_off/const.py`
- Create: `custom_components/auto_off/tests/test_group_config.py`

- [ ] **Step 1: Add `CONF_SENSOR_TEMPLATES` to `const.py`**

Add the line right after `CONF_SENSORS`:

```python
CONF_SENSOR_TEMPLATES = "sensor_templates"
```

Also bump the config entry version constant (add a new line):

```python
CONFIG_VERSION = 3
```

- [ ] **Step 2: Write failing tests for the new GroupConfig**

File: `custom_components/auto_off/tests/test_group_config.py`

```python
"""Tests for the GroupConfig pydantic model."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from custom_components.auto_off.auto_off import GroupConfig


class TestGroupConfig:
    def test_valid_config_with_sensors_only(self):
        cfg = GroupConfig(
            targets=["light.a"],
            sensors=["binary_sensor.motion"],
        )
        assert cfg.sensor_templates == []
        assert cfg.delay == 0

    def test_valid_config_with_sensor_templates_only(self):
        cfg = GroupConfig(
            targets=["light.a"],
            sensor_templates=["{{ is_state('binary_sensor.motion', 'on') }}"],
        )
        assert cfg.sensors == []

    def test_rejects_empty_targets(self):
        with pytest.raises(ValidationError):
            GroupConfig(targets=[], sensors=["binary_sensor.motion"])

    def test_rejects_no_sensor_sources(self):
        with pytest.raises(ValidationError):
            GroupConfig(targets=["light.a"], sensors=[], sensor_templates=[])

    def test_delay_accepts_int(self):
        cfg = GroupConfig(targets=["light.a"], sensors=["binary_sensor.motion"], delay=5)
        assert cfg.delay == 5

    def test_delay_accepts_template_string(self):
        template = "{{ states('input_number.delay') | int }}"
        cfg = GroupConfig(
            targets=["light.a"],
            sensors=["binary_sensor.motion"],
            delay=template,
        )
        assert cfg.delay == template
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest custom_components/auto_off/tests/test_group_config.py -v`

Expected: at least `test_rejects_no_sensor_sources` and
`test_valid_config_with_sensor_templates_only` fail because the current
`GroupConfig` does not have `sensor_templates` and does not validate.

- [ ] **Step 4: Update `GroupConfig` in `auto_off.py`**

Replace the current `GroupConfig` class (lines 14–24) with:

```python
from pydantic import BaseModel, model_validator


class GroupConfig(BaseModel):
    """Configuration for a single auto-off group.

    A group is active while any sensor (entity or template) reports `on`.
    Once all sensors are off and any target is on, the delay starts and
    eventually turns off every target.
    """

    targets: list[str]
    sensors: list[str] = []
    sensor_templates: list[str] = []
    delay: int | str = 0

    @model_validator(mode="after")
    def _require_targets_and_sensor_source(self) -> "GroupConfig":
        if not self.targets:
            raise ValueError("'targets' must be non-empty")
        if not self.sensors and not self.sensor_templates:
            raise ValueError(
                "At least one of 'sensors' or 'sensor_templates' must be non-empty"
            )
        return self
```

Delete the old `validate_delay` field validator (no longer needed; pydantic
accepts `int | str = 0`).

- [ ] **Step 5: Add explicit `kind` to `Sensor`**

In `auto_off.py`, replace the `Sensor` class construction logic. Change the
constructor signature from:

```python
    def __init__(self, hass: HomeAssistant, sensor_def, on_state_change_callback):
        ...
        self._is_template = self._detect_template()
```

to:

```python
    def __init__(
        self,
        hass: HomeAssistant,
        raw: str,
        kind: str,
        on_state_change_callback,
    ):
        """Create a sensor wrapper.

        kind: one of "entity" or "template". Determines the tracking path
        and how is_on() resolves.
        """
        if kind not in ("entity", "template"):
            raise ValueError(f"Unsupported sensor kind: {kind!r}")
        self.hass = hass
        self.raw = raw
        self._kind = kind
        self._is_template = kind == "template"
        self._on_change_callback = on_state_change_callback
        self._unsub = None
        self._last_known_good_state: bool | None = None
```

Delete the now-unused `_detect_template` method.

- [ ] **Step 6: Update `SensorGroup._init_from_config`**

Replace the loop that iterates `self._config.sensors` to feed both entity
sensors and template sensors with explicit kinds:

```python
    def _init_from_config(self):
        self._sensors = []
        self._targets = []
        for sensor_id in self._config.sensors:
            try:
                sensor_obj = Sensor(
                    self.hass,
                    sensor_id,
                    kind="entity",
                    on_state_change_callback=self._on_sensor_state_change,
                )
                self._sensors.append(sensor_obj)
                asyncio.create_task(sensor_obj.start_tracking())
            except Exception as e:
                _LOGGER.error(f"Sensor entity '{sensor_id}' is invalid and will be ignored: {e}")
        for template_str in self._config.sensor_templates:
            try:
                sensor_obj = Sensor(
                    self.hass,
                    template_str,
                    kind="template",
                    on_state_change_callback=self._on_sensor_state_change,
                )
                self._sensors.append(sensor_obj)
                asyncio.create_task(sensor_obj.start_tracking())
            except Exception as e:
                _LOGGER.error(f"Sensor template '{template_str}' is invalid and will be ignored: {e}")
        for target_def in self._config.targets:
            target = Target(self.hass, target_def, self._on_target_state_change)
            self._targets.append(target)
            asyncio.create_task(target.start_tracking())
```

- [ ] **Step 7: Run the new group-config tests**

Run: `pytest custom_components/auto_off/tests/test_group_config.py -v`

Expected: all six tests pass.

- [ ] **Step 8: Run the full auto_off unit suite**

Run: `pytest custom_components/auto_off/ -v -m "not docker_e2e"`

Expected: existing tests referring to the legacy `sensors`/`targets`/`delay`
fields still pass because their fixtures use valid configs. Any that broke
due to the validator will be fixed in Task 10 when the service fixtures and
`conftest_unit.py` are updated — if any fail at this point, note the failure
and proceed to Task 10. (The `conftest_unit.py` `sample_group_config_dict`
is still valid because it has both `targets` and `sensors`.)

- [ ] **Step 9: Commit**

```bash
git add custom_components/auto_off/auto_off.py \
        custom_components/auto_off/const.py \
        custom_components/auto_off/tests/test_group_config.py
git commit -m "feat(auto_off): explicit sensor kind + sensor_templates field

GroupConfig now has sensors (entity ids) and sensor_templates (Jinja
strings) as separate fields. Sensor takes an explicit kind argument
instead of heuristically detecting templates by the presence of
'{{'. Model validator requires non-empty targets and at least one
sensor source."
```

---

### Task 10: Replace `set_group` service with structured fields (TDD)

Remove `CONF_CONFIG`. The service now takes structured `targets`, `sensors`,
`sensor_templates`, `delay` fields directly.

**Files:**
- Modify: `custom_components/auto_off/__init__.py`
- Modify: `custom_components/auto_off/services.yaml`
- Modify: `custom_components/auto_off/strings.json`
- Modify: `custom_components/auto_off/translations/en.json`
- Modify: `custom_components/auto_off/const.py`
- Modify: `custom_components/auto_off/tests/conftest_unit.py`
- Modify: `custom_components/auto_off/tests/test_services.py`
- Create: `custom_components/auto_off/tests/test_set_group_service.py`

- [ ] **Step 1: Remove `CONF_CONFIG` from `const.py`**

Delete the line:

```python
CONF_CONFIG = "config"
```

Add (if not already present):

```python
CONF_SENSOR_TEMPLATES = "sensor_templates"
```

- [ ] **Step 2: Add a `dict`-shaped fixture for the new service payload**

Edit `custom_components/auto_off/tests/conftest_unit.py`. Add:

```python
@pytest.fixture
def sample_set_group_payload():
    """Service call data for set_group in the new structured shape."""
    return {
        "group_name": "kitchen",
        "targets": ["light.kitchen"],
        "sensors": ["binary_sensor.motion_kitchen"],
        "sensor_templates": [],
        "delay": 5,
    }
```

Keep `sample_group_config_dict` unchanged; it is still useful for internal
group payloads stored in `hass.data[DOMAIN]._groups_data`.

- [ ] **Step 3: Write failing tests for the new service handler**

File: `custom_components/auto_off/tests/test_set_group_service.py`

```python
"""Tests for the new structured auto_off.set_group service.

Covers behavior: validates via GroupConfig, constructs the internal
config dict in the shape the manager expects, and rejects invalid
combinations (empty targets, no sensor sources).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.auto_off.const import CONF_GROUPS, DOMAIN


@pytest.fixture
def registered_handler(hass, config_entry):
    """Register the services and return the set_group handler."""
    from custom_components.auto_off import _async_register_services

    async def _register():
        await _async_register_services(hass, config_entry)

    # Run the sync fixture body in an async helper.
    import asyncio
    asyncio.get_event_loop().run_until_complete(_register())
    set_group_call = hass.services.async_register.call_args_list[0]
    return set_group_call[0][2]


class TestSetGroupStructured:
    @pytest.mark.asyncio
    async def test_creates_group_from_structured_fields(
        self, hass, config_entry, sample_set_group_payload
    ):
        from custom_components.auto_off import _async_register_services

        mock_manager = MagicMock()
        mock_manager.set_group = AsyncMock()
        hass.data[DOMAIN] = mock_manager

        await _async_register_services(hass, config_entry)
        handler = hass.services.async_register.call_args_list[0][0][2]

        call = MagicMock()
        call.data = sample_set_group_payload
        await handler(call)

        mock_manager.set_group.assert_called_once()
        args = mock_manager.set_group.call_args[0]
        assert args[0] == "kitchen"
        assert args[1] == {
            "targets": ["light.kitchen"],
            "sensors": ["binary_sensor.motion_kitchen"],
            "sensor_templates": [],
            "delay": 5,
        }
        assert args[2] is True  # is_new_group

    @pytest.mark.asyncio
    async def test_rejects_empty_targets(self, hass, config_entry):
        from custom_components.auto_off import _async_register_services

        mock_manager = MagicMock()
        mock_manager.set_group = AsyncMock()
        hass.data[DOMAIN] = mock_manager

        await _async_register_services(hass, config_entry)
        handler = hass.services.async_register.call_args_list[0][0][2]

        call = MagicMock()
        call.data = {
            "group_name": "bad",
            "targets": [],
            "sensors": ["binary_sensor.a"],
        }
        await handler(call)

        mock_manager.set_group.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejects_no_sensor_source(self, hass, config_entry):
        from custom_components.auto_off import _async_register_services

        mock_manager = MagicMock()
        mock_manager.set_group = AsyncMock()
        hass.data[DOMAIN] = mock_manager

        await _async_register_services(hass, config_entry)
        handler = hass.services.async_register.call_args_list[0][0][2]

        call = MagicMock()
        call.data = {
            "group_name": "bad",
            "targets": ["light.a"],
            "sensors": [],
            "sensor_templates": [],
        }
        await handler(call)

        mock_manager.set_group.assert_not_called()

    @pytest.mark.asyncio
    async def test_accepts_sensor_templates_only(self, hass, config_entry):
        from custom_components.auto_off import _async_register_services

        mock_manager = MagicMock()
        mock_manager.set_group = AsyncMock()
        hass.data[DOMAIN] = mock_manager

        await _async_register_services(hass, config_entry)
        handler = hass.services.async_register.call_args_list[0][0][2]

        call = MagicMock()
        call.data = {
            "group_name": "tpl_only",
            "targets": ["light.a"],
            "sensors": [],
            "sensor_templates": ["{{ is_state('light.a', 'on') }}"],
            "delay": 1,
        }
        await handler(call)

        mock_manager.set_group.assert_called_once()

    @pytest.mark.asyncio
    async def test_accepts_delay_as_template_string(self, hass, config_entry):
        from custom_components.auto_off import _async_register_services

        mock_manager = MagicMock()
        mock_manager.set_group = AsyncMock()
        hass.data[DOMAIN] = mock_manager

        await _async_register_services(hass, config_entry)
        handler = hass.services.async_register.call_args_list[0][0][2]

        template = "{{ states('input_number.delay') | int }}"
        call = MagicMock()
        call.data = {
            "group_name": "kitchen",
            "targets": ["light.a"],
            "sensors": ["binary_sensor.b"],
            "delay": template,
        }
        await handler(call)

        mock_manager.set_group.assert_called_once()
        args = mock_manager.set_group.call_args[0]
        assert args[1]["delay"] == template
```

- [ ] **Step 4: Delete the old YAML-based tests**

```bash
git rm custom_components/auto_off/tests/test_services.py
```

- [ ] **Step 5: Rewrite `_async_register_services` in `__init__.py`**

Replace the existing service registration and schema in
`custom_components/auto_off/__init__.py`. Top-level imports become:

```python
import logging

import voluptuous as vol
from pydantic import ValidationError

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.config_entries import ConfigEntry, SOURCE_IMPORT
from homeassistant.helpers import config_validation as cv, device_registry as dr

from .const import (
    DOMAIN,
    CONF_GROUPS,
    CONF_GROUP_NAME,
    CONF_SENSORS,
    CONF_SENSOR_TEMPLATES,
    CONF_TARGETS,
    CONF_DELAY,
    SERVICE_SET_GROUP,
    SERVICE_DELETE_GROUP,
    PLATFORMS,
)
from .auto_off import GroupConfig
```

Replace the two service schemas with:

```python
SERVICE_SET_GROUP_SCHEMA = vol.Schema({
    vol.Required(CONF_GROUP_NAME): cv.string,
    vol.Required(CONF_TARGETS): vol.All(cv.ensure_list, [cv.entity_id]),
    vol.Optional(CONF_SENSORS, default=list): vol.All(cv.ensure_list, [cv.entity_id]),
    vol.Optional(CONF_SENSOR_TEMPLATES, default=list): vol.All(cv.ensure_list, [cv.string]),
    vol.Optional(CONF_DELAY, default=0): vol.Any(int, cv.string),
})

SERVICE_DELETE_GROUP_SCHEMA = vol.Schema({
    vol.Required(CONF_GROUP_NAME): cv.string,
})
```

Replace `handle_set_group` with:

```python
    async def handle_set_group(call: ServiceCall) -> None:
        """Create or update an auto-off group from structured service data."""
        group_name = call.data[CONF_GROUP_NAME]
        config_dict = {
            CONF_TARGETS: list(call.data[CONF_TARGETS]),
            CONF_SENSORS: list(call.data.get(CONF_SENSORS, [])),
            CONF_SENSOR_TEMPLATES: list(call.data.get(CONF_SENSOR_TEMPLATES, [])),
            CONF_DELAY: call.data.get(CONF_DELAY, 0),
        }

        try:
            GroupConfig.model_validate(config_dict)
        except ValidationError as err:
            _LOGGER.error("Invalid config for group '%s': %s", group_name, err.errors())
            return

        current_groups = dict(entry.data.get(CONF_GROUPS, {}))
        is_new_group = group_name not in current_groups
        current_groups[group_name] = config_dict

        new_data = dict(entry.data)
        new_data[CONF_GROUPS] = current_groups
        hass.config_entries.async_update_entry(entry, data=new_data)

        manager = hass.data.get(DOMAIN)
        if manager is None:
            _LOGGER.error("Integration manager not found")
            return

        await manager.set_group(group_name, config_dict, is_new_group)
        _LOGGER.info(
            "Group '%s' %s", group_name, "created" if is_new_group else "updated"
        )
```

Replace `handle_delete_group` with a narrower error policy (catch
`HomeAssistantError` only; let unexpected errors propagate):

```python
    async def handle_delete_group(call: ServiceCall) -> None:
        """Delete an auto-off group."""
        group_name = call.data[CONF_GROUP_NAME]

        current_groups = dict(entry.data.get(CONF_GROUPS, {}))
        if group_name not in current_groups:
            _LOGGER.warning("Group '%s' does not exist", group_name)
            return

        del current_groups[group_name]
        new_data = dict(entry.data)
        new_data[CONF_GROUPS] = current_groups
        hass.config_entries.async_update_entry(entry, data=new_data)

        manager = hass.data.get(DOMAIN)
        if manager is None:
            _LOGGER.error("Integration manager not found")
            return

        await manager.delete_group(group_name)
        _LOGGER.info("Group '%s' deleted", group_name)
```

Also remove the legacy YAML import path: delete the `async_setup` function
entirely (it only existed to import `yaml` groups). HA calls
`async_setup_entry` automatically for UI-added entries.

- [ ] **Step 6: Update `services.yaml`**

Replace the contents of `custom_components/auto_off/services.yaml` with:

```yaml
set_group:
  name: Set Group
  description: Create or update an auto-off group.
  fields:
    group_name:
      name: Group Name
      description: Unique name of the group.
      required: true
      example: kitchen
      selector:
        text:
    targets:
      name: Targets
      description: Entities to turn off when the delay elapses.
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
        Jinja templates rendered to bool. Treated the same as binary sensors.
      required: false
      selector:
        object:
    delay:
      name: Delay (minutes)
      description: >
        Integer (minutes) or a Jinja template rendering to minutes.
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
      name: Group Name
      description: Name of the group to delete.
      required: true
      example: kitchen
      selector:
        text:
```

- [ ] **Step 7: Update `strings.json` and `translations/en.json`**

In both files, replace the `services.set_group.fields` block to match the new
field list:

```json
  "services": {
    "set_group": {
      "name": "Set Group",
      "description": "Create or update an auto-off group.",
      "fields": {
        "group_name": {
          "name": "Group Name",
          "description": "Unique name of the group."
        },
        "targets": {
          "name": "Targets",
          "description": "Entities to turn off when the delay elapses."
        },
        "sensors": {
          "name": "Sensors",
          "description": "Binary sensors whose OFF state starts the turn-off timer."
        },
        "sensor_templates": {
          "name": "Sensor templates",
          "description": "Jinja templates rendered to bool."
        },
        "delay": {
          "name": "Delay (minutes)",
          "description": "Integer minutes or a Jinja template rendering to minutes."
        }
      }
    },
    "delete_group": {
      "name": "Delete Group",
      "description": "Delete an auto-off group.",
      "fields": {
        "group_name": {
          "name": "Group Name",
          "description": "Name of the group to delete."
        }
      }
    }
  }
```

Keep the surrounding `config`/`options` sections unchanged.

- [ ] **Step 8: Drop `pyyaml` requirement from `manifest.json`**

Edit `custom_components/auto_off/manifest.json` and replace `requirements` with:

```json
  "requirements": [
    "pydantic"
  ],
```

(Remove `jinja2`: HA ships it. Remove `pyyaml`: no longer imported.)

- [ ] **Step 9: Run the new service tests**

Run: `pytest custom_components/auto_off/tests/test_set_group_service.py -v`

Expected: five tests pass.

- [ ] **Step 10: Run the full unit suite**

Run: `pytest custom_components/ -v -m "not docker_e2e"`

Expected: all unit tests pass. (If existing `test_config_flow.py::test_step_import_creates_entry`
breaks because `async_setup` was removed, adjust that test: either remove
the assertion on YAML import behavior or mark it skipped with a reference
to the removal of YAML import support — note this in the commit message.)

- [ ] **Step 11: Commit**

```bash
git add custom_components/auto_off/__init__.py \
        custom_components/auto_off/services.yaml \
        custom_components/auto_off/strings.json \
        custom_components/auto_off/translations/en.json \
        custom_components/auto_off/const.py \
        custom_components/auto_off/manifest.json \
        custom_components/auto_off/tests/conftest_unit.py \
        custom_components/auto_off/tests/test_set_group_service.py
git add -u   # picks up the test_services.py deletion
git commit -m "feat(auto_off): structured set_group service fields

set_group now takes targets / sensors / sensor_templates / delay as
distinct fields instead of one YAML-string 'config' parameter.
Validation goes through GroupConfig and reports pydantic errors.
YAML import path (async_setup) removed; HA UI is the only supported
setup path. pyyaml dropped from requirements."
```

---

### Task 11: Add `async_migrate_entry` for breaking version bump

The new config entry format is incompatible with existing entries (version 2).
Users are expected to delete and recreate, as agreed in the design. The
migration function logs a clear, actionable error and returns `False` so HA
marks the entry as needing reconfiguration.

**Files:**
- Modify: `custom_components/auto_off/__init__.py`
- Modify: `custom_components/auto_off/config_flow.py`
- Create: `custom_components/auto_off/tests/test_migration.py`

- [ ] **Step 1: Bump `VERSION` in `config_flow.py`**

Change:

```python
class AutoOffConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 2
```

to:

```python
class AutoOffConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 3
```

- [ ] **Step 2: Add a failing migration test**

File: `custom_components/auto_off/tests/test_migration.py`

```python
"""Tests for auto_off config entry migration.

Covers the v3 cutover: older entries (version < 3) must fail migration
with a clear message directing users to the README migration section.
"""
from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest


class TestAsyncMigrateEntry:
    @pytest.mark.asyncio
    async def test_old_entry_fails_migration(self, hass, caplog):
        """An entry at version 2 must not be silently migrated."""
        from custom_components.auto_off import async_migrate_entry

        entry = MagicMock()
        entry.version = 2
        entry.data = {"groups": {"kitchen": {"sensors": [], "targets": [], "delay": 0}}}

        with caplog.at_level(logging.ERROR):
            result = await async_migrate_entry(hass, entry)

        assert result is False
        assert any(
            "migration" in record.message.lower() for record in caplog.records
        )
        assert any(
            "readme" in record.message.lower() or "reinstall" in record.message.lower()
            for record in caplog.records
        )

    @pytest.mark.asyncio
    async def test_current_version_entry_passes(self, hass):
        """An entry already at version 3 is considered up to date."""
        from custom_components.auto_off import async_migrate_entry

        entry = MagicMock()
        entry.version = 3
        entry.data = {"groups": {}}

        result = await async_migrate_entry(hass, entry)
        assert result is True
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest custom_components/auto_off/tests/test_migration.py -v`

Expected: both tests fail with `ImportError: cannot import name 'async_migrate_entry'`.

- [ ] **Step 4: Implement `async_migrate_entry`**

Add to `custom_components/auto_off/__init__.py` (below `async_unload_entry`):

```python
async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Handle config entry migration for auto_off.

    Version 3 introduces a breaking change to the group payload shape
    (structured fields instead of a YAML string) and moves occupancy
    sensors to the separate door_occupancy integration. Auto-migration
    is intentionally not implemented; users are asked to delete the
    old entry and recreate groups via the new set_group service.
    See docs/superpowers/specs/2026-04-22-split-integrations-design.md
    section 'Migration (for users)'.
    """
    if entry.version >= 3:
        return True

    _LOGGER.error(
        "Auto Off config entry at version %s requires manual migration. "
        "Delete this integration entry and reinstall per the README "
        "migration section.",
        entry.version,
    )
    return False
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest custom_components/auto_off/tests/test_migration.py -v`

Expected: both tests pass.

- [ ] **Step 6: Commit**

```bash
git add custom_components/auto_off/__init__.py \
        custom_components/auto_off/config_flow.py \
        custom_components/auto_off/tests/test_migration.py
git commit -m "feat(auto_off): version 3 config entry with fail-loud migration

Old entries (version < 3) fail migration with an explicit log message
pointing users to the README migration section. HA surfaces a
reconfiguration prompt; there is no silent transform."
```

---

### Task 12: Remove `GroupConfigSensorEntity`

The per-group Config summary sensor is a static UI mirror with no behavior.
Remove the class and all wiring; keep `DeadlineSensorEntity` on the `sensor`
platform and `DelayTextEntity` on the `text` platform.

**Files:**
- Modify: `custom_components/auto_off/sensor.py`
- Modify: `custom_components/auto_off/integration_manager.py`
- Modify: `custom_components/auto_off/tests/test_integration_manager.py`

- [ ] **Step 1: Delete `GroupConfigSensorEntity` from `sensor.py`**

Remove the `GroupConfigSensorEntity` class entirely (currently lines ~40–98
of `custom_components/auto_off/sensor.py`). Keep `DeadlineSensorEntity` and
`async_setup_entry`. Also remove the now-unused helper `_format_delay_minutes`
and the `CONF_SENSORS`/`CONF_TARGETS` imports if they become unused.

The resulting `sensor.py` should only contain `async_setup_entry` and
`DeadlineSensorEntity`.

- [ ] **Step 2: Remove bookkeeping from `integration_manager.py`**

Edits:

1. Delete the `self._sensor_entities: dict[str, Any] = {}` field from `__init__`.

2. Delete the `_create_sensor_entities_for_existing_groups` method entirely.

3. Update `sensor_platform_ready` so it only creates `DeadlineSensorEntity`:

```python
    def sensor_platform_ready(self, async_add_entities: AddEntitiesCallback) -> None:
        """Called when the sensor platform is ready."""
        self._sensor_async_add_entities = async_add_entities
        self._create_deadline_entities_for_existing_groups()

    def _create_deadline_entities_for_existing_groups(self) -> None:
        if not self._sensor_async_add_entities:
            return

        from .sensor import DeadlineSensorEntity

        new_entities = []
        for group_name in self._groups_data:
            if group_name in self._deadline_entities:
                continue
            deadline_entity = DeadlineSensorEntity(self.hass, self.entry, group_name)
            self._deadline_entities[group_name] = deadline_entity
            new_entities.append(deadline_entity)

        if new_entities:
            self._sensor_async_add_entities(new_entities)
            _LOGGER.info(
                "Created %d deadline sensor entities for groups", len(new_entities)
            )
```

4. In `set_group`, replace the `is_new and self._sensor_async_add_entities`
   block so it only creates the deadline entity (no config sensor):

```python
            if is_new and self._sensor_async_add_entities:
                from .sensor import DeadlineSensorEntity

                deadline_entity = DeadlineSensorEntity(
                    self.hass, self.entry, group_name
                )
                self._deadline_entities[group_name] = deadline_entity
                self._sensor_async_add_entities([deadline_entity])
                _LOGGER.info("Created deadline sensor for new group '%s'", group_name)

                self._update_deadline_sensor_for_group(group_name)
            # No `elif group_name in self._sensor_entities:` branch — the
            # config sensor no longer exists.
```

5. In `delete_group`, delete the block that removes `self._sensor_entities`:

```python
            if group_name in self._sensor_entities:
                entity = self._sensor_entities.pop(group_name)
                ent_reg = er.async_get(self.hass)
                if ent_reg and entity.entity_id:
                    ent_reg.async_remove(entity.entity_id)
```

6. In `async_unload`, delete:

```python
        self._sensor_entities.clear()
```

- [ ] **Step 3: Update `test_integration_manager.py`**

Find the assertion in `test_set_group_creates_new`:

```python
        manager._text_async_add_entities.assert_called_once()
```

Leave this as-is (text entity creation is unchanged).

Add checks that `_sensor_async_add_entities` was called with a list of one
`DeadlineSensorEntity`. Replace `test_set_group_creates_new` body with:

```python
    @pytest.mark.asyncio
    async def test_set_group_creates_new(
        self, manager, sample_group_config_dict
    ):
        manager._text_async_add_entities = MagicMock()
        manager._sensor_async_add_entities = MagicMock()

        await manager.set_group("new_group", sample_group_config_dict, is_new=True)

        assert "new_group" in manager._groups_data
        assert manager._groups_data["new_group"] == sample_group_config_dict
        manager.auto_off.async_init_groups.assert_awaited_once()
        manager._text_async_add_entities.assert_called_once()
        # Exactly one deadline sensor is created, no config sensor.
        manager._sensor_async_add_entities.assert_called_once()
        added = manager._sensor_async_add_entities.call_args[0][0]
        assert len(added) == 1
```

- [ ] **Step 4: Run unit suite**

Run: `pytest custom_components/ -v -m "not docker_e2e"`

Expected: all unit tests pass.

- [ ] **Step 5: Commit**

```bash
git add custom_components/auto_off/sensor.py \
        custom_components/auto_off/integration_manager.py \
        custom_components/auto_off/tests/test_integration_manager.py
git commit -m "refactor(auto_off): drop GroupConfigSensorEntity

Configuration management lives in services now; the per-group Config
summary sensor is a static UI mirror with no behavior. Removing it
trims one platform registration per group and the _sensor_entities
bookkeeping path. Each group keeps DeadlineSensorEntity and
DelayTextEntity."
```

---

### Task 13: Update e2e scenarios and HA packages

The e2e suite under `custom_components/auto_off/tests/test_integration_e2e.py`
and `test_e2e_playwright.py` uses the old `config:` service payload and
references `sensor.auto_off_*_config`. Update both to the new contract and
add a minimal e2e registration path for `door_occupancy`.

**Files:**
- Modify: `custom_components/auto_off/tests/test_integration_e2e.py`
- Modify: `custom_components/auto_off/tests/test_e2e_playwright.py`
- Modify: `custom_components/auto_off/tests/ha_packages/auto_off_test.yaml` (if needed)
- Create: `custom_components/door_occupancy/tests/__init__.py` (already exists from Task 1)
- Optional create: `custom_components/door_occupancy/tests/test_integration_e2e.py`
- Optional create: `custom_components/door_occupancy/tests/ha_packages/door_occupancy_test.yaml`

- [ ] **Step 1: Rewrite `test_integration_e2e.py` set_group calls**

In every `ha_instance.call_service("auto_off", "set_group", {...})` call,
replace the `config: <yaml-string>` payload with structured fields. The
existing YAML content must be translated as follows:

For `test_set_group_service`:

```python
        await ha_instance.call_service("auto_off", "set_group", {
            "group_name": "test_group",
            "targets": ["light.test_light"],
            "sensors": ["binary_sensor.test_motion"],
            "delay": 1,
        })
```

For `test_auto_off_functionality`:

```python
        await ha_instance.call_service("auto_off", "set_group", {
            "group_name": "test_auto_off_group",
            "targets": ["light.test_light_2"],
            "sensors": ["binary_sensor.test_motion_2"],
            "delay": 0,
        })
```

For `test_delete_group_service`:

```python
        await ha_instance.call_service("auto_off", "set_group", {
            "group_name": "group_to_delete",
            "targets": ["light.test_light"],
            "sensors": ["binary_sensor.test_motion"],
            "delay": 5,
        })
```

For `test_update_group_config`:

```python
        # v1
        await ha_instance.call_service("auto_off", "set_group", {
            "group_name": "update_test_group",
            "targets": ["light.test_light"],
            "sensors": ["binary_sensor.test_motion"],
            "delay": 5,
        })
        await asyncio.sleep(2)
        # v2
        await ha_instance.call_service("auto_off", "set_group", {
            "group_name": "update_test_group",
            "targets": ["light.test_light", "light.test_light_2"],
            "sensors": ["binary_sensor.test_motion", "binary_sensor.test_motion_2"],
            "delay": 10,
        })
```

For `test_set_group_invalid_yaml`: rename to `test_set_group_empty_targets`:

```python
    async def test_set_group_empty_targets(self, ha_instance):
        """Empty targets must be rejected (validated by GroupConfig)."""
        with pytest.raises(Exception):
            await ha_instance.call_service("auto_off", "set_group", {
                "group_name": "invalid_group",
                "targets": [],
                "sensors": ["binary_sensor.test_motion"],
            })
```

For `test_set_group_missing_required_fields`: rename to
`test_set_group_requires_sensor_source`:

```python
    async def test_set_group_requires_sensor_source(self, ha_instance):
        with pytest.raises(Exception):
            await ha_instance.call_service("auto_off", "set_group", {
                "group_name": "incomplete_group",
                "targets": ["light.test_light"],
                "sensors": [],
                "sensor_templates": [],
            })
```

Remove the two `text.auto_off_*_config` state lookups in
`test_set_group_service` and `test_delete_group_service` — they referenced
`text.` with a `_config` suffix that never existed (only `sensor.*_config`
existed, now removed). Replace with asserts on `sensor.auto_off_<group>_deadline`
presence:

```python
        state = await ha_instance.get_state("sensor.auto_off_test_group_deadline")
        assert state is not None
```

- [ ] **Step 2: Rewrite `test_e2e_playwright.py` config-sensor assertions**

Search for `config_entity = f"sensor.auto_off_{group_name}_config"` (two
occurrences) and related state reads. Replace the verification with reads of
`text.auto_off_<group_name>_delay_minutes` for delay-persistence scenarios,
and with `sensor.auto_off_<group_name>_deadline` for group existence.

Concretely, replace:

```python
            config_entity = f"sensor.auto_off_{group_name}_config"
            config_state = await ha_instance.get_state(config_entity)
            assert "90" in config_state["state"], ...
```

with:

```python
            delay_entity = f"text.auto_off_{group_name}_delay_minutes"
            delay_state = await ha_instance.get_state(delay_entity)
            assert delay_state["state"] == "90", (
                f"Delay text entity should show 90, got {delay_state['state']}"
            )
```

(Repeat the pattern wherever `sensor.auto_off_*_config` is referenced.)

- [ ] **Step 3 (optional): Add a minimal door_occupancy e2e smoke test**

File: `custom_components/door_occupancy/tests/test_integration_e2e.py`

```python
"""E2E smoke tests for door_occupancy integration."""
from __future__ import annotations

import asyncio
import pytest

pytestmark = pytest.mark.docker_e2e


@pytest.mark.asyncio
class TestDoorOccupancyE2E:
    async def test_add_door_occupancy_integration(self, ha_instance):
        result = await ha_instance.add_integration(
            "door_occupancy",
            {"poll_interval": 30, "occupancy_timeout": 15},
        )
        if result.get("type") == "create_entry":
            assert result.get("title") == "Door Occupancy"
        elif result.get("type") == "abort":
            assert result.get("reason") in {"already_configured", "single_instance_allowed"}
        else:
            raise AssertionError(f"Unexpected flow result: {result}")

        entries = await ha_instance.get_config_entries("door_occupancy")
        assert len(entries) == 1

    async def test_door_change_pulses_occupancy(self, ha_instance):
        # Requires the door_occupancy test package to include a template
        # binary_sensor with device_class: door named test_door.
        await ha_instance.call_service("input_boolean", "turn_on", {
            "entity_id": "input_boolean.test_door_state",
        })
        await asyncio.sleep(2)

        state = await ha_instance.get_state(
            "binary_sensor.binary_sensor_test_door_occupancy"
        )
        assert state["state"] == "on"

        await asyncio.sleep(20)
        state = await ha_instance.get_state(
            "binary_sensor.binary_sensor_test_door_occupancy"
        )
        assert state["state"] == "off"
```

File: `custom_components/door_occupancy/tests/ha_packages/door_occupancy_test.yaml`

```yaml
# HA package: test entities for door_occupancy E2E tests
template:
  - binary_sensor:
      - name: "Test Door"
        unique_id: test_door
        state: "{{ states('input_boolean.test_door_state') }}"
        device_class: door

input_boolean:
  test_door_state:
    name: Test Door State
    initial: false

logger:
  default: info
  logs:
    custom_components.door_occupancy: debug
```

Note: if the project's e2e harness in `ha-test-kit/ha_test_kit/autoqa.py`
autodiscovers `ha_packages/` directories under `custom_components/*/tests/`,
this package gets picked up automatically. If it only looks in one location,
merge the `input_boolean` and logger entries into the existing
`custom_components/auto_off/tests/ha_packages/auto_off_test.yaml`.

- [ ] **Step 4: Run the unit suite (e2e tests are not run here)**

Run: `pytest custom_components/ -v -m "not docker_e2e"`

Expected: all unit tests still pass; e2e edits do not affect them.

- [ ] **Step 5: Commit**

```bash
git add custom_components/auto_off/tests/test_integration_e2e.py \
        custom_components/auto_off/tests/test_e2e_playwright.py
# Only add the door_occupancy e2e files if step 3 was executed.
git add custom_components/door_occupancy/tests/test_integration_e2e.py \
        custom_components/door_occupancy/tests/ha_packages/door_occupancy_test.yaml 2>/dev/null || true
git commit -m "test: port e2e scenarios to the new split contract

auto_off e2e tests now use structured set_group payload and assert on
text/delay + sensor/deadline entities instead of the removed
sensor/*_config entity. Adds a smoke e2e for door_occupancy setup
and the pulse-on-door-change flow."
```

---

### Task 14: Update `hacs.json` for two integrations

**Files:**
- Modify: `hacs.json`

- [ ] **Step 1: Rewrite `hacs.json`**

File: `hacs.json`

```json
{
  "name": "Auto Off + Door Occupancy",
  "content_in_root": false,
  "render_readme": true,
  "homeassistant": "2025.12.0",
  "zip_release": false
}
```

Notes:
- `domains` and `filename` are intentionally omitted: HACS infers per-package
  manifests from the `custom_components/*/manifest.json` files that get
  copied into the HA config. HA's integration registry picks both up.
- `homeassistant` version bumped to the minimum currently required.

- [ ] **Step 2: Commit**

```bash
git add hacs.json
git commit -m "chore(hacs): ship both integrations from the same repository

Removes the single-domain 'filename' pointer so HACS copies the whole
custom_components/ tree. Users add 'Auto Off' and 'Door Occupancy'
independently in the HA UI."
```

---

### Task 15: Documentation update

**Files:**
- Modify: `README.md`
- Create: `custom_components/auto_off/README.md`
- Create: `custom_components/door_occupancy/README.md`
- Modify: `doc/deadline_logic.md` (if it mentions occupancy)

- [ ] **Step 1: Replace the top-level `README.md`**

File: `README.md`

```markdown
# Auto Off + Door Occupancy (Home Assistant custom integrations)

This repository ships two independent custom integrations:

- **Auto Off** — turns off selected entities after a configurable inactivity
  delay when a group of activity sensors goes off. See
  [custom_components/auto_off/README.md](custom_components/auto_off/README.md).
- **Door Occupancy** — auto-discovers doors, locks, and covers, and creates
  `binary_sensor.*_occupancy` entities that pulse on every real state change.
  See [custom_components/door_occupancy/README.md](custom_components/door_occupancy/README.md).

The two integrations are fully independent: they have separate domains,
config flows, services, and config entries. Install whichever ones you need.

## Installation

- **HACS**: add this repo as a Custom Repository (Integration), install it,
  then restart Home Assistant. Both integrations become available under
  Settings → Devices & services → Add integration.
- **Manual**: copy `custom_components/auto_off/` and/or
  `custom_components/door_occupancy/` into `<config>/custom_components/`,
  then restart Home Assistant.

## Migration from the unified 2512.x release

Starting with the next release, the previous unified `auto_off` integration
is split. This is a **breaking change**. To upgrade:

1. Home Assistant → Settings → Devices & services → Auto Off → **Delete**.
   Existing entities from the old integration are removed:
   - `sensor.auto_off_*_deadline`, `text.auto_off_*_delay_minutes`
   - `sensor.auto_off_*_config` (no longer exists in the new version)
   - `binary_sensor.*_occupancy` (now owned by Door Occupancy)
2. Update the HACS repository. After HA restarts, add the integrations
   separately:
   - Settings → Devices & services → Add integration → **Auto Off**.
     Configure `poll_interval`, then recreate groups via the
     `auto_off.set_group` service (see below for the new payload).
   - Settings → Devices & services → Add integration → **Door Occupancy**.
     Configure `poll_interval` and `occupancy_timeout`. Occupancy sensors
     are created automatically on the first discovery tick.
3. Update any script or automation that called `auto_off.set_group` with
   a YAML string to the new structured shape:

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

   If you previously embedded Jinja templates in the sensor list (detected
   by the presence of `{{`), move those into the new `sensor_templates`
   field.

Old `binary_sensor.*_occupancy` entities from the previous integration are
removed when the old entry is deleted. The new Door Occupancy integration
recreates them with the same `unique_id` format, so HA assigns the same
default entity id string — unless you had renamed them, in which case the
custom name is not restored (registry record is gone).
```

- [ ] **Step 2: Create `custom_components/auto_off/README.md`**

File: `custom_components/auto_off/README.md`

```markdown
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
```

- [ ] **Step 3: Create `custom_components/door_occupancy/README.md`**

File: `custom_components/door_occupancy/README.md`

```markdown
# Door Occupancy

Auto-discovers doors, locks, and covers and creates occupancy
`binary_sensor` entities that pulse on every real state change.

## Setup

Settings → Devices & services → Add integration → **Door Occupancy**. Set:

- `poll_interval` (seconds, 5..300): how often the integration scans HA for
  new door-like sources.
- `occupancy_timeout` (seconds, 1..600): how long the occupancy sensor
  stays on after each state change before auto-resetting.

## Discovery rules

The integration creates one occupancy sensor for each:

- `binary_sensor.*` with `device_class: door`;
- every `lock.*`;
- every `cover.*`.

Entity id format: `binary_sensor.<source_with_dots_as_underscores>_occupancy`.

## Behavior

- On every real state change of the source entity (ignoring
  `unknown`/`unavailable` and consecutive equal states), the occupancy
  sensor turns **on**.
- After `occupancy_timeout` seconds, it auto-resets to **off**.
- A new source event while the sensor is still on restarts the reset
  timer (sliding window).
- The occupancy sensor is attached to the same HA device as the source
  entity, so it shows up on that device's card.
```

- [ ] **Step 4: Update `doc/deadline_logic.md`**

Open `doc/deadline_logic.md`. If it mentions occupancy sensors, re-word
those sections to say that occupancy is handled by the separate
`door_occupancy` integration and link to its README. If the file is purely
about the deadline state machine, no edits are needed.

- [ ] **Step 5: Commit**

```bash
git add README.md \
        custom_components/auto_off/README.md \
        custom_components/door_occupancy/README.md
# Only add doc/deadline_logic.md if it was modified.
git add doc/deadline_logic.md 2>/dev/null || true
git commit -m "docs: document the split, per-package READMEs, migration notes

Top-level README describes both integrations and walks through the
breaking migration. Each package has a local README with setup,
services, entities, and behavior."
```

---

### Task 16: Final verification

**Files:** none modified; verification only.

- [ ] **Step 1: Run the full unit suite with coverage**

Run: `pytest custom_components/ -v -m "not docker_e2e"`

Expected: all unit tests pass. Note the count for the commit message.

- [ ] **Step 2: Run the dockerized unit runner if available**

Run: `./ha-test-kit/run_unit.sh`

Expected: same unit-test outcome as Step 1, but executed inside the
project's canonical container.

- [ ] **Step 3: Run the e2e suite if the environment supports it**

Run: `./ha-test-kit/run_e2e.sh`

Expected: auto_off e2e scenarios pass with the new structured payload;
door_occupancy smoke scenarios (if created in Task 13) pass.

- [ ] **Step 4: Import check for both integrations**

Run:

```bash
python -c "import custom_components.auto_off"
python -c "import custom_components.door_occupancy"
```

Both commands must exit 0 with no output.

- [ ] **Step 5: Sanity check via `git grep`**

Run these and confirm the results are empty (no stale references remain):

```bash
git grep -n "CONF_CONFIG" custom_components/
git grep -n "GroupConfigSensorEntity" custom_components/
git grep -n "DoorOccupancyManager" custom_components/auto_off/
git grep -n "door_occupancy" custom_components/auto_off/     # only expected in docs
git grep -n "from .door_occupancy" custom_components/
git grep -n "yaml.safe_load" custom_components/
```

Expected: every command returns no output (or, for the fourth, only
comment/docstring matches — review manually). If any command returns real
code references, fix them before moving on.

- [ ] **Step 6: Commit any final cleanups**

If Step 5 surfaced stale references, fix them, run the unit suite again,
and commit with a message like:

```bash
git commit -m "chore: remove stale references after integration split"
```

If Step 5 was clean, there is no commit to make. This task does not
produce a commit in that case.

---

## Execution notes

- Each task ends with a commit; after each commit the working tree should
  compile and the unit suite should pass. If a commit would leave the tree
  broken, the task decomposition is wrong — flag it rather than committing.
- Tasks 1–6 build `door_occupancy` independently of `auto_off`; there is
  no interaction with the live `auto_off` code until Task 7.
- Tasks 7–12 rework `auto_off` in small, self-contained slices: first
  remove door_occupancy code, then reduce platforms and drop the
  binary_sensor indirection, then introduce the new sensor/template split,
  then the new service shape, then the migration fence, then the
  GroupConfigSensorEntity removal.
- Tasks 13–15 update e2e scenarios, HACS packaging, and documentation.
- Task 16 is a verification-only pass.

If running this plan in a subagent-driven flow, dispatch one subagent per
task. Each task is self-contained: the subagent has everything it needs
(test code, implementation code, commands, expected output) without
having to reread the spec or earlier tasks.
