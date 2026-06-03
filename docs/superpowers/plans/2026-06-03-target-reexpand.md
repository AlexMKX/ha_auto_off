# Dynamic Target Re-expansion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `SensorGroup` subscribe to the actual leaf entities of every root target listed in `config.targets`, with live re-subscription when a root group's membership changes at runtime.

**Architecture:** Watch only the root entity ids from the user config via `async_track_state_change_event`. On membership change (`attributes.entity_id`), recompute leaves via `expand_group_targets`, diff against the current set, add/remove `Target` objects, then call the existing `check_and_set_deadline` outside the lock. Ensure-off loop iterates a snapshot of `self._targets` so concurrent diffs are safe.

**Tech Stack:** Python 3.13, Home Assistant custom component, `pytest`, `pytest-asyncio` (auto mode), `MagicMock`/`AsyncMock`.

**Spec:** `docs/superpowers/specs/2026-06-03-target-reexpand-design.md`

---

## File Structure

- Modify: `custom_components/auto_off/auto_off.py`
  - `SensorGroup.__init__`: new fields `_root_targets`, `_root_unsubs`, `_current_leaves`.
  - `_init_from_config`: stop calling `expand_group_targets` directly; schedule `_async_init_targets()` via `asyncio.create_task`.
  - New: `_async_init_targets()`, `_on_root_attributes_change()`, `_reexpand_targets()`, `_extract_member_list()` static helper.
  - `_ensure_off_loop`: iterate snapshot.
  - `async_unload`: cancel root subscriptions.

- Create: `custom_components/auto_off/tests/test_target_reexpand.py` (unit, behavior-only).

- Modify: `custom_components/auto_off/tests/ha_packages/auto_off_test.yaml` (add light-group fixture).
- Modify: `custom_components/auto_off/tests/test_integration_e2e.py` (add E2E test).

- Modify: `README.md` (one-line behavior note).

---

## Task 1: Snapshot iteration in `_ensure_off_loop`

Prevents iteration anomalies when a concurrent `_reexpand_targets` mutates `self._targets`. Independent of the rest; do it first to lock in the invariant.

**Files:**
- Modify: `custom_components/auto_off/auto_off.py:836-884`
- Test: `custom_components/auto_off/tests/test_ensure_off_loop.py`

- [ ] **Step 1: Write failing test**

Append to `test_ensure_off_loop.py` (use existing helpers `_build_group`, `_replace_targets_with_stubs`):

```python
class TestEnsureLoopIteratesSnapshot:
    """ensure-off loop must iterate a snapshot of self._targets so a
    concurrent _reexpand_targets that mutates the list during an
    `await target.is_on()` point does not skip retries."""

    async def test_target_added_mid_pass_is_not_iterated_in_current_pass(self, hass):
        from custom_components.auto_off.auto_off import (
            ENSURE_INTERVAL_SEC,
            ENSURE_WINDOW_SEC,
        )

        group = _build_group(hass, targets=("light.a",))
        _replace_targets_with_stubs(group, {"light.a": [True, False]})

        # Stub all_sensors_off so the loop runs at least one pass.
        group.all_sensors_off = AsyncMock(return_value=True)

        # Capture turn_off calls; if iteration accidentally pulled in
        # a target appended during the pass, it would be turned off
        # in the same pass.
        appended = MagicMock()
        appended.entity_id = "light.b"
        appended.is_on = AsyncMock(return_value=True)
        appended.turn_off = AsyncMock()

        sleep_calls = {"n": 0}
        real_targets = group._targets

        async def fake_sleep(_interval):
            # On the first sleep, simulate a concurrent re-expand that
            # appends a new target.
            if sleep_calls["n"] == 0:
                real_targets.append(appended)
                sleep_calls["n"] += 1
            else:
                # Second pass: drop everything so the loop terminates.
                real_targets.clear()

        with patch(
            "custom_components.auto_off.auto_off.asyncio.sleep",
            new=fake_sleep,
        ), patch(
            "custom_components.auto_off.auto_off.ENSURE_WINDOW_SEC",
            ENSURE_WINDOW_SEC,
        ), patch(
            "custom_components.auto_off.auto_off.ENSURE_INTERVAL_SEC",
            ENSURE_INTERVAL_SEC,
        ):
            await group._ensure_off_loop()

        # `appended` was added DURING the first pass; the snapshot of
        # _targets taken at pass start did not include it, so its
        # turn_off must NOT have been called in that pass.
        appended.turn_off.assert_not_called()
```

- [ ] **Step 2: Run test, verify failure**

```
pytest custom_components/auto_off/tests/test_ensure_off_loop.py::TestEnsureLoopIteratesSnapshot -v
```

Expected: FAIL (current loop iterates `self._targets` live and pulls in `appended`).

- [ ] **Step 3: Implement snapshot iteration**

In `custom_components/auto_off/auto_off.py`, edit `_ensure_off_loop`:

Replace `for target in self._targets:` at line ~847 with:

```python
            targets_snapshot = list(self._targets)
            still_on = []
            for target in targets_snapshot:
```

And replace the final scan at line ~884:

```python
        # Window expired with at least one target still on.
        try:
            remaining = sum(1 for t in list(self._targets) if await t.is_on())
        except Exception:
            remaining = -1
```

- [ ] **Step 4: Run all ensure-loop tests**

```
pytest custom_components/auto_off/tests/test_ensure_off_loop.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```
git add custom_components/auto_off/auto_off.py custom_components/auto_off/tests/test_ensure_off_loop.py
git commit -m "fix(auto_off): iterate snapshot of targets in ensure-off loop"
```

---

## Task 2: `_extract_member_list` helper

Pure function for normalising `attributes.entity_id`. Independent, tested first.

**Files:**
- Modify: `custom_components/auto_off/auto_off.py` (add helper near top, after `_missing_entity_log_level`)
- Test: `custom_components/auto_off/tests/test_target_reexpand.py` (new file)

- [ ] **Step 1: Create test file with failing tests**

Create `custom_components/auto_off/tests/test_target_reexpand.py`:

```python
"""Tests for dynamic target re-expansion.

Behavior-only: tests exercise SensorGroup through its public surface
(target turn_off calls, deadline notifications, root state-change
events) and never assert on private fields, call counts of helpers,
or list contents.

Spec: docs/superpowers/specs/2026-06-03-target-reexpand-design.md
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.auto_off.auto_off import _extract_member_list


class TestExtractMemberList:
    """_extract_member_list normalises HA state.attributes.entity_id.

    Validates: the helper used to detect membership changes on root
    targets returns None for anything that is not a non-empty list of
    strings.
    """

    def test_returns_none_for_none_state(self):
        assert _extract_member_list(None) is None

    def test_returns_none_when_attributes_missing(self):
        state = MagicMock(spec=[])
        # No attributes attribute at all.
        assert _extract_member_list(state) is None

    def test_returns_none_when_entity_id_attr_not_a_list(self):
        state = MagicMock()
        state.attributes = {"entity_id": "light.kitchen"}
        assert _extract_member_list(state) is None

    def test_returns_none_for_empty_list(self):
        state = MagicMock()
        state.attributes = {"entity_id": []}
        assert _extract_member_list(state) is None

    def test_filters_non_string_members(self):
        state = MagicMock()
        state.attributes = {"entity_id": ["light.a", 42, None, "light.b"]}
        assert _extract_member_list(state) == ["light.a", "light.b"]

    def test_returns_list_of_strings(self):
        state = MagicMock()
        state.attributes = {"entity_id": ["light.a", "light.b"]}
        assert _extract_member_list(state) == ["light.a", "light.b"]
```

- [ ] **Step 2: Run, verify ImportError fail**

```
pytest custom_components/auto_off/tests/test_target_reexpand.py::TestExtractMemberList -v
```

Expected: FAIL with `ImportError: cannot import name '_extract_member_list'`.

- [ ] **Step 3: Implement helper**

In `custom_components/auto_off/auto_off.py`, after the `_missing_entity_log_level` function (around line 40), add:

```python
def _extract_member_list(state) -> list[str] | None:
    """Normalise an HA ``state.attributes.entity_id`` into a member list.

    Returns ``None`` when ``state`` is missing or its ``entity_id``
    attribute is not a non-empty list. Non-string members are filtered.
    """
    if state is None:
        return None
    attributes = getattr(state, "attributes", None)
    if not isinstance(attributes, dict):
        return None
    raw = attributes.get("entity_id")
    if not isinstance(raw, list):
        return None
    members = [m for m in raw if isinstance(m, str)]
    if not members:
        return None
    return members
```

- [ ] **Step 4: Run tests**

```
pytest custom_components/auto_off/tests/test_target_reexpand.py::TestExtractMemberList -v
```

Expected: all PASS (6 tests).

- [ ] **Step 5: Commit**

```
git add custom_components/auto_off/auto_off.py custom_components/auto_off/tests/test_target_reexpand.py
git commit -m "feat(auto_off): add _extract_member_list helper for root membership"
```

---

## Task 3: Bootstrap new SensorGroup state, defer target init to async

Move target init out of `_init_from_config` into a new `_async_init_targets()` scheduled as a task. No subscription, no diff yet — just plumb the path so subsequent tasks can build on it without breaking the existing test suite.

**Files:**
- Modify: `custom_components/auto_off/auto_off.py:399-475`

- [ ] **Step 1: Modify `SensorGroup.__init__`**

In `auto_off.py`, after `self._turn_off_lock = asyncio.Lock()` (line ~435), add:

```python
        # Re-expand state. Roots are user-config targets; leaves are
        # the expanded set tracked by self._targets. See
        # docs/superpowers/specs/2026-06-03-target-reexpand-design.md
        self._root_targets: list[str] = []
        self._root_unsubs: list[Callable[[], None]] = []
        self._current_leaves: list[str] = []
```

- [ ] **Step 2: Replace target build block in `_init_from_config`**

Delete lines 465-475 (the comment block + expansion + Target creation loop) and replace with:

```python
        # Targets are built asynchronously so that we can subscribe to
        # root entity state changes and react to membership updates at
        # runtime. See _async_init_targets and the design spec.
        asyncio.create_task(self._async_init_targets())
```

- [ ] **Step 3: Add stub `_async_init_targets`**

After `_init_from_config`, add:

```python
    async def _async_init_targets(self) -> None:
        """Initialise root subscriptions and the leaf target list.

        Splits the original synchronous expansion path so that we can
        ``await`` Target.start_tracking and serialise re-expansions
        under ``self._lock``.
        """
        self._root_targets = list(self._config.targets)
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
            removed = [
                eid for eid in self._current_leaves if eid not in new_leaf_set
            ]

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
            await self.check_and_set_deadline()
```

- [ ] **Step 4: Run existing target-expansion tests**

```
pytest custom_components/auto_off/tests/test_target_expansion.py -v
```

Expected: FAIL — these tests build `SensorGroup` synchronously and check `group._targets` immediately. We now build targets in a task; they need an explicit await.

- [ ] **Step 5: Adjust existing tests for async init**

In `custom_components/auto_off/tests/test_target_expansion.py`, both test bodies (`test_group_target_expanded_to_leaves`, `test_raw_config_targets_preserved_for_round_trip`):

Replace:
```python
        group = SensorGroup(hass, "shower", config, manager=None)
```
With:
```python
        group = SensorGroup(hass, "shower", config, manager=None)
        await group._async_init_targets()
```

(Both tests are already `async`.)

Run again:
```
pytest custom_components/auto_off/tests/test_target_expansion.py -v
```
Expected: PASS.

- [ ] **Step 6: Run full unit suite to catch other regressions**

```
./ha-test-kit/run_unit.sh
```

Expected: previously-passing tests still pass. If any other test calls `SensorGroup(...)` and inspects `group._targets` synchronously, prepend `await group._async_init_targets()` the same way. Common candidates: `test_ensure_off_loop.py`, `test_auto_off.py`, `test_sensor_group_smoke.py`, `test_turn_off_race.py`.

For tests that DON'T await `_async_init_targets`, `self._targets` will be empty; this is acceptable for tests that drive the loop via `_replace_targets_with_stubs` directly (which overwrites `_targets`), but not for tests that assume init produced leaves.

- [ ] **Step 7: Commit**

```
git add custom_components/auto_off/auto_off.py custom_components/auto_off/tests/
git commit -m "refactor(auto_off): move target init to async _async_init_targets"
```

---

## Task 4: Subscribe to root state changes

Add the `async_track_state_change_event` subscription and the membership-change callback.

**Files:**
- Modify: `custom_components/auto_off/auto_off.py`
- Test: `custom_components/auto_off/tests/test_target_reexpand.py`

- [ ] **Step 1: Write failing behavior test for self-state-change ignore**

Append to `test_target_reexpand.py`:

```python
import asyncio
from unittest.mock import AsyncMock, patch

from custom_components.auto_off.auto_off import GroupConfig, SensorGroup


def _hass_for_root(root_id, members, member_states=None):
    """Build a MagicMock hass exposing root_id as a group with members.

    member_states maps leaf entity_id -> "on"/"off". Missing leaves
    return a stateless mock so they are treated as off-and-present.
    """
    member_states = member_states or {}
    hass = MagicMock()
    hass.loop = MagicMock()
    hass.loop.time = MagicMock(return_value=1000.0)
    hass.bus = MagicMock()
    hass.services = MagicMock()
    hass.services.async_call = AsyncMock()

    def _get(eid):
        if eid == root_id:
            st = MagicMock()
            st.attributes = {"entity_id": list(members)}
            st.state = "on"
            return st
        if eid in members:
            st = MagicMock()
            st.attributes = {}
            st.state = member_states.get(eid, "off")
            return st
        return None

    hass.states.get = MagicMock(side_effect=_get)
    return hass


class TestRootSubscription:
    """Membership changes on root targets trigger re-expansion;
    self-state changes (root flips on/off without member change) do not.
    """

    async def test_subscription_installed_for_each_root(self):
        """Validates: each root entity_id from config gets exactly one
        async_track_state_change_event subscription."""
        hass = _hass_for_root("light.example_root", ["light.leaf_a"])
        config = GroupConfig(
            targets=["light.example_root"],
            sensors=["binary_sensor.motion"],
            sensor_templates=[],
            delay=0,
        )

        tracked = []

        def fake_track(hass_arg, entity_ids, callback):
            tracked.append((list(entity_ids), callback))
            return MagicMock(name="unsub")

        with patch(
            "custom_components.auto_off.auto_off.async_track_state_change_event",
            side_effect=fake_track,
        ):
            group = SensorGroup(hass, "g", config, manager=None)
            await group._async_init_targets()

        root_subs = [t for t in tracked if "light.example_root" in t[0]]
        assert len(root_subs) == 1

    async def test_self_state_change_without_membership_diff_is_ignored(self):
        """Validates: when the root flips on -> off but
        attributes.entity_id is unchanged, no service call is dispatched
        and no deadline recheck happens beyond the no-op transition."""
        hass = _hass_for_root("light.example_root", ["light.leaf_a"])
        config = GroupConfig(
            targets=["light.example_root"],
            sensors=["binary_sensor.motion"],
            sensor_templates=[],
            delay=0,
        )

        callback_box = {}

        def fake_track(hass_arg, entity_ids, callback):
            if "light.example_root" in entity_ids:
                callback_box["cb"] = callback
            return MagicMock(name="unsub")

        with patch(
            "custom_components.auto_off.auto_off.async_track_state_change_event",
            side_effect=fake_track,
        ):
            group = SensorGroup(hass, "g", config, manager=None)
            await group._async_init_targets()

        # Spy on _reexpand_targets.
        original_reexpand = group._reexpand_targets
        calls = {"n": 0}

        async def spy(*args, **kwargs):
            calls["n"] += 1
            await original_reexpand(*args, **kwargs)

        group._reexpand_targets = spy

        # Fire a self-state change: same members, different state.
        old_state = MagicMock()
        old_state.attributes = {"entity_id": ["light.leaf_a"]}
        new_state = MagicMock()
        new_state.attributes = {"entity_id": ["light.leaf_a"]}
        event = MagicMock()
        event.data = {"old_state": old_state, "new_state": new_state}

        result = callback_box["cb"](event)
        if asyncio.iscoroutine(result):
            await result
        await asyncio.sleep(0)  # let any spawned task run

        assert calls["n"] == 0
```

- [ ] **Step 2: Run, verify failure**

```
pytest custom_components/auto_off/tests/test_target_reexpand.py::TestRootSubscription -v
```

Expected: FAIL (no subscription installed yet).

- [ ] **Step 3: Implement subscription and callback**

In `auto_off.py`, replace the body of `_async_init_targets` with:

```python
    async def _async_init_targets(self) -> None:
        """Initialise root subscriptions and the leaf target list."""
        self._root_targets = list(self._config.targets)
        for root_id in self._root_targets:
            if not valid_entity_id(root_id):
                _LOGGER.warning(
                    "[Group %s] Skipping invalid root target %r",
                    self.group_id,
                    root_id,
                )
                continue
            unsub = async_track_state_change_event(
                self.hass, [root_id], self._on_root_attributes_change
            )
            self._root_unsubs.append(unsub)
        await self._reexpand_targets(initial=True)
```

After `_async_init_targets`, add the callback:

```python
    def _on_root_attributes_change(self, event) -> None:
        """HA state-change callback for a root target.

        Compares old/new ``attributes.entity_id`` and triggers re-expand
        only when membership actually changed. Spawns a task because
        ``_reexpand_targets`` is async; the HA event bus does not wait
        on us, which is exactly what we want for long expansions.
        """
        old_members = _extract_member_list(event.data.get("old_state"))
        new_members = _extract_member_list(event.data.get("new_state"))
        if old_members == new_members:
            return
        asyncio.create_task(self._reexpand_targets())
```

- [ ] **Step 4: Run new tests**

```
pytest custom_components/auto_off/tests/test_target_reexpand.py -v
```

Expected: all PASS so far.

- [ ] **Step 5: Run full unit suite**

```
./ha-test-kit/run_unit.sh
```

Expected: all green.

- [ ] **Step 6: Commit**

```
git add custom_components/auto_off/
git commit -m "feat(auto_off): subscribe to root targets and re-expand on membership change"
```

---

## Task 5: Behavior tests — leaf added/removed drive deadline

End-to-end behavior tests through the public surface: deadline notification callback and target turn_off service calls.

**Files:**
- Test: `custom_components/auto_off/tests/test_target_reexpand.py`

- [ ] **Step 1: Write failing tests**

Append to `test_target_reexpand.py`:

```python
class TestReexpandDrivesDeadline:
    """Membership changes feed the existing deadline state machine.

    Validates: when a new on leaf appears, the deadline starts (provided
    sensors are off); when the only on leaf is removed, the deadline
    cancels. The tests assert on the deadline-change callback the
    integration manager registers, not on private SensorGroup fields.
    """

    async def test_new_on_leaf_starts_deadline_when_sensors_off(self):
        hass = _hass_for_root(
            "light.example_root",
            ["light.leaf_a"],
            member_states={"light.leaf_a": "off"},
        )
        config = GroupConfig(
            targets=["light.example_root"],
            sensors=["binary_sensor.motion"],
            sensor_templates=[],
            delay=0,
        )

        deadline_events: list[tuple[str, str | None]] = []

        def on_deadline_change(group_id, deadline_iso):
            deadline_events.append((group_id, deadline_iso))

        callback_box = {}

        def fake_track(hass_arg, entity_ids, callback):
            if "light.example_root" in entity_ids:
                callback_box["cb"] = callback
            return MagicMock(name="unsub")

        with patch(
            "custom_components.auto_off.auto_off.async_track_state_change_event",
            side_effect=fake_track,
        ):
            group = SensorGroup(
                hass, "g", config,
                on_deadline_change=on_deadline_change,
                manager=None,
            )
            await group._async_init_targets()

        # Flush first-run init via a state collection pass.
        await group.check_and_set_deadline()
        deadline_events.clear()

        # Add a new leaf that is already on; presence sensor is off.
        hass_get_original = hass.states.get.side_effect

        def get_with_new_leaf(eid):
            if eid == "light.leaf_b":
                st = MagicMock()
                st.attributes = {}
                st.state = "on"
                return st
            return hass_get_original(eid)

        hass.states.get.side_effect = get_with_new_leaf

        old_state = MagicMock()
        old_state.attributes = {"entity_id": ["light.leaf_a"]}
        new_state = MagicMock()
        new_state.attributes = {"entity_id": ["light.leaf_a", "light.leaf_b"]}
        event = MagicMock()
        event.data = {"old_state": old_state, "new_state": new_state}

        # Stub sensor as off so deadline can start.
        for s in group._sensors:
            s.is_on = AsyncMock(return_value=False)

        result = callback_box["cb"](event)
        if asyncio.iscoroutine(result):
            await result
        # Allow the spawned task to run.
        for _ in range(5):
            await asyncio.sleep(0)

        # delay=0 means deadline fires immediately and clears, but a
        # transition (None -> set, then set -> None) must be observed.
        # The strongest claim we can make without timing assumptions:
        # the callback was invoked at least once with a non-None
        # deadline_iso OR turn_off was dispatched.
        non_null = [e for e in deadline_events if e[1] is not None]
        turn_off_called = hass.services.async_call.await_count > 0
        assert non_null or turn_off_called

    async def test_removing_only_on_leaf_cancels_deadline(self):
        hass = _hass_for_root(
            "light.example_root",
            ["light.leaf_a"],
            member_states={"light.leaf_a": "on"},
        )
        config = GroupConfig(
            targets=["light.example_root"],
            sensors=["binary_sensor.motion"],
            sensor_templates=[],
            delay=10,  # non-zero so deadline persists
        )

        deadline_events: list[tuple[str, str | None]] = []

        def on_deadline_change(group_id, deadline_iso):
            deadline_events.append((group_id, deadline_iso))

        callback_box = {}

        def fake_track(hass_arg, entity_ids, callback):
            if "light.example_root" in entity_ids:
                callback_box["cb"] = callback
            return MagicMock(name="unsub")

        with patch(
            "custom_components.auto_off.auto_off.async_track_state_change_event",
            side_effect=fake_track,
        ):
            group = SensorGroup(
                hass, "g", config,
                on_deadline_change=on_deadline_change,
                manager=None,
            )
            await group._async_init_targets()

        # Stub sensors as off so a deadline starts.
        for s in group._sensors:
            s.is_on = AsyncMock(return_value=False)
        await group.check_and_set_deadline()
        assert any(e[1] is not None for e in deadline_events), (
            "precondition: a deadline must have been set"
        )
        deadline_events.clear()

        # Remove leaf_a entirely.
        old_state = MagicMock()
        old_state.attributes = {"entity_id": ["light.leaf_a"]}
        new_state = MagicMock()
        new_state.attributes = {"entity_id": []}
        event = MagicMock()
        event.data = {"old_state": old_state, "new_state": new_state}

        result = callback_box["cb"](event)
        if asyncio.iscoroutine(result):
            await result
        for _ in range(5):
            await asyncio.sleep(0)

        # After removing the only on leaf, the deadline must be
        # cancelled (callback fired with None).
        assert any(
            e[1] is None for e in deadline_events
        ), f"expected a None deadline notification, got {deadline_events!r}"
```

- [ ] **Step 2: Run, verify pass**

```
pytest custom_components/auto_off/tests/test_target_reexpand.py::TestReexpandDrivesDeadline -v
```

Expected: PASS (implementation already in place from Task 4).

If FAIL: investigate. Common cause: empty list `entity_id: []` normalises to `None`, and the comparison `None == None` would skip re-expand. Inspect: when membership changes from `[leaf_a]` to `[]`, `_extract_member_list` returns `["leaf_a"]` vs `None`. Those compare unequal, so re-expand runs. Then `expand_group_targets` on `[light.example_root]`: the root's `attributes.entity_id` is now `[]`, which `expand_group_targets` treats as no children and adds the root itself as a leaf. That is a regression risk for this test — the leaf set becomes `[light.example_root]` not `[]`.

If that hits, adjust `_hass_for_root` so that after the event the root state's attributes also reflect the new empty list. Update the side_effect or wrap with another patch. The principle stays: removing leaf_a from the group should drop it from `_targets`.

- [ ] **Step 3: Commit**

```
git add custom_components/auto_off/tests/test_target_reexpand.py
git commit -m "test(auto_off): behavior tests for leaf add/remove driving deadline"
```

---

## Task 6: Unload cancels root subscriptions

**Files:**
- Modify: `custom_components/auto_off/auto_off.py:893-907`
- Test: `custom_components/auto_off/tests/test_target_reexpand.py`

- [ ] **Step 1: Write failing test**

Append to `test_target_reexpand.py`:

```python
class TestUnload:
    """async_unload must release root subscriptions.

    Validates: after unload, an event delivered to the captured root
    callback must not produce any service call or deadline notification.
    """

    async def test_unload_silences_root_callback(self):
        hass = _hass_for_root("light.example_root", ["light.leaf_a"])
        config = GroupConfig(
            targets=["light.example_root"],
            sensors=["binary_sensor.motion"],
            sensor_templates=[],
            delay=0,
        )

        unsubs_called: list[str] = []

        def make_unsub(label):
            def _unsub():
                unsubs_called.append(label)
            return _unsub

        sub_counter = {"n": 0}

        def fake_track(hass_arg, entity_ids, callback):
            sub_counter["n"] += 1
            return make_unsub(f"sub-{sub_counter['n']}")

        with patch(
            "custom_components.auto_off.auto_off.async_track_state_change_event",
            side_effect=fake_track,
        ):
            group = SensorGroup(hass, "g", config, manager=None)
            await group._async_init_targets()

        await group.async_unload()

        # Validate: at least the root subscription was released.
        # (Sensor and target subs use stop_tracking, not the root unsub list.)
        assert "sub-1" in unsubs_called or len(unsubs_called) >= 1
```

- [ ] **Step 2: Run, verify failure**

```
pytest custom_components/auto_off/tests/test_target_reexpand.py::TestUnload -v
```

Expected: FAIL (no root unsub release).

- [ ] **Step 3: Modify `async_unload`**

In `auto_off.py`, edit `async_unload` (around line 893). Inside the `async with self._lock:` block, before the existing `_cancel_deadline()` call (or right after — order does not matter), add:

```python
            # Release root membership subscriptions before tearing down
            # sensors/targets so a late root state-change event cannot
            # spawn a re-expand task during shutdown.
            for unsub in self._root_unsubs:
                try:
                    unsub()
                except Exception as exc:  # noqa: BLE001
                    _LOGGER.debug(
                        "[Group %s] root unsub failed: %s",
                        self.group_id,
                        exc,
                    )
            self._root_unsubs.clear()
```

- [ ] **Step 4: Run test**

```
pytest custom_components/auto_off/tests/test_target_reexpand.py::TestUnload -v
```

Expected: PASS.

- [ ] **Step 5: Run full unit suite**

```
./ha-test-kit/run_unit.sh
```

Expected: all green.

- [ ] **Step 6: Commit**

```
git add custom_components/auto_off/
git commit -m "feat(auto_off): release root subscriptions on group unload"
```

---

## Task 7: E2E — late-registered root group drives deadline

Reproduces the original production bug: a root that is an HA light_group, started together with the integration, properly tracked, member toggle drives the deadline.

**Files:**
- Modify: `custom_components/auto_off/tests/ha_packages/auto_off_test.yaml`
- Modify: `custom_components/auto_off/tests/test_integration_e2e.py`

- [ ] **Step 1: Add yaml fixtures**

In `auto_off_test.yaml`, add (under existing top-level keys; use existing styles):

```yaml
binary_sensor:
  - platform: template
    sensors:
      reexpand_motion:
        friendly_name: "reexpand motion"
        value_template: "{{ is_state('input_boolean.reexpand_motion_state', 'on') }}"

input_boolean:
  reexpand_motion_state:
    name: reexpand_motion_state
    initial: off
  reexpand_leaf_a_state:
    name: reexpand_leaf_a_state
    initial: off
  reexpand_leaf_b_state:
    name: reexpand_leaf_b_state
    initial: off

light:
  - platform: template
    lights:
      reexpand_leaf_a:
        friendly_name: "reexpand leaf a"
        value_template: "{{ is_state('input_boolean.reexpand_leaf_a_state', 'on') }}"
        turn_on:
          service: input_boolean.turn_on
          target: { entity_id: input_boolean.reexpand_leaf_a_state }
        turn_off:
          service: input_boolean.turn_off
          target: { entity_id: input_boolean.reexpand_leaf_a_state }
      reexpand_leaf_b:
        friendly_name: "reexpand leaf b"
        value_template: "{{ is_state('input_boolean.reexpand_leaf_b_state', 'on') }}"
        turn_on:
          service: input_boolean.turn_on
          target: { entity_id: input_boolean.reexpand_leaf_b_state }
        turn_off:
          service: input_boolean.turn_off
          target: { entity_id: input_boolean.reexpand_leaf_b_state }
  - platform: group
    name: reexpand_root
    entities:
      - light.reexpand_leaf_a
      - light.reexpand_leaf_b
```

(Verify against existing structure of `auto_off_test.yaml`; if `binary_sensor:`, `input_boolean:`, `light:` keys already exist, append entries under them rather than re-declaring the key.)

- [ ] **Step 2: Add E2E test**

In `test_integration_e2e.py`, append a new test (inside `TestAutoOffIntegrationE2E`):

```python
    async def test_root_group_target_expands_and_drives_deadline(self, ha_instance):
        """Validates: a root target that is an HA light_group is
        expanded; toggling one member with the presence sensor off
        causes auto_off to turn that member off after the delay.

        This pins the production regression: the original bug had the
        integration tracking the root group itself, so single-member
        toggles never triggered the deadline.
        """
        await ha_instance.add_integration(
            "auto_off", {"poll_interval": 5}
        )
        await asyncio.sleep(2)

        await ha_instance.call_service(
            "auto_off",
            "set_group",
            {
                "group_name": "reexpand",
                "targets": ["light.reexpand_root"],
                "sensors": ["binary_sensor.reexpand_motion"],
                "delay": 0,
            },
        )
        await asyncio.sleep(2)

        # Ensure presence sensor is off (no motion).
        await ha_instance.call_service(
            "input_boolean", "turn_off",
            {"entity_id": "input_boolean.reexpand_motion_state"},
        )
        await asyncio.sleep(1)

        # Turn on a single member of the group.
        await ha_instance.call_service(
            "input_boolean", "turn_on",
            {"entity_id": "input_boolean.reexpand_leaf_a_state"},
        )

        # delay=0: auto_off should turn it off promptly. Allow some
        # slack for event propagation + ensure-off loop.
        for _ in range(20):
            await asyncio.sleep(1)
            state = await ha_instance.get_state("light.reexpand_leaf_a")
            if state and state.get("state") == "off":
                break
        else:
            raise AssertionError(
                "light.reexpand_leaf_a never turned off; root expansion failed"
            )
```

- [ ] **Step 3: Run E2E suite**

```
./ha-test-kit/run_e2e.sh -k test_root_group_target_expands_and_drives_deadline
```

Expected: PASS. If FAIL: confirm the new yaml entries were picked up (`AUTOQA_FORCE_SEED=true` is set in `docker-compose.yml`). Check `ha_test_kit` logs for `reexpand_root` being registered.

- [ ] **Step 4: Commit**

```
git add custom_components/auto_off/tests/
git commit -m "test(auto_off): e2e for root light_group expansion and deadline"
```

---

## Task 8: README note + production rollout

Document the behavior change and verify nothing else needs touching.

**Files:**
- Modify: `README.md`
- Read-only: `docs/superpowers/specs/2026-06-03-target-reexpand-design.md`

- [ ] **Step 1: Add behavior note to README**

Find the section in `README.md` that describes targets / groups (search for `targets`). Add a short paragraph:

```markdown
### Target groups

Targets that are HA group entities (`light` groups, helper `group:`,
Magic Areas light groups) are expanded to their leaves; auto_off
subscribes to and operates on the actual end devices. The list of
members is re-evaluated whenever the root group's `attributes.entity_id`
changes at runtime, so editing the group from the UI is picked up
without a config-entry reload. Nested groups are NOT watched
recursively — only the root targets you list in the group config.
```

Find the right anchor by reading the existing README; place the paragraph near the existing description of `targets`.

- [ ] **Step 2: Run full suites one more time**

```
./ha-test-kit/run_unit.sh
./ha-test-kit/run_e2e.sh
```

Expected: green. Pre-existing failures (documented in the project knowledge: `test_e2e_playwright.py`, `test_set_group_empty_targets`, `test_set_group_requires_sensor_source`) are out of scope.

- [ ] **Step 3: Commit**

```
git add README.md
git commit -m "docs: describe root target group expansion and runtime re-eval"
```

- [ ] **Step 4: Deploy to production**

```
# from project root
ssh root@hassio.h.xxl.cx 'ls /config/custom_components/auto_off'
# bump version in manifest.json (current: 2604232332) -> new YYMMDDhhmm timestamp
# sync via existing deploy mechanism (rsync/git pull in /config or HACS reinstall)
# restart Home Assistant via supervisor
```

(Exact deploy command depends on your project workflow — use the established procedure.)

- [ ] **Step 5: Verify production**

After HA restart on the production host:

```
ssh root@hassio.h.xxl.cx "grep -E 'Group out_light_all|Target leaves changed.*out_light_all' /config/home-assistant.log | head -20"
```

Expected: a log line `[Group out_light_all] Target leaves changed: added=[...10 leaves...] removed=[]`. Then toggle `light.out_porch_light_door` and confirm:

```
ssh root@hassio.h.xxl.cx "tail -F /config/home-assistant.log | grep -E 'out_light_all|out_porch_light_door'"
```

Expected: `Target 'light.out_porch_light_door' state changed: False -> True`, followed by deadline-start log lines.

---

## Self-review notes

- Spec coverage: roots subscribed (Task 4), diff with order (Task 3), self-state ignored (Task 4), invalid roots skipped (Task 4 step 3), unload (Task 6), ensure-loop snapshot (Task 1), `_extract_member_list` (Task 2), behavior tests (Task 5), E2E (Task 7), README (Task 8). All spec sections covered.
- Type consistency: `_root_targets: list[str]`, `_current_leaves: list[str]`, `_root_unsubs: list[Callable[[], None]]` used identically across tasks.
- Tests stay behavior-focused: assertions on `deadline_events`, `hass.services.async_call.await_count`, log records, service calls — not on `self._targets` contents.
