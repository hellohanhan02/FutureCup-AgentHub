"""Check dependency direction without installing runtime dependencies."""

import ast
from pathlib import Path
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "src" / "futurecup"


class DependencyBoundaryTests(unittest.TestCase):
    def test_inner_layers_have_no_framework_or_adapter_imports(self) -> None:
        allowed = {
            "domain": {"dataclasses", "datetime", "enum", "typing", "math", "futurecup.domain"},
            "application": {"dataclasses", "datetime", "typing", "asyncio", "json", "time", "uuid", "contextlib", "futurecup.domain", "futurecup.application"},
        }
        for layer, prefixes in allowed.items():
            paths = list((SOURCE / layer).rglob("*.py"))
            self.assertTrue(paths, f"Missing layer: {layer}")
            for path in paths:
                tree = ast.parse(path.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        modules = [item.name for item in node.names]
                    elif isinstance(node, ast.ImportFrom):
                        self.assertEqual(node.level, 0, f"Use auditable absolute imports: {path}")
                        modules = [node.module or ""]
                    else:
                        continue
                    for module in modules:
                        with self.subTest(path=path.name, module=module):
                            self.assertTrue(any(module == p or module.startswith(p + ".")
                                                for p in prefixes), module)


if __name__ == "__main__":
    unittest.main()
