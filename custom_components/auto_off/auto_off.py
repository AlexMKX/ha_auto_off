import asyncio
import datetime
import logging
from collections.abc import Callable
from typing import Any

from homeassistant.core import HomeAssistant, State, valid_entity_id
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_template,
)
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
    # Post-deadline ensure-off loop knobs (in SECONDS, unlike `delay`
    # which is in minutes). See docs/superpowers/specs/
    # 2026-05-16-ensure-off-loop-design.md.
    ensure_window: int | str = 60
    ensure_interval: int | str = 10

    @model_validator(mode="after")
    def _validate_ensure_settings(self) -> "GroupConfig":
        # Only validate plain ints; templated strings are checked at
        # render time inside ``get_ensure_window`` / ``get_ensure_interval``.
        if isinstance(self.ensure_window, int) and self.ensure_window < 0:
            raise ValueError("'ensure_window' must be >= 0")
        if isinstance(self.ensure_interval, int) and self.ensure_interval <= 0:
            raise ValueError("'ensure_interval' must be > 0")
        return self

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
                    "GroupConfig: target %r is not a valid entity_id, " "it will be skipped at turn_off",
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
        """Subscribes to entity changes.

        Subscription is installed unconditionally as long as the configured
        ``entity_id`` is syntactically valid; ``async_track_state_change_event``
        accepts entity_ids that do not exist in ``hass.states`` yet and starts
        firing the callback as soon as they appear. This avoids a start-up
        race with integrations that register their entities late
        (e.g. Magic Areas), which previously left the sensor permanently
        un-subscribed and forced the group into poll-only operation.
        """
        entity_id = self.get_entity_id()
        if not entity_id:
            _LOGGER.warning(f"Sensor '{self.raw}' is not a valid entity or template")
            return

        try:
            # If the entity already exists, capture its current state so the
            # first state-change comparison in ``_handle_entity_change`` works
            # against a real baseline. Otherwise leave it as ``None`` and let
            # the first valid event populate it.
            entity_present = self.hass.states.get(entity_id) is not None
            if entity_present:
                self._last_known_good_state = await self._check_entity_state()
            else:
                _LOGGER.info(
                    "Sensor entity %s does not exist yet, subscribing for later registration",
                    entity_id,
                )

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
        """Subscribe to state changes for this single entity.

        Subscription is installed unconditionally as long as the configured
        ``entity_id`` is syntactically valid (``self._skip`` covers the
        invalid case at construction time). When the entity does not yet
        exist in ``hass.states``, ``async_track_state_change_event`` still
        installs a listener and starts firing the callback as soon as the
        entity is registered. This avoids a start-up race with integrations
        that register their entities late, which previously left the target
        permanently un-subscribed.
        """
        if self._skip or self._unsub is not None:
            return

        try:
            entity_present = self.hass.states.get(self.entity_id) is not None
            if entity_present:
                self._last_known_good_state = await self.is_on()
            else:
                _LOGGER.info(
                    "Target %s does not exist yet, subscribing for later registration",
                    self.entity_id,
                )

            self._unsub = async_track_state_change_event(self.hass, [self.entity_id], self._handle_my_changes)
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
            _LOGGER.debug("Target '%s' state unchanged (%s), ignoring", self.entity_id, current)
            return

        old = self._last_known_good_state
        _LOGGER.info("Target '%s' state changed: %s -> %s", self.entity_id, old, current)
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
            await self.hass.services.async_call(domain, "turn_off", {"entity_id": self.entity_id}, blocking=True)
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
        *,
        manager: "Any | None" = None,
    ):
        self.hass = hass
        self.group_id = group_id
        self._config = config  # immutable
        self._on_deadline_change = on_deadline_change
        self._manager = manager
        self._sensors: list[Sensor] = []
        self._targets: list[Target] = []
        self._timer: asyncio.TimerHandle | None = None
        self._timer_deadline: float | None = None  # timestamp when timer fires
        self._last_all_sensors_off: bool | None = None
        # Critical section for race condition protection
        self._lock = asyncio.Lock()
        # Tracking previous states for transition detection
        self._last_any_target_on: bool | None = None
        # Post-deadline retry loop handle. See _ensure_off_loop and the
        # design spec (2026-05-16-ensure-off-loop-design.md).
        self._ensure_task: asyncio.Task | None = None
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

    def _render_seconds_field(self, name: str, raw) -> int:
        """Render a seconds-scale config field to int.

        Plain ints are returned as-is to avoid hitting the Template engine
        when no templating is actually needed (this also keeps unit tests
        free of full ``hass.config`` mocks). Strings are rendered through
        the standard Jinja path.
        """
        if isinstance(raw, int):
            return raw
        tpl = Template(str(raw), self.hass)
        rendered = tpl.async_render()
        try:
            return int(rendered)
        except Exception as err:
            raise ValueError(
                f"Failed to render {name} template: {raw}, result: {rendered}"
            ) from err

    async def get_ensure_window(self) -> int:
        """Render ``ensure_window`` to seconds.

        Unlike :meth:`get_delay`, this value is in seconds already - no
        ``* 60`` conversion. The ensure loop operates on the device-timeout
        scale (z2m default 10s), not on the minute scale.
        """
        return self._render_seconds_field("ensure_window", self._config.ensure_window)

    async def get_ensure_interval(self) -> int:
        """Render ``ensure_interval`` to seconds. See :meth:`get_ensure_window`."""
        value = self._render_seconds_field("ensure_interval", self._config.ensure_interval)
        if value <= 0:
            raise ValueError(f"'ensure_interval' must be > 0, got {value}")
        return value

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
        """Log current sensor and target states."""
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
            target_statuses.append(f"{t.entity_id}: {status}")

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
        # Also cancel any active post-deadline retry loop; the new state
        # (sensor on, target off, or new cycle) supersedes the previous
        # turn-off attempt.
        self._cancel_ensure_task()
        self._notify_deadline_change()
        return had_timer

    async def _turn_off_targets(self):
        # Clear timer state BEFORE turning off - timer has fired
        self._timer = None
        self._timer_deadline = None
        self._notify_deadline_change()

        # Primary path: one <domain>.turn_off call per live group entity.
        # We dispatch to the REAL entity_id HA assigned (may differ from
        # our `targets_group_entity_id()` prediction because `name=None`
        # + `translation_key` changes the slugify output).
        dispatched_domains: set[str] = set()
        if self._manager is not None:
            for entity_id in self._manager.get_group_member_group_entity_ids(
                self.group_id
            ):
                domain = entity_id.split(".", 1)[0]
                dispatched_domains.add(domain)
                try:
                    await self.hass.services.async_call(
                        domain,
                        "turn_off",
                        {"entity_id": entity_id},
                        blocking=False,
                    )
                except Exception as exc:  # noqa: BLE001 - never fail the whole expiry
                    _LOGGER.warning(
                        "[Group %s] Group turn_off on %s failed: %s",
                        self.group_id,
                        entity_id,
                        exc,
                    )

        # Fallback: for every target whose domain was NOT dispatched via a
        # live group entity, issue an individual turn_off.  This covers
        # both non-groupable domains (scene, input_boolean, ...) AND the
        # case where a group entity exists in our bookkeeping but has no
        # entity_id assigned yet (e.g. not fully added to HA).
        tasks = []
        for target in self._targets:
            entity_id = getattr(target, "entity_id", "")
            if "." not in entity_id:
                continue
            domain = entity_id.split(".", 1)[0]
            if domain in dispatched_domains:
                continue  # handled by group turn_off above
            tasks.append(target.turn_off())
        if tasks:
            await asyncio.gather(*tasks)
        _LOGGER.info("All targets turned off after deadline.")

        # Detached retry loop. See _ensure_off_loop for semantics.
        self._cancel_ensure_task()
        self._ensure_task = asyncio.create_task(self._ensure_off_loop())

    def _cancel_ensure_task(self) -> None:
        """Cancel an active ensure-off loop, if any. Idempotent."""
        task = self._ensure_task
        if task is None or task.done():
            self._ensure_task = None
            return
        task.cancel()
        self._ensure_task = None

    async def _ensure_off_loop(self) -> None:
        """Retry per-target ``turn_off`` until every target is off.

        Runs after the initial dispatch in :meth:`_turn_off_targets`.
        Stops as soon as one of these is true:

        * Every target reports ``is_on() == False`` (success).
        * ``all_sensors_off()`` returns ``False`` (presence reclaimed).
        * ``ensure_window`` seconds have elapsed (window expired).
        * The task is cancelled from outside (new deadline, deadline
          cancelled, or group unload).

        The retry loop NEVER re-dispatches to a group entity; it iterates
        the individual targets that are still on and calls ``turn_off``
        on each. This avoids spamming a whole group when only one member
        failed to switch.
        """
        import time

        try:
            window = await self.get_ensure_window()
            interval = await self.get_ensure_interval()
        except ValueError as exc:
            _LOGGER.warning(
                "[%s] ensure: invalid config, skipping loop: %s",
                self.group_id,
                exc,
            )
            return

        if window <= 0:
            return

        deadline = time.monotonic() + window
        while time.monotonic() < deadline:
            await asyncio.sleep(interval)

            if not await self.all_sensors_off():
                _LOGGER.info(
                    "[%s] ensure: sensors back on, abort",
                    self.group_id,
                )
                return

            still_on = []
            for target in self._targets:
                try:
                    if await target.is_on():
                        still_on.append(target)
                except Exception as exc:  # noqa: BLE001 - never die mid-loop
                    _LOGGER.warning(
                        "[%s] ensure: is_on check on %s failed: %s",
                        self.group_id,
                        getattr(target, "entity_id", "?"),
                        exc,
                    )

            if not still_on:
                _LOGGER.info(
                    "[%s] ensure: all targets off",
                    self.group_id,
                )
                return

            _LOGGER.info(
                "[%s] ensure: %d target(s) still on, retrying",
                self.group_id,
                len(still_on),
            )
            for target in still_on:
                try:
                    await target.turn_off()
                except Exception as exc:  # noqa: BLE001
                    _LOGGER.warning(
                        "[%s] ensure: retry of %s failed: %s",
                        self.group_id,
                        getattr(target, "entity_id", "?"),
                        exc,
                    )

        # Window expired with at least one target still on.
        try:
            remaining = sum(1 for t in self._targets if await t.is_on())
        except Exception:
            remaining = -1
        _LOGGER.warning(
            "[%s] ensure: window expired, %d target(s) still on",
            self.group_id,
            remaining,
        )

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
        integration_manager: "Any | None" = None,
    ) -> None:
        self.hass = hass
        self.config = config
        self._on_deadline_change = on_deadline_change
        self._integration_manager = integration_manager
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
                    manager=self._integration_manager,
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
