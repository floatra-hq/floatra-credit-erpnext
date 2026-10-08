# -*- coding: utf-8 -*-
"""Input validation for the Floatra confirm-delivery desk action.

Frappe-free so it's
unit-testable, raises a typed `DeliveryValidationError(field, msg)`
so the caller can surface the offending field in the desk UI.

Validation rules:
  - delivered_at must parse as a datetime (ISO-8601 or Frappe's
    'YYYY-MM-DD HH:MM:SS' desk format)
  - delivered_at must not be in the future (clock-skew tolerant up
    to 5 minutes) — we don't want agents back-stamping deliveries
    they're about to do
  - delivered_at must not be older than 30 days (catches paste
    errors where the agent grabs an old timestamp)
  - notes optional, ≤500 chars (matches the gateway DTO)
"""

from datetime import datetime, timedelta
from typing import Optional


CLOCK_SKEW_TOLERANCE = timedelta(minutes=5)
MAX_BACKDATE = timedelta(days=30)


class DeliveryValidationError(Exception):
    """Raised when the wizard inputs can't be coerced into a valid
    Floatra `confirm_delivery` payload.

    Carries the offending `field` name so the desk caller can
    attach the error to the right form input."""

    def __init__(self, field: str, message: str):
        super().__init__(message)
        self.field = field


def validate_delivery_inputs(
    delivered_at: object,
    notes: Optional[object] = None,
    now: Optional[datetime] = None,
) -> dict:
    current = now or datetime.utcnow()
    parsed = _coerce_datetime(delivered_at)
    if parsed is None:
        raise DeliveryValidationError(
            "delivered_at",
            "Delivered at is required (ISO-8601 or 'YYYY-MM-DD HH:MM:SS').",
        )
    if parsed > current + CLOCK_SKEW_TOLERANCE:
        raise DeliveryValidationError(
            "delivered_at",
            "Delivered at cannot be in the future.",
        )
    if current - parsed > MAX_BACKDATE:
        raise DeliveryValidationError(
            "delivered_at",
            "Delivered at is too far in the past (max 30 days).",
        )

    notes_str: Optional[str] = None
    if notes is not None:
        s = str(notes).strip()
        if s:
            if len(s) > 500:
                raise DeliveryValidationError(
                    "notes",
                    "Notes is too long (max 500 chars).",
                )
            notes_str = s

    # Canonicalise to ISO with +00:00 so the gateway sees a stable shape.
    iso = parsed.isoformat()
    if parsed.tzinfo is None:
        iso += "+00:00"

    payload: "dict[str, object]" = {"delivered_at": iso}
    if notes_str is not None:
        payload["notes"] = notes_str
    return payload


def _coerce_datetime(v: Optional[object]) -> Optional[datetime]:
    """Parse the input into a naive UTC datetime.

    Frappe's desk uses naive datetimes (always interpreted as the
    site's timezone — for Floatra we treat them as UTC). ISO inputs
    can be tz-aware; we shift them to naive UTC so the comparison
    against `datetime.utcnow()` is well-defined.
    """
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        if v.tzinfo is not None:
            # Shift to naive UTC.
            return (v - v.utcoffset()).replace(tzinfo=None)
        return v
    s = str(v).strip()
    try:
        parsed = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            return (parsed - parsed.utcoffset()).replace(tzinfo=None)
        return parsed
    except ValueError:
        pass
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
