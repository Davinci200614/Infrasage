"""Ground-truth isolation guard (guide §9).

The diagnostic packages must never be able to read the injected fault label, so
none of them may import ``backend.twin`` (or anything under it). This test is
the enforcement, not a convention.
"""

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
DIAGNOSTIC_PACKAGES = ("api", "collectors", "core", "diagnosis", "optimization", "energy")
FORBIDDEN = "backend.twin"


def _module_name(path: Path) -> str:
    return ".".join(path.relative_to(REPO).with_suffix("").parts)


def _imported_modules(tree: ast.AST, module: str) -> list[str]:
    """Every absolute module name imported by this file, relative imports resolved."""
    package = module.rsplit(".", 1)[0]
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = package.split(".")
                base = parts[: len(parts) - node.level + 1]
                target = ".".join(base + ([node.module] if node.module else []))
            else:
                target = node.module or ""
            names.append(target)
            names += [f"{target}.{alias.name}" for alias in node.names]
    return names


def _violations() -> list[str]:
    found: list[str] = []
    for package in DIAGNOSTIC_PACKAGES:
        for path in (REPO / "backend" / package).rglob("*.py"):
            tree = ast.parse(path.read_text(), filename=str(path))
            for name in _imported_modules(tree, _module_name(path)):
                if name == FORBIDDEN or name.startswith(FORBIDDEN + "."):
                    found.append(f"{path.relative_to(REPO)}: imports {name}")
    return found


def test_no_diagnostic_module_imports_the_twin():
    found = _violations()
    assert not found, "Ground-truth isolation broken:\n" + "\n".join(found)


def test_guard_catches_a_violation(tmp_path):
    """A guard that cannot fail proves nothing; check it on a known-bad file."""
    bad = tmp_path / "bad.py"
    bad.write_text("from backend.twin.ground_truth import label\n")
    names = _imported_modules(ast.parse(bad.read_text()), "bad")
    assert any(n.startswith(FORBIDDEN) for n in names)
