import asyncio
import datetime
import logging
from collections.abc import Callable
from typing import Any

from homeassistant.core import HomeAssistant, State, valid_entity_id
from homeassistant.helpers.event import async_track_state_change_event, async_track_template
from homeassistant.helpers.template import Template
from pydantic import BaseModel, field_validator, model_validator

_LOGGER = logging.getLogger(__name__)


class GroupConfig(BaseModel):
    """Configuration for a single auto-off group.

    A group is active while any sensor (entity or template) reports `on`.
    Once all sensors are off and any target is on, the delay starts and
    eventually turns off every target.
    """

    targets: list[str]
    sensors: list[str] = []
    sensor_templates: list[str] = []
    delay: int | str = 0

    @field_validator("targets")
    @classmethod
    def _warn_on_non_entity_targets(cls, value: list[str]) -> list[str]:
        """Warn on syntactically invalid entity ids in `targets`.

        Invalid items are kept in the list so they remain visible in the UI
        attribute and are skipped at turn_off time.
        """
        for item in value:
            if not valid_entity_id(item):
                _LOGGER.warning(
                    "GroupConfig: target %r is not a valid entity_id, "
                    "it will be skipped at turn_off",
                    item,
                )
        return value

    @model_validator(mode="after")
    def _require_targets_and_sensor_source(self) -> "GroupConfig":
        if not self.targets:
            raise ValueError("'targets' must be non-empty")
        if not self.sensors and not self.sensor_templates:
            raise ValueError("At least one of 'sensors' or 'sensor_templates' must be non-empty")
        return self


class Sensor:
    def __init__(
        self,
        hass: HomeAssistant,
        raw: str,
        kind: str,
        on_state_change_callback,
    ):
        """Create a sensor wrapper.

        kind: one of "entity" or "template". Determines the tracking path
        and how is_on() resolves.
        """
        if kind not in ("entity", "template"):
            raise ValueError(f"Unsupported sensor kind: {kind!r}")
        self.hass = hass
        self.raw = raw
        self._is_template = kind == "template"
        self._on_change_callback = on_state_change_callback
        self._unsub = None
        self._last_known_good_state: bool | None = None

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
            self._unsub = async_track_template(self.hass, template, self._handle_template_change)
            _LOGGER.debug(
                f"Sensor template '{self.raw}' started tracking, initial state: {self._last_known_good_state}"
            )
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

            self._unsub = async_track_state_change_event(self.hass, [entity_id], self._handle_entity_change)
            _LOGGER.debug(f"Sensor entity '{entity_id}' started tracking, initial state: {self._last_known_good_state}")
        except Exception as e:
            _LOGGER.error(f"Failed to track sensor entity '{entity_id}': {e}")

    async def _handle_entity_change(self, event):
        """Handles entity changes"""
        entity_id = event.data.get("entity_id")
        new_state = event.data.get("new_state")

        # Ignore invalid states
        if not new_state or new_state.state in ("unknown", "unavailable"):
            _LOGGER.debug(
                f"Sensor entity {entity_id} state is invalid ({new_state.state if new_state else 'None'}), ignoring"
            )
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

    def get_entity_id(self) -> str | None:
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
    """Single-entity wrapper for turn-off targets.

    `entity_id` must be a syntactically valid Home Assistant entity id. If
    not, the Target is constructed with `_skip=True`; all subsequent
    operations are no-ops. Missing entities in the state machine are handled
    separately at turn_off time (warn + skip).
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entity_id: str,
        on_state_change_callback,
    ):
        self.hass = hass
        self.entity_id = entity_id
        self._on_change_callback = on_state_change_callback
        self._unsub = None
        self._last_known_good_state: bool | None = None
        self._skip = not valid_entity_id(entity_id)

    async def start_tracking(self):
        """Subscribe to state changes for this single entity."""
        if self._skip or self._unsub is not None:
            return

        if self.hass.states.get(self.entity_id) is None:
            _LOGGER.warning(
                "Target %s does not exist in state machine, skipping tracking",
                self.entity_id,
            )
            return

        try:
            self._last_known_good_state = await self.is_on()
            self._unsub = async_track_state_change_event(
                self.hass, [self.entity_id], self._handle_my_changes
            )
            _LOGGER.debug(
                "Target '%s' started tracking, initial state: %s",
                self.entity_id,
                self._last_known_good_state,
            )
        except Exception as e:
            _LOGGER.error("Failed to track target '%s': %s", self.entity_id, e)

    async def _handle_my_changes(self, event):
        new_state = event.data.get("new_state")
        if not new_state or new_state.state in ("unknown", "unavailable"):
            _LOGGER.debug(
                "Target %s state is invalid (%s), ignoring",
                self.entity_id,
                new_state.state if new_state else "None",
            )
            return

        current = await self.is_on()
        if self._last_known_good_state == current:
            _LOGGER.debug(
                "Target '%s' state unchanged (%s), ignoring", self.entity_id, current
            )
            return

        old = self._last_known_good_state
        _LOGGER.info(
            "Target '%s' state changed: %s -> %s", self.entity_id, old, current
        )
        self._last_known_good_state = current
        if self._on_change_callback:
            await self._on_change_callback(self, old, current)

    async def is_on(self) -> bool:
        if self._skip:
            return False
        state = self.hass.states.get(self.entity_id)
        if state is None:
            return False
        return state.state not in ("unavailable", "unknown", "off")

    async def turn_off(self):
        if self._skip:
            return
        state = self.hass.states.get(self.entity_id)
        if state is None:
            _LOGGER.warning(
                "Target %s not found in state machine, skipping turn_off",
                self.entity_id,
            )
            return

        domain = self.entity_id.split(".")[0]
        try:
            await self.hass.services.async_call(
                domain, "turn_off", {"entity_id": self.entity_id}, blocking=True
            )
            _LOGGER.info("Target '%s' turned OFF", self.entity_id)
        except Exception as e:
            _LOGGER.error("Failed to turn off target '%s': %s", self.entity_id, e)

    async def stop_tracking(self):
        if self._unsub:
            self._unsub()
            self._unsub = None
            _LOGGER.debug("Target '%s' stopped tracking", self.entity_id)

    @property
    def raw(self) -> str:
        """Back-compat alias used in SensorGroup log statements."""
        return self.entity_id


class SensorGroup:
    def __init__(
        self,
        hass: HomeAssistant,
        group_id: str,
        config: GroupConfig,
        on_deadline_change: Callable[[str, str | None], None] | None = None,
    ):
        self.hass = hass
        self.group_id = group_id
        self._config = config  # immutable
        self._on_deadline_change = on_deadline_change
        self._sensors: list[Sensor] = []
        self._targets: list[Target] = []
        self._timer: asyncio.TimerHandle | None = None
        self._timer_deadline: float | None = None  # timestamp when timer fires
        self._last_all_sensors_off: bool | None = None
        # Critical section for race condition protection
        self._lock = asyncio.Lock()
        # Tracking previous states for transition detection
        self._last_any_target_on: bool | None = None
        self._init_from_config()

    def _init_from_config(self):
        self._sensors = []
        self._targets = []
        for sensor_id in self._config.sensors:
            try:
                sensor_obj = Sensor(
                    self.hass,
                    sensor_id,
                    kind="entity",
                    on_state_change_callback=self._on_sensor_state_change,
                )
                self._sensors.append(sensor_obj)
                asyncio.create_task(sensor_obj.start_tracking())
            except Exception as e:
                _LOGGER.error(f"Sensor entity '{sensor_id}' is invalid and will be ignored: {e}")
        for template_str in self._config.sensor_templates:
            try:
                sensor_obj = Sensor(
                    self.hass,
                    template_str,
                    kind="template",
                    on_state_change_callback=self._on_sensor_state_change,
                )
                self._sensors.append(sensor_obj)
                asyncio.create_task(sensor_obj.start_tracking())
            except Exception as e:
                _LOGGER.error(f"Sensor template '{template_str}' is invalid and will be ignored: {e}")
        for target_def in self._config.targets:
            target = Target(self.hass, target_def, self._on_target_state_change)
            self._targets.append(target)
            asyncio.create_task(target.start_tracking())

    async def all_sensors_off(self):
        sensors_on = []
        for s in self._sensors:
            is_on = await s.is_on()
            if is_on:
                sensors_on.append(getattr(s, "raw", str(s)))

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
        except Exception as err:
            raise ValueError(f"Failed to render delay template: {self._config.delay}, result: {rendered}") from err

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

            # Make deadline decisions
            await self._handle_deadline_logic(current_state)

            # Save current state as previous
            self._update_last_states(current_state)

    async def _collect_current_state(self) -> dict:
        """Collects current state of sensors and targets"""
        target_on = await self.any_target_on()
        all_sensors_off = await self.all_sensors_off()

        return {
            "target_on": target_on,
            "all_sensors_off": all_sensors_off,
            "human_deadline": self._get_human_deadline(),
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

    def _notify_deadline_change(self) -> None:
        if not self._on_deadline_change:
            return
        deadline_iso: str | None
        if self._timer_deadline is None:
            deadline_iso = None
        else:
            human = self._get_human_deadline()
            deadline_iso = None if human == "None" else human
        try:
            self._on_deadline_change(self.group_id, deadline_iso)
        except Exception as exc:
            _LOGGER.debug("Failed to notify deadline change for group %s: %s", self.group_id, exc)

    def _log_current_state(self, state: dict):
        """Logs current group state"""
        _LOGGER.debug(
            f"[Checking Group {self.group_id}] target_on={state['target_on']}, "
            f"all_sensors_off={state['all_sensors_off']}, deadline={state['human_deadline']}"
        )

    def _is_first_run(self) -> bool:
        """Checks if this is the first run"""
        return self._last_all_sensors_off is None or self._last_any_target_on is None

    def _handle_first_run(self, state: dict):
        """Handles first system run"""
        _LOGGER.info(f"[Group {self.group_id}] First run initialization")
        self._last_all_sensors_off = state["all_sensors_off"]
        self._last_any_target_on = state["target_on"]

        # At startup just set deadline if needed
        # Expired deadlines check will be in periodic worker
        if state["target_on"] and state["all_sensors_off"] and self._timer_deadline is None:
            asyncio.create_task(self._set_deadline_from_delay("startup"))

    async def _set_deadline_from_delay(self, reason: str):
        """Sets deadline based on delay from config"""
        delay = await self.get_delay()
        now = self.hass.loop.time()
        new_deadline = now + delay
        self._start_deadline(force_deadline=new_deadline)

        now_real = datetime.datetime.now().astimezone()
        human_deadline = (now_real + datetime.timedelta(seconds=delay)).isoformat()
        _LOGGER.info(
            f"[Group {self.group_id}] Deadline set by {reason}: {delay}s | New deadline: {new_deadline} ({human_deadline})"
        )

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
                if hasattr(t, "_current_entity_ids") and len(t._current_entity_ids) > 1:
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
        _LOGGER.debug(
            f"[Group {self.group_id}] State transition: "
            f"last_all_sensors_off={self._last_all_sensors_off} -> all_sensors_off={state['all_sensors_off']}, "
            f"last_any_target_on={self._last_any_target_on} -> any_target_on={state['target_on']}"
        )

    async def _handle_deadline_logic(self, state: dict):
        """Main deadline decision logic"""
        # If target is off -> always cancel deadline
        if not state["target_on"]:
            if self._cancel_deadline():
                _LOGGER.info(f"[Group {self.group_id}] Deadline cancelled: target is off")
            return

        # Target is on - analyze state transitions
        transitions = self._analyze_state_transitions(state)

        if transitions["target_turned_on"] and state["all_sensors_off"]:
            await self._set_deadline_from_delay("target turning ON")
        elif transitions["sensors_turned_off"] and state["target_on"]:
            await self._set_deadline_from_delay("sensors turning OFF")
        elif transitions["sensors_turned_on"]:
            if self._cancel_deadline():
                _LOGGER.info(f"[Group {self.group_id}] Deadline cancelled: sensor turned on")
        elif state["target_on"] and state["all_sensors_off"] and self._timer is None:
            # Timer lost (e.g. after restart) - check expired deadlines
            await self._check_expired_deadlines()

    async def _check_expired_deadlines(self):
        """
        Called when target is on, sensors are off, but no timer exists.
        After HA restart timers are lost — recalculate deadline from delay.
        """
        _LOGGER.debug("[Group %s] No active timer, setting new deadline", self.group_id)
        await self._set_deadline_from_delay("no timer (recalculated)")

    def _analyze_state_transitions(self, state: dict) -> dict:
        """Analyzes state transitions"""
        return {
            "target_turned_on": self._last_any_target_on is False and state["target_on"],
            "sensors_turned_off": self._last_all_sensors_off is False and state["all_sensors_off"],
            "sensors_turned_on": self._last_all_sensors_off is True and not state["all_sensors_off"],
        }

    def _update_last_states(self, state: dict):
        """Updates previous states"""
        self._last_all_sensors_off = state["all_sensors_off"]
        self._last_any_target_on = state["target_on"]

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

        self._notify_deadline_change()

    def _cancel_deadline(self) -> bool:
        # This method is only called from check_and_set_deadline, which is already under lock
        had_timer = self._timer is not None
        if self._timer:
            self._timer.cancel()
            self._timer = None
        self._timer_deadline = None  # Always clear deadline when cancelling
        self._notify_deadline_change()
        return had_timer

    async def _turn_off_targets(self):
        # Clear timer state BEFORE turning off - timer has fired
        self._timer = None
        self._timer_deadline = None
        self._notify_deadline_change()

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

    async def _on_target_state_change(self, target: Target, old_state: bool | None, new_state: bool | None):
        """Handler for target state changes, passed to Target"""
        # This method is called from Target._handle_my_changes
        # It is only called when a REAL state change occurs for target
        # (old_state != new_state), ignoring intermediate unknown/unavailable states
        _LOGGER.debug(f"Target {getattr(target, 'entity_id', 'unknown')} state change: {old_state} -> {new_state}")
        await self.check_and_set_deadline()

    async def _on_sensor_state_change(self, sensor: Sensor, old_state: bool | None, new_state: bool | None):
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

    def __init__(
        self,
        hass: HomeAssistant,
        config: dict[str, GroupConfig],
        *,
        on_deadline_change: Callable[[str, str | None], None] | None = None,
    ) -> None:
        self.hass = hass
        self.config = config
        self._on_deadline_change = on_deadline_change
        self._groups: dict[str, SensorGroup] = {}
        self._tasks: list[Any] = []

    async def async_init_groups(self):
        """Initialize sensor groups from configuration. Awaits unload of old groups."""
        for group in self._groups.values():
            try:
                await group.async_unload()
            except Exception as e:
                _LOGGER.error("Error unloading group: %s", e)

        self._groups.clear()
        for group_id, group_config in self.config.items():
            try:
                self._groups[group_id] = SensorGroup(
                    self.hass,
                    group_id,
                    group_config,
                    on_deadline_change=self._on_deadline_change,
                )
                _LOGGER.info(
                    "Initialized auto-off group '%s' with %d sensors and %d targets",
                    group_id,
                    len(group_config.sensors),
                    len(group_config.targets),
                )
            except Exception as e:
                _LOGGER.error("Failed to initialize auto-off group '%s': %s", group_id, e)

    async def periodic_worker(self):
        _LOGGER.debug("Periodic worker tick.")
        try:
            for group in self._groups.values():
                # Check states and set deadlines
                await group.check_and_set_deadline()
        except Exception as e:
            _LOGGER.error(f"Scheduled config reload failed: {e}")

    async def async_unload(self):
        """Clean up resources."""
        for group in self._groups.values():
            await group.async_unload()
        self._groups.clear()
        self._tasks.clear()
