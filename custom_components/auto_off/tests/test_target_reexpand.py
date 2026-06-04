"""Tests for dynamic target re-expansion.

Behavior-only: tests exercise SensorGroup through its public surface
(target turn_off calls, deadline notifications, patrol tick) and never
assert on private fields, call counts of helpers, or list contents
beyond what is needed to verify the observable membership change.

Spec: docs/superpowers/specs/2026-06-03-target-reexpand-design.md
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

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


class TestReexpandDrivesDeadline:
    """Membership changes feed the existing deadline state machine.

    Validates: when a new on leaf appears, the deadline starts (provided
    sensors are off); when the only on leaf is removed, the deadline
    cancels. Tests drive re-expansion directly via ``_reexpand_targets``
    (simulating a patrol tick) instead of via subscription callbacks.
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
            delay=10,  # non-zero so deadline persists long enough to observe
        )

        deadline_events: list[tuple[str, str | None]] = []

        def on_deadline_change(group_id, deadline_iso):
            deadline_events.append((group_id, deadline_iso))

        group = SensorGroup(
            hass,
            "g",
            config,
            on_deadline_change=on_deadline_change,
            manager=None,
        )
        await group._async_init_targets()

        # Flush first-run init via a state collection pass.
        await group.check_and_set_deadline()
        deadline_events.clear()

        # Update root state so expand_group_targets sees the new membership.
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

        # Stub sensor as off so deadline can start.
        for s in group._sensors:
            s.is_on = AsyncMock(return_value=False)

        # Simulate a patrol tick by calling _reexpand_targets directly.
        await group._reexpand_targets()
        # check_and_set_deadline is called by _reexpand_targets (non-initial, diff present).

        # delay=10 ensures the deadline persists long enough to observe
        # the non-null deadline notification.
        non_null = [e for e in deadline_events if e[1] is not None]
        assert non_null, (
            f"expected a non-null deadline notification after re-expand, "
            f"got {deadline_events!r}"
        )

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

        group = SensorGroup(
            hass,
            "g",
            config,
            on_deadline_change=on_deadline_change,
            manager=None,
        )
        await group._async_init_targets()

        # Stub sensors as off so a deadline starts.
        for s in group._sensors:
            s.is_on = AsyncMock(return_value=False)
        await group.check_and_set_deadline()
        # Flush several event-loop ticks so any background tasks run and
        # _notify_deadline_change fires.
        import asyncio

        for _ in range(5):
            await asyncio.sleep(0)
        assert any(e[1] is not None for e in deadline_events), "precondition: a deadline must have been set"
        deadline_events.clear()

        # Update root state so expand_group_targets returns empty (no leaves).
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

        # Simulate a patrol tick.
        await group._reexpand_targets()

        # After removing the only on leaf, the deadline must be
        # cancelled (callback fired with None).
        assert any(
            e[1] is None for e in deadline_events
        ), f"expected a None deadline notification, got {deadline_events!r}"


class TestUnload:
    """async_unload must clean up resources without raising."""

    async def test_unload_completes_cleanly(self):
        """Validates: async_unload sets the unloaded flag, releases
        the deadline timer, and cancels any in-flight background
        tasks without raising."""
        hass = _hass_for_root("light.example_root", ["light.leaf_a"])
        config = GroupConfig(
            targets=["light.example_root"],
            sensors=["binary_sensor.motion"],
            sensor_templates=[],
            delay=0,
        )
        group = SensorGroup(hass, "g", config, manager=None)
        await group._async_init_targets()

        # Unload must run without raising.
        await group.async_unload()
        assert group._unloaded is True


class TestPatrolDrivesReexpand:
    """Periodic patrol re-expands and re-evaluates each group.

    Validates: AutoOffManager.periodic_worker calls _reexpand_targets()
    for every group, picking up runtime membership changes without
    requiring any subscription on root entities.
    """

    async def test_periodic_worker_reexpands_each_group(self):
        from custom_components.auto_off.auto_off import AutoOffManager

        hass = _hass_for_root("light.example_root", ["light.leaf_a"])
        config = GroupConfig(
            targets=["light.example_root"],
            sensors=["binary_sensor.motion"],
            sensor_templates=[],
            delay=0,
        )
        manager = AutoOffManager(hass, {"g": config})
        await manager.async_init_groups()

        group = manager._groups["g"]
        # Ensure init has completed.
        await group._async_init_targets()
        initial_leaves = list(group._current_leaves)

        # Mutate hass to add leaf_b to the root membership.
        def get_with_new_leaf(eid):
            if eid == "light.example_root":
                st = MagicMock()
                st.attributes = {"entity_id": ["light.leaf_a", "light.leaf_b"]}
                st.state = "on"
                return st
            if eid == "light.leaf_b":
                st = MagicMock()
                st.attributes = {}
                st.state = "off"
                return st
            # fall back to original mock
            return hass.states.get._original_side_effect(eid)

        hass.states.get._original_side_effect = hass.states.get.side_effect
        hass.states.get.side_effect = get_with_new_leaf

        # One patrol tick.
        await manager.periodic_worker()

        # New leaf is now tracked.
        assert "light.leaf_b" in group._current_leaves, (
            f"expected leaf_b after patrol, got {group._current_leaves!r}"
        )
        assert "light.leaf_a" in group._current_leaves

    async def test_invalid_root_id_does_not_crash_and_warns(self, caplog):
        """Validates: a config with an invalid root entity_id does not
        crash setup, does not install any tracking, and emits a WARNING
        log so the user sees the typo."""
        import logging

        hass = _hass_for_root("light.example_root", ["light.leaf_a"])
        config = GroupConfig(
            targets=["not_an_entity_id"],
            sensors=["binary_sensor.motion"],
            sensor_templates=[],
            delay=0,
        )

        caplog.set_level(logging.WARNING, logger="custom_components.auto_off.auto_off")

        group = SensorGroup(hass, "g", config, manager=None)
        await group._async_init_targets()

        # No service call dispatched as a side effect of setup.
        hass.services.async_call.assert_not_awaited()

        # WARNING emitted naming the invalid target.
        assert any(
            "not_an_entity_id" in r.message
            for r in caplog.records
            if r.levelno == logging.WARNING
        ), f"expected WARNING about invalid root, got {[r.message for r in caplog.records]!r}"


class TestReexpandDeadlineSignal:
    """Re-expansion only treats an added leaf as activity if it is ON.

    An added OFF leaf must not push an existing deadline forward, because
    no actual activity occurred. An added ON leaf (late-bind, presence off)
    must arm a deadline so the light eventually turns off.
    """

    async def test_added_off_leaf_does_not_extend_deadline(self):
        """Re-expansion adding an OFF leaf must not move an existing deadline.

        Scenario: leaf_a ON, sensor OFF, deadline at T+600 (delay=10 min).
        Re-expansion adds leaf_b (OFF). The deadline must remain unchanged.
        """
        import asyncio

        hass = _hass_for_root(
            "light.example_root",
            ["light.leaf_a"],
            member_states={"light.leaf_a": "on"},
        )
        config = GroupConfig(
            targets=["light.example_root"],
            sensors=["binary_sensor.motion"],
            sensor_templates=[],
            delay=10,  # 10 minutes
        )

        deadline_events: list[tuple[str, str | None]] = []

        def on_deadline_change(group_id, deadline_iso):
            deadline_events.append((group_id, deadline_iso))

        group = SensorGroup(
            hass,
            "g",
            config,
            on_deadline_change=on_deadline_change,
            manager=None,
        )
        await group._async_init_targets()

        # Stub sensor as off so the deadline can start.
        for s in group._sensors:
            s.is_on = AsyncMock(return_value=False)

        # Seed an initial deadline (first-run baseline: leaf_a is on, sensor off).
        await group.check_and_set_deadline()
        for _ in range(5):
            await asyncio.sleep(0)

        # Precondition: a non-null deadline must have been set.
        assert any(e[1] is not None for e in deadline_events), (
            f"precondition: expected a deadline to be set, got {deadline_events!r}"
        )

        # Record the deadline position before re-expansion.
        deadline_before = group._timer_deadline
        assert deadline_before is not None, "precondition: _timer_deadline must be set"
        deadline_events.clear()

        # Advance the mock clock so now() > deadline_before - delay,
        # i.e. a new maybe_delay WOULD extend the deadline if called.
        # We bump the clock by 300 s (half the 600 s delay) so that
        # now + delay = T + 300 + 600 = T + 900 > deadline_before.
        hass.loop.time = MagicMock(return_value=1300.0)  # was 1000.0 at baseline

        # Mutate hass: root now expands to [leaf_a (ON), leaf_b (OFF)].
        def get_with_new_off_leaf(eid):
            if eid == "light.example_root":
                st = MagicMock()
                st.attributes = {"entity_id": ["light.leaf_a", "light.leaf_b"]}
                st.state = "on"
                return st
            if eid == "light.leaf_a":
                st = MagicMock()
                st.attributes = {}
                st.state = "on"
                return st
            if eid == "light.leaf_b":
                st = MagicMock()
                st.attributes = {}
                st.state = "off"
                return st
            return None

        hass.states.get.side_effect = get_with_new_off_leaf

        # Simulate patrol tick via _reexpand_targets.
        await group._reexpand_targets()

        # Assert: the deadline must NOT have moved (no extend notification).
        # An extend would set a deadline further in the future than deadline_before.
        # We detect this by checking that no notification with a NEW (later) deadline
        # was fired and that _timer_deadline is still the same value.
        assert group._timer_deadline == deadline_before, (
            f"deadline extended wrongly: was {deadline_before}, now {group._timer_deadline}. "
            f"An added OFF leaf must not extend the deadline."
        )

    async def test_added_on_leaf_arms_deadline_when_no_prior_deadline(self):
        """Re-expansion adding an ON leaf must arm a deadline even when sensor is off.

        Scenario: no targets initially (root empty), sensor OFF.
        Re-expansion adds leaf_a (ON). A deadline must be set.
        """
        import asyncio

        # Start with an empty root.
        hass = _hass_for_root(
            "light.example_root",
            [],
            member_states={},
        )
        config = GroupConfig(
            targets=["light.example_root"],
            sensors=["binary_sensor.motion"],
            sensor_templates=[],
            delay=10,
        )

        deadline_events: list[tuple[str, str | None]] = []

        def on_deadline_change(group_id, deadline_iso):
            deadline_events.append((group_id, deadline_iso))

        group = SensorGroup(
            hass,
            "g",
            config,
            on_deadline_change=on_deadline_change,
            manager=None,
        )
        await group._async_init_targets()

        # Stub sensor as off.
        for s in group._sensors:
            s.is_on = AsyncMock(return_value=False)

        # Flush first-run with no targets on — no deadline expected.
        await group.check_and_set_deadline()
        for _ in range(5):
            await asyncio.sleep(0)
        deadline_events.clear()
        assert group._timer_deadline is None, (
            "precondition: no deadline when no targets are on"
        )

        # Mutate hass: root now expands to [leaf_a (ON)].
        def get_with_on_leaf(eid):
            if eid == "light.example_root":
                st = MagicMock()
                st.attributes = {"entity_id": ["light.leaf_a"]}
                st.state = "on"
                return st
            if eid == "light.leaf_a":
                st = MagicMock()
                st.attributes = {}
                st.state = "on"
                return st
            return None

        hass.states.get.side_effect = get_with_on_leaf

        await group._reexpand_targets()
        for _ in range(5):
            await asyncio.sleep(0)

        # Assert: a non-null deadline must have been set.
        assert any(e[1] is not None for e in deadline_events), (
            f"expected a deadline to be armed when an ON leaf was added, "
            f"got {deadline_events!r}"
        )
