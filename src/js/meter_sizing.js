/* Holland Supply meter sizing algorithm - browser build.
 *
 * GENERATED FILE. Do not edit dist/usg-meter-sizing.js. Edit this template
 * (src/js/meter_sizing.js) and run `python tools/build.py`; the capacity
 * tables are injected from data/capacities.json at build time so there is one
 * copy of the numbers, not two.
 *
 * This is a line-for-line port of algorithm/meter_sizing.py, which is the
 * source of truth for the rules. tests/test_parity.py runs both against
 * tests/cases.json and fails the build if they disagree, so a change made
 * here without the matching change in Python (or the reverse) does not ship.
 *
 * ES5 on purpose: it loads into the same CMS pages as the regulator tool and
 * is served straight from jsDelivr with no transpile step.
 *
 * Exposes:
 *   window.USGMeterSizing.sizeMeters(input) -> result object
 *   window.USGMeterSizing.PRESSURE_UNITS / FLOW_UNITS / VERSION
 *
 * The global keeps its original name on purpose: nothing a customer sees
 * carries it, and holding it steady means the CMS block and the bundle can be
 * deployed in either order.
 */
(function (root) {
  "use strict";

  var CAPACITIES = /*__CAPACITIES__*/ null;
  // A short hash of the sources this bundle was built from, stamped by
  // tools/build.py. Not a git commit: the build has to be reproducible or
  // the staleness check in CI can never pass. Recompute it locally to
  // confirm the live page is running the sources you think it is.
  var VERSION = /*__VERSION__*/ "dev";

  var FAMILIES = CAPACITIES.families;

  // model code -> its row, with the family it came from
  var BY_MODEL = {};
  var MODEL_ORDER = [];
  (function () {
    var keys = ["small", "roots", "turbo", "rmg"];
    for (var k = 0; k < keys.length; k++) {
      var fam = FAMILIES[keys[k]];
      for (var i = 0; i < fam.meters.length; i++) {
        var m = fam.meters[i];
        BY_MODEL[m.model] = {
          model: m.model,
          size: m.size,
          has_min: m.has_min,
          points: m.points,
          family: keys[k]
        };
        MODEL_ORDER.push(m.model);
      }
    }
  })();

  // ---- unit conversion -----------------------------------------------
  // Everything is converted to psi and CFH before any table is touched.
  var PRESSURE_TO_PSI = {
    "psi": 1.0,
    "in wc": 1.0 / 28.0,   // 28 in wc = 1 psi
    "oz": 1.0 / 16.0,      // 16 oz = 1 psi
    "bar": 14.5038,
    "kpa": 1.0 / 6.89476
  };
  var FLOW_TO_CFH = {
    "cfh": 1.0,
    "cmh": 35.3147,
    "btuh": 1.0 / 1000.0   // 1000 BTUH = 1 CFH
  };
  var PRESSURE_UNITS = ["psi", "in wc", "oz", "bar", "kPa"];

  // The accepted range for the entered figure, in whatever units it is
  // entered in. 1440 is the highest pressure the capacity tables cover (the
  // top row of the turbo tab); above it nothing is rated, so there is nothing
  // to size. It is also what makes the Eagle's 0-1450 band reachable.
  var MAX_INLET = 1440;
  var MAX_FLOW = 100000000;
  var FLOW_UNITS = ["CFH", "BTUH", "CMH"];

  function toPsi(value, units) {
    var f = PRESSURE_TO_PSI[String(units == null ? "" : units).trim().toLowerCase()];
    if (f === undefined) throw new Error("Unknown pressure units: " + units);
    return Number(value) * f;
  }
  function toCfh(value, units) {
    var f = FLOW_TO_CFH[String(units == null ? "" : units).trim().toLowerCase()];
    if (f === undefined) throw new Error("Unknown flow units: " + units);
    return Number(value) * f;
  }

  // ---- capacity lookup -----------------------------------------------
  function ratingPsi(model) {
    var pts = BY_MODEL[model].points;
    return pts[pts.length - 1].p;
  }

  // Linear interpolation of a model's capacity table. Below the first
  // tabulated pressure the first row is used unchanged: the tables start at
  // 0.25 psi (about 7 in wc) and plenty of jobs come in at or under that, so
  // clamping keeps them sizeable. sizeMeters warns whenever it happens.
  function interpolate(model, psi, key) {
    key = key || "max";
    var pts = BY_MODEL[model].points;
    if (!(key in pts[0])) return null;
    if (psi <= pts[0].p) return pts[0][key];
    if (psi >= pts[pts.length - 1].p) return pts[pts.length - 1][key];
    for (var i = 1; i < pts.length; i++) {
      var hi = pts[i];
      if (psi <= hi.p) {
        var lo = pts[i - 1];
        var span = hi.p - lo.p;
        if (span === 0) return hi[key];
        return lo[key] + ((psi - lo.p) / span) * (hi[key] - lo[key]);
      }
    }
    return pts[pts.length - 1][key];
  }

  // Numbers for customer-facing text: thousands separated, no float noise.
  function fmt(n) {
    if (n === null || n === undefined) return "";
    n = Number(n);
    var s;
    if (Math.abs(n - Math.round(n)) < 0.005) {
      s = String(Math.round(n));
    } else {
      s = n.toFixed(2).replace(/0+$/, "").replace(/\.$/, "");
    }
    var parts = s.split(".");
    parts[0] = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, ",");
    return parts.join(".");
  }

  // Capacities, always to the whole CFH. Interpolating between two tabulated
  // rows lands on fractions - 2,698.5 CFH and the like - and a fraction of a
  // cubic foot per hour is noise next to a meter's rating. fmt() is left alone
  // because it also formats the entered pressure and flow, where a decimal the
  // customer typed has to survive.
  function fmtCfh(n) {
    if (n === null || n === undefined) return "";
    var s = String(Math.round(Number(n)));
    return s.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  }

  // Does this meter work for the job? Returns a record either way, with the
  // reason it does not, which is what the "no meter available" copy quotes.
  function evaluate(model, inletPsi, flowCfh) {
    var spec = BY_MODEL[model];
    var oversize = FAMILIES[spec.family].oversize;
    var limit = ratingPsi(model);
    var required = flowCfh * (1.0 + oversize);

    var result = {
      model: model,
      family: spec.family,
      size: spec.size,
      oversize: oversize,
      rating_psi: limit,
      required_cfh: required,
      capacity_cfh: null,
      min_capacity_cfh: null,
      works: false,
      reason: null
    };

    // Pressure first: interpolating a capacity for a pressure the meter is
    // not rated for would produce a number nobody should see.
    if (inletPsi > limit + 1e-9) {
      result.reason = "rated to " + fmt(limit) + " psi, below the " +
        fmt(inletPsi) + " psi inlet pressure";
      return result;
    }

    var cap = interpolate(model, inletPsi, "max");
    result.capacity_cfh = cap;
    if (spec.has_min) {
      result.min_capacity_cfh = interpolate(model, inletPsi, "min") || 0.0;
    }

    if (cap + 1e-9 < required) {
      result.reason = "capacity " + fmtCfh(cap) + " CFH is below the " +
        fmtCfh(required) + " CFH required" +
        (oversize ? " (" + Math.round(oversize * 100) + "% oversize)" : "");
      return result;
    }

    var minCap = result.min_capacity_cfh;
    if (minCap && minCap > flowCfh + 1e-9) {
      result.reason = "minimum capacity " + fmtCfh(minCap) +
        " CFH is above the " + fmtCfh(flowCfh) + " CFH load";
      return result;
    }

    result.works = true;
    return result;
  }

  // ---- meter identity ------------------------------------------------
  var MANUFACTURER = {
    SR275: "Sensus", SIQ250: "Sensus", SIQ425: "Sensus",
    Sonix600: "Sensus", Sonix880: "Sensus",
    DD800: "Dresser", DD1000: "Dresser", DD10C25: "Dresser"
  };
  var MODEL_NAME = {
    SR275: "R275", SIQ250: "Sonix IQ 250", SIQ425: "Sonix IQ 425",
    Sonix600: "Sonix 600", Sonix880: "Sonix 880",
    DD800: "D800", DD1000: "D1000", DD10C25: "10C25"
  };
  var FAMILY_MANUFACTURER = { roots: "Dresser", turbo: "Sensus", rmg: "RMG" };

  // Small meters are a lookup. The tabbed families follow a pattern: roots
  // DR8C175 -> 8C175, turbo T18 -> T-18, and every RMG column is the one
  // model RSM200 in a different line size.
  function displayModel(model) {
    if (MODEL_NAME[model]) return MODEL_NAME[model];
    var fam = BY_MODEL[model].family;
    if (fam === "roots") return model.indexOf("DR") === 0 ? model.slice(2) : model;
    if (fam === "turbo") return model.charAt(0) === "T" ? "T-" + model.slice(1) : model;
    if (fam === "rmg") return "RSM200";
    return model;
  }
  function displayManufacturer(model) {
    if (MANUFACTURER[model]) return MANUFACTURER[model];
    return FAMILY_MANUFACTURER[BY_MODEL[model].family] || "";
  }

  // ---- meter type tiers ----------------------------------------------
  // Which meter types the customer is offered depends on how far up the range
  // the job sits: the rung it lands on plus the next one up. "Ultrasonic"
  // means Sonix 600/880 in the middle tiers and RMG in the top tier, which is
  // why resolution is stored per tier rather than globally.
  var DIAPHRAGM = "Diaphragm";
  var SONIX_IQ = "Sonix IQ";
  var ULTRASONIC = "Ultrasonic";
  var ROTARY_BAR = "Rotary (meter bar)";
  var ROTARY_PIPE = "Rotary (straight pipe)";
  var ROTARY_ROOTS = "Rotary (Roots)";
  var TURBINE = "Turbine";

  var SLUG = {};
  SLUG[DIAPHRAGM] = "diaphragm";
  SLUG[SONIX_IQ] = "sonix_iq";
  SLUG[ULTRASONIC] = "ultrasonic";
  SLUG[ROTARY_BAR] = "rotary_meter_bar";
  SLUG[ROTARY_PIPE] = "rotary_straight_pipe";
  SLUG[ROTARY_ROOTS] = "rotary_roots";
  SLUG[TURBINE] = "turbine";

  // ["pick", [models in preference order]] tries each in turn.
  // ["smallest", family]                   walks the tab left to right.
  var PICK_SIQ = ["pick", ["SIQ250", "SIQ425"]];
  var PICK_SONIX = ["pick", ["Sonix600", "Sonix880"]];
  var PICK_DD = ["pick", ["DD800", "DD1000"]];

  var TIERS = [
    {
      id: "sr275",
      trigger: ["SR275"],
      types: [DIAPHRAGM, SONIX_IQ],
      resolve: (function () {
        var r = {}; r[DIAPHRAGM] = ["pick", ["SR275"]]; r[SONIX_IQ] = PICK_SIQ; return r;
      })()
    },
    {
      id: "siq",
      trigger: ["SIQ250", "SIQ425"],
      types: [SONIX_IQ, ULTRASONIC],
      resolve: (function () {
        var r = {}; r[SONIX_IQ] = PICK_SIQ; r[ULTRASONIC] = PICK_SONIX; return r;
      })()
    },
    {
      id: "sonix",
      trigger: ["Sonix600", "Sonix880"],
      types: [ULTRASONIC, ROTARY_BAR, ROTARY_PIPE],
      resolve: (function () {
        var r = {};
        r[ULTRASONIC] = PICK_SONIX;
        r[ROTARY_BAR] = PICK_DD;
        r[ROTARY_PIPE] = ["pick", ["DD10C25"]];
        return r;
      })()
    },
    {
      id: "dresser",
      trigger: ["DD800", "DD1000"],
      types: [ROTARY_BAR, ROTARY_PIPE, ROTARY_ROOTS],
      resolve: (function () {
        var r = {};
        r[ROTARY_BAR] = PICK_DD;
        r[ROTARY_PIPE] = ["pick", ["DD10C25"]];
        r[ROTARY_ROOTS] = ["smallest", "roots"];
        return r;
      })()
    },
    {
      // The top tier is the fallback: nothing in the small-meter tab works.
      id: "large",
      trigger: null,
      types: [ROTARY_ROOTS, ULTRASONIC, TURBINE],
      resolve: (function () {
        var r = {};
        r[ROTARY_ROOTS] = ["smallest", "roots"];
        r[ULTRASONIC] = ["smallest", "rmg"];
        r[TURBINE] = ["smallest", "turbo"];
        return r;
      })()
    }
  ];

  function resolveModel(rule, evals) {
    var kind = rule[0], arg = rule[1], i;
    if (kind === "pick") {
      for (i = 0; i < arg.length; i++) if (evals[arg[i]].works) return arg[i];
      return null;
    }
    var meters = FAMILIES[arg].meters;
    for (i = 0; i < meters.length; i++) {
      if (evals[meters[i].model].works) return meters[i].model;
    }
    return null;
  }

  // ---- option questions ----------------------------------------------
  // Ferrule lists differ by meter (only the R275 offers 45LT alongside the 1A
  // and 1-1/4 options), so they are held per meter code rather than shared.
  var FERRULES = {
    SR275: ["10LT", "20LT", "30LT", "45LT", "1A", "1-1/4"],
    SIQ250: ["10LT", "20LT", "30LT", "1A", "1-1/4"],
    SIQ425: ["10LT", "20LT", "30LT", "1A", "1-1/4"],
    Sonix600: ["20LT", "30LT", "45LT"],
    Sonix880: ["20LT", "30LT", "45LT"],
    DD800: ["30LT", "45LT", "1-1/2"],
    DD1000: ["30LT", "45LT", "1-1/2"]
  };
  var CONNECTIONS = { DD10C25: ["30LT", "45LT", "1-1/2"] };

  // Meters that ship with a pulse output as standard, reported as a fact
  // rather than asked about. The Sonix IQ pair is deliberately absent: theirs
  // is an option, so it stays a yes/no question (see optionQuestions).
  var PULSE_OUTPUT_INCLUDED = ["Sonix600", "Sonix880", "DD800", "DD1000", "DD10C25"];
  var COMPENSATION = ["None", "Fix-Factored", "Live"];
  var EAGLE_INSTRUMENT = "Eagle MPplusII Instrument";
  var IMC = "IMC-W2-PTZ";
  var EAGLE_TYPES = ["Volume Corrector", "Rotary Corrector"];

  // Roots meters from the 23M up are always corrected by a live Eagle volume
  // corrector, so the compensation, correction and corrector-type questions
  // are not asked for them - there is nothing to choose. Confirmed with
  // Holland Supply.
  //
  // The cut-off is read off the tab rather than written out as a list of
  // model codes: the columns run in ascending capacity, so "23M and larger" is
  // "at or right of the 23M column", and a model added to the sheet later
  // falls on the correct side of the line without this constant being
  // touched.
  var ROOTS_FORCED_EAGLE_FROM = "DR23M232";
  var FORCED_COMPENSATION = "Live";
  var FORCED_EAGLE_TYPE = "Volume Corrector";

  // True when this roots meter's corrector is not a choice.
  function rootsForcedEagle(model) {
    if (BY_MODEL[model].family !== "roots") return false;
    var meters = FAMILIES.roots.meters, mine = -1, cut = -1;
    for (var i = 0; i < meters.length; i++) {
      if (meters[i].model === model) mine = i;
      if (meters[i].model === ROOTS_FORCED_EAGLE_FROM) cut = i;
    }
    if (cut === -1 || mine === -1) return false;
    return mine >= cut;
  }

  // What was assumed for a forced meter, so the customer can see it. These
  // meters ask no questions, and a selection that silently acquired a live
  // Eagle would be a selection nobody could check.
  function rootsAssumedFields(model) {
    if (!rootsForcedEagle(model)) return [];
    return [
      { label: "Pressure compensation", value: FORCED_COMPENSATION },
      { label: "Correction", value: EAGLE_INSTRUMENT }
    ];
  }

  function q(id, label, options, kind) {
    return { id: id, label: label, type: kind || "single_select", options: options.slice() };
  }

  // The option questions for one chosen meter type, in the order they should
  // be asked. Conditional branches only appear once the answer they depend on
  // is present, which is what lets the chatbot walk the roots index tree one
  // turn at a time and the web block reveal the same widgets progressively.
  function optionQuestions(slug, model, answers) {
    var out = [];
    var fam = BY_MODEL[model].family;
    function get(name) { return answers[slug + "." + name]; }

    if (FERRULES[model]) out.push(q(slug + ".ferrule", "Ferrule size", FERRULES[model]));
    if (CONNECTIONS[model]) out.push(q(slug + ".connection", "Connection size", CONNECTIONS[model]));
    if (model === "SIQ250" || model === "SIQ425") {
      out.push(q(slug + ".pulse", "Pulse output", ["Yes", "No"], "yes_no"));
    }

    if (fam === "roots") {
      // A 23M or larger asks nothing: its compensation, correction and
      // corrector type are all fixed. See rootsForcedEagle.
      if (rootsForcedEagle(model)) return out;
      out.push(q(slug + ".compensation", "Pressure compensation", COMPENSATION));
      var comp = get("compensation");
      if (comp === "None") {
        out.push(q(slug + ".radio", "Pulse Output or AMR adapter required",
          ["Yes", "No"], "yes_no"));
        if (get("radio") === "Yes") {
          out.push(q(slug + ".index", "Index", ["TC/AMR", "ETC", "ES3"]));
        }
        // A "No" needs no index question: the rules fix it at TC.
      } else if (comp === "Fix-Factored") {
        out.push(q(slug + ".index", "Index", ["ETC", "ES3", IMC, EAGLE_INSTRUMENT]));
        if (get("index") === EAGLE_INSTRUMENT) {
          out.push(q(slug + ".eagle_type", "Eagle type", EAGLE_TYPES));
        }
      } else if (comp === "Live") {
        out.push(q(slug + ".live", "Correction", [IMC + " index", EAGLE_INSTRUMENT]));
        if (get("live") === EAGLE_INSTRUMENT) {
          out.push(q(slug + ".eagle_type", "Eagle type", EAGLE_TYPES));
        }
      }
    } else if (fam === "rmg") {
      out.push(q(slug + ".compensation", "Pressure compensation", COMPENSATION));
    } else if (fam === "turbo") {
      out.push(q(slug + ".compensation", "Pressure compensation", COMPENSATION));
      if (get("compensation") === "None") {
        out.push(q(slug + ".slot", "High-Frequency Slot Sensor Option",
          ["None", "Conduit Connection", "Bendix Plug In Connection"]));
      }
    }
    return out;
  }

  // ---- part numbers --------------------------------------------------
  var QUOTE_NOTE = "Contact Holland Supply for a quote";
  var ROOTS_MODEL_RE = /^DR(\d+[CM])(\d+)$/;

  // DR15C175 -> ["15C-175", "175"]: the part-number token and the rating.
  function rootsModelParts(model) {
    var m = ROOTS_MODEL_RE.exec(model);
    if (!m) return [model, "175"];
    return [m[1] + "-" + m[2], m[2]];
  }

  function ansiClass(psi) {
    if (psi < 150) return "125";
    if (psi < 275) return "150";
    if (psi < 720) return "300";
    return "600";
  }

  function eagleP1(psi) {
    var steps = [[10, "10"], [50, "51"], [100, "102"], [290, "290"],
                 [500, "508"], [1050, "1050"]];
    for (var i = 0; i < steps.length; i++) {
      if (psi < steps[i][0]) return steps[i][1];
    }
    return "1450";
  }

  // A roots meter driving an external Eagle corrector carries a drive rather
  // than an index, and which drive depends on the corrector. Confirmed with
  // Holland Supply. Both take the same surrounding segments as any
  // non-ETC/ES3/IMC index, so A and B stay NA.
  var ROOTS_EAGLE_INDEX = {
    "Volume Corrector": "CD",
    "Rotary Corrector": "CTR"
  };
  // Used only if the corrector type is somehow missing: the part number is
  // not built until every question is answered, so this should be
  // unreachable.
  var ROOTS_EAGLE_INDEX_DEFAULT = "CD";

  function rootsEagleIndex(slug, answers) {
    return ROOTS_EAGLE_INDEX[answers[slug + ".eagle_type"]] ||
           ROOTS_EAGLE_INDEX_DEFAULT;
  }

  function rootsIndex(slug, answers, model) {
    if (model && rootsForcedEagle(model)) {
      return ROOTS_EAGLE_INDEX[FORCED_EAGLE_TYPE];
    }
    var comp = answers[slug + ".compensation"];
    if (comp === "None") {
      if (answers[slug + ".radio"] === "Yes") {
        var chosen = answers[slug + ".index"] || "TC";
        // TC/AMR is how the index is offered; TC is what the number carries.
        return chosen === "TC/AMR" ? "TC" : chosen;
      }
      return "TC";
    }
    if (comp === "Fix-Factored") {
      var picked = answers[slug + ".index"];
      if (picked === EAGLE_INSTRUMENT) return rootsEagleIndex(slug, answers);
      return picked || "ETC";
    }
    if (comp === "Live") {
      if (answers[slug + ".live"] === EAGLE_INSTRUMENT) {
        return rootsEagleIndex(slug, answers);
      }
      return IMC;
    }
    return "TC";
  }

  // -> { part_number, quote_note, warnings }. Either the number or the note.
  function buildPartNumber(model, slug, answers, inletPsi) {
    var warnings = [];
    function get(name) { return answers[slug + "." + name]; }
    var fam = BY_MODEL[model].family;
    var body, token, rating, index, a, b, comp, slot, conn, choice;

    if (model === "SR275") {
      return { part_number: "M.R275.TC.5.D/R." + get("ferrule") + ".TOP.NA",
               quote_note: null, warnings: warnings };
    }
    if (model === "SIQ250" || model === "SIQ425") {
      return { part_number: null, quote_note: QUOTE_NOTE, warnings: warnings };
    }
    if (model === "Sonix600" || model === "Sonix880") {
      body = model === "Sonix600" ? "SON-600" : "SON-880";
      return { part_number: "M." + body + "." + get("ferrule") + ".FIX.20.PO",
               quote_note: null, warnings: warnings };
    }
    if (model === "DD800" || model === "DD1000") {
      body = model === "DD800" ? "D800" : "D1000";
      return { part_number: "M." + body + "." + get("ferrule") + ".CBG.25.NA.LIT.NA",
               quote_note: null, warnings: warnings };
    }
    if (model === "DD10C25") {
      return { part_number: "M.RT10C25." + get("connection") + ".DI-T.BP.TOP.N/A.TIBT.N/A",
               quote_note: null, warnings: warnings };
    }
    if (fam === "rmg") {
      return { part_number: null, quote_note: QUOTE_NOTE, warnings: warnings };
    }
    if (fam === "roots") {
      token = rootsModelParts(model)[0];
      rating = rootsModelParts(model)[1];
      index = rootsIndex(slug, answers, model);
      if (index === "ETC" || index === "ES3") { a = "CIR"; b = "LITH"; }
      else if (index === IMC) { a = "CIR"; b = "ALK"; }
      else { a = "NA"; b = "NA"; }
      // The 232-rated 23M-232 does not carry a rating segment at all: the
      // model token already says 232, so the field is simply absent from that
      // meter's part number rather than blank. Every 175-rated model keeps
      // it. Confirmed with Holland Supply.
      //   23M-232 -> M.RT23M-232.FLG.TC.NA.VDN.NA.NA.NA
      //   3M-175  -> M.RT3M-175.FLG.TC.NA.175.VDN.NA.NA.NA
      var segments = ["M.RT" + token, "FLG", index, a];
      if (rating !== "232") segments.push(rating);
      segments = segments.concat(["VDN", "NA", b, "NA"]);
      return {
        part_number: segments.join("."),
        quote_note: null, warnings: warnings
      };
    }
    if (fam === "turbo") {
      comp = get("compensation");
      index = (comp === "Fix-Factored" || comp === "Live") ? "VCR" : "VDR";
      if (index === "VCR") { slot = "NA"; conn = "NA"; }
      else {
        choice = get("slot") || "None";
        if (choice.indexOf("Conduit") === 0) { slot = "HF-SS-C"; conn = "CND"; }
        else if (choice.indexOf("Bendix") === 0) { slot = "HF-SS-B"; conn = "PLUG"; }
        else { slot = "NA"; conn = "NA"; }
      }
      return {
        part_number: "M." + displayModel(model) + "." + ansiClass(inletPsi) + "." +
                     index + "." + slot + "." + conn,
        quote_note: null, warnings: warnings
      };
    }
    return { part_number: null, quote_note: QUOTE_NOTE, warnings: warnings };
  }

  // The display wording for the two part-number tokens that describe the
  // hardware. Both are derived from the token rather than hard-coded, so if a
  // part number ever carries a clockwise drive or the larger case, the printed
  // description follows it instead of quietly contradicting it.
  var EAGLE_ROTATION = { CCW: "Counterclockwise", CW: "Clockwise" };
  var EAGLE_CASE = { "8X6": '8"x6"', "12X10": '12"x10"' };

  // The Eagle quotes as its own line item, not part of the meter.
  function eagleSelection(eagleType, inletPsi) {
    var p1 = eagleP1(inletPsi);
    var rotary = eagleType === "Rotary Corrector";
    var body = rotary ? "I.MPP-MRC" : "I.MPP-MVC";
    // Position 8 of the part number: the input the corrector reads. It says
    // the same thing as the "Corrector" field above it, so it is not printed
    // a second time as its own line.
    var mount = rotary ? "INTEG" : "CVI";
    var rotationToken = "CCW";
    var caseToken = "8X6";
    var rotation = EAGLE_ROTATION[rotationToken] || rotationToken;
    var caseSize = EAGLE_CASE[caseToken] || caseToken;
    return {
      manufacturer: "Eagle",
      model: "MPplusII",
      type: eagleType,
      p1: p1,
      rotation: rotation,
      "case": caseSize,
      part_number: body + "." + p1 + ".N.N.N.TC." + mount + "." +
                   rotationToken + ".N.ALK." + caseToken,
      fields: [
        { label: "Manufacturer", value: "Eagle" },
        { label: "Model", value: "MPplusII" },
        { label: "Corrector", value: eagleType },
        { label: "Rotation", value: rotation },
        { label: "Pressure Transducer", value: "0-" + p1 + " psi" },
        { label: "Temperature Probe", value: "Included" },
        { label: "Battery", value: "Alkaline battery pack" },
        { label: "Cellular Communication", value: "None" },
        { label: "Case", value: caseSize }
      ],
      lines: [
        "Eagle", "MPplusII", eagleType, rotation,
        "0-" + p1 + " psi Pressure Transducer",
        "Temperature Probe", "Alkaline battery pack",
        "No Cellular Communication", caseSize + " case"
      ]
    };
  }

  // Which Eagle, if any, this meter type pulls onto the quote.
  function eagleTypeFor(slug, model, answers) {
    var fam = BY_MODEL[model].family;
    var comp = answers[slug + ".compensation"];
    if (fam === "turbo") {
      // Fix-factored and live turbo metering is always corrected by an Eagle
      // volume corrector; the instructions name no alternative.
      return (comp === "Fix-Factored" || comp === "Live") ? "Volume Corrector" : null;
    }
    if (fam === "roots") {
      if (rootsForcedEagle(model)) return FORCED_EAGLE_TYPE;
      var picked = comp === "Fix-Factored" ? answers[slug + ".index"]
                 : comp === "Live" ? answers[slug + ".live"]
                 : null;
      if (picked === EAGLE_INSTRUMENT) return answers[slug + ".eagle_type"] || null;
    }
    return null;
  }

  // ---- display helpers ----------------------------------------------
  function identityFields(entry) {
    var fields = [
      { label: "Manufacturer", value: entry.manufacturer },
      { label: "Model", value: entry.model }
    ];
    if (entry.size) fields.push({ label: "Size", value: entry.size });
    if (contains(PULSE_OUTPUT_INCLUDED, entry.meter)) {
      fields.push({ label: "Pulse Output", value: "Included" });
    }
    fields.push({ label: "Meter Capacity (CFH)", value: fmtCfh(entry.capacity_cfh) });
    if (entry.oversize_pct) {
      fields.push({
        label: "Required Capacity (CFH)",
        value: fmtCfh(entry.required_cfh) + " (" + entry.oversize_pct + "% oversize)"
      });
    }
    if (entry.min_capacity_cfh) {
      fields.push({ label: "Minimum Capacity (CFH)", value: fmtCfh(entry.min_capacity_cfh) });
    }
    return fields;
  }
  function identityLines(entry) {
    var lines = [entry.manufacturer, entry.model];
    if (entry.size) lines.push(entry.size);
    if (contains(PULSE_OUTPUT_INCLUDED, entry.meter)) {
      lines.push("Pulse output included");
    }
    return lines;
  }
  function answerFields(questions, answers) {
    var out = [];
    for (var i = 0; i < questions.length; i++) {
      var v = answers[questions[i].id];
      if (v) out.push({ label: questions[i].label, value: v });
    }
    return out;
  }
  // The sample outputs print the bare answer for a size and a sentence for a
  // flag, which is what this reproduces.
  function answerLines(questions, answers) {
    var out = [];
    for (var i = 0; i < questions.length; i++) {
      var id = questions[i].id, v = answers[id];
      if (!v) continue;
      if (/\.ferrule$/.test(id) || /\.connection$/.test(id)) out.push(v);
      else if (/\.pulse$/.test(id)) out.push(v === "Yes" ? "Pulse output required" : "No pulse output");
      else if (/\.index$/.test(id)) out.push(v + " index");
      else out.push(questions[i].label + ": " + v);
    }
    return out;
  }

  // Why a type on offer has no meter behind it. This is reachable in the
  // normal course of things: a tier is offered because one of its meters
  // works, and the straight-pipe 10C25 has less capacity than the meter-bar
  // models beside it, so it can fall short at a flow the tier itself clears.
  function unavailableMessage(mtype, tier, evals) {
    var rule = tier.resolve[mtype], models, i;
    if (rule[0] === "pick") {
      models = rule[1];
    } else {
      models = [];
      var meters = FAMILIES[rule[1]].meters;
      for (i = 0; i < meters.length; i++) models.push(meters[i].model);
    }
    var best = models[models.length - 1];
    return "No " + mtype + " meter will work for this application (" +
      displayModel(best) + ": " + evals[best].reason + ").";
  }

  // ---- helpers -------------------------------------------------------
  function num(v) {
    if (v === null || v === undefined || v === "") return null;
    var f = Number(v);
    return isFinite(f) ? f : null;
  }
  function asList(v) {
    if (v === null || v === undefined) return [];
    if (typeof v === "string") return [v];
    return [].slice.call(v);
  }
  function contains(arr, v) {
    for (var i = 0; i < arr.length; i++) if (arr[i] === v) return true;
    return false;
  }
  function extend(target, src) {
    for (var k in src) if (Object.prototype.hasOwnProperty.call(src, k)) target[k] = src[k];
    return target;
  }

  // ---- main entry point ----------------------------------------------
  var METER_TYPE_QUESTION_ID = "meter_type";

  function sizeMeters(payload) {
    payload = payload || {};
    var errors = [], warnings = [], i, j;

    var inlet = num(payload.inlet);
    var flow = num(payload.flow);
    var inletUnits = payload.inlet_units || "psi";
    var flowUnits = payload.flow_units || "CFH";

    if (inlet === null || inlet <= 0) errors.push("Enter an inlet pressure greater than zero.");
    if (flow === null || flow <= 0) errors.push("Enter a flow rate greater than zero.");
    if (inlet !== null && !(inlet >= 0 && inlet <= MAX_INLET)) {
      errors.push("Inlet pressure must be between 0 and 1,440.");
    }
    if (flow !== null && !(flow >= 0 && flow <= MAX_FLOW)) {
      errors.push("Flow rate must be between 0 and 100,000,000.");
    }

    var inletPsi = 0, flowCfh = 0;
    try {
      inletPsi = toPsi(inlet || 0, inletUnits);
      flowCfh = toCfh(flow || 0, flowUnits);
    } catch (e) {
      errors.push(e.message);
    }

    if (errors.length) {
      return { ok: false, errors: errors, warnings: warnings, stage: "error" };
    }

    var firstP = FAMILIES.small.meters[0].points[0].p;
    if (inletPsi < firstP) {
      warnings.push("Inlet pressure is below the " + fmt(firstP) +
        " psi start of the capacity tables; the " + fmt(firstP) +
        " psi capacities were used.");
    }

    var evals = {}, anyWorks = false;
    for (i = 0; i < MODEL_ORDER.length; i++) {
      evals[MODEL_ORDER[i]] = evaluate(MODEL_ORDER[i], inletPsi, flowCfh);
      if (evals[MODEL_ORDER[i]].works) anyWorks = true;
    }

    var tier = TIERS[TIERS.length - 1];
    for (i = 0; i < TIERS.length; i++) {
      if (!TIERS[i].trigger) continue;
      var hit = false;
      for (j = 0; j < TIERS[i].trigger.length; j++) {
        if (evals[TIERS[i].trigger[j]].works) { hit = true; break; }
      }
      if (hit) { tier = TIERS[i]; break; }
    }

    var evaluations = [];
    for (i = 0; i < MODEL_ORDER.length; i++) evaluations.push(evals[MODEL_ORDER[i]]);

    var base = {
      ok: true,
      errors: [],
      warnings: warnings,
      converted: {
        inlet_psi: inletPsi,
        flow_cfh: flowCfh
      },
      summary: [
        { label: "Inlet Pressure", value: fmt(inlet) + " " + inletUnits },
        { label: "Flow Rate", value: fmt(flow) + " " + flowUnits },
        { label: "Inlet Pressure (converted)", value: fmt(inletPsi) + " psi" },
        { label: "Flow Rate (converted)", value: fmt(flowCfh) + " CFH" }
      ],
      tier: tier.id,
      meter_type_question: {
        id: METER_TYPE_QUESTION_ID,
        label: "Meter type",
        type: "single_select",
        options: tier.types.slice()
      },
      evaluations: evaluations
    };

    // Nothing in any tab has the capacity: say so rather than offering a type
    // whose every candidate fails.
    if (!anyWorks) {
      return extend(extend({}, base), {
        stage: "complete",
        selected: false,
        message: "No meter in the capacity tables will handle " + fmtCfh(flowCfh) +
          " CFH at " + fmt(inletPsi) + " psi. Contact Holland Supply Company " +
          "to review the application.",
        questions: [],
        results: [],
        part_numbers: [],
        eagle: null
      });
    }

    // Ordered by the tier rather than by the caller, so the same selection
    // always renders and reads back in the same order whether it arrived from
    // a radio group or a chatbot reply.
    //
    // Exactly one type is sized. The question is a single choice, so a caller
    // sending several is either stale or confused; the first in tier order is
    // taken and the rest are called out rather than silently dropped.
    //
    // Matched without regard to case. "Rotary (roots)" was once spelt with a
    // lower-case r, and a chatbot built against that spelling must not have
    // its customer's choice dropped as "does not apply" over a capital letter.
    var requested = asList(payload.meter_types);
    var valid = [], chosenTypes = [], dropped = [];
    var reqKeys = [], tierKeys = [];
    for (i = 0; i < requested.length; i++) {
      reqKeys.push(String(requested[i]).trim().toLowerCase());
    }
    for (i = 0; i < tier.types.length; i++) {
      tierKeys.push(tier.types[i].toLowerCase());
      if (contains(reqKeys, tier.types[i].toLowerCase())) valid.push(tier.types[i]);
    }
    if (valid.length) chosenTypes = [valid[0]];
    if (valid.length > 1) {
      warnings.push("Only one meter type can be selected. " + chosenTypes[0] +
        " was sized; " + valid.slice(1).join(", ") +
        (valid.length === 2 ? " was" : " were") + " ignored.");
    }
    for (i = 0; i < requested.length; i++) {
      if (!contains(tierKeys, reqKeys[i])) dropped.push(requested[i]);
    }
    if (dropped.length) {
      warnings.push("These meter types do not apply at this pressure and flow " +
        "and were ignored: " + dropped.join(", ") + ".");
    }

    if (!chosenTypes.length) {
      return extend(extend({}, base), {
        stage: "meter_type",
        selected: false,
        message: "Select a meter type to continue.",
        questions: [base.meter_type_question],
        results: [],
        part_numbers: [],
        eagle: null
      });
    }

    var answers = extend({}, payload.answers || {});
    var pending = [], results = [], partNumbers = [], eagleRequests = [];

    for (i = 0; i < chosenTypes.length; i++) {
      var mtype = chosenTypes[i];
      var slug = SLUG[mtype];
      var model = resolveModel(tier.resolve[mtype], evals);

      if (model === null) {
        results.push({
          type: mtype, slug: slug, heading: mtype + " Meter",
          available: false,
          message: unavailableMessage(mtype, tier, evals),
          identity_fields: [], answer_fields: [], fields: [], lines: [],
          part_number: null, quote_note: null, warnings: []
        });
        continue;
      }

      var questions = optionQuestions(slug, model, answers);
      var unanswered = [], answerMap = {};
      for (j = 0; j < questions.length; j++) {
        answerMap[questions[j].id] = answers[questions[j].id] || null;
        if (!answers[questions[j].id]) unanswered.push(questions[j]);
      }
      for (j = 0; j < unanswered.length; j++) pending.push(unanswered[j]);

      var ev = evals[model];
      var entry = {
        type: mtype, slug: slug, heading: mtype + " Meter",
        available: true,
        meter: model,
        manufacturer: displayManufacturer(model),
        model: displayModel(model),
        size: ev.size,
        family: ev.family,
        capacity_cfh: ev.capacity_cfh,
        required_cfh: ev.required_cfh,
        min_capacity_cfh: ev.min_capacity_cfh,
        oversize_pct: Math.round(ev.oversize * 100),
        rating_psi: ev.rating_psi,
        questions: questions,
        answers: answerMap,
        complete: unanswered.length === 0,
        warnings: []
      };

      // `identity_fields` and `answer_fields` are kept apart as well as
      // concatenated. A caller that renders the questions as live widgets
      // (the web block does) shows the identity fields only, so an answer is
      // not printed once as a field and again as a ticked radio; a caller
      // that just wants the finished selection as text (the chatbot, the PDF,
      // the PDF) reads `fields`.
      // The assumed compensation goes in with the identity fields, not the
      // answer fields: a caller that renders questions as widgets shows only
      // the identity fields, and a forced meter has no widgets to carry the
      // assumption. Putting it here is what makes it visible at all.
      entry.identity_fields = identityFields(entry)
        .concat(rootsAssumedFields(model));
      entry.answer_fields = answerFields(questions, answers);
      entry.fields = entry.identity_fields.concat(entry.answer_fields);

      if (unanswered.length) {
        entry.lines = identityLines(entry);
        entry.part_number = null;
        entry.quote_note = null;
        results.push(entry);
        continue;
      }

      var built = buildPartNumber(model, slug, answers, inletPsi);
      entry.part_number = built.part_number;
      entry.quote_note = built.quote_note;
      entry.warnings = built.warnings;
      var assumed = rootsAssumedFields(model).map(function (f) {
        return f.label + ": " + f.value;
      });
      entry.lines = identityLines(entry)
        .concat(assumed)
        .concat(answerLines(questions, answers));
      if (built.part_number) partNumbers.push(built.part_number);
      results.push(entry);

      var etype = eagleTypeFor(slug, model, answers);
      if (etype) eagleRequests.push(etype);

      if (ev.family === "turbo") {
        var tcomp = answers[slug + ".compensation"];
        if (tcomp === "Fix-Factored" || tcomp === "Live") {
          entry.warnings.push("Include the Eagle MPplusII Volume Corrector on the quote.");
        }
      }
    }

    // The Eagle is a separate line item under the meter selection, not part
    // of any meter's own part number. Only one meter type is sized, so there
    // is at most one.
    var eagle = null;
    if (eagleRequests.length) {
      eagle = eagleSelection(eagleRequests[0], inletPsi);
      partNumbers.push(eagle.part_number);
    }

    var stage = pending.length ? "options" : "complete";
    var ready = [];
    for (i = 0; i < results.length; i++) {
      if (results[i].available && results[i].complete) ready.push(results[i]);
    }

    var message;
    if (stage === "options") {
      message = "Answer the remaining options to build the part number.";
    } else if (ready.length) {
      message = "Meter selected!";
    } else {
      message = "No meter is available for the selected type.";
    }

    for (i = 0; i < results.length; i++) {
      var rw = results[i].warnings || [];
      for (j = 0; j < rw.length; j++) if (!contains(warnings, rw[j])) warnings.push(rw[j]);
    }

    return extend(extend({}, base), {
      stage: stage,
      selected: ready.length > 0,
      message: message,
      questions: pending,
      results: results,
      part_numbers: partNumbers,
      eagle: eagle,
      warnings: warnings
    });
  }

  root.USGMeterSizing = {
    VERSION: VERSION,
    sizeMeters: sizeMeters,
    PRESSURE_UNITS: PRESSURE_UNITS,
    FLOW_UNITS: FLOW_UNITS,
    METER_TYPE_QUESTION_ID: METER_TYPE_QUESTION_ID,
    // Exposed for the parity test harness and for anything that needs to
    // reason about the tables without re-running a full sizing.
    _internals: {
      evaluate: evaluate,
      interpolate: interpolate,
      toPsi: toPsi,
      toCfh: toCfh,
      capacities: CAPACITIES
    }
  };

  if (typeof module !== "undefined" && module.exports) module.exports = root.USGMeterSizing;
})(typeof window !== "undefined" ? window : (typeof globalThis !== "undefined" ? globalThis : this));
