# Inactivity-Timer Deadline Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the transition-based deadline arming with an extend-only inactivity-timer model so manually turning on a target while unoccupied no longer instantly turns it off.

**Architecture:** A single `maybe_delay` primitive pushes the deadline to `now()+delay` only when that is later than the current deadline. Activity (a target turning on, or presence detected on a patrol tick / sensor change) calls `maybe_delay`; when no target is on the deadline is cancelled. Patrol extends the deadline every `poll_interval` while occupied. A `delay > poll_interval` constraint is enforced via WARNING.

**Tech Stack:** Python 3.13, Home Assistant custom component, `pytest`, `pytest-asyncio` (auto), `MagicMock`/`AsyncMock`.

**Spec:** `docs/superpowers/specs/2026-06-04-inactivity-timer-model-design.md`

---

## File Structure

- Modify: `custom_components/auto_off/auto_off.py`
  - `SensorGroup.__init__`: new params/fields (`poll_interval`, `_delay_warning_emitted`, `_is_first_run` flag); drop `_last_all_sensors_off` aggregate usage.
  - New: `maybe_delay` / `_maybe_delay_locked`, `_warn_if_delay_too_short`.
  - Rewrite: `check_and_set_deadline`, `_on_target_state_change`, first-run seeding.
  - Remove: `_handle_deadline_logic`, `_analyze_state_transitions`, `_check_expired_deadlines`, `_set_deadline_from_delay`, `_update_last_states` (aggregate), `_handle_first_run`.
  - `_turn_off_targets`: fire-time presence re-check.
  - `AutoOffManager.__init__` + `async_init_groups`: accept and forward `poll_interval`.
- Modify: `custom_components/auto_off/integration_manager.py`
  - Pass `poll_interval` to `AutoOffManager` (line ~60) and to the rebuild `SensorGroup` (line ~386).
- Create: `custom_components/auto_off/tests/test_inactivity_timer.py` (behavior tests).
- Modify: `custom_components/auto_off/tests/test_turn_off_race.py` (rename mocked method).
- Modify: `custom_components/auto_off/tests/test_sensor_group_smoke.py` if it references removed methods.
- Modify: `custom_components/auto_off/README.md` (Key principles rewrite).
- Modify: `custom_components/auto_off/manifest.json` (version bump).

Test runner note: the sandbox image's ruff/vulture/coverage gates may fail on a read-only cache. Use `AUTOQA_LINT=false AUTOQA_VULTURE=false AUTOQA_COVERAGE=false ./ha-test-kit/run_unit.sh ...` for all unit runs.

---

## Task 1: Thread poll_interval and add maybe_delay (extend-only)

Plumb `poll_interval` into the group, add the new fields, and introduce `maybe_delay` + the delay warning ALONGSIDE the existing logic (no wiring yet). Suite stays green.

**Files:**
- Modify: `custom_components/auto_off/auto_off.py`
- Modify: `custom_components/auto_off/integration_manager.py`
- Test: `custom_components/auto_off/tests/test_inactivity_timer.py` (new)

- [ ] **Step 1: Write failing unit tests**

Create `custom_components/auto_off/tests/test_inactivity_timer.py`:

```python
"""Behavior tests for the inactivity-timer deadline model.

Spec: docs/superpowers/specs/2026-06-04-inactivity-timer-model-design.md

Tests assert on observable effects: the on_deadline_change callback
(deadline timestamps), Target turn_off / hass.services.async_call, and
WARNING logs. They do not assert on private transition fields.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.auto_off.auto_off import GroupConfig, SensorGroup


def _hass(clock_start: float = 1000.0):
    hass = MagicMock()
    hass.loop = MagicMock()
    hass.loop.time = MagicMock(return_value=clock_start)
    hass.bus = MagicMock()
    hass.services = MagicMock()
    hass.services.async_call = AsyncMock()
    hass.states = MagicMock()
    hass.states.get = MagicMock(return_value=None)
    return hass


def _group(hass, *, delay, poll_interval=15, on_deadline_change=None):
    config = GroupConfig(
        targets=["light.kitchen"],
        sensors=["binary_sensor.motion"],
        sensor_templates=[],
        delay=delay,
    )
    return SensorGroup(
        hass,
        "g",
        config,
        on_deadline_change=on_deadline_change,
        manager=None,
        poll_interval=poll_interval,
    )


class TestMaybeDelayExtendOnly:
    """maybe_delay sets a deadline now()+delay, extend-only."""

    async def test_maybe_delay_sets_deadline_when_none(self):
        hass = _hass(clock_start=1000.0)
        group = _group(hass, delay=2)  # 2 minutes -> 120s
        await group.maybe_delay("test")
        # Deadline scheduled 120s in the future.
        assert group._timer_deadline == pytest.approx(1120.0)

    async def test_maybe_delay_never_shortens(self):
        hass = _hass(clock_start=1000.0)
        group = _group(hass, delay=10)  # 600s -> deadline 1600
        await group.maybe_delay("first")
        assert group._timer_deadline == pytest.approx(1600.0)
        # Now the clock advanced only slightly; a fresh call would compute
        # a smaller potential deadline -> must NOT shorten.
        hass.loop.time.return_value = 1005.0
        await group.maybe_delay("second")
        assert group._timer_deadline == pytest.approx(1600.0)

    async def test_maybe_delay_extends_when_later(self):
        hass = _hass(clock_start=1000.0)
        group = _group(hass, delay=10)  # 600s
        await group.maybe_delay("first")  # deadline 1600
        hass.loop.time.return_value = 1100.0
        await group.maybe_delay("second")  # potential 1700 > 1600
        assert group._timer_deadline == pytest.approx(1700.0)


class TestDelayWarning:
    """A WARNING fires once when delay <= poll_interval."""

    async def test_warns_when_delay_not_greater_than_poll(self, caplog):
        hass = _hass()
        group = _group(hass, delay=0, poll_interval=15)  # 0s <= 15s
        caplog.set_level(logging.WARNING, logger="custom_components.auto_off.auto_off")
        await group.maybe_delay("test")
        assert any(
            "poll_interval" in r.message and r.levelno == logging.WARNING
            for r in caplog.records
        ), f"expected delay/poll warning, got {[r.message for r in caplog.records]!r}"

    async def test_no_warning_when_delay_greater_than_poll(self, caplog):
        hass = _hass()
        group = _group(hass, delay=2, poll_interval=15)  # 120s > 15s
        caplog.set_level(logging.WARNING, logger="custom_components.auto_off.auto_off")
        await group.maybe_delay("test")
        assert not any(
            "poll_interval" in r.message for r in caplog.records
        )
```

- [ ] **Step 2: Run tests, expect failure**

```
AUTOQA_LINT=false AUTOQA_VULTURE=false AUTOQA_COVERAGE=false ./ha-test-kit/run_unit.sh custom_components/auto_off/tests/test_inactivity_timer.py
```

Expected: FAIL — `SensorGroup.__init__` has no `poll_interval` kwarg and `maybe_delay` does not exist.

- [ ] **Step 3: Add poll_interval + fields to SensorGroup.__init__**

In `custom_components/auto_off/auto_off.py`, change the `SensorGroup.__init__` signature. Find:

```python
    def __init__(
        self,
        hass: HomeAssistant,
        group_id: str,
        config: GroupConfig,
        on_deadline_change: Callable[[str, str | None], None] | None = None,
        *,
        manager: "Any | None" = None,
    ):
```

Replace with (add `poll_interval` keyword, default mirrors `DEFAULT_POLL_INTERVAL = 15`):

```python
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
```

In the body, near the other deadline fields (around `self._timer_deadline = None`), add:

```python
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
```

Leave the existing `self._last_all_sensors_off` / `self._last_any_target_on` fields in place for now (Task 2 removes them). They remain harmless.

- [ ] **Step 4: Add maybe_delay, _maybe_delay_locked, and _warn_if_delay_too_short**

In `auto_off.py`, add these methods to `SensorGroup` (place them right before the existing `_set_deadline_from_delay`):

```python
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
```

Note: `_start_deadline` already handles `force_deadline` and the `delay<=0` immediate-fire case. `datetime` is already imported at module top.

- [ ] **Step 5: Forward poll_interval through AutoOffManager**

In `auto_off.py`, find `AutoOffManager.__init__`:

```python
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
```

Add a `poll_interval` keyword:

```python
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
```

In `AutoOffManager.async_init_groups`, find the `SensorGroup(...)` construction and add `poll_interval=self._poll_interval`:

```python
                self._groups[group_id] = SensorGroup(
                    self.hass,
                    group_id,
                    group_config,
                    on_deadline_change=self._on_deadline_change,
                    manager=self._integration_manager,
                    poll_interval=self._poll_interval,
                )
```

- [ ] **Step 6: Pass poll_interval from IntegrationManager**

In `custom_components/auto_off/integration_manager.py`, find the `AutoOffManager(...)` construction (around line 60):

```python
        self.auto_off = AutoOffManager(
            hass,
            group_configs,
            on_deadline_change=self._on_deadline_change,
            integration_manager=self,
        )
```

Change to read the configured poll interval and pass it:

```python
        poll_interval = entry.data.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)
        self.auto_off = AutoOffManager(
            hass,
            group_configs,
            on_deadline_change=self._on_deadline_change,
            integration_manager=self,
            poll_interval=poll_interval,
        )
```

`CONF_POLL_INTERVAL` and `DEFAULT_POLL_INTERVAL` are already imported in this file (`from .const import CONF_GROUPS, CONF_POLL_INTERVAL, DOMAIN` and `DEFAULT_POLL_INTERVAL = 15` defined near the top — verify; if `DEFAULT_POLL_INTERVAL` is not in scope, it is defined at line ~24 of this file).

Also find the rebuild `SensorGroup(...)` (around line 386) and add `poll_interval`:

```python
        new_group = SensorGroup(
            self.hass,
            group_name,
            self.auto_off.config[group_name],
            on_deadline_change=self.auto_off._on_deadline_change,
            manager=self,
            poll_interval=self.auto_off._poll_interval,
        )
```

- [ ] **Step 7: Run new unit tests, expect pass**

```
AUTOQA_LINT=false AUTOQA_VULTURE=false AUTOQA_COVERAGE=false ./ha-test-kit/run_unit.sh custom_components/auto_off/tests/test_inactivity_timer.py
```

Expected: PASS (7 tests).

- [ ] **Step 8: Run full suite, expect no regressions**

```
AUTOQA_LINT=false AUTOQA_VULTURE=false AUTOQA_COVERAGE=false ./ha-test-kit/run_unit.sh
```

Expected: all previously-passing tests still pass (old deadline logic untouched; `maybe_delay` added but not yet wired).

- [ ] **Step 9: Commit**

```
git add custom_components/auto_off/auto_off.py custom_components/auto_off/integration_manager.py custom_components/auto_off/tests/test_inactivity_timer.py
git commit -m "feat(auto_off): add extend-only maybe_delay and poll_interval plumbing"
```

---

## Task 2: Rewrite the deadline state machine to the inactivity model

Wire `maybe_delay` into `check_and_set_deadline` and `_on_target_state_change`, remove the transition logic, and update legacy tests.

**Files:**
- Modify: `custom_components/auto_off/auto_off.py`
- Test: `custom_components/auto_off/tests/test_inactivity_timer.py`
- Modify: `custom_components/auto_off/tests/test_turn_off_race.py`
- Modify: `custom_components/auto_off/tests/test_sensor_group_smoke.py`

- [ ] **Step 1: Write failing integrated behavior tests**

Append to `custom_components/auto_off/tests/test_inactivity_timer.py`:

```python
def _hass_with_states(target_on: bool, presence_on: bool, clock=1000.0):
    """hass whose state lookups reflect a single target and single sensor."""
    hass = _hass(clock_start=clock)

    def _get(eid):
        st = MagicMock()
        if eid == "light.kitchen":
            st.state = "on" if target_on else "off"
            st.attributes = {}
            return st
        if eid == "binary_sensor.motion":
            st.state = "on" if presence_on else "off"
            st.attributes = {}
            return st
        return None

    hass.states.get = MagicMock(side_effect=_get)
    return hass


class TestInactivityModel:
    """check_and_set_deadline / _on_target_state_change behavior.

    Every test awaits ``_async_init_targets`` first: the SensorGroup
    constructor schedules target init as a background task that the tests
    do not otherwise wait for, so ``self._targets`` would be empty and
    ``any_target_on()`` would wrongly return False.
    """

    async def test_manual_on_no_presence_sets_full_delay_not_immediate(self):
        """Target turns on while unoccupied -> deadline now+delay, NOT fired."""
        hass = _hass_with_states(target_on=True, presence_on=False, clock=1000.0)
        events: list[tuple[str, str | None]] = []
        group = _group(
            hass, delay=2, poll_interval=15,
            on_deadline_change=lambda gid, iso: events.append((gid, iso)),
        )
        await group._async_init_targets()
        # Simulate the target's own off->on callback.
        await group._on_target_state_change(MagicMock(), False, True)
        # A non-null deadline was published and no immediate turn-off.
        assert group._timer_deadline == pytest.approx(1120.0)
        assert hass.services.async_call.await_count == 0

    async def test_no_target_on_cancels_deadline(self):
        hass = _hass_with_states(target_on=False, presence_on=False, clock=1000.0)
        events: list[tuple[str, str | None]] = []
        group = _group(
            hass, delay=2,
            on_deadline_change=lambda gid, iso: events.append((gid, iso)),
        )
        await group._async_init_targets()
        # Seed a deadline, then re-check with no target on.
        group._timer_deadline = 1120.0
        group._is_first_run = False
        await group.check_and_set_deadline()
        assert group._timer_deadline is None

    async def test_presence_on_extends_each_call(self):
        hass = _hass_with_states(target_on=True, presence_on=True, clock=1000.0)
        group = _group(hass, delay=10, poll_interval=15)  # 600s
        await group._async_init_targets()
        await group.check_and_set_deadline()  # first run seeds baseline
        first = group._timer_deadline
        assert first == pytest.approx(1600.0)
        # Advance clock by a patrol interval; presence still on -> extend.
        hass.loop.time.return_value = 1015.0
        await group.check_and_set_deadline()
        assert group._timer_deadline == pytest.approx(1615.0)

    async def test_presence_off_does_not_extend_steady_state(self):
        hass = _hass_with_states(target_on=True, presence_on=False, clock=1000.0)
        group = _group(hass, delay=10, poll_interval=15)
        await group._async_init_targets()
        # First run with target on seeds a baseline deadline.
        await group.check_and_set_deadline()
        seeded = group._timer_deadline
        assert seeded == pytest.approx(1600.0)
        # Later patrol tick, presence still off, no fresh turn-on -> noop.
        hass.loop.time.return_value = 1300.0
        await group.check_and_set_deadline()
        assert group._timer_deadline == pytest.approx(1600.0)  # unchanged

    async def test_second_target_on_extends(self):
        # Two targets; first on, second turns on later (presence off).
        hass = _hass(clock_start=1000.0)
        states = {"light.a": "on", "light.b": "off", "binary_sensor.motion": "off"}

        def _get(eid):
            if eid in states:
                st = MagicMock()
                st.state = states[eid]
                st.attributes = {}
                return st
            return None

        hass.states.get = MagicMock(side_effect=_get)
        config = GroupConfig(
            targets=["light.a", "light.b"],
            sensors=["binary_sensor.motion"],
            sensor_templates=[],
            delay=10,
        )
        group = SensorGroup(hass, "g", config, manager=None, poll_interval=15)
        await group._async_init_targets()
        # First evaluation seeds baseline (a is on).
        await group.check_and_set_deadline()
        assert group._timer_deadline == pytest.approx(1600.0)
        # Time passes; b turns on -> its callback extends.
        hass.loop.time.return_value = 1200.0
        states["light.b"] = "on"
        await group._on_target_state_change(MagicMock(), False, True)
        assert group._timer_deadline == pytest.approx(1800.0)
```

- [ ] **Step 2: Run, expect failures**

```
AUTOQA_LINT=false AUTOQA_VULTURE=false AUTOQA_COVERAGE=false ./ha-test-kit/run_unit.sh custom_components/auto_off/tests/test_inactivity_timer.py::TestInactivityModel
```

Expected: FAIL — old `check_and_set_deadline` uses the transition model (`_handle_first_run` + `_handle_deadline_logic`), so several assertions break (e.g. `test_manual_on_no_presence...` currently fires immediately with `delay` semantics through `target turning ON`).

- [ ] **Step 3: Rewrite check_and_set_deadline**

In `auto_off.py`, replace the entire body of `check_and_set_deadline` (keep the `_turn_off_lock.locked()` guard and its docstring intent). New version:

```python
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
```

- [ ] **Step 4: Rewrite _on_target_state_change**

Find `_on_target_state_change` and replace its body so a target turning on extends the deadline regardless of presence:

```python
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
```

- [ ] **Step 5: Remove the dead transition methods**

Delete these methods from `SensorGroup` entirely:
- `_handle_deadline_logic`
- `_analyze_state_transitions`
- `_check_expired_deadlines`
- `_set_deadline_from_delay`
- `_handle_first_run`
- `_update_last_states`
- `_is_first_run` METHOD (the boolean field of the same name added in Task 1 replaces it — make sure you remove the old `def _is_first_run(self)` method; the field `self._is_first_run` set in `__init__` is what remains).
- `_collect_current_state`, `_log_current_state`, `_log_state_transitions` are now unused by `check_and_set_deadline`. `_log_state_transitions` is referenced by `test_sensor_group_smoke.py`; KEEP `_log_state_transitions` for now (Step 8 updates the test). Remove `_collect_current_state` and `_log_current_state` only if nothing else references them — grep first:
  ```
  rg -n '_collect_current_state|_log_current_state' custom_components/auto_off/
  ```
  Remove only the ones with no remaining references.

Also remove the now-unused `self._last_all_sensors_off` and `self._last_any_target_on` fields from `__init__` IF nothing references them after the deletions. Grep:
```
rg -n '_last_all_sensors_off|_last_any_target_on' custom_components/auto_off/
```
If only `__init__` assigns them, delete those two lines.

- [ ] **Step 6: Run the new behavior tests**

```
AUTOQA_LINT=false AUTOQA_VULTURE=false AUTOQA_COVERAGE=false ./ha-test-kit/run_unit.sh custom_components/auto_off/tests/test_inactivity_timer.py
```

Expected: PASS (TestMaybeDelayExtendOnly, TestDelayWarning, TestInactivityModel).

- [ ] **Step 7: Fix test_turn_off_race.py**

This test mocks the removed `_set_deadline_from_delay`. Open `custom_components/auto_off/tests/test_turn_off_race.py`. Replace the mock and assertion:

Find:
```python
        group._set_deadline_from_delay = AsyncMock()
```
Replace with:
```python
        group._maybe_delay_locked = AsyncMock()
```

Find:
```python
        group._set_deadline_from_delay.assert_not_awaited()
```
Replace with:
```python
        group._maybe_delay_locked.assert_not_awaited()
```

The race contract is unchanged: while `_turn_off_lock` is held, `check_and_set_deadline` returns early and never calls `_maybe_delay_locked`. Verify the test still reflects that (it constructs the lock-held scenario).

Run it:
```
AUTOQA_LINT=false AUTOQA_VULTURE=false AUTOQA_COVERAGE=false ./ha-test-kit/run_unit.sh custom_components/auto_off/tests/test_turn_off_race.py
```
Expected: PASS.

- [ ] **Step 8: Fix test_sensor_group_smoke.py**

`test_sensor_group_smoke.py` calls `sg._log_state_transitions({...})`. If you kept `_log_state_transitions` (Step 5), it still works — but it referenced `self._last_all_sensors_off` / `self._last_any_target_on`. If you removed those fields, `_log_state_transitions` will raise `AttributeError`.

Decision: `_log_state_transitions` is debug-logging only and tied to the removed transition model. Remove `_log_state_transitions` from `auto_off.py` AND update the smoke test to assert on something current. Replace the smoke test body:

```python
async def test_sensor_group_state_logging_no_attribute_error(caplog):
    sg_hass = MagicMock()
    sg_hass.loop = MagicMock()
    sg_hass.loop.time = MagicMock(return_value=1000.0)
    sg_hass.bus = MagicMock()
    sg_hass.states = MagicMock()
    sg_hass.states.get = MagicMock(return_value=None)
    cfg = GroupConfig(
        targets=["light.x"],
        sensors=["binary_sensor.m"],
        sensor_templates=[],
        delay=2,
    )
    sg = SensorGroup(sg_hass, "g1", cfg, on_deadline_change=None, poll_interval=15)
    # check_and_set_deadline must run without raising on a group whose
    # entities are absent from the state machine.
    await sg.check_and_set_deadline()
```

Run it:
```
AUTOQA_LINT=false AUTOQA_VULTURE=false AUTOQA_COVERAGE=false ./ha-test-kit/run_unit.sh custom_components/auto_off/tests/test_sensor_group_smoke.py
```
Expected: PASS.

- [ ] **Step 9: Run full suite, fix any remaining fallout**

```
AUTOQA_LINT=false AUTOQA_VULTURE=false AUTOQA_COVERAGE=false ./ha-test-kit/run_unit.sh
```

Other tests may reference removed methods or assert the old "deadline set by sensors turning OFF" / immediate-off-on-delay-0 behavior. For each failure:
- If it asserts the OLD transition model, update it to the inactivity model or remove it (document which in the commit).
- If it references a removed method name, repoint to the new API.

Likely candidates: `test_turn_off_race.py` (done), `test_sensor_group_smoke.py` (done), possibly `test_integration_manager.py` or `test_target_reexpand.py` if they construct groups and assert deadline behavior. Do NOT weaken assertions; adapt them to the new model.

Expected end state: full suite green.

- [ ] **Step 10: Commit**

```
git add custom_components/auto_off/auto_off.py custom_components/auto_off/tests/
git commit -m "refactor(auto_off): inactivity-timer deadline model (extend-only)"
```

---

## Task 3: Fire-time presence re-check

Guard the turn-off dispatch so presence returning between the last patrol tick and the timer firing aborts the turn-off and re-extends.

**Files:**
- Modify: `custom_components/auto_off/auto_off.py` (`_turn_off_targets`)
- Test: `custom_components/auto_off/tests/test_inactivity_timer.py`

- [ ] **Step 1: Write failing test**

Append to `custom_components/auto_off/tests/test_inactivity_timer.py`:

```python
class TestFireTimePresenceRecheck:
    """If presence is on at the moment the timer fires, abort turn-off."""

    async def test_presence_on_at_fire_aborts_turn_off(self):
        hass = _hass_with_states(target_on=True, presence_on=True, clock=1000.0)
        group = _group(hass, delay=10, poll_interval=15)
        await group._async_init_targets()
        # Directly invoke the turn-off phase as the timer would.
        await group._turn_off_targets()
        # Presence is on -> no turn-off dispatched, deadline re-extended.
        assert hass.services.async_call.await_count == 0
        assert group._timer_deadline == pytest.approx(1600.0)
```

- [ ] **Step 2: Run, expect failure**

```
AUTOQA_LINT=false AUTOQA_VULTURE=false AUTOQA_COVERAGE=false ./ha-test-kit/run_unit.sh custom_components/auto_off/tests/test_inactivity_timer.py::TestFireTimePresenceRecheck
```

Expected: FAIL — current `_turn_off_targets` dispatches turn-offs unconditionally.

- [ ] **Step 3: Add the fire-time recheck**

In `auto_off.py` `_turn_off_targets`, inside `async with self._turn_off_lock:`, right after the timer-state clearing block:

```python
        async with self._turn_off_lock:
            # Clear timer state BEFORE turning off - timer has fired
            self._timer = None
            self._timer_deadline = None
            self._notify_deadline_change()

            # Fire-time safety: presence may have returned between the last
            # patrol extend and this firing. If so, abort and re-extend.
            if not await self.all_sensors_off():
                async with self._lock:
                    await self._maybe_delay_locked("presence at fire time")
                _LOGGER.info(
                    "[Group %s] Turn-off aborted: presence on at fire time",
                    self.group_id,
                )
                return
```

Lock ordering note: `_turn_off_targets` acquires `_turn_off_lock` then `_lock`. No other path acquires `_lock` then `_turn_off_lock` (`check_and_set_deadline` only checks `_turn_off_lock.locked()` without acquiring it), so there is no deadlock.

- [ ] **Step 4: Run test, expect pass**

```
AUTOQA_LINT=false AUTOQA_VULTURE=false AUTOQA_COVERAGE=false ./ha-test-kit/run_unit.sh custom_components/auto_off/tests/test_inactivity_timer.py::TestFireTimePresenceRecheck
```

Expected: PASS.

- [ ] **Step 5: Run full suite**

```
AUTOQA_LINT=false AUTOQA_VULTURE=false AUTOQA_COVERAGE=false ./ha-test-kit/run_unit.sh
```

Expected: green. The `test_turn_off_race.py` ensure-loop tests must still pass (the early `return` only triggers when sensors are on; those tests keep sensors off).

- [ ] **Step 6: Commit**

```
git add custom_components/auto_off/auto_off.py custom_components/auto_off/tests/test_inactivity_timer.py
git commit -m "feat(auto_off): re-check presence at timer fire time"
```

---

## Task 4: Docs + version bump

**Files:**
- Modify: `custom_components/auto_off/README.md`
- Modify: `custom_components/auto_off/manifest.json`

- [ ] **Step 1: Rewrite the "Key principles" section**

In `custom_components/auto_off/README.md`, replace the "Key principles" bullet list with:

```markdown
## Key principles

- **Inactivity timer**: the deadline is a single point in time that
  activity pushes forward. When activity stops, the deadline stops
  moving and eventually fires, turning off every target.
- **Activity = a target turning on, or presence detected**: a target
  switching on extends the deadline (even with no presence, giving a
  manually-switched light a full delay); while presence is detected, the
  periodic patrol re-extends the deadline every `poll_interval`.
- **Extend-only**: a new deadline replaces the current one only if it is
  later. Activity never shortens an existing deadline.
- **Cancel only when nothing is on**: the deadline is cancelled when no
  target is on. Presence does not cancel the deadline; it extends it.
- **delay must exceed poll_interval**: the patrol re-extends every
  `poll_interval` seconds (default 15; commonly 60). The group `delay`
  (in minutes) must be greater than `poll_interval`, otherwise the timer
  can fire between patrol ticks while occupied and flap the lights off.
  A WARNING is logged when a group's delay does not exceed
  `poll_interval`. Set `delay` to at least a couple of minutes.
- **Ensure-off retry**: at deadline expiry auto_off does an initial
  `turn_off` dispatch and then runs a bounded retry loop for
  `ENSURE_WINDOW_SEC` seconds (60s), re-issuing `turn_off` every
  `ENSURE_INTERVAL_SEC` seconds (10s) on any target still on while
  sensors stay off. The loop aborts the moment any sensor reports on
  again.
- **Recovery after restart**: on startup a target that is already on is
  granted a fresh full delay (the deadline is re-seeded from the restart
  moment). While presence is on, patrol begins extending immediately.
```

Remove the old bullets that described "Deadline exists only in one state", "Activity cancels the deadline", "Delay extends, never shortens" (superseded), and "Recovery from attributes" (superseded by the restart-baseline behavior). Keep the `auto_off_deadline` attribute section that precedes/follows it intact.

- [ ] **Step 2: Bump manifest version**

In `custom_components/auto_off/manifest.json`, change `version` to a fresh YYMMDDhhmm timestamp later than the current value (current is `2606040920`). Use `2606041600`.

- [ ] **Step 3: Run full suite as sanity check**

```
AUTOQA_LINT=false AUTOQA_VULTURE=false AUTOQA_COVERAGE=false ./ha-test-kit/run_unit.sh
```

Expected: green.

- [ ] **Step 4: Commit**

```
git add custom_components/auto_off/README.md
git commit -m "docs(auto_off): document inactivity-timer model and delay>poll_interval"

git add custom_components/auto_off/manifest.json
git commit -m "chore(auto_off): bump version after inactivity-timer model"
```

---

## Post-implementation (operational, after deploy)

Not a code task — performed against production after deploy + restart:

1. Reconfigure the `out_light_all` group from `delay: 0` to a value
   greater than `poll_interval` (production `poll_interval` is 60s, so
   use e.g. 3 minutes) via `auto_off.set_group`.
2. Verify: manually turn on a single porch light while unoccupied; it
   stays on (deadline ~3 min ahead) instead of switching off in seconds.
3. Verify: with presence active the light stays on; ~3 min after presence
   ends it turns off.

---

## Self-review notes

- Spec coverage: maybe_delay extend-only (Task 1), reactions / per-target
  edge / first-run baseline / cancel-on-no-target (Task 2), removed
  transition methods (Task 2 Step 5), delay>poll_interval warning
  (Task 1 + Task 4 docs), fire-time recheck (Task 3), restart baseline
  (Task 2 first-run), README rewrite + version (Task 4). All spec
  sections covered.
- Type/signature consistency: `maybe_delay(reason)` /
  `_maybe_delay_locked(reason)` / `check_and_set_deadline(*, target_just_turned_on=False)`
  used consistently across tasks; `poll_interval` keyword threaded through
  `SensorGroup`, `AutoOffManager`, and both `IntegrationManager` call
  sites.
- Tests stay behavior-based: assertions on `_timer_deadline`
  (the scheduled deadline, an observable scheduling outcome via the same
  value fed to `on_deadline_change`) and `hass.services.async_call`
  counts, not on removed transition fields.
```
