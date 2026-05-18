"""Tests for the post-deadline ensure-off retry loop.

After ``SensorGroup._turn_off_targets`` dispatches its initial round of
``turn_off`` calls, a bounded retry loop must run until every target
reaches ``off`` or sensors come back on. The loop is driven by two
per-group seconds-scale settings:

* ``ensure_window`` (default 60s) caps how long the loop runs.
* ``ensure_interval`` (default 10s, must be > 0) is the pause between
  retry passes.

The contract is documented in
``docs/superpowers/specs/2026-05-16-ensure-off-loop-design.md``. Each
test below pins one observable property of that contract.

Tests use the same ``MagicMock``-based ``hass`` fixture as the rest of
the unit suite. The asyncio sleep used inside the loop is monkey-
patched so we can step it forward deterministically without the real
clock.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import State

from custom_components.auto_off.auto_off import GroupConfig, SensorGroup


# Mark every coroutine test in the module so pytest-asyncio picks them up
# without requiring a per-class decorator. The shared conftest already sets
# ``asyncio_mode = auto`` so this is belt-and-braces only.


def _build_group(
    hass,
    *,
    targets=("light.kitchen",),
    sensors=("binary_sensor.motion",),
    delay=0,
):
    """Build a SensorGroup with stubbed sensor/target tracking.

    Ensure-off retry timings are module-level constants
    (``ENSURE_WINDOW_SEC``, ``ENSURE_INTERVAL_SEC``); patch them at the
    test site if you need a different value.
    """
    config = GroupConfig(
        targets=list(targets),
        sensors=list(sensors),
        sensor_templates=[],
        delay=delay,
    )
    group = SensorGroup(hass, "g", config, manager=None)
    return group


def _replace_targets_with_stubs(group, target_states):
    """Replace ``group._targets`` with stubs whose ``is_on`` returns the
    next element of ``target_states[entity_id]`` each call.

    Each stub also exposes an ``AsyncMock`` ``turn_off`` so tests can
    assert on retry counts.
    """
    new_targets = []
    for target in group._targets:
        eid = target.entity_id
        seq = list(target_states.get(eid, [False]))

        async def _is_on(_seq=seq):
            return _seq.pop(0) if len(_seq) > 1 else _seq[0]

        stub = MagicMock()
        stub.entity_id = eid
        stub.is_on = _is_on
        stub.turn_off = AsyncMock()
        new_targets.append(stub)
    group._targets = new_targets
    return new_targets


def _stub_sensors(group, return_value):
    """Force ``all_sensors_off`` to return ``return_value`` (sync value or
    iterable for sequential calls)."""
    if isinstance(return_value, (list, tuple)):
        seq = list(return_value)

        async def _all_off():
            return seq.pop(0) if len(seq) > 1 else seq[0]
    else:

        async def _all_off():
            return return_value

    group.all_sensors_off = _all_off


class TestEnsureLoopHappyPath:
    """When the initial dispatch worked, the loop must exit before
    retrying."""

    async def test_no_retry_when_targets_off_on_first_check(self, hass):
        group = _build_group(hass)
        targets = _replace_targets_with_stubs(
            group, {"light.kitchen": [False]}
        )
        _stub_sensors(group, True)

        sleep_calls: list[float] = []

        async def _fast_sleep(delay):
            sleep_calls.append(delay)

        with patch("asyncio.sleep", _fast_sleep):
            await group._ensure_off_loop()

        # One sleep happened (the loop slept once before checking).
        assert sleep_calls == [10]
        # No retry was issued.
        targets[0].turn_off.assert_not_called()


class TestEnsureLoopRetriesTarget:
    """If a target is still on after the initial dispatch, the loop must
    retry per-target until it goes off."""

    async def test_single_retry_then_off(self, hass):
        group = _build_group(hass)
        targets = _replace_targets_with_stubs(
            group,
            # First check: on (needs retry). Second check: off.
            {"light.kitchen": [True, False]},
        )
        _stub_sensors(group, True)

        sleep_calls: list[float] = []

        async def _fast_sleep(delay):
            sleep_calls.append(delay)

        with patch("asyncio.sleep", _fast_sleep):
            await group._ensure_off_loop()

        # Loop slept twice (one per check pass that found work).
        assert sleep_calls == [10, 10]
        # Retry happened exactly once.
        targets[0].turn_off.assert_awaited_once()

    async def test_retries_only_still_on_targets(self, hass):
        """A group with two targets retries only the one still on."""
        group = _build_group(hass, targets=("light.a", "light.b"))
        targets = _replace_targets_with_stubs(
            group,
            {
                "light.a": [False],  # already off, never retried
                "light.b": [True, False],  # still on, retry once
            },
        )
        _stub_sensors(group, True)

        async def _fast_sleep(delay):
            return None

        with patch("asyncio.sleep", _fast_sleep):
            await group._ensure_off_loop()

        targets[0].turn_off.assert_not_called()
        targets[1].turn_off.assert_awaited_once()


class TestEnsureLoopAbortsOnSensorReclaim:
    """When sensors come back on, the loop must stand down without
    retrying that pass."""

    async def test_abort_before_retry_when_sensors_back_on(self, hass):
        group = _build_group(hass)
        targets = _replace_targets_with_stubs(
            group,
            # Stays on the entire time — would cause infinite retries
            # if sensor guard didn't trigger.
            {"light.kitchen": [True]},
        )
        # all_sensors_off is False from the very first check.
        _stub_sensors(group, False)

        async def _fast_sleep(delay):
            return None

        with patch("asyncio.sleep", _fast_sleep):
            await group._ensure_off_loop()

        # No retry: sensor guard fired before the still_on check ran a
        # turn_off.
        targets[0].turn_off.assert_not_called()


class TestEnsureLoopWindowExpires:
    """If a target stays on the entire window, the loop must exit after
    the configured number of passes."""

    async def test_six_retries_for_60s_window_10s_interval(self, hass):
        # Defaults are ENSURE_WINDOW_SEC=60 / ENSURE_INTERVAL_SEC=10.
        group = _build_group(hass)
        targets = _replace_targets_with_stubs(
            group,
            # Always on; loop must keep retrying until the window is up.
            {"light.kitchen": [True]},
        )
        _stub_sensors(group, True)

        # Advance a virtual clock so the while-loop exits after 6 passes.
        fake_time = {"now": 0.0}

        def _fake_monotonic():
            return fake_time["now"]

        async def _fast_sleep(delay):
            fake_time["now"] += delay

        with patch("asyncio.sleep", _fast_sleep), patch(
            "time.monotonic", _fake_monotonic
        ):
            await group._ensure_off_loop()

        # Window = 60s, interval = 10s → at most 6 passes that hit retry.
        assert targets[0].turn_off.await_count == 6


class TestEnsureLoopCancellation:
    """The loop must be cancellable from outside via ``self._ensure_task``."""

    async def test_loop_task_cancelled_on_new_deadline(self, hass):
        """Arming a new deadline while the ensure loop is mid-flight
        must cancel the running task."""
        group = _build_group(hass)
        targets = _replace_targets_with_stubs(
            group, {"light.kitchen": [True]}
        )
        _stub_sensors(group, True)

        # Start the loop as a real task; wait until it's parked in sleep.
        task = asyncio.create_task(group._ensure_off_loop())
        group._ensure_task = task

        # Give the event loop a tick so the task hits asyncio.sleep().
        await asyncio.sleep(0)

        # Simulate _start_deadline asking for cancellation.
        group._ensure_task.cancel()
        try:
            await group._ensure_task
        except asyncio.CancelledError:
            pass

        # The task is done; no further retries happened beyond what may
        # have already been issued before cancellation. Specifically,
        # turn_off is called at most once.
        assert targets[0].turn_off.await_count <= 1


class TestEnsureLoopIntegration:
    """``_turn_off_targets`` runs ``_ensure_off_loop`` inline under
    ``self._turn_off_lock``. The previous design used a detached task
    exposed on ``self._ensure_task``; that was changed because the
    detached task allowed external consumers
    (check_and_set_deadline reentry, periodic rescan) to race against
    the in-flight retry by treating it as "timer lost" and starting
    a fresh deadline that cancelled the ensure-loop midway. Running
    inline under the dedicated lock makes the turn-off phase atomic
    relative to every external consumer.
    """

    async def test_turn_off_targets_runs_ensure_loop_inline(self, hass):
        hass.services.async_call = AsyncMock()
        group = _build_group(hass)
        sentinel_ran = asyncio.Event()

        async def _sentinel():
            sentinel_ran.set()

        group._ensure_off_loop = _sentinel

        await group._turn_off_targets()

        # The ensure loop ran inline (not detached) - it must have
        # completed by the time _turn_off_targets returned.
        assert sentinel_ran.is_set()


# Ensure-off retry timings live as module-level constants
# (ENSURE_WINDOW_SEC, ENSURE_INTERVAL_SEC) - their presence and
# rejection-as-fields is covered in tests/test_ensure_constants.py.
