# Dynamic target re-expansion

## Problem

`SensorGroup._init_from_config` calls `expand_group_targets` once at integration
setup time. When a root target is a HA group helper (e.g. `light.out_all`) that
is not yet registered in `hass.states` at that moment (typical during HA
bootstrap), `expand_group_targets` falls back to treating the root as a leaf and
subscribes only to the group entity itself.

Two downstream failures:

1. The group entity may emit stale or no `state_changed` events for its own
   computed state (observed in production: `light.out_all` stuck at `off` while
   `light.out_porch_light_door` is `on`). The deadline never starts.
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
root's `attributes.entity_id` changes, recompute the leaf set via
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
| False | True (newly added leaf is on) | `target_turned_on` transition → deadline starts (if sensors off) |
| True | False (removed leaf was the only on one) | `not target_on` branch → deadline cancelled |
| True | True | deadline left as is |
| False | False | no-op |

No special-case code is added for the re-expand path; the state machine is
re-used as-is.

## Detailed design

### Changes to `SensorGroup`

New fields:

- `self._root_targets: list[str]` — copy of `config.targets`, untouched.
- `self._root_unsubs: list[Callable[[], None]]` — root state-change
  subscriptions.
- `self._current_leaves: set[str]` — cache of the most recently expanded leaf
  set.

Modified `_init_from_config`:

1. Build sensors as before.
2. Initialise `self._root_targets = list(self._config.targets)`.
3. Subscribe to each root via `async_track_state_change_event` with
   `_on_root_attributes_change`. Subscription is unconditional;
   `async_track_state_change_event` accepts entity_ids that are not yet in the
   state machine, so late-loaded helper groups are handled correctly.
4. Call `_reexpand_targets()` once for the initial expansion. This populates
   `self._targets` and `self._current_leaves`.

New method `_on_root_attributes_change(event)`:

- Read `old_state` and `new_state` from `event.data`.
- Compare `old_state.attributes.get("entity_id")` (or `None`) with
  `new_state.attributes.get("entity_id")` (or `None`). Lists are compared as
  sets — order is irrelevant.
- If equal: return (this is an ordinary self-state change of the root, e.g.
  on→off; we don't care).
- Otherwise: `await self._reexpand_targets()`.

New method `_reexpand_targets()`:

- Acquire `self._lock` for the diff phase, to serialise with
  `check_and_set_deadline`. Release before calling `check_and_set_deadline` at
  the tail.
- `new_leaves = set(expand_group_targets(self.hass, self._root_targets))`.
- `added = new_leaves - self._current_leaves`,
  `removed = self._current_leaves - new_leaves`.
- For each `entity_id` in `removed`: locate the matching `Target` in
  `self._targets`, `await target.stop_tracking()`, remove from list.
- For each `entity_id` in `added`: build a new `Target`, append to
  `self._targets`, `await target.start_tracking()` (do not fire-and-forget;
  the subscription must be live before the deadline recheck).
- `self._current_leaves = new_leaves`.
- Log diff at INFO when non-empty.
- After releasing the lock: `await self.check_and_set_deadline()`.

Modified `async_unload`:

- Cancel `self._root_unsubs` in addition to existing sensor/target unsubscribe.

### Interaction with `_turn_off_lock`

If a re-expand fires while `_turn_off_lock` is held (ensure-off loop running):

- The diff itself runs to completion (it only mutates `self._targets`).
- The subsequent `check_and_set_deadline` enters its existing
  `_turn_off_lock.locked()` early-return guard and skips deadline logic.
- The ensure-off loop iterates `for target in self._targets` each pass, so any
  newly added leaf is picked up on the next retry tick. Removed leaves disappear
  from the iteration set.

No new locking is required.

### Caveat: nested group changes

If a root contains an intermediate group (e.g. `light.out_all` →
`light.out_porch_light_all_zb` → leaves), and the intermediate group's member
list changes, the root's `attributes.entity_id` may not update. In that case
the re-expand event will not fire and the leaf set will drift.

Mitigation: rely on a manual reload of the auto_off config entry, or restart
HA. Watching nested groups recursively is out of scope (option B from
brainstorming Q1, rejected).

In practice the root level is the only level the user manipulates from the UI
for the production setup observed, so this is acceptable.

## Tests

Unit tests in `custom_components/auto_off/tests/test_auto_off.py` (or new
`test_target_reexpand.py`):

1. `test_reexpand_on_root_attribute_change` — start with root state
   `entity_id=[A, B]`, fire state-change with `entity_id=[A, B, C]`, assert
   `self._targets` entity_ids = `{A, B, C}` and `_last_known_good_state` for
   A and B is preserved.
2. `test_reexpand_removes_dropped_leaves` — `[A, B] → [B]`. Assert A's
   `stop_tracking` was called and A is no longer in `self._targets`.
3. `test_reexpand_triggers_deadline_recheck` — patch
   `check_and_set_deadline`; after a re-expand it must be awaited exactly once.
4. `test_root_initially_absent` — root not in `hass.states` at
   `_init_from_config`; subscription is still installed; once the root appears
   with `attributes.entity_id`, leaves materialise.
5. `test_self_state_change_ignored` — root flips on→off with identical
   `entity_id` attribute; `_reexpand_targets` is NOT called.
6. `test_unload_cancels_root_subscriptions` — `async_unload` clears
   `_root_unsubs` and each unsub callable was invoked.

E2E test in `test_integration_e2e.py` with a yaml fixture defining a
`light_group` and an auto_off group whose target is that group:

- Boot HA, mutate the group's members via service or yaml reload.
- Toggle a newly added member on; assert the deadline sensor's
  `deadline_iso` attribute becomes non-null within the delay window.

## Rollout

- No version bump; no `async_migrate_entry` (semantic change is invisible to
  config schema).
- Existing groups whose targets are plain entities (most production groups)
  are unaffected: `expand_group_targets` returns `[entity_id]` unchanged, the
  re-expand subscription fires only on real attribute changes (which won't
  happen for non-group entities).
- The `out_light_all` production case is fixed at first startup after deploy:
  the root subscription catches the moment `light.out_all` registers, the
  re-expand populates the 10 leaves, and individual member state changes drive
  the deadline.
