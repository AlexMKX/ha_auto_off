"""Auto-resetting binary sensor helper.

Subclasses call pulse() to turn the sensor on; it automatically resets
to off after a configured timeout. A new pulse() call cancels and
reschedules the reset, which gives a "sliding window" behavior.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_call_later


class AutoResetBinarySensor(BinarySensorEntity):
    """Binary sensor that pulses on and auto-resets off.

    This class owns only the on/off state and the reset timer. It is the
    subclass' responsibility to decide when to call pulse().
    """

    def __init__(self, hass: HomeAssistant, reset_timeout: float) -> None:
        self.hass = hass
        self._reset_timeout = float(reset_timeout)
        self._attr_is_on = False
        self._cancel_reset: Callable[[], None] | None = None

    @callback
    def pulse(self) -> None:
        """Turn on and (re)schedule the reset-to-off callback."""
        self._attr_is_on = True
        if self._cancel_reset is not None:
            self._cancel_reset()
        self._cancel_reset = async_call_later(self.hass, self._reset_timeout, self._on_reset)
        self.async_write_ha_state()

    @callback
    def _on_reset(self, _now: datetime | None) -> None:
        """Callback fired by async_call_later when the timeout elapses."""
        self._cancel_reset = None
        self._attr_is_on = False
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        """Cancel the pending reset callback, if any, on entity removal."""
        if self._cancel_reset is not None:
            self._cancel_reset()
            self._cancel_reset = None
