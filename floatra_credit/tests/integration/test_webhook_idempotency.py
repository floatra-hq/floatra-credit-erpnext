# -*- coding: utf-8 -*-
"""Frappe-runtime test: the inbound-webhook idempotency guard.

Two webhook deliveries with the same `event_id` must NOT double-
process. The first inserts a `Floatra Webhook Log` row + dispatches
to the handler; the second short-circuits via the `frappe.db.exists`
check in `controllers/webhook.py::receive`.

We exercise the receiver in three ways:
  1. Direct `receive()` call with a request signed exactly as core
     signs (Unix-seconds timestamp, HMAC over "<ts>.<body>")
  2. DB-level UNIQUE constraint — even without the application
     check, the database refuses dupes (defence-in-depth)
  3. Signature / timestamp rejections answer 401 and log nothing
"""

import json
import time
import unittest

import frappe

from ...controllers import webhook as webhook_module
from ...services.floatra_contract import sign_webhook

SECRET = "test-secret-do-not-use"


class TestWebhookIdempotency(unittest.TestCase):
    def setUp(self):
        config = frappe.get_single("Floatra Platform Config")
        config.floatra_gateway_url = "https://api.floatra.test"
        config.platform_id = "test-platform"
        config.api_key = "test-api-key-do-not-use"
        config.webhook_secret = SECRET
        config.save(ignore_permissions=True)
        frappe.local.response = frappe._dict()

    def _request(self, event_id, body: bytes, ts=None, signature=None):
        from werkzeug.test import EnvironBuilder
        from werkzeug.wrappers import Request

        ts = ts or str(int(time.time()))
        env = EnvironBuilder(
            method="POST",
            path="/api/method/floatra_credit.controllers.webhook.receive",
            data=body,
            content_type="application/json",
            headers={
                "X-Floatra-Signature": signature or sign_webhook(SECRET, ts, body),
                "X-Floatra-Event-ID": event_id,
                "X-Floatra-Timestamp": ts,
            },
        ).get_environ()
        return Request(env)

    def _body(self, days_remaining=14):
        return json.dumps({
            "event": "platform.api_key_expiring",
            "platformId": "plat-1",
            "credentialMode": "live",
            "expiresAt": "2026-10-15T00:00:00.000Z",
            "daysRemaining": days_remaining,
            "timestamp": "2026-10-01T12:00:00.000Z",
            "version": "1",
        }).encode("utf-8")

    def test_duplicate_event_id_short_circuits(self):
        body = self._body()
        frappe.local.request = self._request("evt-idempotent-1", body)
        self.assertEqual(webhook_module.receive().get("status"), "ok")
        frappe.local.request = self._request("evt-idempotent-1", body)
        self.assertEqual(webhook_module.receive().get("status"), "duplicate")
        rows = frappe.get_all(
            "Floatra Webhook Log", filters={"event_id": "evt-idempotent-1"},
        )
        self.assertEqual(len(rows), 1)

    def test_same_signature_under_a_new_event_id_is_ignored(self):
        # A body no other test sends: the same body signed in the same
        # second as an earlier test's delivery is already a replay.
        body = self._body(days_remaining=13)
        ts = str(int(time.time()))
        sig = sign_webhook(SECRET, ts, body)
        frappe.local.request = self._request("evt-sig-1", body, ts=ts, signature=sig)
        self.assertEqual(webhook_module.receive().get("status"), "ok")
        frappe.local.request = self._request("evt-sig-2", body, ts=ts, signature=sig)
        self.assertEqual(webhook_module.receive().get("status"), "duplicate")
        self.assertFalse(frappe.db.exists("Floatra Webhook Log", "evt-sig-2"))

    def test_db_uniqueness_rejects_direct_insert_dupe(self):
        frappe.get_doc({
            "doctype": "Floatra Webhook Log",
            "event_id": "evt-db-unique-1",
            "event_type": "order.test",
            "payload": "{}",
        }).insert(ignore_permissions=True)
        with self.assertRaises(frappe.DuplicateEntryError):
            frappe.get_doc({
                "doctype": "Floatra Webhook Log",
                "event_id": "evt-db-unique-1",
                "event_type": "order.test",
                "payload": "{}",
            }).insert(ignore_permissions=True)

    def _assert_rejected(self, event_id, reason, **kwargs):
        frappe.local.request = self._request(event_id, self._body(), **kwargs)
        result = webhook_module.receive()
        self.assertEqual(result, {"status": "rejected", "reason": reason})
        self.assertEqual(frappe.local.response.get("http_status_code"), 401)
        self.assertFalse(frappe.get_all(
            "Floatra Webhook Log", filters={"event_id": event_id},
        ))

    def test_invalid_signature_is_401(self):
        self._assert_rejected("evt-bad-sig-1", "invalid_signature", signature="0" * 64)

    def test_stale_timestamp_is_401(self):
        self._assert_rejected(
            "evt-stale-1", "timestamp_outside_window", ts=str(int(time.time()) - 3600),
        )

    def test_iso_timestamp_is_401(self):
        self._assert_rejected(
            "evt-iso-1", "invalid_timestamp", ts="2026-10-01T12:00:00+00:00",
        )
