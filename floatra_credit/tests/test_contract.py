# -*- coding: utf-8 -*-
"""The app against Floatra core's real contract, without a bench.

Every fixture in ``fixtures/contract`` except the ``request-*`` files is
rendered by core (``src/modules/partner-webhooks/partner-contract-fixtures.spec.ts``)
from its real controllers, DTO pipe, exception filter and webhook pipeline.
The ``request-*`` files are this app's bodies (``contract_support``); core
validates them against its DTOs. So:

* the request bodies built here match what core's DTOs accept;
* the client calls the real ``/v1/partner/...`` route and parses every
  response core can send for it;
* the webhook receiver accepts core's signed deliveries and applies every
  payload to the right Customer / Sales Order.

``frappe`` is stubbed. Run from ``connectors/erpnext/floatra_credit``:
``python3 -m unittest floatra_credit.tests.test_contract`` (or pytest).
"""

import json
import logging
import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace as NS
from unittest.mock import MagicMock, patch

from . import frappe_stub

frappe_stub.install()
load_tests = frappe_stub.load_tests

from .. import api  # noqa: E402
from ..controllers import sales_order as so_controller  # noqa: E402
from ..controllers import webhook  # noqa: E402
from ..services import floatra_api, floatra_client, floatra_contract as contract  # noqa: E402
from . import contract_support as support  # noqa: E402

logging.getLogger("floatra_credit").setLevel(logging.CRITICAL)

fixture = support.fixture
LIVE_KEY = "live_pk_" + "a1" * 24
BASE = "https://api.floatra.com"


def _response(fx):
    resp = MagicMock()
    resp.status_code = fx["status"]
    resp.json.return_value = fx["body"]
    return resp


def _client():
    return floatra_api.FloatraAPIClient(
        base_url="https://api.floatra.io/v1",  # a pre-0.3 stored value
        api_key=LIVE_KEY,
        platform_id="acme-distributors",
        sleep=lambda _s: None,
    )


def _payload(event):
    return json.loads(fixture("webhook-" + event)["body"])


class TestUrlsAndMoney(unittest.TestCase):
    def test_base_url_is_host_only(self):
        for raw in (None, "", "https://api.floatra.io/v1", "https://api.floatra.com/v1/"):
            self.assertEqual(contract.normalize_base_url(raw), BASE, raw)
        self.assertEqual(
            contract.partner_url(BASE, "orders", "loan_01", "return"),
            BASE + "/v1/partner/orders/loan_01/return",
        )

    def test_dot_or_empty_ids_are_refused(self):
        for bad in ("", ".", ".."):
            with self.assertRaises(contract.ContractError):
                contract.partner_path("orders", bad, "cancel")

    def test_decimal_naira_strings(self):
        self.assertEqual(contract.naira(250000.0), "250000.00")
        self.assertEqual(contract.naira("0.005"), "0.01")
        self.assertEqual(contract.naira_from_kobo(5000), "50.00")
        with self.assertRaises(contract.ContractError):
            contract.naira("-5")


class TestRequestBodies(unittest.TestCase):
    def test_each_body_matches_its_fixture(self):
        for name, body in support.build_requests().items():
            self.assertEqual(body, fixture(name), name)

    def test_no_stale_request_fixture(self):
        names = {
            n for n in support.fixture_names("request-")
            if not n.startswith("request-kyc-retry")
        }
        self.assertEqual(names, set(support.build_requests()))

    def test_no_kobo_or_test_flag_or_identity_numbers(self):
        blob = json.dumps(support.build_requests())
        for stale in ('"amount_kobo"', '"is_test"', '"bvn"', '"nin"', '"idempotency_key"'):
            self.assertNotIn(stale, blob)

    def test_onboard_minimal_customer(self):
        body = floatra_api.onboard_body_for_customer(
            NS(name="CUST-1", customer_name="Shop", mobile_no="08031234567"),
        )
        self.assertEqual(body, {
            "external_merchant_id": "CUST-1", "name": "Shop", "phone": "+2348031234567",
        })


CALLS = [
    ("onboard_merchant", lambda r: (r["request-onboard"],), "POST",
     "/v1/partner/merchants/onboard", "request-onboard", "response-onboard-created"),
    ("initiate_order", lambda r: (r["request-initiate"],), "POST",
     "/v1/partner/orders/initiate", "request-initiate", "response-initiate-approved"),
    ("lookup_order", lambda r: ("SAL-ORD-2026-00042",), "GET",
     "/v1/partner/orders/by-external-id/SAL-ORD-2026-00042", None,
     "response-order-lookup"),
    ("confirm_delivery", lambda r: ("loan_01", r["request-confirm-delivery"], "k1"),
     "POST", "/v1/partner/orders/loan_01/confirm-delivery",
     "request-confirm-delivery", "response-confirm-delivery"),
    ("cancel_order", lambda r: ("loan_01", r["request-cancel"], "k2"), "POST",
     "/v1/partner/orders/loan_01/cancel", "request-cancel", "response-cancel"),
    ("record_repayment", lambda r: ("loan_01", r["request-repayment"]), "POST",
     "/v1/partner/orders/loan_01/repayments", "request-repayment", "response-repayment"),
    ("initiate_return", lambda r: ("loan_01", r["request-return"], "k3"), "POST",
     "/v1/partner/orders/loan_01/return", "request-return", "response-return"),
    ("list_orders", lambda r: (), "GET", "/v1/partner/orders", None,
     "response-orders-list"),
    ("bulk_initiate_orders", lambda r: (r["request-bulk"],), "POST",
     "/v1/partner/orders/bulk", "request-bulk", "response-bulk"),
    ("get_credit_status", lambda r: ("mer_01",), "GET",
     "/v1/partner/merchants/mer_01/credit-status", None,
     "response-credit-status-verified"),
    ("get_lock_status", lambda r: ("mer_01",), "GET",
     "/v1/partner/merchants/mer_01/lock-status", None, "response-lock-status-unlocked"),
    ("set_bank_account", lambda r: ("mer_01", r["request-bank-account"]), "POST",
     "/v1/partner/merchants/mer_01/bank-account", "request-bank-account",
     "response-bank-account"),
    ("request_kyc_link", lambda r: ("mer_01",), "POST",
     "/v1/partner/merchants/mer_01/kyc/retry", "request-kyc-retry-live",
     "response-kyc-retry-hosted-link"),
    ("get_platform_health", lambda r: (), "GET", "/v1/partner/platform/health", None,
     "response-health"),
    ("post_key_rotation_complete", lambda r: (), "POST",
     "/v1/partner/platform/key-rotation-complete", None,
     "response-key-rotation-complete"),
    ("get_undelivered_webhooks", lambda r: (50, 0), "GET",
     "/v1/partner/webhooks/undelivered?limit=50&offset=0", None,
     "response-undelivered"),
    ("acknowledge_webhook", lambda r: ("dlv_disbursed",), "POST",
     "/v1/partner/webhooks/dlv_disbursed/acknowledge", None, "response-acknowledge"),
]


class TestClientCalls(unittest.TestCase):
    def test_every_call_hits_the_real_route_with_the_contract_body(self):
        requests_ = support.build_requests()
        requests_["request-kyc-retry-live"] = fixture("request-kyc-retry-live")
        for method, args, verb, path, req_fx, resp_fx in CALLS:
            with self.subTest(method=method):
                with patch.object(
                    floatra_client.requests, "request",
                    return_value=_response(fixture(resp_fx)),
                ) as req:
                    getattr(_client(), method)(*args(requests_))
                (sent_verb, url), kwargs = req.call_args[0], req.call_args[1]
                self.assertEqual((sent_verb, url), (verb, BASE + path))
                headers = kwargs["headers"]
                self.assertEqual(headers["Authorization"], "Bearer " + LIVE_KEY)
                self.assertEqual(headers["X-Platform-ID"], "acme-distributors")
                if verb == "GET":
                    self.assertIsNone(kwargs["data"])
                else:
                    self.assertTrue(headers["Idempotency-Key"])
                    expected = fixture(req_fx) if req_fx else {}
                    self.assertEqual(json.loads(kwargs["data"]), expected)

    def test_parsed_results(self):
        page = contract.parse_orders_page(fixture("response-orders-list")["body"])
        self.assertEqual(page["rows"][0]["floatra_loan_id"], "loan_01")
        self.assertFalse(page["has_more"])
        decision = contract.parse_decision(fixture("response-initiate-declined")["body"])
        self.assertEqual(decision.order_updates()["floatra_decision"], "declined")
        waitlisted = contract.parse_decision(fixture("response-initiate-waitlisted")["body"])
        self.assertIn("#3", waitlisted.reason())
        for name, status in (
            ("response-credit-status-verified", "available"),
            ("response-credit-status-unverified", "pending_kyc"),
            ("response-credit-status-locked", "locked"),
            ("response-credit-status-suspended", "suspended"),
        ):
            self.assertEqual(
                contract.parse_credit_status(fixture(name)["body"])["floatra_credit_status"],
                status, name,
            )

    def test_errors_map_by_code(self):
        cases = {
            "response-return-error-no-dispute": "open delivery dispute",
            "response-initiate-error-merchant-not-found": "Onboard the customer",
            "response-initiate-error-validation": "amount_kobo should not exist",
            "response-lock-status-error-unavailable": "Treat the customer as locked",
        }
        for name, expected in cases.items():
            fx = fixture(name)
            err = contract.FloatraAPIError(fx["status"], fx["body"])
            self.assertIn(expected, err.user_message(), name)

    def test_forbidden_codes_are_not_reported_as_wrong_platform(self):
        # Staging pass 2026-10-08: a 403 AGENT_NOT_AUTHORIZED (an agent id
        # Floatra does not know) fell through to the generic 403 copy, which
        # blamed the platform settings.
        for code, expected in (
            ("AGENT_NOT_AUTHORIZED", "agent ID"),
            ("PLATFORM_SUSPENDED", "suspended your platform"),
        ):
            body = {"success": False, "data": None, "error": "refused",
                    "errorCode": code}
            message = contract.FloatraAPIError(403, body).user_message()
            self.assertIn(expected, message, code)
            self.assertNotIn("does not belong to the platform", message, code)


class TestWebhookVerification(unittest.TestCase):
    def test_every_core_delivery_verifies_and_tampering_fails(self):
        for name in support.fixture_names("webhook-"):
            fx = fixture(name)
            ts = fx["headers"]["X-Floatra-Timestamp"]
            body = fx["body"].encode("utf-8")
            sig = fx["headers"]["X-Floatra-Signature"]
            contract.verify_webhook(fx["secret"], ts, body, sig, now=int(ts))
            with self.assertRaises(contract.WebhookRejected):
                contract.verify_webhook(fx["secret"], ts, body + b"x", sig, now=int(ts))
            with self.assertRaises(contract.WebhookRejected):
                contract.verify_webhook(fx["secret"], ts, body, sig, now=int(ts) + 301)

    def test_every_core_event_has_a_handler(self):
        for name in support.fixture_names("webhook-"):
            self.assertTrue(
                contract.interpret_webhook(_payload(name[len("webhook-"):])).handled, name,
            )


# ----- The app, on a stubbed frappe -----

CUSTOMER_FIELDS = {
    "floatra_merchant_id", "floatra_credit_status", "floatra_credit_limit",
    "floatra_credit_tier", "floatra_kyc_status", "floatra_kyc_url",
    "floatra_kyc_url_expires_at", "floatra_last_status_check",
}
SALES_ORDER_FIELDS = {
    "floatra_order_id", "floatra_loan_id", "floatra_loan_request_id", "floatra_decision",
    "floatra_decision_reason", "floatra_approved_amount", "floatra_interest_amount",
    "floatra_repayment_amount", "floatra_repayment_due_date", "floatra_disbursed",
    "floatra_delivery_confirmed",
}


class FakeDB:
    """Customer + Sales Order rows behind the frappe.db calls the app makes."""

    def __init__(self):
        self.rows = {
            "Customer": {"CUST-77": {
                "name": "CUST-77", "floatra_merchant_id": "mer_01",
                "floatra_credit_status": "pending_kyc",
                "floatra_kyc_url": "https://floatra.com/kyc/t",
            }},
            "Sales Order": {"SAL-ORD-2026-00042": {
                "name": "SAL-ORD-2026-00042", "customer": "CUST-77",
                "floatra_loan_id": None, "floatra_loan_request_id": None,
                "floatra_decision": "waitlisted", "floatra_disbursed": 0,
            }},
        }

    def _find(self, doctype, key):
        table = self.rows.get(doctype, {})
        if isinstance(key, dict):
            for row in table.values():
                if all(row.get(k) == v for k, v in key.items()):
                    return row
            return None
        return table.get(key)

    def get_value(self, doctype, key, field, as_dict=False):
        row = self._find(doctype, key)
        if row is None:
            return None
        return row.get(field)

    def exists(self, doctype, name):
        return self._find(doctype, name) is not None

    def set_value(self, doctype, name, values, value=None, update_modified=True):
        if not isinstance(values, dict):
            values = {values: value}
        self.rows[doctype][name].update(values)


def _meta(doctype):
    fields = CUSTOMER_FIELDS if doctype == "Customer" else SALES_ORDER_FIELDS
    return NS(fields=[NS(fieldname=f) for f in fields])


class _AppCase(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.comments = []
        frappe = api.frappe
        for target, name, value in (
            (frappe, "get_meta", _meta),
            (frappe.db, "get_value", self.db.get_value),
            (frappe.db, "exists", self.db.exists),
            (frappe.db, "set_value", self.db.set_value),
            (webhook, "_comment", lambda dt, name, text: self.comments.append((dt, name, text))),
        ):
            p = patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)

    @property
    def customer(self):
        return self.db.rows["Customer"]["CUST-77"]

    @property
    def order(self):
        return self.db.rows["Sales Order"]["SAL-ORD-2026-00042"]


class TestWebhookApply(_AppCase):
    def test_kyc_completed(self):
        touched = webhook.apply_webhook(_payload("merchant.kyc_completed"))
        self.assertEqual(touched["customer"], "CUST-77")
        self.assertEqual(self.customer["floatra_kyc_status"], "approved")
        self.assertEqual(self.customer["floatra_credit_status"], "available")
        self.assertIsNone(self.customer["floatra_kyc_url"])
        self.assertIsNone(self.customer["floatra_kyc_url_expires_at"])
        self.assertNotIn("floatra_liveness_verified", self.customer)  # no such field

    def test_lock_unlock_by_merchant_id(self):
        self.customer["floatra_credit_status"] = "available"
        webhook.apply_webhook(_payload("merchant.reorder_locked"))
        self.assertEqual(self.customer["floatra_credit_status"], "locked")
        webhook.apply_webhook(_payload("merchant.reorder_unlocked"))
        self.assertEqual(self.customer["floatra_credit_status"], "available")

    def test_external_id_needs_the_floatra_id_to_agree(self):
        # CUST-77 enrolled as another Floatra merchant: the name alone is
        # not enough (a sandbox merchant can share an ERP name).
        self.customer["floatra_merchant_id"] = "mer_other"
        touched = webhook.apply_webhook(_payload("merchant.reorder_locked"))
        self.assertIsNone(touched["customer"])
        self.assertEqual(self.customer["floatra_credit_status"], "pending_kyc")

    def test_order_name_collision_with_another_loan_is_ignored(self):
        self.order["floatra_loan_id"] = "loan_other"
        touched = webhook.apply_webhook(_payload("order.disbursed"))
        self.assertIsNone(touched["sales_order"])
        self.assertEqual(self.order["floatra_disbursed"], 0)

    def test_order_of_another_merchant_is_ignored(self):
        self.customer["floatra_merchant_id"] = "mer_other"
        webhook.apply_webhook(_payload("order.credit_declined"))
        self.assertEqual(self.order["floatra_decision"], "waitlisted")

    def test_waitlisted_order_is_found_by_loan_request(self):
        self.order["floatra_loan_request_id"] = "lr_01"
        payload = {**_payload("order.credit_approved"), "externalOrderId": "OTHER-NAME"}
        touched = webhook.apply_webhook(payload)
        self.assertEqual(touched["sales_order"], "SAL-ORD-2026-00042")
        self.assertEqual(self.order["floatra_loan_id"], "loan_01")

    def test_tier_changed(self):
        webhook.apply_webhook(_payload("merchant.credit_tier_changed"))
        self.assertEqual(self.customer["floatra_credit_tier"], "T1")
        self.assertEqual(self.customer["floatra_credit_limit"], Decimal("400000.00"))

    def test_approved_then_funded_by_loan_id(self):
        webhook.apply_webhook(_payload("order.credit_approved"))
        self.assertEqual(self.order["floatra_decision"], "approved")
        self.assertEqual(self.order["floatra_loan_id"], "loan_01")
        self.assertEqual(self.order["floatra_interest_amount"], Decimal("12500.00"))
        touched = webhook.apply_webhook(_payload("order.funded"))  # loan id only
        self.assertEqual(touched["sales_order"], "SAL-ORD-2026-00042")
        self.assertIs(self.order["floatra_disbursed"], True)
        self.assertEqual(self.comments[-1][:2], ("Sales Order", "SAL-ORD-2026-00042"))

    def test_disbursed_by_order_reference(self):
        webhook.apply_webhook(_payload("order.disbursed"))
        self.assertIs(self.order["floatra_disbursed"], True)
        self.assertEqual(self.order["floatra_loan_id"], "loan_01")

    def test_declined_and_bulk(self):
        webhook.apply_webhook(_payload("order.credit_declined"))
        self.assertEqual(self.order["floatra_decision"], "declined")
        webhook.apply_webhook(_payload("bulk.order_decision"))
        self.assertEqual(self.order["floatra_decision"], "approved")
        self.assertEqual(self.order["floatra_repayment_due_date"], "2026-10-15")

    def test_note_only_events_comment_on_the_order(self):
        self.order["floatra_loan_id"] = "loan_01"
        for event in ("order.overdue", "order.repayment_due", "order.repayment_received",
                      "order.defaulted", "order.disbursement_failed"):
            webhook.apply_webhook(_payload(event))
        self.assertEqual(len(self.comments), 5)

    def test_unknown_event_touches_nothing(self):
        self.assertEqual(
            webhook.apply_webhook({"event": "lender.paused"}),
            {"customer": None, "sales_order": None},
        )


class _Thrown(Exception):
    pass


class TestDeskActions(_AppCase):
    def setUp(self):
        super().setUp()
        self.client = MagicMock()
        for target, name, value in (
            (api.FloatraAPIClient, "for_site", MagicMock(return_value=self.client)),
            (api.frappe, "throw", MagicMock(side_effect=lambda m, **k: (_ for _ in ()).throw(_Thrown(m)))),
        ):
            p = patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)

    def _doc(self, **values):
        """A document with exactly these fields (absent ones read None)."""

        class Doc:
            def __init__(self, data):
                self.__dict__.update(data)

            def __getattr__(self, name):  # only for absent fields
                return None

            def get(self, name, default=None):
                return self.__dict__.get(name, default)

        doc = Doc(values)
        doc.check_permission = MagicMock()
        doc.add_comment = MagicMock()
        return doc

    def _so(self, **values):
        return self._doc(name="SAL-ORD-2026-00042", **values)

    def test_repayment_posts_to_the_loan_with_a_decimal_string(self):
        self.client.record_repayment.return_value = contract.parse_repayment(
            fixture("response-repayment")["body"],
        )
        so = self._so(floatra_disbursed=1, floatra_loan_id="loan_01")
        with patch.object(api.frappe, "get_doc", return_value=so):
            out = api.record_cash_repayment(so.name, 100000, "RCPT-0042", "BANK_TRANSFER")
        loan_id, body = self.client.record_repayment.call_args[0]
        self.assertEqual(loan_id, "loan_01")
        self.assertEqual(body["amount"], "100000.00")
        self.assertEqual(out["outstanding_balance"], "162500.00")

    def test_pre_upgrade_order_looks_up_its_loan(self):
        self.client.lookup_order.return_value = contract.parse_order_lookup(
            fixture("response-order-lookup")["body"],
        )
        self.client.confirm_delivery.return_value = contract.parse_delivery(
            fixture("response-confirm-delivery")["body"],
        )
        so = self._so(floatra_disbursed=1, floatra_loan_id=None)
        with patch.object(api.frappe, "get_doc", return_value=so):
            api.confirm_delivery_for_sales_order(so.name)
        self.client.lookup_order.assert_called_once_with("SAL-ORD-2026-00042")
        self.assertEqual(self.client.confirm_delivery.call_args[0][0], "loan_01")
        self.assertEqual(self.order["floatra_loan_id"], "loan_01")
        self.assertEqual(self.order["floatra_delivery_confirmed"], 1)

    def test_return_refusal_is_a_clear_message(self):
        fx = fixture("response-return-error-no-dispute")
        self.client.initiate_return.side_effect = contract.FloatraAPIError(
            fx["status"], fx["body"],
        )
        so = self._so(floatra_disbursed=1, floatra_loan_id="loan_01")
        with patch.object(api.frappe, "get_doc", return_value=so):
            with self.assertRaises(_Thrown) as ctx:
                api.action_initiate_return(so.name, "50000", "DAMAGED")
        self.assertIn("delivery dispute", ctx.exception.args[0])

    def test_request_credit_writes_the_decision(self):
        self.client.initiate_order.return_value = contract.parse_decision(
            fixture("response-initiate-approved")["body"],
        )
        so = self._so(docstatus=0, customer="CUST-77", grand_total=250000.0, items=[])
        customer = self._doc(
            name="CUST-77",
            floatra_merchant_id="mer_01",
            floatra_credit_status="available",
            floatra_last_status_check=datetime.utcnow(),
        )
        docs = {"Sales Order": so, "Customer": customer}
        with patch.object(api.frappe, "get_doc", side_effect=lambda dt, name: docs[dt]):
            out = api.request_credit(so.name)
        body = self.client.initiate_order.call_args[0][0]
        self.assertEqual(body["amount"], "250000.00")
        self.assertEqual(body["metadata"]["erp_type"], "ERPNEXT")
        self.assertEqual(out["decision"], "APPROVED")
        self.assertEqual(self.order["floatra_loan_id"], "loan_01")
        self.assertEqual(self.order["floatra_repayment_amount"], Decimal("262500.00"))

    def test_on_cancel_releases_an_unfunded_approval(self):
        doc = NS(name="SAL-ORD-2026-00042", floatra_decision="approved",
                 floatra_disbursed=0, floatra_loan_id="loan_01")
        so_controller.on_cancel(doc)
        loan_id, body = self.client.cancel_order.call_args[0][:2]
        self.assertEqual(loan_id, "loan_01")
        self.assertEqual(body["cancelled_by"], "DISTRIBUTOR")
        self.assertEqual(body["reason_code"], "OTHER")


class FakeLog:
    """``Floatra Webhook Log`` rows keyed by event id."""

    rows = {}

    def __init__(self, data):
        self.__dict__.update(data)

    def insert(self, ignore_permissions=False):
        FakeLog.rows[self.event_id] = self

    def save(self, ignore_permissions=False):
        FakeLog.rows[self.event_id] = self

    def reload(self):
        pass


class TestProcessEvent(_AppCase):
    def setUp(self):
        super().setUp()
        FakeLog.rows = {}
        self.order["floatra_loan_id"] = "loan_01"
        self.live = True
        frappe = api.frappe

        def get_doc(arg, name=None):
            if isinstance(arg, dict):
                return FakeLog(arg)
            return FakeLog.rows[name]

        def log_value(doctype, key, field, *a, **k):
            if doctype == "Floatra Webhook Log":
                row = FakeLog.rows.get(key)
                return getattr(row, field, None) if row else None
            return self.db.get_value(doctype, key, field)

        def log_exists(doctype, key):
            if doctype == "Floatra Webhook Log":
                if isinstance(key, dict):
                    return any(
                        all(getattr(r, f, None) == v for f, v in key.items())
                        for r in FakeLog.rows.values()
                    )
                return key in FakeLog.rows
            return self.db.exists(doctype, key)

        for target, name, value in (
            (frappe, "get_doc", get_doc),
            (frappe.db, "get_value", log_value),
            (frappe.db, "exists", log_exists),
            (webhook, "_key_is_live", lambda: self.live),
        ):
            p = patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)

    def test_sandbox_event_is_ignored_by_a_live_key(self):
        self.customer["floatra_credit_status"] = "locked"
        for event in ("merchant.reorder_unlocked", "order.funded"):
            out = webhook.process_event("dlv_" + event, {**_payload(event), "livemode": False}, "s-" + event)
            self.assertEqual(out, "ignored", event)
        self.assertEqual(self.customer["floatra_credit_status"], "locked")
        self.assertEqual(self.order["floatra_disbursed"], 0)
        self.assertEqual(FakeLog.rows["dlv_order.funded"].status, "ignored")

    def test_missing_livemode_is_ignored_live_and_applied_sandbox(self):
        payload = _payload("order.funded")
        del payload["livemode"]
        self.assertEqual(webhook.process_event("dlv_1", payload, "s1"), "ignored")
        self.live = False
        self.assertEqual(webhook.process_event("dlv_2", payload, "s2"), "ok")
        self.assertIs(self.order["floatra_disbursed"], True)

    def test_a_failed_delivery_is_retried_and_applied(self):
        real = webhook.apply_webhook
        calls = {"n": 0}

        def flaky(payload):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("database hiccup")
            return real(payload)

        with patch.object(webhook, "apply_webhook", side_effect=flaky):
            self.assertEqual(webhook.process_event("dlv_1", _payload("order.funded"), "sig-1"), "failed")
            self.assertEqual(FakeLog.rows["dlv_1"].status, "failed")
            self.assertEqual(webhook.process_event("dlv_1", _payload("order.funded"), "sig-2"), "ok")
        self.assertIs(self.order["floatra_disbursed"], True)
        self.assertEqual(FakeLog.rows["dlv_1"].status, "processed")
        self.assertEqual(webhook.process_event("dlv_1", _payload("order.funded"), "sig-3"), "duplicate")

    def test_same_signature_under_a_new_event_id_is_ignored(self):
        self.assertEqual(webhook.process_event("dlv_a", _payload("order.funded"), "sig-x"), "ok")
        self.order["floatra_disbursed"] = 0
        self.assertEqual(webhook.process_event("dlv_b", _payload("order.funded"), "sig-x"), "duplicate")
        self.assertEqual(self.order["floatra_disbursed"], 0)
        self.assertNotIn("dlv_b", FakeLog.rows)


class TestReplayTask(unittest.TestCase):
    def test_acknowledges_only_settled_events(self):
        from .. import tasks

        client = MagicMock()
        client.get_undelivered_webhooks.return_value = {
            "events": [
                {"event_id": e, "event_type": "order.funded", "payload": {}}
                for e in ("ok", "dup", "ign", "bad")
            ],
            "has_more": False,
        }
        outcomes = {"ok": "ok", "dup": "duplicate", "ign": "ignored", "bad": "failed"}
        with patch.object(tasks.FloatraAPIClient, "for_site", return_value=client), \
                patch.object(webhook, "process_event", side_effect=lambda eid, p: outcomes[eid]):
            replayed = tasks.poll_undelivered_webhooks()
        acked = [c[0][0] for c in client.acknowledge_webhook.call_args_list]
        self.assertEqual(acked, ["ok", "dup", "ign"])
        self.assertEqual(replayed, 2)


class TestCancelDraftCredit(TestDeskActions):
    def test_action_cancels_an_approved_unfunded_draft(self):
        so = self._so(docstatus=0, floatra_decision="approved", floatra_disbursed=0,
                      floatra_loan_id="loan_01")
        with patch.object(api.frappe, "get_doc", return_value=so):
            self.assertEqual(api.action_cancel_floatra_credit(so.name), {"status": "cancelled"})
        loan_id, body = self.client.cancel_order.call_args[0][:2]
        self.assertEqual((loan_id, body["cancelled_by"]), ("loan_01", "DISTRIBUTOR"))
        self.assertEqual(self.order["floatra_decision"], "not_requested")

    def test_action_refuses_a_funded_order(self):
        so = self._so(floatra_decision="approved", floatra_disbursed=1, floatra_loan_id="loan_01")
        with patch.object(api.frappe, "get_doc", return_value=so):
            with self.assertRaises(_Thrown):
                api.action_cancel_floatra_credit(so.name)
        self.client.cancel_order.assert_not_called()

    def test_deleting_an_approved_draft_releases_the_reservation(self):
        doc = NS(name="SAL-ORD-2026-00042", floatra_decision="approved",
                 floatra_disbursed=0, floatra_loan_id="loan_01")
        so_controller.on_trash(doc)
        self.assertEqual(self.client.cancel_order.call_args[0][0], "loan_01")

    def test_a_refused_release_aborts_the_delete(self):
        # Floatra funded it but the webhook has not landed: the cancel is
        # refused and the Sales Order (the only record of the loan id) stays.
        self.client.cancel_order.side_effect = contract.FloatraAPIError(
            409, {"success": False, "errorCode": "CANCELLATION_NOT_ALLOWED",
                  "error": "Loan already funded"},
        )
        doc = NS(name="SAL-ORD-2026-00042", floatra_decision="approved",
                 floatra_disbursed=0, floatra_loan_id="loan_01")
        with patch.object(so_controller.frappe, "throw",
                          side_effect=lambda m, **k: (_ for _ in ()).throw(_Thrown(m))):
            with self.assertRaises(_Thrown) as ctx:
                so_controller.on_trash(doc)
        self.assertIn("cannot cancel this loan", ctx.exception.args[0])

    def test_a_network_failure_also_aborts_the_delete(self):
        self.client.cancel_order.side_effect = contract.FloatraAPIError(0, None, "down")
        doc = NS(name="SO-1", floatra_decision="approved", floatra_disbursed=0,
                 floatra_loan_id="loan_01")
        with patch.object(so_controller.frappe, "throw", side_effect=_Thrown):
            with self.assertRaises(_Thrown):
                so_controller.on_trash(doc)

    def test_nothing_to_release_lets_the_delete_through(self):
        fx = fixture("response-order-lookup-error-not-found")
        self.client.lookup_order.side_effect = contract.FloatraAPIError(
            fx["status"], fx["body"],
        )
        doc = NS(name="SO-1", floatra_decision="approved", floatra_disbursed=0,
                 floatra_loan_id=None)
        with patch.object(so_controller.frappe, "throw", side_effect=_Thrown):
            so_controller.on_trash(doc)  # no raise
        self.client.cancel_order.assert_not_called()
        self.client.lookup_order.side_effect = None
        self.client.lookup_order.return_value = {"floatra_loan_id": None}
        so_controller.on_trash(doc)  # waitlisted: no loan either

    def test_deleting_an_unapproved_draft_calls_nothing(self):
        so_controller.on_trash(NS(name="X", floatra_decision="declined", floatra_disbursed=0))
        self.client.cancel_order.assert_not_called()

    def test_deleting_a_funded_order_is_refused(self):
        with patch.object(so_controller.frappe, "throw", side_effect=_Thrown):
            with self.assertRaises(_Thrown):
                so_controller.on_trash(NS(name="X", floatra_decision="approved",
                                          floatra_disbursed=1, floatra_loan_id="loan_01"))
        self.client.cancel_order.assert_not_called()

    def test_before_submit_message_points_to_the_cancel_action(self):
        with patch.object(so_controller.frappe, "throw", side_effect=lambda m, **k: (_ for _ in ()).throw(_Thrown(m))):
            with self.assertRaises(_Thrown) as ctx:
                so_controller.before_submit(NS(floatra_decision="approved", floatra_disbursed=0))
        self.assertIn("Cancel Floatra Credit", ctx.exception.args[0])


class TestReconcileFunded(TestDeskActions):
    def test_a_funded_loan_not_marked_locally_is_drift(self):
        self.order["floatra_loan_id"] = "loan_01"
        self.client.list_orders.return_value = contract.parse_orders_page({
            "data": [{"floatra_order_id": "loan_01", "status": "FUNDED",
                      "floatra_merchant_id": "mer_01", "created_at": "x"}],
            "next_cursor": None, "has_more": False,
        })
        with patch.object(api.frappe, "has_permission", MagicMock()):
            out = api.action_reconcile_orders()
        self.assertEqual(out["status_drift"][0]["floatra_status"], "FUNDED")
        self.assertEqual(out["synced"], 0)


class TestSiteTimezone(TestDeskActions):
    def test_unparseable_desk_time_is_a_user_message(self):
        so = self._so(floatra_disbursed=1, floatra_loan_id="loan_01")
        with patch.object(api.frappe, "get_doc", return_value=so):
            with self.assertRaises(_Thrown) as ctx:
                api.record_cash_repayment(so.name, 1000, "R-1", "MANUAL_CASH",
                                          collected_at="yesterday-ish")
            self.assertIn("Not a date-time", ctx.exception.args[0])
            with self.assertRaises(_Thrown) as ctx:
                api.confirm_delivery_for_sales_order(so.name, delivered_at="31/13/2026")
            self.assertIn("Not a date-time", ctx.exception.args[0])
        self.client.record_repayment.assert_not_called()
        self.client.confirm_delivery.assert_not_called()

    def test_bad_location_time_on_the_order_is_a_user_message(self):
        so = self._so(floatra_disbursed=1, floatra_loan_id="loan_01",
                      floatra_delivery_location_at="not a time")
        with patch.object(api.frappe, "get_doc", return_value=so):
            with self.assertRaises(_Thrown) as ctx:
                api.confirm_delivery_for_sales_order(so.name)
        self.assertIn("Not a date-time", ctx.exception.args[0])

    def test_system_to_utc_lagos(self):
        utc = floatra_api.system_to_utc("2026-10-01 12:30:00", "Africa/Lagos")
        self.assertEqual(utc, datetime(2026, 10, 1, 11, 30))
        self.assertEqual(contract.iso_utc(utc), "2026-10-01T11:30:00+00:00")
        self.assertIsNone(floatra_api.system_to_utc(None, "Africa/Lagos"))

    def test_desk_times_are_sent_as_utc(self):
        self.client.record_repayment.return_value = contract.parse_repayment(
            fixture("response-repayment")["body"],
        )
        self.client.confirm_delivery.return_value = contract.parse_delivery(
            fixture("response-confirm-delivery")["body"],
        )
        so = self._so(floatra_disbursed=1, floatra_loan_id="loan_01",
                      floatra_delivery_location_at="2026-10-01 12:29:00")
        now_lagos = (datetime.utcnow() + timedelta(hours=1)).replace(microsecond=0)
        with patch.object(api.frappe, "get_doc", return_value=so):
            api.record_cash_repayment(so.name, 1000, "R-1", "MANUAL_CASH",
                                      collected_at=now_lagos.strftime("%Y-%m-%d %H:%M:%S"))
            # A delivery stamped "now" on the Lagos desk is not "in the future".
            api.confirm_delivery_for_sales_order(
                so.name, delivered_at=now_lagos.strftime("%Y-%m-%d %H:%M:%S"),
            )
        expected = contract.iso_utc(now_lagos - timedelta(hours=1))
        self.assertEqual(self.client.record_repayment.call_args[0][1]["collected_at"], expected)
        body = self.client.confirm_delivery.call_args[0][1]
        self.assertEqual(body["delivered_at"], expected)
        self.assertEqual(body["location"]["recorded_at"], "2026-10-01T11:29:00+00:00")


class TestVersionHistoryCleanup(unittest.TestCase):
    def test_strips_only_identity_number_changes(self):
        from ..patches.v0_3_0_partner_contract import strip_pii_from_version_data

        data = json.dumps({
            "changed": [["floatra_bvn", None, "22212345678"],
                        ["customer_name", "A", "B"],
                        ["floatra_nin", "1", "2"]],
            "added": [], "removed": [],
        })
        cleaned = json.loads(strip_pii_from_version_data(data))
        self.assertEqual(cleaned["changed"], [["customer_name", "A", "B"]])
        self.assertNotIn("22212345678", json.dumps(cleaned))
        # Idempotent: nothing left to strip.
        self.assertIsNone(strip_pii_from_version_data(json.dumps(cleaned)))
        self.assertIsNone(strip_pii_from_version_data("not json"))


class TestOnboardEmailAndBankKey(unittest.TestCase):
    def test_single_email_agrees_with_core_is_email(self):
        for case in fixture("email-cases"):
            got = contract.single_email(case["email"]) is not None
            self.assertEqual(got, case["isEmail"], case["email"])

    def test_owner_email_only_when_a_single_valid_address(self):
        base = dict(name="CUST-1", customer_name="Shop", mobile_no="08031234567")
        for email, expected in (("shop@example.com", "shop@example.com"),
                                ("a@x.ng, b@x.ng", None), ("nope", None)):
            body = floatra_api.onboard_body_for_customer(NS(**base, email_id=email))
            self.assertEqual(body.get("owner", {}).get("email"), expected, email)

    def test_bank_key_never_carries_the_account_number(self):
        body = contract.bank_account_body(
            bank_code="058", account_number="0123456789", account_name="N O",
        )
        with patch.object(floatra_client.requests, "request",
                          return_value=_response(fixture("response-bank-account"))) as req:
            _client().set_bank_account("mer_01", body)
        self.assertNotIn("0123456789", req.call_args[1]["headers"]["Idempotency-Key"])


if __name__ == "__main__":
    unittest.main()
