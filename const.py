"""Constants for the Nice Gate integration."""
from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "nicegate"
PLATFORMS: list[Platform] = [
    Platform.COVER,
    Platform.SENSOR,
    Platform.BUTTON,
]

DEFAULT_NAME = "Nice Gate"
DEFAULT_USERNAME = "HomeAssistant"
DEFAULT_UPDATE_INTERVAL_SECONDS = 30
MANUFACTURER = "Nice"
MODEL = "IT4WIFI"

CONF_MAC = "mac"
CONF_SETUP_CODE = "setup_code"

BASE_T4_CODES: tuple[str, ...] = ("MDAx", "MDAy", "MDAz", "MDA0")
CONTROL_T4_CODES: tuple[str, ...] = ("MDBk", "MDFh")

T4_COMMAND_LABELS: dict[str, str] = {
    "MDAx": "Krok-po-kroku",
    "MDAy": "Stop",
    "MDAz": "Otwórz",
    "MDA0": "Zamknij",
    "MDA1": "Otwórz częściowo 1",
    "MDA2": "Otwórz częściowo 2",
    "MDA3": "Otwórz częściowo 3",
    "MDBi": "Krok-po-kroku wspólnotowy",
    "MDBj": "Krok-po-kroku wysoki priorytet",
    "MDBk": "Otwórz i zablokuj",
    "MDBl": "Zamknij i zablokuj",
    "MDBm": "Zablokuj",
    "MDEw": "Odblokuj",
    "MDEx": "Światło czasowe",
    "MDEy": "Światło włącz/wyłącz",
    "MDEz": "Krok-po-kroku skrzydło master",
    "MDE0": "Otwórz skrzydło master",
    "MDE1": "Zamknij skrzydło master",
    "MDE2": "Krok-po-kroku skrzydło slave",
    "MDE3": "Otwórz skrzydło slave",
    "MDE4": "Zamknij skrzydło slave",
    "MDE5": "Odblokuj i otwórz",
    "MDFh": "Odblokuj i zamknij",
}

T4_ALLOWED_BIT_ORDER: tuple[str | None, ...] = (
    None,
    "MDAx",
    "MDAy",
    "MDAz",
    "MDA0",
    "MDA1",
    "MDA2",
    "MDA3",
    "MDBi",
    "MDBj",
    "MDBk",
    "MDBl",
    "MDBm",
    "MDEw",
    "MDEx",
    "MDEy",
    "MDEz",
    "MDE0",
    "MDE1",
    "MDE2",
    "MDE3",
    "MDE4",
    "MDE5",
    "MDFh",
)


def t4_label(code: str) -> str:
    return T4_COMMAND_LABELS.get(code, code)


def decode_allowed_t4(mask_hex: str | None) -> list[str]:
    if not mask_hex:
        return []
    try:
        mask = int(mask_hex, 16)
    except ValueError:
        return []
    allowed: list[str] = []
    for bit_index, code in enumerate(T4_ALLOWED_BIT_ORDER):
        if code is None:
            continue
        if mask & (1 << bit_index):
            allowed.append(code)
    return allowed


def known_t4_codes() -> list[str]:
    return [code for code in T4_ALLOWED_BIT_ORDER if code is not None]


def extra_t4_codes(allowed: list[str] | None) -> list[str]:
    excluded = set(BASE_T4_CODES) | set(CONTROL_T4_CODES)
    return [code for code in (allowed or []) if code not in excluded]


def missing_t4_codes(allowed: list[str] | None) -> list[str]:
    allowed_set = set(allowed or [])
    return [code for code in known_t4_codes() if code not in allowed_set]


def missing_extra_t4_codes(allowed: list[str] | None) -> list[str]:
    excluded = set(BASE_T4_CODES) | set(CONTROL_T4_CODES)
    return [code for code in missing_t4_codes(allowed) if code not in excluded]
