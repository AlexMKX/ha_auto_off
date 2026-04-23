"""Constants for the Auto Off integration."""

import json
from pathlib import Path

DOMAIN = "auto_off"


def _read_manifest_version() -> str:
    """Read version from manifest.json at import time.

    The manifest sits next to this file; read it synchronously once so
    entities can advertise the integration version as DeviceInfo.sw_version
    without doing async work in property getters.
    """
    try:
        manifest_path = Path(__file__).parent / "manifest.json"
        return json.loads(manifest_path.read_text())["version"]
    except Exception:
        return "unknown"


VERSION = _read_manifest_version()

# Config entry storage keys
CONF_GROUPS = "groups"
CONF_POLL_INTERVAL = "poll_interval"

# Service names and field names
SERVICE_SET_GROUP = "set_group"
SERVICE_DELETE_GROUP = "delete_group"
CONF_GROUP_NAME = "group_name"
CONF_TARGETS = "targets"
CONF_SENSORS = "sensors"
CONF_SENSOR_TEMPLATES = "sensor_templates"
CONF_DELAY = "delay"

# Platforms forwarded by async_setup_entry
PLATFORMS = ["sensor", "text"]

__all__ = [
    "DOMAIN",
    "VERSION",
    "CONF_GROUPS",
    "CONF_POLL_INTERVAL",
    "SERVICE_SET_GROUP",
    "SERVICE_DELETE_GROUP",
    "CONF_GROUP_NAME",
    "CONF_TARGETS",
    "CONF_SENSORS",
    "CONF_SENSOR_TEMPLATES",
    "CONF_DELAY",
    "PLATFORMS",
]
