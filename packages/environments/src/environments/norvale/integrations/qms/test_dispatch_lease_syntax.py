"""Offline guard against RestrictedPython-disallowed unpacking in QMS server scripts."""

import ast

from environments.norvale.integrations.qms import dispatch_lease


def test_dispatch_lease_endpoint_has_no_tuple_unpacking():
    tree = ast.parse(dispatch_lease.ENDPOINT)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                assert not isinstance(target, (ast.Tuple, ast.List)), (
                    f"Line {node.lineno}: tuple/list unpacking compiles to _unpack_sequence_ in Frappe Server Scripts"
                )
