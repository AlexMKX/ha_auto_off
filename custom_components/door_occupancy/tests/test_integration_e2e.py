"""E2E smoke tests for door_occupancy integration."""
from __future__ import annotations

import asyncio
import pytest

pytestmark = pytest.mark.docker_e2e


@pytest.mark.asyncio
class TestDoorOccupancyE2E:
    async def test_add_door_occupancy_integration(self, ha_instance):
        result = await ha_instance.add_integration(
            "door_occupancy",
            {"poll_interval": 30, "occupancy_timeout": 15},
        )
        if result.get("type") == "create_entry":
            assert result.get("title") == "Door Occupancy"
        elif result.get("type") == "abort":
            assert result.get("reason") in {"already_configured", "single_instance_allowed"}
        else:
            raise AssertionError(f"Unexpected flow result: {result}")

        entries = await ha_instance.get_config_entries("door_occupancy")
        assert len(entries) == 1
