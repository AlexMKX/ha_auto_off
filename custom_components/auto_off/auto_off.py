import asyncio
import datetime
import logging
from collections.abc import Callable
from typing import Any

from homeassistant.core import CoreState, HomeAssistant, State, valid_entity_id
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_template,
)
from homeassistant.helpers.template import Template
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

# Local import to avoid a top-level cycle through __init__ → integration_manager.
# group_entities only imports from .const, so this is safe.
from .group_entities import expand_group_targets  # noqa: E402

_LOGGER = logging.getLogger(__name__)

# Ensure-off retry timings. Held as module constants on purpose: production
# data showed a single fixed value works for every group, and per-group
# tuning never came up. Avoid promoting these to GroupConfig until a real
# use case requires it (YAGNI).
ENSURE_WINDOW_SEC = 60
ENSURE_INTERVAL_SEC = 10


def _missing_entity_log_level(hass: HomeAssistant) -> int:
    """Choose log level for "entity not in state machine" events.

    INFO while HA is still starting (integrations register their entities
    on different schedules; absence is expected during this window).
    WARNING in every other phase, in particular ``running``, when a
    missing entity points at a real configuration or registration bug.
    """
    if getattr(hass, "state", None) == CoreState.starting:
        return logging.INFO
    return logging.WARNING



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

    # Reject unknown fields so a stale ``ensure_window`` /
    # ``ensure_interval`` in an old service payload surfaces as a
    # validation error rather than silently being stored on the
    # config entry.
    model_config = ConfigDict(extra="forbid")

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

        _LOGGER.log(
            _missing_entity_log_level(self.hass),
            "Sensor entity '%s' state not found",
            entity_id,
        )
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
        poll_interval: int = 15,
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
        # Patrol cadence (seconds). The inactivity-timer model requires
        # delay > poll_interval; otherwise the deadline can fire between
        # patrol ticks during presence. See the design spec.
        self._poll_interval = poll_interval
        # One-shot guard so the delay<=poll_interval warning is not
        # repeated on every maybe_delay call (hot path).
        self._delay_warning_emitted = False
        # True until the first check_and_set_deadline runs; used to seed a
        # baseline deadline at startup when a target is already on.
        self._is_first_run = True
        # Critical section for race condition protection
        self._lock = asyncio.Lock()
        # Post-deadline retry loop handle. See _ensure_off_loop and the
        # design spec (2026-05-16-ensure-off-loop-design.md).
        self._ensure_task: asyncio.Task | None = None
        # Held during the entire turn-off phase (initial dispatch +
        # ensure-off retry loop). External consumers
        # (check_and_set_deadline reentries via state-change callbacks,
        # periodic rescan) check ``self._turn_off_lock.locked()`` and
        # skip their work cleanly while it is held, so a late
        # ``Target X turned off`` event arriving mid-retry does not
        # trigger a stray "no timer (recalculated)" deadline that would
        # cancel the in-flight ensure-loop and push the remaining
        # leaves out by another full ``delay``.
        self._turn_off_lock = asyncio.Lock()
        # Re-expand state. Roots are user-config targets; leaves are
        # the expanded set tracked by self._targets. See
        # docs/superpowers/specs/2026-06-03-target-reexpand-design.md
        self._root_targets: list[str] = []
        self._current_leaves: list[str] = []
        self._targets_initialised: bool = False
        # Strong references for background tasks spawned by this group.
        # Without these, Python's GC may collect tasks before they run
        # (the event loop holds only weak references). The set is also
        # used to cancel all in-flight work during async_unload.
        self._background_tasks: set[asyncio.Task] = set()
        # Set to True at the start of async_unload so any in-flight
        # callback can short-circuit cleanly during shutdown.
        self._unloaded: bool = False
        self._init_from_config()

    def _track_background(self, coro) -> asyncio.Task:
        """Schedule a coroutine as a background task with a strong reference.

        Without holding a strong reference the event loop may garbage-
        collect the task before it completes. The reference is removed
        when the task finishes (success, failure, or cancellation).
        """
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

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
                self._track_background(sensor_obj.start_tracking())
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
                self._track_background(sensor_obj.start_tracking())
            except Exception as e:
                _LOGGER.error(f"Sensor template '{template_str}' is invalid and will be ignored: {e}")
        # Targets are built asynchronously so that we can subscribe to
        # root entity state changes and react to membership updates at
        # runtime. See _async_init_targets and the design spec.
        self._track_background(self._async_init_targets())

    async def _async_init_targets(self) -> None:
        """Initialise the leaf target list.

        Invalid root entity_ids from config are filtered out of
        ``self._root_targets`` after a one-time warning. ``GroupConfig``
        keeps the raw list (validator only warns), so ``dump_group``
        still reports user intent including typos; auto_off itself
        operates on the validated subset only.

        Membership changes on root group entities are detected by the
        periodic patrol in ``AutoOffManager.periodic_worker``, which
        calls ``_reexpand_targets()`` on every tick.
        """
        if self._targets_initialised:
            return
        self._targets_initialised = True
        all_roots = list(self._config.targets)
        valid_roots: list[str] = []
        for root_id in all_roots:
            if not valid_entity_id(root_id):
                _LOGGER.warning(
                    "[Group %s] Skipping invalid root target %r",
                    self.group_id,
                    root_id,
                )
                continue
            valid_roots.append(root_id)
        self._root_targets = valid_roots
        await self._reexpand_targets(initial=True)

    async def _reexpand_targets(self, *, initial: bool = False) -> None:
        """Recompute leaves and diff against the current set.

        See docs/superpowers/specs/2026-06-03-target-reexpand-design.md
        for the protocol. Phase 1 (diff) runs under ``self._lock``;
        phase 2 (deadline recheck) runs strictly after release to
        avoid self-deadlock against ``check_and_set_deadline``.
        """
        async with self._lock:
            new_leaves = expand_group_targets(self.hass, self._root_targets)
            new_leaf_set = set(new_leaves)
            current_leaf_set = set(self._current_leaves)
            added = [eid for eid in new_leaves if eid not in current_leaf_set]
            removed = [eid for eid in self._current_leaves if eid not in new_leaf_set]

            for entity_id in removed:
                target = next(
                    (t for t in self._targets if t.entity_id == entity_id),
                    None,
                )
                if target is not None:
                    await target.stop_tracking()
                    self._targets.remove(target)

            for entity_id in added:
                target = Target(self.hass, entity_id, self._on_target_state_change)
                self._targets.append(target)
                await target.start_tracking()

            self._current_leaves = new_leaves

            if added or removed:
                _LOGGER.info(
                    "[Group %s] Target leaves changed: added=%s removed=%s",
                    self.group_id,
                    added,
                    removed,
                )

        if not initial and (added or removed):
            # A newly-added leaf that is already on counts as activity
            # (a light appeared on); an added OFF leaf must not extend the
            # deadline. Compute the precise signal instead of bool(added).
            added_on = False
            for entity_id in added:
                target = next(
                    (t for t in self._targets if t.entity_id == entity_id),
                    None,
                )
                if target is not None and await target.is_on():
                    added_on = True
                    break
            await self.check_and_set_deadline(target_just_turned_on=added_on)

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

    async def check_and_set_deadline(self, *, target_just_turned_on: bool = False):
        """Evaluate the inactivity timer for this group.

        Extend-only model:
        - No target on -> cancel the deadline (nothing to manage).
        - A target just turned on, OR presence is on, OR this is the first
          run with a target on -> maybe_delay (push the deadline forward).
        - Otherwise (target on, presence off, steady state) -> noop; let
          the existing deadline ride to expiry.

        The ``self._turn_off_lock`` guard suppresses re-entry during the
        turn-off / ensure-off phase (a late target ``True -> False`` event
        must not perturb the in-flight retry loop).
        """
        if self._turn_off_lock.locked():
            _LOGGER.debug(
                "[Group %s] check_and_set_deadline skipped: turn-off phase in progress",
                self.group_id,
            )
            return

        async with self._lock:
            target_on = await self.any_target_on()
            first = self._is_first_run
            self._is_first_run = False

            if not target_on:
                if self._cancel_deadline():
                    _LOGGER.info(
                        "[Group %s] Deadline cancelled: no target on",
                        self.group_id,
                    )
                return

            presence_on = not await self.all_sensors_off()
            if target_just_turned_on or presence_on or first:
                reason = (
                    "target turned on"
                    if target_just_turned_on
                    else ("presence on" if presence_on else "startup baseline")
                )
                await self._maybe_delay_locked(reason)
            # else: steady state, target on + presence off -> let it ride.

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

    def _warn_if_delay_too_short(self, delay_seconds: int) -> None:
        """Warn once if the configured delay does not exceed poll_interval.

        With delay <= poll_interval the inactivity timer can fire between
        patrol ticks while presence is still on, flapping the targets off
        mid-occupancy. The fix is operational (raise the delay), so this
        is a warning, not an error.
        """
        if delay_seconds <= self._poll_interval and not self._delay_warning_emitted:
            self._delay_warning_emitted = True
            _LOGGER.warning(
                "[Group %s] delay (%ds) <= poll_interval (%ds): the inactivity "
                "timer may fire between patrol ticks during presence. Set the "
                "group delay greater than poll_interval.",
                self.group_id,
                delay_seconds,
                self._poll_interval,
            )

    async def maybe_delay(self, reason: str) -> None:
        """Public extend-only deadline bump. Acquires the group lock."""
        async with self._lock:
            await self._maybe_delay_locked(reason)

    async def _maybe_delay_locked(self, reason: str) -> None:
        """Extend the deadline to now()+delay if later than the current one.

        Extend-only: never shortens an existing deadline; creates one if
        none exists. Caller must hold ``self._lock``.
        """
        delay = await self.get_delay()
        self._warn_if_delay_too_short(delay)
        now = self.hass.loop.time()
        potential = now + delay
        if self._timer_deadline is not None and potential <= self._timer_deadline:
            return  # would not extend
        self._start_deadline(force_deadline=potential)
        now_real = datetime.datetime.now().astimezone()
        human = (now_real + datetime.timedelta(seconds=delay)).isoformat()
        _LOGGER.info(
            "[Group %s] Deadline extended by %s: +%ds | deadline %s",
            self.group_id,
            reason,
            delay,
            human,
        )

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
            self._timer = loop.call_later(delay, lambda: self._track_background(self._turn_off_targets()))
            self._timer_deadline = loop.time() + delay
            _LOGGER.info(f"[{self.group_id}] All sensors are off/false. Deadline delay started.")
        else:
            self._track_background(self._turn_off_targets())
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
        """Execute the full turn-off phase under ``self._turn_off_lock``.

        The lock spans the initial dispatch AND the ensure-off retry
        loop. While it is held, ``check_and_set_deadline`` (driven by
        state-change callbacks for the targets we are turning off, or
        by periodic rescan) bails out without scheduling a new deadline:
        the in-flight retry loop already owns recovery for this group
        and a parallel "no timer (recalculated)" branch would otherwise
        cancel us and push the slow leaves out by another full
        ``delay``.

        After the lock is released we re-evaluate state exactly once so
        any genuine change observed during the turn-off phase
        (e.g. a sensor flipped back on, a user turned a target on
        again) lands in the deadline state machine without further
        prodding.
        """
        async with self._turn_off_lock:
            # Clear timer state BEFORE turning off - timer has fired
            self._timer = None
            self._timer_deadline = None
            self._notify_deadline_change()

            # Primary path: one <domain>.turn_off call per live group
            # entity. We dispatch to the REAL entity_id HA assigned
            # (may differ from our ``targets_group_entity_id()``
            # prediction because ``name=None`` + ``translation_key``
            # changes the slugify output).
            dispatched_domains: set[str] = set()
            if self._manager is not None:
                for entity_id in self._manager.get_group_member_group_entity_ids(self.group_id):
                    domain = entity_id.split(".", 1)[0]
                    dispatched_domains.add(domain)
                    try:
                        await self.hass.services.async_call(
                            domain,
                            "turn_off",
                            {"entity_id": entity_id},
                            blocking=False,
                        )
                    except Exception as exc:  # noqa: BLE001
                        _LOGGER.warning(
                            "[Group %s] Group turn_off on %s failed: %s",
                            self.group_id,
                            entity_id,
                            exc,
                        )

            # Fallback: for every target whose domain was NOT
            # dispatched via a live group entity, issue an individual
            # turn_off. Covers non-groupable domains (scene,
            # input_boolean, ...) AND the case where a group entity
            # exists in our bookkeeping but has no entity_id assigned
            # yet.
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

            # Run the ensure-off retry loop INLINE so it inherits the
            # turn-off lock and external callbacks remain suppressed
            # until every retry pass is done. No external code should
            # cancel it - cancellation now flows through the lock
            # release after the loop finishes naturally.
            await self._ensure_off_loop()

        # Lock released here. Pending events (state-change callbacks
        # for targets that took longer to ack, periodic rescan ticks)
        # naturally re-fire ``check_and_set_deadline`` on their own
        # schedule and pick up any state change that happened during
        # the turn-off phase. No explicit post-call needed.

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

        window = ENSURE_WINDOW_SEC
        interval = ENSURE_INTERVAL_SEC
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

            targets_snapshot = list(self._targets)
            still_on = []
            for target in targets_snapshot:
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
            remaining = sum(1 for t in list(self._targets) if await t.is_on())
        except Exception:
            remaining = -1
        _LOGGER.warning(
            "[%s] ensure: window expired, %d target(s) still on",
            self.group_id,
            remaining,
        )

    async def async_unload(self):
        """Cleans up group resources."""
        # Set the unloaded flag early so any in-flight callback (e.g. a
        # pending state-change reaction) can short-circuit cleanly.
        self._unloaded = True

        async with self._lock:
            # Cancel timer and any pending ensure-off loop.
            self._cancel_deadline()

            # Sensors unsubscribe from their own events
            for sensor in self._sensors:
                await sensor.stop_tracking()

            # Targets unsubscribe from their own events
            for target in self._targets:
                await target.stop_tracking()

            _LOGGER.info("[Group %s] Unloaded successfully", self.group_id)

        # Cancel any background tasks still in flight. Done OUTSIDE the
        # lock so tasks holding the lock can release it and observe the
        # cancellation cleanly.
        pending = [t for t in self._background_tasks if not t.done()]
        for task in pending:
            task.cancel()
        for task in pending:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as exc:  # noqa: BLE001
                _LOGGER.debug(
                    "[Group %s] background task error during unload: %s",
                    self.group_id,
                    exc,
                )

    async def tick(self) -> None:
        """Patrol tick: re-expand targets then evaluate the deadline.

        Called by ``AutoOffManager.periodic_worker`` on every scheduler
        interval. Re-expanding first ensures that runtime membership
        changes on root group entities (added/removed leaves) are
        detected within one poll interval before the deadline state
        machine evaluates.
        """
        await self._reexpand_targets()
        await self.check_and_set_deadline()

    async def _on_target_state_change(self, target: Target, old_state: bool | None, new_state: bool | None):
        """Handler for target state changes, passed to Target.

        A target turning on is activity: it extends the deadline even when
        no presence is detected (so a manually-switched light gets a full
        delay). A target turning off re-evaluates (and cancels if nothing
        is left on).
        """
        _LOGGER.debug(
            "Target %s state change: %s -> %s",
            getattr(target, "entity_id", "unknown"),
            old_state,
            new_state,
        )
        await self.check_and_set_deadline(target_just_turned_on=bool(new_state))

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
        poll_interval: int = 15,
    ) -> None:
        self.hass = hass
        self.config = config
        self._on_deadline_change = on_deadline_change
        self._integration_manager = integration_manager
        self._poll_interval = poll_interval
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
                    poll_interval=self._poll_interval,
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
                # Re-expand first so late-registered group helpers and
                # runtime membership changes are picked up before the
                # deadline state machine evaluates.
                try:
                    await group.tick()
                except Exception as exc:  # noqa: BLE001
                    _LOGGER.warning(
                        "Group %s periodic tick failed: %s",
                        group.group_id,
                        exc,
                    )
        except Exception as e:
            _LOGGER.error(f"Scheduled config reload failed: {e}")

    async def async_unload(self):
        """Clean up resources."""
        for group in self._groups.values():
            await group.async_unload()
        self._groups.clear()
        self._tasks.clear()
