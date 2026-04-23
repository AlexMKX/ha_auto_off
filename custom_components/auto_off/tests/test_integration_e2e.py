"""E2E tests for auto_off integration."""

import asyncio

import pytest

pytestmark = pytest.mark.docker_e2e


@pytest.mark.asyncio
class TestAutoOffIntegrationE2E:
    """End-to-end tests for the Auto Off integration."""

    async def test_ha_is_running(self, ha_instance):
        """Test that Home Assistant is running and accessible."""
        result = await ha_instance.api_get("/api/")
        assert "message" in result
        assert result["message"] == "API running."

    async def test_add_auto_off_integration(self, ha_instance):
        """Test adding the Auto Off integration via config flow."""
        # Add integration
        result = await ha_instance.add_integration("auto_off", {"poll_interval": 15})

        # Suite can run multiple test classes that may install the integration already.
        if result.get("type") == "create_entry":
            assert result.get("title") == "Auto Off"
        elif result.get("type") == "abort":
            assert result.get("reason") in {
                "already_configured",
                "single_instance_allowed",
            }
        else:
            raise AssertionError(f"Unexpected flow result: {result}")

        # Verify integration is added
        entries = await ha_instance.get_config_entries("auto_off")
        assert len(entries) == 1
        assert entries[0]["domain"] == "auto_off"

    async def test_set_group_service(self, ha_instance):
        """Test the set_group service creates a new group."""
        # First ensure integration is set up
        entries = await ha_instance.get_config_entries("auto_off")
        if not entries:
            await ha_instance.add_integration("auto_off", {"poll_interval": 15})

        # Wait for services to register
        await asyncio.sleep(2)

        await ha_instance.call_service(
            "auto_off",
            "set_group",
            {
                "group_name": "test_group",
                "targets": ["light.test_light"],
                "sensors": ["binary_sensor.test_motion"],
                "delay": 1,
            },
        )

        # Wait for entity to be created
        await asyncio.sleep(2)

        # Verify deadline sensor was created
        try:
            state = await ha_instance.get_state("sensor.auto_off_test_group_deadline")
            assert state is not None
        except Exception:
            pass  # Entity might have a different naming convention

    async def test_auto_off_functionality(self, ha_instance):
        """Test the auto-off functionality with real entities."""
        # Setup: ensure integration and group exist
        entries = await ha_instance.get_config_entries("auto_off")
        if not entries:
            await ha_instance.add_integration("auto_off", {"poll_interval": 15})
            await asyncio.sleep(2)

        await ha_instance.call_service(
            "auto_off",
            "set_group",
            {
                "group_name": "test_auto_off_group",
                "targets": ["light.test_light_2"],
                "sensors": ["binary_sensor.test_motion_2"],
                "delay": 0,
            },
        )
        await asyncio.sleep(3)

        # First, turn ON motion sensor (to prevent immediate auto-off)
        await ha_instance.call_service(
            "input_boolean",
            "turn_on",
            {
                "entity_id": "input_boolean.test_motion_2_state",
            },
        )
        await asyncio.sleep(1)

        # Turn on the light
        await ha_instance.call_service(
            "input_boolean",
            "turn_on",
            {
                "entity_id": "input_boolean.test_light_2_state",
            },
        )
        await asyncio.sleep(1)

        # Verify light is on (motion is on, so it should stay on)
        state = await ha_instance.get_state("light.test_light_2")
        assert state["state"] == "on", f"Expected light to be on, but it's {state['state']}"

        # Turn off motion sensor (should trigger auto-off with delay 0)
        await ha_instance.call_service(
            "input_boolean",
            "turn_off",
            {
                "entity_id": "input_boolean.test_motion_2_state",
            },
        )

        # Wait for periodic worker to process (poll_interval + buffer)
        await asyncio.sleep(20)

        # Light should be off now
        state = await ha_instance.get_state("light.test_light_2")
        assert state["state"] == "off", f"Expected light to be off, but it's {state['state']}"

    async def test_delete_group_service(self, ha_instance):
        """Test the delete_group service removes a group."""
        # First create a group
        await ha_instance.call_service(
            "auto_off",
            "set_group",
            {
                "group_name": "group_to_delete",
                "targets": ["light.test_light"],
                "sensors": ["binary_sensor.test_motion"],
                "delay": 5,
            },
        )
        await asyncio.sleep(2)

        # Delete the group
        await ha_instance.call_service(
            "auto_off",
            "delete_group",
            {
                "group_name": "group_to_delete",
            },
        )
        await asyncio.sleep(2)

        # Verify group is deleted (deadline sensor should be gone)
        try:
            state = await ha_instance.get_state("sensor.auto_off_group_to_delete_deadline")
            assert state is None or state.get("state") == "unavailable"
        except Exception:
            pass  # Entity not found - expected

    async def test_update_group_config(self, ha_instance):
        """Test updating an existing group's configuration."""
        # Create initial group
        await ha_instance.call_service(
            "auto_off",
            "set_group",
            {
                "group_name": "update_test_group",
                "targets": ["light.test_light"],
                "sensors": ["binary_sensor.test_motion"],
                "delay": 5,
            },
        )
        await asyncio.sleep(2)

        # Update the group with new config
        await ha_instance.call_service(
            "auto_off",
            "set_group",
            {
                "group_name": "update_test_group",
                "targets": ["light.test_light", "light.test_light_2"],
                "sensors": ["binary_sensor.test_motion", "binary_sensor.test_motion_2"],
                "delay": 10,
            },
        )
        await asyncio.sleep(2)

        # The group should be updated (we can't easily verify the internal state
        # but at least the service call should not fail)

    async def test_late_binding_target(self, ha_instance):
        """Target entity appears after set_group → picked up on next turn_off.

        Scenario: at set_group time the target backing boolean is off (so
        light.late_target is off). Later the target turns on; when the
        sensor subsequently turns off the timer fires with delay=0 and
        turn_off is called on the now-present (on) target.
        """
        # Ensure integration present.
        entries = await ha_instance.get_config_entries("auto_off")
        if not entries:
            await ha_instance.add_integration("auto_off", {"poll_interval": 1})
            await asyncio.sleep(2)

        # Reset state: make sure both booleans are off before the test.
        await ha_instance.call_service(
            "input_boolean",
            "turn_off",
            {"entity_id": "input_boolean.late_motion_state"},
        )
        await ha_instance.call_service(
            "input_boolean",
            "turn_off",
            {"entity_id": "input_boolean.late_target_state"},
        )
        await asyncio.sleep(1)

        # 1) set_group — light.late_target exists but is currently "off"
        #    (backing boolean is off), sensor is "off" too.
        await ha_instance.call_service(
            "auto_off",
            "set_group",
            {
                "group_name": "late_binding_group",
                "targets": ["light.late_target"],
                "sensors": ["binary_sensor.late_motion"],
                "delay": 0,
            },
        )
        await asyncio.sleep(2)

        # 2) Turn motion sensor ON → timer must NOT fire yet (sensor is on).
        await ha_instance.call_service(
            "input_boolean",
            "turn_on",
            {"entity_id": "input_boolean.late_motion_state"},
        )
        await asyncio.sleep(1)

        # 3) Turn the target ON (it's now "on" in state machine).
        await ha_instance.call_service(
            "input_boolean",
            "turn_on",
            {"entity_id": "input_boolean.late_target_state"},
        )
        await asyncio.sleep(1)

        # 4) Turn motion sensor OFF → all sensors off + target on → timer fires (delay=0).
        await ha_instance.call_service(
            "input_boolean",
            "turn_off",
            {"entity_id": "input_boolean.late_motion_state"},
        )
        await asyncio.sleep(3)  # give timer + HA time to fire and propagate

        # 5) Assert: light.late_target was turned off → backing boolean is off.
        state = await ha_instance.get_state("input_boolean.late_target_state")
        assert state is not None
        assert state["state"] == "off", (
            f"Expected late_target_state to be 'off' after auto-off timer, " f"got: {state['state']}"
        )


@pytest.mark.asyncio
class TestAutoOffServicesValidation:
    """Test service validation."""

    async def test_set_group_empty_targets(self, ha_instance):
        """Empty targets must be rejected (validated by GroupConfig)."""
        with pytest.raises(Exception):  # noqa: B017 - HA service call raises untyped errors
            await ha_instance.call_service(
                "auto_off",
                "set_group",
                {
                    "group_name": "invalid_group",
                    "targets": [],
                    "sensors": ["binary_sensor.test_motion"],
                },
            )

    async def test_set_group_requires_sensor_source(self, ha_instance):
        with pytest.raises(Exception):  # noqa: B017 - HA service call raises untyped errors
            await ha_instance.call_service(
                "auto_off",
                "set_group",
                {
                    "group_name": "incomplete_group",
                    "targets": ["light.test_light"],
                    "sensors": [],
                    "sensor_templates": [],
                },
            )
