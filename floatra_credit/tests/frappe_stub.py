# -*- coding: utf-8 -*-
"""A minimal ``frappe`` stand-in so the unit tests run without a bench.

Every unit test module calls ``install()`` before importing app code. It
creates the module if absent and adds any missing attribute, so test
modules run in one process in any order. Tests patch what they assert on.

Under ``bench run-tests`` the real frappe is loaded: the stubbed modules
then contribute no tests (``load_tests``) and install nothing, since their
mocks would replace the real frappe for the bench suite that runs after
them. They run as the plain suite (``python -m unittest``).
"""

import sys
import types
import unittest
from unittest.mock import MagicMock


def under_bench():
    """True when the real frappe package is loaded (``bench run-tests``)."""
    return getattr(sys.modules.get("frappe"), "__file__", None) is not None


def load_tests(loader, tests, pattern):
    """unittest ``load_tests`` hook for the stubbed modules: none under bench."""
    if under_bench():
        return unittest.TestSuite()
    return tests


def _whitelist(*args, **kwargs):
    if args and callable(args[0]):
        return args[0]
    return lambda func: func


def install():
    if under_bench():
        return sys.modules["frappe"]
    frappe = sys.modules.get("frappe")
    if frappe is None:
        frappe = types.ModuleType("frappe")
        sys.modules["frappe"] = frappe
    defaults = {
        "whitelist": _whitelist,
        "_": lambda s: s,
        "_dict": dict,
        "get_single": MagicMock(),
        "get_doc": MagicMock(),
        "get_meta": MagicMock(),
        "get_all": MagicMock(return_value=[]),
        "has_permission": MagicMock(return_value=True),
        "throw": MagicMock(side_effect=RuntimeError),
        "log_error": MagicMock(),
        "local": types.SimpleNamespace(request=None, response={}),
        "PermissionError": type("PermissionError", (Exception,), {}),
    }
    for name, value in defaults.items():
        if not hasattr(frappe, name):
            setattr(frappe, name, value)
    db_defaults = {
        "set_value": MagicMock(),
        "exists": MagicMock(return_value=True),
        "get_value": MagicMock(),
        "get_single_value": MagicMock(return_value="test-platform"),
        "commit": MagicMock(),
        "rollback": MagicMock(),
    }
    # `from frappe.utils import ...` resolves through sys.modules. The site
    # timezone is Lagos (UTC+1), as on a Nigerian distributor's bench.
    utils = sys.modules.get("frappe.utils")
    if utils is None:
        utils = types.ModuleType("frappe.utils")
        sys.modules["frappe.utils"] = utils
    if not hasattr(utils, "get_system_timezone"):
        utils.get_system_timezone = lambda: "Africa/Lagos"
    frappe.utils = utils
    if not hasattr(frappe, "db"):
        frappe.db = types.SimpleNamespace()
    for name, value in db_defaults.items():
        if not hasattr(frappe.db, name):
            setattr(frappe.db, name, value)
    return frappe
