# -*- coding: utf-8 -*-
"""Sales Order doc-event handlers.

  validate
    P0-15 server-side reorder lock. ERP Spec v3.1 §4 (merchant
    .reorder_locked event) requires "ERP MUST: immediately block ALL
    new orders for this merchant." Refuses to save a Sales Order for
    a Customer whose `floatra_credit_status` is `locked` or
    `suspended`, regardless of whether a credit request was
    ever made. v3.0 only gated `before_submit` against
    `floatra_disbursed`, so a sales user who never clicked "Request
    Financing" could ship to a defaulted merchant.

  before_submit
    The funding gate. While Floatra credit is pending, approved or
    waitlisted (`floatra_decision`) and Floatra has not funded the order
    yet (`floatra_disbursed` is False), block the submit. A declined
    order ships on the distributor's own terms.

  on_submit
    Hook surface for future logic (e.g., posting a "delivery
    initiated" event to Floatra). Not used at scaffold time but
    wired in `hooks.py` so future code lands without a manifest
    change.
"""

import frappe
from frappe import _


# Customer states that block new orders. `pending_kyc` and other
# transient states are allowed through — only the explicit
# enforcement statuses gate.
_BLOCKING_CUSTOMER_STATES = {"locked", "suspended"}


def validate(doc, method=None) -> None:
    """P0-15: refuse to save a SO for a Floatra-blocked customer.

    Fires on every save (draft, submit, edit). Cheap — a single
    DB lookup of one column on the Customer row.
    """
    customer = getattr(doc, "customer", None)
    if not customer:
        return
    status = frappe.db.get_value(
        "Customer", customer, "floatra_credit_status",
    ) or ""
    if status not in _BLOCKING_CUSTOMER_STATES:
        return
    if status == "locked":
        frappe.throw(
            _(
                "Cannot save this Sales Order — customer {customer} is "
                "currently <b>reorder-locked</b> by Floatra. The "
                "outstanding balance must be cleared before any new "
                "order can be created."
            ).format(customer=customer),
            title=_("Floatra: customer locked"),
        )
    frappe.throw(
        _(
            "Cannot save this Sales Order — customer {customer} is "
            "<b>suspended</b> by Floatra. Contact Floatra ops to "
            "reinstate before placing new orders."
        ).format(customer=customer),
        title=_("Floatra: customer suspended"),
    )


# Decisions under which Floatra may still fund the order: shipping waits.
_AWAITING_FUNDING = {"pending", "approved", "waitlisted"}


def before_submit(doc, method=None) -> None:
    decision = getattr(doc, "floatra_decision", "") or ""
    if decision not in _AWAITING_FUNDING:
        # No credit requested, or declined: nothing to gate.
        return

    if not bool(getattr(doc, "floatra_disbursed", False)):
        frappe.throw(
            _(
                "Cannot submit this Sales Order — Floatra credit was requested "
                "but Floatra has not funded the order yet. Wait for the "
                "<b>Disbursed</b> flag (set by Floatra's funding webhook), "
                "or use <b>Floatra → Cancel Floatra Credit</b> to ship on your "
                "own terms."
            ),
            title=_("Floatra funding pending"),
        )


def on_submit(doc, method=None) -> None:
    # Placeholder. Real implementation goes here once we add the
    # post-submit notification flow.
    pass


# Loan statuses where on_cancel must refuse — at these states the
# disbursement has happened and the merchant has goods on the way
# (or in hand). Cancelling at this point would orphan the loan
# without releasing exposure, which would unbalance the gateway.
# The merchant must either repay or initiate a return.
_NON_CANCELLABLE_DECISIONS = {"approved"}


def on_cancel(doc, method=None) -> None:
    """P1-16: when the salesperson cancels a SO that's tied to a
    credit decision, mirror the cancel into Floatra so the
    gateway releases lender exposure and removes the reorder lock.

    Cases:
      - No credit approved (decision not_requested / pending / declined /
        waitlisted): nothing to do; no capacity has been reserved.
      - Approved + NOT funded: call /v1/partner/orders/{loan_id}/cancel
        so the lender capacity reservation is released. Cancel is
        idempotent on the gateway side.
      - Approved + disbursed: refuse with frappe.throw — the
        merchant has goods + a debt. Cancelling the SO would lose
        the loan tracking. The salesperson must initiate a return
        through the dedicated flow instead.
    """
    decision = getattr(doc, "floatra_decision", "") or ""
    if decision not in _NON_CANCELLABLE_DECISIONS:
        # Pending / declined / waitlisted / not_requested — no
        # reservation to release; safe to no-op.
        return

    if bool(getattr(doc, "floatra_disbursed", False)):
        frappe.throw(
            _(
                "Cannot cancel Sales Order {name} — credit has "
                "already disbursed. The merchant has received the goods "
                "+ has an active loan. Initiate a return via the "
                "Floatra return workflow instead of cancelling here."
            ).format(name=doc.name),
            title=_("Floatra: cancel not allowed"),
        )

    # Approved + unfunded → tell Floatra to release the reservation.
    # Failures don't block the local cancel: they are logged for ops to
    # reconcile. The cancel endpoint is idempotent, so a replay is safe.
    _release_or_log(doc, "cancelled")


def on_trash(doc, method=None) -> None:
    """Deleting a draft with approved, unfunded Floatra credit releases the
    reservation (a draft is deleted, never cancelled, so ``on_cancel`` does
    not run for it). A funded order cannot be deleted while its loan lives,
    and a release Floatra refuses aborts the delete.
    """
    if (getattr(doc, "floatra_decision", "") or "") not in _NON_CANCELLABLE_DECISIONS:
        return
    if bool(getattr(doc, "floatra_disbursed", False)):
        frappe.throw(
            _(
                "Cannot delete Sales Order {name} — Floatra has funded it, so "
                "the customer has a loan."
            ).format(name=doc.name),
            title=_("Floatra: delete not allowed"),
        )
    from ..services.floatra_api import FloatraAPIError

    # The order may hold the only record of the loan id: if Floatra refuses
    # the cancel (e.g. it funded the loan and the webhook has not landed
    # yet), abort the delete. Only "Floatra has no such order/loan" (404) —
    # nothing to release — lets the delete through.
    try:
        release_floatra_reservation(doc)
    except FloatraAPIError as err:
        if err.status == 404:
            return
        frappe.throw(
            _(
                "Cannot delete Sales Order {name}: Floatra did not release the "
                "credit ({reason}). Sync with Floatra and try again."
            ).format(name=doc.name, reason=err.user_message()),
            title=_("Floatra: delete not allowed"),
        )


def release_floatra_reservation(doc) -> None:
    """``POST /v1/partner/orders/:loan_id/cancel`` for an approved, unfunded
    order. Raises FloatraAPIError on failure."""
    from ..services.floatra_api import FloatraAPIClient, FloatraAPIError
    from ..services.floatra_contract import cancel_body

    client = FloatraAPIClient.for_site()
    loan_id = getattr(doc, "floatra_loan_id", None) or client.lookup_order(
        doc.name,
    ).get("floatra_loan_id")
    if not loan_id:
        # Nothing to release (no loan yet): the same meaning as a 404.
        raise FloatraAPIError(404, None, "Floatra has no loan for this order")
    client.cancel_order(
        loan_id,
        cancel_body(
            reason_code="OTHER",
            reason_notes=f"Sales Order {doc.name} cancelled in ERPNext.",
        ),
        idempotency_key=f"so-cancel:{doc.name}",
    )


def _release_or_log(doc, action: str) -> None:
    from ..services.floatra_api import FloatraAPIError

    try:
        release_floatra_reservation(doc)
    except FloatraAPIError as err:
        frappe.log_error(
            title=_("Floatra cancel-on-{0} failed").format(action),
            message=(
                f"Sales Order {doc.name} {action} locally but the Floatra "
                f"cancel call failed: {err.user_message()}. Reconcile with "
                f"Floatra."
            ),
        )
