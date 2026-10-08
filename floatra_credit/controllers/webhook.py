# -*- coding: utf-8 -*-
"""Floatra → ERPNext webhook receiver.

Endpoint: ``POST /api/method/floatra_credit.controllers.webhook.receive``

Per delivery:
  1. Verify (``floatra_contract.verify_webhook``): ``X-Floatra-Timestamp`` is
     Unix SECONDS; ``X-Floatra-Signature`` is hex HMAC-SHA256 over
     ``"<timestamp>.<raw body>"`` with the webhook secret; the timestamp must
     be within 5 minutes. Any failure → 401.
  2. Skip an event id already processed, or a signature already processed
     under another event id (200 ``duplicate``); re-run a failed one.
  3. Drop (200 ``ignored``, logged) an event whose ``livemode`` is not this
     site's key realm.
  4. Apply the flat camelCase payload (``{event, merchantId, loanId, ...}``,
     money as decimal-Naira strings), matching records by Floatra ids first.
  5. Log every delivery (PII-redacted) with its outcome. A handler error
     rolls back and answers 5xx so Floatra retries.

``process_event`` is shared with the undelivered-webhook replay task.
"""

import json
import logging
from datetime import datetime

import frappe

from ..services.floatra_api import frappe_values
from ..services.floatra_contract import (
    WebhookRejected,
    credit_status_after_kyc,
    credit_status_after_lock,
    interpret_webhook,
    is_live_key,
    order_candidate_ok,
    partner_candidate_ok,
    verify_webhook,
    webhook_realm_matches,
)
from ..services.pii_redaction import redact_pii

_logger = logging.getLogger(__name__)

_LOG_LEVELS = {"info": logging.INFO, "warning": logging.WARNING, "error": logging.ERROR}


@frappe.whitelist(allow_guest=True)
def receive():
    """Read the raw body (byte-for-byte what Floatra signed) and process it."""
    headers = frappe.local.request.headers
    raw_body = frappe.local.request.get_data() or b""
    event_id = headers.get("X-Floatra-Event-ID") or ""

    config = frappe.get_single("Floatra Platform Config")
    secret = config.get_password("webhook_secret", raise_exception=False) or ""
    try:
        verify_webhook(
            secret,
            headers.get("X-Floatra-Timestamp") or "",
            raw_body,
            headers.get("X-Floatra-Signature") or "",
        )
    except WebhookRejected as rejected:
        _logger.warning("Floatra webhook %s rejected: %s", event_id, rejected.reason)
        frappe.local.response["http_status_code"] = 401
        return {"status": "rejected", "reason": rejected.reason}

    if not event_id:
        frappe.local.response["http_status_code"] = 400
        return {"status": "rejected", "reason": "missing_event_id"}
    try:
        payload = json.loads(raw_body.decode("utf-8") or "{}")
    except ValueError:
        frappe.local.response["http_status_code"] = 400
        return {"status": "rejected", "reason": "invalid_json"}

    outcome = process_event(event_id, payload, headers.get("X-Floatra-Signature") or "")
    if outcome == "failed":
        frappe.local.response["http_status_code"] = 500
    return {"status": outcome}


def _key_is_live() -> bool:
    config = frappe.get_single("Floatra Platform Config")
    return is_live_key(config.get_password("api_key", raise_exception=False) or "")


def process_event(event_id: str, payload: dict, signature: str = "") -> str:
    """Log + apply one event; returns duplicate / ok / ignored / failed.

    Only a successfully processed row counts as done: a ``failed`` (or
    interrupted ``received``) row is re-run when Floatra retries or the
    replay task lists it. A signature that was already processed under
    another event id is a replay and is not applied again. An event from the
    other realm (``livemode`` vs this site's key) is logged ``ignored``.
    """
    existing = frappe.db.get_value("Floatra Webhook Log", event_id, "status")
    if existing in ("processed", "ignored"):
        return "duplicate"
    if signature and frappe.db.exists(
        "Floatra Webhook Log", {"signature": signature, "status": "processed"},
    ):
        _logger.warning(
            "Floatra webhook %s replays an already-processed signature; ignored",
            event_id,
        )
        return "duplicate"
    if existing:
        log = frappe.get_doc("Floatra Webhook Log", event_id)
        if signature:
            log.signature = signature
    else:
        log = frappe.get_doc(
            {
                "doctype": "Floatra Webhook Log",
                "event_id": event_id,
                "event_type": str(payload.get("event") or "unknown"),
                "received_at": datetime.utcnow(),
                "signature_verified": 1 if signature else 0,
                "signature": signature or None,
                "status": "received",
                "platform_id": frappe.db.get_single_value(
                    "Floatra Platform Config", "platform_id",
                ),
                # PII never persists in plaintext on the ERP side (spec §14).
                "payload": json.dumps(redact_pii(payload), indent=2),
            }
        )
        log.insert(ignore_permissions=True)
    frappe.db.commit()

    if not webhook_realm_matches(payload, _key_is_live()):
        _logger.info(
            "Floatra webhook %s (%s) is from the other realm (livemode=%r); ignored",
            event_id, payload.get("event"), payload.get("livemode"),
        )
        log.status = "ignored"
        log.error = "Event from the other realm (livemode mismatch)"
        log.processed_at = datetime.utcnow()
        log.save(ignore_permissions=True)
        frappe.db.commit()
        return "ignored"
    try:
        apply_webhook(payload)
    except Exception as err:  # noqa: BLE001 — logged; Floatra retries
        _logger.exception("Floatra webhook %s processing failed", event_id)
        frappe.db.rollback()
        log.reload()
        log.status = "failed"
        log.error = str(err)[:140]
        log.save(ignore_permissions=True)
        frappe.db.commit()
        return "failed"
    log.status = "processed"
    log.error = None
    log.processed_at = datetime.utcnow()
    log.save(ignore_permissions=True)
    frappe.db.commit()
    return "ok"


def apply_webhook(payload: dict) -> dict:
    """Apply one payload; returns ``{customer, sales_order}`` it touched."""
    effect = interpret_webhook(payload)
    if not effect.handled:
        _logger.info("Floatra webhook: no handler for %r", effect.event)
        return {"customer": None, "sales_order": None}

    customer = _find_customer(effect)
    sales_order = _find_sales_order(effect)
    if customer:
        _apply_customer(customer, effect)
    if sales_order and effect.order_updates:
        values = frappe_values(effect.order_updates, _fields_of("Sales Order"))
        if values:
            frappe.db.set_value("Sales Order", sales_order, values, update_modified=False)

    if effect.note:
        _logger.log(
            _LOG_LEVELS.get(effect.severity, logging.INFO),
            "Floatra %s: %s", effect.event, effect.note,
        )
        target = ("Sales Order", sales_order) if sales_order else (
            ("Customer", customer) if customer else None
        )
        if target:
            _comment(*target, effect.note)
    return {"customer": customer, "sales_order": sales_order}


def _apply_customer(customer: str, effect) -> None:
    values = frappe_values(effect.partner_updates, _fields_of("Customer"))
    if effect.lock is not None or effect.kyc_completed:
        current = frappe.db.get_value("Customer", customer, "floatra_credit_status")
        status = current
        if effect.lock is not None:
            status = credit_status_after_lock(status, effect.lock)
        if effect.kyc_completed:
            status = credit_status_after_kyc(status)
        values["floatra_credit_status"] = status
        values["floatra_last_status_check"] = datetime.utcnow()
    if values:
        frappe.db.set_value("Customer", customer, values, update_modified=False)


def _find_customer(effect):
    """By Floatra merchant id; our id (Customer name) only if its stored
    Floatra merchant id equals the payload's."""
    if effect.merchant_id:
        name = frappe.db.get_value(
            "Customer", {"floatra_merchant_id": effect.merchant_id}, "name",
        )
        if name:
            return name
    external = effect.merchant_external_id
    if external and partner_candidate_ok(
        frappe.db.get_value("Customer", external, "floatra_merchant_id"), effect,
    ):
        return external
    return None


def _find_sales_order(effect):
    """By Floatra loan id, then loan-request id; by order reference (Sales
    Order name) only when its stored Floatra ids agree with the payload's."""
    if effect.loan_id:
        name = frappe.db.get_value(
            "Sales Order", {"floatra_loan_id": effect.loan_id}, "name",
        )
        if name:
            return name
    if effect.loan_request_id:
        name = frappe.db.get_value(
            "Sales Order", {"floatra_loan_request_id": effect.loan_request_id}, "name",
        )
        if name:
            return name
    name = effect.external_order_id
    if name and frappe.db.exists("Sales Order", name):
        customer = frappe.db.get_value("Sales Order", name, "customer")
        if order_candidate_ok(
            frappe.db.get_value("Sales Order", name, "floatra_loan_id"),
            frappe.db.get_value("Sales Order", name, "floatra_loan_request_id"),
            frappe.db.get_value("Customer", customer, "floatra_merchant_id")
            if customer else None,
            effect,
        ):
            return name
        _logger.warning(
            "Floatra %s names Sales Order %s but its Floatra ids disagree; ignored",
            effect.event, name,
        )
    return None


def _fields_of(doctype: str) -> set:
    return {df.fieldname for df in frappe.get_meta(doctype).fields}


def _comment(doctype: str, name: str, text: str) -> None:
    frappe.get_doc(
        {
            "doctype": "Comment",
            "comment_type": "Comment",
            "reference_doctype": doctype,
            "reference_name": name,
            "content": text,
        }
    ).insert(ignore_permissions=True)
