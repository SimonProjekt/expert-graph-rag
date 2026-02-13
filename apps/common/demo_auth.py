from __future__ import annotations

from django.http import HttpRequest

from apps.documents.models import SecurityLevel

SESSION_ROLE_KEY = "demo_user_role"
SESSION_NAME_KEY = "demo_user_name"


def normalize_role(raw_value: str | None, *, default: str = SecurityLevel.PUBLIC) -> str:
    value = (raw_value or default).strip().upper()
    if value in SecurityLevel.values:
        return value
    return default


def get_session_role(request: HttpRequest) -> str:
    raw = request.session.get(SESSION_ROLE_KEY)
    if isinstance(raw, str):
        return normalize_role(raw)
    return SecurityLevel.PUBLIC


def get_session_name(request: HttpRequest) -> str | None:
    raw = request.session.get(SESSION_NAME_KEY)
    if not isinstance(raw, str):
        return None
    value = raw.strip()
    return value or None


def set_session_identity(request: HttpRequest, *, role: str, name: str | None) -> None:
    request.session[SESSION_ROLE_KEY] = normalize_role(role)
    if name and name.strip():
        request.session[SESSION_NAME_KEY] = name.strip()[:128]
    else:
        request.session.pop(SESSION_NAME_KEY, None)


def clear_session_identity(request: HttpRequest) -> None:
    request.session.pop(SESSION_ROLE_KEY, None)
    request.session.pop(SESSION_NAME_KEY, None)


def resolve_clearance(*, requested_clearance: str | None, session_role: str) -> str:
    if requested_clearance:
        return normalize_role(requested_clearance)
    return normalize_role(session_role)
