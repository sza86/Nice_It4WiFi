"""The Nice Gate integration."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import CONF_MAC, DEFAULT_NAME, DOMAIN, MANUFACTURER, MODEL, PLATFORMS
from .coordinator import NiceCoordinator
from .nice_api import NiceGateApi, NiceGateApiConnectionError, NiceGateApiError


def _build_device_info(mac: str) -> dict[str, object]:
    return {
        "identifiers": {(DOMAIN, mac)},
        "manufacturer": MANUFACTURER,
        "model": MODEL,
        "name": DEFAULT_NAME,
    }


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})
    api = NiceGateApi(entry.data[CONF_HOST], entry.data[CONF_MAC], entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD])
    coordinator = NiceCoordinator(hass, api)
    try:
        await coordinator.async_initial_load()
    except (NiceGateApiConnectionError, NiceGateApiError) as err:
        raise ConfigEntryNotReady(str(err)) from err
    runtime = {
        "api": api,
        "coordinator": coordinator,
        "device_info": _build_device_info(entry.data[CONF_MAC]),
    }
    hass.data[DOMAIN][entry.entry_id] = runtime
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        runtime = hass.data[DOMAIN].pop(entry.entry_id)
        unsub = runtime.pop("button_unsub", None)
        if unsub:
            unsub()
        await runtime["api"].disconnect()
    return unload_ok
