"""E2E test fixtures for auto_off integration."""
import pytest
import asyncio
import aiohttp
import time
import subprocess
import os
import shutil
import signal
from pathlib import Path
from typing import Generator, AsyncGenerator

# Test configuration
HA_PORT = 18123
HA_HOST = "localhost"
HA_URL = f"http://{HA_HOST}:{HA_PORT}"
TEST_CONFIG_DIR = Path(__file__).parent / "test_config"
CUSTOM_COMPONENTS_SRC = Path(__file__).parent.parent.parent / "custom_components"
VENV_DIR = Path(__file__).parent.parent.parent / "venv"
HASS_BIN = VENV_DIR / "bin" / "hass"


class HAInstance:
    """Manages a Home Assistant instance for testing."""

    def __init__(self, config_dir: Path, port: int = HA_PORT):
        self.config_dir = config_dir
        self.port = port
        self.process = None
        self.access_token = None
        self._session = None

    async def start(self, timeout: int = 120) -> None:
        """Start Home Assistant and wait for it to be ready."""
        # Prepare config directory
        self._prepare_config()

        # Start HA process
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"

        self.process = subprocess.Popen(
            [
                str(HASS_BIN),
                "--config", str(self.config_dir),
                "--skip-pip",
            ],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

        # Wait for HA to start
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(f"{HA_URL}/api/", timeout=aiohttp.ClientTimeout(total=5)) as resp:
                        if resp.status in (200, 401):
                            print(f"HA started on port {self.port}")
                            return
            except (aiohttp.ClientError, asyncio.TimeoutError):
                pass
            await asyncio.sleep(2)

        # If we get here, HA didn't start
        self.stop()
        raise RuntimeError(f"Home Assistant did not start within {timeout} seconds")

    def _prepare_config(self) -> None:
        """Prepare the config directory."""
        # Clean up existing config
        if self.config_dir.exists():
            shutil.rmtree(self.config_dir)
        self.config_dir.mkdir(parents=True)

        # Copy custom_components
        dest_cc = self.config_dir / "custom_components"
        shutil.copytree(CUSTOM_COMPONENTS_SRC, dest_cc)

        # Create minimal configuration.yaml
        config_yaml = self.config_dir / "configuration.yaml"
        config_yaml.write_text(f"""
homeassistant:
  name: Test Home
  unit_system: metric
  time_zone: UTC
  latitude: 0
  longitude: 0
  elevation: 0

http:
  server_port: {self.port}

logger:
  default: info
  logs:
    custom_components.auto_off: debug

# Create test entities
input_boolean:
  test_motion:
    name: Test Motion Sensor
  test_light:
    name: Test Light
  test_motion_2:
    name: Test Motion Sensor 2
  test_light_2:
    name: Test Light 2
""")

    async def complete_onboarding(self) -> str:
        """Complete the onboarding process and get access token."""
        async with aiohttp.ClientSession() as session:
            # Check if onboarding is needed
            async with session.get(f"{HA_URL}/api/onboarding") as resp:
                if resp.status != 200:
                    raise RuntimeError("Failed to get onboarding status")
                data = await resp.json()

            # Step 1: Create user - this returns auth_code directly
            if "user" in [step["step"] for step in data]:
                user_data = {
                    "client_id": HA_URL,
                    "name": "Test User",
                    "username": "test",
                    "password": "testpassword123",
                    "language": "en",
                }
                async with session.post(
                    f"{HA_URL}/api/onboarding/users",
                    json=user_data,
                ) as resp:
                    if resp.status != 200:
                        text = await resp.text()
                        raise RuntimeError(f"Failed to create user: {text}")
                    result = await resp.json()
                    auth_code = result.get("auth_code")

                # Exchange auth_code for access token
                token_data = aiohttp.FormData()
                token_data.add_field("grant_type", "authorization_code")
                token_data.add_field("code", auth_code)
                token_data.add_field("client_id", HA_URL)

                async with session.post(
                    f"{HA_URL}/auth/token",
                    data=token_data,
                ) as resp:
                    if resp.status != 200:
                        text = await resp.text()
                        raise RuntimeError(f"Failed to exchange auth code: {text}")
                    result = await resp.json()
                    self.access_token = result["access_token"]

            # Complete remaining onboarding steps
            headers = {"Authorization": f"Bearer {self.access_token}"}

            # Step 2: Core config (skip)
            async with session.post(
                f"{HA_URL}/api/onboarding/core_config",
                headers=headers,
                json={},
            ) as resp:
                pass  # May fail if already done

            # Step 3: Analytics (skip)
            async with session.post(
                f"{HA_URL}/api/onboarding/analytics",
                headers=headers,
                json={},
            ) as resp:
                pass

            # Step 4: Integration (skip)
            async with session.post(
                f"{HA_URL}/api/onboarding/integration",
                headers=headers,
                json={},
            ) as resp:
                pass

        return self.access_token

    def _get_headers(self) -> dict:
        """Get authentication headers."""
        return {"Authorization": f"Bearer {self.access_token}"}

    async def api_get(self, path: str) -> dict:
        """Make authenticated GET request."""
        async with aiohttp.ClientSession(headers=self._get_headers()) as session:
            async with session.get(f"{HA_URL}{path}") as resp:
                resp.raise_for_status()
                return await resp.json()

    async def api_post(self, path: str, data: dict = None) -> dict:
        """Make authenticated POST request."""
        async with aiohttp.ClientSession(headers=self._get_headers()) as session:
            async with session.post(f"{HA_URL}{path}", json=data or {}) as resp:
                if resp.status >= 400:
                    text = await resp.text()
                    raise RuntimeError(f"API error {resp.status}: {text}")
                return await resp.json() if resp.content_length else {}

    async def call_service(self, domain: str, service: str, data: dict = None) -> None:
        """Call a Home Assistant service."""
        await self.api_post(f"/api/services/{domain}/{service}", data or {})

    async def get_state(self, entity_id: str) -> dict:
        """Get entity state."""
        return await self.api_get(f"/api/states/{entity_id}")

    async def set_state(self, entity_id: str, state: str, attributes: dict = None) -> None:
        """Set entity state."""
        await self.api_post(f"/api/states/{entity_id}", {
            "state": state,
            "attributes": attributes or {},
        })

    async def add_integration(self, domain: str, data: dict = None) -> dict:
        """Add an integration via config flow."""
        async with aiohttp.ClientSession(headers=self._get_headers()) as session:
            # Start flow
            async with session.post(
                f"{HA_URL}/api/config/config_entries/flow",
                json={"handler": domain},
            ) as resp:
                resp.raise_for_status()
                flow = await resp.json()

            flow_id = flow["flow_id"]

            # Complete flow
            async with session.post(
                f"{HA_URL}/api/config/config_entries/flow/{flow_id}",
                json=data or {},
            ) as resp:
                resp.raise_for_status()
                return await resp.json()

    async def get_config_entries(self, domain: str = None) -> list:
        """Get config entries."""
        entries = await self.api_get("/api/config/config_entries/entry")
        if domain:
            entries = [e for e in entries if e["domain"] == domain]
        return entries

    async def close(self) -> None:
        """Close session (no-op since we create sessions per request)."""
        pass

    def stop(self) -> None:
        """Stop Home Assistant."""
        if self.process:
            self.process.send_signal(signal.SIGTERM)
            try:
                self.process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self.process.kill()
            self.process = None


@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
async def ha_instance(event_loop) -> AsyncGenerator[HAInstance, None]:
    """Start Home Assistant instance for testing."""
    ha = HAInstance(TEST_CONFIG_DIR, HA_PORT)

    try:
        await ha.start()
        await ha.complete_onboarding()
        yield ha
    finally:
        await ha.close()
        ha.stop()
        # Cleanup config dir
        if TEST_CONFIG_DIR.exists():
            shutil.rmtree(TEST_CONFIG_DIR)
