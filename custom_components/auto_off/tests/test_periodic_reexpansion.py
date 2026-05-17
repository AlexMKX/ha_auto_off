"""Tests that the periodic worker re-expands group targets.

After HA restart, integrations register their entities on different
schedules. If a group target (e.g. ``light.f1_hall_all``) is not yet
in ``hass.states`` when ``_sync_group_entities`` first runs, it is
kept as a single leaf and the per-domain target group entity ends up
holding the group itself instead of its real members.

The fix: on every periodic tick, re-run expansion for every group.
If the expanded leaves changed since the last tick, re-run
``_sync_group_entities`` for that group so the per-domain target
group catches up to the now-registered members. The same loop also
covers runtime composition changes (new device added to a Magic
Areas area, helper group edited via the UI).
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.auto_off.integration_manager import IntegrationManager


def _manager_with_one_group(hass, group_name: str, targets: list[str]):
    """Build an IntegrationManager with a single auto_off group whose
    targets list is ``targets``. The internal sync function is stubbed
    so we can observe re-sync triggers without exercising the full
    entity-registry path."""
    entry = MagicMock()
    entry.entry_id = "e"
    entry.data = {
        "poll_interval": 60,
        "groups": {group_name: {"targets": targets, "sensors": ["binary_sensor.m"]}},
    }
    entry.options = {}
    entry.add_update_listener = MagicMock(return_value=lambda: None)

    manager = IntegrationManager(hass, entry)
    manager._groups_data = {group_name: {"targets": targets, "sensors": ["binary_sensor.m"]}}
    # Stub out the side effects we are not testing here.
    manager._sync_group_entities = AsyncMock()
    manager.auto_off = MagicMock()
    manager.auto_off.periodic_worker = AsyncMock()
    manager.auto_off._groups = {}
    return manager


class TestPeriodicReexpansion:
    async def test_resync_when_expansion_changes(self):
        """If a target that used to be a single leaf (because the group
        entity was not yet registered) now expands to multiple members,
        the next periodic tick must re-run ``_sync_group_entities`` so
        downstream entities catch up."""
        hass = MagicMock()
        # Provide an entity whose entity_id attribute is a list, but only
        # after the second call to states.get (HA loaded the group late).
        calls: list[str] = []

        def _states_get(eid):
            calls.append(eid)
            if eid == "light.house_all":
                # Always present, but the entity_id list "grew" between
                # first and second tick. Returning the same list every
                # call is enough to exercise the re-expand path -
                # initially the manager has not stored any expansion, so
                # the first periodic tick observes a change vs the empty
                # baseline.
                st = MagicMock()
                st.attributes = {"entity_id": ["light.a", "light.b"]}
                return st
            return None

        hass.states.get = MagicMock(side_effect=_states_get)
        manager = _manager_with_one_group(hass, "kitchen", ["light.house_all"])
        # The internal _last_expanded baseline is empty before the first
        # tick, so any non-empty expansion should trigger a re-sync.

        await manager._periodic_worker(None)

        manager._sync_group_entities.assert_awaited_once()
        args, kwargs = manager._sync_group_entities.call_args
        # The re-sync must be for the group whose expansion changed.
        assert args[0] == "kitchen" or kwargs.get("group_name") == "kitchen"

    async def test_no_resync_when_expansion_stable(self):
        """Second tick with the same expansion result must not re-trigger
        ``_sync_group_entities`` - the integration must not spam re-sync
        on every tick when nothing changed."""
        hass = MagicMock()

        def _states_get(eid):
            if eid == "light.house_all":
                st = MagicMock()
                st.attributes = {"entity_id": ["light.a", "light.b"]}
                return st
            return None

        hass.states.get = MagicMock(side_effect=_states_get)
        manager = _manager_with_one_group(hass, "kitchen", ["light.house_all"])

        await manager._periodic_worker(None)
        manager._sync_group_entities.reset_mock()
        # Second tick - expansion result identical, no re-sync expected.
        await manager._periodic_worker(None)

        manager._sync_group_entities.assert_not_awaited()
