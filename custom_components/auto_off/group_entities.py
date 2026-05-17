"""HA-stdlib group entity subclasses bound to the auto_off device.

These entities exist purely for UI and convenience turn-off.  They do
NOT participate in the auto-off state machine: the deadline logic in
SensorGroup continues to subscribe to individual member entities.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.group.binary_sensor import BinarySensorGroup
from homeassistant.components.group.cover import CoverGroup
from homeassistant.components.group.fan import FanGroup
from homeassistant.components.group.light import LightGroup
from homeassistant.components.group.lock import LockGroup
from homeassistant.components.group.media_player import MediaPlayerGroup
from homeassistant.components.group.switch import SwitchGroup
from homeassistant.components.group.valve import ValveGroup
from homeassistant.core import Event, EventStateChangedData, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.event import async_track_state_change_event

from .const import DOMAIN, VERSION

_LOGGER = logging.getLogger(__name__)


def _install_member_listener(entity) -> Callable[[], None] | None:
    """Install our own state-change listener for ``entity._entity_ids``.

    The parent ``GroupEntity.async_added_to_hass`` already installed a
    listener, but it captured the entity-id list as it was at that
    moment. Subsequent ``update_members`` calls swap ``_entity_ids`` on
    the instance without touching the subscription, so changes to the
    new members would be missed.

    We register an additional listener mirroring the parent callback
    (recompute group state, refresh UI). It is owned by us so we can
    cancel and reinstall on every ``update_members`` call. Returns the
    unsubscribe callable, or ``None`` if the entity is not attached to
    a hass instance yet (no-op until ``async_added_to_hass``).
    """
    if entity.hass is None:
        return None

    @callback
    def _on_member_state_change(event: Event[EventStateChangedData]) -> None:
        entity.async_update_group_state()
        if entity.hass is not None:
            entity.async_write_ha_state()

    return async_track_state_change_event(
        entity.hass, list(entity._entity_ids), _on_member_state_change
    )

# Map HA domain -> HA stdlib *Group class.  Keep in sync with GROUPABLE_DOMAINS.
_TARGET_GROUP_CLASSES: dict[str, type] = {
    "light": LightGroup,
    "switch": SwitchGroup,
    "fan": FanGroup,
    "cover": CoverGroup,
    "media_player": MediaPlayerGroup,
    "lock": LockGroup,
    "valve": ValveGroup,
}


def _device_info(group_name: str) -> DeviceInfo:
    """DeviceInfo shared by every auto_off entity for a given group."""
    return DeviceInfo(
        identifiers={(DOMAIN, group_name)},
        name=f"Auto Off: {group_name}",
        manufacturer="Auto Off",
        model="Sensor Group",
        sw_version=VERSION,
    )


class AutoOffSensorsGroup(BinarySensorGroup):
    """binary_sensor group aggregating GroupConfig.sensors.

    Attaches DeviceInfo for the auto_off group, exposes sensor_templates
    as an extra_state_attribute, and uses OR semantics (any on → on).
    """

    _attr_has_entity_name = True
    _attr_translation_key = "sensors"
    _attr_should_poll = False

    def __init__(
        self,
        *,
        group_name: str,
        entity_ids: list[str],
        sensor_templates: list[str],
    ) -> None:
        unique_id = f"{DOMAIN}_{group_name}_sensors"
        super().__init__(
            unique_id=unique_id,
            name=None,  # translation_key drives the rendered name
            device_class=BinarySensorDeviceClass.OCCUPANCY,
            entity_ids=list(entity_ids),
            mode=False,  # any-on = on
        )
        self._group_name = group_name
        self._sensor_templates = list(sensor_templates)
        self._attr_device_info = _device_info(group_name)
        self._auto_off_member_unsub: Callable[[], None] | None = None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        base = dict(self._attr_extra_state_attributes or {})
        base["sensor_templates"] = list(self._sensor_templates)
        return base

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._auto_off_member_unsub = _install_member_listener(self)

    async def async_will_remove_from_hass(self) -> None:
        if self._auto_off_member_unsub is not None:
            self._auto_off_member_unsub()
            self._auto_off_member_unsub = None
        await super().async_will_remove_from_hass()

    def update_members(
        self, entity_ids: list[str], sensor_templates: list[str]
    ) -> None:
        """Update members and templates after a set_group call.

        Rebuilds the state-change subscription for the new member list
        so the group entity actually reacts to its post-update members.
        The standard ``GroupEntity`` listener subscribed once during
        ``async_added_to_hass`` and is never refreshed, so without this
        rewiring the entity would keep tracking only the original ids.

        Caller must invoke ``async_write_ha_state`` afterwards.
        """
        self._entity_ids = list(entity_ids)
        self._attr_extra_state_attributes = {"entity_id": list(entity_ids)}
        self._sensor_templates = list(sensor_templates)

        if self._auto_off_member_unsub is not None:
            self._auto_off_member_unsub()
            self._auto_off_member_unsub = None
        self._auto_off_member_unsub = _install_member_listener(self)


def _make_targets_group_class(domain: str, base: type) -> type:
    """Build a per-domain subclass of a HA *Group class.

    The subclass attaches DeviceInfo, has_entity_name, translation_key,
    and exposes an update_members() hook.  We build classes dynamically
    to avoid seven near-identical hand-written copies.
    """

    class _AutoOffTargetsGroup(base):  # type: ignore[valid-type,misc]
        _attr_has_entity_name = True
        _attr_translation_key = f"targets_{domain}"
        _attr_should_poll = False

        def __init__(self, *, group_name: str, entity_ids: list[str]) -> None:
            unique_id = f"{DOMAIN}_{group_name}_targets_{domain}"
            super().__init__(
                unique_id=unique_id,
                name=None,
                entity_ids=list(entity_ids),
                mode=False,  # any-on = on
            )
            self._group_name = group_name
            self._attr_device_info = _device_info(group_name)
            self._auto_off_member_unsub: Callable[[], None] | None = None

        async def async_added_to_hass(self) -> None:
            await super().async_added_to_hass()
            self._auto_off_member_unsub = _install_member_listener(self)

        async def async_will_remove_from_hass(self) -> None:
            if self._auto_off_member_unsub is not None:
                self._auto_off_member_unsub()
                self._auto_off_member_unsub = None
            await super().async_will_remove_from_hass()

        def update_members(self, entity_ids: list[str]) -> None:
            """Replace the tracked member list.

            Rebuilds the state-change subscription so the entity actually
            reacts to its post-update members. See
            ``AutoOffSensorsGroup.update_members`` for the rationale.
            Caller must invoke ``async_write_ha_state`` afterwards.
            """
            self._entity_ids = list(entity_ids)
            self._attr_extra_state_attributes = {"entity_id": list(entity_ids)}

            if self._auto_off_member_unsub is not None:
                self._auto_off_member_unsub()
                self._auto_off_member_unsub = None
            self._auto_off_member_unsub = _install_member_listener(self)

    _AutoOffTargetsGroup.__name__ = f"AutoOffTargets{domain.title().replace('_', '')}Group"
    _AutoOffTargetsGroup.__qualname__ = _AutoOffTargetsGroup.__name__
    return _AutoOffTargetsGroup


# Pre-build one class per groupable domain.
TARGET_GROUP_ENTITY_CLASSES: dict[str, type] = {
    domain: _make_targets_group_class(domain, base)
    for domain, base in _TARGET_GROUP_CLASSES.items()
}


def split_targets_by_domain(targets: list[str]) -> dict[str, list[str]]:
    """Bucket targets by domain, skipping non-groupable domains.

    Targets whose domain is not in TARGET_GROUP_ENTITY_CLASSES are
    omitted from the returned dict (their turn-off is handled by the
    per-target fallback in SensorGroup.turn_off).  Invalid entity ids
    (no domain prefix) are also omitted.
    """
    buckets: dict[str, list[str]] = {}
    for entity_id in targets:
        if "." not in entity_id:
            continue
        domain = entity_id.split(".", 1)[0]
        if domain not in TARGET_GROUP_ENTITY_CLASSES:
            continue
        buckets.setdefault(domain, []).append(entity_id)
    return buckets


def targets_group_entity_id(domain: str, group_name: str) -> str:
    """Return the stable entity_id of the targets-group for (domain, group)."""
    return f"{domain}.auto_off_{group_name}_targets_{domain}"


def sensors_group_entity_id(group_name: str) -> str:
    """Return the stable entity_id of the sensors-group for a group."""
    return f"binary_sensor.auto_off_{group_name}_sensors"
