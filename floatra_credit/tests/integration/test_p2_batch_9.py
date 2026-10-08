# -*- coding: utf-8 -*-
"""P2-13 batch: ERPNext operational endpoints catchup.

Two surfaces under test:

  1. `tasks.poll_undelivered_webhooks` — pulls the gateway's
     undelivered queue, replays each event through the live receiver's
     `process_event`, ACKs the gateway on success. Mirrors the Odoo `floatra_webhook_log._floatra_cron_poll_undelivered_webhooks`
     contract + the generic-adapter `UndeliveredWebhookPoller`.

  2. `api.action_get_kyc_link` — whitelisted endpoint that gets a
     Floatra hosted KYC link for a Customer (no identity data sent).
     Same invariant set as the Odoo `action_floatra_get_kyc_link`.

Both run inside the Frappe runtime — the API client is mocked at
the `for_site` boundary so we don't hit a real gateway.
"""

import unittest
from unittest.mock import MagicMock, patch

import frappe

from ...services.floatra_api import FloatraAPIClient, FloatraAPIError
from ... import tasks


class TestPollUndeliveredWebhooks(unittest.TestCase):
    """The cron task: walks /v1/partner/webhooks/undelivered, replays each
    event, ACKs the gateway.
    """

    def setUp(self):
        # Make sure the receiver has a config to read at import time.
        config = frappe.get_single("Floatra Platform Config")
        config.floatra_gateway_url = "https://api.floatra.test"
        config.platform_id = "test-platform"
        config.api_key = "test-api-key-do-not-use"
        config.webhook_secret = "test-secret-do-not-use"
        config.save(ignore_permissions=True)

        # Purge log rows from previous runs.
        frappe.db.delete(
            "Floatra Webhook Log",
            {"event_id": ["like", "p2b9-evt-%"]},
        )
        frappe.db.commit()

    def _patch_client(self, **method_returns):
        fake = MagicMock(spec=FloatraAPIClient)
        for method, value in method_returns.items():
            getattr(fake, method).return_value = value
        return patch.object(
            FloatraAPIClient, "for_site", return_value=fake,
        )

    def _event(self, event_id: str, event_type: str = "unknown.event") -> dict:
        # The shape `contract.parse_undelivered` hands the task.
        return {
            "event_id": event_id,
            "event_type": event_type,
            "payload": {"event": event_type, "timestamp": "2026-10-01T12:00:00.000Z"},
        }

    def _page(self, events, has_more=False) -> dict:
        return {"events": events, "has_more": has_more}

    def test_replays_one_event_and_acks_on_success(self):
        with self._patch_client(
            get_undelivered_webhooks=self._page([self._event("p2b9-evt-1")]),
            acknowledge_webhook={"status": "ACKNOWLEDGED"},
        ) as patched_for_site:
            replayed = tasks.poll_undelivered_webhooks()
            self.assertEqual(replayed, 1)
            client = patched_for_site.return_value
            client.acknowledge_webhook.assert_called_once_with("p2b9-evt-1")

        # Log row persisted as `processed`.
        log = frappe.get_doc("Floatra Webhook Log", "p2b9-evt-1")
        self.assertEqual(log.status, "processed")

    def test_dedups_against_existing_log_row(self):
        # Pre-create a log row for an event_id; poll should skip
        # the handler call entirely but still ACK the gateway.
        frappe.get_doc({
            "doctype": "Floatra Webhook Log",
            "event_id": "p2b9-evt-dup",
            "event_type": "merchant.reorder_locked",
            "status": "processed",
            "signature_verified": 1,
            "platform_id": "test-platform",
            "payload": "{}",
        }).insert(ignore_permissions=True)
        frappe.db.commit()

        with self._patch_client(
            get_undelivered_webhooks=self._page([self._event("p2b9-evt-dup")]),
            acknowledge_webhook={"status": "ALREADY_ACKNOWLEDGED"},
        ) as patched_for_site:
            replayed = tasks.poll_undelivered_webhooks()
            # Dedup short-circuits — not counted as a "replay" because
            # the live receiver already did the work; only the ACK
            # ran. replayed == 0 is correct.
            self.assertEqual(replayed, 0)
            patched_for_site.return_value.acknowledge_webhook.assert_called_once_with(
                "p2b9-evt-dup",
            )

    def test_walks_pagination_until_has_more_false(self):
        page_1 = self._page(
            [self._event("p2b9-evt-a"), self._event("p2b9-evt-b")], has_more=True,
        )
        page_2 = self._page([self._event("p2b9-evt-c")])
        pages = [page_1, page_2]
        fake = MagicMock(spec=FloatraAPIClient)
        fake.get_undelivered_webhooks.side_effect = lambda **_: (
            pages.pop(0) if pages else self._page([])
        )
        fake.acknowledge_webhook.return_value = {"status": "ACKNOWLEDGED"}

        with patch.object(
            FloatraAPIClient, "for_site", return_value=fake,
        ):
            replayed = tasks.poll_undelivered_webhooks()
            self.assertEqual(replayed, 3)
            self.assertEqual(fake.get_undelivered_webhooks.call_count, 2)
            self.assertEqual(fake.acknowledge_webhook.call_count, 3)

    def test_no_op_when_envelope_empty(self):
        with self._patch_client(
            get_undelivered_webhooks=self._page([]),
        ) as patched_for_site:
            replayed = tasks.poll_undelivered_webhooks()
            self.assertEqual(replayed, 0)
            patched_for_site.return_value.acknowledge_webhook.assert_not_called()

    def test_ack_404_treated_as_benign(self):
        fake = MagicMock(spec=FloatraAPIClient)
        fake.get_undelivered_webhooks.return_value = self._page(
            [self._event("p2b9-evt-ack404")],
        )
        fake.acknowledge_webhook.side_effect = FloatraAPIError(
            404, {"error": "EVENT_NOT_FOUND"},
        )
        with patch.object(
            FloatraAPIClient, "for_site", return_value=fake,
        ):
            replayed = tasks.poll_undelivered_webhooks()
            # Replay succeeded; ACK 404 is benign → still counted.
            self.assertEqual(replayed, 1)


CUSTOMER_GROUP_FIXTURE = "Floatra P2-9 Test Group"
TERRITORY_FIXTURE = "Floatra P2-9 Test Territory"


def _ensure_customer_dependencies():
    """ERPNext Customer creation requires a non-group Customer Group +
    Territory. The default "All Customer Groups" / "All Territories"
    are themselves group entries and refuse selection.
    """
    if not frappe.db.exists("Customer Group", CUSTOMER_GROUP_FIXTURE):
        frappe.get_doc({
            "doctype": "Customer Group",
            "customer_group_name": CUSTOMER_GROUP_FIXTURE,
            "parent_customer_group": "All Customer Groups",
            "is_group": 0,
        }).insert(ignore_permissions=True)
    if not frappe.db.exists("Territory", TERRITORY_FIXTURE):
        frappe.get_doc({
            "doctype": "Territory",
            "territory_name": TERRITORY_FIXTURE,
            "parent_territory": "All Territories",
            "is_group": 0,
        }).insert(ignore_permissions=True)


class TestActionGetKycLink(unittest.TestCase):
    """The whitelisted Get KYC Link endpoint (hosted KYC link)."""

    def setUp(self):
        # Create a fresh Customer for each test to keep state isolated.
        _ensure_customer_dependencies()
        self.customer_name = (
            "P2-batch9 Test Customer " + frappe.generate_hash(length=6)
        )
        self.customer = frappe.get_doc({
            "doctype": "Customer",
            "customer_name": self.customer_name,
            "customer_type": "Company",
            "customer_group": CUSTOMER_GROUP_FIXTURE,
            "territory": TERRITORY_FIXTURE,
        }).insert(ignore_permissions=True)
        frappe.db.commit()

    def tearDown(self):
        try:
            frappe.delete_doc(
                "Customer", self.customer.name, force=True,
                ignore_permissions=True,
            )
            frappe.db.commit()
        except Exception:
            pass

    def _patch_client(self, **method_returns):
        fake = MagicMock(spec=FloatraAPIClient)
        for method, value in method_returns.items():
            getattr(fake, method).return_value = value
        return patch.object(
            FloatraAPIClient, "for_site", return_value=fake,
        )

    def test_refuses_unenrolled_customer(self):
        # No floatra_merchant_id → nothing to verify on Floatra.
        from ... import api
        with self.assertRaises(frappe.ValidationError):
            api.action_get_kyc_link(self.customer.name)

    def _hosted_link(self):
        from datetime import datetime, timezone

        from ...services.kyc_link import HOSTED_LINK, KycRetryOutcome
        return KycRetryOutcome(
            kind=HOSTED_LINK,
            kyc_url="https://floatra.com/kyc/tok",
            expires_at=datetime(2099, 1, 2, 12, 0, tzinfo=timezone.utc),
        )

    def test_happy_path_stores_the_link_without_identity_data(self):
        from ... import api
        self.customer.db_set("floatra_merchant_id", "FLT-M-X-003")
        self.customer.db_set("floatra_kyc_status", "rejected")
        self.customer.db_set("floatra_credit_status", "pending_kyc")

        with self._patch_client(
            request_kyc_link=self._hosted_link(),
        ) as patched:
            out = api.action_get_kyc_link(self.customer.name)
            patched.return_value.request_kyc_link.assert_called_once_with(
                "FLT-M-X-003",
            )

        self.assertEqual(out["kind"], "hosted_link")
        self.customer.reload()
        self.assertEqual(
            self.customer.floatra_kyc_url, "https://floatra.com/kyc/tok",
        )
        self.assertTrue(self.customer.floatra_kyc_url_expires_at)
        # Issuing a link changes no KYC / credit state.
        self.assertEqual(self.customer.floatra_kyc_status, "rejected")
        self.assertEqual(self.customer.floatra_credit_status, "pending_kyc")

    def test_works_for_any_enrolled_customer(self):
        from ... import api
        self.customer.db_set("floatra_merchant_id", "FLT-M-X-004")
        with self._patch_client(request_kyc_link=self._hosted_link()):
            api.action_get_kyc_link(self.customer.name)
        self.customer.reload()
        self.assertTrue(self.customer.floatra_kyc_url)

    def test_already_verified_is_a_clean_message(self):
        from ... import api
        self.customer.db_set("floatra_merchant_id", "FLT-M-X-005")
        with self._patch_client() as patched:
            patched.return_value.request_kyc_link.side_effect = (
                FloatraAPIError(409, {"errorCode": "KYC_ALREADY_VERIFIED"})
            )
            with self.assertRaises(frappe.ValidationError) as ctx:
                api.action_get_kyc_link(self.customer.name)
        self.assertIn("already verified", str(ctx.exception))
