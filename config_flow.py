"""Config flow for Nice Gate."""
from __future__ import annotations

import asyncio
import logging
import socket
from typing import Any

import voluptuous as vol

from homeassistant import config_entries, data_entry_flow
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import format_mac

from .const import CONF_MAC, CONF_SETUP_CODE, DEFAULT_NAME, DEFAULT_USERNAME, DOMAIN
from .nice_api import NiceGateApi, NiceGateApiAuthError, NiceGateApiConnectionError

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema({
    vol.Required(CONF_HOST): str,
    vol.Required(CONF_MAC): str,
    vol.Optional(CONF_USERNAME, default=DEFAULT_USERNAME): str,
})
STEP_PAIR_DATA_SCHEMA = vol.Schema({vol.Required(CONF_SETUP_CODE): str})


def _normalize_user_input(data: dict[str, Any]) -> dict[str, Any]:
    host = data[CONF_HOST].strip()
    username = data.get(CONF_USERNAME, DEFAULT_USERNAME).strip() or DEFAULT_USERNAME
    mac = format_mac(data[CONF_MAC]).upper()
    socket.gethostbyname(host)
    return {CONF_HOST: host, CONF_MAC: mac, CONF_USERNAME: username}


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1
    MINOR_VERSION = 5

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                self._data = await self.hass.async_add_executor_job(_normalize_user_input, user_input)
                try:
                    await self.async_set_unique_id(self._data[CONF_MAC])
                except data_entry_flow.AbortFlow as err:
                    if "already_in_progress" in str(err):
                        errors["base"] = "already_in_progress"
                        return self.async_show_form(step_id="user", data_schema=self.add_suggested_values_to_schema(STEP_USER_DATA_SCHEMA, user_input), errors=errors)
                    raise
                self._abort_if_unique_id_configured()
                return await self.async_step_pair()
            except ValueError:
                errors["base"] = "invalid_host"
            except OSError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error during Nice Gate user step")
                errors["base"] = "unknown"
        return self.async_show_form(step_id="user", data_schema=self.add_suggested_values_to_schema(STEP_USER_DATA_SCHEMA, user_input or self._data), errors=errors)

    async def async_step_pair(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            setup_code = user_input[CONF_SETUP_CODE].strip()
            api = NiceGateApi(self._data[CONF_HOST], self._data[CONF_MAC], self._data[CONF_USERNAME], self._data.get(CONF_PASSWORD, ""))
            try:
                password = self._data.get(CONF_PASSWORD)
                if not password:
                    password = await api.pair(setup_code)
                    if not password:
                        raise InvalidAuth
                    self._data[CONF_PASSWORD] = password
                verify_api = NiceGateApi(
                    self._data[CONF_HOST],
                    self._data[CONF_MAC],
                    self._data[CONF_USERNAME],
                    self._data[CONF_PASSWORD],
                )
                deadline = asyncio.get_running_loop().time() + 30
                while True:
                    try:
                        state = await verify_api.verify_connect()
                    except NiceGateApiConnectionError as err:
                        if asyncio.get_running_loop().time() < deadline:
                            _LOGGER.debug("Pairing reconnect retry for %s after transient error: %s", self._data.get(CONF_HOST), err)
                            await asyncio.sleep(2)
                            continue
                        _LOGGER.warning("Nice Gate connection failed during pairing for host %s: %s", self._data.get(CONF_HOST), err)
                        errors["base"] = "cannot_connect"
                        break
                    if state == "connect":
                        try:
                            info_deadline = asyncio.get_running_loop().time() + 20
                            while True:
                                info_api = NiceGateApi(
                                    self._data[CONF_HOST],
                                    self._data[CONF_MAC],
                                    self._data[CONF_USERNAME],
                                    self._data[CONF_PASSWORD],
                                )
                                try:
                                    info = await info_api.async_get_info_data()
                                    if info:
                                        break
                                except Exception:
                                    pass
                                if asyncio.get_running_loop().time() >= info_deadline:
                                    break
                                await asyncio.sleep(2)
                        except Exception:
                            pass
                        return self.async_create_entry(title=DEFAULT_NAME, data=self._data)
                    if state == "wait" and asyncio.get_running_loop().time() < deadline:
                        await asyncio.sleep(2)
                        continue
                    if state == "wait":
                        errors["base"] = "waiting_permission"
                    else:
                        errors["base"] = "cannot_connect"
                    break
            except NiceGateApiAuthError:
                errors["base"] = "invalid_auth"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error during Nice Gate pairing for host %s", self._data.get(CONF_HOST))
                errors["base"] = "unknown"
        return self.async_show_form(
            step_id="pair",
            data_schema=self.add_suggested_values_to_schema(STEP_PAIR_DATA_SCHEMA, user_input or {}),
            errors=errors,
            description_placeholders={"host": self._data.get(CONF_HOST, ""), "username": self._data.get(CONF_USERNAME, DEFAULT_USERNAME)},
        )


class InvalidAuth(HomeAssistantError):
    """Error to indicate invalid auth."""
