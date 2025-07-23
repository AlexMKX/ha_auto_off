import logging
from typing import Any, Dict, List, Optional, Union, Callable
from homeassistant.core import HomeAssistant, State, Event
from homeassistant.helpers.template import Template
import asyncio
from pydantic import BaseModel, field_validator, ValidationError, model_validator
import datetime
from homeassistant.helpers.event import async_track_time_interval, async_track_state_change_event, async_track_template
from datetime import timedelta
import functools
from .door_occupancy import DoorOccupancyManager

_LOGGER = logging.getLogger(__name__)

#todo: оно почему-то постоянно ресетит дедлайн на проде, а главное - оно уходит в дедлуп т.к. дедлайн есть атрибут.
#todo: добавить логгирование - с данными события. 

class GroupConfig(BaseModel):
    sensors: List[str]
    targets: List[str]  # Изменил с Union[str, dict] на str
    delay: Union[int, str, None] = 0

    @field_validator('delay')
    @classmethod
    def validate_delay(cls, v):
        if v is None:
            return 0
        return v


class AutoOffConfig(BaseModel):
    groups: Dict[str, GroupConfig]

class IntegrationConfig(BaseModel):
    poll_interval: int = 15
    doors: Optional[dict] = None
    groups: Dict[str,GroupConfig]


class Sensor:
    def __init__(self, hass: HomeAssistant, sensor_def, on_state_change_callback):
        self.hass = hass
        self.raw = sensor_def
        self._on_change_callback = on_state_change_callback
        self._unsub = None
        self._is_template = self._detect_template()
        self._last_known_good_state: Optional[bool] = None  # Последнее валидное состояние

    def _detect_template(self) -> bool:
        """Определяет является ли sensor_def шаблоном"""
        return isinstance(self.raw, str) and '{{' in self.raw and '}}' in self.raw

    async def start_tracking(self):
        """Сам подписывается на изменения своего состояния"""
        if self._unsub is not None:
            return  # Уже подписан
            
        if self._is_template:
            await self._start_template_tracking()
        else:
            await self._start_entity_tracking()

    async def _start_template_tracking(self):
        """Подписывается на изменения шаблона"""
        try:
            # Инициализируем последнее валидное состояние
            self._last_known_good_state = await self._check_template_state()
            
            template = Template(str(self.raw), self.hass)
            self._unsub = async_track_template(
                self.hass,
                template,
                self._handle_template_change
            )
            _LOGGER.debug(f"Sensor template '{self.raw}' started tracking, initial state: {self._last_known_good_state}")
        except Exception as e:
            _LOGGER.error(f"Failed to track sensor template '{self.raw}': {e}")

    async def _start_entity_tracking(self):
        """Подписывается на изменения entity"""
        entity_id = self.get_entity_id()
        if not entity_id:
            _LOGGER.warning(f"Sensor '{self.raw}' is not a valid entity or template")
            return
            
        # Проверяем что сущность существует
        if self.hass.states.get(entity_id) is None:
            _LOGGER.warning(f"Sensor entity {entity_id} does not exist, skipping tracking")
            return
            
        try:
            # Инициализируем последнее валидное состояние
            self._last_known_good_state = await self._check_entity_state()
            
            self._unsub = async_track_state_change_event(
                self.hass, [entity_id], self._handle_entity_change
            )
            _LOGGER.debug(f"Sensor entity '{entity_id}' started tracking, initial state: {self._last_known_good_state}")
        except Exception as e:
            _LOGGER.error(f"Failed to track sensor entity '{entity_id}': {e}")

    async def _handle_entity_change(self, event):
        """САМ обрабатывает изменения entity"""
        entity_id = event.data.get("entity_id")
        new_state = event.data.get("new_state")
        
        # Игнорируем невалидные состояния
        if not new_state or new_state.state in ("unknown", "unavailable"):
            _LOGGER.debug(f"Sensor entity {entity_id} state is invalid ({new_state.state if new_state else 'None'}), ignoring")
            return
        
        # Получаем текущее валидное состояние сенсора
        try:
            current_sensor_state = await self._check_entity_state()
        except Exception as e:
            _LOGGER.error(f"Failed to check sensor {entity_id} state: {e}")
            return
        
        # Сравниваем с последним известным состоянием
        if self._last_known_good_state == current_sensor_state:
            _LOGGER.debug(f"Sensor entity {entity_id} state unchanged ({current_sensor_state}), ignoring")
            return
        
        # Реальное изменение состояния!
        old_state_str = "None" if self._last_known_good_state is None else str(self._last_known_good_state)
        _LOGGER.info(f"Sensor entity {entity_id} state changed: {old_state_str} -> {current_sensor_state}")
        
        # Обновляем последнее валидное состояние
        old_known_state = self._last_known_good_state
        self._last_known_good_state = current_sensor_state
        
        # Уведомляем группу о реальном изменении
        if self._on_change_callback:
            await self._on_change_callback(self, old_known_state, current_sensor_state)

    async def _handle_template_change(self, entity_id, from_state, to_state):
        """САМ обрабатывает изменения шаблона"""
        # Получаем текущее валидное состояние шаблона
        try:
            current_sensor_state = await self._check_template_state()
        except Exception as e:
            _LOGGER.error(f"Failed to check template sensor '{self.raw}' state: {e}")
            return
        
        # Сравниваем с последним известным состоянием
        if self._last_known_good_state == current_sensor_state:
            _LOGGER.debug(f"Sensor template '{self.raw}' state unchanged ({current_sensor_state}), ignoring")
            return
        
        # Реальное изменение состояния!
        old_state_str = "None" if self._last_known_good_state is None else str(self._last_known_good_state)
        _LOGGER.info(f"Sensor template '{self.raw}' changed: {old_state_str} -> {current_sensor_state}")
        
        # Обновляем последнее валидное состояние
        old_known_state = self._last_known_good_state
        self._last_known_good_state = current_sensor_state
        
        # Уведомляем группу о реальном изменении
        if self._on_change_callback:
            await self._on_change_callback(self, old_known_state, current_sensor_state)

    async def is_on(self):
        """Проверяет включен ли сенсор"""
        if self._is_template:
            return await self._check_template_state()
        else:
            return await self._check_entity_state()

    async def _check_template_state(self) -> bool:
        """Проверяет состояние шаблона"""
        try:
            tpl = Template(str(self.raw), self.hass)
            rendered = tpl.async_render()
            if isinstance(rendered, bool):
                _LOGGER.debug(f"Template sensor '{self.raw}' rendered to: {rendered}")
                return rendered
        except Exception as e:
            _LOGGER.error(f"Template sensor '{self.raw}' failed to render: {e}")
        return False

    async def _check_entity_state(self) -> bool:
        """Проверяет состояние entity"""
        entity_id = self.get_entity_id()
        if not entity_id:
            _LOGGER.info(f"Sensor '{self.raw}' is not a valid entity")
            return False
            
        state = self.hass.states.get(entity_id)
        if isinstance(state, State):
            result = state.state in ("on", "true", "1")
            _LOGGER.debug(f"Entity sensor '{entity_id}' state: {state.state} -> {result}")
            return result
        
        _LOGGER.info(f"Sensor entity '{entity_id}' state not found")
        return False

    def get_entity_id(self) -> Optional[str]:
        """Возвращает entity_id, если sensor_def является сущностью, а не шаблоном"""
        if not self._is_template and isinstance(self.raw, str):
            return self.raw
        return None

    async def stop_tracking(self):
        """Отписывается от событий"""
        if self._unsub:
            self._unsub()
            self._unsub = None
            _LOGGER.debug(f"Sensor '{self.raw}' stopped tracking")


class Target:
    def __init__(self, hass: HomeAssistant, entity_id: str, on_state_change_callback):
        self.hass = hass
        self.entity_id = entity_id
        self._on_change_callback = on_state_change_callback
        self._unsub = None
        self._deadline = None
        self._last_known_good_state: Optional[bool] = None  # Последнее валидное состояние

    async def start_tracking(self):
        """Сам подписывается на изменения своего состояния"""
        if self._unsub is not None:
            return  # Уже подписан
            
        # Проверяем что сущность существует
        if self.hass.states.get(self.entity_id) is None:
            _LOGGER.warning(f"Target entity {self.entity_id} does not exist, skipping tracking")
            return
        
        # Инициализируем последнее валидное состояние
        self._last_known_good_state = await self.is_on()
            
        self._unsub = async_track_state_change_event(
            self.hass, [self.entity_id], self._handle_my_changes
        )
        _LOGGER.debug(f"Target {self.entity_id} started tracking its state, initial state: {self._last_known_good_state}")

    async def _handle_my_changes(self, event):
        """САМ решает что важно, а что нет"""
        new_state = event.data.get("new_state")
        
        # Игнорируем невалидные состояния
        if not new_state or new_state.state in ("unknown", "unavailable"):
            _LOGGER.debug(f"Target {self.entity_id} state is invalid ({new_state.state if new_state else 'None'}), ignoring")
            return
        
        # Получаем текущее валидное состояние target
        current_target_state = await self.is_on()
        
        # Сравниваем с последним известным состоянием
        if self._last_known_good_state == current_target_state:
            _LOGGER.debug(f"Target {self.entity_id} state unchanged ({current_target_state}), ignoring")
            return
        
        # Реальное изменение состояния!
        old_state_str = "None" if self._last_known_good_state is None else str(self._last_known_good_state)
        _LOGGER.info(f"Target {self.entity_id} state changed: {old_state_str} -> {current_target_state}")
        
        # Обновляем последнее валидное состояние
        old_known_state = self._last_known_good_state
        self._last_known_good_state = current_target_state
        
        # Уведомляем группу о ВАЖНОМ изменении состояния
        if self._on_change_callback:
            await self._on_change_callback(self, old_known_state, current_target_state)

    async def is_on(self):
        """Проверяет включен ли target"""
        state = self.hass.states.get(self.entity_id)
        if state is None:
            return False
        # Target считается включенной, если она НЕ unavailable, НЕ unknown и НЕ 'off'
        return state.state not in ("unavailable", "unknown", "off")

    def set_deadline(self, deadline_timestamp: Optional[float]):
        """САМ обновляет свой атрибут дедлайна"""
        state = self.hass.states.get(self.entity_id)
        if state is None:
            return
            
        current_deadline = state.attributes.get("auto_off_deadline")
        is_on = state.state == "on"
        
        if is_on and deadline_timestamp is not None:
            # Корректно вычисляем реальное время дедлайна
            now_real = datetime.datetime.now().astimezone()
            now_monotonic = self.hass.loop.time()
            seconds_until_deadline = deadline_timestamp - now_monotonic
            real_deadline = now_real + datetime.timedelta(seconds=seconds_until_deadline)
            new_deadline = real_deadline.isoformat()
            
            # Обновляем только если дедлайн действительно изменился
            if current_deadline != new_deadline:
                attrs = dict(state.attributes)
                attrs["auto_off_deadline"] = new_deadline
                self.hass.states.async_set(self.entity_id, state.state, attrs)
                _LOGGER.debug(f"Target {self.entity_id} deadline updated: {new_deadline}")
        elif not is_on and current_deadline is not None:
            # Сбрасываем атрибут в None только если он сейчас не None
            attrs = dict(state.attributes)
            attrs["auto_off_deadline"] = None
            self.hass.states.async_set(self.entity_id, state.state, attrs)
            _LOGGER.debug(f"Target {self.entity_id} deadline cleared")

    async def turn_off(self):
        """САМ себя выключает"""
        domain = self.entity_id.split(".")[0]
        service = "turn_off"
        try:
            await self.hass.services.async_call(domain, service, {"entity_id": self.entity_id}, blocking=True)
            _LOGGER.info(f"Target {self.entity_id} turned OFF via {domain}.{service}")
        except Exception as e:
            _LOGGER.error(f"Failed to turn off target {self.entity_id}: {e}")

    async def stop_tracking(self):
        """Отписывается от событий"""
        if self._unsub:
            self._unsub()
            self._unsub = None
            _LOGGER.debug(f"Target {self.entity_id} stopped tracking its state")


class SensorGroup:
    def __init__(self, hass: HomeAssistant, group_id: str, config: GroupConfig):
        self.hass = hass
        self.group_id = group_id
        self._config = config  # immutable
        self._sensors: List[Sensor] = []
        self._targets: List[Target] = []
        self._timer: Optional[asyncio.TimerHandle] = None
        self._timer_deadline: Optional[float] = None  # timestamp when timer fires
        self._last_all_sensors_off: Optional[bool] = None
        # Критическая секция для защиты от race conditions
        self._lock = asyncio.Lock()
        # Отслеживание предыдущих состояний для определения переходов
        self._last_any_target_on: Optional[bool] = None
        self._init_from_config()

    def _init_from_config(self):
        self._sensors = []
        self._targets = []
        for sensor in self._config.sensors:
            try:
                sensor_obj = Sensor(self.hass, sensor, self._on_sensor_state_change)
                self._sensors.append(sensor_obj)
                # Sensors сами начинают отслеживать свои изменения
                asyncio.create_task(sensor_obj.start_tracking())
            except Exception as e:
                _LOGGER.error(f"Sensor '{sensor}' is invalid and will be ignored: {e}")
        for t in self._config.targets:
            target = Target(self.hass, t, self._on_target_state_change)
            self._targets.append(target)
            # Targets сами начинают отслеживать свои изменения
            asyncio.create_task(target.start_tracking())

    async def all_sensors_off(self):
        sensors_on = []
        for s in self._sensors:
            is_on = await s.is_on()
            if is_on:
                sensors_on.append(getattr(s, 'raw', str(s)))
        
        if sensors_on:
            _LOGGER.debug(f"[Group {self.group_id}] Sensors still ON: {sensors_on}")
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
            return int(rendered) * 60
        except Exception:
            raise ValueError(f"Failed to render delay template: {self._config.delay}, result: {rendered}")

    async def check_and_set_deadline(self):
        """Главный метод проверки и установки дедлайна"""
        async with self._lock:
            # Собираем текущее состояние
            current_state = await self._collect_current_state()
            
            # Логируем состояние
            self._log_current_state(current_state)
            
            # Инициализация при первом запуске
            if self._is_first_run():
                self._handle_first_run(current_state)
                return
            
            # Логируем переходы состояний
            await self._log_state_transitions(current_state)
            
            # Принимаем решения о дедлайне
            await self._handle_deadline_logic(current_state)
            
            # Сохраняем текущее состояние как предыдущее
            self._update_last_states(current_state)
            
            # Обновляем дедлайны для всех targets
            self._update_targets_deadline()

    async def _collect_current_state(self) -> dict:
        """Собирает текущее состояние сенсоров и targets"""
        target_on = await self.any_target_on()
        all_sensors_off = await self.all_sensors_off()
        
        return {
            'target_on': target_on,
            'all_sensors_off': all_sensors_off,
            'human_deadline': self._get_human_deadline()
        }

    def _get_human_deadline(self) -> str:
        """Преобразует deadline в человеческий формат для логирования"""
        if self._timer_deadline is None:
            return "None"
        
        now_real = datetime.datetime.now().astimezone()
        now_monotonic = self.hass.loop.time()
        seconds_until_deadline = self._timer_deadline - now_monotonic
        real_deadline = now_real + datetime.timedelta(seconds=seconds_until_deadline)
        return real_deadline.isoformat()

    def _log_current_state(self, state: dict):
        """Логирует текущее состояние группы"""
        _LOGGER.info(f"[Checking Group {self.group_id}] target_on={state['target_on']}, "
                    f"all_sensors_off={state['all_sensors_off']}, deadline={state['human_deadline']}")

    def _is_first_run(self) -> bool:
        """Проверяет является ли это первый запуск"""
        return self._last_all_sensors_off is None or self._last_any_target_on is None

    def _handle_first_run(self, state: dict):
        """Обрабатывает первый запуск системы"""
        _LOGGER.info(f"[Group {self.group_id}] First run initialization")
        self._last_all_sensors_off = state['all_sensors_off']
        self._last_any_target_on = state['target_on']
        
        # Если при старте target включен и все сенсоры выключены → устанавливаем дедлайн
        if state['target_on'] and state['all_sensors_off'] and self._timer_deadline is None:
            asyncio.create_task(self._set_deadline_from_delay("startup"))

    async def _set_deadline_from_delay(self, reason: str):
        """Устанавливает дедлайн на основе delay из конфига"""
        delay = await self.get_delay()
        now = self.hass.loop.time()
        new_deadline = now + delay
        self._start_deadline(force_deadline=new_deadline)
        
        now_real = datetime.datetime.now().astimezone()
        human_deadline = (now_real + datetime.timedelta(seconds=delay)).isoformat()
        _LOGGER.info(f"[Group {self.group_id}] Deadline set by {reason}: {delay}s | New deadline: {new_deadline} ({human_deadline})")

    async def _log_state_transitions(self, state: dict):
        """Логирует подробную информацию о переходах состояний"""
        # Собираем статусы для логирования
        sensor_statuses = []
        for s in self._sensors:
            try:
                status = await s.is_on()
            except Exception as e:
                status = f"error: {e}"
            sensor_statuses.append(f"{getattr(s, 'raw', str(s))}: {status}")
        
        target_statuses = []
        for t in self._targets:
            try:
                status = await t.is_on()
            except Exception as e:
                status = f"error: {e}"
            target_statuses.append(f"{getattr(t, 'entity_id', str(t))}: {status}")

        _LOGGER.info(f"[Group {self.group_id}] Sensors: {sensor_statuses} | Targets: {target_statuses}")
        _LOGGER.info(f"[Group {self.group_id}] State transition: "
                    f"last_all_sensors_off={self._last_all_sensors_off} -> all_sensors_off={state['all_sensors_off']}, "
                    f"last_any_target_on={self._last_any_target_on} -> any_target_on={state['target_on']}")

    async def _handle_deadline_logic(self, state: dict):
        """Основная логика принятия решений о дедлайне"""
        # Если target выключен → всегда отменяем дедлайн
        if not state['target_on']:
            if self._cancel_deadline():
                _LOGGER.info(f"[Group {self.group_id}] Deadline cancelled: target is off")
            return

        # Target включен - анализируем переходы состояний
        transitions = self._analyze_state_transitions(state)
        
        if transitions['target_turned_on'] and state['all_sensors_off']:
            await self._set_deadline_from_delay("target turning ON")
        elif transitions['sensors_turned_off'] and state['target_on']:
            await self._set_deadline_from_delay("sensors turning OFF")
        elif transitions['sensors_turned_on']:
            if self._cancel_deadline():
                _LOGGER.info(f"[Group {self.group_id}] Deadline cancelled: sensor turned on")

    def _analyze_state_transitions(self, state: dict) -> dict:
        """Анализирует переходы состояний"""
        return {
            'target_turned_on': self._last_any_target_on is False and state['target_on'],
            'sensors_turned_off': self._last_all_sensors_off is False and state['all_sensors_off'],
            'sensors_turned_on': self._last_all_sensors_off is True and not state['all_sensors_off']
        }

    def _update_last_states(self, state: dict):
        """Обновляет предыдущие состояния"""
        self._last_all_sensors_off = state['all_sensors_off']
        self._last_any_target_on = state['target_on']

    def _update_targets_deadline(self):
        """
        Обновляет дедлайны для всех targets в группе.
        Каждый target сам решает нужно ли обновлять свой атрибут auto_off_deadline.
        """
        for target in self._targets:
            target.set_deadline(self._timer_deadline)

    def _start_deadline(self, force_deadline=None):
        # Этот метод вызывается только из check_and_set_deadline, который уже под lock'ом
        if self._cancel_deadline():
            _LOGGER.info("Previous deadline was cancelled.")
        loop = self.hass.loop
        delay = 0
        if force_deadline is not None:
            now = loop.time()
            delay = max(0, force_deadline - now)
        if delay > 0:
            self._timer = loop.call_later(delay, lambda: asyncio.create_task(self._turn_off_targets()))
            self._timer_deadline = loop.time() + delay
            _LOGGER.info(f"[{self.group_id}] All sensors are off/false. Deadline delay started.")
        else:
            asyncio.create_task(self._turn_off_targets())
            self._timer_deadline = None
            _LOGGER.info(f"[{self.group_id}] All sensors are off/false. Turning off targets immediately.")

    def _cancel_deadline(self) -> bool:
        # Этот метод вызывается только из check_and_set_deadline, который уже под lock'ом
        if self._timer:
            self._timer.cancel()
            self._timer = None
            return True
        self._timer_deadline = None
        return False

    async def _turn_off_targets(self):
        tasks = []
        for target in self._targets:
            tasks.append(target.turn_off())
        if tasks:
            await asyncio.gather(*tasks)
        _LOGGER.info("All targets turned off after deadline.")

    async def async_unload(self):
        """Очистка ресурсов группы"""
        async with self._lock:
            # Отменяем таймер
            self._cancel_deadline()
            
            # Sensors сами отписываются от своих событий
            for sensor in self._sensors:
                await sensor.stop_tracking()
            
            # Targets сами отписываются от своих событий
            for target in self._targets:
                await target.stop_tracking()
            
            _LOGGER.info(f"[Group {self.group_id}] Unloaded successfully")

    async def _on_target_state_change(self, target: Target, old_state: Optional[bool], new_state: Optional[bool]):
        """Обработчик изменений состояний targets, который передается в Target"""
        # Этот метод вызывается из Target._handle_my_changes
        # Он вызывается только когда происходит РЕАЛЬНОЕ изменение состояния target
        # (old_state != new_state), игнорируя промежуточные unknown/unavailable состояния
        _LOGGER.debug(f"Target {getattr(target, 'entity_id', 'unknown')} state change: {old_state} -> {new_state}")
        await self.check_and_set_deadline()

    async def _on_sensor_state_change(self, sensor: Sensor, old_state: Optional[bool], new_state: Optional[bool]):
        """Обработчик изменений состояний сенсоров, который передается в Sensor"""
        # Этот метод вызывается из Sensor._handle_entity_change или Sensor._handle_template_change
        # Он вызывается только когда происходит РЕАЛЬНОЕ изменение состояния сенсора
        # (old_state != new_state), игнорируя промежуточные unknown/unavailable состояния
        _LOGGER.debug(f"Sensor {getattr(sensor, 'raw', 'unknown')} state change: {old_state} -> {new_state}")
        await self.check_and_set_deadline()


class AutoOffManager:
    """
    Manager for automatic device turn-off by events and timeout.
    """

    def __init__(self, hass: HomeAssistant, config: Dict[str, GroupConfig]) -> None:
        self.hass = hass
        self.config = config
        self._targets_state: Dict[str, Dict[str, Any]] = {}
        self._groups: Dict[str, SensorGroup] = {}
        self._tasks: List[Any] = []
        self._init_groups()

    def _init_groups(self):
        """Initialize sensor groups from configuration."""
        # Очищаем существующие группы
        for group in self._groups.values():
            try:
                asyncio.create_task(group.async_unload())
            except Exception as e:
                _LOGGER.error(f"Error unloading group: {e}")
        
        self._groups.clear()
        for group_id, group_config in self.config.items():
            try:
                self._groups[group_id] = SensorGroup(self.hass, group_id, group_config)
                _LOGGER.info(f"Initialized auto-off group '{group_id}' with {len(group_config.sensors)} sensors and {len(group_config.targets)} targets")
            except Exception as e:
                _LOGGER.error(f"Failed to initialize auto-off group '{group_id}': {e}")

    async def periodic_worker(self):
        _LOGGER.info(f"Periodic worker started.")
        try:
            for group in self._groups.values():
                # Проверяем состояния и устанавливаем дедлайны
                await group.check_and_set_deadline()
        except Exception as e:
            _LOGGER.error(f"Scheduled config reload failed: {e}")

    def update_config(self, new_config: Dict[str, GroupConfig]):
        """Update configuration and reinitialize groups."""
        self.config = new_config
        self._init_groups()
        _LOGGER.info(f"AutoOffManager configuration updated with {len(new_config)} groups")

    async def async_unload(self):
        """Clean up resources."""
        for group in self._groups.values():
            await group.async_unload()
        self._groups.clear()
        self._tasks.clear()
