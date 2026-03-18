"""API client for the Nice gate Wi-Fi interface."""
from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import logging
import random
import re
import socket
import ssl
from typing import Any, Final

import defusedxml.ElementTree as ET

from .const import decode_allowed_t4, t4_label

_LOGGER = logging.getLogger(__name__)

_PORT: Final = 443
_SOCKET_CONNECT_TIMEOUT: Final = 8
_SOCKET_HANDSHAKE_TIMEOUT: Final = 12
_SOCKET_READ_TIMEOUT: Final = 8
_FRAME_END: Final = b"\x03"
_FRAME_START: Final = b"\x02"


class NiceGateApiError(Exception):
    """Base API error."""


class NiceGateApiAuthError(NiceGateApiError):
    """Raised when authentication fails."""


class NiceGateApiConnectionError(NiceGateApiError):
    """Raised when the device cannot be reached or the session fails."""


class NiceGateApiCommandRejectedError(NiceGateApiError):
    """Raised when the bridge explicitly rejects a command."""

    def __init__(self, code: int) -> None:
        super().__init__(f"Device rejected command with error code {code}")
        self.code = code


class NiceGateApi:
    def __init__(self, host: str, mac: str, username: str, password: str) -> None:
        self.host = host.strip()
        self.target = mac.upper()
        self.device_id = "1"
        self.username = username.strip()
        self.password = password
        self.source = f"python_{self.username}"
        self.description = "Home Assistant integration"
        self.client_challenge = f"{random.randint(1, 9_999_999):08x}".upper()
        self.server_challenge = ""
        self.command_sequence = 1
        self.command_id = 0
        self.session_id: int | str = 1
        self.gate_status: dict[str, Any] | None = None
        self.info_data: dict[str, Any] = {}
        self.raw_info_xml: str | None = None
        self.raw_status_xml: str | None = None
        self.last_change_xml: str | None = None
        self.status_property_names: list[str] = []
        self.status_properties: list[dict[str, Any]] = []
        self.last_request_type: str | None = None
        self.last_request_xml: str | None = None
        self.last_command_error: int | None = None
        self.last_command_code: str | None = None
        self.last_command_label: str | None = None
        self.last_command_frame_type: str | None = None
        self.last_command_supported: bool | None = None
        self.last_command_result: str | None = None
        self.last_change_request_xml: str | None = None
        self._preferred_ssl_label: str | None = None
        self._command_lock = asyncio.Lock()

    async def pair(self, setup_code: str) -> str | None:
        return await asyncio.to_thread(self._pair_sync, setup_code)

    async def verify_connect(self) -> str:
        return await asyncio.to_thread(self._verify_connect_sync)

    async def async_get_status(self) -> dict[str, Any] | None:
        response = await self._send_authenticated_request("STATUS", "", expect_reply=True)
        self.raw_status_xml = response
        status = self._extract_status_data(response)
        if status is None:
            raise NiceGateApiConnectionError("Device did not return gate status")
        self.gate_status = status
        return status

    async def async_get_info_data(self) -> dict[str, Any]:
        response = await self._send_authenticated_request("INFO", "", expect_reply=True)
        self.raw_info_xml = response
        info = self._extract_info_data(response)
        if not info:
            raise NiceGateApiConnectionError("Device did not return INFO data")
        self.info_data = info
        return info

    async def change(self, command: str) -> dict[str, Any] | None:
        if command in {"open", "close", "stop"} and self.info_data.get("door_action_values"):
            return await self.send_command(command, frame_type="DoorAction")
        mapping = {"open": "MDAz", "close": "MDA0", "stop": "MDAy"}
        return await self.send_command(mapping.get(command, command), frame_type="T4Action")

    async def send_command(self, command: str, *, frame_type: str = "DoorAction") -> dict[str, Any] | None:
        field_name = "DoorAction" if frame_type == "DoorAction" else "T4Action"
        body = (
            f'<Devices><Device id="{self.device_id}"><Services>'
            f"<{field_name}>{command}</{field_name}>"
            "</Services></Device></Devices>"
        )

        self.last_command_code = command
        self.last_command_label = t4_label(command) if frame_type == "T4Action" else command
        self.last_command_frame_type = frame_type
        self.last_command_supported = True if frame_type != "T4Action" else command in (self.info_data.get("allowed_t4_codes") or [])
        self.last_command_result = "sending"

        request_xml = self._build_request_preview("CHANGE", body)
        self.last_change_request_xml = request_xml

        _LOGGER.debug(
            "Sending Nice command: frame=%s field=%s code=%s label=%s supported=%s",
            frame_type,
            field_name,
            command,
            self.last_command_label,
            self.last_command_supported,
        )

        response = await self._send_authenticated_request("CHANGE", body, expect_reply=False)
        self.last_change_xml = response or None
        error_code = self._extract_error_code(response)
        if error_code is not None:
            self.last_command_error = error_code
            self.last_command_result = f"rejected:{error_code}"
            raise NiceGateApiCommandRejectedError(error_code)
        self.last_command_error = None
        response_status = self._extract_status_data(response) if response else None
        if response_status is not None:
            self.gate_status = response_status
            self.last_command_result = "accepted_with_status"
            return response_status
        optimistic = self._optimistic_status_for_command(command)
        if optimistic is not None:
            self.gate_status = optimistic
            self.last_command_result = "accepted_optimistic"
        else:
            self.last_command_result = "accepted_no_status"
        return self.gate_status

    async def disconnect(self) -> None:
        self.command_id = 0
        self.command_sequence = 1
        self.session_id = 1
        self.server_challenge = ""
        self.gate_status = None
        self.last_command_error = None
        self.last_command_code = None
        self.last_command_label = None
        self.last_command_frame_type = None
        self.last_command_supported = None
        self.last_command_result = None
        self.last_change_request_xml = None

    async def _send_authenticated_request(self, command_type: str, body: str, *, expect_reply: bool) -> str:
        async with self._command_lock:
            return await asyncio.to_thread(self._send_authenticated_request_sync, command_type, body, expect_reply)

    def _pair_sync(self, setup_code: str) -> str | None:
        if not self.username:
            return None
        sock = None
        try:
            sock = self._open_tls_socket_sync()
            message = self._build_message(
                "PAIR",
                (
                    f'<Authentication username="{self.username}" '
                    f'cc="{self.client_challenge}" '
                    f'check="{self._get_setup_code_check(setup_code)}" '
                    'CType="phone" OSType="Android" OSVer="6.0.1" '
                    f'desc="{self.description}" />'
                ),
            )
            self._send_message_sync(sock, message)
            response = self._read_message_sync(sock)
            auth = self._extract_authentication(response)
            password = auth.get("pwd")
            if not password:
                raise NiceGateApiAuthError("Pairing did not return a password")
            self.password = password
            return password
        except NiceGateApiError:
            raise
        except Exception as err:
            raise NiceGateApiConnectionError(f"Unable to pair with device at {self.host}") from err
        finally:
            self._close_socket_sync(sock)

    def _verify_connect_sync(self) -> str:
        if not self.username:
            raise NiceGateApiAuthError("Missing username")
        sock = None
        try:
            sock = self._open_tls_socket_sync()
            permission = self._verify_on_socket_sync(sock)
            if permission == "wait":
                return "wait"
            self._connect_on_socket_sync(sock)
            return "connect"
        except NiceGateApiError:
            raise
        except Exception as err:
            raise NiceGateApiConnectionError(f"Unable to verify device connection for {self.host}") from err
        finally:
            self._close_socket_sync(sock)

    def _send_authenticated_request_sync(self, command_type: str, body: str, expect_reply: bool) -> str:
        sock = None
        try:
            sock = self._open_tls_socket_sync()
            permission = self._verify_on_socket_sync(sock)
            if permission == "wait":
                raise NiceGateApiAuthError("User still waiting for approval / permissions")
            self._connect_on_socket_sync(sock)
            message = self._build_message(command_type, body)
            if command_type in {"INFO", "STATUS", "CHANGE"}:
                self.last_request_type = command_type
                self.last_request_xml = message[1:-1].decode(errors="ignore")
            self._send_message_sync(sock, message)
            if expect_reply:
                return self._read_messages_until_useful_sync(sock)
            optional = self._try_read_optional_message_sync(sock)
            return optional or ""
        except NiceGateApiError:
            raise
        except Exception as err:
            raise NiceGateApiConnectionError(f"Communication with device {self.host} failed") from err
        finally:
            self._close_socket_sync(sock)

    def _verify_on_socket_sync(self, sock: ssl.SSLSocket) -> str:
        self._send_message_sync(sock, self._build_message("VERIFY", f'<User username="{self.username}"/>'))
        verify = self._read_message_sync(sock)
        auth = self._extract_authentication(verify)
        permission = auth.get("perm")
        if not permission:
            raise NiceGateApiAuthError("Device did not return authentication data")
        return permission

    def _connect_on_socket_sync(self, sock: ssl.SSLSocket) -> None:
        self._send_message_sync(sock, self._build_message("CONNECT", f'<Authentication username="{self.username}" cc="{self.client_challenge}"/>'))
        connect = self._read_message_sync(sock)
        self._find_server_challenge(connect)

    def _read_messages_until_useful_sync(self, sock: ssl.SSLSocket) -> str:
        while True:
            message = self._read_message_sync(sock)
            if self._extract_status_data(message) is not None:
                return message
            if "<Response" in message or "<Event" in message:
                return message

    def _try_read_optional_message_sync(self, sock: ssl.SSLSocket) -> str:
        old_timeout = sock.gettimeout()
        try:
            sock.settimeout(1.5)
            return self._read_message_sync(sock)
        except Exception:
            return ""
        finally:
            sock.settimeout(old_timeout)

    def _open_tls_socket_sync(self) -> ssl.SSLSocket:
        last_error: Exception | None = None
        for label, context in self._iter_ssl_contexts():
            raw_sock: socket.socket | None = None
            ssl_sock: ssl.SSLSocket | None = None
            try:
                _LOGGER.debug("Opening TLS connection to %s with profile %s", self.host, label)
                raw_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                raw_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                raw_sock.settimeout(_SOCKET_CONNECT_TIMEOUT)
                raw_sock.connect((self.host, _PORT))
                raw_sock.settimeout(_SOCKET_HANDSHAKE_TIMEOUT)
                ssl_sock = context.wrap_socket(raw_sock, do_handshake_on_connect=False)
                ssl_sock.settimeout(_SOCKET_HANDSHAKE_TIMEOUT)
                ssl_sock.do_handshake()
                ssl_sock.settimeout(_SOCKET_READ_TIMEOUT)
                self._preferred_ssl_label = label
                return ssl_sock
            except Exception as err:
                last_error = err
                _LOGGER.warning("TLS connection attempt '%s' to %s failed: %s", label, self.host, err)
                try:
                    if ssl_sock is not None:
                        ssl_sock.close()
                except Exception:
                    pass
                try:
                    if raw_sock is not None:
                        raw_sock.close()
                except Exception:
                    pass
        raise NiceGateApiConnectionError(f"TLS connection to {self.host} failed") from last_error

    def _iter_ssl_contexts(self) -> list[tuple[str, ssl.SSLContext]]:
        contexts = [
            ("legacy-tls12", self._build_ssl_context(maximum_version=ssl.TLSVersion.TLSv1_2)),
            ("legacy-default", self._build_ssl_context()),
        ]
        try:
            tls_v1 = ssl.TLSVersion.TLSv1
            contexts.append(("legacy-tlsv1-only", self._build_ssl_context(minimum_version=tls_v1, maximum_version=tls_v1)))
        except Exception:
            pass
        if self._preferred_ssl_label:
            contexts.sort(key=lambda item: 0 if item[0] == self._preferred_ssl_label else 1)
        return contexts

    def _send_message_sync(self, sock: ssl.SSLSocket, msg: bytes) -> None:
        sock.sendall(msg)

    def _read_message_sync(self, sock: ssl.SSLSocket) -> str:
        chunks = bytearray()
        started = False
        while True:
            data = sock.recv(4096)
            if not data:
                raise NiceGateApiConnectionError("Device closed the connection")
            for b in data:
                if not started:
                    if b == _FRAME_START[0]:
                        started = True
                    continue
                if b == _FRAME_END[0]:
                    answer = chunks.decode(errors="ignore")
                    self._find_session_id(answer)
                    return answer
                chunks.append(b)

    def _close_socket_sync(self, sock: ssl.SSLSocket | None) -> None:
        if sock is None:
            return
        try:
            sock.close()
        except Exception:
            pass

    def _extract_authentication(self, message: str) -> dict[str, str]:
        try:
            xml = ET.fromstring(message)
        except ET.ParseError as err:
            raise NiceGateApiError("Invalid XML received from device") from err
        auth = xml.find(".//Authentication")
        if auth is None and xml.tag == "Authentication":
            auth = xml
        if auth is None:
            raise NiceGateApiAuthError("Authentication element missing in device response")
        return {key: value for key, value in auth.attrib.items()}

    def _find_session_id(self, message: str) -> None:
        match = re.search(r'Authentication\sid=[\'\"]?([^\'\" >]+)', message)
        if match:
            self.session_id = match.group(1)

    def _find_server_challenge(self, message: str) -> None:
        match = re.search(r'sc=[\'\"]?([^\'\" >]+)', message)
        if not match:
            raise NiceGateApiAuthError("Server challenge missing in connect response")
        self.server_challenge = match.group(1)

    def _collect_nodes(self, root: ET.Element, xpath: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for node in root.findall(xpath):
            items.append({"tag": node.tag, "text": (node.text or "").strip() or None, "attrib": dict(node.attrib)})
        return items

    def _extract_status_data(self, message: str) -> dict[str, Any] | None:
        try:
            root = ET.fromstring(message)
        except ET.ParseError:
            return None
        door_status = root.findtext("./Devices/Device/Properties/DoorStatus") or root.findtext(".//DoorStatus")
        if not door_status:
            return None
        device = root.find("./Devices/Device") or root.find(".//Devices/Device")
        if device is not None and device.attrib.get("id"):
            self.device_id = device.attrib["id"]
        source = root.attrib.get("source")
        if source:
            self.target = source
        obstruct_text = root.findtext("./Devices/Device/Properties/Obstruct") or root.findtext(".//Obstruct")
        device_last_event = root.findtext("./Devices/Device/Events/LastEvent") or root.findtext(".//Devices/Device/Events/LastEvent")
        interface_last_event = root.findtext("./Interface/Events/LastEvent") or root.findtext(".//Interface/Events/LastEvent")
        interface_date = root.findtext("./Interface/Date") or root.findtext(".//Date")
        obstruct = None
        if obstruct_text not in (None, ""):
            try:
                obstruct = int(obstruct_text)
            except ValueError:
                obstruct = None
        self.status_properties = self._collect_nodes(root, ".//Properties/*")
        self.status_property_names = [item["tag"] for item in self.status_properties]
        status: dict[str, Any] = {
            "door_status": door_status,
            "obstruct": obstruct,
            "device_last_event": device_last_event,
            "interface_last_event": interface_last_event,
            "interface_date": interface_date,
            "target": self.target,
            "device_id": self.device_id,
        }
        status.update(self.info_data)
        return status

    def _extract_info_data(self, message: str) -> dict[str, Any]:
        try:
            root = ET.fromstring(message)
        except ET.ParseError:
            return {}
        info: dict[str, Any] = {}
        services = self._collect_nodes(root, ".//Services/*")
        properties = self._collect_nodes(root, ".//Properties/*")
        events = self._collect_nodes(root, ".//Events/*")
        source = root.attrib.get("source")
        if source:
            self.target = source
        device = root.find("./Devices/Device") or root.find(".//Devices/Device")
        if device is not None and device.attrib.get("id"):
            self.device_id = device.attrib["id"]
        info["target"] = self.target
        info["device_id"] = self.device_id
        info["raw_info_xml"] = message
        info["info_service_names"] = [item["tag"] for item in services]
        info["info_services"] = services
        info["info_property_names"] = [item["tag"] for item in properties]
        info["info_properties"] = properties
        info["info_event_names"] = [item["tag"] for item in events]
        info["info_events"] = events
        door_action = root.find(".//Services/DoorAction")
        t4_action = root.find(".//Services/T4Action")
        t4_allowed = root.find(".//Properties/T4_allowed")
        if door_action is not None:
            info["door_action_values"] = door_action.attrib.get("values")
        if t4_action is not None:
            info["t4_action_values"] = t4_action.attrib.get("values")
        if t4_allowed is not None:
            mask = t4_allowed.attrib.get("values") or (t4_allowed.text or "").strip()
            info["allowed_t4_hex"] = mask
            allowed_codes = decode_allowed_t4(mask)
            info["allowed_t4_codes"] = allowed_codes
            info["allowed_t4_labels"] = [t4_label(code) for code in allowed_codes]
        return info

    def _extract_error_code(self, message: str) -> int | None:
        if not message:
            return None
        try:
            root = ET.fromstring(message)
        except ET.ParseError:
            return None
        text = root.findtext(".//Error/Code")
        if not text:
            return None
        try:
            return int(text)
        except ValueError:
            return None

    def _build_request_preview(self, command_type: str, body: str) -> str:
        command_id = self._generate_command_id(self.session_id)
        start_request = (
            '<Request id="{}" source="{}" target="{}" gw="gwID" '
            'protocolType="NHK" protocolVersion="1.0" type="{}">\r\n'
        ).format(command_id, self.source, self.target, command_type)
        sign = self._build_signature(start_request + body) if self._is_sign_needed(command_type) else ""
        return start_request + body + sign + "</Request>\r\n"

    def _build_message(self, command_type: str, body: str) -> bytes:
        self.command_id = self._generate_command_id(self.session_id)
        start_request = (
            '<Request id="{}" source="{}" target="{}" gw="gwID" '
            'protocolType="NHK" protocolVersion="1.0" type="{}">\r\n'
        ).format(self.command_id, self.source, self.target, command_type)
        end_request = "</Request>\r\n"
        payload = start_request + body + (self._build_signature(start_request + body) if self._is_sign_needed(command_type) else "") + end_request
        return _FRAME_START + payload.encode() + _FRAME_END

    def _build_ssl_context(self, *, minimum_version: ssl.TLSVersion | None = None, maximum_version: ssl.TLSVersion | None = None) -> ssl.SSLContext:
        protocol = getattr(ssl, "PROTOCOL_TLS", ssl.PROTOCOL_TLS_CLIENT)
        ctx = ssl.SSLContext(protocol)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        if hasattr(ssl, "OP_LEGACY_SERVER_CONNECT"):
            ctx.options |= ssl.OP_LEGACY_SERVER_CONNECT
        try:
            ctx.set_ciphers("ALL:@SECLEVEL=0")
        except ssl.SSLError:
            pass
        if minimum_version is not None:
            ctx.minimum_version = minimum_version
        if maximum_version is not None:
            ctx.maximum_version = maximum_version
        return ctx

    def _hex_to_bytearray(self, hex_str: str) -> bytes:
        return bytes.fromhex(hex_str)

    def _sha256(self, *parts: bytes) -> bytes:
        digest = hashlib.sha256()
        for part in parts:
            digest.update(part)
        return digest.digest()

    def _invert_array(self, data: bytes) -> bytes:
        return data[::-1]

    def _build_signature(self, xml_command: str) -> str:
        client_challenge = self._hex_to_bytearray(self.client_challenge)
        server_challenge = self._hex_to_bytearray(self.server_challenge)
        pairing_password = base64.b64decode(self.password)
        session_password = self._sha256(pairing_password, self._invert_array(server_challenge), self._invert_array(client_challenge))
        msg_hash = self._sha256(xml_command.encode())
        sign = self._sha256(msg_hash, session_password)
        return f"<Sign>{base64.b64encode(sign).decode('utf-8')}</Sign>"

    def _is_sign_needed(self, command_type: str) -> bool:
        return command_type not in {"PAIR", "CONNECT", "VERIFY", "IDENTIFY"}

    def _generate_command_id(self, session_id: int | str) -> int:
        low = self.command_sequence
        self.command_sequence += 1
        return (low << 8) | (int(session_id) & 0xFF)

    def _get_setup_code_check(self, setup_code: str) -> str:
        client_challenge = self._hex_to_bytearray(self.client_challenge)
        payload = setup_code.encode("utf-8") + client_challenge[::-1] + b"Nice4U"
        crc32 = (~binascii.crc32(payload)) & 0xFFFFFFFF
        return f"{crc32:08X}"

    def _optimistic_status_for_command(self, command: str) -> dict[str, Any] | None:
        optimistic = dict(self.gate_status or {"door_status": None, "obstruct": None, "device_last_event": None, "interface_last_event": None, "interface_date": None})
        if command in {"open", "MDBk", "MDAz", "MDE5", "MDE5"}:
            optimistic["door_status"] = "opening"
            return optimistic
        if command in {"close", "MDFh", "MDA0", "MDBl"}:
            optimistic["door_status"] = "closing"
            return optimistic
        if command in {"stop", "MDAy"}:
            optimistic["door_status"] = "stopped"
            return optimistic
        return None
