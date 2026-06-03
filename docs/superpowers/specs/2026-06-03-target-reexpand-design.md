# Dynamic target re-expansion

## Problem

`SensorGroup._init_from_config` calls `expand_group_targets` once at integration
setup time. When a root target is an HA group helper (e.g.
`light.example_root_group`) that is not yet registered in `hass.states` at that
moment (typical during HA bootstrap), `expand_group_targets` falls back to
treating the root as a leaf and subscribes only to the group entity itself.

Two downstream failures:

1. The group entity may emit stale or no `state_changed` events for its own
   computed state (observed in production: a root group stuck at `off` while a
   member is `on`). The deadline never starts.
2. Even if the group entity is healthy, its `state` reflects the group mode
   (any-on vs all-on). For `all-on` groups, the deadline would only start when
   every member is on, which is not the intended semantics for auto-off.

## Goal

Subscribe `auto_off` to the actual leaves of every root target, and re-subscribe
whenever the root's member list changes at runtime. The list of "raw" targets
stored on `GroupConfig` is untouched so `dump_group` still reports user intent.

## Non-goals

- Subscribing to intermediate groups inside a root. Only roots from user config
  are watched (see Caveat below).
- Periodic rescan of the expansion. Re-expansion is event-driven only.
- Persisting `Target._last_known_good_state` across full rebuilds. Diff-based
  updates preserve it for members that remain in the set.

## Approach

Watch only the root entity ids the user listed in `config.targets`. When a
root's `attributes.entity_id` changes, recompute the leaf list via
`expand_group_targets` and diff against the current set:

- Remove `Target`s whose entity_id is no longer in the leaf set
  (`stop_tracking` + drop from `self._targets`).
- Create new `Target`s for entity_ids that appeared (`start_tracking`).
- Members present in both sets are left untouched, preserving their
  `_last_known_good_state`.

After the diff, invoke `check_and_set_deadline()` so the existing deadline
state machine processes the change naturally:

| Was `_last_any_target_on` | New `any_target_on()` | Outcome |
|---|---|---|
| False | True (newly added leaf is on) | `target_turned_on` transition -> deadline starts (if sensors off) |
| True | False (removed leaf was the only on one) | `not target_on` branch -> deadline cancelled |
| True | True | deadline left as is |
| False | False | no-op |

No special-case code is added for the re-expand path; the state machine is
reused as-is.

## Detailed design

### Changes to `SensorGroup`

New fields:

- `self._root_targets: list[str]` - copy of `config.targets`, untouched.
- `self._root_unsubs: list[Callable[[], None]]` - root state-change
  subscriptions.
- `self._current_leaves: list[str]` - ordered cache of the most recently
  expanded leaf set. Order matches the output of `expand_group_targets`
  (first-seen during recursive walk), which is the same order callers see in
  logs and turn-off dispatch today.

### Async initialisation

`SensorGroup.__init__` and `_init_from_config` are synchronous today
(`custom_components/auto_off/auto_off.py::SensorGroup.__init__`). To introduce
`await`-able re-expand:

1. Split target setup out of `_init_from_config` into a new async method
   `_async_init_targets()`.
2. From `_init_from_config` schedule it via
   `asyncio.create_task(self._async_init_targets())`, mirroring how sensors are
   already started (`auto_off.py:450, 462`). The synchronous constructor stays
   synchronous; consistent with the existing pattern in this file.
3. `_async_init_targets()`:
   - Sets `self._root_targets = list(self._config.targets)`.
   - For each valid root entity_id (see "Invalid root targets" below),
     installs an `async_track_state_change_event` subscription with
     `_on_root_attributes_change` and appends the unsubscribe callable to
     `self._root_unsubs`.
   - Calls `await self._reexpand_targets(initial=True)`.

### `_on_root_attributes_change(event)`

- Pull `old_state` and `new_state` from `event.data`.
- Normalise each side via `_extract_member_list(state) -> list[str] | None`
  (helper, see below).
- If both normalisations are equal (compared as ordered lists), return: this is
  a self-state change of the root with no membership change.
- Otherwise schedule `asyncio.create_task(self._reexpand_targets())`.
  The callback is registered as a regular HA event listener; HA event listeners
  expect a sync callable or a coroutine - we use the sync callable form and
  spawn the task explicitly so that long re-expand work cannot stall the event
  bus.

### `_extract_member_list(state) -> list[str] | None`

- Returns `None` if `state` is None, has no `attributes`, or the
  `attributes.entity_id` value is not a `list`.
- Returns a list[str] of stringy members otherwise (filters non-str via
  `isinstance(x, str)` to match `expand_group_targets`).
- Empty list is treated as `None` (no members = not a group for our purposes).

### `_reexpand_targets(*, initial: bool = False)`

Two distinct phases. Lock discipline is critical:

Phase 1 - diff (under `self._lock`):

1. `new_leaves = expand_group_targets(self.hass, self._root_targets)` (list,
   preserves order).
2. `new_leaf_set = set(new_leaves)`,
   `current_leaf_set = set(self._current_leaves)`.
3. `added = [eid for eid in new_leaves if eid not in current_leaf_set]`
   (preserves expansion order).
4. `removed = [eid for eid in self._current_leaves if eid not in new_leaf_set]`.
5. For each `entity_id` in `removed`: find the matching `Target` in
   `self._targets` (single-pass `next(...)`); `await target.stop_tracking()`;
   remove from `self._targets`.
6. For each `entity_id` in `added`: build a `Target`, append to
   `self._targets`, `await target.start_tracking()` (NOT
   `asyncio.create_task` - the subscription must be live before the deadline
   recheck observes state).
7. `self._current_leaves = new_leaves`.
8. If `added` or `removed` is non-empty, log at INFO: leaf diff with group_id,
   added list, removed list.

Phase 2 - deadline recheck (after lock release):

- If `initial` is True: do not call `check_and_set_deadline()` directly. The
  existing first-run path inside `check_and_set_deadline` handles startup
  consistently; let the periodic worker or any incoming state-change event
  drive it. Calling it eagerly here re-introduces the startup race we are
  trying to remove.
- If `initial` is False and `added or removed`:
  `await self.check_and_set_deadline()`. The new state set is now the source
  of truth; the existing transition logic in `_handle_deadline_logic` decides
  start/cancel/no-op based on `_last_any_target_on` vs current
  `any_target_on()` (see the table in "Approach").

Implementation must NOT call `check_and_set_deadline` while holding
`self._lock`: `check_and_set_deadline` does `async with self._lock` itself
(`auto_off.py::check_and_set_deadline`), which would self-deadlock.

### Interaction with `_ensure_off_loop`

`_ensure_off_loop` iterates `self._targets` with `await` calls inside the loop
(`auto_off.py::_ensure_off_loop`). If `_reexpand_targets` mutates
`self._targets` between iterations, the loop can skip or re-process members.

Required adjustment (small, scoped to this change):

- In `_ensure_off_loop`, take a snapshot at the start of each pass:
  `targets = list(self._targets)`. Iterate the snapshot. Members removed by a
  concurrent re-expand are simply skipped on the next pass; newly added
  members are picked up on the next pass.

`_reexpand_targets` itself does not need to wait for the ensure loop. The
subsequent `check_and_set_deadline()` enters its existing
`_turn_off_lock.locked()` early-return guard and skips deadline logic until
the turn-off phase finishes (`auto_off.py::check_and_set_deadline`).

### Modified `async_unload`

- Call every callable in `self._root_unsubs` and clear the list.
- Then proceed with existing sensor/target unload.

### Invalid root targets

`GroupConfig` validates each entry in `targets` and keeps invalid entries with
a warning (`GroupConfig._warn_on_non_entity_targets`). For re-expand:

- Roots failing `valid_entity_id(eid)` are skipped at subscription time: no
  `_root_unsubs` entry, no state-change subscription. They cannot become groups
  later; treating them as leaves matches current `expand_group_targets`
  behaviour (`hass.states.get(invalid_id)` returns None, leaf fallback).
- Invalid roots therefore never trigger re-expand and never appear in
  `_current_leaves`. They still appear in `dump_group` (raw config) and are
  surfaced to the user via the existing validator warning.

## Caveat: nested group changes

If a root contains an intermediate group (root -> nested_group -> leaves) and
the nested group's member list changes, the root's `attributes.entity_id` may
not update. In that case the re-expand event will not fire and the leaf set
will drift.

Mitigation: rely on a manual reload of the auto_off config entry, or restart
HA. Watching nested groups recursively is out of scope (option B from
brainstorming, rejected).

This is a known limitation. Dynamic changes are supported only for direct root
membership, not nested membership.

## Tests

Tests target observable behaviour, not private state. Per `testing.md` "behavior
over shape": we do not assert on `self._targets` contents, private flag fields,
or call counts of internal helpers. We exercise the public surface
(`async_track_state_change_event` callbacks fired against a fake HA, deadline
sensor state, `Target.turn_off` service calls) and assert on the resulting
deadline sensor attributes and service calls.

Unit tests in
`custom_components/auto_off/tests/test_target_reexpand.py`:

1. `test_leaf_added_then_on_starts_deadline` - root initially absent from
   state machine; root appears with members `[leaf_a]`; toggle `leaf_a` on
   with sensors off; assert deadline-sensor attribute `deadline_iso` becomes a
   non-null ISO timestamp within one event-loop tick. This validates: late
   root registration is picked up, expansion runs, leaf subscription is live,
   and the deadline state machine starts.
2. `test_leaf_removed_while_on_cancels_deadline` - root members `[leaf_a]`,
   sensors off, deadline running; fire root state-change with members `[]`;
   assert `deadline_iso` becomes null. Validates: removal of the only
   currently-on target cancels the deadline through the existing
   `not target_on` branch.
3. `test_root_self_state_change_does_not_disturb_deadline` - root members
   `[leaf_a, leaf_b]`, deadline running; fire root state-change `on -> off`
   with identical members; assert `deadline_iso` unchanged (same timestamp,
   not cancelled and not reset). Validates: self-state changes without
   membership change are ignored.
4. `test_root_initially_invalid_id_is_skipped` - config with target
   `not_an_entity_id`; assert no exception during init, no service calls when
   any other entity in the system changes, deadline sensor stays at default.
   Validates: invalid roots do not crash setup and do not interfere with the
   rest of the integration.
5. `test_unload_removes_root_subscription` - after `async_unload`, firing the
   root state-change event must not produce any service call or deadline
   change. Validates: subscription cleanup is complete.

E2E test in `test_integration_e2e.py`:

- Yaml package defines a `binary_sensor` for presence and a `light.group`
  helper with two member lights, plus an auto_off group whose target is the
  light group.
- Scenario: start HA with the root light group present; turn on one member
  light with the presence sensor off; wait for the configured delay; assert
  the member light is turned off by auto_off.
- This exercises the production-bug path end to end (root is a real HA light
  group, expansion finds members, individual member toggle drives the
  deadline). Runtime mutation of group membership is left to unit tests since
  HA yaml reload is harder to script reliably in `ha-test-kit`.

## Rollout

- No version bump; no `async_migrate_entry`. The semantic change is invisible
  to the config schema and to `GroupConfig` model.
- Existing groups whose targets are plain entities are unaffected:
  `expand_group_targets` returns `[entity_id]` unchanged, and the re-expand
  subscription on a non-group entity only fires on its own state changes,
  which the equal-membership check skips.
- Production cases where the root is a group helper are fixed on first
  startup after deploy: the root subscription catches the moment the helper
  registers, the re-expand populates the leaves, and individual member state
  changes drive the deadline.
