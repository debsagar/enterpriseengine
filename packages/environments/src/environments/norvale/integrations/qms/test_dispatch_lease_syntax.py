"""Offline guard against RestrictedPython-disallowed unpacking in QMS server scripts."""

import ast
from unittest import TestCase

from environments.norvale.integrations.qms import dispatch_lease


class DispatchLeaseSyntax(TestCase):
    def test_endpoint_has_no_tuple_unpacking(self):
        for node in ast.walk(ast.parse(dispatch_lease.ENDPOINT)):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    self.assertNotIsInstance(
                        target,
                        (ast.Tuple, ast.List),
                        f"Line {node.lineno}: tuple/list unpacking compiles to _unpack_sequence_ in Frappe Server Scripts",
                    )
