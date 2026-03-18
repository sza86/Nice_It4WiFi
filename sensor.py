"""Sensor platform for Nice Gate."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_MAC, DOMAIN
from .coordinator import NiceCoordinator

_STATE_LABELS = {
    "open": "otwarta",
    "opening": "otwieranie",
    "closed": "zamknięta",
    "closing": "zamykanie",
    "stopped": "zatrzymana",
}
_STATE_ICONS = {
    "open": "mdi:gate-open",
    "opening": "mdi:gate-arrow-right",
    "closed": "mdi:gate",
    "closing": "mdi:gate-arrow-left",
    "stopped": "mdi:pause-circle-outline",
}


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    runtime = hass.data[DOMAIN][entry.entry_id]
    coordinator = runtime["coordinator"]
    device_info = runtime["device_info"]
    async_add_entities([
        NiceGateStatusSensor(coordinator, entry.data[CONF_MAC], device_info),
    ])


class NiceGateStatusSensor(CoordinatorEntity[NiceCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "gate_status"
    _attr_name = "Stan bramy"

    def __init__(self, coordinator: NiceCoordinator, unique_part: str, device_info: dict[str, object]) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{unique_part}_gate_status"
        self._attr_device_info = device_info

    @property
    def native_value(self) -> str:
        raw = (self.coordinator.data or {}).get("door_status")
        if not raw:
            return "nieznany"
        return _STATE_LABELS.get(raw, str(raw))

    @property
    def icon(self) -> str:
        raw = (self.coordinator.data or {}).get("door_status")
        return _STATE_ICONS.get(raw, "mdi:gate-alert")

    @property
    def extra_state_attributes(self) -> dict[str, object | None]:
        data = self.coordinator.data or {}
        return {
            "raw_status": data.get("door_status"),
            "obstruct": data.get("obstruct"),
            "target": data.get("target"),
            "device_id": data.get("device_id"),
            "interface_date": data.get("interface_date"),
            "device_last_event": data.get("device_last_event"),
            "interface_last_event": data.get("interface_last_event"),
            "door_action_values": data.get("door_action_values"),
            "t4_action_values": data.get("t4_action_values"),
            "allowed_t4_hex": data.get("allowed_t4_hex"),
            "allowed_t4_codes": data.get("allowed_t4_codes"),
            "allowed_t4_labels": data.get("allowed_t4_labels"),
            "last_command_error": data.get("last_command_error"),
            "last_command_code": data.get("last_command_code"),
            "last_command_label": data.get("last_command_label"),
            "last_command_frame_type": data.get("last_command_frame_type"),
            "last_command_supported": data.get("last_command_supported"),
            "last_command_result": data.get("last_command_result"),
            "raw_info_xml": data.get("raw_info_xml"),
            "raw_status_xml": data.get("raw_status_xml"),
            "last_change_xml": data.get("last_change_xml"),
            "last_change_request_xml": data.get("last_change_request_xml"),
            "info_service_names": data.get("info_service_names"),
            "info_services": data.get("info_services"),
            "info_property_names": data.get("info_property_names"),
            "info_properties": data.get("info_properties"),
            "info_event_names": data.get("info_event_names"),
            "info_events": data.get("info_events"),
            "status_property_names": data.get("status_property_names"),
            "status_properties": data.get("status_properties"),
            "last_request_type": data.get("last_request_type"),
            "last_request_xml": data.get("last_request_xml"),
        }
