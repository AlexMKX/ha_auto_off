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
        """An entry already at version 4 is considered up to date."""
        from custom_components.auto_off import async_migrate_entry

        entry = MagicMock()
        entry.version = 4
        entry.data = {"groups": {}}

        result = await async_migrate_entry(hass, entry)
        assert result is True

    async def test_v3_entry_drops_ensure_off_fields(self, hass, caplog):
        """v3 stored configs may contain stale ``ensure_window`` and
        ``ensure_interval`` fields from a now-rolled-back experiment.
        The migration to v4 must strip them so GroupConfig (now
        ``extra='forbid'``) accepts the data."""
        from custom_components.auto_off import async_migrate_entry

        entry = MagicMock()
        entry.version = 3
        entry.data = {
            "groups": {
                "kitchen": {
                    "sensors": ["binary_sensor.m"],
                    "targets": ["light.k"],
                    "sensor_templates": [],
                    "delay": 5,
                    "ensure_window": 60,
                    "ensure_interval": 10,
                },
                "office": {
                    "sensors": ["binary_sensor.o"],
                    "targets": ["light.o"],
                    "sensor_templates": [],
                    "delay": 10,
                },
            }
        }

        result = await async_migrate_entry(hass, entry)

        assert result is True
        # async_update_entry must have been called with cleaned data
        # AND a version bump to 4.
        hass.config_entries.async_update_entry.assert_called_once()
        kwargs = hass.config_entries.async_update_entry.call_args.kwargs
        assert kwargs["version"] == 4
        groups = kwargs["data"]["groups"]
        assert "ensure_window" not in groups["kitchen"]
        assert "ensure_interval" not in groups["kitchen"]
        assert groups["kitchen"]["delay"] == 5
        # Untouched group unchanged
        assert groups["office"]["delay"] == 10
