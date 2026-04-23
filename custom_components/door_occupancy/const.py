"""Constants for the Door Occupancy integration."""

DOMAIN = "door_occupancy"
PLATFORMS = ["binary_sensor"]

CONF_POLL_INTERVAL = "poll_interval"
CONF_OCCUPANCY_TIMEOUT = "occupancy_timeout"

DEFAULT_POLL_INTERVAL = 30
DEFAULT_OCCUPANCY_TIMEOUT = 15

CONFIG_VERSION = 1
