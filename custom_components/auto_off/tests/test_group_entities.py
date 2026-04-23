"""Unit tests for group_entities helpers."""

from __future__ import annotations

from custom_components.auto_off.group_entities import (
    sensors_group_entity_id,
    split_targets_by_domain,
    targets_group_entity_id,
)


class TestSplitTargetsByDomain:
    def test_mixed_domains_are_bucketed(self):
        result = split_targets_by_domain(
            ["light.kitchen", "switch.fan", "light.hallway"]
        )
        assert result == {
            "light": ["light.kitchen", "light.hallway"],
            "switch": ["switch.fan"],
        }

    def test_non_groupable_domain_is_skipped(self):
        result = split_targets_by_domain(["scene.evening", "light.kitchen"])
        assert result == {"light": ["light.kitchen"]}

    def test_invalid_entity_id_is_skipped(self):
        result = split_targets_by_domain(["not-an-id", "light.kitchen"])
        assert result == {"light": ["light.kitchen"]}

    def test_empty_input(self):
        assert split_targets_by_domain([]) == {}


class TestEntityIdHelpers:
    def test_targets_group_entity_id(self):
        assert (
            targets_group_entity_id("light", "kitchen_auto_off")
            == "light.auto_off_kitchen_auto_off_targets_light"
        )

    def test_sensors_group_entity_id(self):
        assert (
            sensors_group_entity_id("kitchen_auto_off")
            == "binary_sensor.auto_off_kitchen_auto_off_sensors"
        )
