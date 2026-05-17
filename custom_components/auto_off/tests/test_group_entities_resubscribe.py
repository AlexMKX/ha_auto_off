"""Tests that ``update_members`` rewires state-change subscriptions.

When auto_off.set_group is called for an existing group with a different
``sensors`` or ``targets`` list, the in-process group entities created by
``_sync_group_entities`` get their ``_entity_ids`` swapped via
``update_members``. The bug fixed here: the parent ``GroupEntity`` (from
HA stdlib) subscribed to the original ids inside ``async_added_to_hass``
once, and that subscription is never refreshed - so the group entity
keeps reacting only to the old members, while the new members go
unnoticed and the entity ends up ``unavailable`` with ``entity_id=None``
in the UI.

The fix adds our own subscription that mirrors the parent listener and
is rebuilt on every ``update_members`` call. These tests pin that
contract.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.auto_off.group_entities import (
    AutoOffSensorsGroup,
    TARGET_GROUP_ENTITY_CLASSES,
)


def _patch_tracker(monkeypatch) -> list[dict[str, Any]]:
    """Replace ``async_track_state_change_event`` in group_entities with a
    recorder that captures every subscription request and returns a fresh
    MagicMock unsub for each."""
    calls: list[dict[str, Any]] = []

    def _tracker(hass, entity_ids, action):
        unsub = MagicMock(name=f"unsub_{len(calls)}")
        calls.append({"entity_ids": list(entity_ids), "callback": action, "unsub": unsub})
        return unsub

    monkeypatch.setattr(
        "custom_components.auto_off.group_entities.async_track_state_change_event",
        _tracker,
    )
    return calls


class TestSensorsGroupResubscribes:
    """``AutoOffSensorsGroup.update_members`` must drop the previous
    subscription and install a fresh one for the new member list."""

    async def test_install_subscription_after_added_to_hass(
        self, monkeypatch
    ):
        """First subscription gets installed when the entity is attached
        to hass via the standard ``async_added_to_hass`` lifecycle."""
        tracker = _patch_tracker(monkeypatch)
        entity = AutoOffSensorsGroup(
            group_name="g",
            entity_ids=["binary_sensor.a"],
            sensor_templates=[],
        )
        # Pretend hass attached; bypass parent listener with a stub.
        hass = MagicMock()
        entity.hass = hass
        entity.async_write_ha_state = MagicMock()
        with patch(
            "homeassistant.components.group.binary_sensor.BinarySensorGroup.async_added_to_hass",
            new=AsyncMock(),
        ):
            await entity.async_added_to_hass()

        assert any(c["entity_ids"] == ["binary_sensor.a"] for c in tracker), tracker

    async def test_update_members_cancels_previous_subscription(
        self, monkeypatch
    ):
        tracker = _patch_tracker(monkeypatch)
        entity = AutoOffSensorsGroup(
            group_name="g",
            entity_ids=["binary_sensor.a"],
            sensor_templates=[],
        )
        hass = MagicMock()
        entity.hass = hass
        entity.async_write_ha_state = MagicMock()
        with patch(
            "homeassistant.components.group.binary_sensor.BinarySensorGroup.async_added_to_hass",
            new=AsyncMock(),
        ):
            await entity.async_added_to_hass()

        first_unsub = tracker[0]["unsub"]

        entity.update_members(
            entity_ids=["binary_sensor.b"],
            sensor_templates=[],
        )

        # Old subscription cancelled exactly once.
        first_unsub.assert_called_once()

    async def test_update_members_installs_subscription_for_new_ids(
        self, monkeypatch
    ):
        tracker = _patch_tracker(monkeypatch)
        entity = AutoOffSensorsGroup(
            group_name="g",
            entity_ids=["binary_sensor.a"],
            sensor_templates=[],
        )
        hass = MagicMock()
        entity.hass = hass
        entity.async_write_ha_state = MagicMock()
        with patch(
            "homeassistant.components.group.binary_sensor.BinarySensorGroup.async_added_to_hass",
            new=AsyncMock(),
        ):
            await entity.async_added_to_hass()

        entity.update_members(
            entity_ids=["binary_sensor.b", "binary_sensor.c"],
            sensor_templates=[],
        )

        # A second subscription request with the new ids must appear.
        new_subs = [c for c in tracker if c["entity_ids"] == ["binary_sensor.b", "binary_sensor.c"]]
        assert len(new_subs) == 1, [c["entity_ids"] for c in tracker]


class TestTargetsGroupResubscribes:
    """The dynamically-built per-domain target groups need the same
    rewiring on ``update_members``."""

    async def test_light_targets_group_resubscribes_on_update(
        self, monkeypatch
    ):
        tracker = _patch_tracker(monkeypatch)
        LightTargets = TARGET_GROUP_ENTITY_CLASSES["light"]
        entity = LightTargets(group_name="g", entity_ids=["light.a"])
        hass = MagicMock()
        entity.hass = hass
        # async_update_group_state on the real LightGroup reaches into
        # hass.states; stub it so we test our wiring, not HA internals.
        entity.async_update_group_state = MagicMock()
        entity.async_write_ha_state = MagicMock()
        # The HA LightGroup.async_added_to_hass touches many things; stub it.
        with patch(
            "homeassistant.components.group.light.LightGroup.async_added_to_hass",
            new=AsyncMock(),
        ):
            await entity.async_added_to_hass()

        first_unsub = tracker[0]["unsub"]
        entity.update_members(entity_ids=["light.b"])

        first_unsub.assert_called_once()
        assert any(c["entity_ids"] == ["light.b"] for c in tracker)


class TestUpdateMembersRefreshesState:
    """``update_members`` must trigger an immediate recomputation of the
    group state.

    Without this, a group that switched from an all-unavailable member
    set to a healthy one stays ``unavailable`` until the next
    state-change event on one of the new members - which can be a long
    wait for stable sensors.
    """

    async def test_update_members_calls_async_update_group_state(
        self, monkeypatch
    ):
        _patch_tracker(monkeypatch)
        entity = AutoOffSensorsGroup(
            group_name="g",
            entity_ids=["binary_sensor.a"],
            sensor_templates=[],
        )
        hass = MagicMock()
        entity.hass = hass
        entity.async_update_group_state = MagicMock()
        entity.async_write_ha_state = MagicMock()
        with patch(
            "homeassistant.components.group.binary_sensor.BinarySensorGroup.async_added_to_hass",
            new=AsyncMock(),
        ):
            await entity.async_added_to_hass()

        entity.update_members(
            entity_ids=["binary_sensor.b"],
            sensor_templates=[],
        )

        entity.async_update_group_state.assert_called_once()


class TestRemoveCancelsSubscription:
    """``async_will_remove_from_hass`` must drop our extra subscription
    so reload / delete cycles do not leak listeners."""

    async def test_remove_cancels_active_subscription(self, monkeypatch):
        tracker = _patch_tracker(monkeypatch)
        entity = AutoOffSensorsGroup(
            group_name="g",
            entity_ids=["binary_sensor.a"],
            sensor_templates=[],
        )
        hass = MagicMock()
        entity.hass = hass
        entity.async_write_ha_state = MagicMock()
        with patch(
            "homeassistant.components.group.binary_sensor.BinarySensorGroup.async_added_to_hass",
            new=AsyncMock(),
        ):
            await entity.async_added_to_hass()
        with patch(
            "homeassistant.components.group.binary_sensor.BinarySensorGroup.async_will_remove_from_hass",
            new=AsyncMock(),
        ):
            await entity.async_will_remove_from_hass()

        tracker[0]["unsub"].assert_called_once()
