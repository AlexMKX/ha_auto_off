"""Tests for auto_off config entry migration.

Covers the v2→v3 migration path: v2 entries can be automatically migrated
because the data shape is already compatible with v3.  Unknown/ancient entries
(version < 2) must fail with an error directing users to reinstall.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock


class TestAsyncMigrateEntry:
    async def test_v2_entry_migrates_automatically(self, hass, caplog):
        """A v2 entry must be auto-migrated to v3 (data shape is compatible)."""
        from custom_components.auto_off import async_migrate_entry

        entry = MagicMock()
        entry.version = 2
        entry.data = {"groups": {"kitchen": {"sensors": [], "targets": [], "delay": 0}}}

        with caplog.at_level(logging.INFO):
            result = await async_migrate_entry(hass, entry)

        assert result is True
        assert any("migrating" in record.message.lower() for record in caplog.records)
        # version must have been bumped
        hass.config_entries.async_update_entry.assert_called_once_with(entry, version=3)

    async def test_ancient_entry_fails_migration(self, hass, caplog):
        """An entry at version 1 (pre-structured data) must fail migration."""
        from custom_components.auto_off import async_migrate_entry

        entry = MagicMock()
        entry.version = 1
        entry.data = {}

        with caplog.at_level(logging.ERROR):
            result = await async_migrate_entry(hass, entry)

        assert result is False
        assert any("reinstall" in record.message.lower() for record in caplog.records)

    async def test_current_version_entry_passes(self, hass):
        """An entry already at version 3 is considered up to date."""
        from custom_components.auto_off import async_migrate_entry

        entry = MagicMock()
        entry.version = 3
        entry.data = {"groups": {}}

        result = await async_migrate_entry(hass, entry)
        assert result is True
