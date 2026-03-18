"""Dynamic extra T4 buttons for Nice Gate."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_MAC, DOMAIN, CONTROL_T4_CODES, extra_t4_codes, missing_extra_t4_codes, t4_label
from .coordinator import NiceCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    runtime = hass.data[DOMAIN][entry.entry_id]
    coordinator: NiceCoordinator = runtime["coordinator"]
    device_info = runtime["device_info"]
    mac = entry.data[CONF_MAC]

    created_supported: set[str] = set()
    created_potential: set[str] = set()

    async_add_entities([
        NiceGateMainControlT4Button(coordinator, mac, device_info, code) for code in CONTROL_T4_CODES
    ])

    async def _add_missing_buttons() -> None:
        allowed = (coordinator.data or {}).get("allowed_t4_codes")

        supported_codes = extra_t4_codes(allowed)
        new_supported = [code for code in supported_codes if code not in created_supported]
        if new_supported:
            created_supported.update(new_supported)
            async_add_entities([
                NiceGateAvailableT4Button(coordinator, mac, device_info, code) for code in new_supported
            ])

        potential_codes = missing_extra_t4_codes(allowed)
        new_potential = [code for code in potential_codes if code not in created_potential]
        if new_potential:
            created_potential.update(new_potential)
            async_add_entities([
                NiceGatePotentialT4Button(coordinator, mac, device_info, code) for code in new_potential
            ])

    await _add_missing_buttons()

    def _listener() -> None:
        hass.async_create_task(_add_missing_buttons())

    runtime["button_unsub"] = coordinator.async_add_listener(_listener)


class _BaseNiceGateT4Button(CoordinatorEntity[NiceCoordinator], ButtonEntity):
    _attr_has_entity_name = True
    _attr_entity_registry_enabled_default = True

    def __init__(self, coordinator: NiceCoordinator, mac: str, device_info: dict[str, object], code: str) -> None:
        super().__init__(coordinator)
        self._code = code
        self._attr_device_info = device_info
        self._attr_unique_id = f"{mac}_{self._unique_suffix}_{code.lower()}"
        self._attr_name = self._build_name(code)
        self._attr_icon = self._icon
        if getattr(self, "_entity_category", None) is not None:
            self._attr_entity_category = self._entity_category

    async def async_press(self) -> None:
        await self.coordinator.async_execute_t4(self._code)


class NiceGateMainControlT4Button(_BaseNiceGateT4Button):
    _unique_suffix = "t4_control"
    _icon = "mdi:gate-arrow-right"

    def _build_name(self, code: str) -> str:
        return t4_label(code)

    def __init__(self, coordinator: NiceCoordinator, mac: str, device_info: dict[str, object], code: str) -> None:
        if code == "MDBk":
            self._icon = "mdi:lock-open-check-outline"
        elif code == "MDFh":
            self._icon = "mdi:lock-check-outline"
        super().__init__(coordinator, mac, device_info, code)


class NiceGateAvailableT4Button(_BaseNiceGateT4Button):
    _unique_suffix = "t4_available"
    _icon = "mdi:gesture-tap-button"
    _entity_category = EntityCategory.CONFIG

    def _build_name(self, code: str) -> str:
        return t4_label(code)


class NiceGatePotentialT4Button(_BaseNiceGateT4Button):
    _unique_suffix = "t4_maybe"
    _icon = "mdi:gesture-tap-button-outline"
    _entity_category = EntityCategory.DIAGNOSTIC

    def _build_name(self, code: str) -> str:
        return f"{t4_label(code)} (może nie być wspierane)"
