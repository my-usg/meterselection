/* Audit every surface a customer can see for the string "USG".
 *
 * The rename is only worth anything if it covers what is actually on screen,
 * so this drives the block through a full sizing and reads back:
 *
 *   - all rendered text, before and after a run
 *   - every visible attribute (button labels, placeholders, titles, alt text)
 *   - the generated PDF's text and its download filename
 *   - the print-fallback document
 *
 * It deliberately does NOT check class names, element IDs, the script URL or
 * the window global. Those appear only in the page source, never to a
 * customer reading the page, and holding them steady is what lets the block
 * and the bundle be deployed in either order.
 *
 *     node tests/test_no_usg.js
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
  // The <script src> tags go, and with them the one legitimate "usg" in the
  // markup - the bundle URL, which is not customer-visible text.
  html = html.replace(/<script[^>]*\bsrc=[^>]*><\/script>/g, "");
  // Strip HTML comments: they are source, not screen.
  html = html.replace(/<!--[\s\S]*?-->/g, "");

  const vc = new VirtualConsole();
  const dom = new JSDOM(
    "<!doctype html><html><body>" + html + "</body></html>",
    { runScripts: "outside-only", pretendToBeVisual: true, virtualConsole: vc }
  );
  dom.window.eval(fs.readFileSync(BUNDLE, "utf8"));
  dom.window.TextEncoder = TextEncoder;
  dom.window.TextDecoder = TextDecoder;
  dom.window.HTMLCanvasElement.prototype.getContext = function () { return null; };
  dom.window.eval(fs.readFileSync(JSPDF, "utf8"));
  const inline = /<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/.exec(html);
  dom.window.eval(inline[1]);
  return dom;
}

// Everything on screen: text nodes plus the attributes that render as words.
const VISIBLE_ATTRS = ["title", "placeholder", "alt", "aria-label", "value", "data-tip"];
function visibleText(dom) {
  const root = dom.window.document.getElementById("hsc-meter-tool");
  const parts = [root.textContent];
  for (const el of root.querySelectorAll("*")) {
    for (const a of VISIBLE_ATTRS) {
      const v = el.getAttribute && el.getAttribute(a);
      if (v) parts.push(v);
    }
  }
  return parts.join("\n");
}

function el(dom, id) { return dom.window.document.getElementById(id); }
function out(dom) { return el(dom, "hscm-output"); }
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

// Capture the PDF and its filename without writing anything.
function renderPdf(dom) {
  const Orig = dom.window.jspdf.jsPDF;
  let captured = null, filename = null;
  function Patched() {
    const inst = new Orig(...arguments);
    inst.save = function (name) { captured = inst; filename = name; return inst; };
    return inst;
  }
  dom.window.jspdf.jsPDF = Patched;
  dom.window.alert = function () {};
  try {
    el(dom, "hscm-pdf-btn").click();
  } finally {
    dom.window.jspdf.jsPDF = Orig;
  }
  return { doc: captured, filename: filename };
}

function pdfText(doc) {
  const raw = doc.output();
  const parts = [];
  const re = /\(((?:\\.|[^()\\])*)\)\s*(?:Tj|TJ)/g;
  let m;
  while ((m = re.exec(raw)) !== null) {
    parts.push(m[1].replace(/\\([()\\])/g, "$1"));
  }
  return parts.join("\n");
}

function report(where, text) {
  const hits = [];
  const re = /.{0,40}USG.{0,40}/gi;
  let m;
  while ((m = re.exec(text)) !== null) hits.push(m[0].replace(/\s+/g, " ").trim());
  check(
    'no "USG" in ' + where,
    hits.length === 0,
    hits.length ? hits.join("\n         ") : ""
  );
}

async function main() {
  console.log("customer-visible surfaces\n");

  // ---- the form, before anything is run ----
  let dom = build();
  report("the empty form", visibleText(dom));
  check(
    "the heading reads Meter Selection Tool",
    /Meter Selection Tool/.test(visibleText(dom)),
    dom.window.document.querySelector("h1").textContent
  );

  // ---- a full selection, meter plus Eagle ----
  el(dom, "hscm-inlet").value = "60";
  el(dom, "hscm-flow").value = "9000";
  el(dom, "hscm-run-btn").click();
  await new Promise((r) => setTimeout(r, 80));
  report("the meter type question", visibleText(dom));

  tickType(dom, "Rotary (roots)");
  answer(dom, "rotary_roots.compensation", "Live");
  answer(dom, "rotary_roots.live", "Eagle MPplusII Instrument");
  answer(dom, "rotary_roots.eagle_type", "Volume Corrector");
  report("a finished selection with an Eagle", visibleText(dom));

  // ---- the PDF ----
  const pdf = renderPdf(dom);
  check("a PDF is produced", !!pdf.doc);
  report("the PDF text", pdfText(pdf.doc));
  report("the PDF filename", pdf.filename || "");
  check(
    "the PDF is titled HSC Meter Selection Tool",
    /HSC Meter Selection Tool/.test(pdfText(pdf.doc))
  );

  // ---- the print fallback, used when jsPDF cannot load ----
  const dom2 = build();
  el(dom2, "hscm-inlet").value = "60";
  el(dom2, "hscm-flow").value = "9000";
  el(dom2, "hscm-run-btn").click();
  await new Promise((r) => setTimeout(r, 80));
  tickType(dom2, "Rotary (roots)");
  answer(dom2, "rotary_roots.compensation", "None");
  answer(dom2, "rotary_roots.radio", "No");
  let printed = "";
  dom2.window.jspdf = undefined;          // force the fallback path
  dom2.window.open = function () {
    return {
      document: { write: function (h) { printed += h; }, close: function () {} },
      focus: function () {}, print: function () {},
    };
  };
  el(dom2, "hscm-pdf-btn").click();
  check("the print fallback produced a document", printed.length > 0);
  report("the print fallback", printed.replace(/<[^>]+>/g, " "));

  // ---- the error and no-meter paths, which a customer can reach ----
  const dom3 = build();
  el(dom3, "hscm-inlet").value = "0";
  el(dom3, "hscm-flow").value = "0";
  el(dom3, "hscm-run-btn").click();
  await new Promise((r) => setTimeout(r, 60));
  report("the input error message", visibleText(dom3));

  const dom4 = build();
  el(dom4, "hscm-inlet").value = "400";
  el(dom4, "hscm-flow").value = "50000000";
  el(dom4, "hscm-run-btn").click();
  await new Promise((r) => setTimeout(r, 60));
  report("the no-meter-available message", visibleText(dom4));

  // ---- the quote-only wording, which names the company ----
  const dom5 = build();
  el(dom5, "hscm-inlet").value = "2";
  el(dom5, "hscm-flow").value = "380";
  el(dom5, "hscm-run-btn").click();
  await new Promise((r) => setTimeout(r, 60));
  tickType(dom5, "Sonix IQ");
  answer(dom5, "sonix_iq.ferrule", "20LT");
  answer(dom5, "sonix_iq.pulse", "Yes");
  report("the contact-for-quote wording", visibleText(dom5));

  console.log(
    "\n" + (failures ? "FAILED: " + failures + " check(s)" : "All checks passed")
  );
  process.exit(failures ? 1 : 0);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
