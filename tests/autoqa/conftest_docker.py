"""
Pytest fixtures for Docker-based E2E tests with Playwright.
"""
import os
import pytest
import asyncio
import aiohttp
from typing import AsyncGenerator, Generator
from playwright.async_api import async_playwright, Browser, Page, BrowserContext

from provisioning import HAProvisioner


# Configuration from environment
HA_URL = os.environ.get("HA_URL", "http://homeassistant:8123")
TEST_USER = os.environ.get("TEST_USER", "user")
TEST_PASSWORD = os.environ.get("TEST_PASSWORD", "user")


@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for session."""
    policy = asyncio.get_event_loop_policy()
    loop = policy.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
async def ha_provisioner() -> AsyncGenerator[HAProvisioner, None]:
    """Provision Home Assistant and return the provisioner."""
    provisioner = HAProvisioner()
    provisioner._session = aiohttp.ClientSession()
    
    try:
        await provisioner.wait_for_ha()
        try:
            await provisioner.complete_onboarding()
        except Exception as e:
            if "already" in str(e).lower():
                # Onboarding done by entrypoint, just login
                await provisioner._login()
            else:
                raise
        yield provisioner
    finally:
        if provisioner._session:
            await provisioner._session.close()


@pytest.fixture(scope="session")
async def ha_with_integration(ha_provisioner: HAProvisioner) -> HAProvisioner:
    """Ensure auto_off integration is installed."""
    try:
        await ha_provisioner.add_auto_off_integration(poll_interval=5)
    except Exception as e:
        if "already" in str(e).lower() or "exists" in str(e).lower():
            print("auto_off integration already exists, skipping...")
        else:
            raise
    # Wait for integration to fully initialize
    await asyncio.sleep(2)
    return ha_provisioner


@pytest.fixture(scope="session")
async def browser() -> AsyncGenerator[Browser, None]:
    """Create browser instance."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
            ]
        )
        yield browser
        await browser.close()


@pytest.fixture(scope="function")
async def browser_context(browser: Browser) -> AsyncGenerator[BrowserContext, None]:
    """Create browser context for each test."""
    context = await browser.new_context(
        viewport={"width": 1920, "height": 1080},
        ignore_https_errors=True,
    )
    yield context
    await context.close()


@pytest.fixture(scope="function")
async def page(browser_context: BrowserContext) -> AsyncGenerator[Page, None]:
    """Create page for each test."""
    page = await browser_context.new_page()
    yield page
    await page.close()


@pytest.fixture(scope="function")
async def logged_in_page(
    page: Page,
    ha_provisioner: HAProvisioner
) -> AsyncGenerator[Page, None]:
    """Page with logged-in Home Assistant session."""
    # Navigate to HA
    await page.goto(HA_URL)
    
    # Wait for login form
    await page.wait_for_selector('input[name="username"]', timeout=30000)
    
    # Fill login form
    await page.fill('input[name="username"]', TEST_USER)
    await page.fill('input[name="password"]', TEST_PASSWORD)
    
    # Click login button
    await page.click('button[type="submit"]')
    
    # Wait for dashboard to load
    await page.wait_for_selector('home-assistant', timeout=30000)
    await asyncio.sleep(2)  # Give HA time to fully load
    
    yield page


@pytest.fixture
async def auto_off_group(ha_with_integration: HAProvisioner):
    """Create a test auto_off group and clean up after."""
    group_name = "e2e_test_group"
    
    await ha_with_integration.create_auto_off_group(
        group_name=group_name,
        sensors=["binary_sensor.test_motion"],
        targets=["light.test_light"],
        delay=10  # 10 seconds for faster testing
    )
    
    yield group_name
    
    # Cleanup - delete the group
    try:
        await ha_with_integration.call_service(
            "auto_off",
            "delete_group",
            {"group_name": group_name}
        )
    except Exception:
        pass  # Group might already be deleted


@pytest.fixture
async def reset_test_entities(ha_with_integration: HAProvisioner):
    """Reset all test entities to off state before test."""
    entities_to_reset = [
        "input_boolean.test_motion_sensor",
        "input_boolean.test_motion_sensor_2",
        "input_boolean.test_door_sensor",
        "input_boolean.test_light_state",
        "input_boolean.test_light_2_state",
        "input_boolean.test_switch_state",
    ]
    
    for entity in entities_to_reset:
        try:
            await ha_with_integration.call_service(
                "input_boolean",
                "turn_off",
                {"entity_id": entity}
            )
        except Exception:
            pass
    
    await asyncio.sleep(1)
    yield
