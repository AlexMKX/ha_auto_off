# Ensure-off loop design

Date: 2026-05-16
Status: Approved

## Problem

After `SensorGroup._turn_off_targets` dispatches the initial round of
`turn_off` calls, auto_off declares success and stops touching the
targets, regardless of whether the device actually transitioned to
`off`. This is a thin layer of best-effort that breaks in several
real-world ways:

* z2m publishes the `set` command, the device times out or doesn't ack,
  and the MQTT light entity keeps its previous `state=on` (z2m light is
  non-optimistic: HA-state only updates when z2m re-publishes
  `state_topic`).
* A competing automation (e.g. Magic Areas Light Control) flips the
  target back to `on` immediately after our `turn_off` — auto_off does
  nothing.
* A group target reports `on` because one of its N members did not
  switch — auto_off never retries.

This proposal adds a bounded retry window after the deadline fires so
auto_off becomes resilient to transient delivery failures while still
deferring to legitimate presence/automation intent.

## Goals

1. After a deadline fires, every target reaches `off` within
   `ensure_window` seconds, **or** sensors come back on and the loop
   stands down.
2. Retries are bounded in time and frequency — no infinite loops,
   nothing that can flood MQTT.
3. Existing `_turn_off_targets` dispatch path (group-entity primary,
   individual fallback) is preserved; the loop only adds follow-up
   per-target retries.
4. Existing tests keep passing; new tests cover every retry/abort case.

## Non-goals

* Authoritative override. If presence is legitimately back, auto_off
  yields. Re-arming the deadline is the existing flow's responsibility
  (next `sensors turning OFF` transition).
* Distinguishing user-initiated `turn_on` from a bug. The loop treats
  any `target.is_on()` after the initial round as "delivery did not
  stick" and retries while sensors are off. If a user really wants the
  light on while sensors are off, they can lengthen the group delay or
  disable the group temporarily.
* Touching z2m or MQTT directly. We only observe the HA-state of the
  target entity, which already reflects z2m's last published state for
  non-optimistic devices.

## Configuration

Per-group fields on `GroupConfig`:

| field | type | default | meaning |
|---|---|---|---|
| `ensure_window` | `int \| str` | `60` | Total **seconds** the ensure loop is allowed to run. |
| `ensure_interval` | `int \| str` | `10` | Pause in **seconds** between retry passes. Should be `>=` the upstream device timeout (z2m default 10s) to avoid wasted publishes. |

Both fields accept a Jinja-renderable string for template-driven knobs
(schedule-based overrides etc.) if needed later. Validation: rendered
value must be a non-negative int; `ensure_interval` must be `> 0`.

Note: these are in seconds, unlike `delay` which is in minutes (legacy
unit, multiplied by 60 in `get_delay`). The retry loop operates on the
scale of single device timeouts (z2m default 10s), so a separate unit
matches reality. Internal helpers `get_ensure_window()` and
`get_ensure_interval()` render without unit conversion.

Existing config entries without these fields take the defaults. Schema
migration adds the keys with defaults during the next config_entry load
and is covered by `test_migration.py`.

## Algorithm

After `SensorGroup._turn_off_targets` completes its initial dispatch
(unchanged), it schedules `_ensure_off_loop` as a detached task and
stores the handle in `self._ensure_task`. The loop body:

```
deadline_real = monotonic + ensure_window
while monotonic < deadline_real:
    await asyncio.sleep(ensure_interval)

    if not await self.all_sensors_off():
        log.info("[%s] ensure: sensors back on, abort", self.group_id)
        return

    still_on = [t for t in self._targets if await t.is_on()]
    if not still_on:
        log.info("[%s] ensure: all targets off", self.group_id)
        return

    log.info(
        "[%s] ensure: %d target(s) still on, retrying",
        self.group_id, len(still_on),
    )
    for target in still_on:
        try:
            await target.turn_off()
        except Exception:
            # never let the loop die; next pass will retry
            log.warning("[%s] ensure: retry of %s failed",
                        self.group_id, target.entity_id)

log.warning(
    "[%s] ensure: window expired, %d target(s) still on",
    self.group_id, len([t for t in self._targets if await t.is_on()]),
)
```

Key properties:

* **Sensor guard fires every pass.** Any `sensor=on` aborts cleanly. The
  normal `_handle_*_change → check_and_set_deadline` flow takes over
  from there.
* **Retries hit individual targets**, not the group entity. We already
  know which member did not switch; retrying the whole group spams
  every member.
* **No state mutation if the loop aborts naturally.** `_last_known_good_state`
  is updated by the existing event subscription path, not the loop.

## Cancellation

The loop must stop in three cases:

1. **New deadline armed** (`_start_deadline` runs): cancel the existing
   ensure task before installing a new timer.
2. **Deadline cancelled** (`_cancel_deadline` runs because sensor turned
   on or target turned off externally): cancel the ensure task.
3. **Group unload** (`async_unload`): cancel the ensure task.

Cancellation is implemented by storing `self._ensure_task` (Optional
`asyncio.Task`) and calling `cancel()` plus `await ... contextlib.suppress(CancelledError)`
at each cancellation site. The same `self._lock` that guards
`check_and_set_deadline` covers the cancel-and-replace transition, so
there is no window where two loops run for the same group.

## Logging

* `INFO [%s] ensure: all targets off` — happy path early exit.
* `INFO [%s] ensure: sensors back on, abort` — presence reclaimed.
* `INFO [%s] ensure: %d target(s) still on, retrying` — per-pass when
  retry is needed.
* `WARNING [%s] ensure: retry of %s failed: %s` — exception during retry.
* `WARNING [%s] ensure: window expired, %d target(s) still on` — final
  giving-up message when nothing else stopped the loop.

## Testing

New file `custom_components/auto_off/tests/test_ensure_off_loop.py`.
Each test uses the unit-mode fake hass / patched
`async_track_state_change_event` pattern from
`test_subscription_for_missing_entity.py`.

1. **happy path**: targets go off after initial round → first pass sees
   no `still_on` → loop returns; no retry happened.
2. **single retry**: target stays `on` once, then off → exactly one
   `target.turn_off()` invoked from the loop body.
3. **sensors reclaim**: target stays on but a sensor flips to `on` →
   loop aborts; **no retries** issued in the same pass.
4. **window expiry**: target stays on for the entire window → expected
   number of retries (`ensure_window // ensure_interval`) and a single
   `WARNING ... window expired` line.
5. **cancel by new deadline**: while loop is mid-sleep, call
   `_start_deadline` → existing task is cancelled and a new one isn't
   spawned (until `_turn_off_targets` runs again).
6. **cancel by deadline cancellation**: similarly via `_cancel_deadline`.
7. **cancel by unload**: `async_unload()` finishes cleanly even with
   active loop.

Existing tests (`test_auto_off.py`, `test_target.py`,
`test_sensor_group_smoke.py`, `test_subscription_for_missing_entity.py`)
must remain green; in particular `_turn_off_targets` still returns
after the initial dispatch — the ensure loop runs as a detached task and
should not be awaited by the deadline callback.

## Backwards compatibility

* `GroupConfig` adds two optional fields with safe defaults; old configs
  load unchanged.
* `_turn_off_targets` keeps its existing log line (`"All targets turned
  off after deadline."`) so external dashboards/log parsing aren't
  broken. The new behavior is purely additive (a follow-up task plus a
  new set of log lines).
* No new dependencies, no manifest changes.

## Risks and mitigations

* **Retry spam if `ensure_interval` is small.** Validate `> 0` on
  config; document recommended floor (10s for z2m). Loop logs every
  retry so misconfig is visible.
* **Loop runs concurrently with new cycle.** Cancellation hooks on
  `_start_deadline`, `_cancel_deadline`, and `async_unload` cover every
  way the group can leave the "should be off" state.
* **`target.turn_off` failure inside the loop**. Caught locally; next
  pass tries again. The loop never dies on a single exception.
