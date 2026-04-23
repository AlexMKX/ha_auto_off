"""Tests for auto_off.SensorGroup._turn_off_targets routing."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from custom_components.auto_off.auto_off import GroupConfig, SensorGroup


class TestTurnOffRoutingThroughGroups:
    async def test_turn_off_dispatches_group_service_per_domain(self, hass):
        hass.services.async_call = AsyncMock()

        manager = MagicMock()
        manager.get_group_member_group_entity_ids.return_value = [
            "light.auto_off_k_targets_light",
            "switch.auto_off_k_targets_switch",
        ]

        config = GroupConfig(
            targets=["light.a", "switch.b"],
            sensors=["binary_sensor.m"],
            sensor_templates=[],
            delay=0,
        )
        group = SensorGroup(hass, "k", config, manager=manager)

        await group._turn_off_targets()

        # Two calls, one per domain, with the group entity id
        calls = hass.services.async_call.await_args_list
        assert len(calls) == 2
        domains = {c.args[0] for c in calls}
        assert domains == {"light", "switch"}
        for c in calls:
            assert c.args[1] == "turn_off"
            assert c.args[2]["entity_id"].startswith(c.args[0] + ".auto_off_k_targets_")

    async def test_turn_off_falls_back_for_non_groupable_target(self, hass):
        hass.services.async_call = AsyncMock()

        manager = MagicMock()
        # No groupable targets → empty list
        manager.get_group_member_group_entity_ids.return_value = []

        config = GroupConfig(
            targets=["scene.evening"],
            sensors=["binary_sensor.m"],
            sensor_templates=[],
            delay=0,
        )
        group = SensorGroup(hass, "k", config, manager=manager)

        # Replace Target.turn_off with a spy
        target_spy = AsyncMock()
        for target in group._targets:
            target.turn_off = target_spy

        await group._turn_off_targets()

        # No group service call was dispatched
        assert hass.services.async_call.await_count == 0
        # The per-target fallback was invoked
        assert target_spy.await_count == 1
