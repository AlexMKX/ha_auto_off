"""Tests for auto_off text entities."""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from custom_components.auto_off.text import (
    SensorsTextEntity,
    TargetsTextEntity, 
    DelayTextEntity,
    GroupTextEntities,
)
from custom_components.auto_off.const import DOMAIN, CONF_SENSORS, CONF_TARGETS, CONF_DELAY


class TestSensorsTextEntity:
    """Test SensorsTextEntity class."""

    @pytest.fixture
    def mock_manager(self):
        """Create mock IntegrationManager."""
        manager = MagicMock()
        manager.update_group_config = AsyncMock()
        return manager

    @pytest.fixture
    def sensors_entity(self, hass, mock_manager, sample_group_config_dict):
        """Create a SensorsTextEntity instance."""
        return SensorsTextEntity(
            hass, mock_manager, "test_group", sample_group_config_dict
        )

    def test_init(self, sensors_entity, sample_group_config_dict):
        """Test entity initialization."""
        assert sensors_entity._group_name == "test_group"
        sensors = sample_group_config_dict[CONF_SENSORS]
        assert sensors_entity._attr_native_value == ", ".join(sensors)
        assert sensors_entity._attr_unique_id == f"{DOMAIN}_test_group_sensors"

    def test_device_info(self, sensors_entity):
        """Test device_info returns correct identifiers."""
        device_info = sensors_entity.device_info
        assert device_info is not None
        assert (DOMAIN, "test_group") in device_info["identifiers"]

    @pytest.mark.asyncio
    async def test_async_set_value(self, sensors_entity, mock_manager):
        """Test async_set_value updates config."""
        await sensors_entity.async_set_value("sensor.a, sensor.b")
        
        mock_manager.update_group_config.assert_called_once()
        call_args = mock_manager.update_group_config.call_args[0]
        assert call_args[0] == "test_group"
        assert call_args[1][CONF_SENSORS] == ["sensor.a", "sensor.b"]


class TestDelayTextEntity:
    """Test DelayTextEntity class."""

    @pytest.fixture
    def mock_manager(self):
        """Create mock IntegrationManager."""
        manager = MagicMock()
        manager.update_group_config = AsyncMock()
        return manager

    @pytest.fixture
    def delay_entity(self, hass, mock_manager, sample_group_config_dict):
        """Create a DelayTextEntity instance."""
        return DelayTextEntity(
            hass, mock_manager, "test_group", sample_group_config_dict
        )

    def test_init(self, delay_entity, sample_group_config_dict):
        """Test entity initialization - delay is in minutes."""
        delay_minutes = sample_group_config_dict[CONF_DELAY]
        assert delay_entity._attr_native_value == str(delay_minutes)
        assert delay_entity._attr_name == "Delay (minutes)"

    @pytest.mark.asyncio
    async def test_async_set_value_int(self, delay_entity, mock_manager):
        """Test async_set_value with integer (minutes)."""
        await delay_entity.async_set_value("10")
        
        mock_manager.update_group_config.assert_called_once()
        call_args = mock_manager.update_group_config.call_args[0]
        assert call_args[1][CONF_DELAY] == 10

    @pytest.mark.asyncio
    async def test_async_set_value_template(self, delay_entity, mock_manager):
        """Test async_set_value with template string."""
        template = "{{ states('input_number.delay') | int }}"
        await delay_entity.async_set_value(template)
        
        mock_manager.update_group_config.assert_called_once()
        call_args = mock_manager.update_group_config.call_args[0]
        assert call_args[1][CONF_DELAY] == template


class TestGroupTextEntities:
    """Test GroupTextEntities container."""

    @pytest.fixture
    def mock_manager(self):
        """Create mock IntegrationManager."""
        manager = MagicMock()
        manager.update_group_config = AsyncMock()
        return manager

    def test_get_all(self, hass, mock_manager, sample_group_config_dict):
        """Test get_all returns all entities."""
        group_texts = GroupTextEntities(
            hass, mock_manager, "test_group", sample_group_config_dict
        )
        
        entities = group_texts.get_all()
        assert len(entities) == 3
        assert isinstance(entities[0], SensorsTextEntity)
        assert isinstance(entities[1], TargetsTextEntity)
        assert isinstance(entities[2], DelayTextEntity)
