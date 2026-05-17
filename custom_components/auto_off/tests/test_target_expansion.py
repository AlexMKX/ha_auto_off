"""Tests that ``SensorGroup`` expands group-like targets at init time.

When a stored ``GroupConfig.targets`` entry is a group entity (HA
group helper, ``light.*`` group, Magic Areas light group, anything
with an ``entity_id`` attribute list), ``SensorGroup`` must build its
internal ``self._targets`` from the **leaves** instead of the group
itself. This way the ensure-off retry loop iterates real end devices
and can tell precisely which leaves did not switch.

The original raw config is preserved on ``self._config.targets`` so
``dump_group`` round-trips the user's intent rather than the expanded
form.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.auto_off.auto_off import GroupConfig, SensorGroup


def _hass_with_group(group_id: str, members: list[str]) -> MagicMock:
    """Minimal hass mock where ``group_id`` exposes ``members`` and the
    members themselves are leaves."""
    hass = MagicMock()
    hass.loop = MagicMock()
    hass.loop.time = MagicMock(return_value=1000.0)
    hass.bus = MagicMock()

    def _get(eid):
        if eid == group_id:
            st = MagicMock()
            st.attributes = {"entity_id": list(members)}
            return st
        # leaf entity_ids - return a state without entity_id attribute.
        if eid in members:
            st = MagicMock()
            st.attributes = {}
            return st
        return None

    hass.states.get = MagicMock(side_effect=_get)
    return hass


class TestTargetExpansionAtInit:
    async def test_group_target_expanded_to_leaves(self):
        hass = _hass_with_group(
            "light.showerroom_all",
            ["light.showerroom_main", "light.showerroom_mirror"],
        )
        config = GroupConfig(
            targets=["light.showerroom_all"],
            sensors=["binary_sensor.shower_motion"],
            sensor_templates=[],
            delay=5,
        )
        group = SensorGroup(hass, "shower", config, manager=None)

        target_eids = [t.entity_id for t in group._targets]
        assert target_eids == [
            "light.showerroom_main",
            "light.showerroom_mirror",
        ]

    async def test_raw_config_targets_preserved_for_round_trip(self):
        """The expansion is internal; ``self._config.targets`` keeps the
        original list so ``dump_group`` reports the user's intent."""
        hass = _hass_with_group(
            "light.showerroom_all",
            ["light.showerroom_main", "light.showerroom_mirror"],
        )
        config = GroupConfig(
            targets=["light.showerroom_all"],
            sensors=["binary_sensor.shower_motion"],
            sensor_templates=[],
            delay=5,
        )
        group = SensorGroup(hass, "shower", config, manager=None)

        assert group._config.targets == ["light.showerroom_all"]

    async def test_plain_leaf_target_unchanged(self):
        """Non-group targets pass through unchanged."""
        hass = _hass_with_group("light.kitchen", [])  # leaf
        config = GroupConfig(
            targets=["light.kitchen"],
            sensors=["binary_sensor.motion"],
            sensor_templates=[],
            delay=5,
        )
        group = SensorGroup(hass, "k", config, manager=None)

        target_eids = [t.entity_id for t in group._targets]
        assert target_eids == ["light.kitchen"]

    async def test_missing_group_target_kept_as_leaf(self):
        """A target that doesn't exist in hass.states yet (late-loaded
        integration) stays as a single leaf - auto_off will drive it
        directly once it appears, no expansion possible until then."""
        hass = _hass_with_group("light.late", [])
        # remove "light.late" from states map entirely
        hass.states.get = MagicMock(return_value=None)
        config = GroupConfig(
            targets=["light.late"],
            sensors=["binary_sensor.motion"],
            sensor_templates=[],
            delay=5,
        )
        group = SensorGroup(hass, "g", config, manager=None)

        target_eids = [t.entity_id for t in group._targets]
        assert target_eids == ["light.late"]
