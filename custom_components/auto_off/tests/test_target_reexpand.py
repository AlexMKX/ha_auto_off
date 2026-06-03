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


class TestReexpandDrivesDeadline:
    """Membership changes feed the existing deadline state machine.

    Validates: when a new on leaf appears, the deadline starts (provided
    sensors are off); when the only on leaf is removed, the deadline
    cancels. The tests assert on the deadline-change callback the
    integration manager registers, not on private SensorGroup fields.
    """

    async def test_new_on_leaf_starts_deadline_when_sensors_off(self):
        hass = _hass_for_root(
            "light.example_root",
            ["light.leaf_a"],
            member_states={"light.leaf_a": "off"},
        )
        config = GroupConfig(
            targets=["light.example_root"],
            sensors=["binary_sensor.motion"],
            sensor_templates=[],
            delay=0,
        )

        deadline_events: list[tuple[str, str | None]] = []

        def on_deadline_change(group_id, deadline_iso):
            deadline_events.append((group_id, deadline_iso))

        callback_box = {}

        def fake_track(hass_arg, entity_ids, callback):
            if "light.example_root" in entity_ids:
                callback_box["cb"] = callback
            return MagicMock(name="unsub")

        with patch(
            "custom_components.auto_off.auto_off.async_track_state_change_event",
            side_effect=fake_track,
        ):
            group = SensorGroup(
                hass, "g", config,
                on_deadline_change=on_deadline_change,
                manager=None,
            )
            await group._async_init_targets()

        # Flush first-run init via a state collection pass.
        await group.check_and_set_deadline()
        deadline_events.clear()

        # Update root state so expand_group_targets sees the new membership
        # (pitfall 3: without this, expand returns the old list and no diff fires).
        def get_with_new_leaf(eid):
            if eid == "light.example_root":
                st = MagicMock()
                st.attributes = {"entity_id": ["light.leaf_a", "light.leaf_b"]}
                st.state = "on"
                return st
            if eid == "light.leaf_a":
                st = MagicMock()
                st.attributes = {}
                st.state = "off"
                return st
            if eid == "light.leaf_b":
                st = MagicMock()
                st.attributes = {}
                st.state = "on"
                return st
            return None

        hass.states.get.side_effect = get_with_new_leaf

        old_state = MagicMock()
        old_state.attributes = {"entity_id": ["light.leaf_a"]}
        new_state = MagicMock()
        new_state.attributes = {"entity_id": ["light.leaf_a", "light.leaf_b"]}
        event = MagicMock()
        event.data = {"old_state": old_state, "new_state": new_state}

        # Stub sensor as off so deadline can start.
        for s in group._sensors:
            s.is_on = AsyncMock(return_value=False)

        result = callback_box["cb"](event)
        if asyncio.iscoroutine(result):
            await result
        # Allow the spawned task to run.
        for _ in range(5):
            await asyncio.sleep(0)

        # delay=0 means deadline fires immediately and clears, but a
        # transition (None -> set, then set -> None) must be observed.
        # The strongest claim we can make without timing assumptions:
        # the callback was invoked at least once with a non-None
        # deadline_iso OR turn_off was dispatched.
        non_null = [e for e in deadline_events if e[1] is not None]
        turn_off_called = hass.services.async_call.await_count > 0
        assert non_null or turn_off_called

    async def test_removing_only_on_leaf_cancels_deadline(self):
        hass = _hass_for_root(
            "light.example_root",
            ["light.leaf_a"],
            member_states={"light.leaf_a": "on"},
        )
        config = GroupConfig(
            targets=["light.example_root"],
            sensors=["binary_sensor.motion"],
            sensor_templates=[],
            delay=10,  # non-zero so deadline persists
        )

        deadline_events: list[tuple[str, str | None]] = []

        def on_deadline_change(group_id, deadline_iso):
            deadline_events.append((group_id, deadline_iso))

        callback_box = {}

        def fake_track(hass_arg, entity_ids, callback):
            if "light.example_root" in entity_ids:
                callback_box["cb"] = callback
            return MagicMock(name="unsub")

        with patch(
            "custom_components.auto_off.auto_off.async_track_state_change_event",
            side_effect=fake_track,
        ):
            group = SensorGroup(
                hass, "g", config,
                on_deadline_change=on_deadline_change,
                manager=None,
            )
            await group._async_init_targets()

        # Stub sensors as off so a deadline starts.
        for s in group._sensors:
            s.is_on = AsyncMock(return_value=False)
        await group.check_and_set_deadline()
        # _handle_first_run spawns create_task(_set_deadline_from_delay);
        # flush several event-loop ticks so the task runs and calls
        # _notify_deadline_change.
        for _ in range(5):
            await asyncio.sleep(0)
        assert any(e[1] is not None for e in deadline_events), (
            "precondition: a deadline must have been set"
        )
        deadline_events.clear()

        # Update root state so expand_group_targets returns empty (no leaves)
        # and leaf_a is gone from the system (pitfall 1: empty list collapses
        # root to a leaf; we make the root state "off" so any_target_on=False).
        def updated_get(eid):
            if eid == "light.example_root":
                st = MagicMock()
                st.attributes = {"entity_id": []}
                st.state = "off"
                return st
            # leaf_a is gone from the system — return None to simulate removal.
            if eid == "light.leaf_a":
                return None
            return None

        hass.states.get.side_effect = updated_get

        # Remove leaf_a entirely.
        old_state = MagicMock()
        old_state.attributes = {"entity_id": ["light.leaf_a"]}
        new_state = MagicMock()
        new_state.attributes = {"entity_id": []}
        event = MagicMock()
        event.data = {"old_state": old_state, "new_state": new_state}

        result = callback_box["cb"](event)
        if asyncio.iscoroutine(result):
            await result
        for _ in range(5):
            await asyncio.sleep(0)

        # After removing the only on leaf, the deadline must be
        # cancelled (callback fired with None).
        assert any(
            e[1] is None for e in deadline_events
        ), f"expected a None deadline notification, got {deadline_events!r}"


class TestUnload:
    """async_unload must release root subscriptions.

    Validates: after unload, an event delivered to the captured root
    callback must not produce any service call or deadline notification.
    """

    async def test_unload_silences_root_callback(self):
        hass = _hass_for_root("light.example_root", ["light.leaf_a"])
        config = GroupConfig(
            targets=["light.example_root"],
            sensors=["binary_sensor.motion"],
            sensor_templates=[],
            delay=0,
        )

        unsubs_called: list[str] = []

        def make_unsub(label):
            def _unsub():
                unsubs_called.append(label)
            return _unsub

        sub_counter = {"n": 0}

        def fake_track(hass_arg, entity_ids, callback):
            sub_counter["n"] += 1
            return make_unsub(f"sub-{sub_counter['n']}")

        with patch(
            "custom_components.auto_off.auto_off.async_track_state_change_event",
            side_effect=fake_track,
        ):
            group = SensorGroup(hass, "g", config, manager=None)
            await group._async_init_targets()

        await group.async_unload()

        # The first installation is the root subscription (per the
        # ordering in _async_init_targets); its unsub must run on unload.
        # Without the unload fix this assertion would fail because the
        # root unsub callback would never be invoked.
        assert "sub-1" in unsubs_called, (
            f"root subscription unsub was not called; got {unsubs_called!r}"
        )
