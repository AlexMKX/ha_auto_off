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

_LOGGER = logging.getLogger(__name__)

#todo: it constantly resets the deadline in production, and gets into a loop because deadline is an attribute.
#todo: add logging - with event data. 

class GroupConfig(BaseModel):
    sensors: List[str]
    targets: List[str]  # Changed from Union[str, dict] to str
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
        self._last_known_good_state: Optional[bool] = None  # Last valid state

    def _detect_template(self) -> bool:
        """Determines if sensor_def is a template"""
        return isinstance(self.raw, str) and '{{' in self.raw and '}}' in self.raw

    async def start_tracking(self):
        """Subscribes to its own state changes"""
        if self._unsub is not None:
            return  # Already subscribed
            
        if self._is_template:
            await self._start_template_tracking()
        else:
            await self._start_entity_tracking()

    async def _start_template_tracking(self):
        """Subscribes to template changes"""
        try:
            # Initialize last valid state
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
        """Subscribes to entity changes"""
        entity_id = self.get_entity_id()
        if not entity_id:
            _LOGGER.warning(f"Sensor '{self.raw}' is not a valid entity or template")
            return
            
        # Check that entity exists
        if self.hass.states.get(entity_id) is None:
            _LOGGER.warning(f"Sensor entity {entity_id} does not exist, skipping tracking")
            return
            
        try:
            # Initialize last valid state
            self._last_known_good_state = await self._check_entity_state()
            
            self._unsub = async_track_state_change_event(
                self.hass, [entity_id], self._handle_entity_change
            )
            _LOGGER.debug(f"Sensor entity '{entity_id}' started tracking, initial state: {self._last_known_good_state}")
        except Exception as e:
            _LOGGER.error(f"Failed to track sensor entity '{entity_id}': {e}")

    async def _handle_entity_change(self, event):
        """Handles entity changes"""
        entity_id = event.data.get("entity_id")
        new_state = event.data.get("new_state")
        
        # Ignore invalid states
        if not new_state or new_state.state in ("unknown", "unavailable"):
            _LOGGER.debug(f"Sensor entity {entity_id} state is invalid ({new_state.state if new_state else 'None'}), ignoring")
            return
        
        # Get current valid sensor state
        try:
            current_sensor_state = await self._check_entity_state()
        except Exception as e:
            _LOGGER.error(f"Failed to check sensor {entity_id} state: {e}")
            return
        
        # Compare with last known state
        if self._last_known_good_state == current_sensor_state:
            _LOGGER.debug(f"Sensor entity {entity_id} state unchanged ({current_sensor_state}), ignoring")
            return
        
        # Real state change!
        old_state_str = "None" if self._last_known_good_state is None else str(self._last_known_good_state)
        _LOGGER.info(f"Sensor entity {entity_id} state changed: {old_state_str} -> {current_sensor_state}")
        
        # Update last valid state
        old_known_state = self._last_known_good_state
        self._last_known_good_state = current_sensor_state
        
        # Notify group about real change
        if self._on_change_callback:
            await self._on_change_callback(self, old_known_state, current_sensor_state)

    async def _handle_template_change(self, entity_id, from_state, to_state):
        """Handles template changes"""
        # Get current valid template state
        try:
            current_sensor_state = await self._check_template_state()
        except Exception as e:
            _LOGGER.error(f"Failed to check template sensor '{self.raw}' state: {e}")
            return
        
        # Compare with last known state
        if self._last_known_good_state == current_sensor_state:
            _LOGGER.debug(f"Sensor template '{self.raw}' state unchanged ({current_sensor_state}), ignoring")
            return
        
        # Real state change!
        old_state_str = "None" if self._last_known_good_state is None else str(self._last_known_good_state)
        _LOGGER.info(f"Sensor template '{self.raw}' changed: {old_state_str} -> {current_sensor_state}")
        
        # Update last valid state
        old_known_state = self._last_known_good_state
        self._last_known_good_state = current_sensor_state
        
        # Notify group about real change
        if self._on_change_callback:
            await self._on_change_callback(self, old_known_state, current_sensor_state)

    async def is_on(self):
        """Checks if sensor is on"""
        if self._is_template:
            return await self._check_template_state()
        else:
            return await self._check_entity_state()

    async def _check_template_state(self) -> bool:
        """Checks template state"""
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
        """Checks entity state"""
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
        """Returns entity_id if sensor_def is an entity, not a template"""
        if not self._is_template and isinstance(self.raw, str):
            return self.raw
        return None

    async def stop_tracking(self):
        """Unsubscribes from events"""
        if self._unsub:
            self._unsub()
            self._unsub = None
            _LOGGER.debug(f"Sensor '{self.raw}' stopped tracking")


class Target:
    def __init__(self, hass: HomeAssistant, target_def: str, on_state_change_callback):
        self.hass = hass
        self.raw = target_def  # Can store either entity_id or template
        self._on_change_callback = on_state_change_callback
        self._unsub_list = []  # List of subscriptions for multiple entity_ids
        self._deadline = None
        self._last_known_good_state: Optional[bool] = None  # Last valid state
        self._is_template = self._detect_template()
        self._current_entity_ids: List[str] = []  # Current list of entity_ids

    def _detect_template(self) -> bool:
        """Determines if target_def is a template"""
        return isinstance(self.raw, str) and '{{' in self.raw and '}}' in self.raw

    async def _get_entity_ids(self) -> List[str]:
        """Gets list of entity_ids from template or returns single entity_id"""
        if self._is_template:
            return await self._render_template_entities()
        else:
            return [self.raw] if self.raw else []

    async def _render_template_entities(self) -> List[str]:
        """Renders template and returns list of entity_ids"""
        try:
            tpl = Template(str(self.raw), self.hass)
            rendered = tpl.async_render()
            
            # Template should return a list
            if isinstance(rendered, list):
                # Filter only valid entity_ids
                entity_ids = [str(e) for e in rendered if isinstance(e, str) and '.' in str(e)]
                _LOGGER.debug(f"Template target '{self.raw}' rendered to entities: {entity_ids}")
                return entity_ids
            else:
                _LOGGER.warning(f"Template target '{self.raw}' rendered to non-list: {rendered}")
                return []
        except Exception as e:
            _LOGGER.error(f"Template target '{self.raw}' failed to render: {e}")
            return []

    async def start_tracking(self):
        """Subscribes to its own state changes"""
        if self._unsub_list:
            return  # Already subscribed
        
        # Get current list of entity_ids
        self._current_entity_ids = await self._get_entity_ids()
        
        if not self._current_entity_ids:
            _LOGGER.warning(f"Target '{self.raw}' has no valid entities, skipping tracking")
            return
        
        # Check that all entities exist
        valid_entities = []
        for entity_id in self._current_entity_ids:
            if self.hass.states.get(entity_id) is not None:
                valid_entities.append(entity_id)
            else:
                _LOGGER.warning(f"Target entity {entity_id} does not exist, skipping")
        
        if not valid_entities:
            _LOGGER.warning(f"Target '{self.raw}' has no existing entities, skipping tracking")
            return
        
        self._current_entity_ids = valid_entities
        
        # Initialize last valid state
        self._last_known_good_state = await self.is_on()
        
        # Subscribe to changes for each entity
        for entity_id in self._current_entity_ids:
            unsub = async_track_state_change_event(
                self.hass, [entity_id], self._handle_my_changes
            )
            self._unsub_list.append(unsub)
        
        _LOGGER.debug(f"Target '{self.raw}' started tracking {len(self._current_entity_ids)} entities, initial state: {self._last_known_good_state}")

    async def _handle_my_changes(self, event):
        """Decides what's important and what's not"""
        entity_id = event.data.get("entity_id")
        new_state = event.data.get("new_state")
        
        # Ignore invalid states
        if not new_state or new_state.state in ("unknown", "unavailable"):
            _LOGGER.debug(f"Target entity {entity_id} state is invalid ({new_state.state if new_state else 'None'}), ignoring")
            return
        
        # Get current valid state of all targets
        current_target_state = await self.is_on()
        
        # Compare with last known state
        if self._last_known_good_state == current_target_state:
            _LOGGER.debug(f"Target '{self.raw}' overall state unchanged ({current_target_state}), ignoring")
            return
        
        # Real state change!
        old_state_str = "None" if self._last_known_good_state is None else str(self._last_known_good_state)
        _LOGGER.info(f"Target '{self.raw}' state changed: {old_state_str} -> {current_target_state} (triggered by {entity_id})")
        
        # Update last valid state
        old_known_state = self._last_known_good_state
        self._last_known_good_state = current_target_state
        
        # Notify group about IMPORTANT state change
        if self._on_change_callback:
            await self._on_change_callback(self, old_known_state, current_target_state)

    async def is_on(self):
        """Checks if at least one target from the list is on"""
        # If template, need to update entity_ids list
        if self._is_template:
            self._current_entity_ids = await self._get_entity_ids()
        
        if not self._current_entity_ids:
            return False
        
        # Target is considered on if at least one entity is on
        for entity_id in self._current_entity_ids:
            state = self.hass.states.get(entity_id)
            if state is not None and state.state not in ("unavailable", "unknown", "off"):
                return True
        
        return False

    def set_deadline(self, deadline_timestamp: Optional[float]):
        """Updates deadline attribute for all entity_ids"""
        for entity_id in self._current_entity_ids:
            state = self.hass.states.get(entity_id)
            if state is None:
                continue
                
            current_deadline = state.attributes.get("auto_off_deadline")
            is_on = state.state == "on"
            
            if is_on and deadline_timestamp is not None:
                # Calculate real deadline time
                now_real = datetime.datetime.now().astimezone()
                now_monotonic = self.hass.loop.time()
                seconds_until_deadline = deadline_timestamp - now_monotonic
                real_deadline = now_real + datetime.timedelta(seconds=seconds_until_deadline)
                new_deadline = real_deadline.isoformat()
                
                # Update only if deadline actually changed
                if current_deadline != new_deadline:
                    attrs = dict(state.attributes)
                    attrs["auto_off_deadline"] = new_deadline
                    self.hass.states.async_set(entity_id, state.state, attrs)
                    _LOGGER.debug(f"Target entity {entity_id} deadline updated: {new_deadline}")
            elif not is_on and current_deadline is not None:
                # Reset attribute to None only if it's currently not None
                attrs = dict(state.attributes)
                attrs["auto_off_deadline"] = None
                self.hass.states.async_set(entity_id, state.state, attrs)
                _LOGGER.debug(f"Target entity {entity_id} deadline cleared")

    def get_existing_deadline(self) -> Optional[datetime.datetime]:
        """Reads existing deadline from attributes of first ON entity"""
        for entity_id in self._current_entity_ids:
            state = self.hass.states.get(entity_id)
            if state is None or state.state not in ("on",):
                continue
            
            deadline_str = state.attributes.get("auto_off_deadline")
            if deadline_str:
                try:
                    return datetime.datetime.fromisoformat(deadline_str)
                except (ValueError, TypeError) as e:
                    _LOGGER.warning(f"Invalid deadline format in {entity_id}: {deadline_str}, error: {e}")
        return None

    async def turn_off_expired(self) -> bool:
        """
        Checks each entity for expired deadline and turns it off.
        Returns True if at least one entity was turned off.
        Used for cases when turn_off command didn't reach device (zigbee etc.)
        """
        now_real = datetime.datetime.now().astimezone()
        turned_off_any = False
        
        for entity_id in self._current_entity_ids:
            state = self.hass.states.get(entity_id)
            if state is None or state.state not in ("on",):
                continue
            
            deadline_str = state.attributes.get("auto_off_deadline")
            if not deadline_str:
                continue
            
            try:
                deadline = datetime.datetime.fromisoformat(deadline_str)
                if deadline <= now_real:
                    # Deadline expired, entity still ON -> turn off
                    domain = entity_id.split(".")[0]
                    await self.hass.services.async_call(domain, "turn_off", {"entity_id": entity_id}, blocking=True)
                    _LOGGER.warning(f"Entity {entity_id} still ON after expired deadline {deadline_str}, turning off again")
                    turned_off_any = True
            except (ValueError, TypeError) as e:
                _LOGGER.warning(f"Invalid deadline format in {entity_id}: {deadline_str}, error: {e}")
        
        return turned_off_any

    async def turn_off(self):
        """Turns off all entity_ids"""
        tasks = []
        for entity_id in self._current_entity_ids:
            domain = entity_id.split(".")[0]
            service = "turn_off"
            task = self.hass.services.async_call(domain, service, {"entity_id": entity_id}, blocking=True)
            tasks.append(task)
        
        if tasks:
            try:
                await asyncio.gather(*tasks)
                _LOGGER.info(f"Target '{self.raw}' turned OFF all {len(self._current_entity_ids)} entities")
            except Exception as e:
                _LOGGER.error(f"Failed to turn off target '{self.raw}': {e}")

    async def stop_tracking(self):
        """Unsubscribes from events"""
        for unsub in self._unsub_list:
            if unsub:
                unsub()
        self._unsub_list.clear()
        self._current_entity_ids.clear()
        _LOGGER.debug(f"Target '{self.raw}' stopped tracking")

    # For backward compatibility
    @property
    def entity_id(self) -> str:
        """Returns first entity_id for compatibility or string representation"""
        if self._current_entity_ids:
            return self._current_entity_ids[0]
        return self.raw


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
        # Critical section for race condition protection
        self._lock = asyncio.Lock()
        # Tracking previous states for transition detection
        self._last_any_target_on: Optional[bool] = None
        self._init_from_config()

    def _init_from_config(self):
        self._sensors = []
        self._targets = []
        for sensor in self._config.sensors:
            try:
                sensor_obj = Sensor(self.hass, sensor, self._on_sensor_state_change)
                self._sensors.append(sensor_obj)
                # Sensors start tracking their own changes
                asyncio.create_task(sensor_obj.start_tracking())
            except Exception as e:
                _LOGGER.error(f"Sensor '{sensor}' is invalid and will be ignored: {e}")
        for target_def in self._config.targets:
            target = Target(self.hass, target_def, self._on_target_state_change)
            self._targets.append(target)
            # Targets start tracking their own changes
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
        """Main method for checking and setting deadline"""
        async with self._lock:
            # Collect current state
            current_state = await self._collect_current_state()
            
            # Log state
            self._log_current_state(current_state)
            
            # First run initialization
            if self._is_first_run():
                self._handle_first_run(current_state)
                return
            
            # Log state transitions
            await self._log_state_transitions(current_state)
            
            # Check for expired deadlines BEFORE updating deadline attributes
            # This handles the case when turn_off command didn't reach device
            if current_state['target_on'] and current_state['all_sensors_off']:
                await self._turn_off_expired_targets()
            
            # Make deadline decisions
            await self._handle_deadline_logic(current_state)
            
            # Save current state as previous
            self._update_last_states(current_state)
            
            # Update deadlines for all targets
            self._update_targets_deadline()

    async def _collect_current_state(self) -> dict:
        """Collects current state of sensors and targets"""
        target_on = await self.any_target_on()
        all_sensors_off = await self.all_sensors_off()
        
        return {
            'target_on': target_on,
            'all_sensors_off': all_sensors_off,
            'human_deadline': self._get_human_deadline()
        }

    def _get_human_deadline(self) -> str:
        """Converts deadline to human-readable format for logging"""
        if self._timer_deadline is None:
            return "None"
        
        now_real = datetime.datetime.now().astimezone()
        now_monotonic = self.hass.loop.time()
        seconds_until_deadline = self._timer_deadline - now_monotonic
        real_deadline = now_real + datetime.timedelta(seconds=seconds_until_deadline)
        return real_deadline.isoformat()

    def _log_current_state(self, state: dict):
        """Logs current group state"""
        _LOGGER.debug(f"[Checking Group {self.group_id}] target_on={state['target_on']}, "
                    f"all_sensors_off={state['all_sensors_off']}, deadline={state['human_deadline']}")

    def _is_first_run(self) -> bool:
        """Checks if this is the first run"""
        return self._last_all_sensors_off is None or self._last_any_target_on is None

    def _handle_first_run(self, state: dict):
        """Handles first system run"""
        _LOGGER.info(f"[Group {self.group_id}] First run initialization")
        self._last_all_sensors_off = state['all_sensors_off']
        self._last_any_target_on = state['target_on']
        
        # At startup just set deadline if needed
        # Expired deadlines check will be in periodic worker
        if state['target_on'] and state['all_sensors_off'] and self._timer_deadline is None:
            asyncio.create_task(self._set_deadline_from_delay("startup"))

    async def _set_deadline_from_delay(self, reason: str):
        """Sets deadline based on delay from config"""
        delay = await self.get_delay()
        now = self.hass.loop.time()
        new_deadline = now + delay
        self._start_deadline(force_deadline=new_deadline)
        
        now_real = datetime.datetime.now().astimezone()
        human_deadline = (now_real + datetime.timedelta(seconds=delay)).isoformat()
        _LOGGER.info(f"[Group {self.group_id}] Deadline set by {reason}: {delay}s | New deadline: {new_deadline} ({human_deadline})")

    async def _log_state_transitions(self, state: dict):
        """Logs detailed information about state transitions"""
        # Collect statuses for logging
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
                # Show how many entity_ids are active for templates
                if hasattr(t, '_current_entity_ids') and len(t._current_entity_ids) > 1:
                    active_count = 0
                    for eid in t._current_entity_ids:
                        state_obj = self.hass.states.get(eid)
                        if state_obj and state_obj.state not in ("unavailable", "unknown", "off"):
                            active_count += 1
                    status_str = f"{status} ({active_count}/{len(t._current_entity_ids)} active)"
                else:
                    status_str = str(status)
            except Exception as e:
                status_str = f"error: {e}"
            target_statuses.append(f"{getattr(t, 'raw', str(t))}: {status_str}")

        _LOGGER.debug(f"[Group {self.group_id}] Sensors: {sensor_statuses} | Targets: {target_statuses}")
        _LOGGER.debug(f"[Group {self.group_id}] State transition: "
                    f"last_all_sensors_off={self._last_all_sensors_off} -> all_sensors_off={state['all_sensors_off']}, "
                    f"last_any_target_on={self._last_any_target_on} -> any_target_on={state['target_on']}")

    async def _handle_deadline_logic(self, state: dict):
        """Main deadline decision logic"""
        # If target is off -> always cancel deadline
        if not state['target_on']:
            if self._cancel_deadline():
                _LOGGER.info(f"[Group {self.group_id}] Deadline cancelled: target is off")
            return

        # Target is on - analyze state transitions
        transitions = self._analyze_state_transitions(state)
        
        if transitions['target_turned_on'] and state['all_sensors_off']:
            await self._set_deadline_from_delay("target turning ON")
        elif transitions['sensors_turned_off'] and state['target_on']:
            await self._set_deadline_from_delay("sensors turning OFF")
        elif transitions['sensors_turned_on']:
            if self._cancel_deadline():
                _LOGGER.info(f"[Group {self.group_id}] Deadline cancelled: sensor turned on")
        elif state['target_on'] and state['all_sensors_off'] and self._timer is None:
            # Timer lost (e.g. after restart) - check expired deadlines
            await self._check_expired_deadlines()

    async def _turn_off_expired_targets(self):
        """
        Checks all targets for expired deadline attributes and turns them off.
        Called on every periodic check when target is on and sensors are off.
        This handles the case when turn_off command didn't reach device (zigbee etc.)
        """
        for target in self._targets:
            await target.turn_off_expired()

    async def _check_expired_deadlines(self):
        """
        Checks expired deadlines from target attributes and turns them off.
        Called periodically when target is on, sensors are off, but no timer.
        Solves two problems:
        1. Timer lost after HA restart
        2. Turn_off command didn't reach device (zigbee etc.)
        """
        turned_off_any = False
        for target in self._targets:
            if await target.turn_off_expired():
                turned_off_any = True
        
        if not turned_off_any:
            # Didn't turn off anyone, but no timer - set new deadline
            _LOGGER.debug(f"[Group {self.group_id}] No expired deadlines found, setting new deadline")
            await self._set_deadline_from_delay("no timer and no expired deadlines")

    def _analyze_state_transitions(self, state: dict) -> dict:
        """Analyzes state transitions"""
        return {
            'target_turned_on': self._last_any_target_on is False and state['target_on'],
            'sensors_turned_off': self._last_all_sensors_off is False and state['all_sensors_off'],
            'sensors_turned_on': self._last_all_sensors_off is True and not state['all_sensors_off']
        }

    def _update_last_states(self, state: dict):
        """Updates previous states"""
        self._last_all_sensors_off = state['all_sensors_off']
        self._last_any_target_on = state['target_on']

    def _update_targets_deadline(self):
        """
        Updates deadlines for all targets in the group.
        Each target decides itself whether to update its auto_off_deadline attribute.
        """
        for target in self._targets:
            target.set_deadline(self._timer_deadline)

    def _start_deadline(self, force_deadline=None):
        # This method is only called from check_and_set_deadline, which is already under lock
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
        # This method is only called from check_and_set_deadline, which is already under lock
        if self._timer:
            self._timer.cancel()
            self._timer = None
            return True
        self._timer_deadline = None
        return False

    async def _turn_off_targets(self):
        # Clear timer state BEFORE turning off - timer has fired
        self._timer = None
        self._timer_deadline = None
        
        tasks = []
        for target in self._targets:
            tasks.append(target.turn_off())
        if tasks:
            await asyncio.gather(*tasks)
        _LOGGER.info("All targets turned off after deadline.")

    async def async_unload(self):
        """Cleans up group resources"""
        async with self._lock:
            # Cancel timer
            self._cancel_deadline()
            
            # Sensors unsubscribe from their own events
            for sensor in self._sensors:
                await sensor.stop_tracking()
            
            # Targets unsubscribe from their own events
            for target in self._targets:
                await target.stop_tracking()
            
            _LOGGER.info(f"[Group {self.group_id}] Unloaded successfully")

    async def _on_target_state_change(self, target: Target, old_state: Optional[bool], new_state: Optional[bool]):
        """Handler for target state changes, passed to Target"""
        # This method is called from Target._handle_my_changes
        # It is only called when a REAL state change occurs for target
        # (old_state != new_state), ignoring intermediate unknown/unavailable states
        _LOGGER.debug(f"Target {getattr(target, 'entity_id', 'unknown')} state change: {old_state} -> {new_state}")
        await self.check_and_set_deadline()

    async def _on_sensor_state_change(self, sensor: Sensor, old_state: Optional[bool], new_state: Optional[bool]):
        """Handler for sensor state changes, passed to Sensor"""
        # This method is called from Sensor._handle_entity_change or Sensor._handle_template_change
        # It is only called when a REAL state change occurs for sensor
        # (old_state != new_state), ignoring intermediate unknown/unavailable states
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
        # Clear existing groups
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
        _LOGGER.debug(f"Periodic worker started.")
        try:
            for group in self._groups.values():
                # Check states and set deadlines
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
