"""HTTP wrapper around the meter sizing algorithm, for the chatbot.

This file holds no rules. Every sizing decision comes from
algorithm/meter_sizing.py -- the same module the browser bundle is generated
from -- so the chatbot and the website cannot give different answers.

    pip install -r requirements.txt
    uvicorn api.main:app --reload

    POST /api/meter-sizing          size a meter (see the schema below)
    GET  /api/meter-sizing/schema   the input options, for building prompts
    GET  /health                    liveness

THE CONVERSATION LOOP
---------------------
The endpoint is stateless. It answers with the questions that are still
outstanding, and the caller sends everything back each time with one more
answer filled in. A chatbot turn looks like:

    POST {"inlet": 5, "inlet_units": "psi", "flow": 1500, "flow_units": "CFH"}
      <- stage "meter_type", questions[0] = which meter type?
         ask the customer, then send their reply back:

    POST {..., "meter_types": ["Rotary (meter bar)"]}
      <- stage "options", questions[0] = ferrule size?

    POST {..., "answers": {"rotary_meter_bar.ferrule": "45LT"}}
      <- stage "complete", with the part number

Keep the payload in the conversation state and add to it; do not try to
reconstruct it from the previous reply. `questions` is empty exactly when
`stage` is "complete", which is the signal to stop asking and present the
result.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from algorithm.meter_sizing import (  # noqa: E402
    FLOW_UNITS,
    MAX_FLOW,
    MAX_INLET,
    PRESSURE_UNITS,
    TIERS,
    size_meters,
)

app = FastAPI(
    title="Holland Supply Meter Sizing",
    version="1.0.0",
    description=__doc__,
)

# The website block calls the JavaScript bundle directly and never touches
# this service, so the only callers are the chatbot and internal tooling.
# Narrow this to the chatbot's origin before it goes anywhere public.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


class SizingRequest(BaseModel):
    inlet: Optional[float] = Field(
        None, description="Inlet pressure, 0-1440, in `inlet_units`."
    )
    inlet_units: str = Field("psi", description="psi, in wc, oz, bar or kPa.")
    flow: Optional[float] = Field(
        None, description="Flow rate, 0-100000000, in `flow_units`."
    )
    flow_units: str = Field("CFH", description="CFH, BTUH or CMH.")
    meter_types: List[str] = Field(
        default_factory=list,
        description=(
            "The customer's answer to the `meter_type` question. Send back "
            "exactly the strings listed in that question's `options`; more "
            "than one is allowed and each is sized separately."
        ),
    )
    answers: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Answers to the option questions, keyed by the question `id` "
            "returned in `questions` -- for example "
            "{\"rotary_roots.compensation\": \"Live\"}."
        ),
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "inlet": 60,
                "inlet_units": "psi",
                "flow": 9000,
                "flow_units": "CFH",
                "meter_types": ["Rotary (roots)"],
                "answers": {
                    "rotary_roots.compensation": "Fix-Factored",
                    "rotary_roots.index": "ETC",
                },
            }
        }
    }


@app.post("/api/meter-sizing")
def meter_sizing(req: SizingRequest) -> Dict[str, Any]:
    """Size a meter, or return the questions still needed to size one.

    Always 200. A rejected input comes back as `{"ok": false, "errors": [...]}`
    rather than an HTTP error, so a chatbot can read the reason out loud
    instead of handling a status code.
    """
    return size_meters(req.model_dump())


@app.get("/api/meter-sizing/schema")
def schema() -> Dict[str, Any]:
    """The fixed input vocabulary, for prompt construction and validation.

    The meter types are listed per tier for reference only. Which tier applies
    depends on the pressure and flow, so never offer a customer a type from
    here -- offer the `options` from the `meter_type` question the sizing call
    returns, which is the list that actually applies to their job.
    """
    return {
        "pressure_units": PRESSURE_UNITS,
        "flow_units": FLOW_UNITS,
        "inlet_range": [0, MAX_INLET],
        "flow_range": [0, MAX_FLOW],
        "stages": ["error", "meter_type", "options", "complete"],
        "meter_types_by_tier": {t["id"]: list(t["types"]) for t in TIERS},
    }


@app.get("/health")
def health() -> Dict[str, Any]:
    """Sizes a known job as a smoke test, so a green check means the tables
    loaded and the rules run -- not merely that the process is up."""
    probe = size_meters(
        {
            "inlet": 7,
            "inlet_units": "in wc",
            "flow": 250,
            "flow_units": "CFH",
            "meter_types": ["Diaphragm"],
            "answers": {"diaphragm.ferrule": "1-1/4"},
        }
    )
    ok = probe.get("part_numbers") == ["M.R275.TC.5.D/R.1-1/4.TOP.NA"]
    return {"status": "ok" if ok else "degraded", "probe_passed": ok}
