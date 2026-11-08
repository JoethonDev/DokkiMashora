"""Language-neutral storage and rendering for historical import errors."""

from __future__ import annotations

from typing import Any

from django.utils.translation import gettext as _

from .constants import PROGRAM_APP_STATUS_CHOICES


def make_import_error(msgid: str, **params: Any) -> dict[str, Any]:
    """Build one serializable error record for an import row."""
    return {"msgid": msgid, "params": params}


def render_import_error(error: dict[str, Any] | str) -> str:
    """Render one stored error in the active language."""
    if isinstance(error, str):
        return error
    msgid = str(error.get("msgid") or "")
    if not msgid:
        return ""
    params = dict(error.get("params") or {})
    status_code = params.pop("status_code", None)
    if status_code is not None:
        params["status"] = str(dict(PROGRAM_APP_STATUS_CHOICES).get(status_code, status_code))
    message = _(msgid)
    return message % params if params else message


def render_import_errors(errors: list[dict[str, Any] | str] | None) -> str:
    """Render all stored errors for one row."""
    return " ".join(
        rendered for rendered in (render_import_error(error) for error in (errors or [])) if rendered
    )
