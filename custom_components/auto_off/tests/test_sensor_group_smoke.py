"""Smoke test that SensorGroup construction and check_and_set_deadline work
without errors even when entities are absent from the state machine."""

from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.auto_off.auto_off import GroupConfig, SensorGroup


async def test_sensor_group_state_logging_no_attribute_error(caplog):
    sg_hass = MagicMock()
    sg_hass.loop = MagicMock()
    sg_hass.loop.time = MagicMock(return_value=1000.0)
    sg_hass.bus = MagicMock()
    sg_hass.states = MagicMock()
    sg_hass.states.get = MagicMock(return_value=None)
    cfg = GroupConfig(
        targets=["light.x"],
        sensors=["binary_sensor.m"],
        sensor_templates=[],
        delay=2,
    )
    sg = SensorGroup(sg_hass, "g1", cfg, on_deadline_change=None, poll_interval=15)
    # check_and_set_deadline must run without raising on a group whose
    # entities are absent from the state machine.
    await sg.check_and_set_deadline()
