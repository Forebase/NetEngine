#!/usr/bin/env python3
"""Generate a Markdown license inventory for installed Python distributions."""
from __future__ import annotations

from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "licenses.md"


def field(meta: metadata.PackageMetadata, name: str) -> str:
    value = meta.get(name) or ""
    compact = " ".join(value.split())
    if len(compact) > 120:
        compact = compact[:117].rstrip() + "..."
    return compact or "UNKNOWN"


def main() -> int:
    rows = []
    for dist in sorted(metadata.distributions(), key=lambda d: (d.metadata.get("Name") or "").lower()):
        meta = dist.metadata
        name = field(meta, "Name")
        version = dist.version or "UNKNOWN"
        license_expr = field(meta, "License-Expression")
        license_field = license_expr if license_expr != "UNKNOWN" else field(meta, "License")
        homepage = field(meta, "Home-page")
        rows.append((name, version, license_field, homepage))

    OUT.write_text(
        "# Python License Inventory\n\n"
        "Generated from installed Python package metadata. Re-run with "
        "`python scripts/generate_license_list.py` after dependency changes.\n\n"
        "| Package | Version | License | Home page |\n"
        "| --- | --- | --- | --- |\n"
        + "".join(f"| {n} | {v} | {l} | {h} |\n" for n, v, l, h in rows)
    )
    print(f"wrote {OUT.relative_to(ROOT)} with {len(rows)} packages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
