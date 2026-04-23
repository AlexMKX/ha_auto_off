"""Smoke test that SensorGroup construction and state logging don't
reference attributes removed from Target."""

from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.auto_off.auto_off import GroupConfig, SensorGroup


def make_hass():
    hass = MagicMock()
    hass.states.get = MagicMock(return_value=None)
    hass.loop = MagicMock()
    hass.loop.time = MagicMock(return_value=1000.0)
    return hass


async def test_sensor_group_state_logging_no_attribute_error():
    sg_hass = make_hass()
    cfg = GroupConfig(
        targets=["light.kitchen"],
        sensors=["binary_sensor.motion"],
    )
    sg = SensorGroup(sg_hass, "g1", cfg, on_deadline_change=None)
    # Directly call the transition logger; should not raise AttributeError.
    await sg._log_state_transitions({"target_on": False, "all_sensors_off": True, "human_deadline": "None"})
