"""Log-level selection for ``_check_entity_state`` when entity is absent.

During HA startup Magic Areas (and other integrations) register their
entities late. ``Sensor._check_entity_state`` is called every time the
``periodic_worker`` ticks, every time ``check_and_set_deadline`` runs,
and inside the new ensure-off loop. While entities are missing this can
produce a flood of identical log lines.

Policy: while ``hass.state == CoreState.starting`` the missing-entity
message is INFO; once HA has finished loading (any other state, in
practice ``running``) the same message becomes WARNING so prolonged
absence stays visible.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import CoreState

from custom_components.auto_off.auto_off import Sensor


def _make_hass(core_state: CoreState):
    hass = MagicMock()
    hass.state = core_state
    hass.states = MagicMock()
    # Force "entity does not exist" path.
    hass.states.get = MagicMock(return_value=None)
    return hass


class TestMissingEntityLogLevel:
    async def test_info_while_starting(self, caplog):
        """While HA is still starting, the missing-entity log is INFO."""
        hass = _make_hass(CoreState.starting)
        sensor = Sensor(
            hass,
            "binary_sensor.late",
            kind="entity",
            on_state_change_callback=AsyncMock(),
        )

        caplog.set_level(logging.INFO, logger="custom_components.auto_off.auto_off")
        await sensor._check_entity_state()

        matching = [
            r for r in caplog.records
            if "binary_sensor.late" in r.message and "state not found" in r.message
        ]
        assert len(matching) == 1, f"expected exactly one missing-entity log, got {matching}"
        assert matching[0].levelno == logging.INFO

    async def test_warning_once_running(self, caplog):
        """After HA has reached the running state, the same log is WARNING."""
        hass = _make_hass(CoreState.running)
        sensor = Sensor(
            hass,
            "binary_sensor.late",
            kind="entity",
            on_state_change_callback=AsyncMock(),
        )

        caplog.set_level(logging.INFO, logger="custom_components.auto_off.auto_off")
        await sensor._check_entity_state()

        matching = [
            r for r in caplog.records
            if "binary_sensor.late" in r.message and "state not found" in r.message
        ]
        assert len(matching) == 1
        assert matching[0].levelno == logging.WARNING

    async def test_warning_for_stopping_phase(self, caplog):
        """Non-starting phases (e.g. stopping) also use WARNING."""
        hass = _make_hass(CoreState.stopping)
        sensor = Sensor(
            hass,
            "binary_sensor.late",
            kind="entity",
            on_state_change_callback=AsyncMock(),
        )

        caplog.set_level(logging.INFO, logger="custom_components.auto_off.auto_off")
        await sensor._check_entity_state()

        matching = [
            r for r in caplog.records
            if "binary_sensor.late" in r.message and "state not found" in r.message
        ]
        assert len(matching) == 1
        assert matching[0].levelno == logging.WARNING
