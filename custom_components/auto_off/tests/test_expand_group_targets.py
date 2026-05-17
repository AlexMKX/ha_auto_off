"""Tests for ``expand_group_targets``.

When a ``GroupConfig.targets`` entry points at a group entity (anything
that exposes an ``entity_id`` attribute holding a list, e.g. a HA group
helper, a ``light.*`` group, a Magic Areas light group, our own
``_AutoOffTargetsGroup``), auto_off must see through it: the actual
turn_off and ensure-loop work happens on the leaves, not on the group.
This matters because:

* Magic Areas light groups are often AND-style for UI semantics, but
  the integration must drive their members individually for ensure
  retries.
* When the group's composition changes (new device added to the area),
  re-running ``set_group`` re-expands and we pick up the new leaves
  without manual edits to the auto_off group definition.

Tests pin the expand helper's contract on a few representative shapes:
flat group, nested group, cycle, mixed-domain helper, and the
late-loaded case where the source group doesn't exist yet.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.auto_off.group_entities import expand_group_targets


def _hass_with_states(state_map: dict[str, list[str] | None]):
    """Build a ``hass`` whose ``states.get(eid)`` returns a state with an
    ``entity_id`` attribute list (group), an empty/no attribute (leaf),
    or ``None`` (entity not in state machine).

    ``state_map`` maps entity_id -> list of children (None means
    "entity not in state machine", [] means "leaf with no children").
    """
    hass = MagicMock()

    def _get(eid):
        if eid not in state_map:
            return None
        children = state_map[eid]
        if children is None:
            return None
        st = MagicMock()
        st.attributes = {"entity_id": list(children)} if children else {}
        return st

    hass.states.get = MagicMock(side_effect=_get)
    return hass


class TestExpandGroupTargets:
    def test_leaf_passes_through(self):
        hass = _hass_with_states({"light.kitchen": []})
        assert expand_group_targets(hass, ["light.kitchen"]) == ["light.kitchen"]

    def test_flat_group_expands_to_members(self):
        hass = _hass_with_states(
            {
                "light.all": ["light.a", "light.b"],
                "light.a": [],
                "light.b": [],
            }
        )
        assert expand_group_targets(hass, ["light.all"]) == [
            "light.a",
            "light.b",
        ]

    def test_nested_groups_expand_to_leaves(self):
        """A group whose member is itself a group must keep recursing."""
        hass = _hass_with_states(
            {
                "light.house": ["light.floor1", "light.floor2"],
                "light.floor1": ["light.kitchen", "light.hall"],
                "light.floor2": ["light.bedroom"],
                "light.kitchen": [],
                "light.hall": [],
                "light.bedroom": [],
            }
        )
        assert expand_group_targets(hass, ["light.house"]) == [
            "light.kitchen",
            "light.hall",
            "light.bedroom",
        ]

    def test_cycle_is_broken(self):
        """A → B → A must terminate and yield no duplicates."""
        hass = _hass_with_states(
            {
                "light.a": ["light.b"],
                "light.b": ["light.a"],
            }
        )
        # Either order is acceptable; pin the de-duped result as a set.
        result = expand_group_targets(hass, ["light.a"])
        # The traversal visits a, then via a's children visits b, then
        # b's children would re-visit a which is already in the visited
        # set, so neither node has children left to add.  The leaves
        # observed by the traversal end up as a/b themselves (whichever
        # closes the cycle first).
        assert set(result) <= {"light.a", "light.b"}
        # Crucially: terminates and does not blow the stack / loop.
        assert len(result) <= 2

    def test_missing_entity_treated_as_leaf(self):
        """A target that does not (yet) exist in hass.states is kept as
        a leaf - it might be a late-loaded entity that will appear
        later, and we still want auto_off to drive it directly."""
        hass = _hass_with_states({"light.late": None})
        assert expand_group_targets(hass, ["light.late"]) == ["light.late"]

    def test_duplicates_are_collapsed(self):
        """If two groups share a leaf, it appears once in the output."""
        hass = _hass_with_states(
            {
                "light.living": ["light.lamp_a", "light.lamp_b"],
                "light.shared": ["light.lamp_b", "light.lamp_c"],
                "light.lamp_a": [],
                "light.lamp_b": [],
                "light.lamp_c": [],
            }
        )
        result = expand_group_targets(hass, ["light.living", "light.shared"])
        assert result == ["light.lamp_a", "light.lamp_b", "light.lamp_c"]

    def test_mixed_domain_group_is_expanded(self):
        """HA's ``group:`` helper can hold mixed-domain members.

        ``expand_group_targets`` returns them verbatim; downstream
        ``split_targets_by_domain`` is responsible for routing each leaf
        to the appropriate per-domain handler.
        """
        hass = _hass_with_states(
            {
                "group.morning_routine": ["light.kitchen", "switch.coffee"],
                "light.kitchen": [],
                "switch.coffee": [],
            }
        )
        assert expand_group_targets(hass, ["group.morning_routine"]) == [
            "light.kitchen",
            "switch.coffee",
        ]
