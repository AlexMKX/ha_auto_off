from homeassistant import config_entries

class AutoOffConfigFlow(config_entries.ConfigFlow, domain="auto_off"):
    """Dummy config flow for YAML import only."""

    VERSION = 1

    async def async_step_import(self, import_config):
        """Handle import from YAML."""
        await self.async_set_unique_id("main")
        # Проверяем, есть ли уже такая entry
        entries = self._async_current_entries()
        if entries:
            return self.async_abort(reason="already_configured")
        import_config = dict(import_config)
        import_config["import_id"] = "main"
        return self.async_create_entry(title="Auto Off (YAML)", data=import_config)