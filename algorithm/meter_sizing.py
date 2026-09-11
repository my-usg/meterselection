"""Holland Supply meter sizing algorithm.

THIS FILE IS THE SOURCE OF TRUTH. The JavaScript in dist/ is generated from
src/js/meter_sizing.js by tools/build.py and is verified against this module
by tests/test_parity.py, so the browser block and the chatbot cannot drift.
Change the rules here and in src/js/meter_sizing.js together.

WHY THE API LOOKS LIKE THIS
---------------------------
Sizing a regulator is one shot: inputs in, part number out. Sizing a meter is
not. Once the tool knows which meters have the capacity, it has to ask the
customer which meter *type* they want, and then a different set of follow-up
questions depending on what they picked -- ferrule size, pulse output,
pressure compensation, index, and so on.

Rather than hold that conversation in the web page (where the chatbot could
not reuse it) this module is a stateless resolver. Every call takes the whole
answer set collected so far and returns the questions that are still
outstanding:

    size_meters({inlet: 5, flow: 1200, ...})
        -> stage "meter_type", one question: which meter type?

    size_meters({..., meter_types: ["Diaphragm"]})
        -> stage "options", one question: ferrule size?

    size_meters({..., meter_types: ["Diaphragm"], answers: {...}})
        -> stage "complete", with the selection and part numbers

The HTML block renders each returned question as a widget and calls again on
change. The chatbot asks each returned question as a turn and calls again with
the reply. Same function, same rules, no server-side session.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

# --------------------------------------------------------------------------
# Capacity tables
# --------------------------------------------------------------------------
# data/capacities.json is generated from data/capacities.xlsx by
# tools/extract_capacities.py. Cells that read "N/A" (small meters) or 0
# (roots, above a model's rating) are dropped there, so the last pressure
# stored for a model IS its pressure limit.

_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "capacities.json"

with _DATA_PATH.open() as fh:
    CAPACITIES = json.load(fh)

FAMILIES = CAPACITIES["families"]

# Left-to-right order in each sheet is ascending capacity, which is what
# "choose the smallest meter from the tab" means.
BY_MODEL = {
    m["model"]: dict(m, family=key)
    for key, fam in FAMILIES.items()
    for m in fam["meters"]
}


# --------------------------------------------------------------------------
# Unit conversion
# --------------------------------------------------------------------------
# Everything is converted to psi and CFH before any table is touched.

PRESSURE_TO_PSI = {
    "psi": 1.0,
    "in wc": 1.0 / 28.0,        # 28 in wc = 1 psi
    "oz": 1.0 / 16.0,           # 16 oz = 1 psi
    "bar": 14.5038,
    "kpa": 1.0 / 6.89476,
}

FLOW_TO_CFH = {
    "cfh": 1.0,
    "cmh": 35.3147,
    "btuh": 1.0 / 1000.0,       # 1000 BTUH = 1 CFH
}

PRESSURE_UNITS = ["psi", "in wc", "oz", "bar", "kPa"]

# The accepted range for the entered figure, in whatever units it is entered
# in. 1440 is the highest pressure the capacity tables cover (the top row of
# the turbo tab); above it nothing is rated, so there is nothing to size.
MAX_INLET = 1440
MAX_FLOW = 100000000
FLOW_UNITS = ["CFH", "BTUH", "CMH"]


def to_psi(value, units):
    f = PRESSURE_TO_PSI.get(str(units).strip().lower())
    if f is None:
        raise ValueError(f"Unknown pressure units: {units}")
    return float(value) * f


def to_cfh(value, units):
    f = FLOW_TO_CFH.get(str(units).strip().lower())
    if f is None:
        raise ValueError(f"Unknown flow units: {units}")
    return float(value) * f


# --------------------------------------------------------------------------
# Capacity lookup
# --------------------------------------------------------------------------

def rating_psi(model):
    """The highest inlet pressure the model is rated for."""
    return BY_MODEL[model]["points"][-1]["p"]


def interpolate(model, psi, key="max"):
    """Linear interpolation of a model's capacity table at `psi`.

    Below the first tabulated pressure the first row is used unchanged. The
    tables start at 0.25 psi (about 7 in wc) and a good many jobs come in at
    or under that, so clamping keeps them sizeable; `size_meters` raises a
    warning whenever it happens so the number is never quietly assumed.
    """
    pts = BY_MODEL[model]["points"]
    if key not in pts[0]:
        return None
    if psi <= pts[0]["p"]:
        return pts[0][key]
    if psi >= pts[-1]["p"]:
        return pts[-1][key]
    for i in range(1, len(pts)):
        hi = pts[i]
        if psi <= hi["p"]:
            lo = pts[i - 1]
            span = hi["p"] - lo["p"]
            if span == 0:
                return hi[key]
            frac = (psi - lo["p"]) / span
            return lo[key] + frac * (hi[key] - lo[key])
    return pts[-1][key]


def evaluate(model, inlet_psi, flow_cfh):
    """Does this meter work for the job? -> dict, always, with the reason.

    A meter works when
      * it is rated for the inlet pressure,
      * its interpolated capacity covers the flow plus the family's oversize
        allowance (0 for small meters and RMG, 10% for roots and turbo), and
      * its minimum capacity, if it has one, is at or below the flow.
    """
    spec = BY_MODEL[model]
    fam = FAMILIES[spec["family"]]
    oversize = fam["oversize"]
    limit = rating_psi(model)
    required = flow_cfh * (1.0 + oversize)

    result = {
        "model": model,
        "family": spec["family"],
        "size": spec["size"],
        "oversize": oversize,
        "rating_psi": limit,
        "required_cfh": required,
        "capacity_cfh": None,
        "min_capacity_cfh": None,
        "works": False,
        "reason": None,
    }

    # The pressure check comes first: interpolating a capacity for a pressure
    # the meter is not rated for would produce a number nobody should see.
    if inlet_psi > limit + 1e-9:
        result["reason"] = (
            f"rated to {_fmt(limit)} psi, below the {_fmt(inlet_psi)} psi inlet pressure"
        )
        return result

    cap = interpolate(model, inlet_psi, "max")
    result["capacity_cfh"] = cap
    if spec["has_min"]:
        result["min_capacity_cfh"] = interpolate(model, inlet_psi, "min") or 0.0

    if cap + 1e-9 < required:
        result["reason"] = (
            f"capacity {_fmt_cfh(cap)} CFH is below the {_fmt_cfh(required)} CFH required"
            + (f" ({int(oversize * 100)}% oversize)" if oversize else "")
        )
        return result

    min_cap = result["min_capacity_cfh"]
    if min_cap and min_cap > flow_cfh + 1e-9:
        result["reason"] = (
            f"minimum capacity {_fmt_cfh(min_cap)} CFH is above the "
            f"{_fmt_cfh(flow_cfh)} CFH load"
        )
        return result

    result["works"] = True
    return result


def _fmt(n):
    """Numbers for customer-facing text: thousands separated, no noise."""
    if n is None:
        return ""
    n = float(n)
    if abs(n - round(n)) < 0.005:
        return f"{int(round(n)):,}"
    return f"{n:,.2f}".rstrip("0").rstrip(".")


def _fmt_cfh(n):
    """Capacities, always to the whole CFH.

    Interpolating between two tabulated rows lands on fractions - 2,698.5 CFH
    and the like - and a fraction of a cubic foot per hour is noise next to a
    meter's rating. `_fmt` is left alone because it also formats the entered
    pressure and flow, where a decimal the customer typed has to survive.

    floor(n + 0.5) rather than round(): Python's round() is round-half-to-even,
    so it takes 892.5 down to 892 while JavaScript's Math.round takes it up to
    893. Interpolation lands on exact halves often enough that the two builds
    disagreed on real capacities. Capacities are never negative, so this
    matches Math.round throughout the range that occurs.
    """
    if n is None:
        return ""
    return f"{math.floor(float(n) + 0.5):,}"


# --------------------------------------------------------------------------
# Meter identity
# --------------------------------------------------------------------------

MANUFACTURER = {
    "SR275": "Sensus",
    "SIQ250": "Sensus",
    "SIQ425": "Sensus",
    "Sonix600": "Sensus",
    "Sonix880": "Sensus",
    "DD800": "Dresser",
    "DD1000": "Dresser",
    "DD10C25": "Dresser",
}

MODEL_NAME = {
    "SR275": "R275",
    "SIQ250": "Sonix IQ 250",
    "SIQ425": "Sonix IQ 425",
    "Sonix600": "Sonix 600",
    "Sonix880": "Sonix 880",
    "DD800": "D800",
    "DD1000": "D1000",
    "DD10C25": "10C25",
}

FAMILY_MANUFACTURER = {"roots": "Dresser", "turbo": "Sensus", "rmg": "RMG"}


def display_model(model):
    """The catalogue name for a meter code.

    Small meters are a lookup. The three tabbed families follow a pattern:
    roots DR8C175 -> 8C175, turbo T18 -> T-18, and every RMG column is the
    one model RSM200 in a different line size.
    """
    if model in MODEL_NAME:
        return MODEL_NAME[model]
    fam = BY_MODEL[model]["family"]
    if fam == "roots":
        return model[2:] if model.startswith("DR") else model
    if fam == "turbo":
        return "T-" + model[1:] if model.startswith("T") else model
    if fam == "rmg":
        return "RSM200"
    return model


def display_manufacturer(model):
    if model in MANUFACTURER:
        return MANUFACTURER[model]
    return FAMILY_MANUFACTURER.get(BY_MODEL[model]["family"], "")


# --------------------------------------------------------------------------
# Meter type tiers
# --------------------------------------------------------------------------
# Which meter types the customer is offered depends on how far up the range
# the job sits, and the offered types are the rung it lands on plus the next
# one up. "Ultrasonic" means Sonix 600/880 in the middle tiers and RMG in the
# top tier, which is why resolution is stored per tier rather than globally.

DIAPHRAGM = "Diaphragm"
SONIX_IQ = "Sonix IQ"
ULTRASONIC = "Ultrasonic"
ROTARY_BAR = "Rotary (meter bar)"
ROTARY_PIPE = "Rotary (straight pipe)"
ROTARY_ROOTS = "Rotary (roots)"
TURBINE = "Turbine"

SLUG = {
    DIAPHRAGM: "diaphragm",
    SONIX_IQ: "sonix_iq",
    ULTRASONIC: "ultrasonic",
    ROTARY_BAR: "rotary_meter_bar",
    ROTARY_PIPE: "rotary_straight_pipe",
    ROTARY_ROOTS: "rotary_roots",
    TURBINE: "turbine",
}

# ("pick", [models in preference order]) tries each in turn.
# ("smallest", family)                   walks the tab left to right.
_PICK_SIQ = ("pick", ["SIQ250", "SIQ425"])
_PICK_SONIX = ("pick", ["Sonix600", "Sonix880"])
_PICK_DD = ("pick", ["DD800", "DD1000"])

TIERS = [
    {
        "id": "sr275",
        "trigger": ["SR275"],
        "types": [DIAPHRAGM, SONIX_IQ],
        "resolve": {DIAPHRAGM: ("pick", ["SR275"]), SONIX_IQ: _PICK_SIQ},
    },
    {
        "id": "siq",
        "trigger": ["SIQ250", "SIQ425"],
        "types": [SONIX_IQ, ULTRASONIC],
        "resolve": {SONIX_IQ: _PICK_SIQ, ULTRASONIC: _PICK_SONIX},
    },
    {
        "id": "sonix",
        "trigger": ["Sonix600", "Sonix880"],
        "types": [ULTRASONIC, ROTARY_BAR, ROTARY_PIPE],
        "resolve": {
            ULTRASONIC: _PICK_SONIX,
            ROTARY_BAR: _PICK_DD,
            ROTARY_PIPE: ("pick", ["DD10C25"]),
        },
    },
    {
        "id": "dresser",
        "trigger": ["DD800", "DD1000"],
        "types": [ROTARY_BAR, ROTARY_PIPE, ROTARY_ROOTS],
        "resolve": {
            ROTARY_BAR: _PICK_DD,
            ROTARY_PIPE: ("pick", ["DD10C25"]),
            ROTARY_ROOTS: ("smallest", "roots"),
        },
    },
    {
        # The top tier is the fallback: nothing in the small-meter tab works.
        "id": "large",
        "trigger": None,
        "types": [ROTARY_ROOTS, ULTRASONIC, TURBINE],
        "resolve": {
            ROTARY_ROOTS: ("smallest", "roots"),
            ULTRASONIC: ("smallest", "rmg"),
            TURBINE: ("smallest", "turbo"),
        },
    },
]


def resolve(rule, evals):
    """Apply a tier's resolution rule -> model code, or None if none works."""
    kind, arg = rule
    if kind == "pick":
        for model in arg:
            if evals[model]["works"]:
                return model
        return None
    for meter in FAMILIES[arg]["meters"]:
        if evals[meter["model"]]["works"]:
            return meter["model"]
    return None


# --------------------------------------------------------------------------
# Option questions
# --------------------------------------------------------------------------
# One question spec per meter or family. Ferrule lists differ by meter (only
# the R275 offers 45LT alongside the 1A and 1-1/4 options), so they are held
# per meter code rather than shared.

FERRULES = {
    "SR275": ["10LT", "20LT", "30LT", "45LT", "1A", "1-1/4"],
    "SIQ250": ["10LT", "20LT", "30LT", "1A", "1-1/4"],
    "SIQ425": ["10LT", "20LT", "30LT", "1A", "1-1/4"],
    "Sonix600": ["20LT", "30LT", "45LT"],
    "Sonix880": ["20LT", "30LT", "45LT"],
    "DD800": ["30LT", "45LT", "1-1/2"],
    "DD1000": ["30LT", "45LT", "1-1/2"],
}

CONNECTIONS = {"DD10C25": ["30LT", "45LT", "1-1/2"]}

# Meters that ship with a pulse output as standard, reported as a fact rather
# than asked about. The Sonix IQ pair is deliberately absent: theirs is an
# option, so it stays a yes/no question (see `option_questions`).
PULSE_OUTPUT_INCLUDED = ("Sonix600", "Sonix880", "DD800", "DD1000", "DD10C25")

COMPENSATION = ["None", "Fix-Factored", "Live"]
EAGLE_INSTRUMENT = "Eagle MPplusII Instrument"
IMC = "IMC-W2-PTZ"
EAGLE_TYPES = ["Volume Corrector", "Rotary Corrector"]


# Roots meters from the 23M up are always corrected by a live Eagle volume
# corrector, so the compensation, correction and corrector-type questions are
# not asked for them - there is nothing to choose. Confirmed with Holland
# Supply.
#
# The cut-off is read off the tab rather than written out as a list of model
# codes: the columns run in ascending capacity, so "23M and larger" is
# "at or right of the 23M column", and a model added to the sheet later falls
# on the correct side of the line without this constant being touched.
ROOTS_FORCED_EAGLE_FROM = "DR23M232"

FORCED_COMPENSATION = "Live"
FORCED_EAGLE_TYPE = "Volume Corrector"


def roots_forced_eagle(model):
    """True when this roots meter's corrector is not a choice."""
    if BY_MODEL[model]["family"] != "roots":
        return False
    order = [m["model"] for m in FAMILIES["roots"]["meters"]]
    if ROOTS_FORCED_EAGLE_FROM not in order:
        return False
    return order.index(model) >= order.index(ROOTS_FORCED_EAGLE_FROM)


def roots_assumed_fields(model):
    """What was assumed for a forced meter, so the customer can see it.

    These meters ask no questions, and a selection that silently acquired a
    live Eagle would be a selection nobody could check.
    """
    if not roots_forced_eagle(model):
        return []
    return [
        {"label": "Pressure compensation", "value": FORCED_COMPENSATION},
        {"label": "Correction", "value": EAGLE_INSTRUMENT},
    ]


def _q(qid, label, options, kind="single_select"):
    return {"id": qid, "label": label, "type": kind, "options": list(options)}


def option_questions(slug, model, answers):
    """The option questions for one chosen meter type.

    Returned in the order they should be asked. Conditional branches only
    appear once the answer they depend on is in `answers`, which is what lets
    the chatbot walk the roots index tree one turn at a time and the web block
    reveal the same widgets as the customer fills them in.
    """
    out = []
    fam = BY_MODEL[model]["family"]

    def get(name):
        return answers.get(f"{slug}.{name}")

    if model in FERRULES:
        out.append(_q(f"{slug}.ferrule", "Ferrule size", FERRULES[model]))
    if model in CONNECTIONS:
        out.append(_q(f"{slug}.connection", "Connection size", CONNECTIONS[model]))
    if model in ("SIQ250", "SIQ425"):
        out.append(_q(f"{slug}.pulse", "Pulse output", ["Yes", "No"], "yes_no"))

    if fam == "roots":
        # A 23M or larger asks nothing: its compensation, correction and
        # corrector type are all fixed. See roots_forced_eagle.
        if roots_forced_eagle(model):
            return out
        out.append(_q(f"{slug}.compensation", "Pressure compensation", COMPENSATION))
        comp = get("compensation")
        if comp == "None":
            out.append(_q(f"{slug}.radio", "AMI/AMR radio", ["Yes", "No"], "yes_no"))
            if get("radio") == "Yes":
                out.append(_q(f"{slug}.index", "Index", ["TC/AMR", "ETC", "ES3"]))
            # A "No" needs no index question: the rules fix it at TC.
        elif comp == "Fix-Factored":
            out.append(
                _q(f"{slug}.index", "Index", ["ETC", "ES3", IMC, EAGLE_INSTRUMENT])
            )
            if get("index") == EAGLE_INSTRUMENT:
                out.append(_q(f"{slug}.eagle_type", "Eagle type", EAGLE_TYPES))
        elif comp == "Live":
            out.append(
                _q(f"{slug}.live", "Correction", [f"{IMC} index", EAGLE_INSTRUMENT])
            )
            if get("live") == EAGLE_INSTRUMENT:
                out.append(_q(f"{slug}.eagle_type", "Eagle type", EAGLE_TYPES))

    elif fam == "rmg":
        out.append(_q(f"{slug}.compensation", "Pressure compensation", COMPENSATION))

    elif fam == "turbo":
        out.append(_q(f"{slug}.compensation", "Pressure compensation", COMPENSATION))
        if get("compensation") == "None":
            out.append(
                _q(
                    f"{slug}.slot",
                    "High-Frequency Slot Sensor Option",
                    ["None", "Conduit Connection", "Bendix Plug In Connection"],
                )
            )

    return out


# --------------------------------------------------------------------------
# Part numbers
# --------------------------------------------------------------------------

QUOTE_NOTE = "contact Holland Supply for a quote"

_ROOTS_MODEL_RE = re.compile(r"^DR(\d+[CM])(\d+)$")


def roots_model_parts(model):
    """DR15C175 -> ("15C-175", "175"): the part-number model token and rating."""
    m = _ROOTS_MODEL_RE.match(model)
    if not m:
        return model, "175"
    return f"{m.group(1)}-{m.group(2)}", m.group(2)


def ansi_class(psi):
    """Turbo flange class from the working pressure."""
    if psi < 150:
        return "125"
    if psi < 275:
        return "150"
    if psi < 720:
        return "300"
    return "600"


def eagle_p1(psi):
    """Transducer range for the Eagle part number."""
    for limit, p1 in ((10, "10"), (50, "51"), (100, "102"), (290, "290"),
                      (500, "508"), (1050, "1050")):
        if psi < limit:
            return p1
    return "1450"


# A roots meter driving an external Eagle corrector carries a drive rather
# than an index, and which drive depends on the corrector. Confirmed with
# Holland Supply. Both take the same surrounding segments as any non-ETC/ES3/
# IMC index, so A and B stay NA.
ROOTS_EAGLE_INDEX = {
    "Volume Corrector": "CD",
    "Rotary Corrector": "CTR",
}
# Used only if the corrector type is somehow missing: the part number is not
# built until every question is answered, so this should be unreachable.
ROOTS_EAGLE_INDEX_DEFAULT = "CD"


def roots_eagle_index(slug, answers):
    return ROOTS_EAGLE_INDEX.get(
        answers.get(f"{slug}.eagle_type"), ROOTS_EAGLE_INDEX_DEFAULT
    )


def build_part_number(model, slug, answers, inlet_psi):
    """-> (part_number, quote_note, [warnings]). Either the number or the note."""
    warnings = []

    def get(name):
        return answers.get(f"{slug}.{name}")

    fam = BY_MODEL[model]["family"]

    if model == "SR275":
        return f"M.R275.TC.5.D/R.{get('ferrule')}.TOP.NA", None, warnings

    if model in ("SIQ250", "SIQ425"):
        return None, QUOTE_NOTE, warnings

    if model in ("Sonix600", "Sonix880"):
        body = "SON-600" if model == "Sonix600" else "SON-880"
        return f"M.{body}.{get('ferrule')}.FIX.20.PO", None, warnings

    if model in ("DD800", "DD1000"):
        body = "D800" if model == "DD800" else "D1000"
        return f"M.{body}.{get('ferrule')}.CBG.25.NA.LIT.NA", None, warnings

    if model == "DD10C25":
        return f"M.RT10C25.{get('connection')}.DI-T.BP.TOP.N/A.TIBT.N/A", None, warnings

    if fam == "rmg":
        return None, QUOTE_NOTE, warnings

    if fam == "roots":
        token, rating = roots_model_parts(model)
        index = roots_index(slug, answers, model)
        if index in ("ETC", "ES3"):
            a, b = "CIR", "LITH"
        elif index == IMC:
            a, b = "CIR", "ALK"
        else:
            a, b = "NA", "NA"
        # The 232-rated 23M-232 does not carry a rating segment at all: the
        # model token already says 232, so the field is simply absent from that
        # meter's part number rather than blank. Every 175-rated model keeps
        # it. Confirmed with Holland Supply.
        #   23M-232 -> M.RT23M-232.FLG.TC.NA.VDN.NA.NA.NA
        #   3M-175  -> M.RT3M-175.FLG.TC.NA.175.VDN.NA.NA.NA
        segments = ["M.RT" + token, "FLG", index, a]
        if rating != "232":
            segments.append(rating)
        segments += ["VDN", "NA", b, "NA"]
        return ".".join(segments), None, warnings

    if fam == "turbo":
        comp = get("compensation")
        index = "VCR" if comp in ("Fix-Factored", "Live") else "VDR"
        if index == "VCR":
            slot, conn = "NA", "NA"
        else:
            choice = get("slot") or "None"
            if choice.startswith("Conduit"):
                slot, conn = "HF-SS-C", "CND"
            elif choice.startswith("Bendix"):
                slot, conn = "HF-SS-B", "PLUG"
            else:
                slot, conn = "NA", "NA"
        return (
            f"M.{display_model(model)}.{ansi_class(inlet_psi)}.{index}.{slot}.{conn}",
            None,
            warnings,
        )

    return None, QUOTE_NOTE, warnings


def roots_index(slug, answers, model=None):
    """The index token for a roots part number, from the compensation branch."""
    if model is not None and roots_forced_eagle(model):
        return ROOTS_EAGLE_INDEX[FORCED_EAGLE_TYPE]
    comp = answers.get(f"{slug}.compensation")
    if comp == "None":
        if answers.get(f"{slug}.radio") == "Yes":
            chosen = answers.get(f"{slug}.index") or "TC"
            # TC/AMR is how the index is offered; TC is what the number carries.
            return "TC" if chosen == "TC/AMR" else chosen
        return "TC"
    if comp == "Fix-Factored":
        chosen = answers.get(f"{slug}.index")
        if chosen == EAGLE_INSTRUMENT:
            return roots_eagle_index(slug, answers)
        return chosen or "ETC"
    if comp == "Live":
        if answers.get(f"{slug}.live") == EAGLE_INSTRUMENT:
            return roots_eagle_index(slug, answers)
        return IMC
    return "TC"


# The display wording for the two part-number tokens that describe the
# hardware. Both are derived from the token rather than hard-coded, so if a
# part number ever carries a clockwise drive or the larger case, the printed
# description follows it instead of quietly contradicting it.
EAGLE_ROTATION = {"CCW": "Counterclockwise", "CW": "Clockwise"}
EAGLE_CASE = {"8X6": '8"x6"', "12X10": '12"x10"'}


def eagle_selection(eagle_type, inlet_psi):
    """The Eagle block. It quotes as its own line item, not part of the meter."""
    p1 = eagle_p1(inlet_psi)
    rotary = eagle_type == "Rotary Corrector"
    body = "I.MPP-MRC" if rotary else "I.MPP-MVC"
    # Position 8 of the part number: the input the corrector reads. It says
    # the same thing as the "Corrector" field above it, so it is not printed
    # a second time as its own line.
    mount = "INTEG" if rotary else "CVI"
    rotation_token = "CCW"
    case_token = "8X6"
    rotation = EAGLE_ROTATION.get(rotation_token, rotation_token)
    case = EAGLE_CASE.get(case_token, case_token)
    return {
        "manufacturer": "Eagle",
        "model": "MPplusII",
        "type": eagle_type,
        "p1": p1,
        "rotation": rotation,
        "case": case,
        "part_number": f"{body}.{p1}.N.N.N.TC.{mount}.{rotation_token}.N.ALK.{case_token}",
        "fields": [
            {"label": "Manufacturer", "value": "Eagle"},
            {"label": "Model", "value": "MPplusII"},
            {"label": "Corrector", "value": eagle_type},
            {"label": "Rotation", "value": rotation},
            {"label": "Pressure Transducer", "value": f"0-{p1} psi"},
            {"label": "Temperature Probe", "value": "Included"},
            {"label": "Battery", "value": "Alkaline battery pack"},
            {"label": "Cellular Communication", "value": "None"},
            {"label": "Case", "value": case},
        ],
        "lines": [
            "Eagle",
            "MPplusII",
            eagle_type,
            rotation,
            f"0-{p1} psi Pressure Transducer",
            "Temperature Probe",
            "Alkaline battery pack",
            "No Cellular Communication",
            f"{case} case",
        ],
    }


def eagle_type_for(slug, model, answers):
    """Which Eagle, if any, this meter type pulls onto the quote."""
    fam = BY_MODEL[model]["family"]
    if fam == "turbo":
        # Fix-factored and live turbo metering is always corrected by an
        # Eagle volume corrector; the instructions name no alternative.
        if answers.get(f"{slug}.compensation") in ("Fix-Factored", "Live"):
            return "Volume Corrector"
        return None
    if fam == "roots":
        if roots_forced_eagle(model):
            return FORCED_EAGLE_TYPE
        comp = answers.get(f"{slug}.compensation")
        picked = (
            answers.get(f"{slug}.index") if comp == "Fix-Factored"
            else answers.get(f"{slug}.live") if comp == "Live"
            else None
        )
        if picked == EAGLE_INSTRUMENT:
            return answers.get(f"{slug}.eagle_type")
    return None


# --------------------------------------------------------------------------
# Main entry point
# --------------------------------------------------------------------------

METER_TYPE_QUESTION_ID = "meter_type"


def size_meters(payload):
    """Size a gas meter. See the module docstring for the call pattern."""
    payload = payload or {}
    errors = []
    warnings = []

    inlet = _number(payload.get("inlet"))
    flow = _number(payload.get("flow"))
    inlet_units = payload.get("inlet_units") or "psi"
    flow_units = payload.get("flow_units") or "CFH"

    if inlet is None or inlet <= 0:
        errors.append("Enter an inlet pressure greater than zero.")
    if flow is None or flow <= 0:
        errors.append("Enter a flow rate greater than zero.")
    # 1440 psi is the top of the turbo capacity table, so it is the highest
    # pressure any meter in the tables is rated for. It is also what makes the
    # Eagle's 0-1450 transducer band reachable.
    if inlet is not None and not (0 <= inlet <= MAX_INLET):
        errors.append(f"Inlet pressure must be between 0 and {MAX_INLET:,.0f}.")
    if flow is not None and not (0 <= flow <= MAX_FLOW):
        errors.append(f"Flow rate must be between 0 and {MAX_FLOW:,}.")

    try:
        inlet_psi = to_psi(inlet or 0, inlet_units)
        flow_cfh = to_cfh(flow or 0, flow_units)
    except ValueError as exc:
        errors.append(str(exc))
        inlet_psi = flow_cfh = 0.0

    if errors:
        return {"ok": False, "errors": errors, "warnings": warnings, "stage": "error"}

    first_p = FAMILIES["small"]["meters"][0]["points"][0]["p"]
    if inlet_psi < first_p:
        warnings.append(
            f"Inlet pressure is below the {_fmt(first_p)} psi start of the capacity "
            f"tables; the {_fmt(first_p)} psi capacities were used."
        )

    evals = {
        model: evaluate(model, inlet_psi, flow_cfh)
        for model in BY_MODEL
    }

    tier = next(
        (t for t in TIERS if t["trigger"] and any(evals[m]["works"] for m in t["trigger"])),
        TIERS[-1],
    )

    summary = [
        {"label": "Inlet Pressure", "value": f"{_fmt(inlet)} {inlet_units}"},
        {"label": "Flow Rate", "value": f"{_fmt(flow)} {flow_units}"},
        {"label": "Inlet Pressure (converted)", "value": f"{_fmt(inlet_psi)} psi"},
        {"label": "Flow Rate (converted)", "value": f"{_fmt(flow_cfh)} CFH"},
    ]

    base = {
        "ok": True,
        "errors": [],
        "warnings": warnings,
        "converted": {
            "inlet_psi": inlet_psi,
            "flow_cfh": flow_cfh,
        },
        "summary": summary,
        "tier": tier["id"],
        "meter_type_question": {
            "id": METER_TYPE_QUESTION_ID,
            "label": "Meter type",
            "type": "multi_select",
            "options": list(tier["types"]),
        },
        "evaluations": [evals[m] for m in BY_MODEL],
    }

    # Nothing in any tab has the capacity: say so rather than offering a type
    # whose every candidate fails.
    if not any(e["works"] for e in evals.values()):
        return dict(
            base,
            stage="complete",
            selected=False,
            message=(
                "No meter in the capacity tables will handle "
                f"{_fmt_cfh(flow_cfh)} CFH at {_fmt(inlet_psi)} psi. Contact Holland "
                "Supply Company to review the application."
            ),
            questions=[],
            results=[],
            part_numbers=[],
            eagle=None,
        )

    # Ordered by the tier rather than by the caller, so the same selection
    # always renders and reads back in the same order whether it arrived from
    # a set of checkboxes or a chatbot reply.
    requested = _as_list(payload.get("meter_types"))
    chosen_types = [t for t in tier["types"] if t in requested]
    dropped = [t for t in requested if t not in tier["types"]]
    if dropped:
        warnings.append(
            "These meter types do not apply at this pressure and flow and were "
            "ignored: " + ", ".join(dropped) + "."
        )

    if not chosen_types:
        return dict(
            base,
            stage="meter_type",
            selected=False,
            message="Select a meter type to continue.",
            questions=[base["meter_type_question"]],
            results=[],
            part_numbers=[],
            eagle=None,
        )

    answers = dict(payload.get("answers") or {})
    pending = []
    results = []
    part_numbers = []
    eagle_requests = []

    for mtype in chosen_types:
        slug = SLUG[mtype]
        model = resolve(tier["resolve"][mtype], evals)
        if model is None:
            results.append(
                {
                    "type": mtype,
                    "slug": slug,
                    "heading": f"{mtype} Meter",
                    "available": False,
                    "message": _unavailable_message(mtype, tier, evals),
                    "identity_fields": [],
                    "answer_fields": [],
                    "fields": [],
                    "lines": [],
                    "part_number": None,
                    "quote_note": None,
                    "warnings": [],
                }
            )
            continue

        questions = option_questions(slug, model, answers)
        unanswered = [q for q in questions if not answers.get(q["id"])]
        pending.extend(unanswered)

        ev = evals[model]
        entry = {
            "type": mtype,
            "slug": slug,
            "heading": f"{mtype} Meter",
            "available": True,
            "meter": model,
            "manufacturer": display_manufacturer(model),
            "model": display_model(model),
            "size": ev["size"],
            "family": ev["family"],
            "capacity_cfh": ev["capacity_cfh"],
            "required_cfh": ev["required_cfh"],
            "min_capacity_cfh": ev["min_capacity_cfh"],
            "oversize_pct": int(ev["oversize"] * 100),
            "rating_psi": ev["rating_psi"],
            "questions": questions,
            "answers": {q["id"]: answers.get(q["id"]) for q in questions},
            "complete": not unanswered,
            "warnings": [],
        }

        # `identity_fields` and `answer_fields` are kept apart as well as
        # concatenated. A caller that renders the questions as live widgets
        # (the web block does) shows the identity fields only, so an answer is
        # not printed once as a field and again as a ticked radio; a caller
        # that just wants the finished selection as text (the chatbot, the
        # PDF, the PDF) reads `fields`.
        # The assumed compensation goes in with the identity fields, not the
        # answer fields: a caller that renders questions as widgets shows only
        # the identity fields, and a forced meter has no widgets to carry the
        # assumption. Putting it here is what makes it visible at all.
        entry["identity_fields"] = (
            _identity_fields(entry) + roots_assumed_fields(model)
        )
        entry["answer_fields"] = _answer_fields(questions, answers)
        entry["fields"] = entry["identity_fields"] + entry["answer_fields"]

        if unanswered:
            entry["lines"] = _identity_lines(entry)
            entry["part_number"] = None
            entry["quote_note"] = None
            results.append(entry)
            continue

        pn, quote_note, pn_warnings = build_part_number(model, slug, answers, inlet_psi)
        entry["part_number"] = pn
        entry["quote_note"] = quote_note
        entry["warnings"] = pn_warnings
        entry["lines"] = (
            _identity_lines(entry)
            + [f["label"] + ": " + f["value"] for f in roots_assumed_fields(model)]
            + _answer_lines(questions, answers)
        )
        if pn:
            part_numbers.append(pn)
        results.append(entry)

        etype = eagle_type_for(slug, model, answers)
        if etype:
            eagle_requests.append(etype)

        if ev["family"] == "turbo" and answers.get(f"{slug}.compensation") in (
            "Fix-Factored",
            "Live",
        ):
            entry["warnings"].append(
                "Include the Eagle MPplusII Volume Corrector on the quote."
            )

    # The Eagle is a separate line item under the meter selection, not part of
    # any meter's own part number. Two chosen types asking for the same Eagle
    # produce one.
    eagle = None
    if eagle_requests:
        etype = eagle_requests[0]
        if len(set(eagle_requests)) > 1:
            warnings.append(
                "More than one Eagle corrector type was selected; the "
                f"{etype} is shown."
            )
        eagle = eagle_selection(etype, inlet_psi)
        part_numbers.append(eagle["part_number"])

    stage = "options" if pending else "complete"
    ready = [r for r in results if r.get("available") and r.get("complete")]

    if stage == "options":
        message = "Answer the remaining options to build the part number."
    elif ready:
        message = (
            "Meter selected!" if len(ready) == 1
            else f"{len(ready)} meter options selected!"
        )
    else:
        message = "No meter is available for the selected type."

    for r in results:
        warnings.extend(w for w in r.get("warnings", []) if w not in warnings)

    return dict(
        base,
        stage=stage,
        selected=bool(ready),
        message=message,
        questions=pending,
        results=results,
        part_numbers=part_numbers,
        eagle=eagle,
        warnings=warnings,
    )


def _unavailable_message(mtype, tier, evals):
    """Why a type on offer has no meter behind it.

    This is reachable in the normal course of things: a tier is offered
    because one of its meters works, and the straight-pipe 10C25 has less
    capacity than the meter-bar models beside it, so it can fall short at a
    flow the tier itself clears.
    """
    rule = tier["resolve"][mtype]
    kind, arg = rule
    models = arg if kind == "pick" else [m["model"] for m in FAMILIES[arg]["meters"]]
    best = models[-1]
    return (
        f"No {mtype.lower()} meter will work for this application "
        f"({display_model(best)}: {evals[best]['reason']})."
    )


def _identity_fields(entry):
    fields = [
        {"label": "Manufacturer", "value": entry["manufacturer"]},
        {"label": "Model", "value": entry["model"]},
    ]
    if entry.get("size"):
        fields.append({"label": "Size", "value": entry["size"]})
    if entry.get("meter") in PULSE_OUTPUT_INCLUDED:
        fields.append({"label": "Pulse Output", "value": "Included"})
    fields.append(
        {"label": "Meter Capacity (CFH)", "value": _fmt_cfh(entry["capacity_cfh"])}
    )
    if entry.get("oversize_pct"):
        fields.append(
            {
                "label": "Required Capacity (CFH)",
                "value": f"{_fmt_cfh(entry['required_cfh'])} "
                         f"({entry['oversize_pct']}% oversize)",
            }
        )
    if entry.get("min_capacity_cfh"):
        fields.append(
            {"label": "Minimum Capacity (CFH)", "value": _fmt_cfh(entry["min_capacity_cfh"])}
        )
    return fields


def _identity_lines(entry):
    lines = [entry["manufacturer"], entry["model"]]
    if entry.get("size"):
        lines.append(entry["size"])
    if entry.get("meter") in PULSE_OUTPUT_INCLUDED:
        lines.append("Pulse output included")
    return lines


def _answer_fields(questions, answers):
    out = []
    for q in questions:
        v = answers.get(q["id"])
        if v:
            out.append({"label": q["label"], "value": v})
    return out


def _answer_lines(questions, answers):
    """The sample outputs print the bare answer for a size and a sentence for
    a flag, which is what this reproduces."""
    out = []
    for q in questions:
        v = answers.get(q["id"])
        if not v:
            continue
        if q["id"].endswith(".ferrule") or q["id"].endswith(".connection"):
            out.append(v)
        elif q["id"].endswith(".pulse"):
            out.append("Pulse output required" if v == "Yes" else "No pulse output")
        elif q["id"].endswith(".index"):
            out.append(f"{v} index")
        else:
            out.append(f"{q['label']}: {v}")
    return out


def _number(v):
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def _as_list(v):
    if v is None:
        return []
    if isinstance(v, str):
        return [v]
    return list(v)
