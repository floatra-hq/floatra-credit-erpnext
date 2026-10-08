# -*- coding: utf-8 -*-
"""ERPNext side of the Floatra client: settings → client, docs → request bodies.

The HTTP client (``floatra_client.FloatraClient``) and the wire contract
(``floatra_contract``) are shared with the Odoo addon; this module only knows
ERPNext documents. ``frappe`` is imported lazily, so the document mappers are
unit-tested with plain stand-in objects.

Identifiers sent to Floatra:

* ``external_merchant_id`` = the Customer ``name`` (set at onboarding, sent
  again on every order);
* ``external_order_id`` = the Sales Order ``name``;
* lifecycle calls (confirm-delivery, cancel, repayments, return) use the
  Floatra LOAN id (``Sales Order.floatra_loan_id``), not the Order id.
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from . import floatra_contract as contract
from .floatra_client import FloatraClient
from .floatra_contract import ContractError, FloatraAPIError  # noqa: F401

ERP_TYPE = "ERPNEXT"

__all__ = [
    "ContractError",
    "FloatraAPIClient",
    "confirm_delivery_body_for_sales_order",
    "FloatraAPIError",
    "frappe_values",
    "initiate_body_for_sales_order",
    "onboard_body_for_customer",
    "system_to_utc",
]


class FloatraAPIClient(FloatraClient):
    """``FloatraClient`` configured from the Floatra Platform Config single."""

    @classmethod
    def for_site(cls) -> "FloatraAPIClient":
        import frappe

        config = frappe.get_single("Floatra Platform Config")
        # Password fields are stored encrypted; get_password() decrypts.
        return cls(
            base_url=config.floatra_gateway_url,
            api_key=config.get_password("api_key", raise_exception=False) or "",
            platform_id=config.platform_id or "",
        )


def onboard_body_for_customer(customer, address: dict = None) -> dict:
    """Customer (+ its primary Address row) → onboard body."""
    address = address or {}
    return contract.onboard_body(
        external_merchant_id=customer.name,
        name=getattr(customer, "customer_name", None) or customer.name,
        phone=getattr(customer, "mobile_no", None),
        business_type=getattr(customer, "floatra_business_type", None),
        category=getattr(customer, "floatra_primary_category", None),
        assigned_agent_id=getattr(customer, "floatra_assigned_agent_id", None),
        owner_first_name=getattr(customer, "floatra_owner_first_name", None),
        owner_last_name=getattr(customer, "floatra_owner_last_name", None),
        owner_email=getattr(customer, "email_id", None),
        street=address.get("address_line1"),
        lga=address.get("city"),
        state=address.get("state"),
    )


def initiate_body_for_sales_order(sales_order, category: str) -> dict:
    """Sales Order → initiate body (category resolved by the caller)."""
    return contract.initiate_body(
        external_merchant_id=sales_order.customer,
        external_order_id=sales_order.name,
        # Tax-inclusive by design (founder ruling 2026-10-08): the loan finances
        # the full invoice the merchant owes, VAT included, for VAT-registered
        # distributors too. Do not switch to the untaxed amount.
        amount=sales_order.grand_total,
        category=category,
        tenure_days=getattr(sales_order, "floatra_tenure_days", None),
        erp_type=ERP_TYPE,
    )


def frappe_values(updates: dict, field_names) -> dict:
    """Contract updates → a ``frappe.db.set_value`` dict for existing fields.

    Money stays ``Decimal`` (exact); ``None`` clears a field.
    """
    return {name: value for name, value in updates.items() if name in field_names}


def system_to_utc(value, tz_name: str):
    """A desk date-time (naive, in the site's SYSTEM timezone) → naive UTC.

    Frappe stores and shows Datetime values in the system timezone (e.g.
    Africa/Lagos, UTC+1); Floatra wants UTC. An aware value is converted;
    an empty one is returned unchanged. Anything unparseable raises
    ``ContractError`` (shown to the user like every other input error).
    """
    if value in (None, "", False):
        return value
    if isinstance(value, str):
        text = value.strip()
        try:
            value = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            try:
                value = datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S")
            except ValueError as exc:
                raise ContractError(f"Not a date-time: {text!r}") from exc
    if not isinstance(value, datetime):
        raise ContractError(f"Not a date-time: {value!r}")
    if value.tzinfo is None:
        value = value.replace(tzinfo=ZoneInfo(tz_name or "UTC"))
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def confirm_delivery_body_for_sales_order(
    sales_order, delivered_at, notes=None, tz_name: str = "UTC",
) -> dict:
    """Sales Order delivery fields (+ the dialog's time/notes) → confirm body.

    ``delivered_at`` is already UTC; the Sales Order's location time is a
    desk value in ``tz_name`` and is converted here.
    """
    return contract.confirm_delivery_body(
        delivered_at=delivered_at,
        notes=notes,
        agent_name=getattr(sales_order, "floatra_agent_name", None),
        confirmation_method=getattr(sales_order, "floatra_confirmation_method", None),
        lat=getattr(sales_order, "floatra_delivery_lat", None) or None,
        lng=getattr(sales_order, "floatra_delivery_lng", None) or None,
        accuracy_meters=(
            getattr(sales_order, "floatra_delivery_accuracy_meters", None) or None
        ),
        location_recorded_at=system_to_utc(
            getattr(sales_order, "floatra_delivery_location_at", None) or None,
            tz_name,
        ),
        proof_of_delivery_url=getattr(
            sales_order, "floatra_proof_of_delivery_url", None,
        ),
    )
