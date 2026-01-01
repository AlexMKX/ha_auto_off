"""
End-to-end tests for auto_off integration using Playwright.
Tests run against a real Home Assistant instance in Docker.
"""
import pytest
import asyncio
import os
from playwright.async_api import Page, expect

# Fixtures are in conftest.py (auto-loaded by pytest)

HA_URL = os.environ.get("HA_URL", "http://homeassistant:8123")
TEST_USER = os.environ.get("TEST_USER", "test_admin")
TEST_PASSWORD = os.environ.get("TEST_PASSWORD", "test_password_123")


@pytest.mark.asyncio
class TestHomeAssistantConnection:
    """Test basic connectivity to Home Assistant."""
    
    async def test_ha_is_accessible(self, ha_provisioner):
        """Verify Home Assistant is running and accessible."""
        states = await ha_provisioner.get_states()
        assert isinstance(states, list)
        assert len(states) > 0
    
    async def test_test_entities_exist(self, ha_provisioner):
        """Verify test entities are created."""
        expected_entities = [
            "binary_sensor.test_motion",
            "binary_sensor.test_motion_2",
            "light.test_light",
            "switch.test_switch",
        ]
        
        states = await ha_provisioner.get_states()
        entity_ids = [s["entity_id"] for s in states]
        
        for entity in expected_entities:
            assert entity in entity_ids, f"Entity {entity} not found"


@pytest.mark.asyncio
class TestAutoOffIntegrationSetup:
    """Test auto_off integration installation and configuration."""
    
    async def test_integration_installed(self, ha_with_integration):
        """Verify auto_off integration is installed."""
        # The fixture already installs the integration
        # Just verify we can call its services
        states = await ha_with_integration.get_states()
        assert states is not None
    
    async def test_create_group_via_service(self, ha_with_integration, reset_test_entities):
        """Test creating an auto_off group via service call."""
        group_name = "test_create_group"
        
        await ha_with_integration.create_auto_off_group(
            group_name=group_name,
            sensors=["binary_sensor.test_motion"],
            targets=["light.test_light"],
            delay=5
        )
        
        # Wait for group to be created
        await asyncio.sleep(2)
        
        # Cleanup
        await ha_with_integration.call_service(
            "auto_off",
            "delete_group",
            {"group_name": group_name}
        )
    
    async def test_delete_group_via_service(self, ha_with_integration, reset_test_entities):
        """Test deleting an auto_off group via service call."""
        group_name = "test_delete_group"
        
        # Create group first
        await ha_with_integration.create_auto_off_group(
            group_name=group_name,
            sensors=["binary_sensor.test_motion"],
            targets=["light.test_light"],
            delay=5
        )
        await asyncio.sleep(2)
        
        # Delete group
        await ha_with_integration.call_service(
            "auto_off",
            "delete_group",
            {"group_name": group_name}
        )
        await asyncio.sleep(1)


@pytest.mark.asyncio
class TestAutoOffFunctionality:
    """Test core auto_off functionality."""
    
    async def test_light_stays_on_with_motion(
        self, ha_with_integration, reset_test_entities
    ):
        """Light should stay on while motion sensor is active."""
        group_name = "test_motion_keeps_light"
        
        # Create group with short delay
        await ha_with_integration.create_auto_off_group(
            group_name=group_name,
            sensors=["binary_sensor.test_motion"],
            targets=["light.test_light"],
            delay=0  # Immediate turn off when sensor goes off
        )
        await asyncio.sleep(2)
        
        try:
            # Turn on motion sensor first
            await ha_with_integration.call_service(
                "input_boolean",
                "turn_on",
                {"entity_id": "input_boolean.test_motion_sensor"}
            )
            await asyncio.sleep(1)
            
            # Turn on light
            await ha_with_integration.call_service(
                "input_boolean",
                "turn_on",
                {"entity_id": "input_boolean.test_light_state"}
            )
            await asyncio.sleep(1)
            
            # Light should stay on because motion is detected
            state = await ha_with_integration.get_state("light.test_light")
            assert state["state"] == "on", "Light should stay on while motion is active"
            
            # Wait a bit more and check again
            await asyncio.sleep(5)
            state = await ha_with_integration.get_state("light.test_light")
            assert state["state"] == "on", "Light should still be on"
            
        finally:
            await ha_with_integration.call_service(
                "auto_off",
                "delete_group",
                {"group_name": group_name}
            )
    
    async def test_light_turns_off_after_motion_stops(
        self, ha_with_integration, reset_test_entities
    ):
        """Light should turn off after motion stops and delay expires."""
        group_name = "test_auto_off_delay"
        
        # Create group with 0 delay (immediate)
        await ha_with_integration.create_auto_off_group(
            group_name=group_name,
            sensors=["binary_sensor.test_motion"],
            targets=["light.test_light"],
            delay=0  # Immediate
        )
        await asyncio.sleep(2)
        
        try:
            # Turn on motion sensor
            await ha_with_integration.call_service(
                "input_boolean",
                "turn_on",
                {"entity_id": "input_boolean.test_motion_sensor"}
            )
            await asyncio.sleep(1)
            
            # Turn on light
            await ha_with_integration.call_service(
                "input_boolean",
                "turn_on",
                {"entity_id": "input_boolean.test_light_state"}
            )
            await asyncio.sleep(1)
            
            # Turn off motion sensor
            await ha_with_integration.call_service(
                "input_boolean",
                "turn_off",
                {"entity_id": "input_boolean.test_motion_sensor"}
            )
            
            # Wait for auto-off (poll_interval is 5s + some buffer)
            await asyncio.sleep(10)
            
            # Light should be off now
            state = await ha_with_integration.get_state("light.test_light")
            assert state["state"] == "off", "Light should turn off after motion stops"
            
        finally:
            await ha_with_integration.call_service(
                "auto_off",
                "delete_group",
                {"group_name": group_name}
            )
    
    async def test_multiple_sensors(
        self, ha_with_integration, reset_test_entities
    ):
        """Light should stay on if any sensor is active."""
        group_name = "test_multiple_sensors"
        
        await ha_with_integration.create_auto_off_group(
            group_name=group_name,
            sensors=[
                "binary_sensor.test_motion",
                "binary_sensor.test_motion_2"
            ],
            targets=["light.test_light"],
            delay=0
        )
        await asyncio.sleep(2)
        
        try:
            # Turn on first motion sensor
            await ha_with_integration.call_service(
                "input_boolean",
                "turn_on",
                {"entity_id": "input_boolean.test_motion_sensor"}
            )
            await asyncio.sleep(1)
            
            # Turn on light
            await ha_with_integration.call_service(
                "input_boolean",
                "turn_on",
                {"entity_id": "input_boolean.test_light_state"}
            )
            await asyncio.sleep(1)
            
            # Turn on second sensor FIRST, then turn off first
            # This ensures at least one sensor is always on
            await ha_with_integration.call_service(
                "input_boolean",
                "turn_on",
                {"entity_id": "input_boolean.test_motion_sensor_2"}
            )
            await asyncio.sleep(1)  # Wait for sensor 2 to register
            
            await ha_with_integration.call_service(
                "input_boolean",
                "turn_off",
                {"entity_id": "input_boolean.test_motion_sensor"}
            )
            await asyncio.sleep(5)
            
            # Light should stay on because second sensor is active
            state = await ha_with_integration.get_state("light.test_light")
            assert state["state"] == "on", "Light should stay on while any sensor is active"
            
        finally:
            await ha_with_integration.call_service(
                "auto_off",
                "delete_group",
                {"group_name": group_name}
            )
    
    async def test_multiple_targets(
        self, ha_with_integration, reset_test_entities
    ):
        """All targets should turn off when sensors go inactive."""
        group_name = "test_multiple_targets"
        
        await ha_with_integration.create_auto_off_group(
            group_name=group_name,
            sensors=["binary_sensor.test_motion"],
            targets=[
                "light.test_light",
                "light.test_light_2"
            ],
            delay=0
        )
        await asyncio.sleep(2)
        
        try:
            # Turn on motion sensor
            await ha_with_integration.call_service(
                "input_boolean",
                "turn_on",
                {"entity_id": "input_boolean.test_motion_sensor"}
            )
            await asyncio.sleep(1)
            
            # Turn on both lights
            await ha_with_integration.call_service(
                "input_boolean",
                "turn_on",
                {"entity_id": "input_boolean.test_light_state"}
            )
            await ha_with_integration.call_service(
                "input_boolean",
                "turn_on",
                {"entity_id": "input_boolean.test_light_2_state"}
            )
            await asyncio.sleep(1)
            
            # Turn off motion sensor
            await ha_with_integration.call_service(
                "input_boolean",
                "turn_off",
                {"entity_id": "input_boolean.test_motion_sensor"}
            )
            
            # Wait for auto-off
            await asyncio.sleep(10)
            
            # Both lights should be off
            state1 = await ha_with_integration.get_state("light.test_light")
            state2 = await ha_with_integration.get_state("light.test_light_2")
            assert state1["state"] == "off", "First light should be off"
            assert state2["state"] == "off", "Second light should be off"
            
        finally:
            await ha_with_integration.call_service(
                "auto_off",
                "delete_group",
                {"group_name": group_name}
            )
    
    async def test_switch_target(
        self, ha_with_integration, reset_test_entities
    ):
        """Switches should also be turned off."""
        group_name = "test_switch_target"
        
        await ha_with_integration.create_auto_off_group(
            group_name=group_name,
            sensors=["binary_sensor.test_motion"],
            targets=["switch.test_switch"],
            delay=0
        )
        await asyncio.sleep(2)
        
        try:
            # Turn on motion and switch
            await ha_with_integration.call_service(
                "input_boolean",
                "turn_on",
                {"entity_id": "input_boolean.test_motion_sensor"}
            )
            await ha_with_integration.call_service(
                "input_boolean",
                "turn_on",
                {"entity_id": "input_boolean.test_switch_state"}
            )
            await asyncio.sleep(2)
            
            # Turn off motion
            await ha_with_integration.call_service(
                "input_boolean",
                "turn_off",
                {"entity_id": "input_boolean.test_motion_sensor"}
            )
            
            # Wait for auto-off
            await asyncio.sleep(10)
            
            state = await ha_with_integration.get_state("switch.test_switch")
            assert state["state"] == "off", "Switch should be off"
            
        finally:
            await ha_with_integration.call_service(
                "auto_off",
                "delete_group",
                {"group_name": group_name}
            )

    async def test_orphaned_target_turns_off(
        self, ha_with_integration, reset_test_entities
    ):
        """Expired deadline should trigger cleanup and turn off the target.
        
        Scenario:
        1. Create group with 10 minute delay
        2. Turn on light (sets deadline 10 min in future)
        3. Verify deadline attribute is set
        4. Manipulate deadline to past via API
        5. Periodic worker should detect expired deadline and turn off
        """
        group_name = "test_orphaned_target"
        from datetime import datetime, timedelta, timezone
        
        # Create group with 10 minute delay
        await ha_with_integration.create_auto_off_group(
            group_name=group_name,
            sensors=["binary_sensor.test_motion"],
            targets=["light.test_light"],
            delay=10  # 10 minutes
        )
        await asyncio.sleep(2)
        
        try:
            # Turn on light (sensors are off, so deadline will be set)
            await ha_with_integration.call_service(
                "input_boolean",
                "turn_on",
                {"entity_id": "input_boolean.test_light_state"}
            )
            await asyncio.sleep(2)
            
            # Verify light is on and deadline is set in the future
            state = await ha_with_integration.get_state("light.test_light")
            assert state["state"] == "on", "Light should be on"
            
            deadline_str = state.get("attributes", {}).get("auto_off_deadline")
            assert deadline_str is not None, "Deadline should be set"
            
            deadline = datetime.fromisoformat(deadline_str)
            now = datetime.now(timezone.utc)
            assert deadline > now, f"Deadline should be in future: {deadline} vs {now}"
            
            # Manipulate deadline to 5 minutes in the past
            past_deadline = (now - timedelta(minutes=5)).isoformat()
            await ha_with_integration.set_state(
                "light.test_light",
                "on",
                {
                    **state.get("attributes", {}),
                    "auto_off_deadline": past_deadline
                }
            )
            await asyncio.sleep(1)
            
            # Verify deadline is now in the past
            state = await ha_with_integration.get_state("light.test_light")
            deadline_str = state.get("attributes", {}).get("auto_off_deadline")
            deadline = datetime.fromisoformat(deadline_str)
            assert deadline < now, f"Deadline should be in past: {deadline}"
            
            # Wait for periodic worker to detect expired deadline (poll_interval=5s)
            # Give it up to 15 seconds (3 poll cycles)
            for _ in range(15):
                await asyncio.sleep(1)
                state = await ha_with_integration.get_state("light.test_light")
                if state["state"] == "off":
                    break
            
            # Light should be turned off by cleanup
            assert state["state"] == "off", "Expired deadline should trigger turn off"
            
        finally:
            await ha_with_integration.call_service(
                "auto_off",
                "delete_group",
                {"group_name": group_name}
            )

@pytest.mark.asyncio
class TestAutoOffWithDelay:
    """Test auto_off delay functionality."""
    
    async def test_delay_prevents_immediate_off(
        self, ha_with_integration, reset_test_entities
    ):
        """Light should not turn off immediately with delay configured."""
        group_name = "test_delay"
        
        # Create group with 1 minute delay (we'll check it doesn't turn off in 5 seconds)
        await ha_with_integration.create_auto_off_group(
            group_name=group_name,
            sensors=["binary_sensor.test_motion"],
            targets=["light.test_light"],
            delay=1  # 1 minute
        )
        await asyncio.sleep(2)
        
        try:
            # Turn on motion and light
            await ha_with_integration.call_service(
                "input_boolean",
                "turn_on",
                {"entity_id": "input_boolean.test_motion_sensor"}
            )
            await ha_with_integration.call_service(
                "input_boolean",
                "turn_on",
                {"entity_id": "input_boolean.test_light_state"}
            )
            await asyncio.sleep(1)
            
            # Turn off motion
            await ha_with_integration.call_service(
                "input_boolean",
                "turn_off",
                {"entity_id": "input_boolean.test_motion_sensor"}
            )
            
            # Wait 5 seconds - light should still be on due to delay
            await asyncio.sleep(5)
            
            state = await ha_with_integration.get_state("light.test_light")
            assert state["state"] == "on", "Light should still be on during delay period"
            
        finally:
            await ha_with_integration.call_service(
                "auto_off",
                "delete_group",
                {"group_name": group_name}
            )
    
    async def test_motion_during_delay_cancels_timer(
        self, ha_with_integration, reset_test_entities
    ):
        """Motion during delay period should cancel the turn-off timer."""
        group_name = "test_cancel_timer"
        
        await ha_with_integration.create_auto_off_group(
            group_name=group_name,
            sensors=["binary_sensor.test_motion"],
            targets=["light.test_light"],
            delay=10  # 10 second delay to test cancellation
        )
        await asyncio.sleep(2)
        
        try:
            # Turn on motion and light
            await ha_with_integration.call_service(
                "input_boolean",
                "turn_on",
                {"entity_id": "input_boolean.test_motion_sensor"}
            )
            await ha_with_integration.call_service(
                "input_boolean",
                "turn_on",
                {"entity_id": "input_boolean.test_light_state"}
            )
            await asyncio.sleep(1)
            
            # Turn off motion briefly - this starts the delay timer
            await ha_with_integration.call_service(
                "input_boolean",
                "turn_off",
                {"entity_id": "input_boolean.test_motion_sensor"}
            )
            await asyncio.sleep(3)  # Wait less than the 10s delay
            
            # Turn motion back on before timeout - should cancel timer
            await ha_with_integration.call_service(
                "input_boolean",
                "turn_on",
                {"entity_id": "input_boolean.test_motion_sensor"}
            )
            
            # Wait longer than original timeout would have been
            await asyncio.sleep(12)
            
            # Light should still be on because motion was detected
            state = await ha_with_integration.get_state("light.test_light")
            assert state["state"] == "on", "Light should stay on after motion resumed"
            
        finally:
            await ha_with_integration.call_service(
                "auto_off",
                "delete_group",
                {"group_name": group_name}
            )


@pytest.mark.skip(reason="UI tests require manual browser debugging - selectors may vary by HA version")
class TestUIInteractions:
    """Test UI interactions with Playwright."""
    
    def test_login_to_home_assistant(self, page: Page):
        """Test logging into Home Assistant via UI."""
        page.goto(HA_URL)
        
        # Wait for login form
        page.wait_for_selector('input[name="username"]', timeout=30000)
        
        # Fill credentials
        page.fill('input[name="username"]', TEST_USER)
        page.fill('input[name="password"]', TEST_PASSWORD)
        
        # Submit
        page.click('button[type="submit"]')
        
        # Wait for dashboard
        page.wait_for_selector('home-assistant', timeout=30000)
        
        # Take screenshot
        page.screenshot(path="/screenshots/logged_in.png")
    
    def test_navigate_to_settings(self, page: Page):
        """Test navigating to Settings > Devices & Services."""
        # Login first
        page.goto(HA_URL)
        page.wait_for_selector('input[name="username"]', timeout=30000)
        page.fill('input[name="username"]', TEST_USER)
        page.fill('input[name="password"]', TEST_PASSWORD)
        page.click('button[type="submit"]')
        page.wait_for_selector('home-assistant', timeout=30000)
        
        import time
        time.sleep(2)
        
        # Click Settings in sidebar
        page.click('a[href="/config"]')
        time.sleep(2)
        
        # Take screenshot
        page.screenshot(path="/screenshots/settings.png")
    
    def test_view_auto_off_integration(self, page: Page):
        """Test viewing Auto Off integration in UI."""
        # Login first
        page.goto(HA_URL)
        page.wait_for_selector('input[name="username"]', timeout=30000)
        page.fill('input[name="username"]', TEST_USER)
        page.fill('input[name="password"]', TEST_PASSWORD)
        page.click('button[type="submit"]')
        page.wait_for_selector('home-assistant', timeout=30000)
        
        import time
        time.sleep(2)
        
        # Navigate to integrations
        page.goto(f"{HA_URL}/config/integrations")
        time.sleep(3)
        
        # Take screenshot
        page.screenshot(path="/screenshots/integrations.png")
        
        # Look for Auto Off integration
        auto_off_card = page.locator('text=Auto Off')
        if auto_off_card.count() > 0:
            auto_off_card.first.click()
            time.sleep(2)
            page.screenshot(path="/screenshots/auto_off_integration.png")


@pytest.mark.asyncio
class TestEdgeCases:
    """Test edge cases and error handling."""
    
    async def test_entity_unavailable(
        self, ha_with_integration, reset_test_entities
    ):
        """Test behavior when target entity becomes unavailable."""
        # This test verifies graceful handling of unavailable entities
        group_name = "test_unavailable"
        
        await ha_with_integration.create_auto_off_group(
            group_name=group_name,
            sensors=["binary_sensor.test_motion"],
            targets=["light.test_light", "light.nonexistent_light"],
            delay=0
        )
        await asyncio.sleep(2)
        
        # Should not crash when one target doesn't exist
        await ha_with_integration.call_service(
            "input_boolean",
            "turn_on",
            {"entity_id": "input_boolean.test_motion_sensor"}
        )
        await asyncio.sleep(2)
        
        await ha_with_integration.call_service(
            "auto_off",
            "delete_group",
            {"group_name": group_name}
        )
    
    async def test_rapid_state_changes(
        self, ha_with_integration, reset_test_entities
    ):
        """Test handling of rapid sensor state changes."""
        group_name = "test_rapid_changes"
        
        await ha_with_integration.create_auto_off_group(
            group_name=group_name,
            sensors=["binary_sensor.test_motion"],
            targets=["light.test_light"],
            delay=5  # 5 second delay to allow rapid toggling
        )
        await asyncio.sleep(2)
        
        try:
            # Turn on motion first, then light
            await ha_with_integration.call_service(
                "input_boolean",
                "turn_on",
                {"entity_id": "input_boolean.test_motion_sensor"}
            )
            await ha_with_integration.call_service(
                "input_boolean",
                "turn_on",
                {"entity_id": "input_boolean.test_light_state"}
            )
            await asyncio.sleep(1)
            
            # Rapidly toggle motion sensor - each toggle resets the timer
            for _ in range(3):
                await ha_with_integration.call_service(
                    "input_boolean",
                    "turn_off",
                    {"entity_id": "input_boolean.test_motion_sensor"}
                )
                await asyncio.sleep(1)  # Less than 5s delay
                await ha_with_integration.call_service(
                    "input_boolean",
                    "turn_on",
                    {"entity_id": "input_boolean.test_motion_sensor"}
                )
                await asyncio.sleep(1)
            
            # Motion is on at the end, wait a bit
            await asyncio.sleep(2)
            
            # Light should still be on since motion is active
            state = await ha_with_integration.get_state("light.test_light")
            assert state["state"] == "on"
            
        finally:
            await ha_with_integration.call_service(
                "auto_off",
                "delete_group",
                {"group_name": group_name}
            )
    
    async def test_update_group_config(
        self, ha_with_integration, reset_test_entities
    ):
        """Test updating an existing group configuration."""
        group_name = "test_update_config"
        
        # Create initial group
        await ha_with_integration.create_auto_off_group(
            group_name=group_name,
            sensors=["binary_sensor.test_motion"],
            targets=["light.test_light"],
            delay=5
        )
        await asyncio.sleep(2)
        
        # Update with new config
        await ha_with_integration.create_auto_off_group(
            group_name=group_name,
            sensors=["binary_sensor.test_motion", "binary_sensor.test_motion_2"],
            targets=["light.test_light", "light.test_light_2"],
            delay=10
        )
        await asyncio.sleep(2)
        
        # Cleanup
        await ha_with_integration.call_service(
            "auto_off",
            "delete_group",
            {"group_name": group_name}
        )


@pytest.mark.asyncio
class TestTextEntities:
    """Test editable text entities for group configuration."""

    async def test_delay_text_entity_created_for_group(
        self, ha_with_integration, reset_test_entities
    ):
        """Test that delay text entity is created when a group is created."""
        group_name = "test_text_entities"
        
        await ha_with_integration.create_auto_off_group(
            group_name=group_name,
            sensors=["binary_sensor.test_motion"],
            targets=["light.test_light"],
            delay=60
        )
        await asyncio.sleep(3)
        
        try:
            # Check that delay text entity exists
            delay_entity = f"text.auto_off_{group_name}_delay"
            
            delay_state = await ha_with_integration.get_state(delay_entity)
            assert delay_state is not None, f"Delay text entity not found: {delay_entity}"
            assert delay_state["state"] == "60"
            
        finally:
            await ha_with_integration.call_service(
                "auto_off",
                "delete_group",
                {"group_name": group_name}
            )

    async def test_edit_delay_via_text_entity(
        self, ha_with_integration, reset_test_entities
    ):
        """Test editing delay via text entity."""
        group_name = "test_edit_delay"
        
        await ha_with_integration.create_auto_off_group(
            group_name=group_name,
            sensors=["binary_sensor.test_motion"],
            targets=["light.test_light"],
            delay=30
        )
        await asyncio.sleep(3)
        
        try:
            delay_entity = f"text.auto_off_{group_name}_delay"
            
            # Edit delay via text entity
            await ha_with_integration.set_text_value(delay_entity, "120")
            await asyncio.sleep(2)
            
            # Verify the change
            delay_state = await ha_with_integration.get_state(delay_entity)
            assert delay_state["state"] == "120"
            
            # Verify the sensor entity also shows updated delay
            config_entity = f"sensor.auto_off_{group_name}_config"
            config_state = await ha_with_integration.get_state(config_entity)
            # Should show "120 min" format (delay is in minutes)
            assert "120 min" in config_state["state"]
            
        finally:
            await ha_with_integration.call_service(
                "auto_off",
                "delete_group",
                {"group_name": group_name}
            )

    async def test_edit_delay_with_template(
        self, ha_with_integration, reset_test_entities
    ):
        """Test setting delay as a template string."""
        group_name = "test_delay_template"
        
        await ha_with_integration.create_auto_off_group(
            group_name=group_name,
            sensors=["binary_sensor.test_motion"],
            targets=["light.test_light"],
            delay=30
        )
        await asyncio.sleep(3)
        
        try:
            delay_entity = f"text.auto_off_{group_name}_delay"
            
            # Set delay as template
            template_delay = "{{ states('input_number.delay_value') | int }}"
            await ha_with_integration.set_text_value(delay_entity, template_delay)
            await asyncio.sleep(2)
            
            # Verify the template is stored
            delay_state = await ha_with_integration.get_state(delay_entity)
            assert "{{" in delay_state["state"]
            assert "states" in delay_state["state"]
            
        finally:
            await ha_with_integration.call_service(
                "auto_off",
                "delete_group",
                {"group_name": group_name}
            )

    async def test_delay_change_persists_in_config(
        self, ha_with_integration, reset_test_entities
    ):
        """Test that delay text entity changes are persisted in config entry."""
        group_name = "test_persist_config"
        
        await ha_with_integration.create_auto_off_group(
            group_name=group_name,
            sensors=["binary_sensor.test_motion"],
            targets=["light.test_light"],
            delay=30
        )
        await asyncio.sleep(3)
        
        try:
            delay_entity = f"text.auto_off_{group_name}_delay"
            
            await ha_with_integration.set_text_value(delay_entity, "90")
            await asyncio.sleep(2)
            
            # Get config entry and verify delay change
            config_entry = await ha_with_integration.get_config_entry("auto_off")
            assert config_entry is not None
            
            groups = config_entry.get("data", {}).get("groups", {})
            group_config = groups.get(group_name, {})
            
            assert group_config.get("delay") == 90
            
        finally:
            await ha_with_integration.call_service(
                "auto_off",
                "delete_group",
                {"group_name": group_name}
            )
