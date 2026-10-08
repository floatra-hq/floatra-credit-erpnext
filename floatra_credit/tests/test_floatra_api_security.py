# -*- coding: utf-8 -*-
"""VULN-002 + minor: FloatraAPIClient must not log raw PII on error
responses (spec §14), and must URL-encode query params.

`frappe` is stubbed so the client imports without a bench.
"""

import sys
import types
import unittest
from unittest.mock import MagicMock, patch


def _install_frappe_stub() -> None:
    if "frappe" not in sys.modules:
        frappe = types.ModuleType("frappe")
        frappe.get_single = MagicMock()
        frappe._ = lambda s: s
        frappe.db = types.SimpleNamespace()
        sys.modules["frappe"] = frappe


from . import frappe_stub  # noqa: E402

frappe_stub.install()
if not frappe_stub.under_bench():
    _install_frappe_stub()
load_tests = frappe_stub.load_tests

from ..services import floatra_client  # noqa: E402
from ..services.floatra_api import FloatraAPIClient, FloatraAPIError  # noqa: E402


def _client() -> FloatraAPIClient:
    # Construct directly (bypass for_site) so no Frappe singleton lookup.
    return FloatraAPIClient(
        base_url="https://gateway.example",
        api_key="sk_test_dummy",
        platform_id="plat_1",
    )


class TestErrorLogRedaction(unittest.TestCase):
    def test_error_response_body_is_redacted_in_logs(self):
        resp = MagicMock()
        resp.ok = False
        resp.status_code = 400
        resp.json.return_value = {
            "error": "validation_failed",
            "bvn": "22212345678",
            "account_number": "0123456789",
            "phone": "+2348012345678",
        }

        with self.assertLogs(floatra_client._logger.name, level="WARNING") as cm:
            with self.assertRaises(FloatraAPIError):
                _client()._handle_response(resp, "POST", "/v1/partner/merchants/onboard")

        output = "\n".join(cm.output)
        self.assertIn("[REDACTED]", output)
        # None of the raw PII values may appear in the log line.
        self.assertNotIn("22212345678", output)
        self.assertNotIn("0123456789", output)
        self.assertNotIn("+2348012345678", output)

    def test_success_response_does_not_log_the_body(self):
        resp = MagicMock()
        resp.ok = True
        resp.status_code = 200
        resp.json.return_value = {"bvn": "22212345678"}

        with self.assertLogs(floatra_client._logger.name, level="INFO") as cm:
            _client()._handle_response(resp, "GET", "/v1/partner/merchants/x/credit-status")

        # Success path logs status only, never the body.
        self.assertNotIn("22212345678", "\n".join(cm.output))


class TestQueryEncoding(unittest.TestCase):
    def _url(self, call):
        with patch.object(
            floatra_client.requests, "request",
            return_value=MagicMock(status_code=200, json=lambda: {"success": True, "data": {"data": []}}),
        ) as req:
            call(_client())
        return req.call_args[0][1]

    def test_list_orders_url_encodes_params(self):
        url = self._url(lambda c: c.list_orders(
            status="a b", external_merchant_id="M 1", cursor="x&y=1",
        ))
        self.assertEqual(
            url,
            "https://gateway.example/v1/partner/orders"
            "?status=a+b&external_merchant_id=M+1&cursor=x%26y%3D1",
        )

    def test_undelivered_webhooks_encodes_params(self):
        url = self._url(lambda c: c.get_undelivered_webhooks(limit=10, offset=20))
        self.assertEqual(
            url, "https://gateway.example/v1/partner/webhooks/undelivered?limit=10&offset=20",
        )

    def test_path_ids_cannot_inject_segments(self):
        url = self._url(lambda c: c.lookup_order("../merchants?x=1"))
        self.assertEqual(
            url,
            "https://gateway.example/v1/partner/orders/by-external-id/"
            "..%2Fmerchants%3Fx%3D1",
        )


if __name__ == "__main__":
    unittest.main()
