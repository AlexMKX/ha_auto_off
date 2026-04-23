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
