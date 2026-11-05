#!/usr/bin/env python3
"""
Checks that every dataclass/enum in each agents/*_runtime/agent_types.py
matches the class of the same name in pa_types.py (same fields, same order,
same annotations and defaults; same enum members). Containers may hold a
subset of classes, but never a drifted copy of one.

Exits non-zero on drift, so a missing field (e.g. NecessityDetermination.
peer_clarification) fails at build time instead of as a TypeError in
CloudWatch. Usage: python3 scripts/check_agent_types_sync.py
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Container classes that are *deliberately* trimmed input contracts. These may
# omit fields, but every field they do declare must still match pa_types.py
# exactly and in order. Every other class must match field-for-field.
INTENTIONAL_SUBSETS = {
    # Clinical Extraction only reads the fields its contract documents.
    ("clinical_extraction_runtime", "PARequest"),
}


def _class_signatures(path: Path) -> dict[str, list[str]]:
    tree = ast.parse(path.read_text(), filename=str(path))
    out: dict[str, list[str]] = {}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        members: list[str] = []
        for stmt in node.body:
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                default = ast.unparse(stmt.value) if stmt.value is not None else ""
                members.append(f"{stmt.target.id}: {ast.unparse(stmt.annotation)} = {default}")
            elif isinstance(stmt, ast.Assign):
                for target in stmt.targets:
                    if isinstance(target, ast.Name):
                        members.append(f"{target.id} = {ast.unparse(stmt.value)}")
        out[node.name] = members
    return out


def main() -> int:
    reference = _class_signatures(ROOT / "pa_types.py")
    problems: list[str] = []
    for path in sorted((ROOT / "agents").glob("*_runtime/agent_types.py")):
        rel = path.relative_to(ROOT)
        for name, members in _class_signatures(path).items():
            if name not in reference:
                problems.append(f"{rel}: class {name} does not exist in pa_types.py")
            elif (path.parent.name, name) in INTENTIONAL_SUBSETS:
                ref = reference[name]
                if [m for m in ref if m in members] != members:
                    problems.append(
                        f"{rel}: {name} (intentional subset) declares fields that "
                        f"differ from pa_types.py: {[m for m in members if m not in ref]}"
                    )
            elif members != reference[name]:
                missing = [m for m in reference[name] if m not in members]
                extra = [m for m in members if m not in reference[name]]
                problems.append(
                    f"{rel}: {name} differs from pa_types.py"
                    + (f"\n    missing: {missing}" if missing else "")
                    + (f"\n    extra:   {extra}" if extra else "")
                    + ("\n    (same members, different order)" if not missing and not extra else "")
                )
    if problems:
        print("agent_types.py drift detected:\n  " + "\n  ".join(problems))
        return 1
    print("agent_types.py copies are in sync with pa_types.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
