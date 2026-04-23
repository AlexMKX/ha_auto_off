"""Unit tests for AutoResetBinarySensor.

Covers the contract: pulse() turns on, a scheduled reset turns off,
a new pulse() restarts the reset timer, and removal cancels the timer.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from custom_components.door_occupancy.auto_reset import AutoResetBinarySensor


class _Probe(AutoResetBinarySensor):
    """Concrete subclass used by the tests."""

    def __init__(self, hass, reset_timeout: float) -> None:
        super().__init__(hass, reset_timeout)
        self.state_writes = 0

    def async_write_ha_state(self) -> None:
        # Replace HA write with a counter so we can assert it was called.
        self.state_writes += 1


class TestAutoResetBinarySensor:
    """Behavior contract for AutoResetBinarySensor."""

    def test_pulse_turns_on_and_schedules_reset(self, hass):
        """pulse() must flip is_on to True and schedule a reset."""
        cancel = MagicMock()
        with patch(
            "custom_components.door_occupancy.auto_reset.async_call_later",
            return_value=cancel,
        ) as mock_call_later:
            sensor = _Probe(hass, reset_timeout=15)

            sensor.pulse()

            assert sensor.is_on is True
            mock_call_later.assert_called_once()
            # First positional arg is hass, second is the delay in seconds.
            assert mock_call_later.call_args[0][0] is hass
            assert mock_call_later.call_args[0][1] == 15
            assert sensor.state_writes == 1

    def test_second_pulse_cancels_and_reschedules(self, hass):
        """A second pulse() must cancel the prior reset and schedule a new one."""
        cancel_first = MagicMock()
        cancel_second = MagicMock()
        with patch(
            "custom_components.door_occupancy.auto_reset.async_call_later",
            side_effect=[cancel_first, cancel_second],
        ):
            sensor = _Probe(hass, reset_timeout=15)

            sensor.pulse()
            sensor.pulse()

            cancel_first.assert_called_once()
            cancel_second.assert_not_called()
            assert sensor.is_on is True

    def test_reset_callback_turns_off(self, hass):
        """The callback passed to async_call_later must turn is_on back to False."""
        cancel = MagicMock()
        captured = {}

        def fake_call_later(_hass, _delay, cb):
            captured["cb"] = cb
            return cancel

        with patch(
            "custom_components.door_occupancy.auto_reset.async_call_later",
            side_effect=fake_call_later,
        ):
            sensor = _Probe(hass, reset_timeout=15)
            sensor.pulse()

            assert "cb" in captured
            captured["cb"](None)  # simulate the scheduled reset firing

            assert sensor.is_on is False
            # One write for pulse(), one for the reset.
            assert sensor.state_writes == 2

    async def test_remove_cancels_pending_reset(self, hass):
        """async_will_remove_from_hass must cancel the pending reset callback."""
        cancel = MagicMock()
        with patch(
            "custom_components.door_occupancy.auto_reset.async_call_later",
            return_value=cancel,
        ):
            sensor = _Probe(hass, reset_timeout=15)
            sensor.pulse()

            await sensor.async_will_remove_from_hass()

            cancel.assert_called_once()
