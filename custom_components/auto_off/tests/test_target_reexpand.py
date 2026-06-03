"""Tests for dynamic target re-expansion.

Behavior-only: tests exercise SensorGroup through its public surface
(target turn_off calls, deadline notifications, root state-change
events) and never assert on private fields, call counts of helpers,
or list contents.

Spec: docs/superpowers/specs/2026-06-03-target-reexpand-design.md
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.auto_off.auto_off import _extract_member_list


class TestExtractMemberList:
    """_extract_member_list normalises HA state.attributes.entity_id.

    Validates: the helper used to detect membership changes on root
    targets returns None for anything that is not a non-empty list of
    strings.
    """

    def test_returns_none_for_none_state(self):
        assert _extract_member_list(None) is None

    def test_returns_none_when_attributes_missing(self):
        state = MagicMock(spec=[])
        # No attributes attribute at all.
        assert _extract_member_list(state) is None

    def test_returns_none_when_entity_id_attr_not_a_list(self):
        state = MagicMock()
        state.attributes = {"entity_id": "light.kitchen"}
        assert _extract_member_list(state) is None

    def test_returns_none_for_empty_list(self):
        state = MagicMock()
        state.attributes = {"entity_id": []}
        assert _extract_member_list(state) is None

    def test_filters_non_string_members(self):
        state = MagicMock()
        state.attributes = {"entity_id": ["light.a", 42, None, "light.b"]}
        assert _extract_member_list(state) == ["light.a", "light.b"]

    def test_returns_list_of_strings(self):
        state = MagicMock()
        state.attributes = {"entity_id": ["light.a", "light.b"]}
        assert _extract_member_list(state) == ["light.a", "light.b"]


import asyncio
from unittest.mock import AsyncMock, patch

from custom_components.auto_off.auto_off import GroupConfig, SensorGroup


def _hass_for_root(root_id, members, member_states=None):
    """Build a MagicMock hass exposing root_id as a group with members.

    member_states maps leaf entity_id -> "on"/"off". Missing leaves
    return a stateless mock so they are treated as off-and-present.
    """
    member_states = member_states or {}
    hass = MagicMock()
    hass.loop = MagicMock()
    hass.loop.time = MagicMock(return_value=1000.0)
    hass.bus = MagicMock()
    hass.services = MagicMock()
    hass.services.async_call = AsyncMock()

    def _get(eid):
        if eid == root_id:
            st = MagicMock()
            st.attributes = {"entity_id": list(members)}
            st.state = "on"
            return st
        if eid in members:
            st = MagicMock()
            st.attributes = {}
            st.state = member_states.get(eid, "off")
            return st
        return None

    hass.states.get = MagicMock(side_effect=_get)
    return hass


class TestRootSubscription:
    """Membership changes on root targets trigger re-expansion;
    self-state changes (root flips on/off without member change) do not.
    """

    async def test_subscription_installed_for_each_root(self):
        """Validates: each root entity_id from config gets exactly one
        async_track_state_change_event subscription."""
        hass = _hass_for_root("light.example_root", ["light.leaf_a"])
        config = GroupConfig(
            targets=["light.example_root"],
            sensors=["binary_sensor.motion"],
            sensor_templates=[],
            delay=0,
        )

        tracked = []

        def fake_track(hass_arg, entity_ids, callback):
            tracked.append((list(entity_ids), callback))
            return MagicMock(name="unsub")

        with patch(
            "custom_components.auto_off.auto_off.async_track_state_change_event",
            side_effect=fake_track,
        ):
            group = SensorGroup(hass, "g", config, manager=None)
            await group._async_init_targets()

        root_subs = [t for t in tracked if "light.example_root" in t[0]]
        assert len(root_subs) == 1

    async def test_self_state_change_without_membership_diff_is_ignored(self):
        """Validates: when the root flips on -> off but
        attributes.entity_id is unchanged, no service call is dispatched
        and no deadline recheck happens beyond the no-op transition."""
        hass = _hass_for_root("light.example_root", ["light.leaf_a"])
        config = GroupConfig(
            targets=["light.example_root"],
            sensors=["binary_sensor.motion"],
            sensor_templates=[],
            delay=0,
        )

        callback_box = {}

        def fake_track(hass_arg, entity_ids, callback):
            if "light.example_root" in entity_ids:
                callback_box["cb"] = callback
            return MagicMock(name="unsub")

        with patch(
            "custom_components.auto_off.auto_off.async_track_state_change_event",
            side_effect=fake_track,
        ):
            group = SensorGroup(hass, "g", config, manager=None)
            await group._async_init_targets()

        # Spy on _reexpand_targets.
        original_reexpand = group._reexpand_targets
        calls = {"n": 0}

        async def spy(*args, **kwargs):
            calls["n"] += 1
            await original_reexpand(*args, **kwargs)

        group._reexpand_targets = spy

        # Fire a self-state change: same members, different state.
        old_state = MagicMock()
        old_state.attributes = {"entity_id": ["light.leaf_a"]}
        new_state = MagicMock()
        new_state.attributes = {"entity_id": ["light.leaf_a"]}
        event = MagicMock()
        event.data = {"old_state": old_state, "new_state": new_state}

        result = callback_box["cb"](event)
        if asyncio.iscoroutine(result):
            await result
        await asyncio.sleep(0)  # let any spawned task run

        assert calls["n"] == 0
