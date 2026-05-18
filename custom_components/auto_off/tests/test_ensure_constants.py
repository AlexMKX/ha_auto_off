"""Tests pinning that ensure-off retry timings are module-level constants.

YAGNI / global-rules require: do not add configuration fields without a
proven need. The ensure-off retry loop ran for several hundred turn-off
cycles in production with a single fixed value; per-group tuning never
came up. Constants stay on the module so they can be tightened later
without rewriting the public service schema.
"""

from __future__ import annotations

import pytest

from custom_components.auto_off import auto_off as auto_off_module
from custom_components.auto_off.auto_off import GroupConfig


class TestEnsureConstants:
    def test_window_and_interval_are_module_constants(self):
        assert auto_off_module.ENSURE_WINDOW_SEC == 60
        assert auto_off_module.ENSURE_INTERVAL_SEC == 10

    def test_group_config_rejects_ensure_window_field(self):
        """``ensure_window`` is not a per-group setting."""
        with pytest.raises(Exception):
            GroupConfig(
                targets=["light.x"],
                sensors=["binary_sensor.y"],
                sensor_templates=[],
                delay=5,
                ensure_window=90,
            )

    def test_group_config_rejects_ensure_interval_field(self):
        """``ensure_interval`` is not a per-group setting either."""
        with pytest.raises(Exception):
            GroupConfig(
                targets=["light.x"],
                sensors=["binary_sensor.y"],
                sensor_templates=[],
                delay=5,
                ensure_interval=15,
            )
