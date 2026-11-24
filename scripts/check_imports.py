"""Reject imports from lower-level workspace packages into environments or components."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENVIRONMENT_IMPORTS = {"environments", "consumers", "integrations", "native", "norvale", "walkthrough"}
FORBIDDEN = {
    "engine": ENVIRONMENT_IMPORTS | {"components"},
    "components": ENVIRONMENT_IMPORTS,
}


def main():
    violations = []
    for package, forbidden in FORBIDDEN.items():
        for source in sorted((ROOT / "packages" / package / "src").rglob("*.py")):
            for node in ast.walk(ast.parse(source.read_bytes(), filename=str(source))):
                if isinstance(node, ast.Import):
                    imports = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0:
                    imports = [node.module]
                else:
                    continue
                for name in imports:
                    if name.split(".")[0] in forbidden:
                        violations.append(f"{source.relative_to(ROOT)}:{node.lineno}: {package} imports {name}")
    if violations:
        raise SystemExit("\n".join(violations))


if __name__ == "__main__":
    main()
