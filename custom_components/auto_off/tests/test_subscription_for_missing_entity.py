"""Tests that ``Sensor`` and ``Target`` subscribe to state changes even when
the configured entity_id does not exist in the state machine yet.

Background / why this exists
----------------------------
auto_off can start before integrations that own its sensors/targets are fully
loaded.  In practice this happens with Magic Areas (`binary_sensor.magic_areas_*`
and `light.magic_areas_*` are registered late during HA startup).  Before this
fix, ``Sensor._start_entity_tracking`` and ``Target.start_tracking`` short-
circuited with a warning when the entity wasn't yet in ``hass.states`` and
never installed an ``async_track_state_change_event`` subscription.  The group
then degenerated into a poll-only loop driven by ``periodic_worker`` (60s
default), opening a window where deadlines could expire *after* a sensor flips
back to ``on`` but *before* the next poll cancels the timer.

The current behavior is:
* subscription is always installed via ``async_track_state_change_event``,
  which HA dispatches purely on entity_id strings and works fine for not-yet-
  existing entities;
* ``_last_known_good_state`` stays ``None`` until the first valid (i.e. not
  ``unknown``/``unavailable``) state change arrives; the existing
  ``_handle_entity_change`` flow then transitions it and fires the group
  callback as usual.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import State

from custom_components.auto_off.auto_off import Sensor, Target


@pytest.fixture
def fake_hass():
    """A minimal hass mock with a controllable ``states.get`` and a recording
    ``async_track_state_change_event`` patched onto the auto_off module."""
    hass = MagicMock()
    hass.states = MagicMock()
    hass.services = MagicMock()
    hass.services.async_call = AsyncMock()
    return hass


def _patch_tracker(monkeypatch) -> list[dict[str, Any]]:
    """Replace ``async_track_state_change_event`` with a recorder.

    Returns the list that captures one dict per subscription.  Each dict has
    ``entity_ids`` (list[str]), ``callback`` (the handler the module passed in)
    and ``unsub`` (the no-op cleanup that the production code will store).
    """
    calls: list[dict[str, Any]] = []

    def _tracker(hass, entity_ids, action):
        unsub = MagicMock(name="unsub")
        calls.append({"entity_ids": list(entity_ids), "callback": action, "unsub": unsub})
        return unsub

    monkeypatch.setattr(
        "custom_components.auto_off.auto_off.async_track_state_change_event",
        _tracker,
    )
    return calls


class TestSensorSubscribesEvenWhenEntityMissing:
    """``Sensor._start_entity_tracking`` must subscribe regardless of whether
    the entity exists in ``hass.states`` at start-up.

    Validates the documented fix for the start-up race between auto_off and
    integrations that register their entities late (e.g. Magic Areas).
    """

    async def test_subscribes_when_entity_missing_at_start(self, fake_hass, monkeypatch):
        """Without an existing state, ``Sensor`` still installs a single
        subscription against the configured ``entity_id``.

        Validates: auto_off can start before its sensor integration; once the
        entity appears, state changes will be delivered through the existing
        ``async_track_state_change_event`` subscription, not via the 60-second
        ``periodic_worker`` fallback.

        Method:
        1. Arrange: ``hass.states.get`` returns ``None`` for the sensor.
        2. Act: construct ``Sensor`` and call ``start_tracking``.
        3. Assert: the subscription was installed for exactly the configured
           entity_id, and ``_unsub`` stored the returned cleanup handle.
        """
        tracker_calls = _patch_tracker(monkeypatch)
        fake_hass.states.get = MagicMock(return_value=None)

        sensor = Sensor(
            fake_hass,
            "binary_sensor.magic_areas_presence_tracking_kabinet_sasha_area_state",
            kind="entity",
            on_state_change_callback=AsyncMock(),
        )
        await sensor.start_tracking()

        assert len(tracker_calls) == 1
        assert tracker_calls[0]["entity_ids"] == [
            "binary_sensor.magic_areas_presence_tracking_kabinet_sasha_area_state"
        ]
        assert sensor._unsub is tracker_calls[0]["unsub"]

    async def test_late_state_change_fires_group_callback(self, fake_hass, monkeypatch):
        """A state event delivered after start-up reaches the group callback.

        Validates: the missing-entity-at-start path does not silently drop the
        first transition; once the late-registered entity emits a real
        ``state_changed`` event, the wrapped callback runs and ``True``/``False``
        are forwarded to the group exactly like for entities that existed all
        along.

        Method:
        1. Arrange: ``hass.states.get`` returns ``None`` at construction time
           so we exercise the missing-entity branch; record the subscription
           callback via the patched tracker.
        2. Act: flip ``hass.states.get`` to return an ``on`` state, then invoke
           the captured callback with a synthesized event whose ``new_state``
           is ``on``.
        3. Assert: the group callback received ``(sensor, None, True)`` —
           ``None`` because we never had a valid state before.
        """
        tracker_calls = _patch_tracker(monkeypatch)
        fake_hass.states.get = MagicMock(return_value=None)
        group_cb = AsyncMock()

        sensor = Sensor(
            fake_hass,
            "binary_sensor.magic_areas_presence_tracking_kabinet_sasha_area_state",
            kind="entity",
            on_state_change_callback=group_cb,
        )
        await sensor.start_tracking()
        captured_cb = tracker_calls[0]["callback"]

        # Entity now exists and reports "on". ``_check_entity_state`` requires
        # a real ``State`` instance (it uses ``isinstance(state, State)``) so a
        # bare MagicMock is not enough here.
        on_state = State(
            "binary_sensor.magic_areas_presence_tracking_kabinet_sasha_area_state",
            "on",
        )
        fake_hass.states.get = MagicMock(return_value=on_state)

        event = MagicMock()
        event.data = {
            "entity_id": "binary_sensor.magic_areas_presence_tracking_kabinet_sasha_area_state",
            "new_state": on_state,
        }
        await captured_cb(event)

        group_cb.assert_awaited_once()
        args = group_cb.await_args.args
        assert args[0] is sensor
        assert args[1] is None  # _last_known_good_state was None
        assert args[2] is True  # entity is now on


class TestTargetSubscribesEvenWhenEntityMissing:
    """``Target.start_tracking`` mirrors the Sensor behavior.

    Targets must also subscribe before their entity exists so that turn-off
    decisions and group transitions remain event-driven instead of falling
    back to ``periodic_worker``.
    """

    async def test_subscribes_when_entity_missing_at_start(self, fake_hass, monkeypatch):
        """Without an existing state, ``Target`` still installs a single
        subscription against the configured ``entity_id``.

        Validates: targets that come from late-loaded integrations (e.g. Magic
        Areas light groups) are still tracked event-driven; auto_off no longer
        depends on poll order for transition detection on those targets.

        Method:
        1. Arrange: ``hass.states.get`` returns ``None`` for the target.
        2. Act: construct ``Target`` and call ``start_tracking``.
        3. Assert: the subscription was installed for exactly the configured
           entity_id, and ``_unsub`` stored the returned cleanup handle.
        """
        tracker_calls = _patch_tracker(monkeypatch)
        fake_hass.states.get = MagicMock(return_value=None)

        target = Target(
            fake_hass,
            "light.magic_areas_light_groups_kabinet_sasha_all_lights",
            AsyncMock(),
        )
        await target.start_tracking()

        assert len(tracker_calls) == 1
        assert tracker_calls[0]["entity_ids"] == [
            "light.magic_areas_light_groups_kabinet_sasha_all_lights"
        ]
        assert target._unsub is tracker_calls[0]["unsub"]

    async def test_invalid_entity_id_is_still_skipped(self, fake_hass, monkeypatch):
        """Syntactically invalid entity_ids stay un-subscribed.

        Validates: only the *missing in state machine* case changes behavior.
        Syntactically broken ids (no domain, contains spaces, ...) must still
        short-circuit because ``async_track_state_change_event`` would raise
        for them, which would mask config-time mistakes that callers want
        surfaced via the existing ``_skip`` path on ``Target``.

        Method:
        1. Arrange: build a ``Target`` from an obviously invalid id.
        2. Act: call ``start_tracking``.
        3. Assert: no subscription was attempted.
        """
        tracker_calls = _patch_tracker(monkeypatch)

        target = Target(fake_hass, "not a valid entity id", AsyncMock())
        await target.start_tracking()

        assert tracker_calls == []
        assert target._unsub is None
