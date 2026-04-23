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
    """Target exposes the entity_id it was built with as a public attribute.

    All other init-time behavior (accept vs. skip) is observed through
    `turn_off` in `TestTargetTurnOff` below.
    """

    def test_stores_entity_id_as_public_attribute(self, target_hass):
        t = Target(target_hass, "light.kitchen", AsyncMock())
        assert t.entity_id == "light.kitchen"

    def test_stores_invalid_entity_id_verbatim_on_public_attribute(self, target_hass):
        # Invalid ids must survive on the entity for UI/diagnostics.
        t = Target(target_hass, "not-valid", AsyncMock())
        assert t.entity_id == "not-valid"


class TestTargetTurnOff:
    """`turn_off` is the only behavior users / callers observe."""

    async def test_turn_off_does_not_call_service_when_entity_id_is_invalid(self, target_hass):
        t = Target(target_hass, "invalid id", AsyncMock())
        await t.turn_off()
        target_hass.services.async_call.assert_not_called()

    async def test_turn_off_does_not_call_service_for_template_string(self, target_hass):
        # Targets never accept Jinja; a template string is treated as invalid.
        t = Target(target_hass, "{{ states('light.x') }}", AsyncMock())
        await t.turn_off()
        target_hass.services.async_call.assert_not_called()

    async def test_turn_off_warns_and_skips_when_entity_state_missing(self, target_hass, caplog):
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
