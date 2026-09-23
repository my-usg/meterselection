#!/usr/bin/env python3
"""Run every case in cases.json through both implementations and diff them.

The Python module and the built JS bundle are the same rules written twice, so
the only way to know they still agree is to run them side by side. This is the
check that makes "the chatbot and the website give the same answer" a fact
rather than a hope.

    python tests/test_parity.py            # needs node on PATH
    python -m pytest tests/test_parity.py

Also runs the fixed expectations in tests/test_rules.py' sibling list below:
sample outputs taken from the sizing instructions, which catch the case where
both implementations are wrong in the same way and parity alone would pass.
"""

import json
import math
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from algorithm.meter_sizing import (  # noqa: E402
    eagle_p1,
    roots_forced_eagle,
    size_meters,
)

CASES = REPO / "tests" / "cases.json"
BUNDLE = REPO / "dist" / "usg-meter-sizing.js"

# Floats cross the language boundary through JSON, so capacities that came out
# of an interpolation can differ in the last bit. Anything larger than this is
# a real disagreement.
TOLERANCE = 1e-9


def run_js(cases):
    """-> list of results, one per case, from the built bundle under node."""
    driver = (
        "const M = require(%s);\n"
        "const cases = JSON.parse(require('fs').readFileSync(%s, 'utf8'));\n"
        "const out = cases.map(function (c) {\n"
        "  try { return M.sizeMeters(c); }\n"
        "  catch (e) { return { __threw: String(e && e.message || e) }; }\n"
        "});\n"
        "process.stdout.write(JSON.stringify(out));\n"
        % (json.dumps(str(BUNDLE)), json.dumps(str(CASES)))
    )
    proc = subprocess.run(
        ["node", "-e", driver], capture_output=True, text=True, cwd=str(REPO)
    )
    if proc.returncode != 0:
        raise SystemExit("node failed:\n" + proc.stderr)
    return json.loads(proc.stdout)


def run_py(cases):
    out = []
    for case in cases:
        try:
            out.append(json.loads(json.dumps(size_meters(case))))
        except Exception as exc:  # noqa: BLE001 - mirrors the JS driver
            out.append({"__threw": str(exc)})
    return out


def diff(a, b, path=""):
    """-> list of human-readable differences between two JSON values."""
    if isinstance(a, float) or isinstance(b, float):
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            if math.isclose(float(a), float(b), rel_tol=TOLERANCE, abs_tol=TOLERANCE):
                return []
            return [f"{path}: py={a!r} js={b!r}"]
    if type(a) is not type(b) and not (a is None or b is None):
        # bool/int and int/float already handled; anything else is a mismatch.
        if not (isinstance(a, (int, float)) and isinstance(b, (int, float))):
            return [f"{path}: type py={type(a).__name__} js={type(b).__name__}"]
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for key in sorted(set(a) | set(b)):
            if key not in a:
                out.append(f"{path}.{key}: missing in py")
            elif key not in b:
                out.append(f"{path}.{key}: missing in js")
            else:
                out.extend(diff(a[key], b[key], f"{path}.{key}"))
        return out
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return [f"{path}: length py={len(a)} js={len(b)}"]
        out = []
        for i, (x, y) in enumerate(zip(a, b)):
            out.extend(diff(x, y, f"{path}[{i}]"))
        return out
    if a != b:
        return [f"{path}: py={a!r} js={b!r}"]
    return []


# --------------------------------------------------------------------------
# Fixed expectations from the sizing instructions.
# --------------------------------------------------------------------------
# Parity proves the two builds agree. These prove they agree on the right
# answer: each row is a worked example or a stated rule from the instructions
# document, so a rule that gets ported wrongly to BOTH sides still fails here.

EXPECTED = [
    # (label, payload, {field: expected})
    (
        "R275 sample output",
        {"inlet": 7, "inlet_units": "in wc", "flow": 250, "flow_units": "CFH",
         "meter_types": ["Diaphragm"], "answers": {"diaphragm.ferrule": "1-1/4"}},
        {"manufacturer": "Sensus", "model": "R275", "size": '6" CTC',
         "part_number": "M.R275.TC.5.D/R.1-1/4.TOP.NA"},
    ),
    (
        "Sonix IQ 250 quotes rather than numbering",
        {"inlet": 2, "flow": 380, "meter_types": ["Sonix IQ"],
         "answers": {"sonix_iq.ferrule": "20LT", "sonix_iq.pulse": "Yes"}},
        {"manufacturer": "Sensus", "model": "Sonix IQ 250", "part_number": None,
         "quote_note": "contact Holland Supply for a quote"},
    ),
    (
        "Sonix 600 sample output",
        {"inlet": 10, "flow": 1800, "meter_types": ["Ultrasonic"],
         "answers": {"ultrasonic.ferrule": "30LT"}},
        {"manufacturer": "Sensus", "model": "Sonix 600",
         "part_number": "M.SON-600.30LT.FIX.20.PO"},
    ),
    (
        "Sonix 880 is the fallback when the 600 falls short",
        {"inlet": 10, "flow": 2000, "meter_types": ["Ultrasonic"],
         "answers": {"ultrasonic.ferrule": "30LT"}},
        {"model": "Sonix 880", "part_number": "M.SON-880.30LT.FIX.20.PO"},
    ),
    (
        "D800 sample output",
        {"inlet": 20, "flow": 3900, "meter_types": ["Rotary (meter bar)"],
         "answers": {"rotary_meter_bar.ferrule": "45LT"}},
        {"manufacturer": "Dresser", "model": "D800", "size": '11" CTC',
         "part_number": "M.D800.45LT.CBG.25.NA.LIT.NA"},
    ),
    (
        "10C25 sample output, and it carries no CTC size",
        {"inlet": 20, "flow": 2000, "meter_types": ["Rotary (straight pipe)"],
         "answers": {"rotary_straight_pipe.connection": "1-1/2"}},
        {"manufacturer": "Dresser", "model": "10C25", "size": None,
         "part_number": "M.RT10C25.1-1/2.DI-T.BP.TOP.N/A.TIBT.N/A"},
    ),
    (
        "roots 3M175 sample output, ETC index -> CIR / LITH",
        {"inlet": 60, "flow": 12000, "meter_types": ["Rotary (roots)"],
         "answers": {"rotary_roots.compensation": "Fix-Factored",
                     "rotary_roots.index": "ETC"}},
        {"manufacturer": "Dresser", "model": "3M175", "size": '2"',
         "part_number": "M.RT3M-175.FLG.ETC.CIR.175.VDN.NA.LITH.NA"},
    ),
    (
        "an Eagle volume corrector puts CD in the roots part number",
        {"inlet": 60, "flow": 12000, "meter_types": ["Rotary (roots)"],
         "answers": {"rotary_roots.compensation": "Fix-Factored",
                     "rotary_roots.index": "Eagle MPplusII Instrument",
                     "rotary_roots.eagle_type": "Volume Corrector"}},
        {"part_number": "M.RT3M-175.FLG.CD.NA.175.VDN.NA.NA.NA"},
    ),
    (
        "an Eagle rotary corrector puts CTR there instead",
        {"inlet": 60, "flow": 12000, "meter_types": ["Rotary (roots)"],
         "answers": {"rotary_roots.compensation": "Fix-Factored",
                     "rotary_roots.index": "Eagle MPplusII Instrument",
                     "rotary_roots.eagle_type": "Rotary Corrector"}},
        {"part_number": "M.RT3M-175.FLG.CTR.NA.175.VDN.NA.NA.NA"},
    ),
    (
        "the live path resolves the drive the same way - volume",
        {"inlet": 60, "flow": 12000, "meter_types": ["Rotary (roots)"],
         "answers": {"rotary_roots.compensation": "Live",
                     "rotary_roots.live": "Eagle MPplusII Instrument",
                     "rotary_roots.eagle_type": "Volume Corrector"}},
        {"part_number": "M.RT3M-175.FLG.CD.NA.175.VDN.NA.NA.NA"},
    ),
    (
        "the live path resolves the drive the same way - rotary",
        {"inlet": 60, "flow": 12000, "meter_types": ["Rotary (roots)"],
         "answers": {"rotary_roots.compensation": "Live",
                     "rotary_roots.live": "Eagle MPplusII Instrument",
                     "rotary_roots.eagle_type": "Rotary Corrector"}},
        {"part_number": "M.RT3M-175.FLG.CTR.NA.175.VDN.NA.NA.NA"},
    ),
    (
        # A 23M or larger cannot reach CTR - it is forced to a volume
        # corrector - so this is checked on a meter that still asks.
        "CTR takes the same NA / NA segments as CD",
        {"inlet": 60, "flow": 12000, "meter_types": ["Rotary (roots)"],
         "answers": {"rotary_roots.compensation": "Live",
                     "rotary_roots.live": "Eagle MPplusII Instrument",
                     "rotary_roots.eagle_type": "Rotary Corrector"}},
        {"part_number": "M.RT3M-175.FLG.CTR.NA.175.VDN.NA.NA.NA"},
    ),
    (
        # The 23M is a forced-Eagle meter, so CD is the only index it can
        # carry; the rule being checked here is the missing rating segment.
        "the 232 psi roots meter drops the rating segment entirely",
        {"inlet": 200, "flow": 250000, "meter_types": ["Rotary (roots)"]},
        {"model": "23M232",
         "part_number": "M.RT23M-232.FLG.CD.NA.VDN.NA.NA.NA"},
    ),
    (
        "a 175-rated meter on the same CD index keeps its rating segment",
        {"inlet": 60, "flow": 12000, "meter_types": ["Rotary (roots)"],
         "answers": {"rotary_roots.compensation": "Live",
                     "rotary_roots.live": "Eagle MPplusII Instrument",
                     "rotary_roots.eagle_type": "Volume Corrector"}},
        {"model": "3M175",
         "part_number": "M.RT3M-175.FLG.CD.NA.175.VDN.NA.NA.NA"},
    ),
    (
        "a 175-rated roots meter still carries its rating segment",
        {"inlet": 60, "flow": 12000, "meter_types": ["Rotary (roots)"],
         "answers": {"rotary_roots.compensation": "None",
                     "rotary_roots.radio": "No"}},
        {"model": "3M175",
         "part_number": "M.RT3M-175.FLG.TC.NA.175.VDN.NA.NA.NA"},
    ),
    (
        "roots IMC index -> CIR / ALK",
        {"inlet": 60, "flow": 12000, "meter_types": ["Rotary (roots)"],
         "answers": {"rotary_roots.compensation": "Live",
                     "rotary_roots.live": "IMC-W2-PTZ index"}},
        {"part_number": "M.RT3M-175.FLG.IMC-W2-PTZ.CIR.175.VDN.NA.ALK.NA"},
    ),
    (
        "roots with no compensation and no radio -> TC, NA / NA",
        {"inlet": 60, "flow": 12000, "meter_types": ["Rotary (roots)"],
         "answers": {"rotary_roots.compensation": "None",
                     "rotary_roots.radio": "No"}},
        {"part_number": "M.RT3M-175.FLG.TC.NA.175.VDN.NA.NA.NA"},
    ),
    (
        "turbo, no compensation, Bendix slot sensor",
        {"inlet": 300, "flow": 100000, "meter_types": ["Turbine"],
         "answers": {"turbine.compensation": "None",
                     "turbine.slot": "Bendix Plug In Connection"}},
        {"manufacturer": "Sensus", "model": "T-18",
         "part_number": "M.T-18.300.VDR.HF-SS-B.PLUG"},
    ),
    (
        "turbo, live compensation -> VCR with NA slot and connection",
        {"inlet": 300, "flow": 100000, "meter_types": ["Turbine"],
         "answers": {"turbine.compensation": "Live"}},
        {"part_number": "M.T-18.300.VCR.NA.NA"},
    ),
    (
        "RMG quotes rather than numbering",
        {"inlet": 60, "flow": 9000, "meter_types": ["Ultrasonic"],
         "answers": {"ultrasonic.compensation": "Live"}},
        {"manufacturer": "RMG", "model": "RSM200", "part_number": None,
         "quote_note": "contact Holland Supply for a quote"},
    ),
]

# Whole-result expectations that are not about one meter entry.
EXPECTED_TOP = [
    (
        "Eagle volume corrector on a live turbo, 300 psi -> P1 508",
        {"inlet": 300, "flow": 100000, "meter_types": ["Turbine"],
         "answers": {"turbine.compensation": "Live"}},
        lambda r: r["eagle"]["part_number"] == "I.MPP-MVC.508.N.N.N.TC.CVI.CCW.N.ALK.8X6",
    ),
    (
        "Eagle rotary corrector on live roots, 60 psi -> P1 102 and INTEG",
        {"inlet": 60, "flow": 12000, "meter_types": ["Rotary (roots)"],
         "answers": {"rotary_roots.compensation": "Live",
                     "rotary_roots.live": "Eagle MPplusII Instrument",
                     "rotary_roots.eagle_type": "Rotary Corrector"}},
        lambda r: r["eagle"]["part_number"] == "I.MPP-MRC.102.N.N.N.TC.INTEG.CCW.N.ALK.8X6",
    ),
    (
        "a CTR drive is paired with the rotary corrector instrument",
        {"inlet": 60, "flow": 12000, "meter_types": ["Rotary (roots)"],
         "answers": {"rotary_roots.compensation": "Live",
                     "rotary_roots.live": "Eagle MPplusII Instrument",
                     "rotary_roots.eagle_type": "Rotary Corrector"}},
        lambda r: r["part_numbers"] == [
            "M.RT3M-175.FLG.CTR.NA.175.VDN.NA.NA.NA",
            "I.MPP-MRC.102.N.N.N.TC.INTEG.CCW.N.ALK.8X6",
        ],
    ),
    (
        "a CD drive is paired with the volume corrector instrument",
        {"inlet": 60, "flow": 12000, "meter_types": ["Rotary (roots)"],
         "answers": {"rotary_roots.compensation": "Live",
                     "rotary_roots.live": "Eagle MPplusII Instrument",
                     "rotary_roots.eagle_type": "Volume Corrector"}},
        lambda r: r["part_numbers"] == [
            "M.RT3M-175.FLG.CD.NA.175.VDN.NA.NA.NA",
            "I.MPP-MVC.102.N.N.N.TC.CVI.CCW.N.ALK.8X6",
        ],
    ),
    (
        "the confirmed Eagle index raises no warning",
        {"inlet": 60, "flow": 12000, "meter_types": ["Rotary (roots)"],
         "answers": {"rotary_roots.compensation": "Live",
                     "rotary_roots.live": "Eagle MPplusII Instrument",
                     "rotary_roots.eagle_type": "Rotary Corrector"}},
        lambda r: not any("index token" in w for w in r["warnings"]),
    ),
    (
        "every Eagle transducer band, through a real sizing run",
        {"inlet": 1, "flow": 100000},   # payload unused; see the predicate
        lambda r: all(
            size_meters({
                "inlet": psi, "flow": 100000, "meter_types": ["Turbine"],
                "answers": {"turbine.compensation": "Live"},
            })["eagle"]["p1"] == want
            for psi, want in [
                (0.5, "10"), (9.99, "10"),
                (10, "51"), (40, "51"), (49.99, "51"),
                (50, "102"), (99.99, "102"),
                (100, "290"), (289.99, "290"),
                (290, "508"), (499.99, "508"),
                (500, "1050"), (1049.99, "1050"),
                (1050, "1450"), (1440, "1450"),
            ]
        ),
    ),
    (
        "1440 psi is accepted and 1441 is not",
        {"inlet": 1440, "flow": 100000},
        lambda r: r["ok"] is True
        and size_meters({"inlet": 1441, "flow": 100000})["ok"] is False,
    ),
    (
        "a 23M or larger roots meter asks nothing and completes at once",
        {"inlet": 200, "flow": 250000, "meter_types": ["Rotary (roots)"]},
        lambda r: r["stage"] == "complete" and r["questions"] == []
        and r["results"][0]["questions"] == [],
    ),
    (
        "every roots meter from the 23M up is forced, and none below it",
        {"inlet": 200, "flow": 250000},
        lambda r: [
            m for m in ["DR8C175", "DR11C175", "DR15C175", "DR2M175", "DR3M175",
                        "DR5M175", "DR7M175", "DR11M175", "DR16M175",
                        "DR23M232", "DR38M175", "DR56M175"]
            if roots_forced_eagle(m)
        ] == ["DR23M232", "DR38M175", "DR56M175"],
    ),
    (
        "a forced meter comes with a live Eagle volume corrector",
        {"inlet": 200, "flow": 250000, "meter_types": ["Rotary (roots)"]},
        lambda r: r["eagle"]["type"] == "Volume Corrector"
        and r["eagle"]["part_number"].startswith("I.MPP-MVC."),
    ),
    (
        "and says on screen what was assumed for it",
        {"inlet": 200, "flow": 250000, "meter_types": ["Rotary (roots)"]},
        lambda r: {f["label"]: f["value"] for f in r["results"][0]["fields"]}.get(
            "Pressure compensation"
        ) == "Live"
        and {f["label"]: f["value"] for f in r["results"][0]["fields"]}.get(
            "Correction"
        ) == "Eagle MPplusII Instrument",
    ),
    (
        "a 16M or smaller still asks for its compensation",
        {"inlet": 60, "flow": 14000, "meter_types": ["Rotary (roots)"]},
        lambda r: r["stage"] == "options"
        and r["questions"][0]["id"] == "rotary_roots.compensation",
    ),
    (
        "capacities are whole CFH, with no interpolated fraction left on show",
        # DD800 at 12.75 psi interpolates to exactly 3,133.5 CFH - a half-way
        # value, which also pins the rounding direction the two builds share.
        {"inlet": 12.75, "flow": 2000, "meter_types": ["Rotary (meter bar)"],
         "answers": {"rotary_meter_bar.ferrule": "30LT"}},
        lambda r: {f["label"]: f["value"] for f in r["results"][0]["fields"]}[
            "Meter Capacity (CFH)"
        ] == "3,134",
    ),
    (
        "a capacity in a failure reason is whole CFH too",
        {"inlet": 2, "flow": 500000},
        lambda r: all(
            "." not in e["reason"].split(" CFH")[0]
            for e in r["evaluations"] if e["reason"] and "capacity" in e["reason"]
        ),
    ),
    (
        "the Eagle transducer range carries its unit",
        {"inlet": 300, "flow": 100000, "meter_types": ["Turbine"],
         "answers": {"turbine.compensation": "Live"}},
        lambda r: {f["label"]: f["value"] for f in r["eagle"]["fields"]}[
            "Pressure Transducer"
        ] == "0-508 psi",
    ),
    (
        "the Eagle labels are capitalised and the case carries inch marks",
        {"inlet": 300, "flow": 100000, "meter_types": ["Turbine"],
         "answers": {"turbine.compensation": "Live"}},
        lambda r: [f["label"] for f in r["eagle"]["fields"]] == [
            "Manufacturer", "Model", "Corrector", "Rotation",
            "Pressure Transducer", "Temperature Probe", "Battery",
            "Cellular Communication", "Case",
        ]
        and {f["label"]: f["value"] for f in r["eagle"]["fields"]}["Case"] == '8"x6"',
    ),
    (
        "rotation reads as a direction alone, for both correctors",
        {"inlet": 60, "flow": 12000, "meter_types": ["Rotary (roots)"],
         "answers": {"rotary_roots.compensation": "Live",
                     "rotary_roots.live": "Eagle MPplusII Instrument",
                     "rotary_roots.eagle_type": "Rotary Corrector"}},
        lambda r: {f["label"]: f["value"] for f in r["eagle"]["fields"]}[
            "Rotation"
        ] == "Counterclockwise",
    ),
    (
        "the five meters with a standard pulse output all report it",
        {"inlet": 1, "flow": 100},   # payload unused; see the predicate
        lambda r: all(
            {f["label"]: f["value"] for f in [
                e for e in size_meters(payload)["results"] if e.get("available")
            ][0]["fields"]}.get("Pulse Output") == "Included"
            for payload in [
                # Sonix 600, Sonix 880, D800, D1000, 10C25 in turn. The two
                # Dresser meter-bar models share a capacity above 1 psi, so
                # the D1000 is reached at 0.25 psi with a load the D800 cannot
                # carry.
                {"inlet": 10, "flow": 1800, "meter_types": ["Ultrasonic"],
                 "answers": {"ultrasonic.ferrule": "30LT"}},
                {"inlet": 10, "flow": 2000, "meter_types": ["Ultrasonic"],
                 "answers": {"ultrasonic.ferrule": "30LT"}},
                {"inlet": 20, "flow": 3900, "meter_types": ["Rotary (meter bar)"],
                 "answers": {"rotary_meter_bar.ferrule": "45LT"}},
                {"inlet": 0.25, "flow": 900, "meter_types": ["Rotary (meter bar)"],
                 "answers": {"rotary_meter_bar.ferrule": "45LT"}},
                {"inlet": 20, "flow": 2000, "meter_types": ["Rotary (straight pipe)"],
                 "answers": {"rotary_straight_pipe.connection": "1-1/2"}},
            ]
        ),
    ),
    (
        "it sits directly below the size",
        {"inlet": 20, "flow": 3900, "meter_types": ["Rotary (meter bar)"],
         "answers": {"rotary_meter_bar.ferrule": "45LT"}},
        lambda r: [f["label"] for f in r["results"][0]["identity_fields"]][:4]
        == ["Manufacturer", "Model", "Size", "Pulse Output"],
    ),
    (
        "the R275 does not claim one",
        {"inlet": 0.25, "flow": 250, "meter_types": ["Diaphragm"],
         "answers": {"diaphragm.ferrule": "1-1/4"}},
        lambda r: not any(f["label"] == "Pulse Output"
                          for f in r["results"][0]["fields"]),
    ),
    (
        "and a Sonix IQ still asks rather than assuming",
        {"inlet": 2, "flow": 380, "meter_types": ["Sonix IQ"],
         "answers": {"sonix_iq.ferrule": "20LT"}},
        lambda r: any(q["id"] == "sonix_iq.pulse" for q in r["questions"])
        and not any(f["label"] == "Pulse Output"
                    for f in r["results"][0]["identity_fields"]),
    ),
    (
        "no Eagle when a turbo has no compensation",
        {"inlet": 300, "flow": 100000, "meter_types": ["Turbine"],
         "answers": {"turbine.compensation": "None", "turbine.slot": "None"}},
        lambda r: r["eagle"] is None,
    ),
    (
        "tier 1: an R275 job is offered diaphragm and Sonix IQ",
        {"inlet": 0.25, "flow": 250},
        lambda r: r["meter_type_question"]["options"] == ["Diaphragm", "Sonix IQ"],
    ),
    (
        "tier 5: nothing small works, so roots / ultrasonic / turbine",
        {"inlet": 60, "flow": 9000},
        lambda r: r["meter_type_question"]["options"]
        == ["Rotary (roots)", "Ultrasonic", "Turbine"],
    ),
    (
        "an SR275 is not offered above its 5 psi rating",
        {"inlet": 6, "flow": 250},
        lambda r: "Diaphragm" not in r["meter_type_question"]["options"],
    ),
    (
        "10% oversize applies to turbo: 100,000 CFH needs 110,000 of capacity",
        {"inlet": 300, "flow": 100000, "meter_types": ["Turbine"],
         "answers": {"turbine.compensation": "Live"}},
        lambda r: abs(r["results"][0]["required_cfh"] - 110000) < 1e-6,
    ),
    (
        "and to roots, and to nothing else",
        {"inlet": 60, "flow": 12000},
        lambda r: {
            e["family"]: round(e["oversize"], 6) for e in r["evaluations"]
        } == {"small": 0.0, "roots": 0.1, "turbo": 0.1, "rmg": 0.0},
    ),
    (
        "no oversize on small meters: an SR275 covers exactly its capacity",
        {"inlet": 0.25, "flow": 275, "meter_types": ["Diaphragm"],
         "answers": {"diaphragm.ferrule": "10LT"}},
        lambda r: r["selected"] is True,
    ),
    (
        "a turbo minimum capacity rules out an oversized meter at low flow",
        # T18 needs at least 1,200 CFH at 0.25 psi, so a 600 CFH load at high
        # pressure must not land on it.
        {"inlet": 1000, "flow": 900, "meter_types": ["Turbine"], "answers": {}},
        lambda r: not any(x.get("available") and x.get("meter") == "T18"
                          for x in r["results"]),
    ),
    (
        "the inlet pressure alone decides the rating check",
        {"inlet": 26, "flow": 2000},
        lambda r: all(not e["works"] for e in r["evaluations"]
                      if e["family"] == "small"),
    ),
    (
        "a MAOP key in the payload is ignored rather than sized against",
        {"inlet": 5, "flow": 1500, "maop": 250},
        lambda r: r["meter_type_question"]["options"]
        == size_meters({"inlet": 5, "flow": 1500})["meter_type_question"]["options"],
    ),
    (
        "the result carries no MAOP anywhere",
        {"inlet": 5, "flow": 1500},
        lambda r: "maop_psi" not in r["converted"]
        and not any("MAOP" in s["label"] for s in r["summary"]),
    ),
    (
        "interpolation: DD800 at 12.5 psi sits midway between 2,820 and 3,390",
        {"inlet": 12.5, "flow": 2000, "meter_types": ["Rotary (meter bar)"],
         "answers": {"rotary_meter_bar.ferrule": "30LT"}},
        lambda r: abs(r["results"][0]["capacity_cfh"] - 3105) < 1e-6,
    ),
    (
        "unit conversion: 1 bar of inlet reads as 14.5038 psi",
        {"inlet": 1, "inlet_units": "bar", "flow": 1000},
        lambda r: abs(r["converted"]["inlet_psi"] - 14.5038) < 1e-9,
    ),
    (
        "unit conversion: 1,000,000 BTUH is 1,000 CFH",
        {"inlet": 5, "flow": 1000000, "flow_units": "BTUH"},
        lambda r: abs(r["converted"]["flow_cfh"] - 1000) < 1e-9,
    ),
    (
        "unit conversion: 1 CMH is 35.3147 CFH",
        {"inlet": 5, "flow": 1, "flow_units": "CMH"},
        lambda r: abs(r["converted"]["flow_cfh"] - 35.3147) < 1e-9,
    ),
    (
        "the meter type question comes first and asks for a single choice",
        {"inlet": 5, "flow": 300},
        lambda r: r["stage"] == "meter_type"
        and r["questions"][0]["id"] == "meter_type"
        and r["questions"][0]["type"] == "single_select",
    ),
    (
        "only one meter type is ever sized, whatever the caller sends",
        {"inlet": 10, "flow": 2000,
         "meter_types": ["Rotary (meter bar)", "Ultrasonic"]},
        lambda r: len(r["results"]) == 1,
    ),
    (
        "the extra type is taken in tier order and the rest called out",
        {"inlet": 10, "flow": 2000,
         "meter_types": ["Rotary (meter bar)", "Ultrasonic"]},
        # Ultrasonic comes first in this tier, so it wins regardless of the
        # order the caller listed them in.
        lambda r: r["results"][0]["type"] == "Ultrasonic"
        and any("Only one meter type" in w for w in r["warnings"]),
    ),
    (
        "options stage asks the ferrule and holds back the part number",
        {"inlet": 5, "flow": 300, "meter_types": ["Diaphragm"]},
        lambda r: r["stage"] == "options"
        and r["questions"][0]["id"] == "diaphragm.ferrule"
        and r["part_numbers"] == [],
    ),
    (
        "a flow beyond every table is refused, not sized",
        {"inlet": 400, "flow": 50000000},
        lambda r: r["ok"] is True and r["selected"] is False and not r["results"],
    ),
    (
        "a zero flow is an input error",
        {"inlet": 5, "flow": 0},
        lambda r: r["ok"] is False and r["errors"],
    ),
]


def check_expected():
    """-> list of failures."""
    failures = []
    for label, payload, expect in EXPECTED:
        result = size_meters(payload)
        entries = [r for r in result.get("results", []) if r.get("available")]
        if not entries:
            failures.append(f"{label}: no meter selected ({result.get('message')})")
            continue
        entry = entries[0]
        for field, want in expect.items():
            got = entry.get(field)
            if got != want:
                failures.append(f"{label}: {field} want {want!r} got {got!r}")
    for label, payload, predicate in EXPECTED_TOP:
        result = size_meters(payload)
        try:
            ok = bool(predicate(result))
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{label}: raised {exc}")
            continue
        if not ok:
            failures.append(f"{label}: predicate failed")
    return failures


# --------------------------------------------------------------------------
# pytest entry points
# --------------------------------------------------------------------------

def test_expected_outputs():
    failures = check_expected()
    assert not failures, "\n".join(failures)


def test_python_js_parity():
    cases = json.loads(CASES.read_text())
    py, js = run_py(cases), run_js(cases)
    assert len(py) == len(js)
    failures = []
    for i, (a, b) in enumerate(zip(py, js)):
        d = diff(a, b, f"case[{i}]")
        if d:
            failures.append(
                f"case {i} {json.dumps(cases[i])}\n  " + "\n  ".join(d[:8])
            )
    assert not failures, (
        f"{len(failures)} of {len(cases)} cases differ:\n\n"
        + "\n\n".join(failures[:10])
    )


def main():
    print(f"loading {CASES.relative_to(REPO)}")
    cases = json.loads(CASES.read_text())
    print(f"{len(cases)} cases\n")

    failures = check_expected()
    if failures:
        print(f"FAIL {len(failures)} fixed expectation(s):")
        for f in failures:
            print("  -", f)
        print()
    else:
        print(f"OK   {len(EXPECTED) + len(EXPECTED_TOP)} fixed expectations")

    py, js = run_py(cases), run_js(cases)
    mismatches = []
    for i, (a, b) in enumerate(zip(py, js)):
        d = diff(a, b, f"case[{i}]")
        if d:
            mismatches.append((i, cases[i], d))

    if mismatches:
        print(f"FAIL {len(mismatches)} of {len(cases)} cases differ between "
              "Python and JavaScript:\n")
        for i, case, d in mismatches[:10]:
            print(f"  case {i}: {json.dumps(case)}")
            for line in d[:8]:
                print("    ", line)
            print()
        return 1

    print(f"OK   {len(cases)} cases identical in Python and JavaScript")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
