"""Offline checks only; no ERP provisioning or model calls."""

import ast
from pathlib import Path

from environments.norvale.native.regimes import factors

values = [factors(42, d) for d in range(120)]
assert values == [factors(42, d) for d in range(120)]
for kind in ("demand", "supply", "cost"):
    assert any(values[d][kind] == values[d + 1][kind] > 1 for d in range(119))
    assert any(1 < values[d + 1][kind] < values[d][kind] for d in range(119))
source = Path(__file__).with_name("company.py").read_text()
assert "policy-model-budget" not in source and "Four-call" not in source
module = ast.parse(source)
functions = [n for n in module.body if isinstance(n, ast.FunctionDef) and n.name == "observable"]
namespace = {}
exec(compile(ast.Module(body=functions, type_ignores=[]), "observable", "exec"), namespace)
assert namespace["observable"]({"items": [{"custom_norvale_operation": "secret", "qty": 2}]}) == {"items": [{"qty": 2}]}
print("core offline checks passed")
