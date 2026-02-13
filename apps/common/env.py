"""Environment parsing helpers with strict validation."""

from __future__ import annotations

import os

from django.core.exceptions import ImproperlyConfigured


TRUE_VALUES = {"1", "true", "yes", "on"}
FALSE_VALUES = {"0", "false", "no", "off"}


def get_env(name: str, *, default: str | None = None, required: bool = False) -> str:
    value = os.getenv(name, default)
    if required and (value is None or value == ""):
        raise ImproperlyConfigured(f"Missing required environment variable: {name}")
    if value is None:
        raise ImproperlyConfigured(f"Environment variable is not set: {name}")
    return value


def get_bool(name: str, *, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default

    normalized = raw.strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    raise ImproperlyConfigured(
        f"Invalid boolean for {name}: {raw!r}. Use one of {sorted(TRUE_VALUES | FALSE_VALUES)}"
    )


def get_int(name: str, *, default: int | None = None) -> int:
    raw = os.getenv(name)
    if raw is None:
        if default is None:
            raise ImproperlyConfigured(f"Missing required integer environment variable: {name}")
        return default

    try:
        return int(raw)
    except ValueError as exc:
        raise ImproperlyConfigured(f"Invalid integer for {name}: {raw!r}") from exc


def get_list(name: str, *, default: list[str] | None = None) -> list[str]:
    raw = os.getenv(name)
    if raw is None:
        return default or []

    values = [item.strip() for item in raw.split(",")]
    return [item for item in values if item]
