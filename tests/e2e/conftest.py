"""
Pytest fixtures for Docker-based E2E tests with Playwright.
"""
import os
import sys
import pytest
import pytest_asyncio
import asyncio
import aiohttp
from typing import AsyncGenerator

# Add docker directory to path for provisioning module
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'docker'))

# Configuration from environment
HA_URL = os.environ.get("HA_URL", "http://homeassistant:8123")
TEST_USER = os.environ.get("TEST_USER", "user")
TEST_PASSWORD = os.environ.get("TEST_PASSWORD", "user")


class HAProvisioner:
    """Handles Home Assistant provisioning for E2E tests."""
    
    def __init__(self):
        self.ha_url = HA_URL
        self.test_user = TEST_USER
        self.test_password = TEST_PASSWORD
        self.access_token = None
        self._session = None
    
    async def wait_for_ha(self, timeout: int = 300) -> bool:
        """Wait for Home Assistant to be ready."""
        import time
        print(f"Waiting for Home Assistant at {self.ha_url}...")
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            try:
                async with self._session.get(
                    f"{self.ha_url}/api/",
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    if resp.status in (200, 401):
                        print("Home Assistant is ready!")
                        return True
            except (aiohttp.ClientError, asyncio.TimeoutError):
                pass
            await asyncio.sleep(2)
        
        raise RuntimeError(f"Home Assistant not ready after {timeout}s")
    
    async def complete_onboarding(self) -> str:
        """Complete the onboarding process and return access token."""
        print("Starting onboarding process...")
        
        # Check onboarding status
        async with self._session.get(f"{self.ha_url}/api/onboarding") as resp:
            if resp.status != 200:
                # Onboarding already done, try to login
                print("Onboarding already complete, logging in...")
                return await self._login()
            status = await resp.json()
        
        if not status:
            # Empty list means onboarding done
            print("Onboarding already complete, logging in...")
            return await self._login()
        
        steps = [step["step"] for step in status]
        print(f"Onboarding steps required: {steps}")
        
        # Step 1: Create user
        if "user" in steps:
            print("Creating test user...")
            user_data = {
                "client_id": self.ha_url,
                "name": "Test Admin",
                "username": self.test_user,
                "password": self.test_password,
                "language": "en",
            }
            async with self._session.post(
                f"{self.ha_url}/api/onboarding/users",
                json=user_data
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    # User already exists, try login
                    if "already done" in text.lower():
                        print("User already created, logging in...")
                        return await self._login()
                    raise RuntimeError(f"Failed to create user: {text}")
                result = await resp.json()
                auth_code = result.get("auth_code")
            
            # Exchange auth_code for access token
            token_data = aiohttp.FormData()
            token_data.add_field("grant_type", "authorization_code")
            token_data.add_field("code", auth_code)
            token_data.add_field("client_id", self.ha_url)
            
            async with self._session.post(
                f"{self.ha_url}/auth/token",
                data=token_data
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise RuntimeError(f"Failed to exchange auth_code: {text}")
                result = await resp.json()
                self.access_token = result["access_token"]
        else:
            # No user step, try login
            return await self._login()
        
        headers = {"Authorization": f"Bearer {self.access_token}"}
        
        # Complete remaining steps
        for step in ["core_config", "analytics", "integration"]:
            async with self._session.post(
                f"{self.ha_url}/api/onboarding/{step}",
                headers=headers,
                json={}
            ) as resp:
                pass
        
        print("Onboarding complete!")
        return self.access_token
    
    async def _login(self) -> str:
        """Login with existing user and get access token."""
        print(f"Logging in as {self.test_user}...")
        
        # Start auth flow
        async with self._session.post(
            f"{self.ha_url}/auth/login_flow",
            json={"client_id": self.ha_url, "handler": ["homeassistant", None], "redirect_uri": self.ha_url}
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"Failed to start login flow: {text}")
            flow = await resp.json()
        
        flow_id = flow["flow_id"]
        
        # Submit credentials
        async with self._session.post(
            f"{self.ha_url}/auth/login_flow/{flow_id}",
            json={"username": self.test_user, "password": self.test_password, "client_id": self.ha_url}
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"Failed to login: {text}")
            result = await resp.json()
        
        if result.get("type") != "create_entry":
            raise RuntimeError(f"Login failed: {result}")
        
        auth_code = result["result"]
        
        # Exchange code for token
        token_data = aiohttp.FormData()
        token_data.add_field("grant_type", "authorization_code")
        token_data.add_field("code", auth_code)
        token_data.add_field("client_id", self.ha_url)
        
        async with self._session.post(
            f"{self.ha_url}/auth/token",
            data=token_data
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"Failed to get token: {text}")
            result = await resp.json()
            self.access_token = result["access_token"]
        
        print("Login successful!")
        return self.access_token
    
    async def add_auto_off_integration(self, poll_interval: int = 5) -> dict:
        """Add the auto_off integration via config flow."""
        headers = {"Authorization": f"Bearer {self.access_token}"}
        
        # Check if integration already exists
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{self.ha_url}/api/config/config_entries/entry",
                headers=headers
            ) as resp:
                entries = await resp.json()
                for entry in entries:
                    if entry.get("domain") == "auto_off":
                        print("auto_off integration already installed")
                        return entry
        
        print("Adding auto_off integration...")
        
        async with self._session.post(
            f"{self.ha_url}/api/config/config_entries/flow",
            headers=headers,
            json={"handler": "auto_off"}
        ) as resp:
            resp.raise_for_status()
            flow = await resp.json()
        
        flow_id = flow["flow_id"]
        
        async with self._session.post(
            f"{self.ha_url}/api/config/config_entries/flow/{flow_id}",
            headers=headers,
            json={"poll_interval": poll_interval}
        ) as resp:
            resp.raise_for_status()
            result = await resp.json()
        
        print(f"Integration added: {result}")
        return result
    
    async def create_auto_off_group(
        self, group_name: str, sensors: list, targets: list, delay: int = 10
    ) -> None:
        """Create an auto_off group via service call."""
        headers = {"Authorization": f"Bearer {self.access_token}"}
        
        service_data = {
            "group_name": group_name,
            "sensors": sensors,
            "targets": targets,
            "delay": delay,
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.ha_url}/api/services/auto_off/set_group",
                headers=headers,
                json=service_data
            ) as resp:
                if resp.status >= 400:
                    text = await resp.text()
                    raise RuntimeError(f"Failed to create group: {text}")
    
    async def call_service(self, domain: str, service: str, data: dict = None) -> None:
        """Call a Home Assistant service."""
        headers = {"Authorization": f"Bearer {self.access_token}"}
        
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.ha_url}/api/services/{domain}/{service}",
                headers=headers,
                json=data or {}
            ) as resp:
                if resp.status >= 400:
                    text = await resp.text()
                    raise RuntimeError(f"Service call failed: {text}")
    
    async def get_state(self, entity_id: str) -> dict:
        """Get entity state."""
        headers = {"Authorization": f"Bearer {self.access_token}"}
        
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{self.ha_url}/api/states/{entity_id}",
                headers=headers
            ) as resp:
                resp.raise_for_status()
                return await resp.json()
    
    async def get_states(self) -> list:
        """Get all entity states."""
        headers = {"Authorization": f"Bearer {self.access_token}"}
        
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{self.ha_url}/api/states",
                headers=headers
            ) as resp:
                resp.raise_for_status()
                return await resp.json()
    
    async def set_state(self, entity_id: str, state: str, attributes: dict = None) -> dict:
        """Set entity state and attributes via API."""
        headers = {"Authorization": f"Bearer {self.access_token}"}
        payload = {"state": state}
        if attributes:
            payload["attributes"] = attributes
        
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.ha_url}/api/states/{entity_id}",
                headers=headers,
                json=payload
            ) as resp:
                resp.raise_for_status()
                return await resp.json()

    async def set_text_value(self, entity_id: str, value: str) -> None:
        """Set value for a text entity via service call."""
        headers = {"Authorization": f"Bearer {self.access_token}"}
        
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.ha_url}/api/services/text/set_value",
                headers=headers,
                json={"entity_id": entity_id, "value": value}
            ) as resp:
                if resp.status >= 400:
                    text = await resp.text()
                    raise RuntimeError(f"Failed to set text value: {text}")

    async def get_config_entry(self, domain: str) -> dict:
        """Get config entry for a domain."""
        headers = {"Authorization": f"Bearer {self.access_token}"}
        
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{self.ha_url}/api/config/config_entries/entry",
                headers=headers
            ) as resp:
                resp.raise_for_status()
                entries = await resp.json()
                for entry in entries:
                    if entry.get("domain") == domain:
                        return entry
                return None


@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for session."""
    policy = asyncio.get_event_loop_policy()
    loop = policy.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session")
async def ha_provisioner() -> AsyncGenerator[HAProvisioner, None]:
    """Provision Home Assistant and return the provisioner."""
    provisioner = HAProvisioner()
    provisioner._session = aiohttp.ClientSession()
    
    try:
        await provisioner.wait_for_ha()
        await provisioner.complete_onboarding()
        yield provisioner
    finally:
        if provisioner._session:
            await provisioner._session.close()


@pytest_asyncio.fixture(scope="session")
async def ha_with_integration(ha_provisioner: HAProvisioner) -> HAProvisioner:
    """Ensure auto_off integration is installed."""
    await ha_provisioner.add_auto_off_integration(poll_interval=5)
    await asyncio.sleep(2)
    return ha_provisioner


@pytest_asyncio.fixture
async def reset_test_entities(ha_with_integration: HAProvisioner):
    """Reset all test entities to off state before test."""
    entities = [
        "input_boolean.test_motion_sensor",
        "input_boolean.test_motion_sensor_2",
        "input_boolean.test_door_sensor",
        "input_boolean.test_light_state",
        "input_boolean.test_light_2_state",
        "input_boolean.test_switch_state",
    ]
    
    for entity in entities:
        try:
            await ha_with_integration.call_service(
                "input_boolean", "turn_off", {"entity_id": entity}
            )
        except Exception:
            pass
    
    await asyncio.sleep(1)
    yield


@pytest_asyncio.fixture
async def logged_in_page(page, ha_provisioner):
    """Page with logged-in Home Assistant session."""
    await page.goto(HA_URL)
    await page.wait_for_selector('input[name="username"]', timeout=30000)
    await page.fill('input[name="username"]', TEST_USER)
    await page.fill('input[name="password"]', TEST_PASSWORD)
    await page.click('button[type="submit"]')
    await page.wait_for_selector('home-assistant', timeout=30000)
    await asyncio.sleep(2)
    yield page
