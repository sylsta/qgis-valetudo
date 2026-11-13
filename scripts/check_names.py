#!/usr/bin/env python3
"""Report names used but never defined or imported in the plugin modules.

Catches a missing import (e.g. a Qt widget class) without needing QGIS or Qt.
Usage: python3 scripts/check_names.py   (exit code 1 if something is undefined)
"""

import ast
import builtins
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
MODULE_GLOBALS = {"__file__", "__name__", "__doc__"}


def undefined_names(source):
    tree = ast.parse(source)
    defined = set(dir(builtins)) | MODULE_GLOBALS
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            defined |= {(a.asname or a.name).split(".")[0] for a in node.names}
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(node.name)
        elif isinstance(node, ast.arg):
            defined.add(node.arg)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            defined.add(node.id)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            defined.add(node.name)
    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    return sorted(used - defined)


def main():
    ok = True
    for path in sorted((ROOT / "valetudo_qgis").glob("**/*.py")):
        missing = undefined_names(path.read_text(encoding="utf-8"))
        if missing:
            ok = False
            print(f"{path.relative_to(ROOT)}: undefined: {', '.join(missing)}")
    print("All names defined." if ok else "Undefined names found.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
