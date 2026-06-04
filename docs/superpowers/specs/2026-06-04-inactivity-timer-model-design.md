# Inactivity-timer deadline model

## Problem

The current deadline state machine arms the turn-off deadline whenever
"any target is on AND all sensors are off", triggered by either
transition `target_turned_on` or `sensors_turned_off`. Combined with
leaf-level target tracking (now correct after the re-expansion work) and
a group configured with `delay: 0`, this produces a user-visible defect:
manually turning on a light while no presence is detected turns it off
within seconds.

Root cause: the model treats "a target turned on while presence is
absent" as a reason to start the (possibly zero) turn-off delay. That is
not "auto off after inactivity"; it is "forbid manual control while
unoccupied".

## Goal

Replace the two-state arming logic with an inactivity-timer model. The
deadline is a single point in time that activity pushes forward
(extend-only). When activity stops, the deadline stops moving and
eventually fires, turning off the targets.

This makes the integration behave like a classic occupancy lighting
timer: lights stay on while occupied (presence keeps extending the
deadline) and turn off `delay` after the last activity.

## Core mechanism: maybe_delay (extend-only)

```
maybe_delay():
    potential = now() + get_delay()
    if current_deadline is None or potential > current_deadline:
        current_deadline = potential   # (re)schedule timer + notify
    # else: noop  (never shorten an existing deadline)
```

`get_delay()` is per-group (no per-target delay) and supports the
existing Jinja `delay` template. `now()` is the event-loop monotonic
clock (`hass.loop.time()`), consistent with the existing timer.

## Reactions

`maybe_delay` / cancel are driven by three entry points:

1. Per-target state change `off -> on` (`_on_target_state_change` with
   `new_state` truthy): call `maybe_delay`. This is "a light came on" and
   applies regardless of presence. It gives a manually-turned-on light a
   full `delay` grace even with no presence.

2. `check_and_set_deadline` (invoked on sensor state changes and on every
   patrol tick):
   - `target_on = any_target_on()`
   - If `not target_on`: `cancel_deadline()`; return. Nothing to manage.
   - `presence_on = not all_sensors_off()`
   - If `presence_on`: `maybe_delay()`. Activity ongoing; keeps the
     deadline in the future while occupied (covers the patrol-extend
     requirement).
   - Else (target on, presence off): noop. Let the existing deadline ride
     to expiry.

3. First run / restart with a target already on: `maybe_delay()` to seed
   a baseline deadline of `now() + delay` from startup.

### Removed behaviour

- `_analyze_state_transitions` and its `target_turned_on`,
  `sensors_turned_off`, `sensors_turned_on` branches.
- The `all_sensors_off` gate on arming.
- `_check_expired_deadlines` ("no timer recalculated").
- Presence turning on no longer *cancels* the deadline; it *extends* it.
  The deadline is cancelled only when no target is on.

### Presence end is NOT an explicit trigger

By design choice (documented decision), the `presence on -> off`
transition does not call `maybe_delay`. The deadline was already being
extended by patrol while presence was on, so when presence ends the
deadline sits at `last_extend_tick + delay`. The effective turn-off time
is therefore between `presence_end + delay - poll_interval` and
`presence_end + delay`. This imprecision (up to `poll_interval`) is
accepted in exchange for the robustness of patrol-driven extension, which
survives missed state-change events and HA restarts.

## Hard constraint: delay must exceed poll_interval

Patrol extends the deadline once per `poll_interval` (default 60s). For
the deadline to stay in the future during continuous presence, the delay
must be strictly greater than `poll_interval`. Otherwise the timer can
fire between patrol ticks while occupied, turning lights off mid-presence
(then re-extending on the next tick: flapping).

`delay` is stored in minutes and converted via `* 60`. With the default
`poll_interval = 60s`:

- `delay >= 2` minutes: safe.
- `delay == 1` minute: borderline (equals poll_interval).
- `delay == 0`: broken (immediate fire on every `maybe_delay`, including
  during presence).

### Handling

- Document the constraint in `README.md`.
- Emit a WARNING when a group's effective delay (`get_delay()` seconds)
  is `<= poll_interval`. The warning is logged:
  - at `SensorGroup` init (once, best-effort: if the delay template
    renders to a constant), and
  - whenever `maybe_delay` computes `get_delay() <= poll_interval`
    (rate-limited to once per group per process via a flag, to avoid
    hot-path spam).
- No clamping. The user reconfigures the group. The production
  `out_light_all` group (currently `delay: 0`) must be set to a sane
  value (e.g. 3 minutes) after deploy. This is an operational action, not
  a code change.

The `poll_interval` value is available to the group via the manager;
`SensorGroup` already holds a `manager` reference. If the poll interval
is not reachable from the group, pass it into `SensorGroup.__init__`
from `AutoOffManager` (it owns `poll_interval`).

## Timer fire behaviour

When the timer fires (`_turn_off_targets`):

- Before dispatching turn-offs, re-check `all_sensors_off()`. If presence
  has returned (race between the last patrol tick and the fire), call
  `maybe_delay()` and abort the turn-off instead. This is a cheap safety
  net on top of the existing ensure-off loop (which already aborts if
  sensors come back on mid-retry).

The ensure-off retry loop is unchanged.

## Recovery after restart

First run with a target on calls `maybe_delay`, seeding
`now() + delay`. This supersedes the previous `auto_off_deadline`
attribute-based timer reconstruction: recovery now means "grant a full
delay from restart". If presence is on at restart, patrol immediately
begins extending. The `auto_off_deadline` attribute continues to be
written on targets for UI/observability, but is no longer read back to
reconstruct the timer.

## State tracking simplification

`_last_any_target_on` is retained only to detect the per-target
`off -> on` edge in `_on_target_state_change` (entry point 1). The
aggregate `_last_all_sensors_off` and the transition analysis are no
longer needed for arming and are removed. `_is_first_run` remains to seed
the startup baseline.

Note on per-target edges: `_on_target_state_change` already receives
`(target, old_state, new_state)` per target and fires only on real
changes. Entry point 1 uses `new_state` truthy directly, so each target's
own `off -> on` is observed independently (turning on a second target
while the first is already on still extends the deadline).

## Files affected

- `custom_components/auto_off/auto_off.py`
  - `SensorGroup`: rework `check_and_set_deadline`,
    `_handle_deadline_logic` (replaced by extension logic),
    `_handle_first_run`, `_on_target_state_change`,
    `_set_deadline_from_delay` (becomes `maybe_delay`), remove
    `_analyze_state_transitions`, `_check_expired_deadlines`,
    `_update_last_states` aggregate bits.
  - `_turn_off_targets`: add fire-time presence re-check.
  - `SensorGroup.__init__`: accept / access `poll_interval` for the
    delay-vs-poll warning.
  - `AutoOffManager`: pass `poll_interval` to groups if not already
    reachable.
- `custom_components/auto_off/README.md`: rewrite "Key principles" to
  describe the inactivity-timer model and the `delay > poll_interval`
  constraint.
- `custom_components/auto_off/manifest.json`: version bump.

## Tests

Behaviour-based unit tests in
`custom_components/auto_off/tests/` (new module
`test_inactivity_timer.py`, or extend existing deadline tests). Use the
existing `MagicMock` hass fixture. Assert on the `on_deadline_change`
callback (deadline timestamps) and on `Target.turn_off` /
`hass.services.async_call`.

1. `test_manual_on_no_presence_keeps_light_for_delay`: target turns on,
   presence off, `delay=2min`. Assert a deadline of ~`now+120s` is set
   (non-null), and NOT fired immediately.
2. `test_manual_on_no_presence_fires_after_delay`: same setup; advance
   the loop clock past the deadline; assert turn-off dispatched.
3. `test_presence_extends_deadline_via_patrol`: target on, presence on.
   Run two patrol ticks (simulated) advancing the clock by
   `poll_interval` each; assert the deadline moves forward each tick
   (extend-only) and no turn-off occurs.
4. `test_deadline_rides_after_presence_ends`: target on, presence on
   (deadline extended), then presence off; no further extension; advance
   clock; assert turn-off after the last-extended deadline.
5. `test_maybe_delay_never_shortens`: set a far-future deadline, then a
   call with a smaller delay; assert the deadline is unchanged.
6. `test_all_targets_off_cancels_deadline`: deadline active, all targets
   turn off; assert deadline cancelled (callback fires None).
7. `test_fire_time_presence_recheck_aborts_turn_off`: timer fires but
   `all_sensors_off()` returns False at that moment; assert no turn-off
   and the deadline is re-extended.
8. `test_warning_when_delay_not_greater_than_poll_interval`: group with
   `delay=0` (or `delay*60 <= poll_interval`); assert a WARNING is logged
   naming the group and the constraint.
9. `test_second_target_on_extends_deadline`: first target on (deadline
   set), second target turns on later (presence off); assert the deadline
   extended to the second turn-on time + delay.

Existing tests that encode the old transition model
(`test_auto_off.py` deadline-logic tests, any test asserting "deadline
set by sensors turning OFF" or immediate off on `delay=0` with target on)
must be updated or removed to match the new model.

E2E: extend `test_integration_e2e.py` only if cheaply expressible;
otherwise rely on the unit suite plus the production verification
(reconfigure `out_light_all` to `delay: 3`, confirm manual leaf turn-on
stays on, and turns off ~3 min after presence ends).

## Rollout

- Version bump.
- After deploy: reconfigure the production `out_light_all` group from
  `delay: 0` to a value `> poll_interval` (e.g. 3 minutes) via
  `auto_off.set_group`. Until then the WARNING fires and the group
  behaves degenerately (immediate off), which is strictly no worse than
  today.
- No config-schema migration; `GroupConfig` is unchanged.
