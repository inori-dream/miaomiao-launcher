"""Static guard for import entry points that can create or replace item state."""
from __future__ import annotations

import ast
import json
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
REGISTRY = REPO / "item_grant_audit_registry.json"
ENTRY_NAMES = {
    "build_player",
    "_write_database",
    "import_capture",
    "import_account",
    "import_backup",
}
REQUIRED_FIELDS = {"category", "idempotency", "transaction", "durability"}


def source_files():
    for path in REPO.rglob("*.py"):
        if any(part in {"__pycache__", "proto_gen"} for part in path.parts):
            continue
        if path.name.startswith("test_"):
            continue
        yield path


def top_level_functions(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node


def declared_import_functions() -> dict[str, dict]:
    data = json.loads(REGISTRY.read_text(encoding="utf-8"))
    return data["functions"]


class ItemImportAuditRegistryTests(unittest.TestCase):
    def test_every_import_entry_declares_idempotency_transaction_and_durability(self):
        declared = declared_import_functions()
        actual = set()
        for path in source_files():
            relative = path.relative_to(REPO).as_posix()
            for function in top_level_functions(path):
                if function.name.startswith("import_") or function.name in ENTRY_NAMES:
                    actual.add(f"miaomiao-launcher/{relative}::{function.name}")
        self.assertEqual(actual, set(declared))

    def test_declarations_are_complete(self):
        for key, declaration in declared_import_functions().items():
            with self.subTest(function=key):
                self.assertTrue(REQUIRED_FIELDS <= declaration.keys())
                self.assertEqual(declaration["category"], "import")
                for field in REQUIRED_FIELDS - {"category"}:
                    self.assertIsInstance(declaration[field], str)
                    self.assertTrue(declaration[field].strip())

    def test_journaled_import_recovery_remains_declared(self):
        declaration = declared_import_functions()[
            "miaomiao-launcher/capture_import.py::import_capture"
        ]
        self.assertIn("journal-recovery", declaration["transaction"])
        self.assertEqual(declaration["durability"], "journal-staging-db-commit-rename")


if __name__ == "__main__":
    unittest.main()
