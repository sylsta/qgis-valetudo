#!/usr/bin/env python3
"""Check that every tr("...") string of the plugin is translated in each .ts file.

Usage: python3 scripts/check_translations.py
Exit code 1 if a string is missing or unfinished in a translation file.
Obsolete entries (in the .ts but no longer in the code) are only reported.
Needs only the Python standard library (no Qt required).
"""

import ast
import pathlib
import sys
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "valetudo_qgis"
CONTEXT = "ValetudoQgis"


def source_strings():
    found = {}
    for path in sorted(PLUGIN.glob("**/*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "tr"):
                arg = node.args[0] if node.args else None
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    found.setdefault(arg.value, f"{path.relative_to(ROOT)}:{node.lineno}")
                else:
                    print(f"WARNING {path.relative_to(ROOT)}:{node.lineno}: "
                          "tr() argument is not a string literal")
    return found


def ts_messages(path):
    messages = {}
    for ctx in ET.parse(path).getroot().iter("context"):
        if ctx.findtext("name") != CONTEXT:
            continue
        for msg in ctx.iter("message"):
            tr_el = msg.find("translation")
            done = (tr_el is not None and (tr_el.text or "").strip()
                    and tr_el.get("type") not in ("unfinished", "obsolete", "vanished"))
            messages[msg.findtext("source")] = bool(done)
    return messages


def main():
    sources = source_strings()
    ts_files = sorted((PLUGIN / "i18n").glob("*.ts"))
    if not ts_files:
        print("No .ts file found in valetudo_qgis/i18n/")
        return 1
    ok = True
    for ts in ts_files:
        messages = ts_messages(ts)
        missing = [s for s in sources if not messages.get(s)]
        obsolete = [s for s in messages if s not in sources]
        print(f"{ts.name}: {len(sources) - len(missing)}/{len(sources)} translated")
        for s in missing:
            print(f"  MISSING  {s!r}  ({sources[s]})")
            ok = False
        for s in obsolete:
            print(f"  OBSOLETE {s!r}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
