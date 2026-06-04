"""Behavior tests for the inactivity-timer deadline model.

Spec: docs/superpowers/specs/2026-06-04-inactivity-timer-model-design.md

Tests assert on observable effects: the on_deadline_change callback
(deadline timestamps), Target turn_off / hass.services.async_call, and
WARNING logs. They do not assert on private transition fields.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.auto_off.auto_off import GroupConfig, SensorGroup
from homeassistant.core import State


def _hass(clock_start: float = 1000.0):
    hass = MagicMock()
    hass.loop = MagicMock()
    hass.loop.time = MagicMock(return_value=clock_start)
    hass.bus = MagicMock()
    hass.services = MagicMock()
    hass.services.async_call = AsyncMock()
    hass.states = MagicMock()
    hass.states.get = MagicMock(return_value=None)
    return hass


def _group(hass, *, delay, poll_interval=15, on_deadline_change=None):
    config = GroupConfig(
        targets=["light.kitchen"],
        sensors=["binary_sensor.motion"],
        sensor_templates=[],
        delay=delay,
    )
    return SensorGroup(
        hass,
        "g",
        config,
        on_deadline_change=on_deadline_change,
        manager=None,
        poll_interval=poll_interval,
    )


class TestMaybeDelayExtendOnly:
    """maybe_delay sets a deadline now()+delay, extend-only."""

    async def test_maybe_delay_sets_deadline_when_none(self):
        hass = _hass(clock_start=1000.0)
        group = _group(hass, delay=2)  # 2 minutes -> 120s
        await group.maybe_delay("test")
        # Deadline scheduled 120s in the future.
        assert group._timer_deadline == pytest.approx(1120.0)

    async def test_maybe_delay_never_shortens(self):
        hass = _hass(clock_start=1000.0)
        group = _group(hass, delay=10)  # 600s
        await group.maybe_delay("first")
        assert group._timer_deadline == pytest.approx(1600.0)
        # Artificially push the deadline far into the future (simulating an
        # earlier extend at a higher clock) so that the next call computes a
        # potential that is SMALLER than the current deadline -> must NOT shorten.
        group._timer_deadline = 2200.0  # far future deadline
        hass.loop.time.return_value = 1005.0  # potential = 1005 + 600 = 1605 < 2200
        await group.maybe_delay("second")
        assert group._timer_deadline == pytest.approx(2200.0)

    async def test_maybe_delay_extends_when_later(self):
        hass = _hass(clock_start=1000.0)
        group = _group(hass, delay=10)  # 600s
        await group.maybe_delay("first")  # deadline 1600
        hass.loop.time.return_value = 1100.0
        await group.maybe_delay("second")  # potential 1700 > 1600
        assert group._timer_deadline == pytest.approx(1700.0)


class TestDelayWarning:
    """A WARNING fires once when delay <= poll_interval."""

    async def test_warns_when_delay_not_greater_than_poll(self, caplog):
        hass = _hass()
        group = _group(hass, delay=0, poll_interval=15)  # 0s <= 15s
        caplog.set_level(logging.WARNING, logger="custom_components.auto_off.auto_off")
        await group.maybe_delay("test")
        assert any(
            "poll_interval" in r.message and r.levelno == logging.WARNING
            for r in caplog.records
        ), f"expected delay/poll warning, got {[r.message for r in caplog.records]!r}"

    async def test_no_warning_when_delay_greater_than_poll(self, caplog):
        hass = _hass()
        group = _group(hass, delay=2, poll_interval=15)  # 120s > 15s
        caplog.set_level(logging.WARNING, logger="custom_components.auto_off.auto_off")
        await group.maybe_delay("test")
        assert not any(
            "poll_interval" in r.message for r in caplog.records
        )


def _hass_with_states(target_on: bool, presence_on: bool, clock=1000.0):
    """hass whose state lookups reflect a single target and single sensor.

    Returns real ``homeassistant.core.State`` objects so that
    ``isinstance(state, State)`` checks in Sensor._check_entity_state pass.
    """
    hass = _hass(clock_start=clock)

    def _get(eid):
        if eid == "light.kitchen":
            return State(eid, "on" if target_on else "off")
        if eid == "binary_sensor.motion":
            return State(eid, "on" if presence_on else "off")
        return None

    hass.states.get = MagicMock(side_effect=_get)
    return hass


class TestInactivityModel:
    """check_and_set_deadline / _on_target_state_change behavior.

    Every test awaits ``_async_init_targets`` first: the SensorGroup
    constructor schedules target init as a background task that the tests
    do not otherwise wait for, so ``self._targets`` would be empty and
    ``any_target_on()`` would wrongly return False.
    """

    async def test_manual_on_no_presence_sets_full_delay_not_immediate(self):
        """Target turns on while unoccupied -> deadline now+delay, NOT fired."""
        hass = _hass_with_states(target_on=True, presence_on=False, clock=1000.0)
        events: list[tuple[str, str | None]] = []
        group = _group(
            hass, delay=2, poll_interval=15,
            on_deadline_change=lambda gid, iso: events.append((gid, iso)),
        )
        await group._async_init_targets()
        # Simulate the target's own off->on callback.
        await group._on_target_state_change(MagicMock(), False, True)
        # A non-null deadline was published and no immediate turn-off.
        assert group._timer_deadline == pytest.approx(1120.0)
        assert hass.services.async_call.await_count == 0

    async def test_no_target_on_cancels_deadline(self):
        hass = _hass_with_states(target_on=False, presence_on=False, clock=1000.0)
        events: list[tuple[str, str | None]] = []
        group = _group(
            hass, delay=2,
            on_deadline_change=lambda gid, iso: events.append((gid, iso)),
        )
        await group._async_init_targets()
        # Seed a deadline, then re-check with no target on.
        group._timer_deadline = 1120.0
        group._is_first_run = False
        await group.check_and_set_deadline()
        assert group._timer_deadline is None

    async def test_presence_on_extends_each_call(self):
        hass = _hass_with_states(target_on=True, presence_on=True, clock=1000.0)
        group = _group(hass, delay=10, poll_interval=15)  # 600s
        await group._async_init_targets()
        await group.check_and_set_deadline()  # first run seeds baseline
        first = group._timer_deadline
        assert first == pytest.approx(1600.0)
        # Advance clock by a patrol interval; presence still on -> extend.
        hass.loop.time.return_value = 1015.0
        await group.check_and_set_deadline()
        assert group._timer_deadline == pytest.approx(1615.0)

    async def test_presence_off_does_not_extend_steady_state(self):
        hass = _hass_with_states(target_on=True, presence_on=False, clock=1000.0)
        group = _group(hass, delay=10, poll_interval=15)
        await group._async_init_targets()
        # First run with target on seeds a baseline deadline.
        await group.check_and_set_deadline()
        seeded = group._timer_deadline
        assert seeded == pytest.approx(1600.0)
        # Later patrol tick, presence still off, no fresh turn-on -> noop.
        hass.loop.time.return_value = 1300.0
        await group.check_and_set_deadline()
        assert group._timer_deadline == pytest.approx(1600.0)  # unchanged

    async def test_second_target_on_extends(self):
        # Two targets; first on, second turns on later (presence off).
        hass = _hass(clock_start=1000.0)
        states = {"light.a": "on", "light.b": "off", "binary_sensor.motion": "off"}

        def _get(eid):
            if eid in states:
                st = MagicMock()
                st.state = states[eid]
                st.attributes = {}
                return st
            return None

        hass.states.get = MagicMock(side_effect=_get)
        config = GroupConfig(
            targets=["light.a", "light.b"],
            sensors=["binary_sensor.motion"],
            sensor_templates=[],
            delay=10,
        )
        group = SensorGroup(hass, "g", config, manager=None, poll_interval=15)
        await group._async_init_targets()
        # First evaluation seeds baseline (a is on).
        await group.check_and_set_deadline()
        assert group._timer_deadline == pytest.approx(1600.0)
        # Time passes; b turns on -> its callback extends.
        hass.loop.time.return_value = 1200.0
        states["light.b"] = "on"
        await group._on_target_state_change(MagicMock(), False, True)
        assert group._timer_deadline == pytest.approx(1800.0)


class TestFireTimePresenceRecheck:
    """If presence is on at the moment the timer fires, abort turn-off."""

    async def test_presence_on_at_fire_aborts_turn_off(self):
        hass = _hass_with_states(target_on=True, presence_on=True, clock=1000.0)
        group = _group(hass, delay=10, poll_interval=15)
        await group._async_init_targets()
        # Directly invoke the turn-off phase as the timer would.
        await group._turn_off_targets()
        # Presence is on -> no turn-off dispatched, deadline re-extended.
        assert hass.services.async_call.await_count == 0
        assert group._timer_deadline == pytest.approx(1600.0)
