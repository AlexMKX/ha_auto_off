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
