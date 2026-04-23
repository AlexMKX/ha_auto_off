"""Tests for the GroupConfig pydantic model."""

from __future__ import annotations

import logging

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


class TestGroupConfigTargetSyntax:
    def test_warns_but_keeps_invalid_target_syntax(self, caplog):
        caplog.set_level(logging.WARNING, logger="custom_components.auto_off.auto_off")
        cfg = GroupConfig(
            targets=["light.good", "not-an-entity-id"],
            sensors=["binary_sensor.motion"],
        )
        assert cfg.targets == ["light.good", "not-an-entity-id"]
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("not-an-entity-id" in r.message for r in warnings)

    def test_warns_on_template_string_in_targets(self, caplog):
        caplog.set_level(logging.WARNING, logger="custom_components.auto_off.auto_off")
        template = "{{ states('light.x') }}"
        cfg = GroupConfig(
            targets=[template],
            sensors=["binary_sensor.motion"],
        )
        assert cfg.targets == [template]
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any(template in r.message for r in warnings)

    def test_no_warning_for_all_valid_targets(self, caplog):
        caplog.set_level(logging.WARNING, logger="custom_components.auto_off.auto_off")
        GroupConfig(
            targets=["light.a", "switch.b"],
            sensors=["binary_sensor.motion"],
        )
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warnings == []
