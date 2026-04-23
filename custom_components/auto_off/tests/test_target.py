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

    def test_marks_invalid_entity_id_as_skip(self, target_hass):
        t = Target(target_hass, "not-valid", AsyncMock())
        assert t.entity_id == "not-valid"
        assert t._skip is True

    def test_marks_template_string_as_skip(self, target_hass):
        t = Target(target_hass, "{{ states('light.x') }}", AsyncMock())
        assert t._skip is True


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
