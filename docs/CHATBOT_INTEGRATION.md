# Meter Selection Tool — Chatbot Integration & Continuity Guide

Everything needed to wire the meter selection tool into a chatbot, and to pick
the work up cold months from now.

Repository: `https://github.com/my-usg/meterselection`

---

## 1. What this is, in one paragraph

The tool takes an inlet pressure and a flow rate, works out which gas meters
have the capacity, asks the customer which meter **type** they want, asks the
follow-up questions that choice implies, and produces a part number. The rules
live in Python. A generated JavaScript build of the same rules runs the
website's sizing block, and a parity test proves the two agree, so the chatbot
and the website cannot give different answers.

**The chatbot must call the tool. It must not reason about meter sizing
itself.** Capacities are interpolated from manufacturer tables and part numbers
are assembled from exact tokens; a language model guessing at either will be
confidently wrong in ways nobody catches until a customer receives the wrong
meter.

---

## 2. Architecture

```
                    data/capacities.xlsx          (engineering source)
                             |  tools/extract_capacities.py
                             v
                    data/capacities.json          (the numbers, once)
                       /                \
   algorithm/meter_sizing.py      src/js/meter_sizing.js
        (SOURCE OF TRUTH)            |  tools/build.py
          /            \              v
   api/main.py    (chatbot)    dist/usg-meter-sizing.js
        |                             |  jsDelivr, pinned to a commit
        v                             v
     chatbot                    block/block.html   (Concrete CMS)
```

`algorithm/meter_sizing.py` is the only place rules are decided. Everything
else either transports or renders its output.

---

## 3. How to call it

Two options. Both run the identical module.

### 3a. HTTP (recommended for a chatbot)

```
POST /api/meter-sizing
Content-Type: application/json
```

Start the service with:

```bash
pip install -r requirements.txt
uvicorn api.main:app --host 0.0.0.0 --port 8000
```

Supporting endpoints:

| Endpoint | Purpose |
| --- | --- |
| `GET /api/meter-sizing/schema` | the fixed input vocabulary, for prompt construction and validation |
| `GET /health` | sizes a known job and reports `probe_passed` — a green check means the tables loaded and the rules ran, not merely that the process is up |

### 3b. Direct Python import

```python
from algorithm.meter_sizing import size_meters
result = size_meters(payload)
```

Same function, same dict in and out. Use this if the bot runs in the same
process. `api/chatbot_example.py` is a runnable worked example of the whole
loop — run it and watch a sizing play out turn by turn.

---

## 4. The conversation loop

This is the part that differs from the regulator sizing tool and the part most
worth understanding.

Sizing a regulator is one shot: inputs in, part number out. Sizing a meter is
a conversation. Once the tool knows which meters have the capacity, it has to
ask which **type** the customer wants, and then a different set of follow-up
questions depending on the answer.

**The algorithm is a stateless resolver.** Every call takes the whole answer
set collected so far and returns the questions still outstanding. There is no
session, no server-side state, no step counter.

```
keep one payload object for the conversation
  |
  v
POST it  ->  is `ok` false?  ->  read `errors`, fix the input, retry
  |
  `-- ok is true  ->  read `questions`
         |
         |-- empty?      ->  stage is "complete". Present the result. Done.
         |
         `-- non-empty?  ->  ask the customer questions[0]
                             write their reply into the payload
                             POST the whole payload again
```

**Check `ok` first.** A rejected-input response carries only `ok`, `errors`,
`warnings` and `stage` — there is no `questions` key on it at all, so a bot
that reads `questions` before checking `ok` will crash on a blank form.

### The four rules of the loop

1. **Keep the payload and add to it.** Do not rebuild it from the previous
   reply. The tool needs the complete answer set every time.
2. **Ask one question per turn**, `questions[0]`. A conditional branch can
   reveal a question that only exists because of the answer just given, so
   answering the batch at once means asking about things the customer has not
   been offered.
3. **Offer exactly the strings in `options`.** The tool matches on them
   literally. `"live"` will not match `"Live"`.
4. **When `ok` is true, `questions` is empty exactly when `stage` is
   `"complete"`.** That is the signal to stop asking. Do not test for anything
   else, and do not read `questions` before checking `ok`.

### Where each answer goes

| Question `id` | Where it belongs in the payload |
| --- | --- |
| `meter_type` | `payload["meter_types"]` — a **list holding exactly one** type |
| anything else | `payload["answers"][<id>]` — a string |

**One meter type per sizing.** The customer picks a single type; the field
stays a list because that is the shape the algorithm takes, but it never holds
more than one. Send several and the tool sizes the first in tier order and
warns that the rest were ignored — it does not error, but the customer only
ever sees one meter. To price a second type, run the sizing again with that
type instead.

---

## 5. Input reference

Complete. There are no other inputs.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `inlet` | number | yes | 0–1440, expressed in `inlet_units`. Must be > 0 |
| `inlet_units` | string | no | `psi` (default), `in wc`, `oz`, `bar`, `kPa` |
| `flow` | number | yes | 0–100,000,000, expressed in `flow_units`. Must be > 0 |
| `flow_units` | string | no | `CFH` (default), `BTUH`, `CMH` |
| `meter_types` | array of string | no | the answer to the `meter_type` question. A list holding **exactly one** type. More than one is accepted but only the first in tier order is sized, with a warning |
| `answers` | object | no | option answers keyed by question `id` |

Notes that matter:

- **The range applies to the number as entered, not converted to psi.** `100`
  with `inlet_units: "bar"` passes validation and is 1,450 psi — above every
  meter's rating — so it comes back as "no meter will handle this" rather than
  a validation error.
- **Unknown keys are ignored.** `maop` was an input in an early version; a
  stale caller sending it changes nothing.
- **Unit conversions:** 28 in wc = 1 psi · 16 oz = 1 psi · 1 bar = 14.5038 psi
  · 6.89476 kPa = 1 psi · 1 CMH = 35.3147 CFH · 1000 BTUH = 1 CFH.

### Minimal first call

```json
{ "inlet": 60, "inlet_units": "psi", "flow": 12000, "flow_units": "CFH" }
```

### Full call

```json
{
  "inlet": 60,
  "inlet_units": "psi",
  "flow": 12000,
  "flow_units": "CFH",
  "meter_types": ["Rotary (roots)"],
  "answers": {
    "rotary_roots.compensation": "Live",
    "rotary_roots.live": "Eagle MPplusII Instrument",
    "rotary_roots.eagle_type": "Rotary Corrector"
  }
}
```

---

## 6. Output reference

Always HTTP 200, including for rejected input, so the bot reads a reason
rather than handling a status code.

### Top level

| Field | Type | Meaning |
| --- | --- | --- |
| `ok` | bool | `false` only for rejected input. Then only `errors`, `warnings`, `stage` are present |
| `errors` | string[] | why the input was rejected. Read these to the customer |
| `warnings` | string[] | things the customer should know. Read these out |
| `stage` | string | `error` · `meter_type` · `options` · `complete` |
| `questions` | object[] | what is still outstanding. **Empty ⇔ `stage == "complete"`** |
| `meter_type_question` | object | the type question, always present, even after it is answered |
| `results` | object[] | the chosen meter type. One entry, unless nothing was chosen yet |
| `part_numbers` | string[] | every part number this run produced, meters first, Eagle last |
| `eagle` | object \| null | the Eagle instrument. A **separate line item**, never part of a meter's part number |
| `selected` | bool | true when at least one type reached a finished selection |
| `summary` | {label,value}[] | the inputs, for a printed record |
| `converted` | object | `{inlet_psi, flow_cfh}` |
| `tier` | string | which rung the job landed on. Diagnostic |
| `evaluations` | object[] | every meter in the tables with its capacity and, if it failed, why. Diagnostic |

### A question object

```json
{
  "id": "rotary_roots.compensation",
  "label": "Pressure compensation",
  "type": "single_select",
  "options": ["None", "Fix-Factored", "Live"]
}
```

`type` is `single_select` or `yes_no`. The meter type question is a
`single_select` like any other; it is only distinguished by its `id`.

### An entry in `results`

| Field | Meaning |
| --- | --- |
| `type` | the meter type, e.g. `Rotary (roots)` |
| `slug` | its answer-key namespace, e.g. `rotary_roots` |
| `heading` | display heading, e.g. `Rotary (roots) Meter` |
| `available` | **false** when the type is offered but no meter behind it works. Then read `message` and skip the rest |
| `message` | present when `available` is false: why nothing works |
| `meter` | internal model code, e.g. `DR3M175`. For diagnostics, not for the customer |
| `manufacturer` | `Dresser`, `Sensus`, `RMG` |
| `model` | the catalogue name the customer should hear, e.g. `3M175` |
| `size` | e.g. `2"`, `11" CTC`. **May be null** — the 10C25 has none |
| `capacity_cfh` / `required_cfh` / `min_capacity_cfh` | numbers, unrounded |
| `oversize_pct` | 0, or 10 for roots and turbine |
| `rating_psi` | the meter's pressure rating |
| `questions` / `answers` / `complete` | this type's own question set and state |
| `identity_fields` | manufacturer, model, size, pulse output, capacities |
| `answer_fields` | the options as answered |
| `fields` | the two concatenated. **Use this to present a finished selection** |
| `lines` | the same content as flat strings, no labels |
| `part_number` | the SKU, or **null** |
| `quote_note` | present when `part_number` is null: `contact Holland Supply for a quote` |
| `warnings` | notes specific to this entry |

### The `eagle` object

```json
{
  "manufacturer": "Eagle",
  "model": "MPplusII",
  "type": "Volume Corrector",
  "p1": "290",
  "rotation": "Counterclockwise",
  "case": "8\"x6\"",
  "part_number": "I.MPP-MVC.290.N.N.N.TC.CVI.CCW.N.ALK.8X6",
  "fields": [ ... ],
  "lines": [ ... ]
}
```

Present it as its own item beneath the meter, never folded into the meter's
part number.

---

## 7. Every question, with its full tree

Question ids are namespaced by meter type slug, so two types selected at once
never collide.

### Diaphragm → `SR275`
```
diaphragm.ferrule : 10LT | 20LT | 30LT | 45LT | 1A | 1-1/4
```

### Sonix IQ → `SIQ250`, else `SIQ425`
```
sonix_iq.ferrule : 10LT | 20LT | 30LT | 1A | 1-1/4
sonix_iq.pulse   : Yes | No
```

### Ultrasonic → `Sonix600`/`Sonix880` in the middle tiers
```
ultrasonic.ferrule : 20LT | 30LT | 45LT
```

### Ultrasonic → RMG in the top tier
```
ultrasonic.compensation : None | Fix-Factored | Live
```

### Rotary (meter bar) → `DD800`, else `DD1000`
```
rotary_meter_bar.ferrule : 30LT | 45LT | 1-1/2
```

### Rotary (straight pipe) → `DD10C25`
```
rotary_straight_pipe.connection : 30LT | 45LT | 1-1/2
```

### Rotary (roots), below the 23M
```
rotary_roots.compensation = None
    rotary_roots.radio = Yes
        rotary_roots.index : TC/AMR | ETC | ES3
    rotary_roots.radio = No
        (no further questions; the index is fixed at TC)
rotary_roots.compensation = Fix-Factored
    rotary_roots.index = ETC | ES3 | IMC-W2-PTZ
    rotary_roots.index = Eagle MPplusII Instrument
        rotary_roots.eagle_type : Volume Corrector | Rotary Corrector
rotary_roots.compensation = Live
    rotary_roots.live = IMC-W2-PTZ index
    rotary_roots.live = Eagle MPplusII Instrument
        rotary_roots.eagle_type : Volume Corrector | Rotary Corrector
```

### Rotary (roots), the 23M and larger
**No questions at all.** Compensation is always Live, correction always an
Eagle, corrector always a Volume Corrector. The assumption is reported in
`identity_fields` so the customer can see it — read it out.

### Turbine
```
turbine.compensation = None
    turbine.slot : None | Conduit Connection | Bendix Plug In Connection
turbine.compensation = Fix-Factored
    (no further questions; an Eagle volume corrector is added)
turbine.compensation = Live
    (no further questions; an Eagle volume corrector is added)
```

---

## 8. Which meter types get offered

The tool finds the smallest meter that works and offers that rung plus the
next one up. **Never offer a fixed list** — offer `meter_type_question.options`
from the response.

| Smallest thing that works | Types offered |
| --- | --- |
| SR275 | Diaphragm, Sonix IQ |
| SIQ250 / SIQ425 | Sonix IQ, Ultrasonic |
| Sonix600 / Sonix880 | Ultrasonic, Rotary (meter bar), Rotary (straight pipe) |
| DD800 / DD1000 | Rotary (meter bar), Rotary (straight pipe), Rotary (roots) |
| nothing above | Rotary (roots), Ultrasonic, Turbine |

"Ultrasonic" means Sonix 600/880 in the middle tiers and RMG in the top tier.

**A type can be offered and still have no meter behind it.** The straight-pipe
10C25 has less capacity than the meter-bar models beside it, so it can fall
short at a flow its own tier clears. That arrives as a `results` entry with
`available: false` and a `message` explaining why. Read the message; do not
treat it as an error.

---

## 9. Part numbers

| Meter | Shape | Example |
| --- | --- | --- |
| SR275 | `M.R275.TC.5.D/R.<ferrule>.TOP.NA` | `M.R275.TC.5.D/R.1-1/4.TOP.NA` |
| Sonix 600/880 | `M.SON-600.<ferrule>.FIX.20.PO` | `M.SON-600.30LT.FIX.20.PO` |
| D800 / D1000 | `M.D800.<ferrule>.CBG.25.NA.LIT.NA` | `M.D800.45LT.CBG.25.NA.LIT.NA` |
| 10C25 | `M.RT10C25.<conn>.DI-T.BP.TOP.N/A.TIBT.N/A` | `M.RT10C25.1-1/2.DI-T.BP.TOP.N/A.TIBT.N/A` |
| Roots | `M.RT<model>.FLG.<index>.<A>.<rating>.VDN.NA.<B>.NA` | `M.RT3M-175.FLG.ETC.CIR.175.VDN.NA.LITH.NA` |
| Turbine | `M.T-<n>.<ANSI>.<index>.<slot>.<conn>` | `M.T-18.300.VDR.HF-SS-C.CND` |
| Eagle | `I.MPP-MVC.<P1>.N.N.N.TC.CVI.CCW.N.ALK.8X6` | `I.MPP-MVC.290.N.N.N.TC.CVI.CCW.N.ALK.8X6` |
| Sonix IQ, RMG | none — `contact Holland Supply for a quote` | |

Two roots quirks worth knowing, because they look like bugs:

- **The 23M-232 omits the rating segment entirely** (`M.RT23M-232.FLG.CD.NA.VDN.NA.NA.NA`),
  because the model token already says 232. It is one field shorter than every
  other roots part number. Anything parsing these positionally will read that
  meter's fields off by one.
- The index drives two trailing tokens: `ETC`/`ES3` → `CIR`/`LITH`,
  `IMC-W2-PTZ` → `CIR`/`ALK`, everything else → `NA`/`NA`.

---

## 10. The sizing rules, briefly

- **A meter works** when its interpolated capacity at the inlet pressure covers
  the flow plus the family's oversize allowance, it is rated for the inlet
  pressure, and its minimum capacity (if it has one) is at or below the flow.
- **Oversize allowance:** 0% for small meters and RMG, **10%** for roots and
  turbine.
- **Capacity tables** are interpolated linearly. Below 0.25 psi the 0.25 psi
  row is used unchanged, and a warning says so.
- **Capacities display to the whole CFH.**
- **The Eagle transducer band** comes from the inlet pressure: <10 → `10`,
  <50 → `51`, <100 → `102`, <290 → `290`, <500 → `508`, <1050 → `1050`, else
  `1450`.
- **The turbine ANSI class** comes from the inlet pressure: <150 → `125`,
  <275 → `150`, <720 → `300`, else `600`.

---

## 11. Terminal states the bot must handle

There are five. Handle all of them.

**1. Rejected input** — `ok: false`
```json
{ "ok": false, "stage": "error",
  "errors": ["Enter an inlet pressure greater than zero."], "warnings": [] }
```
Read the errors, ask for the missing figure, retry.

**2. Nothing in the tables works** — `ok: true`, `selected: false`, no results
```json
{ "ok": true, "stage": "complete", "selected": false,
  "message": "No meter in the capacity tables will handle 50,000,000 CFH at 400 psi. Contact Holland Supply Company to review the application.",
  "questions": [], "results": [], "part_numbers": [], "eagle": null }
```
Read `message`. Do not offer alternatives — there are none.

**3. One type offered has no meter** — entry with `available: false`
Read that entry's `message`, and carry on with the other entries.

**4. A meter with no part number** — `part_number: null`, `quote_note` set
Sonix IQ and RMG. Say the selection and then the quote note. Never invent a
SKU.

**5. Success** — `stage: "complete"`, `selected: true`
Present each entry, then the Eagle if present, then the warnings.

---

## 12. Worked conversation

```
Bot:  What's the inlet pressure and the load?
Cust: 60 psi, 12,000 CFH

  POST {"inlet":60,"inlet_units":"psi","flow":12000,"flow_units":"CFH"}
  -> stage "meter_type"
     questions[0] = {id:"meter_type", type:"single_select",
                     options:["Rotary (roots)","Ultrasonic","Turbine"]}

Bot:  Three meter types will work here: rotary (roots), ultrasonic, or
      turbine. Which would you like?
Cust: roots

  POST {...same..., "meter_types":["Rotary (roots)"]}
  -> stage "options"
     questions[0] = {id:"rotary_roots.compensation",
                     options:["None","Fix-Factored","Live"]}

Bot:  Pressure compensation — none, fix-factored, or live?
Cust: live

  POST {...same..., "answers":{"rotary_roots.compensation":"Live"}}
  -> stage "options"
     questions[0] = {id:"rotary_roots.live",
                     options:["IMC-W2-PTZ index","Eagle MPplusII Instrument"]}

Bot:  For live correction — an IMC-W2-PTZ index, or an Eagle MPplusII?
Cust: eagle

  POST {...+ "rotary_roots.live":"Eagle MPplusII Instrument"}
  -> stage "options"
     questions[0] = {id:"rotary_roots.eagle_type",
                     options:["Volume Corrector","Rotary Corrector"]}

Bot:  Volume corrector or rotary corrector?
Cust: rotary

  POST {...+ "rotary_roots.eagle_type":"Rotary Corrector"}
  -> stage "complete"
     part_numbers: ["M.RT3M-175.FLG.CTR.NA.175.VDN.NA.NA.NA",
                    "I.MPP-MRC.102.N.N.N.TC.INTEG.CCW.N.ALK.8X6"]

Bot:  Rotary (roots) Meter — Dresser 3M175, 2", 15,200 CFH at 60 psi.
      Part number M.RT3M-175.FLG.CTR.NA.175.VDN.NA.NA.NA
      With an Eagle MPplusII Rotary Corrector, 0-102 psi transducer,
      part number I.MPP-MRC.102.N.N.N.TC.INTEG.CCW.N.ALK.8X6
```

---

## 13. Bot behaviour: do and don't

**Do**

- Offer the exact `options` strings, every time, from the response.
- Ask one question per turn.
- Read `warnings` out — they carry assumptions the customer needs.
- Read `quote_note` verbatim when there is no part number.
- Say the `model` (`3M175`), not the `meter` code (`DR3M175`).
- Present the Eagle as a separate item.
- Offer to size again with a different type if the customer wants to compare.
- State the assumed compensation on a 23M or larger, from `identity_fields`.

**Don't**

- Don't compute capacities, pick meters, or assemble part numbers yourself.
- Don't offer a meter type that isn't in the current `options`.
- Don't send more than one meter type. To compare two, size twice.
- Don't invent a part number for a quote-only meter.
- Don't treat `available: false` as an error — it is a normal outcome.
- Don't round capacities yourself; use the formatted `fields`.
- Don't assume `size` exists — the 10C25 has none.
- Don't cache a result against a customer's earlier figures. Any change to
  pressure or flow means a fresh call.

---

## 14. Keeping the chatbot and the website in step

Both run the same rules, but by different routes:

| | Rules from | Updated by |
| --- | --- | --- |
| Website block | `dist/usg-meter-sizing.js` via jsDelivr, pinned to a commit | bumping the pinned hash in the CMS block |
| Chatbot | `algorithm/meter_sizing.py` | redeploying the API service |

**They can drift if only one is updated.** After a rules change, do both.

To change a rule:

1. Edit `algorithm/meter_sizing.py` **and** `src/js/meter_sizing.js`. They are
   the same rules written twice; changing one alone fails CI.
2. `python tools/build.py`
3. `python tests/test_parity.py` and `npm test`
4. Commit and push. CI re-runs everything.
5. Update the pinned commit hash in the CMS block.
6. Redeploy the API service.

To change a capacity table: edit `data/capacities.xlsx`, run
`python tools/extract_capacities.py data/capacities.xlsx`, then from step 2.

### Verifying what is live

- Website: `window.USGMeterSizing.VERSION` in the browser console returns a
  short hash of the sources the loaded bundle was built from. Run
  `tools/build.py` locally and compare.
- Chatbot: `GET /health` returns `probe_passed: true` only if a known job
  still sizes to its known part number.

---

## 15. Tests

| File | What it protects |
| --- | --- |
| `tests/test_parity.py` | 611 cases run through both implementations and diffed, plus 60 fixed expectations taken from the sizing instructions |
| `tests/test_block.js` | drives the real CMS block in jsdom through the whole question flow |
| `tests/test_pdf.js` | renders the PDF and reads the text back |
| `tests/test_no_usg.js` | sweeps every customer-visible surface for old branding |

Run all: `python tests/test_parity.py && npm test`.

The fixed expectations matter as much as the parity check: parity proves the
two builds agree, the expectations prove they agree on the *right* answer.

---

## 16. Open items

- **Turbine ANSI class and Eagle transducer band are taken from the inlet
  pressure**, as the sizing instructions say. The flange class arguably belongs
  on a maximum operating pressure instead. `ansi_class` and `eagle_p1` are the
  two call sites if that changes.
- **The Eagle's rotation and case are read from part-number tokens** (`CCW`/`CW`,
  `8X6`/`12X10`) rather than hard-coded, but nothing currently selects the
  clockwise drive or the 12"x10" case. If either is a real option, something
  has to choose it.
- **The inlet range applies to the entered number, not the converted psi.** See
  §5.
- **`window.USGMeterSizing` and `dist/usg-meter-sizing.js` still read "usg"** on
  purpose — neither is customer-visible, and holding them steady lets the block
  and the bundle deploy in either order. Everything a customer sees reads HSC.
