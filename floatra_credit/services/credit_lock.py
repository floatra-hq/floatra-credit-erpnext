# -*- coding: utf-8 -*-
"""Credit-lock failsafe check.

Mirrors `connectors/odoo/floatra_credit/services/credit_lock.py`:
treats a stale cached `available` status (older than 15 minutes)
as locked. This guards against silent communication failures with
the Floatra gateway — if we haven't refreshed the lock state in a
while, we'd rather refuse a credit order than send one Floatra
considers blocked.

Caller flow:
  1. webhook lands → updates customer.floatra_credit_status +
     customer.floatra_last_status_check
  2. SalesOrder.before_submit → calls is_locked_failsafe(customer)
  3. on first stale read after a deploy, the controller force-syncs
     via FloatraAPIClient.get_lock_status, then retries the gate

Returns True when the customer should be blocked from initiating a
new Floatra-credit order.
"""

from datetime import datetime, timedelta
from typing import Optional

CACHE_TTL_MINUTES = 15


def is_locked_failsafe(customer) -> bool:
    """`customer` is the Frappe Customer doc (object-like, has
    .floatra_credit_status and .floatra_last_status_check)."""

    status = getattr(customer, "floatra_credit_status", None) or "not_enrolled"

    # Explicit lock states are authoritative — no TTL needed.
    if status in ("locked", "suspended"):
        return True
    if status in ("not_enrolled", "pending_kyc"):
        # Not eligible; treat as locked from the order-flow point
        # of view so before_submit refuses.
        return True

    # available — apply TTL.
    last_check = getattr(customer, "floatra_last_status_check", None)
    last_dt = _coerce_datetime(last_check)
    if last_dt is None:
        # No last check yet (newly enrolled, never synced). Failsafe.
        return True

    age = datetime.utcnow() - last_dt
    if age > timedelta(minutes=CACHE_TTL_MINUTES):
        return True

    return False


def _coerce_datetime(v) -> Optional[datetime]:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v
    try:
        return datetime.fromisoformat(str(v))
    except ValueError:
        return None
