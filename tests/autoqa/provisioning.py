"""
Home Assistant provisioning utilities for E2E tests.
Handles user creation and onboarding skip.
"""
import os
import time
import asyncio
import aiohttp
from typing import Optional


class HAProvisioner:
    """Handles Home Assistant provisioning for E2E tests."""
    
    def __init__(self):
        self.ha_url = os.environ.get("HA_URL", "http://homeassistant:8123")
        self.test_user = os.environ.get("TEST_USER", "user")
        self.test_password = os.environ.get("TEST_PASSWORD", "user")
        self.access_token: Optional[str] = None
        self._session: Optional[aiohttp.ClientSession] = None
    
    async def __aenter__(self):
        self._session = aiohttp.ClientSession()
        return self
    
    async def __aexit__(self, *args):
        if self._session:
            await self._session.close()
    
    async def wait_for_ha(self, timeout: int = 300) -> bool:
        """Wait for Home Assistant to be ready."""
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
    
    async def check_onboarding_status(self) -> dict:
        """Check current onboarding status."""
        async with self._session.get(f"{self.ha_url}/api/onboarding") as resp:
            if resp.status != 200:
                raise RuntimeError(f"Failed to get onboarding status: {resp.status}")
            return await resp.json()
    
    async def complete_onboarding(self) -> str:
        """Complete the onboarding process and return access token."""
        print("Starting onboarding process...")
        
        # Check onboarding status
        status = await self.check_onboarding_status()
        steps = [step["step"] for step in status]
        print(f"Onboarding steps required: {steps}")
        
        # If no steps left, onboarding is done - just login
        if not steps:
            print("Onboarding already complete, logging in...")
            return await self._login()
        
        # Step 1: Create user (returns auth_code)
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
                    # User step already done - login and continue with remaining steps
                    if "already done" in text.lower():
                        print("User already exists, logging in...")
                        await self._login()
                    else:
                        raise RuntimeError(f"Failed to create user: {text}")
                else:
                    result = await resp.json()
                    auth_code = result.get("auth_code")
                    print(f"User created, got auth_code")
                    
                    # Exchange auth_code for access token
                    print("Exchanging auth_code for access token...")
                    token_data = aiohttp.FormData()
                    token_data.add_field("grant_type", "authorization_code")
                    token_data.add_field("code", auth_code)
                    token_data.add_field("client_id", self.ha_url)
                    
                    async with self._session.post(
                        f"{self.ha_url}/auth/token",
                        data=token_data
                    ) as resp2:
                        if resp2.status != 200:
                            text = await resp2.text()
                            raise RuntimeError(f"Failed to exchange auth_code: {text}")
                        result = await resp2.json()
                        self.access_token = result["access_token"]
                        print("Got access token!")
        
        headers = {"Authorization": f"Bearer {self.access_token}"}
        
        # Complete remaining onboarding steps
        step_data = {
            "core_config": {},
            "analytics": {},
            "integration": {"client_id": self.ha_url, "redirect_uri": f"{self.ha_url}/?auth_callback=1"}
        }
        for step, data in step_data.items():
            print(f"Completing {step} step...")
            async with self._session.post(
                f"{self.ha_url}/api/onboarding/{step}",
                headers=headers,
                json=data
            ) as resp:
                if resp.status == 200:
                    print(f"  {step}: OK")
                else:
                    text = await resp.text()
                    if "already done" in text.lower():
                        print(f"  {step}: already done")
                    else:
                        print(f"  {step}: {resp.status} - {text[:100]}")
        
        # Verify onboarding is complete
        remaining = await self.check_onboarding_status()
        if remaining:
            print(f"Warning: Onboarding steps still pending: {[s['step'] for s in remaining]}")
        else:
            print("Onboarding complete - all steps done!")
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
    
    async def add_auto_off_integration(self, poll_interval: int = 15) -> dict:
        """Add the auto_off integration via config flow."""
        headers = {"Authorization": f"Bearer {self.access_token}"}
        
        print("Adding auto_off integration...")
        
        # Start config flow
        async with self._session.post(
            f"{self.ha_url}/api/config/config_entries/flow",
            headers=headers,
            json={"handler": "auto_off"}
        ) as resp:
            resp.raise_for_status()
            flow = await resp.json()
        
        flow_id = flow["flow_id"]
        
        # Complete config flow
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
        self,
        group_name: str,
        sensors: list,
        targets: list,
        delay: int = 10
    ) -> None:
        """Create an auto_off group via service call."""
        headers = {"Authorization": f"Bearer {self.access_token}"}
        
        print(f"Creating auto_off group '{group_name}'...")
        
        service_data = {
            "group_name": group_name,
            "sensors": sensors,
            "targets": targets,
            "delay": delay,
        }
        
        async with self._session.post(
            f"{self.ha_url}/api/services/auto_off/set_group",
            headers=headers,
            json=service_data
        ) as resp:
            if resp.status >= 400:
                text = await resp.text()
                raise RuntimeError(f"Failed to create group: {text}")
        
        print(f"Group '{group_name}' created!")
    
    async def call_service(
        self,
        domain: str,
        service: str,
        data: dict = None
    ) -> None:
        """Call a Home Assistant service."""
        headers = {"Authorization": f"Bearer {self.access_token}"}
        
        async with self._session.post(
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
        
        async with self._session.get(
            f"{self.ha_url}/api/states/{entity_id}",
            headers=headers
        ) as resp:
            resp.raise_for_status()
            return await resp.json()
    
    async def get_states(self) -> list:
        """Get all entity states."""
        headers = {"Authorization": f"Bearer {self.access_token}"}
        
        async with self._session.get(
            f"{self.ha_url}/api/states",
            headers=headers
        ) as resp:
            resp.raise_for_status()
            return await resp.json()


async def provision_ha() -> HAProvisioner:
    """Main provisioning function."""
    provisioner = HAProvisioner()
    async with provisioner:
        await provisioner.wait_for_ha()
        
        # Try login first - if it works, provisioning was already done
        try:
            await provisioner._login()
            print(f"Already provisioned, logged in as: {provisioner.test_user}")
        except Exception as e:
            # Login failed, need to complete onboarding
            print(f"Login failed ({e}), completing onboarding...")
            await provisioner.complete_onboarding()
            print(f"Home Assistant provisioned with user: {provisioner.test_user}")
        
        try:
            await provisioner.add_auto_off_integration()
        except Exception as e:
            if "already" in str(e).lower() or "exists" in str(e).lower():
                print("auto_off integration already exists, skipping...")
            else:
                print(f"Warning: Could not add auto_off integration: {e}")
    return provisioner


if __name__ == "__main__":
    print("Starting Home Assistant provisioning...")
    asyncio.run(provision_ha())
    print("Provisioning complete!")
