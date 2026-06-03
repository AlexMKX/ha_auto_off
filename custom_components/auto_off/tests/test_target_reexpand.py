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
