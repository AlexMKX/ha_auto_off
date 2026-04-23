"""
Pytest fixtures for Docker-based E2E tests.

ha-test-kit handles provisioning and exports:
  - HA_BASE_URL
  - HASS_LONG_LIVED_TOKEN
"""

import contextlib
import os

import aiohttp
import pytest
import pytest_asyncio

# ha-test-kit exports these env vars before running pytest
HA_BASE_URL = os.environ.get("HA_BASE_URL", "http://homeassistant:8123").rstrip("/")
HASS_TOKEN = os.environ.get("HASS_LONG_LIVED_TOKEN", "")

AUTOQA_USERNAME = os.environ.get("AUTOQA_USERNAME", "autoqa")
AUTOQA_PASSWORD = os.environ.get("AUTOQA_PASSWORD", "autoqa")


class HAInstance:
    """Thin REST-API wrapper for E2E tests. Token is provided by ha-test-kit."""

    def __init__(self, base_url: str, token: str):
        self.base_url = base_url
        self.token = token
        self._headers = {"Authorization": f"Bearer {token}"}

    def _connector(self) -> aiohttp.TCPConnector:
        # Work around aiohttp+aiodns incompatibilities (py3.13 / c-ares) observed in
        # the Docker E2E environment by forcing a ThreadedResolver.
        resolver = aiohttp.resolver.ThreadedResolver()
        return aiohttp.TCPConnector(resolver=resolver)

    # -- low-level helpers ---------------------------------------------------

    async def api_get(self, path: str) -> dict:
        async with (
            aiohttp.ClientSession(connector=self._connector()) as s,
            s.get(f"{self.base_url}{path}", headers=self._headers) as r,
        ):
            r.raise_for_status()
            return await r.json()

    async def api_post(self, path: str, json: dict | None = None) -> dict:
        async with (
            aiohttp.ClientSession(connector=self._connector()) as s,
            s.post(f"{self.base_url}{path}", headers=self._headers, json=json or {}) as r,
        ):
            r.raise_for_status()
            return await r.json()

    # -- domain helpers ------------------------------------------------------

    async def call_service(self, domain: str, service: str, data: dict | None = None) -> None:
        async with (
            aiohttp.ClientSession(connector=self._connector()) as s,
            s.post(
                f"{self.base_url}/api/services/{domain}/{service}",
                headers=self._headers,
                json=data or {},
            ) as r,
        ):
            if r.status >= 400:
                text = await r.text()
                raise RuntimeError(f"Service call {domain}.{service} failed: {text}")

    async def get_state(self, entity_id: str) -> dict:
        return await self.api_get(f"/api/states/{entity_id}")

    async def get_states(self) -> list:
        return await self.api_get("/api/states")

    async def get_config_entries(self, domain: str) -> list:
        entries = await self.api_get("/api/config/config_entries/entry")
        return [e for e in entries if e.get("domain") == domain]

    async def add_integration(self, domain: str, user_input: dict) -> dict:
        """Add an integration via config flow."""
        flow = await self.api_post(
            "/api/config/config_entries/flow",
            json={"handler": domain},
        )

        # If already configured, HA can immediately abort without returning flow_id.
        if flow.get("type") == "abort":
            return flow

        flow_id = flow.get("flow_id")
        if not flow_id:
            raise RuntimeError(f"Config flow init missing flow_id: {flow}")

        return await self.api_post(
            f"/api/config/config_entries/flow/{flow_id}",
            json=user_input,
        )

    async def set_text_value(self, entity_id: str, value: str) -> None:
        """Set value for a text entity via service call."""
        await self.call_service("text", "set_value", {"entity_id": entity_id, "value": value})

    async def create_auto_off_group(
        self, group_name: str, sensors: list[str], targets: list[str], delay: int = 0
    ) -> None:
        """Create an auto_off group via YAML-only set_group service."""
        import yaml

        config_yaml = yaml.dump({"sensors": sensors, "targets": targets, "delay": delay})
        await self.call_service(
            "auto_off",
            "set_group",
            {
                "group_name": group_name,
                "config": config_yaml,
            },
        )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="session")
async def ha_instance() -> HAInstance:
    """Return an authenticated HAInstance (token from ha-test-kit)."""
    assert HASS_TOKEN, (
        "HASS_LONG_LIVED_TOKEN is not set. "
        "Run tests via ha-test-kit (./ha-test-kit/run_e2e.sh) or set the env var manually."
    )
    return HAInstance(HA_BASE_URL, HASS_TOKEN)


@pytest_asyncio.fixture(scope="session")
async def ha_with_integration(ha_instance: HAInstance) -> HAInstance:
    """Ensure auto_off integration is installed."""
    import asyncio

    entries = await ha_instance.get_config_entries("auto_off")
    if not entries:
        await ha_instance.add_integration("auto_off", {"poll_interval": 5})
        await asyncio.sleep(2)
    return ha_instance


@pytest_asyncio.fixture
async def reset_test_entities(ha_instance: HAInstance):
    """Reset all test entities to off state before each test."""
    entities = [
        "input_boolean.test_motion_state",
        "input_boolean.test_motion_2_state",
        "input_boolean.test_door_state",
        "input_boolean.test_light_state",
        "input_boolean.test_light_2_state",
        "input_boolean.test_switch_state",
    ]
    for entity in entities:
        with contextlib.suppress(Exception):
            await ha_instance.call_service("input_boolean", "turn_off", {"entity_id": entity})
    import asyncio

    await asyncio.sleep(1)
    yield


@pytest_asyncio.fixture
async def async_page():
    """Async Playwright page for UI tests."""
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        yield page
        await context.close()
        await browser.close()


@pytest_asyncio.fixture
async def logged_in_page(async_page):
    """Page with logged-in HA session."""
    import asyncio

    await async_page.goto(HA_BASE_URL)
    await async_page.wait_for_selector('input[name="username"]', timeout=30000)
    await async_page.fill('input[name="username"]', AUTOQA_USERNAME)
    await async_page.fill('input[name="password"]', AUTOQA_PASSWORD)
    await async_page.click('button[type="submit"]')
    await async_page.wait_for_selector("home-assistant", timeout=30000)
    await asyncio.sleep(2)
    yield async_page
