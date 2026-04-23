"""Tests for auto_off config entry migration.

Covers the v3 cutover: older entries (version < 3) must fail migration
with a clear message directing users to the README migration section.
"""
from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest


class TestAsyncMigrateEntry:
    async def test_old_entry_fails_migration(self, hass, caplog):
        """An entry at version 2 must not be silently migrated."""
        from custom_components.auto_off import async_migrate_entry

        entry = MagicMock()
        entry.version = 2
        entry.data = {"groups": {"kitchen": {"sensors": [], "targets": [], "delay": 0}}}

        with caplog.at_level(logging.ERROR):
            result = await async_migrate_entry(hass, entry)

        assert result is False
        assert any(
            "migration" in record.message.lower() for record in caplog.records
        )
        assert any(
            "readme" in record.message.lower() or "reinstall" in record.message.lower()
            for record in caplog.records
        )

    async def test_current_version_entry_passes(self, hass):
        """An entry already at version 3 is considered up to date."""
        from custom_components.auto_off import async_migrate_entry

        entry = MagicMock()
        entry.version = 3
        entry.data = {"groups": {}}

        result = await async_migrate_entry(hass, entry)
        assert result is True
