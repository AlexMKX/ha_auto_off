import logging
from typing import Any, Dict, List, Optional, Union
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers.template import Template
import asyncio
from pydantic import BaseModel, field_validator, ValidationError

_LOGGER = logging.getLogger(__name__)


class GroupConfig(BaseModel):
    sensors: List[str]
    targets: List[Union[str, dict]]
    delay: Union[int, str, None] = 0

    @field_validator('delay')
    @classmethod
    def validate_delay(cls, v):
        if v is None:
            return 0
        return v


class ConfigModel(BaseModel):
    sensors: List[GroupConfig]


class Sensor:
    def __init__(self, hass: HomeAssistant, sensor_def):
        self.hass = hass
        self.raw = sensor_def

    async def is_on(self):
        # 1. Пробуем рендерить как шаблон
        try:
            tpl = Template(str(self.raw), self.hass)
            rendered = tpl.async_render()
            if isinstance(rendered, bool):
                return rendered
        except Exception:
            pass
        # 2. Пробуем как entity
        state = self.hass.states.get(self.raw)
        if isinstance(state, State):
            return state.state in ("on", "true", "1")
        # 3. Если ни то, ни другое — ошибка
        _LOGGER.info(f"Sensor '{self.raw}' is not a valid template or entity")
        return False


class Target:
    def __init__(self, hass: HomeAssistant, entity_id: str):
        self.hass = hass
        self.entity_id = entity_id

    async def is_on(self):
        state = self.hass.states.get(self.entity_id)
        return state is not None and state.state == "on"


class SensorGroup:
    def __init__(self, hass: HomeAssistant, group_id: str, config: GroupConfig):
        self.hass = hass
        self.group_id = group_id
        self._config = config  # immutable
        self._sensors: List[Sensor] = []
        self._targets: List[Target] = []
        self._unsubs: List[Any] = []
        self._timer: Optional[asyncio.TimerHandle] = None
        self._timer_deadline: Optional[float] = None  # timestamp when timer fires
        self._last_all_sensors_off: Optional[bool] = None
        self._init_from_config()

    def _init_from_config(self):
        self._sensors = []
        self._targets = []
        for sensor in self._config.sensors:
            try:
                self._sensors.append(Sensor(self.hass, sensor))
            except Exception as e:
                _LOGGER.error(f"Sensor '{sensor}' is invalid and will be ignored: {e}")
        for t in self._config.targets:
            self._targets.append(Target(self.hass, t))

    async def refresh(self):
        for unsub in self._unsubs:
            try:
                unsub()
            except Exception:
                pass
        self._unsubs = []
        self._sensors = []
        self._targets = []
        for sensor in self._config.sensors:
            try:
                self._sensors.append(Sensor(self.hass, sensor))
            except Exception as e:
                _LOGGER.error(f"Sensor '{sensor}' is invalid and will be ignored: {e}")
        for t in self._config.targets:
            self._targets.append(Target(self.hass, t))
        _LOGGER.info(f"Refreshed sensors: {self._sensors}")
        # Логируем дедлайн и статусы
        deadline = None
        if self._timer_deadline is not None:
            now = self.hass.loop.time()
            deadline = max(0, self._timer_deadline - now)
        statuses = []
        for s in self._sensors:
            statuses.append(str(s))
        _LOGGER.info(
            f"[Group {self.group_id}] Deadline: {deadline}, Sensors: {statuses}, Targets: {[str(t) for t in self._targets]}")

    async def all_sensors_off(self):
        for s in self._sensors:
            if await s.is_on():
                return False
        return True

    async def any_target_on(self):
        for t in self._targets:
            if await t.is_on():
                return True
        return False

    async def get_delay(self) -> int:
        tpl = Template(str(self._config.delay), self.hass)
        rendered = tpl.async_render()
        try:
            return int(rendered)
        except Exception:
            raise ValueError(f"Failed to render delay template: {self._config.delay}, result: {rendered}")

    async def check_and_set_deadline(self):
        target_on = await self.any_target_on()
        all_sensors_off = await self.all_sensors_off()

        # Случай старта: если таргет включен, все сенсоры выключены, дедлайн не установлен
        if target_on and all_sensors_off and self._last_all_sensors_off and self._timer_deadline is None:
            delay = await self.get_delay()
            now = self.hass.loop.time()
            new_deadline = now + delay
            self._start_deadline(force_deadline=new_deadline)
            _LOGGER.info(f"[Group {self.group_id}] Deadline set at startup: {delay}s")

        if target_on:
            if self._last_all_sensors_off is False and all_sensors_off:
                # Переход any_sensor_on -> all_sensors_off: ставим дедлайн
                delay = await self.get_delay()
                now = self.hass.loop.time()
                new_deadline = now + delay
                self._start_deadline(force_deadline=new_deadline)
                _LOGGER.info(f"[Group {self.group_id}] Deadline set by transition to all_sensors_off: {delay}s")
            elif self._last_all_sensors_off is True and not all_sensors_off:
                # Переход all_sensors_off -> any_sensor_on: отменяем дедлайн
                self._cancel_deadline()
                _LOGGER.info(f"[Group {self.group_id}] Deadline cancelled by sensor ON")
        else:
            # Если таргет выключен — всегда отменяем дедлайн
            self._cancel_deadline()

        self._last_all_sensors_off = all_sensors_off

    def _start_deadline(self, force_deadline=None):
        self._cancel_deadline()
        loop = self.hass.loop
        delay = 0
        if force_deadline is not None:
            now = loop.time()
            delay = max(0, force_deadline - now)
        if delay > 0:
            self._timer = loop.call_later(delay, lambda: asyncio.create_task(self._turn_off_targets()))
            self._timer_deadline = loop.time() + delay
            _LOGGER.info("All sensors are off/false. Deadline %ss started.", delay)
        else:
            asyncio.create_task(self._turn_off_targets())
            self._timer_deadline = None
            _LOGGER.info("All sensors are off/false. Turning off targets immediately.")

    def _cancel_deadline(self):
        if self._timer:
            self._timer.cancel()
            self._timer = None
        self._timer_deadline = None
        _LOGGER.info("Timer cancelled (at least one sensor is on/true)")

    async def _turn_off_targets(self):
        tasks = []
        for target in self._targets:
            tasks.append(self._turn_off_target(target))
        if tasks:
            await asyncio.gather(*tasks)
        _LOGGER.info("All targets turned off after deadline.")

    async def _turn_off_target(self, target: Target) -> None:
        domain: str = target.entity_id.split(".")[0]
        service: str = "turn_off"
        try:
            await self.hass.services.async_call(domain, service, {"entity_id": target.entity_id}, blocking=True)
            _LOGGER.info("%s turned OFF via %s.%s", target.entity_id, domain, service)
        except Exception as e:
            _LOGGER.error("Failed to turn off %s: %s", target.entity_id, e)


class AutoOffManager:
    """
    Менеджер автоматического выключения устройств по событиям и тайм-ауту.
    """
    hass: HomeAssistant
    config: Dict[str, Any]
    _targets_state: Dict[str, Dict[str, Any]]
    _groups: Dict[str, SensorGroup]
    _tasks: List[Any]

    def __init__(self, hass: HomeAssistant, config: Dict[str, Any]) -> None:
        self.hass = hass
        self.config = config
        self._targets_state: Dict[str, Dict[str, Any]] = {}
        self._groups: Dict[str, SensorGroup] = {}
        self._tasks: List[Any] = []

    async def async_initialize(self) -> None:
        await self._parse_config()
        asyncio.create_task(self.async_schedule_config_reload())
        _LOGGER.info("AutoOffManager initialized")

    async def _parse_config(self) -> None:
        try:
            config_model = ConfigModel.model_validate(self.config)
        except ValidationError as e:
            _LOGGER.error(f"Config validation error: {e}")
            raise
        self._groups = {}
        new_groups = {}
        for idx, group in enumerate(config_model.sensors):
            group_id = f"group_{idx}"
            if group_id not in self._groups:
                new_groups[group_id] = SensorGroup(self.hass, group_id, group)
            else:
                new_groups[group_id] = self._groups[group_id]
            await new_groups[group_id].refresh()
        self._groups = new_groups

    async def async_schedule_config_reload(self) -> None:
        while True:
            try:
                # Проверяем дедлайны для всех групп
                for group in self._groups.values():
                    await group.check_and_set_deadline()
                _LOGGER.debug("Config reloaded by schedule")
            except Exception as e:
                _LOGGER.error(f"Scheduled config reload failed: {e}")
            await asyncio.sleep(60)
