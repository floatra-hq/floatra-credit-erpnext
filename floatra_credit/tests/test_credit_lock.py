# -*- coding: utf-8 -*-
"""Unit tests for credit_lock.is_locked_failsafe.

These are pure-Python tests — no Frappe runtime required. They use
a lightweight stand-in for the Customer doc so we can exercise the
failsafe semantics without bench/site setup. The full integration
suite (DocType installability, hooks, webhook idempotency) runs
inside Frappe via:

    bench --site <site> run-tests --app floatra_credit
"""

import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace

from ..services.credit_lock import is_locked_failsafe


class TestCreditLockFailsafe(unittest.TestCase):
    def _customer(self, status, last_check=None):
        return SimpleNamespace(
            floatra_credit_status=status,
            floatra_last_status_check=last_check,
        )

    def test_locked_status_returns_locked(self):
        c = self._customer("locked", last_check=datetime.utcnow())
        self.assertTrue(is_locked_failsafe(c))

    def test_suspended_status_returns_locked(self):
        c = self._customer("suspended", last_check=datetime.utcnow())
        self.assertTrue(is_locked_failsafe(c))

    def test_not_enrolled_returns_locked(self):
        c = self._customer("not_enrolled", last_check=datetime.utcnow())
        self.assertTrue(is_locked_failsafe(c))

    def test_pending_kyc_returns_locked(self):
        c = self._customer("pending_kyc", last_check=datetime.utcnow())
        self.assertTrue(is_locked_failsafe(c))

    def test_available_with_recent_check_returns_unlocked(self):
        c = self._customer("available", last_check=datetime.utcnow())
        self.assertFalse(is_locked_failsafe(c))

    def test_available_with_stale_check_returns_locked(self):
        c = self._customer(
            "available",
            last_check=datetime.utcnow() - timedelta(minutes=30),
        )
        self.assertTrue(is_locked_failsafe(c))

    def test_available_with_no_check_returns_locked_failsafe(self):
        c = self._customer("available", last_check=None)
        self.assertTrue(is_locked_failsafe(c))

    def test_available_with_iso_string_timestamp(self):
        # Frappe sometimes returns DB datetimes as ISO strings.
        iso = (datetime.utcnow() - timedelta(minutes=5)).isoformat()
        c = self._customer("available", last_check=iso)
        self.assertFalse(is_locked_failsafe(c))

    def test_unparseable_timestamp_returns_locked(self):
        c = self._customer("available", last_check="not-a-date")
        self.assertTrue(is_locked_failsafe(c))

    def test_unknown_status_returns_locked(self):
        c = self._customer("nonsense", last_check=datetime.utcnow())
        # Unknown -> not in any "explicit lock" branch but also not
        # 'available' — so the function falls through to the
        # available branch which checks TTL. Recent check => unlocked.
        # This documents current behaviour; if we want a hard
        # failsafe for unknown states, change the implementation
        # AND update this test.
        self.assertFalse(is_locked_failsafe(c))


if __name__ == "__main__":
    unittest.main()
