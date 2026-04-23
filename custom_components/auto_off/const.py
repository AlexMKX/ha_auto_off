"""Constants for the Auto Off integration."""

DOMAIN = "auto_off"

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
