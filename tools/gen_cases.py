#!/usr/bin/env python3
"""Regenerate tests/cases.json.

The cases are the inputs only -- no expected output. The parity test runs both
implementations over them and compares the two results to each other, so this
file never needs to record what the right answer is; it only needs to reach
every branch. Run it after adding a rule, and commit the result.

    python tools/gen_cases.py
"""

import itertools
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from algorithm.meter_sizing import size_meters  # noqa: E402

OUT = REPO / "tests" / "cases.json"


def sweep():
    """Inputs alone: unit combinations, tier boundaries, and invalid values."""
    cases = []

    # Every pressure unit against every flow unit, at a load each can express.
    for punit, pval in [("psi", 5), ("in wc", 7), ("oz", 8), ("bar", 0.5), ("kPa", 40)]:
        for funit, fval in [("CFH", 1500), ("BTUH", 1500000), ("CMH", 42)]:
            cases.append({"inlet": pval, "inlet_units": punit,
                          "flow": fval, "flow_units": funit})

    # A pressure and flow grid that walks every tier and falls off both ends.
    pressures = [0.1, 0.25, 1, 2, 3.5, 5, 7.5, 10, 15, 20, 25, 26,
                 60, 100, 175, 176, 232, 290, 300, 720, 1000,
                 1049, 1050, 1200, 1440]
    flows = [1, 250, 275, 400, 900, 1500, 2000, 3900, 4600, 9000,
             25000, 120000, 500000, 3000000, 50000000]
    for p, f in itertools.product(pressures, flows):
        cases.append({"inlet": p, "inlet_units": "psi", "flow": f,
                      "flow_units": "CFH"})

    # Just above and just below each family's pressure rating, where the
    # rating check decides whether a meter is offered at all.
    for p, f in [(5, 1500), (5.01, 1500), (10, 400), (10.01, 400),
                 (20, 2000), (20.01, 2000), (25, 2000), (25.01, 2000),
                 (175, 25000), (175.01, 25000), (232, 250000), (232.01, 250000),
                 (290, 50000), (290.01, 50000), (1440, 200000), (1440, 100000)]:
        cases.append({"inlet": p, "inlet_units": "psi", "flow": f,
                      "flow_units": "CFH"})

    # An unexpected key in the payload must be ignored, not crash: MAOP was
    # an input in an early draft and a stale caller may still send it.
    cases.append({"inlet": 5, "flow": 1500, "maop": 250})

    # Rejected inputs.
    cases.extend([
        {"inlet": 0, "flow": 100},
        {"inlet": 5, "flow": 0},
        {"inlet": -1, "flow": 100},
        {"inlet": 1441, "flow": 100},
        {"inlet": 5, "flow": 100000001},
        {"inlet": 5, "flow": 100, "inlet_units": "furlongs"},
        {"inlet": 5, "flow": 100, "flow_units": "gallons"},
        {"inlet": None, "flow": None},
        {},
    ])
    return cases


# Answer sets per meter type, covering every conditional branch of the option
# tree. Each is applied to any base case whose tier offers that type.
ANSWER_SETS = {
    "Diaphragm": [
        {},
        {"diaphragm.ferrule": "10LT"},
        {"diaphragm.ferrule": "1-1/4"},
        {"diaphragm.ferrule": "45LT"},
    ],
    "Sonix IQ": [
        {},
        {"sonix_iq.ferrule": "20LT"},
        {"sonix_iq.ferrule": "20LT", "sonix_iq.pulse": "Yes"},
        {"sonix_iq.ferrule": "1A", "sonix_iq.pulse": "No"},
    ],
    "Ultrasonic": [
        {},
        {"ultrasonic.ferrule": "30LT"},
        {"ultrasonic.ferrule": "45LT"},
        # The top tier resolves Ultrasonic to RMG, which asks compensation
        # instead of a ferrule.
        {"ultrasonic.compensation": "None"},
        {"ultrasonic.compensation": "Fix-Factored"},
        {"ultrasonic.compensation": "Live"},
    ],
    "Rotary (meter bar)": [
        {},
        {"rotary_meter_bar.ferrule": "30LT"},
        {"rotary_meter_bar.ferrule": "1-1/2"},
    ],
    "Rotary (straight pipe)": [
        {},
        {"rotary_straight_pipe.connection": "45LT"},
        {"rotary_straight_pipe.connection": "1-1/2"},
    ],
    "Rotary (roots)": [
        {},
        # None -> no radio -> index fixed at TC
        {"rotary_roots.compensation": "None", "rotary_roots.radio": "No"},
        # None -> radio -> each index
        {"rotary_roots.compensation": "None", "rotary_roots.radio": "Yes",
         "rotary_roots.index": "TC/AMR"},
        {"rotary_roots.compensation": "None", "rotary_roots.radio": "Yes",
         "rotary_roots.index": "ETC"},
        {"rotary_roots.compensation": "None", "rotary_roots.radio": "Yes",
         "rotary_roots.index": "ES3"},
        # Fix-factored -> each index, including the Eagle branch
        {"rotary_roots.compensation": "Fix-Factored", "rotary_roots.index": "ETC"},
        {"rotary_roots.compensation": "Fix-Factored", "rotary_roots.index": "ES3"},
        {"rotary_roots.compensation": "Fix-Factored", "rotary_roots.index": "IMC-W2-PTZ"},
        {"rotary_roots.compensation": "Fix-Factored",
         "rotary_roots.index": "Eagle MPplusII Instrument"},
        {"rotary_roots.compensation": "Fix-Factored",
         "rotary_roots.index": "Eagle MPplusII Instrument",
         "rotary_roots.eagle_type": "Volume Corrector"},
        {"rotary_roots.compensation": "Fix-Factored",
         "rotary_roots.index": "Eagle MPplusII Instrument",
         "rotary_roots.eagle_type": "Rotary Corrector"},
        # Live -> IMC index, and the Eagle branch both ways
        {"rotary_roots.compensation": "Live", "rotary_roots.live": "IMC-W2-PTZ index"},
        {"rotary_roots.compensation": "Live",
         "rotary_roots.live": "Eagle MPplusII Instrument"},
        {"rotary_roots.compensation": "Live",
         "rotary_roots.live": "Eagle MPplusII Instrument",
         "rotary_roots.eagle_type": "Volume Corrector"},
        {"rotary_roots.compensation": "Live",
         "rotary_roots.live": "Eagle MPplusII Instrument",
         "rotary_roots.eagle_type": "Rotary Corrector"},
    ],
    "Turbine": [
        {},
        {"turbine.compensation": "None", "turbine.slot": "None"},
        {"turbine.compensation": "None", "turbine.slot": "Conduit Connection"},
        {"turbine.compensation": "None", "turbine.slot": "Bendix Plug In Connection"},
        {"turbine.compensation": "Fix-Factored"},
        {"turbine.compensation": "Live"},
    ],
}

# One base case per tier, chosen so the tier is the one that fires.
TIER_BASES = [
    {"inlet": 0.25, "flow": 250},        # sr275
    {"inlet": 2, "flow": 500},           # siq
    {"inlet": 10, "flow": 2000},         # sonix
    {"inlet": 20, "flow": 3900},         # dresser
    {"inlet": 60, "flow": 9000},         # large
    {"inlet": 300, "flow": 400000},      # large, turbo territory
    {"inlet": 1000, "flow": 3000000},    # large, top of the turbo table
]


def answer_cases():
    cases = []
    for base in TIER_BASES:
        probe = size_meters(dict(base, inlet_units="psi", flow_units="CFH"))
        if not probe.get("ok"):
            continue
        offered = probe["meter_type_question"]["options"]

        # Each offered type on its own, through every answer branch.
        for mtype in offered:
            for answers in ANSWER_SETS.get(mtype, [{}]):
                cases.append(dict(base, inlet_units="psi", flow_units="CFH",
                                  meter_types=[mtype], answers=answers))

        # Every offered type at once. The question is a single choice, so
        # this exercises the "caller sent too many" path: the first in tier
        # order is sized and the rest are called out.
        merged = {}
        for mtype in offered:
            sets = ANSWER_SETS.get(mtype, [{}])
            merged.update(sets[-1])
        cases.append(dict(base, inlet_units="psi", flow_units="CFH",
                          meter_types=list(offered), answers=merged))
        cases.append(dict(base, inlet_units="psi", flow_units="CFH",
                          meter_types=list(reversed(offered)), answers=merged))

        # A type the tier does not offer, which must be dropped with a warning.
        bogus = [t for t in ANSWER_SETS if t not in offered]
        if bogus:
            cases.append(dict(base, inlet_units="psi", flow_units="CFH",
                              meter_types=[bogus[0]], answers={}))
            cases.append(dict(base, inlet_units="psi", flow_units="CFH",
                              meter_types=[offered[0], bogus[0]], answers=merged))

    # Two Eagle types requested at once, from the tier that can do it.
    cases.append({
        "inlet": 60, "inlet_units": "psi", "flow": 9000, "flow_units": "CFH",
        "meter_types": ["Rotary (roots)", "Turbine"],
        "answers": {
            "rotary_roots.compensation": "Live",
            "rotary_roots.live": "Eagle MPplusII Instrument",
            "rotary_roots.eagle_type": "Rotary Corrector",
            "turbine.compensation": "Live",
        },
    })

    # Every Eagle transducer band.
    for p in [5, 9.9, 10, 49, 50, 99, 100, 289, 290, 499, 500, 1049, 1000]:
        cases.append({
            "inlet": p, "inlet_units": "psi", "flow": 200000, "flow_units": "CFH",
            "meter_types": ["Turbine"], "answers": {"turbine.compensation": "Live"},
        })

    # Every turbo ANSI band.
    for p in [1, 149, 150, 274, 275, 719, 720, 1000]:
        cases.append({
            "inlet": p, "inlet_units": "psi", "flow": 100000, "flow_units": "CFH",
            "meter_types": ["Turbine"],
            "answers": {"turbine.compensation": "None", "turbine.slot": "None"},
        })

    # The 232-rated roots model is the only one above 175 psi, so it is the
    # only path that puts a rating other than 175 in a part number.
    for p in [175, 200, 232]:
        cases.append({
            "inlet": p, "inlet_units": "psi", "flow": 250000, "flow_units": "CFH",
            "meter_types": ["Rotary (roots)"],
            "answers": {"rotary_roots.compensation": "None", "rotary_roots.radio": "No"},
        })

    # The 23M and larger ask no questions. Sized with an empty answer set, and
    # again with answers that contradict the forced choice - which must be
    # ignored rather than followed.
    for flow in [250000, 400000, 700000]:
        cases.append({
            "inlet": 100, "inlet_units": "psi", "flow": flow, "flow_units": "CFH",
            "meter_types": ["Rotary (roots)"], "answers": {},
        })
        cases.append({
            "inlet": 100, "inlet_units": "psi", "flow": flow, "flow_units": "CFH",
            "meter_types": ["Rotary (roots)"],
            "answers": {"rotary_roots.compensation": "None",
                        "rotary_roots.radio": "No",
                        "rotary_roots.eagle_type": "Rotary Corrector"},
        })

    # Pressures that interpolate onto an exact half, where the two builds'
    # rounding has to agree.
    for p, f in [(12.75, 2000), (3.5, 900), (7.5, 1500), (17.5, 3000)]:
        cases.append({"inlet": p, "inlet_units": "psi", "flow": f,
                      "flow_units": "CFH"})

    return cases


def main():
    cases = sweep() + answer_cases()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(cases, indent=2) + "\n")
    print(f"wrote {OUT.relative_to(REPO)}  ({len(cases)} cases)")


if __name__ == "__main__":
    main()
