#!/usr/bin/env python3
"""Turn capacities.xlsx into data/capacities.json.

The workbook is the engineering source; the JSON is what ships. Run this after
any change to the workbook, commit both, and CI will confirm the JSON matches:

    python tools/extract_capacities.py path/to/capacities.xlsx

Sheet shapes this understands:

  "small meters" / "roots"  one Max column per model
  "turbo" / "rmg"           a Max and a Min column per model

Row 1 is the model, row 2 the meter size, row 3 a spanning "Capacity (CFH)"
label, and for the two-column sheets row 4 the Max/Min sub-header. Data rows
follow, keyed by inlet pressure in column A.

Cells that read "N/A" or 0 mean the model is not rated at that pressure. They
are dropped rather than stored, so a model's last stored pressure IS its
pressure limit and no interpolation can ever run through a hole in the table.
"""

import json
import sys
from pathlib import Path

from openpyxl import load_workbook

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "data" / "capacities.json"

# Which sheets carry a Min capacity, and the oversize factor the sizing rules
# apply to each family.
FAMILIES = [
    # key,     sheet name,       has_min, oversize
    ("small", "small meters", False, 0.0),
    ("roots", "roots", False, 0.2),
    ("turbo", "turbo", True, 0.2),
    ("rmg", "rmg", True, 0.0),
]


def _blank(v):
    """True when a capacity cell carries no rating."""
    if v is None:
        return True
    if isinstance(v, str):
        return v.strip().upper() in ("", "N/A", "NA", "-")
    # A 0 in the roots sheet marks a pressure the model is not rated for; a
    # real meter never has a capacity of zero, so this is unambiguous.
    return float(v) == 0.0


def _num(v):
    """Capacities come back as floats with float noise (130800.00000000001)."""
    f = float(v)
    return round(f, 6)


def read_sheet(ws, has_min):
    """-> list of model dicts in left-to-right (ascending capacity) order."""
    rows = list(ws.iter_rows(values_only=True))
    models_row, size_row = rows[0], rows[1]
    first_data = 3 if not has_min else 4  # skip the Max/Min sub-header

    # Column index per model. On the two-column sheets the model name sits on
    # the Max column and the Min column beside it is blank.
    cols = []
    for c in range(1, len(models_row)):
        name = models_row[c]
        if name is None or str(name).strip() == "":
            continue
        cols.append(
            {
                "model": str(name).strip(),
                "size": (str(size_row[c]).strip() if size_row[c] else None),
                "max_col": c,
                "min_col": (c + 1) if has_min else None,
            }
        )

    out = []
    for spec in cols:
        points = []
        for row in rows[first_data:]:
            p = row[0]
            if p is None:
                continue
            mx = row[spec["max_col"]]
            if _blank(mx):
                continue
            point = {"p": _num(p), "max": _num(mx)}
            if spec["min_col"] is not None:
                mn = row[spec["min_col"]]
                # A missing Min on a sheet that has the column means no
                # minimum at that pressure, which the rules treat as 0.
                point["min"] = 0.0 if _blank(mn) else _num(mn)
            points.append(point)
        points.sort(key=lambda d: d["p"])
        out.append(
            {
                "model": spec["model"],
                "size": spec["size"],
                "has_min": bool(spec["min_col"] is not None),
                "points": points,
            }
        )
    return out


def main():
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO / "data" / "capacities.xlsx"
    wb = load_workbook(src, data_only=True)
    data = {"source": src.name, "families": {}}
    for key, sheet, has_min, oversize in FAMILIES:
        data["families"][key] = {
            "sheet": sheet,
            "oversize": oversize,
            "meters": read_sheet(wb[sheet], has_min),
        }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, indent=2) + "\n")

    for key, fam in data["families"].items():
        for m in fam["meters"]:
            pts = m["points"]
            print(
                f"{key:6} {m['model']:10} size={str(m['size']):7} "
                f"{len(pts):2} points, {pts[0]['p']}-{pts[-1]['p']} psi"
            )
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
