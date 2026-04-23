"""Constants for the Door Occupancy integration."""

DOMAIN = "door_occupancy"

# Config entry storage keys
CONF_POLL_INTERVAL = "poll_interval"
CONF_OCCUPANCY_TIMEOUT = "occupancy_timeout"

# Defaults for config flow
DEFAULT_POLL_INTERVAL = 30
DEFAULT_OCCUPANCY_TIMEOUT = 15

# Platforms forwarded by async_setup_entry
PLATFORMS = ["binary_sensor"]

__all__ = [
    "DOMAIN",
    "CONF_POLL_INTERVAL",
    "CONF_OCCUPANCY_TIMEOUT",
    "DEFAULT_POLL_INTERVAL",
    "DEFAULT_OCCUPANCY_TIMEOUT",
    "PLATFORMS",
]
