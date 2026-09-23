/* Build the PDF through the block's own code and read the text back out.
 *
 * The PDF is a customer-facing document assembled by hand, section by
 * section, and nothing else checks it. The failure it guards against is a
 * silent one: a part number that stops being printed because the section that
 * carried it moved or went away. So this drives the real page, intercepts the
 * save, and asserts on the words that come back out of the file.
 *
 *     node tests/test_pdf.js
 *
 * jspdf is loaded from node_modules instead of the CDN; the block's own
 * <script src> tags are stripped the same way test_block.js strips them.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const { JSDOM, VirtualConsole } = require("jsdom");

const REPO = path.resolve(__dirname, "..");
const BLOCK = path.join(REPO, "block", "block.html");
const BUNDLE = path.join(REPO, "dist", "usg-meter-sizing.js");
const JSPDF = path.join(REPO, "node_modules", "jspdf", "dist", "jspdf.umd.min.js");

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
  html = html.replace(/<script[^>]*\bsrc=[^>]*><\/script>/g, "");

  // jsPDF 2.5.1 probes for a canvas on load, which it only needs for image
  // and HTML rendering; this block writes text and lines only. jsdom answers
  // that probe with a "not implemented" error on the virtual console, which
  // would otherwise print a ten-line stack trace on every run and bury a real
  // failure. Forwarding only console.log keeps the output readable.
  const vc = new VirtualConsole();
  vc.on("log", (...a) => console.log(...a));

  const dom = new JSDOM(
    "<!doctype html><html><body>" + html + "</body></html>",
    { runScripts: "outside-only", pretendToBeVisual: true, virtualConsole: vc }
  );
  dom.window.eval(fs.readFileSync(BUNDLE, "utf8"));
  // jsPDF reaches for TextEncoder/TextDecoder, which jsdom does not provide.
  // The browser has them natively, so handing Node's over is the faithful
  // stand-in rather than a workaround for something the real page lacks.
  dom.window.TextEncoder = TextEncoder;
  dom.window.TextDecoder = TextDecoder;
  dom.window.HTMLCanvasElement.prototype.getContext = function () { return null; };
  dom.window.eval(fs.readFileSync(JSPDF, "utf8"));
  const inline = /<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/.exec(html);
  dom.window.eval(inline[1]);
  return dom;
}

function el(dom, id) {
  return dom.window.document.getElementById(id);
}
function out(dom) {
  return el(dom, "hscm-output");
}
function fire(dom, node) {
  node.dispatchEvent(new dom.window.Event("change", { bubbles: true }));
}
function tickType(dom, label) {
  for (const b of out(dom).querySelectorAll('[data-q="meter_type"]')) {
    if (b.value === label) { b.checked = true; fire(dom, b); return true; }
  }
  return false;
}
function answer(dom, qid, value) {
  for (const e of out(dom).querySelectorAll('[data-q="' + qid + '"]')) {
    if (e.tagName === "SELECT") { e.value = value; fire(dom, e); return true; }
    if (e.value === value) { e.checked = true; fire(dom, e); return true; }
  }
  return false;
}

// Capture the document instead of letting it be written. `save` is installed
// per instance rather than on the prototype, so patching the prototype does
// nothing: the real save runs, fails for want of a browser download, and the
// block quietly falls back to the print path. Wrapping the constructor is what
// actually gets a handle on the document.
function renderPdf(dom) {
  const Orig = dom.window.jspdf.jsPDF;
  let captured = null;
  function Patched() {
    const inst = new Orig(...arguments);
    inst.save = function () { captured = inst; return inst; };
    return inst;
  }
  dom.window.jspdf.jsPDF = Patched;
  // A pop-up blocked in jsdom would otherwise reach window.alert, which jsdom
  // does not implement and which would bury the real failure in a stack trace.
  const alerted = [];
  dom.window.alert = function (m) { alerted.push(m); };
  try {
    el(dom, "hscm-pdf-btn").click();
  } finally {
    dom.window.jspdf.jsPDF = Orig;
  }
  if (!captured && alerted.length) {
    console.log("         (fell back to the print path: " + alerted[0] + ")");
  }
  return captured;
}

function pdfText(doc) {
  // Concatenate the raw output and recover the drawn strings. Escapes matter:
  // jsPDF writes "1-1/4" and "6\" CTC", and the quote arrives backslashed.
  const raw = doc.output();
  const parts = [];
  const re = /\(((?:\\.|[^()\\])*)\)\s*(?:Tj|TJ)/g;
  let m;
  while ((m = re.exec(raw)) !== null) {
    parts.push(m[1].replace(/\\([()\\])/g, "$1"));
  }
  return parts.join("\n");
}

async function main() {
  console.log("PDF summary\n");

  // A roots meter with a live Eagle: two part numbers, in two sections.
  const dom = build();
  el(dom, "hscm-inlet").value = "60";
  el(dom, "hscm-flow").value = "12000";
  el(dom, "hscm-run-btn").click();
  await new Promise((r) => setTimeout(r, 80));

  tickType(dom, "Rotary (Roots)");
  answer(dom, "rotary_roots.compensation", "Live");
  answer(dom, "rotary_roots.live", "Eagle MPplusII Instrument");
  answer(dom, "rotary_roots.eagle_type", "Rotary Corrector");

  check("the PDF button is present", !!el(dom, "hscm-pdf-btn"));
  const doc = renderPdf(dom);
  check("a PDF is produced", !!doc);
  if (!doc) { process.exit(1); }

  const text = pdfText(doc);

  check(
    "the Inputs section is still there",
    /Inputs/.test(text) && /Inlet Pressure/.test(text),
    text.slice(0, 300)
  );
  check(
    "the meter option section is headed by its type",
    /Rotary \(Roots\) Meter/.test(text),
    text.slice(0, 400)
  );
  check(
    "the meter part number is printed inside that section",
    text.indexOf("M.RT3M-175.FLG.CTR.NA.175.VDN.NA.NA.NA") !== -1,
    text
  );
  check(
    "the Eagle section carries its own part number",
    /Eagle Corrector/.test(text) &&
      text.indexOf("I.MPP-MRC.102.N.N.N.TC.INTEG.CCW.N.ALK.8X6") !== -1,
    text
  );

  // The two removed sections.
  // A Sonix 600 in the PDF, to confirm the standard pulse output reaches the
  // saved document and not just the screen.
  const dom3 = build();
  el(dom3, "hscm-inlet").value = "10";
  el(dom3, "hscm-flow").value = "1800";
  el(dom3, "hscm-run-btn").click();
  await new Promise((r) => setTimeout(r, 80));
  tickType(dom3, "Ultrasonic");
  answer(dom3, "ultrasonic.ferrule", "30LT");
  const text3 = pdfText(renderPdf(dom3));
  check(
    "the standard pulse output is printed in the PDF",
    /Pulse Output/.test(text3) && /Included/.test(text3),
    text3
  );

  check(
    "there is no consolidated Part Number(s) section",
    !/Part Number\(s\)/.test(text),
    text
  );
  check(
    "there is no Warnings section",
    !/Warnings/.test(text),
    text
  );
  check(
    "each part number appears exactly once",
    text.split("M.RT3M-175.FLG.CTR.NA.175.VDN.NA.NA.NA").length - 1 === 1 &&
      text.split("I.MPP-MRC.102.N.N.N.TC.INTEG.CCW.N.ALK.8X6").length - 1 === 1,
    text
  );

  // A quote-only meter has no SKU; the section must still say so rather than
  // leaving the customer with a blank where a part number belongs.
  const dom2 = build();
  el(dom2, "hscm-inlet").value = "2";
  el(dom2, "hscm-flow").value = "380";
  el(dom2, "hscm-run-btn").click();
  await new Promise((r) => setTimeout(r, 80));
  tickType(dom2, "Sonix IQ");
  answer(dom2, "sonix_iq.ferrule", "20LT");
  answer(dom2, "sonix_iq.pulse", "Yes");
  const text2 = pdfText(renderPdf(dom2));
  check(
    "a quote-only meter prints the quote note in place of a part number",
    /Sonix IQ Meter/.test(text2) &&
      /Contact Holland Supply for a quote/.test(text2),
    text2
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
