"""Tests that the turn-off phase is atomic w.r.t. external callbacks.

Reproduces a race seen on live HA: after the deadline expiry fires
``_turn_off_targets``, the first target acknowledges its ``off`` state
a fraction of a second later (z2m / MQTT delivery). The state-change
callback drives ``check_and_set_deadline``, which observes
``target_on=True`` (the SECOND target is still ``on``), ``sensors_off=True``,
``self._timer is None`` (the deadline timer just fired) - and concludes
that the timer is "lost", so it schedules a fresh deadline via the
"no timer (recalculated)" path. That fresh deadline cancels our
ensure-off retry task and instead pushes the SECOND target's turn-off
out by ``delay`` minutes (typically 20 in production), producing a
characteristic double turn-off pattern in the log with a long pause
between leaves.

The fix: while ``_turn_off_targets`` and its ensure-off retry loop are
running for a group, ``check_and_set_deadline`` (and any other
reentrant consumer) must skip cleanly instead of treating the
in-progress turn-off as "timer lost". The mechanism here is a
dedicated ``asyncio.Lock`` owned by the turn-off phase; callbacks
check ``locked()`` and bail out early.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.auto_off.auto_off import GroupConfig, SensorGroup


def _build_group(hass, *, targets, sensors=("binary_sensor.m",), delay=20):
    config = GroupConfig(
        targets=list(targets),
        sensors=list(sensors),
        sensor_templates=[],
        delay=delay,
    )
    return SensorGroup(hass, "g", config, manager=None)


@pytest.fixture
def hass_for_group():
    hass = MagicMock()
    hass.loop = MagicMock()
    hass.loop.time = MagicMock(return_value=1000.0)
    hass.services = MagicMock()
    hass.services.async_call = AsyncMock()
    hass.bus = MagicMock()
    return hass


class TestTurnOffRace:
    async def test_callback_during_turn_off_does_not_reschedule_deadline(
        self, hass_for_group
    ):
        """While ``_turn_off_targets`` is running, an incoming
        target-state-change callback must NOT schedule a new deadline
        via ``check_and_set_deadline``. Otherwise the ensure-loop is
        cancelled and the slow leaves wait a full ``delay`` again."""
        group = _build_group(hass_for_group, targets=["light.kitchen"])

        # Stub out the two helpers we care about so the test does not
        # exercise the full HA group machinery.
        group._cancel_ensure_task = MagicMock(wraps=group._cancel_ensure_task)
        group._set_deadline_from_delay = AsyncMock()
        group._cancel_deadline = MagicMock(return_value=False)

        # Pretend a target is on (the second leaf hasn't acked off yet).
        for target in group._targets:
            target.is_on = AsyncMock(return_value=True)
            target.turn_off = AsyncMock()

        # Patch the ensure-off loop to be a brief await we can pause on,
        # so we can race a callback against it mid-flight.
        ensure_started = asyncio.Event()
        ensure_release = asyncio.Event()

        async def _slow_ensure():
            ensure_started.set()
            await ensure_release.wait()

        group._ensure_off_loop = _slow_ensure

        # Force "all sensors off" so the callback path would normally
        # try to reschedule.
        group.all_sensors_off = AsyncMock(return_value=True)
        group.any_target_on = AsyncMock(return_value=True)

        turn_off_task = asyncio.create_task(group._turn_off_targets())

        await ensure_started.wait()

        # Simulate the late "target turned off" callback firing while
        # the turn-off phase is still running.
        await group.check_and_set_deadline()

        # The callback must have skipped: no new deadline got placed.
        group._set_deadline_from_delay.assert_not_awaited()

        # Now let the ensure-loop finish.
        ensure_release.set()
        await turn_off_task

    async def test_turn_off_phase_lock_released_after_completion(
        self, hass_for_group
    ):
        """After ``_turn_off_targets`` finishes, callbacks must be able
        to schedule a fresh deadline again."""
        group = _build_group(hass_for_group, targets=["light.kitchen"])
        group._ensure_off_loop = AsyncMock()

        await group._turn_off_targets()

        # The dedicated turn-off lock must NOT be held after completion.
        # We rely on the lock's name; this is verified by introspection
        # rather than its exact API to keep the test resilient.
        lock_attr = getattr(group, "_turn_off_lock", None)
        assert lock_attr is not None, (
            "SensorGroup must expose an asyncio-style lock named "
            "_turn_off_lock so external consumers can detect the phase"
        )
        assert not lock_attr.locked()
