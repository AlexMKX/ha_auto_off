import logging
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry, SOURCE_IMPORT
from .integration_manager import async_unload_integration

_LOGGER = logging.getLogger(__name__)

async def async_setup(hass: HomeAssistant, config):
    # Only save yaml_config for compatibility
    conf = config.get("auto_off")
    if conf is not None:
        hass.data.setdefault("auto_off", {})
        hass.data["auto_off"]["yaml_config"] = conf
        # Create config entry from YAML if it doesn't exist
        hass.async_create_task(
            hass.config_entries.flow.async_init(
                "auto_off", context={"source": SOURCE_IMPORT}, data=conf
            )
        )
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    await hass.config_entries.async_forward_entry_setups(entry, ["binary_sensor"])
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    await hass.config_entries.async_forward_entry_unload(entry, "binary_sensor")
    await async_unload_integration(hass, entry)
    return True
