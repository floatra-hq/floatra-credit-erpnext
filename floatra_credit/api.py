# -*- coding: utf-8 -*-
"""Whitelisted endpoints called from the desk via frappe.call.

Every method authorizes on the specific document first (VULN-001:
``@frappe.whitelist`` only requires a login and ``frappe.get_doc`` does not
enforce record permissions), then builds the request with
``services.floatra_contract`` and calls Floatra through
``services.floatra_client``.

Floatra ids: ``Sales Order.floatra_order_id`` is Floatra's Order;
lifecycle calls (confirm-delivery, repayments, return, cancel) take the
LOAN id, ``Sales Order.floatra_loan_id``. Orders created before 0.3.0 only
have the Order id; ``_loan_id_for`` looks the loan up by order reference.
"""

from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from typing import Optional

import frappe
from frappe import _

from .services import floatra_contract as contract
from .services.credit_lock import is_locked_failsafe
from .services.delivery_validation import (
    DeliveryValidationError,
    validate_delivery_inputs,
)
from .services.floatra_api import (
    ContractError,
    FloatraAPIClient,
    FloatraAPIError,
    confirm_delivery_body_for_sales_order,
    frappe_values,
    initiate_body_for_sales_order,
    onboard_body_for_customer,
    system_to_utc,
)
from .services.kyc_link import HOSTED_LINK, kyc_retry_error_message

_TITLE = "Floatra"


def _fail(message: str) -> None:
    frappe.throw(message, title=_(_TITLE))


def _client() -> FloatraAPIClient:
    try:
        return FloatraAPIClient.for_site()
    except FloatraAPIError as err:
        _fail(_("Floatra is not configured: {0}").format(err.user_message()))


def _to_utc(value):
    """A desk date-time (site SYSTEM timezone) → naive UTC for Floatra."""
    from frappe.utils import get_system_timezone

    return system_to_utc(value, get_system_timezone())


def _system_tz() -> str:
    from frappe.utils import get_system_timezone

    return get_system_timezone()


def _fields_of(doctype: str) -> set:
    return {df.fieldname for df in frappe.get_meta(doctype).fields}


# ----------------------------------------------------------------
# Customers
# ----------------------------------------------------------------


def onboard_customer(customer) -> Optional[str]:
    """``POST /v1/partner/merchants/onboard`` for a Customer; stores the id.

    Returns the Floatra merchant id. Raises ContractError (bad Customer data)
    or FloatraAPIError; callers decide whether that blocks.
    """
    body = onboard_body_for_customer(customer, _primary_address(customer.name))
    onboarded = FloatraAPIClient.for_site().onboard_merchant(body)
    frappe.db.set_value(
        "Customer",
        customer.name,
        {
            "floatra_merchant_id": onboarded.floatra_merchant_id,
            "floatra_credit_status": "pending_kyc",
            "floatra_kyc_status": contract.kyc_status_for(onboarded.kyc_status),
        },
        update_modified=False,
    )
    return onboarded.floatra_merchant_id


def _primary_address(customer_name: str) -> dict:
    """The Customer's first linked Address (street / city as LGA / state)."""
    address_name = frappe.db.get_value(
        "Dynamic Link",
        {"link_doctype": "Customer", "link_name": customer_name, "parenttype": "Address"},
        "parent",
    )
    if not address_name:
        return {}
    return frappe.db.get_value(
        "Address", address_name, ["address_line1", "city", "state"], as_dict=True,
    ) or {}


@frappe.whitelist()
def action_onboard_customer(customer: str) -> dict:
    """Onboard (or retry onboarding) a Customer with Floatra."""
    doc = frappe.get_doc("Customer", customer)
    doc.check_permission("write")
    if doc.get("floatra_merchant_id"):
        return {"floatra_merchant_id": doc.floatra_merchant_id, "already": True}
    try:
        merchant_id = onboard_customer(doc)
    except ContractError as err:
        _fail(_("Cannot onboard with Floatra: {0}").format(err))
    except FloatraAPIError as err:
        _fail(_("Floatra onboarding failed: {0}").format(err.user_message()))
    except ValueError as err:
        _fail(_("Floatra returned an unexpected response: {0}").format(err))
    frappe.db.commit()
    return {"floatra_merchant_id": merchant_id, "already": False}


def sync_customer(client: FloatraAPIClient, customer_name: str, merchant_id: str) -> None:
    """Refresh one Customer from ``GET /v1/partner/merchants/:id/credit-status``."""
    updates = client.get_credit_status(merchant_id)
    updates["floatra_last_status_check"] = datetime.utcnow()
    frappe.db.set_value(
        "Customer",
        customer_name,
        frappe_values(updates, _fields_of("Customer")),
        update_modified=False,
    )


@frappe.whitelist()
def action_sync_customer(customer: str) -> dict:
    """Desk "Sync from Floatra": refresh this Customer's credit status."""
    doc = frappe.get_doc("Customer", customer)
    doc.check_permission("write")
    if not doc.get("floatra_merchant_id"):
        _fail(_("Customer is not onboarded with Floatra yet. Run Onboard first."))
    try:
        sync_customer(_client(), doc.name, doc.floatra_merchant_id)
    except FloatraAPIError as err:
        _fail(_("Floatra sync failed: {0}").format(err.user_message()))
    frappe.db.commit()
    return {"status": "ok"}


@frappe.whitelist()
def action_get_kyc_link(customer: str) -> dict:
    """Get a hosted KYC link for a Customer from Floatra.

    Calls ``POST /v1/partner/merchants/:id/kyc/retry`` with NO identity
    data. Live key: stores the returned ``kyc_url`` + expiry for staff to
    copy or email; the outcome arrives as ``merchant.kyc_completed``. Sandbox
    key: Floatra returns a simulated result, mirrored onto
    ``floatra_kyc_status``.

    Returns ``{kind: "hosted_link", kyc_url, expires_at}`` or
    ``{kind: "sandbox_result", status, verification_tier, message}``.
    """
    customer_doc = frappe.get_doc("Customer", customer)
    customer_doc.check_permission("write")  # VULN-001: authorize on the Customer

    if not customer_doc.get("floatra_merchant_id"):
        frappe.throw(_(
            "Customer is not onboarded with Floatra yet. Run Onboard first."
        ))

    client = _client()
    try:
        outcome = client.request_kyc_link(customer_doc.floatra_merchant_id)
    except FloatraAPIError as err:
        frappe.throw(kyc_retry_error_message(err.status, err.body))
    except ValueError as err:
        frappe.throw(
            _("Floatra returned an unexpected response: {0}").format(err),
        )

    if outcome.kind == HOSTED_LINK:
        expires_local = _utc_to_system_naive(outcome.expires_at)
        frappe.db.set_value(
            "Customer",
            customer,
            {
                "floatra_kyc_url": outcome.kyc_url,
                "floatra_kyc_url_expires_at": expires_local,
            },
            update_modified=False,
        )
        frappe.db.commit()
        return {
            "kind": outcome.kind,
            "kyc_url": outcome.kyc_url,
            "expires_at": str(expires_local),
        }

    frappe.db.set_value(
        "Customer",
        customer,
        {
            "floatra_kyc_url": None,
            "floatra_kyc_url_expires_at": None,
            "floatra_kyc_status": (
                "approved" if outcome.sandbox_verified else "pending"
            ),
        },
        update_modified=False,
    )
    frappe.db.commit()
    return {
        "kind": outcome.kind,
        "status": outcome.status,
        "verification_tier": outcome.verification_tier,
        "message": outcome.message,
    }


def _utc_to_system_naive(value: datetime) -> datetime:
    """Frappe stores Datetime naive in the SYSTEM timezone (not UTC)."""
    from frappe.utils import convert_utc_to_system_timezone

    return convert_utc_to_system_timezone(
        value.replace(tzinfo=None),
    ).replace(tzinfo=None)


@frappe.whitelist()
def action_set_bank_account(
    customer: str,
    bank_code: str,
    account_number: str,
    account_name: str,
    is_primary: int = 1,
) -> dict:
    """``POST /v1/partner/merchants/:id/bank-account`` for a Customer."""
    customer_doc = frappe.get_doc("Customer", customer)
    customer_doc.check_permission("write")  # VULN-001: authorize on the Customer
    if not customer_doc.get("floatra_merchant_id"):
        _fail(_("Customer is not onboarded with Floatra yet. Run Onboard first."))
    try:
        body = contract.bank_account_body(
            bank_code=bank_code,
            account_number=account_number,
            account_name=account_name,
            is_primary=bool(int(is_primary)) if is_primary not in ("", None) else True,
        )
    except ContractError as err:
        _fail(str(err))

    client = _client()
    try:
        result = client.set_bank_account(customer_doc.floatra_merchant_id, body)
    except FloatraAPIError as err:
        _fail(_("Floatra rejected the bank-account update: {0}").format(
            err.user_message(),
        ))

    masked = "*" * 8 + body["account_number"][-2:]
    customer_doc.add_comment(
        "Comment",
        _("Floatra bank account upserted: {bank} / {masked} ({name})").format(
            bank=body["bank_code"], masked=masked, name=body["account_name"],
        ),
    )
    return result


# ----------------------------------------------------------------
# Sales Orders
# ----------------------------------------------------------------


@frappe.whitelist()
def request_credit(sales_order_name: str) -> dict:
    """``POST /v1/partner/orders/initiate`` for a draft Sales Order.

    Preconditions: draft SO; Customer enrolled and not locked/suspended
    (``credit_lock`` fail-safe). Writes the decision onto the SO and
    returns ``{decision, reason}`` for the desk.
    """
    so = frappe.get_doc("Sales Order", sales_order_name)
    so.check_permission("write")  # VULN-001
    if so.docstatus != 0:
        _fail(_("Cannot request credit on a submitted Sales Order."))

    customer = frappe.get_doc("Customer", so.customer)
    if not customer.get("floatra_merchant_id"):
        _fail(
            _("Customer {0} is not enrolled with Floatra. Onboard it first.").format(
                customer.name,
            ),
        )
    if is_locked_failsafe(customer):
        _fail(
            _(
                "Customer {0} is locked from new credit (or its Floatra status "
                "is stale). Sync from Floatra, then retry."
            ).format(customer.name),
        )

    try:
        body = initiate_body_for_sales_order(so, _resolve_category_for(so))
    except ContractError as err:
        _fail(_("Cannot request Floatra credit: {0}").format(err))

    try:
        decision = _client().initiate_order(body)
    except FloatraAPIError as err:
        _fail(_("Credit request failed: {0}").format(err.user_message()))
    except ValueError as err:
        _fail(_("Floatra returned an unexpected response: {0}").format(err))

    frappe.db.set_value(
        "Sales Order",
        so.name,
        frappe_values(decision.order_updates(), _fields_of("Sales Order")),
        update_modified=False,
    )
    return {"decision": decision.decision, "reason": decision.reason()}


def _loan_id_for(so, client: FloatraAPIClient) -> str:
    """The Floatra LOAN id of a Sales Order (looked up once if missing)."""
    if so.get("floatra_loan_id"):
        return so.floatra_loan_id
    try:
        found = client.lookup_order(so.name)
    except FloatraAPIError as err:
        _fail(_("Could not find this order's Floatra loan: {0}").format(
            err.user_message(),
        ))
    if not found.get("floatra_loan_id"):
        _fail(_("Floatra has no loan for this order yet (it may be waitlisted)."))
    frappe.db.set_value(
        "Sales Order", so.name, "floatra_loan_id", found["floatra_loan_id"],
        update_modified=False,
    )
    return found["floatra_loan_id"]


def _require_funded(so, action: str) -> None:
    if not so.get("floatra_disbursed"):
        _fail(
            _(
                "Floatra has not funded this order yet. Wait for the funding "
                "webhook before {0}."
            ).format(action),
        )


@frappe.whitelist()
def record_cash_repayment(
    sales_order_name: str,
    amount,
    reference: str,
    source: str = "MANUAL_CASH",
    collected_at: Optional[str] = None,
    notes: Optional[str] = None,
) -> dict:
    """``POST /v1/partner/orders/:loan_id/repayments`` (dedup on reference)."""
    so = frappe.get_doc("Sales Order", sales_order_name)
    so.check_permission("write")  # VULN-001
    _require_funded(so, _("recording a repayment"))
    try:
        body = contract.repayment_body(
            amount=amount,
            reference=reference,
            source=source,
            # The dialog's value is site-local time; Floatra wants UTC.
            collected_at=_to_utc(collected_at) if collected_at else datetime.utcnow(),
            notes=notes,
        )
    except ContractError as err:
        _fail(_("Repayment validation failed: {0}").format(err))

    client = _client()
    try:
        result = client.record_repayment(_loan_id_for(so, client), body)
    except FloatraAPIError as err:
        _fail(_("Floatra repayment recording failed: {0}").format(err.user_message()))

    so.add_comment(
        "Comment",
        _("Floatra repayment recorded: ₦{amount} via {source} (ref: {ref}). "
          "Outstanding: ₦{left}.").format(
            amount=result["amount"],
            source=body["source"],
            ref=body["reference"],
            left=result["outstanding_balance"],
        ),
    )
    return {
        "amount": str(result["amount"]),
        "outstanding_balance": str(result["outstanding_balance"]),
    }


@frappe.whitelist()
def confirm_delivery_for_sales_order(
    sales_order_name: str,
    delivered_at: Optional[str] = None,
    notes: Optional[str] = None,
) -> dict:
    """``POST /v1/partner/orders/:loan_id/confirm-delivery`` (full delivery)."""
    so = frappe.get_doc("Sales Order", sales_order_name)
    so.check_permission("write")  # VULN-001
    _require_funded(so, _("recording delivery"))
    if so.get("floatra_delivery_confirmed"):
        return {"already_confirmed": True}

    try:
        # Desk times are site-local: convert to UTC BEFORE the future/backdate
        # checks (which compare against UTC now) and before sending.
        validated = validate_delivery_inputs(
            delivered_at=_to_utc(delivered_at) if delivered_at else datetime.utcnow(),
            notes=notes,
        )
        body = confirm_delivery_body_for_sales_order(
            so, validated["delivered_at"], validated.get("notes"), tz_name=_system_tz(),
        )
    except (DeliveryValidationError, ContractError) as err:
        _fail(_("Delivery validation failed: {0}").format(err))

    client = _client()
    try:
        delivery = client.confirm_delivery(
            _loan_id_for(so, client), body, idempotency_key=f"{so.name}:confirm",
        )
    except FloatraAPIError as err:
        _fail(_("Floatra delivery confirmation failed: {0}").format(
            err.user_message(),
        ))

    updates = {"floatra_delivery_confirmed": 1 if delivery.delivery_confirmed else 0}
    if delivery.repayment_due_date:
        updates["floatra_repayment_due_date"] = delivery.repayment_due_date
    if delivery.repayment_amount is not None:
        updates["floatra_repayment_amount"] = delivery.repayment_amount
    frappe.db.set_value("Sales Order", so.name, updates, update_modified=False)
    so.add_comment(
        "Comment",
        _("Floatra delivery confirmed at {0}").format(body["delivered_at"]),
    )
    return {"delivery_confirmed": delivery.delivery_confirmed, "status": delivery.status}


@frappe.whitelist()
def action_cancel_floatra_credit(sales_order_name: str) -> dict:
    """Cancel Floatra credit on a DRAFT Sales Order (approved, not funded).

    A draft cannot be cancelled in ERPNext (only deleted), so the
    ``on_cancel`` hook never runs for it: this releases the lender's
    reservation (``POST /v1/partner/orders/:loan_id/cancel``) so the order
    can ship on the distributor's own terms or be deleted.
    """
    from .controllers.sales_order import release_floatra_reservation

    so = frappe.get_doc("Sales Order", sales_order_name)
    so.check_permission("write")  # VULN-001
    if so.get("floatra_decision") != "approved":
        _fail(_("This Sales Order has no approved Floatra credit to cancel."))
    if so.get("floatra_disbursed"):
        _fail(_(
            "Floatra has already funded this order: the customer has a loan. "
            "It can no longer be cancelled."
        ))
    try:
        release_floatra_reservation(so)
    except FloatraAPIError as err:
        _fail(_("Floatra could not cancel the credit: {0}").format(err.user_message()))
    frappe.db.set_value(
        "Sales Order",
        so.name,
        {"floatra_decision": "not_requested",
         "floatra_decision_reason": _("Floatra credit cancelled")},
        update_modified=False,
    )
    so.add_comment("Comment", _("Floatra credit cancelled; lender capacity released."))
    return {"status": "cancelled"}


@frappe.whitelist()
def action_initiate_return(
    sales_order_name: str,
    return_amount,
    reason_code: str,
    reason_notes: str = "",
    initiated_by_agent_id: str = "",
) -> dict:
    """``POST /v1/partner/orders/:loan_id/return``.

    ``return_amount`` is Naira (decimal string or number). Floatra takes a
    return only against a loan with a live delivery dispute (``RETURN_015``),
    which today exists only for offline orders.
    """
    so = frappe.get_doc("Sales Order", sales_order_name)
    so.check_permission("write")  # VULN-001
    _require_funded(so, _("initiating a return"))
    try:
        body = contract.return_body(
            return_amount=return_amount,
            reason_code=reason_code,
            reason_notes=reason_notes,
            agent_id=initiated_by_agent_id,
        )
    except ContractError as err:
        _fail(str(err))

    client = _client()
    try:
        result = client.initiate_return(
            _loan_id_for(so, client), body, idempotency_key=f"so-return:{so.name}",
        )
    except FloatraAPIError as err:
        _fail(_("Floatra rejected the return: {0}").format(err.user_message()))

    so.add_comment(
        "Comment",
        _("Floatra return initiated: ₦{amount} ({reason})").format(
            amount=body["return_amount"], reason=body["reason_code"],
        ),
    )
    return result


def _resolve_category_for(so) -> str:
    """The Floatra category carrying the most revenue on the order (else OTHER)."""
    weights: dict = defaultdict(Decimal)
    for item in so.items or []:
        group_name = item.item_group or _resolve_item_group(item.item_code)
        if not group_name:
            continue
        floatra_cat = frappe.db.get_value("Item Group", group_name, "floatra_category")
        if not floatra_cat:
            continue
        weights[floatra_cat] += contract.to_decimal(item.amount or 0)
    if not weights:
        return "OTHER"
    return max(weights.items(), key=lambda kv: kv[1])[0]


def _resolve_item_group(item_code: Optional[str]) -> Optional[str]:
    if not item_code:
        return None
    return frappe.db.get_value("Item", item_code, "item_group")


# ----------------------------------------------------------------
# Reconciliation
# ----------------------------------------------------------------

_LOAN_STATUSES = (
    "CREATED", "APPROVED", "FUNDED", "ACTIVE", "REPAID", "OVERDUE",
    "DEFAULTED", "DECLINED", "DISBURSEMENT_FAILED", "RESTRUCTURED",
    "WRITTEN_OFF", "AWAITING_MANDATE",
)
_FUNDED_STATUSES = (
    "FUNDED", "ACTIVE", "OVERDUE", "REPAID", "DEFAULTED", "RESTRUCTURED",
)


@frappe.whitelist()
def action_reconcile_orders(
    status: str = "",
    from_iso: str = "",
    to_iso: str = "",
    max_orders: int = 200,
) -> dict:
    """Walk ``GET /v1/partner/orders`` against local Sales Orders.

    Each Floatra row is a loan; it is matched on ``floatra_loan_id``.
    Buckets: ``synced``; ``missing_local`` (no Sales Order carries the
    loan); ``status_drift`` (Floatra says the loan was funded but the Sales
    Order is not marked funded). Bounded by ``max_orders`` (cap 1000); re-run
    with the returned ``next_cursor`` to continue.
    """
    # VULN-001: reconciliation walks every Sales Order.
    frappe.has_permission("Sales Order", "read", throw=True)
    max_orders = max(1, min(int(max_orders or 200), 1000))
    if status and status not in _LOAN_STATUSES:
        _fail(_("status must be a Floatra loan status or empty."))

    client = _client()
    scanned = 0
    synced = 0
    missing_local: list = []
    status_drift: list = []
    cursor: Optional[str] = None
    has_more = False

    while scanned < max_orders:
        try:
            page = client.list_orders(
                status=status or None,
                from_iso=from_iso or None,
                to_iso=to_iso or None,
                cursor=cursor,
                limit=50,
            )
        except FloatraAPIError as err:
            _fail(_("Floatra orders listing failed after {0} rows: {1}").format(
                scanned, err.user_message(),
            ))
        for row in page["rows"]:
            if scanned >= max_orders:
                break
            scanned += 1
            so_name = frappe.db.get_value(
                "Sales Order", {"floatra_loan_id": row["floatra_loan_id"]}, "name",
            )
            if not so_name:
                missing_local.append({
                    "floatra_loan_id": row["floatra_loan_id"],
                    "floatra_status": row["status"],
                    "floatra_merchant_id": row["floatra_merchant_id"],
                    "created_at": row["created_at"],
                })
                continue
            funded_locally = frappe.db.get_value(
                "Sales Order", so_name, "floatra_disbursed",
            )
            if row["status"] in _FUNDED_STATUSES and not funded_locally:
                status_drift.append({
                    "floatra_loan_id": row["floatra_loan_id"],
                    "sales_order": so_name,
                    "floatra_status": row["status"],
                    "reason": f"{row['status']} but not marked funded locally",
                })
            else:
                synced += 1
        has_more = page["has_more"]
        cursor = page["next_cursor"]
        if not has_more or not cursor or not page["rows"]:
            break

    return {
        "scanned": scanned,
        "synced": synced,
        "missing_local": missing_local,
        "status_drift": status_drift,
        "has_more": has_more,
        "next_cursor": cursor,
    }
