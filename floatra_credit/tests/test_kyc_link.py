# -*- coding: utf-8 -*-
"""Hosted KYC link: request body, response parsing, error copy, the API
client call, and the `api.action_get_kyc_link` desk endpoint, against
core-rendered contract fixtures.

The fixtures in ``tests/fixtures/contract/`` are written by core's
``src/modules/partner-webhooks/partner-contract-fixtures.spec.ts`` from the
real controller, issuer and exception filter (the two ``request-*`` files are
ours; core validates them against its DTO). A contract change on either side
fails one of the two test suites.

`frappe` is stubbed so this runs without a bench:
``python3 -m unittest floatra_credit.tests.test_kyc_link`` (or pytest) from
``connectors/erpnext/floatra_credit``.
"""

import json
import os
import sys
import types
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch


def _install_frappe_stub() -> None:
    frappe = sys.modules.get("frappe")
    if frappe is None:
        frappe = types.ModuleType("frappe")

        def _whitelist(*args, **kwargs):
            if args and callable(args[0]):
                return args[0]
            return lambda func: func

        frappe.whitelist = _whitelist
        frappe._ = lambda s: s
        frappe.get_single = MagicMock()
        frappe.db = types.SimpleNamespace(
            set_value=MagicMock(),
            exists=MagicMock(return_value=True),
            get_value=MagicMock(),
            commit=MagicMock(),
        )
        sys.modules["frappe"] = frappe
    if not hasattr(frappe, "get_doc"):
        frappe.get_doc = MagicMock()
    if not hasattr(frappe, "throw"):
        # Same convention as test_api_authorization's stub: throw raises.
        frappe.throw = MagicMock(side_effect=RuntimeError)
    if not hasattr(frappe, "PermissionError"):
        frappe.PermissionError = type("PermissionError", (Exception,), {})


from . import frappe_stub  # noqa: E402

frappe_stub.install()
if not frappe_stub.under_bench():
    _install_frappe_stub()
load_tests = frappe_stub.load_tests

from .. import api  # noqa: E402
from ..services import floatra_api, floatra_client, kyc_link  # noqa: E402

_FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "contract")
# Realistic key shapes, as core issues them.
LIVE_KEY = "live_pk_" + "a1" * 24
SANDBOX_KEY = "sbx_pk_" + "b2" * 16
ROTATED_SANDBOX_KEY = "pk_" + "c3" * 24
BASE_URL = "https://gateway.example"


def fixture(name):
    with open(os.path.join(_FIXTURES, f"{name}.json"), encoding="utf-8") as f:
        return json.load(f)


def _response(fx):
    resp = MagicMock()
    resp.status_code = fx["status"]
    resp.ok = 200 <= fx["status"] < 300
    resp.json.return_value = fx["body"]
    return resp


class TestRequestBody(unittest.TestCase):
    def test_live_body_is_empty_and_matches_the_contract_fixture(self):
        self.assertEqual(kyc_link.kyc_retry_body(sandbox=False), {})
        self.assertEqual(
            kyc_link.kyc_retry_body(sandbox=False),
            fixture("request-kyc-retry-live"),
        )

    def test_sandbox_body_matches_the_contract_fixture(self):
        self.assertEqual(
            kyc_link.kyc_retry_body(sandbox=True),
            fixture("request-kyc-retry-sandbox"),
        )

    def test_realm_comes_from_the_key_prefix(self):
        self.assertTrue(kyc_link.is_live_key(LIVE_KEY))
        self.assertFalse(kyc_link.is_sandbox_key(LIVE_KEY))
        for key in (SANDBOX_KEY, ROTATED_SANDBOX_KEY):
            self.assertTrue(kyc_link.is_sandbox_key(key), key)
            self.assertFalse(kyc_link.is_live_key(key), key)
        # The prefixes core never issues are not live either.
        for key in ("pk_live_abc", "pk_sandbox_abc", "", None):
            self.assertFalse(kyc_link.is_live_key(key), key)


class TestParseResponse(unittest.TestCase):
    def test_hosted_link(self):
        out = kyc_link.parse_kyc_retry_response(
            fixture("response-kyc-retry-hosted-link")["body"],
        )
        self.assertEqual(out.kind, kyc_link.HOSTED_LINK)
        self.assertEqual(out.kyc_url, "https://floatra.com/kyc/contract-fixture-token")
        self.assertEqual(
            out.expires_at, datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc),
        )

    def test_sandbox_result(self):
        out = kyc_link.parse_kyc_retry_response(
            fixture("response-kyc-retry-sandbox-result")["body"],
        )
        self.assertEqual(out.kind, kyc_link.SANDBOX_RESULT)
        self.assertTrue(out.sandbox_verified)

    def test_unrecognised_body_raises(self):
        for body in ({"success": True, "data": {}}, [], None):
            with self.assertRaises(ValueError):
                kyc_link.parse_kyc_retry_response(body)


class TestErrorMessages(unittest.TestCase):
    CASES = {
        "response-kyc-retry-error-already-verified": "already verified both",
        "response-kyc-retry-error-not-available": "cannot issue a KYC link",
        "response-kyc-retry-error-rate-limited": "Too many KYC links",
        "response-kyc-retry-error-unavailable": "temporarily unavailable",
        "response-kyc-retry-error-hosted-link-only": "out of date",
        "response-kyc-retry-error-not-found": "does not know this merchant",
        "response-kyc-retry-error-other-platform": "does not belong",
        "response-kyc-retry-error-realm": "other Floatra environment",
    }

    def test_every_contract_error_has_user_copy(self):
        for name, expected in self.CASES.items():
            fx = fixture(name)
            self.assertIn(
                expected,
                kyc_link.kyc_retry_error_message(fx["status"], fx["body"]),
                name,
            )


class TestClientRequestKycLink(unittest.TestCase):
    def _call(self, key, fx_name):
        client = floatra_api.FloatraAPIClient(
            base_url=BASE_URL, api_key=key, platform_id="plat-1",
        )
        with patch.object(
            floatra_client.requests,
            "request",
            return_value=_response(fixture(fx_name)),
        ) as req:
            try:
                return client.request_kyc_link("mer_01"), req
            except floatra_api.FloatraAPIError as err:
                return err, req

    def test_live_key_posts_an_empty_body_to_the_partner_route(self):
        out, req = self._call(LIVE_KEY, "response-kyc-retry-hosted-link")
        method, url = req.call_args.args
        self.assertEqual(method, "POST")
        self.assertEqual(url, f"{BASE_URL}/v1/partner/merchants/mer_01/kyc/retry")
        self.assertEqual(json.loads(req.call_args.kwargs["data"]), {})
        self.assertEqual(out.kind, kyc_link.HOSTED_LINK)

    def test_rotated_pk_key_is_sandbox_and_posts_the_test_body(self):
        _out, req = self._call(ROTATED_SANDBOX_KEY, "response-kyc-retry-sandbox-result")
        self.assertEqual(
            json.loads(req.call_args.kwargs["data"]),
            fixture("request-kyc-retry-sandbox"),
        )

    def test_sandbox_key_posts_the_test_body(self):
        out, req = self._call(SANDBOX_KEY, "response-kyc-retry-sandbox-result")
        self.assertEqual(
            json.loads(req.call_args.kwargs["data"]),
            fixture("request-kyc-retry-sandbox"),
        )
        self.assertEqual(out.kind, kyc_link.SANDBOX_RESULT)

    def test_rate_limit_surfaces_as_an_error_with_its_code(self):
        err, _req = self._call(LIVE_KEY, "response-kyc-retry-error-rate-limited")
        self.assertIsInstance(err, floatra_api.FloatraAPIError)
        self.assertEqual(err.status, 429)
        self.assertEqual(kyc_link.error_code_of(err.body), "KYC_LINK_RATE_LIMITED")

    def test_the_old_identity_body_method_is_gone(self):
        self.assertFalse(hasattr(floatra_api.FloatraAPIClient, "retry_kyc"))


class _Thrown(Exception):
    pass


def _throw(message, *args, **kwargs):
    raise _Thrown(message)


class TestActionGetKycLink(unittest.TestCase):
    """The desk endpoint, with the client mocked at `for_site`."""

    def setUp(self):
        self.doc = MagicMock()
        self.doc.get.side_effect = {"floatra_merchant_id": "mer_01"}.get
        self.doc.floatra_merchant_id = "mer_01"
        self.set_value = MagicMock()
        patches = [
            patch.object(api.frappe, "get_doc", return_value=self.doc),
            patch.object(api.frappe, "throw", side_effect=_throw),
            patch.object(
                api.frappe,
                "db",
                types.SimpleNamespace(set_value=self.set_value, commit=MagicMock()),
            ),
            patch.object(
                api,
                "_utc_to_system_naive",
                side_effect=lambda dt: dt.replace(tzinfo=None),
            ),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.client = MagicMock()
        p = patch.object(api.FloatraAPIClient, "for_site", return_value=self.client)
        p.start()
        self.addCleanup(p.stop)

    def test_hosted_link_is_stored_and_returned(self):
        self.client.request_kyc_link.return_value = kyc_link.parse_kyc_retry_response(
            fixture("response-kyc-retry-hosted-link")["body"],
        )
        out = api.action_get_kyc_link("CUST-1")
        self.client.request_kyc_link.assert_called_once_with("mer_01")
        self.assertEqual(out["kind"], "hosted_link")
        self.assertEqual(out["kyc_url"], "https://floatra.com/kyc/contract-fixture-token")
        _doctype, _name, updates = self.set_value.call_args.args
        self.assertEqual(
            updates,
            {
                "floatra_kyc_url": "https://floatra.com/kyc/contract-fixture-token",
                "floatra_kyc_url_expires_at": datetime(2026, 10, 2, 12, 0),
            },
        )

    def test_sandbox_result_mirrors_status_and_clears_any_link(self):
        self.client.request_kyc_link.return_value = kyc_link.parse_kyc_retry_response(
            fixture("response-kyc-retry-sandbox-result")["body"],
        )
        out = api.action_get_kyc_link("CUST-1")
        self.assertEqual(out["kind"], "sandbox_result")
        _doctype, _name, updates = self.set_value.call_args.args
        self.assertEqual(updates["floatra_kyc_status"], "approved")
        self.assertIsNone(updates["floatra_kyc_url"])

    def test_refusal_is_a_clean_message_and_stores_nothing(self):
        fx = fixture("response-kyc-retry-error-already-verified")
        self.client.request_kyc_link.side_effect = floatra_api.FloatraAPIError(
            fx["status"], fx["body"],
        )
        with self.assertRaises(_Thrown) as ctx:
            api.action_get_kyc_link("CUST-1")
        self.assertIn("already verified both", ctx.exception.args[0])
        self.set_value.assert_not_called()

    def test_unenrolled_customer_is_refused_before_the_api(self):
        self.doc.get.side_effect = {}.get
        with self.assertRaises(_Thrown):
            api.action_get_kyc_link("CUST-1")
        self.client.request_kyc_link.assert_not_called()


if __name__ == "__main__":
    unittest.main()
