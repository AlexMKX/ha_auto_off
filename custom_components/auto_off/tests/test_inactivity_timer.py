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
