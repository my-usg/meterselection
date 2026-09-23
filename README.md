# Meter Sizing Tool

Sizes a gas meter from an inlet pressure and a flow rate, asks the follow-up
questions the choice depends on, and builds the part number.

Same arrangement as [`sizingtool`](https://github.com/my-usg/sizingtool) (the
regulator tool): the rules live here in Python, a generated JavaScript build is
served to the website from this repository through jsDelivr, and the website
block contains no sizing logic at all. The chatbot calls the same Python over
HTTP, so the bot and the page cannot give different answers.

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
        |                             |  jsDelivr
        v                             v
     chatbot                    block/block.html   (Concrete CMS)
```

`tests/test_parity.py` runs both implementations over every case in
`tests/cases.json` and fails if they disagree, so the two copies of the rules
cannot drift apart unnoticed.

## Integrating a chatbot

`docs/CHATBOT_INTEGRATION.md` is the full contract: the conversation loop,
every input and output field, every question id with its branches, the five
terminal states a bot has to handle, and a worked transcript. Read it before
wiring anything up.

## Checking what is live

`window.USGMeterSizing.VERSION` in the browser console returns a short hash of
the sources the loaded bundle was built from. Run `python tools/build.py` in a
clean checkout and the first line of `dist/usg-meter-sizing.js` carries the
same stamp, so the two can be compared. It is deliberately not a git commit:
the build must be reproducible or CI's staleness check could never pass.

## Publishing a change

1. Edit `algorithm/meter_sizing.py` **and** `src/js/meter_sizing.js`. They are
   the same rules written twice; changing one alone will fail CI.
2. `python tools/build.py` to regenerate `dist/usg-meter-sizing.js`.
3. `python tests/test_parity.py`, then `npm test` for the two browser tests.
4. Commit and push. The website picks it up automatically; jsDelivr caches a
   branch URL for up to 12 hours.

To change a capacity table, edit `data/capacities.xlsx`, run
`python tools/extract_capacities.py data/capacities.xlsx`, then rebuild.
Commit the workbook and the JSON together.

The block tracks `@main`:

```html
<script src="https://cdn.jsdelivr.net/gh/my-usg/meterselection@main/dist/usg-meter-sizing.js"></script>
```

Pin a release tag (`@v1.0.0`) instead if you want changes to reach the site
only when you say so.

## Setup

```bash
pip install -r requirements.txt
npm install
python tools/build.py
python tests/test_parity.py
node tests/test_block.js
node tests/test_pdf.js
uvicorn api.main:app --reload      # chatbot endpoint on :8000
```

## The API

Sizing a regulator is one shot. Sizing a meter is not: once the tool knows
which meters have the capacity, it has to ask which meter **type** the
customer wants, and then a different set of follow-up questions depending on
the answer — ferrule size, pulse output, pressure compensation, index.

Rather than hold that conversation in the web page, where the chatbot could
not reuse it, the algorithm is a **stateless resolver**. Every call takes the
whole answer set collected so far and returns the questions still outstanding.
Both callers keep the payload and add to it.

```
POST /api/meter-sizing
{ "inlet": 60, "inlet_units": "psi", "flow": 9000, "flow_units": "CFH" }

  -> "stage": "meter_type"
     "questions": [ { "id": "meter_type", "type": "single_select",
                      "options": ["Rotary (roots)", "Ultrasonic", "Turbine"] } ]

POST { ...same..., "meter_types": ["Rotary (roots)"] }

  -> "stage": "options"
     "questions": [ { "id": "rotary_roots.compensation",
                      "options": ["None", "Fix-Factored", "Live"] } ]

POST { ...same..., "answers": { "rotary_roots.compensation": "Fix-Factored" } }

  -> "stage": "options"
     "questions": [ { "id": "rotary_roots.index",
                      "options": ["ETC", "ES3", "IMC-W2-PTZ",
                                  "Eagle MPplusII Instrument"] } ]

POST { ...same..., "answers": { "rotary_roots.compensation": "Fix-Factored",
                                "rotary_roots.index": "ETC" } }

  -> "stage": "complete"
     "part_numbers": ["M.RT3M-175.FLG.ETC.CIR.175.VDN.NA.LITH.NA"]
```

The same call in the browser:

```js
window.USGMeterSizing.sizeMeters({
  inlet: 60, inlet_units: "psi",
  flow: 9000, flow_units: "CFH",
  meter_types: ["Rotary (roots)"],
  answers: { "rotary_roots.compensation": "Fix-Factored",
             "rotary_roots.index": "ETC" }
});
```

### Input

| Field | Notes |
| --- | --- |
| `inlet` | 0–1440, in `inlet_units` |
| `inlet_units` | `psi`, `in wc`, `oz`, `bar`, `kPa` |
| `flow` | 0–100,000,000, in `flow_units` |
| `flow_units` | `CFH`, `BTUH`, `CMH` |
| `meter_types` | the answer to the `meter_type` question; a list holding exactly one |
| `answers` | option answers keyed by question `id` |

### Output

| Field | Notes |
| --- | --- |
| `ok` | `false` only for a rejected input; `errors` says why |
| `stage` | `error`, `meter_type`, `options`, `complete` |
| `questions` | what is still outstanding. Empty exactly when `stage` is `complete` |
| `meter_type_question` | the type question, always present, so the page can leave it on screen |
| `results` | one entry per chosen type, in tier order |
| `part_numbers` | every part number the run produced, meters then Eagle |
| `eagle` | the Eagle instrument, or `null`. A separate line item, never part of a meter's part number |
| `selected` | true when at least one type reached a finished selection |
| `summary` | the inputs as label/value pairs, for the PDF |
| `warnings` | anything the customer should read, including assumptions |
| `evaluations` | every meter in the tables with its capacity and, if it failed, why |

Each entry in `results` carries `identity_fields` (manufacturer, model, size,
capacity), `answer_fields` (the options as answered), and `fields` — the two
concatenated. Render the questions as live widgets and use `identity_fields`;
render the finished selection as text and use `fields`. `lines` is the same
content in the flat form the sizing instructions' sample outputs use.

A rejected input is still HTTP 200 with `ok: false`, so a chatbot can read the
reason out loud rather than handle a status code.

### Chatbot notes

- Offer the `options` from the `meter_type` question the sizing call returned,
  never a fixed list. Which types apply depends on the pressure and flow.
- Ask the questions in the order they come back. The conditional branches only
  appear once the answer they depend on is present, so the roots index tree
  walks one turn at a time on its own.
- Keep the payload in the conversation state and add to it. Do not rebuild it
  from the previous reply.
- `answers` keys are namespaced by meter type (`rotary_roots.compensation`),
  so two types selected at once do not collide.
- Read `warnings` out. One of them flags an assumption in a part number (see
  "Open questions" below).

## The rules

**Units.** Everything converts to psi and CFH first: 28 in wc = 1 psi,
16 oz = 1 psi, 1 bar = 14.5038 psi, 6.89476 kPa = 1 psi, 1 CMH = 35.3147 CFH,
1000 BTUH = 1 CFH.

**Does a meter work.** Its capacity at the inlet pressure, linearly
interpolated from the table, must cover the flow plus the family's oversize
allowance — 0% for the small meters and RMG, 10% for roots and turbo. It must
be rated for the inlet pressure. If it has a minimum capacity, that minimum
must be at or below the flow.

The inlet pressure and the flow rate are the only inputs. An earlier draft took
a MAOP as well; it is gone, and a `maop` key left in a payload by a stale caller
is ignored rather than sized against.

**Which types are offered.** The rung the job lands on, plus the next one up:

| The smallest thing that works | Types offered |
| --- | --- |
| SR275 | Diaphragm, Sonix IQ |
| SIQ250 / SIQ425 | Sonix IQ, Ultrasonic |
| Sonix600 / Sonix880 | Ultrasonic, Rotary (meter bar), Rotary (straight pipe) |
| DD800 / DD1000 | Rotary (meter bar), Rotary (straight pipe), Rotary (roots) |
| nothing above | Rotary (roots), Ultrasonic, Turbine |

"Ultrasonic" means Sonix 600/880 in the middle tiers and RMG in the top tier,
which is why the type-to-meter mapping is held per tier rather than globally.

A type can be offered and still have no meter behind it: the straight-pipe
10C25 has less capacity than the meter-bar models beside it, so it can fall
short at a flow its own tier clears. That comes back as a `results` entry with
`available: false` and the reason.

**Capacity table holes.** Cells reading `N/A` (small meters) or `0` (roots,
above a model's rating) are dropped when the workbook is extracted, so the last
pressure stored for a model *is* its pressure limit and interpolation can never
run through a gap.

## Decisions worth knowing about

Confirmed with Holland Supply, and each one line to change if that ever moves:

**A roots meter with an Eagle corrector** carries a drive rather than an index,
and which drive depends on the corrector — `CD` for a volume corrector, `CTR`
for a rotary one. The same either way on the fix-factored and the live path,
and both take the `NA` / `NA` surrounding segments. `ROOTS_EAGLE_INDEX` in both
implementations.

```
Volume Corrector   M.RT3M-175.FLG.CD.NA.175.VDN.NA.NA.NA  + I.MPP-MVC…
Rotary Corrector   M.RT3M-175.FLG.CTR.NA.175.VDN.NA.NA.NA + I.MPP-MRC…
```

**The rating field in the roots part number** is present only on the 175-rated
models. The 232 psi `DR23M232` omits the segment altogether — the model token
already says 232 — so that meter is one field shorter than the template:

```
3M-175   M.RT3M-175.FLG.TC.NA.175.VDN.NA.NA.NA
23M-232  M.RT23M-232.FLG.TC.NA.VDN.NA.NA.NA
```

The field is absent, not blank. `build_part_number`'s roots branch in both
implementations.

**Fix-factored roots with an Eagle** also asks the corrector-type question that
the live path asks, since the Eagle part number cannot be built without it.

**Roots meters from the 23M up ask nothing at all.** Their compensation is
always live, their correction always an Eagle, and the corrector always a
volume corrector, so the three questions are skipped and the assumption is
printed with the selection instead. The cut-off is read off the roots tab
(`ROOTS_FORCED_EAGLE_FROM`) rather than written out as a list of model codes:
the columns run in ascending capacity, so a model added to the sheet later
falls on the correct side of the line on its own.

**The Sonix 600/880, D800, D1000 and 10C25 report `Pulse Output: Included`**
directly below the size. It is stated rather than asked, because on those
meters it is standard. The Sonix IQ pair is not in that list: theirs is an
option, so it stays a yes/no question. `PULSE_OUTPUT_INCLUDED` in both
implementations.

**An unpriced part says "Contact Holland Supply for pricing"** rather than
leaving the price slot blank, which reads as free or as a broken page. Three
outcomes are kept apart, in `fetchPrice`:

| Lookup result | Shown |
| --- | --- |
| a price | the figure |
| HTTP 404, or 0.00, or a response with no amount | the contact note |
| any other status, or the request failing | nothing |

The last row is the important one: a price endpoint that is down or blocked
must not tell every customer to ring in about parts that are priced perfectly
well. The console line says which case it was.

An unpriced part still contributes no line-item total (the total only shows
when every part on screen has a price, so a zero or a gap would understate the
order), and its **lead time is unaffected** — price and availability are
separate lookups sharing one panel.

**Capacities print to the whole CFH.** Interpolation lands on fractions and a
fraction of a cubic foot per hour is noise. `_fmt_cfh` / `fmtCfh`, which use
`floor(n + 0.5)` rather than a language `round` so the two builds agree on
exact halves - Python rounds those to even and JavaScript rounds them up.

**The Eagle's rotation and case size are read from the part-number tokens**
(`CCW` / `CW` and `8X6` / `12X10`) rather than hard-coded, so a printed
description cannot contradict the number beside it. Today every Eagle is
built `CCW` and `8X6`; nothing in the sizing instructions selects the
clockwise drive or the 12"x10" case, so if either is a real option something
has to choose it.

Two places where the sizing instructions did not fully determine the behaviour:

**Inlet pressure below 0.25 psi.** The tables start at 0.25 psi (about 7 in wc)
and a good many jobs come in at or under that, so the 0.25 psi row is used
unchanged rather than extrapolated down. Every such run carries a warning.
`interpolate()` in both implementations is where to change it.

**Turbo ANSI class and Eagle transducer range** are taken from the inlet
pressure, as written — `ansi_class` and `eagle_p1`.

**The inlet range is 0–1440**, the top row of the turbo capacity table and so
the highest pressure any meter in the tables is rated for. `MAX_INLET` in both
implementations, and `max` on the input in the block.

**A result is sized for the inputs as they were when Run Sizing was clicked**,
not as they are now. Editing a pressure or flow afterwards does not re-size
anything until it is clicked again — re-running on every keystroke would size
against half-typed numbers. The block dims the result and says so whenever the
form has drifted from the run it is showing, which is the only thing that makes
the frozen snapshot safe.

## Layout

```
algorithm/meter_sizing.py     the rules. SOURCE OF TRUTH
src/js/meter_sizing.js        the JS port, with a placeholder for the tables
dist/usg-meter-sizing.js      GENERATED. Do not edit. Served to the website
data/capacities.xlsx          capacity tables, as maintained
data/capacities.json          capacity tables, as shipped
api/main.py                   HTTP wrapper for the chatbot
block/block.html              the Concrete CMS block
tools/extract_capacities.py   workbook -> JSON
tools/build.py                template + JSON -> dist bundle
tools/gen_cases.py            regenerate the test case set
tests/cases.json              611 cases: every tier, family and answer branch
tests/test_parity.py          Python vs JS, plus fixed expected outputs
tests/test_block.js           drives block.html in jsdom
tests/test_pdf.js             renders the PDF and reads the text back
tests/test_no_usg.js          sweeps every customer-visible surface for "USG"
docs/CHATBOT_INTEGRATION.md   the chatbot contract and continuity guide
```

## Deploying the block

Paste `block/block.html` into a Concrete CMS HTML block. The site's Content
Security Policy must allow:

- `script-src https://cdn.jsdelivr.net` — the algorithm bundle
- `script-src https://cdnjs.cloudflare.com` — jsPDF, already allowed for the
  regulator tool
- `connect-src https://orchestrator.hsc.faxon.tech` — lead times, already
  allowed

Every class and element ID in this block is prefixed `hsc-` / `hscm-`, against
the regulator tool's `usg-`, so the two blocks can sit on one page without
colliding. The stylesheet down to the `METER TOOL ADDITIONS` marker is still
the regulator tool's rules with the prefix and root selector swapped — worth
keeping in step when either changes.

## Naming

Customer-facing text, the CSS prefix, the browser global and the bundle
filename all read HSC. Three things deliberately still say USG, because they
are real names rather than branding:

- the GitHub organisation, `my-usg`
- `https://github.com/my-usg/sizingtool`, the regulator tool's repository
- a comment referring to `usg-441-configurator.html`, the file the chip
  styling was copied from

The bundle also exposes `window.USGMeterSizing` as an alias of
`window.USGMeterSizing`, so a CMS block on the old name keeps working against
a new bundle. Once the block is live, the alias at the foot of
`src/js/meter_sizing.js` can be deleted.
