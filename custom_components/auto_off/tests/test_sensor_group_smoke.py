"""Smoke test that SensorGroup construction and state logging don't
reference attributes removed from Target."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

from custom_components.auto_off.auto_off import GroupConfig, SensorGroup


def make_hass():
    hass = MagicMock()
    hass.states.get = MagicMock(return_value=None)
    hass.loop = MagicMock()
    hass.loop.time = MagicMock(return_value=1000.0)
    return hass


async def test_sensor_group_state_logging_no_attribute_error(caplog):
    sg_hass = make_hass()
    cfg = GroupConfig(
        targets=["light.kitchen"],
        sensors=["binary_sensor.motion"],
    )
    sg = SensorGroup(sg_hass, "g1", cfg, on_deadline_change=None)
    with caplog.at_level(logging.DEBUG, logger="custom_components.auto_off.auto_off"):
        await sg._log_state_transitions(
            {"target_on": False, "all_sensors_off": True, "human_deadline": "None"}
        )
    assert any("g1" in r.message for r in caplog.records), (
        "Expected debug log containing group_id 'g1'"
    )
