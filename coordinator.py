"""Coordinator for the Nice Gate integration."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DEFAULT_NAME, DEFAULT_UPDATE_INTERVAL_SECONDS
from .nice_api import NiceGateApi, NiceGateApiAuthError, NiceGateApiCommandRejectedError, NiceGateApiConnectionError, NiceGateApiError

_LOGGER = logging.getLogger(__name__)


class NiceGateCommandError(HomeAssistantError):
    """Raised when a gate command fails."""


class NiceCoordinator(DataUpdateCoordinator[dict[str, Any] | None]):
    def __init__(self, hass: HomeAssistant, api: NiceGateApi) -> None:
        super().__init__(hass, _LOGGER, name=DEFAULT_NAME, update_interval=timedelta(seconds=DEFAULT_UPDATE_INTERVAL_SECONDS))
        self.api = api

    def _merge_api_metadata(self, updated: dict[str, Any]) -> dict[str, Any]:
        if self.api.info_data:
            updated.update(self.api.info_data)
        updated["last_command_error"] = self.api.last_command_error
        updated["last_command_code"] = self.api.last_command_code
        updated["last_command_label"] = self.api.last_command_label
        updated["last_command_frame_type"] = self.api.last_command_frame_type
        updated["last_command_supported"] = self.api.last_command_supported
        updated["last_command_result"] = self.api.last_command_result
        for key in ("raw_status_xml", "status_property_names", "status_properties", "last_change_xml", "last_change_request_xml", "last_request_type", "last_request_xml"):
            value = getattr(self.api, key, None)
            if value is not None:
                updated[key] = value
        return updated

    async def _async_update_data(self) -> dict[str, Any] | None:
        try:
            async with asyncio.timeout(25):
                status = await self.api.async_get_status()
        except (NiceGateApiConnectionError, NiceGateApiAuthError, NiceGateApiError) as err:
            if self.data is not None:
                _LOGGER.warning("Nice Gate refresh failed, keeping previous state: %s", err)
                return dict(self.data)
            raise UpdateFailed(str(err)) from err
        merged = dict(status or {})
        if not self.api.raw_info_xml or not self.api.info_data.get("door_action_values"):
            try:
                async with asyncio.timeout(25):
                    info = await self.api.async_get_info_data()
                merged.update(info)
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("Nice Gate INFO probe failed: %s", err)
        else:
            merged.update(self.api.info_data)
        return self._merge_api_metadata(merged)

    async def async_initial_load(self) -> None:
        await self.async_config_entry_first_refresh()
        if not self.api.raw_info_xml or not self.api.info_data.get("door_action_values"):
            try:
                await self.async_refresh_info()
            except Exception:  # noqa: BLE001
                _LOGGER.debug("Initial INFO refresh failed", exc_info=True)

    async def async_refresh_info(self) -> None:
        info = await self.api.async_get_info_data()
        updated = dict(self.data or {})
        updated.update(info)
        self.async_set_updated_data(self._merge_api_metadata(updated))

    async def async_execute_command(self, command: str) -> None:
        try:
            status = await self.api.change(command)
        except NiceGateApiCommandRejectedError as err:
            raise NiceGateCommandError(f"Urządzenie Nice odrzuciło komendę (kod {err.code})") from err
        except (NiceGateApiConnectionError, NiceGateApiAuthError, NiceGateApiError) as err:
            raise NiceGateCommandError(str(err)) from err
        updated = dict(self.data or {})
        if status:
            updated.update(status)
        self.async_set_updated_data(self._merge_api_metadata(updated))

    async def async_execute_t4(self, code: str) -> None:
        try:
            status = await self.api.send_command(code, frame_type="T4Action")
        except NiceGateApiCommandRejectedError as err:
            raise NiceGateCommandError(f"Urządzenie Nice odrzuciło komendę {code} (kod {err.code})") from err
        except (NiceGateApiConnectionError, NiceGateApiAuthError, NiceGateApiError) as err:
            raise NiceGateCommandError(str(err)) from err
        updated = dict(self.data or {})
        if status:
            updated.update(status)
        self.async_set_updated_data(self._merge_api_metadata(updated))
