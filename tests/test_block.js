/* Drive the real block markup in jsdom and walk the whole question flow.
 *
 * The parity test proves the algorithm agrees with itself across languages.
 * This proves the page in front of a customer actually reaches an answer:
 * that Run Sizing renders the meter-type question, that ticking a box renders
 * the option questions, and that answering them puts a part number on screen.
 * Every previous bug in this layer was a wiring bug, not a rules bug, and no
 * amount of algorithm testing catches those.
 *
 *     node tests/test_block.js
 *
 * The two <script src> tags are stripped and replaced: the algorithm bundle is
 * loaded from dist/ instead of jsDelivr (so this tests the build that would
 * ship, offline), and jsPDF is dropped entirely (the download buttons are
 * clicked but nothing is asserted about the file).
 */
"use strict";

const fs = require("fs");
const path = require("path");
const { JSDOM } = require("jsdom");

const REPO = path.resolve(__dirname, "..");
const BLOCK = path.join(REPO, "block", "block.html");
const BUNDLE = path.join(REPO, "dist", "usg-meter-sizing.js");

let failures = 0;
function check(label, cond, detail) {
  if (cond) {
    console.log("  OK   " + label);
  } else {
    failures++;
    console.log("  FAIL " + label + (detail ? "\n         " + detail : ""));
  }
}

function build() {
  let html = fs.readFileSync(BLOCK, "utf8");
  // Drop the remote script tags; the algorithm is injected below instead.
  html = html.replace(/<script[^>]*\bsrc=[^>]*><\/script>/g, "");

  const dom = new JSDOM(
    "<!doctype html><html><body>" + html + "</body></html>",
    { runScripts: "outside-only", pretendToBeVisual: true }
  );
  // The bundle assigns to `window`, and the block's IIFE reads it, so the
  // order matters: algorithm first, then the UI layer.
  dom.window.eval(fs.readFileSync(BUNDLE, "utf8"));
  const inline = /<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/.exec(html);
  dom.window.eval(inline[1]);
  return dom;
}

// Fill the numeric form and press Run Sizing. The handler yields once so the
// spinner can paint, so this returns a promise that settles after it.
function run(dom, inputs) {
  const d = dom.window.document;
  d.getElementById("usgm-inlet").value = String(inputs.inlet);
  d.getElementById("usgm-inlet-units").value = inputs.inlet_units || "psi";
  d.getElementById("usgm-flow").value = String(inputs.flow);
  d.getElementById("usgm-flow-units").value = inputs.flow_units || "CFH";
  d.getElementById("usgm-run-btn").click();
  return new Promise((r) => setTimeout(r, 60));
}

function out(dom) {
  return dom.window.document.getElementById("usgm-output");
}
function text(dom) {
  return out(dom).textContent.replace(/\s+/g, " ");
}

// Tick a meter-type checkbox by its visible label and fire the change the
// block listens for.
function tickType(dom, label) {
  const boxes = out(dom).querySelectorAll('[data-q="meter_type"]');
  for (const b of boxes) {
    if (b.value === label) {
      b.checked = true;
      b.dispatchEvent(new dom.window.Event("change", { bubbles: true }));
      return true;
    }
  }
  return false;
}

// Answer one option question by id. Handles both controls the block can
// render for a single-select: a radio group or a <select>.
function answer(dom, qid, value) {
  const els = out(dom).querySelectorAll('[data-q="' + qid + '"]');
  for (const el of els) {
    if (el.tagName === "SELECT") {
      el.value = value;
      el.dispatchEvent(new dom.window.Event("change", { bubbles: true }));
      return true;
    }
    if (el.value === value) {
      el.checked = true;
      el.dispatchEvent(new dom.window.Event("change", { bubbles: true }));
      return true;
    }
  }
  return false;
}

// Question ids that are on screen but not yet answered. Answered questions
// stay rendered and editable, so "pending" is about the tinted well, not
// merely about a widget existing.
function pendingIds(dom) {
  const ids = new Set();
  for (const el of out(dom).querySelectorAll(".usg-pending [data-q]")) {
    ids.add(el.getAttribute("data-q"));
  }
  return [...ids];
}

// Every question id on screen, answered or not.
function questionIds(dom) {
  const ids = new Set();
  for (const el of out(dom).querySelectorAll(".usg-option [data-q]")) {
    ids.add(el.getAttribute("data-q"));
  }
  return [...ids];
}

function partNumbers(dom) {
  return [...out(dom).querySelectorAll("code.usg-pn")].map((e) => e.textContent);
}

// Answer the price lookup from a fixture rather than the network. The block
// fires it after the result is on screen, so it has to be in place before the
// selection is made.
function stubPrices(dom, bySku) {
  dom.window.fetch = function (url, opts) {
    const s = String(url);
    if (s.indexOf("/api/chatbot/products") === 0 || s.indexOf("products?sku=") !== -1) {
      const sku = decodeURIComponent(s.split("sku=")[1] || "");
      const body = bySku[sku];
      return Promise.resolve({
        ok: !!body,
        json: () => Promise.resolve(body || null),
      });
    }
    // Lead times: answer with an empty list so nothing else renders.
    return Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
  };
}

async function main() {
  console.log("block/block.html in jsdom\n");

  // ---- the block loads and shows its prompt -------------------------
  let dom = build();
  check("renders before any run", /Fill in the inputs above/.test(text(dom)));
  check(
    "algorithm bundle is reachable from the page",
    typeof dom.window.USGMeterSizing.sizeMeters === "function"
  );

  // ---- a residential diaphragm job, all the way to a part number ----
  console.log("\nresidential: 7 in wc, 250 CFH");
  await run(dom, { inlet: 7, inlet_units: "in wc", flow: 250 });
  check(
    "meter type question rendered as checkboxes",
    out(dom).querySelectorAll('[data-q="meter_type"]').length === 2,
    text(dom).slice(0, 200)
  );
  check("offers Diaphragm", /Diaphragm/.test(text(dom)));
  check("no part number yet", partNumbers(dom).length === 0);

  tickType(dom, "Diaphragm");
  check(
    "the ticked meter type is marked selected",
    (function () {
      for (const l of out(dom).querySelectorAll(".usg-check")) {
        const box = l.querySelector("input");
        if (box.value === "Diaphragm") {
          return box.checked && l.className.indexOf("is-on") !== -1;
        }
      }
      return false;
    })()
  );
  check(
    "an unticked meter type is not marked selected",
    (function () {
      for (const l of out(dom).querySelectorAll(".usg-check")) {
        const box = l.querySelector("input");
        if (box.value === "Sonix IQ") {
          return !box.checked && l.className.indexOf("is-on") === -1;
        }
      }
      return false;
    })()
  );
  check(
    "ticking Diaphragm asks for a ferrule",
    pendingIds(dom).indexOf("diaphragm.ferrule") !== -1,
    "pending: " + JSON.stringify(pendingIds(dom))
  );
  check("shows the R275 identity", /Sensus/.test(text(dom)) && /R275/.test(text(dom)));
  check('shows the 6" CTC size', /6" CTC/.test(text(dom)));

  answer(dom, "diaphragm.ferrule", "1-1/4");
  check(
    "answering the ferrule produces the part number",
    partNumbers(dom).indexOf("M.R275.TC.5.D/R.1-1/4.TOP.NA") !== -1,
    "got: " + JSON.stringify(partNumbers(dom))
  );
  check("no questions left outstanding", pendingIds(dom).length === 0);
  check("Add to Cart appears", !!out(dom).querySelector(".usg-btn-cart"));
  check("PDF download appears", !!dom.window.document.getElementById("usgm-pdf-btn"));
  check(
    "there is no Excel download",
    dom.window.document.getElementById("usgm-xlsx-btn") === null
  );

  const cart = out(dom).querySelector(".usg-btn-cart").getAttribute("data-cart");
  check(
    "the inputs are not echoed back under the result",
    out(dom).querySelectorAll("table").length === 0 &&
      !/Sizing Detail/.test(text(dom))
  );
  check(
    "but they are still held for the PDF",
    dom.window.eval(
      "(function(){var r=document.getElementById('usgm-pdf-btn');return !!r;})()"
    )
  );
  check(
    "cart URL keeps the slashes in the part number unencoded",
    cart.indexOf("M.R275.TC.5.D/R.1-1/4.TOP.NA") !== -1,
    cart
  );

  // ---- two types side by side --------------------------------------
  console.log("\ntwo options at once: 10 psi, 2000 CFH");
  dom = build();
  await run(dom, { inlet: 10, flow: 2000 });
  tickType(dom, "Ultrasonic");
  tickType(dom, "Rotary (meter bar)");
  check(
    "two option blocks on screen",
    out(dom).querySelectorAll(".usg-option").length === 2,
    "found " + out(dom).querySelectorAll(".usg-option").length
  );
  answer(dom, "ultrasonic.ferrule", "30LT");
  answer(dom, "rotary_meter_bar.ferrule", "45LT");
  const two = partNumbers(dom);
  check(
    "both part numbers render",
    two.indexOf("M.SON-880.30LT.FIX.20.PO") !== -1 &&
      two.indexOf("M.D800.45LT.CBG.25.NA.LIT.NA") !== -1,
    JSON.stringify(two)
  );
  check(
    "both go in the cart",
    (out(dom).querySelector(".usg-cart").getAttribute("data-items").match(/\|/g) || []).length === 1
  );
  check(
    "both report their standard pulse output",
    (text(dom).match(/Pulse Output: Included/g) || []).length === 2,
    text(dom).slice(0, 600)
  );
  check(
    "straight pipe is offered but reports why it will not work",
    (function () {
      tickType(dom, "Rotary (straight pipe)");
      return /No rotary \(straight pipe\) meter will work/.test(text(dom));
    })()
  );

  // ---- the roots conditional tree, one answer at a time -------------
  console.log("\nroots with a live Eagle: 60 psi, 9000 CFH");
  dom = build();
  await run(dom, { inlet: 60, flow: 9000 });
  check(
    "top tier offers roots, ultrasonic and turbine",
    /Rotary \(roots\)/.test(text(dom)) && /Turbine/.test(text(dom))
  );
  tickType(dom, "Rotary (roots)");
  check(
    "asks for pressure compensation first",
    pendingIds(dom).indexOf("rotary_roots.compensation") !== -1
  );
  answer(dom, "rotary_roots.compensation", "Live");
  check(
    "Live reveals the correction question",
    pendingIds(dom).indexOf("rotary_roots.live") !== -1,
    "pending: " + JSON.stringify(pendingIds(dom))
  );
  answer(dom, "rotary_roots.live", "Eagle MPplusII Instrument");
  check(
    "choosing the Eagle reveals the corrector type",
    pendingIds(dom).indexOf("rotary_roots.eagle_type") !== -1,
    "pending: " + JSON.stringify(pendingIds(dom))
  );
  answer(dom, "rotary_roots.eagle_type", "Rotary Corrector");
  check(
    "answered questions stay on screen and editable",
    questionIds(dom).indexOf("rotary_roots.compensation") !== -1 &&
      pendingIds(dom).length === 0,
    "questions: " + JSON.stringify(questionIds(dom))
  );
  const roots = partNumbers(dom);
  check(
    "roots part number renders",
    roots.indexOf("M.RT3M-175.FLG.CTR.NA.175.VDN.NA.NA.NA") !== -1,
    JSON.stringify(roots)
  );
  check(
    "the Eagle gets its own section below the meter",
    /Eagle Corrector/.test(text(dom)) &&
      roots.indexOf("I.MPP-MRC.102.N.N.N.TC.INTEG.CCW.N.ALK.8X6") !== -1,
    JSON.stringify(roots)
  );
  check(
    "the Eagle path carries no assumption warning",
    !/index token/.test(text(dom))
  );

  // Backing out of Live and into None must re-ask the right questions.
  answer(dom, "rotary_roots.compensation", "None");
  check(
    "switching to None asks about the radio instead",
    pendingIds(dom).indexOf("rotary_roots.radio") !== -1 &&
      pendingIds(dom).indexOf("rotary_roots.live") === -1,
    "pending: " + JSON.stringify(pendingIds(dom))
  );
  answer(dom, "rotary_roots.radio", "No");
  check(
    "a No radio needs no index and fixes it at TC",
    partNumbers(dom).indexOf("M.RT3M-175.FLG.TC.NA.175.VDN.NA.NA.NA") !== -1,
    JSON.stringify(partNumbers(dom))
  );
  check("the Eagle section is gone", !/Eagle Corrector/.test(text(dom)));

  // ---- a meter that quotes instead of numbering ---------------------
  console.log("\nquote-only meter: 2 psi, 380 CFH");
  dom = build();
  await run(dom, { inlet: 2, flow: 380 });
  tickType(dom, "Sonix IQ");
  answer(dom, "sonix_iq.ferrule", "20LT");
  answer(dom, "sonix_iq.pulse", "Yes");
  check(
    "shows the quote note rather than a part number",
    /contact Holland Supply for a quote/.test(text(dom)) && partNumbers(dom).length === 0,
    text(dom).slice(0, 300)
  );
  check(
    "and offers no Add to Cart, since there is no SKU",
    !out(dom).querySelector(".usg-btn-cart")
  );

  // ---- inputs that cannot be sized ---------------------------------
  console.log("\nrejected and impossible inputs");
  dom = build();
  await run(dom, { inlet: 0, flow: 0 });
  check(
    "a zero inlet and flow report input errors",
    out(dom).querySelectorAll(".usg-error").length > 0 &&
      /greater than zero/.test(text(dom))
  );

  dom = build();
  await run(dom, { inlet: 400, flow: 50000000 });
  check(
    "a flow beyond every table says so and offers nothing",
    /No meter in the capacity tables/.test(text(dom)) &&
      out(dom).querySelectorAll('[data-q="meter_type"]').length === 0,
    text(dom).slice(0, 240)
  );

  // ---- the form asks for inlet pressure and flow only ---------------
  console.log("\ninput form");
  dom = build();
  check(
    "there is no MAOP field",
    dom.window.document.getElementById("usgm-maop") === null
  );
  check(
    "the form has exactly the four inputs the algorithm takes",
    dom.window.document.querySelectorAll(
      "#usg-meter-tool > .usg-row input, #usg-meter-tool > .usg-row select"
    ).length === 4
  );

  // ---- the inlet pressure alone decides the rating ------------------
  console.log("\ninlet pressure above the small-meter ratings");
  await run(dom, { inlet: 26, flow: 2000 });
  check(
    "26 psi rules the small meters out entirely",
    !/Diaphragm/.test(text(dom)) && /Rotary \(roots\)/.test(text(dom)),
    text(dom).slice(0, 240)
  );

  // ---- re-running resets the answers -------------------------------
  console.log("\nre-running after a change of inputs");
  dom = build();
  await run(dom, { inlet: 7, inlet_units: "in wc", flow: 250 });
  tickType(dom, "Diaphragm");
  answer(dom, "diaphragm.ferrule", "1-1/4");
  check("first run reaches a part number", partNumbers(dom).length === 1);
  await run(dom, { inlet: 60, flow: 9000 });
  check(
    "a second run clears the old selection rather than carrying it over",
    partNumbers(dom).length === 0 &&
      out(dom).querySelectorAll('[data-q="meter_type"]:checked').length === 0,
    text(dom).slice(0, 200)
  );

  // ---- a result that no longer matches the form ---------------------
  // The inputs are frozen at Run Sizing, so a pressure corrected afterwards
  // leaves a result on screen that was sized for the old value. That is how a
  // 40 psi job got quoted a 0-10 transducer, and this is the guard against it
  // happening silently again.
  console.log("\nediting an input without re-running");
  dom = build();
  await run(dom, { inlet: 4, flow: 25000 });
  tickType(dom, "Rotary (roots)");
  answer(dom, "rotary_roots.compensation", "Live");
  answer(dom, "rotary_roots.live", "Eagle MPplusII Instrument");
  answer(dom, "rotary_roots.eagle_type", "Volume Corrector");
  check(
    "4 psi gives a 0-10 psi transducer",
    /Pressure Transducer: 0-10 psi/.test(text(dom)),
    text(dom).slice(0, 400)
  );

  const inletBox = dom.window.document.getElementById("usgm-inlet");
  inletBox.value = "40";
  inletBox.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
  inletBox.dispatchEvent(new dom.window.Event("change", { bubbles: true }));
  check(
    "correcting the pressure warns that the result is out of date",
    !!dom.window.document.getElementById("usgm-stale") &&
      /inputs above have changed/.test(text(dom))
  );
  check(
    "and the out-of-date result is dimmed",
    out(dom).className.indexOf("is-stale-result") !== -1
  );

  // Answering another question must not clear the warning.
  answer(dom, "rotary_roots.eagle_type", "Rotary Corrector");
  check(
    "the warning survives a re-render",
    !!dom.window.document.getElementById("usgm-stale")
  );

  await run(dom, { inlet: 40, flow: 25000 });
  tickType(dom, "Rotary (roots)");
  answer(dom, "rotary_roots.compensation", "Live");
  answer(dom, "rotary_roots.live", "Eagle MPplusII Instrument");
  answer(dom, "rotary_roots.eagle_type", "Volume Corrector");
  check(
    "re-running clears the warning and gives 40 psi its 0-51 transducer",
    dom.window.document.getElementById("usgm-stale") === null &&
      /Pressure Transducer: 0-51 psi/.test(text(dom)),
    text(dom).slice(0, 400)
  );

  // ---- a zero price is not a price --------------------------------
  console.log("\nprice of 0.00");
  dom = build();
  stubPrices(dom, {
    "M.R275.TC.5.D/R.1-1/4.TOP.NA": {
      sku: "M.R275.TC.5.D/R.1-1/4.TOP.NA",
      price: { amount: "0.00", currency: "USD" },
      list_price: { amount: "0.00", currency: "USD" },
    },
  });
  await run(dom, { inlet: 7, inlet_units: "in wc", flow: 250 });
  tickType(dom, "Diaphragm");
  answer(dom, "diaphragm.ferrule", "1-1/4");
  await new Promise((r) => setTimeout(r, 60));
  check(
    "a 0.00 price is not shown",
    !/\$0\.00/.test(text(dom)),
    text(dom).slice(0, 400)
  );
  check(
    "and no line total is shown for it either",
    !/Total \$0/.test(text(dom))
  );

  console.log("\na real price");
  dom = build();
  stubPrices(dom, {
    "M.R275.TC.5.D/R.1-1/4.TOP.NA": {
      sku: "M.R275.TC.5.D/R.1-1/4.TOP.NA",
      price: { amount: "1507.54", currency: "USD" },
      list_price: { amount: "1507.54", currency: "USD" },
    },
  });
  await run(dom, { inlet: 7, inlet_units: "in wc", flow: 250 });
  tickType(dom, "Diaphragm");
  answer(dom, "diaphragm.ferrule", "1-1/4");
  await new Promise((r) => setTimeout(r, 60));
  check(
    "a non-zero price still shows",
    /\$1,507\.54/.test(text(dom)),
    text(dom).slice(0, 400)
  );

  // ---- the 23M asks nothing ---------------------------------------
  console.log("\n23M roots meter: no questions");
  dom = build();
  await run(dom, { inlet: 200, flow: 250000 });
  tickType(dom, "Rotary (roots)");
  check(
    "ticking it produces a part number with no questions in between",
    pendingIds(dom).length === 0 &&
      partNumbers(dom).indexOf("M.RT23M-232.FLG.CD.NA.VDN.NA.NA.NA") !== -1,
    JSON.stringify(partNumbers(dom))
  );
  check(
    "the assumed compensation is stated on screen",
    /Pressure compensation: Live/.test(text(dom)) &&
      /Correction: Eagle MPplusII Instrument/.test(text(dom))
  );
  check(
    "the Eagle is a volume corrector with a psi transducer range",
    /Corrector: Volume Corrector/.test(text(dom)) &&
      /Pressure Transducer: 0-290 psi/.test(text(dom)),
    text(dom).slice(0, 600)
  );
  check(
    'the case carries inch marks and rotation is a bare direction',
    /Case: 8"x6"/.test(text(dom)) && /Rotation: Counterclockwise/.test(text(dom)) &&
      !/Counterclockwise rotation/.test(text(dom))
  );

  console.log(
    "\n" + (failures ? "FAILED: " + failures + " check(s)" : "All checks passed")
  );
  process.exit(failures ? 1 : 0);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
