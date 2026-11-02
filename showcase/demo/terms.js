/* WIDER interactive demo: the terms panel on the Search strings page (openspec change interactive-demo, design D6).
 *
 * The app has no control to add or remove a term on this page: terms are changed on the Concepts page and saved as a
 * new version. The demo adds a panel beside the real (recorded) search string where the visitor can try a change and
 * see at once what it would find among the demo's invented records. Counting happens in the browser; the panel never
 * calls the app's routes and never touches the page above it.
 */
(function () {
  "use strict";
  const W = window, D = W.Demo, DATA = ((W.DEMO_SNAPSHOT || {}).terms) || null;
  if (!D || !DATA) return;
  const NOTE = `Counted on the demo's ${DATA.records.length} invented records only, one per paper after duplicates are removed. A real database search finds different numbers; WIDER never runs your database search for you.`;
  const EMPTY = "A block with no terms finds nothing: every block needs at least one term.";
  const original = DATA.blocks.map((b) => b.terms.slice());
  const includes = DATA.records.filter((r) => r.decision === "include");

  // a term as the panel reads it: case insensitive, from a word boundary, to a word boundary unless it ends in *;
  // * stands for any ending, a space for any whitespace; a phrase in quotes reads as the words inside them
  function pattern(term) {
    const t = String(term).trim().toLowerCase().replace(/^"(.+)"$/, "$1").trim();
    const words = t.split(/\s+/).map((w) => w.replace(/[.+?^${}()|[\]\\]/g, "\\$&").replace(/\*/g, "\\w*"));
    return new RegExp("\\b" + words.join("\\s+") + (t.endsWith("*") ? "" : "\\b"), "i");
  }
  // the records every block matches (a block matches when any of its terms appears; blocks combine with AND)
  function found(blocks) {
    if (!blocks.length || blocks.some((b) => !b.length)) return [];
    const rx = blocks.map((b) => b.map(pattern));
    return DATA.records.filter((r) => {
      const text = r.title + "\n" + r.abstract;
      return rx.every((b) => b.some((x) => x.test(text)));
    }).map((r) => r.id);
  }
  const byId = {};
  DATA.records.forEach((r) => { byId[r.id] = r; });
  const startFound = found(original);

  function terms() {
    const o = D.state.overlay = D.state.overlay || {};
    if (!Array.isArray(o.terms) || o.terms.length !== original.length) o.terms = original.map((b) => b.slice());
    return o.terms;
  }
  // what the current terms find, against the hand-written ones
  function result() {
    const blocks = terms(), now = found(blocks), set = new Set(now), before = new Set(startFound);
    const group = (ids) => ids.slice().sort((a, b) => (byId[a].decision === "include" ? 0 : 1) - (byId[b].decision === "include" ? 0 : 1) || (a < b ? -1 : 1));
    return {
      records: now.length, of: DATA.records.length,
      includes: now.filter((id) => byId[id].decision === "include").length, ofIncludes: includes.length,
      missed: group(startFound.filter((id) => !set.has(id))), added: group(now.filter((id) => !before.has(id))),
      empty: blocks.some((b) => !b.length),
    };
  }

  // ---- the panel ---------------------------------------------------------------------------------------------------
  const doc = W.document;
  const el = (tag, attrs, text) => {
    const e = doc.createElement(tag);
    Object.keys(attrs || {}).forEach((k) => e.setAttribute(k, attrs[k]));
    if (text != null) e.textContent = text;
    return e;
  };
  let panel = null;

  function paperLine(id) {
    const r = byId[id], li = el("li");
    li.appendChild(el("span", { class: "demo-terms-id" }, id));
    li.appendChild(doc.createTextNode(" " + r.title + " "));
    li.appendChild(el("span", { class: "muted small" }, r.decision === "include" ? "(include in the reference labels)" : "(exclude in the reference labels)"));
    return li;
  }
  function paperGroup(ids, head) {
    const box = el("div", { class: "demo-terms-group" });
    box.appendChild(el("p", { class: "small" }, head));
    const ul = el("ul", { class: "small" });
    ids.forEach((id) => ul.appendChild(paperLine(id)));
    box.appendChild(ul);
    return box;
  }
  function renderResult() {
    const r = result(), box = doc.getElementById("demo-terms-result");
    if (!box) return r;
    box.textContent = "";
    box.appendChild(el("p", { id: "demo-terms-count" },
      `Found ${r.records} of ${r.of} records, ${r.includes} of the ${r.ofIncludes} include papers.`));
    if (r.empty) box.appendChild(el("p", { id: "demo-terms-empty", class: "demo-terms-warn" }, EMPTY));
    const missedInc = r.missed.filter((id) => byId[id].decision === "include"), missedOther = r.missed.filter((id) => byId[id].decision !== "include");
    const addedInc = r.added.filter((id) => byId[id].decision === "include"), addedOther = r.added.filter((id) => byId[id].decision !== "include");
    if (!r.empty && missedInc.length) box.appendChild(paperGroup(missedInc, "Include papers the first terms found and these terms miss:"));
    if (!r.empty && missedOther.length) box.appendChild(paperGroup(missedOther, "Other papers no longer found:"));
    if (addedInc.length) box.appendChild(paperGroup(addedInc, "Include papers the first terms missed and these terms find:"));
    if (addedOther.length) box.appendChild(paperGroup(addedOther, "Other papers now found (more to screen, none of them included):"));
    if (!r.missed.length && !r.added.length && !r.empty) box.appendChild(el("p", { class: "muted small" }, "The same papers as the first terms."));
    return r;
  }
  function say(i, text) {
    const m = doc.getElementById(`demo-terms-msg-${i}`);
    if (m) m.textContent = text || "";
  }
  function renderBlock(i) {
    const ul = doc.getElementById(`demo-terms-chips-${i}`);
    if (!ul) return;
    ul.textContent = "";
    terms()[i].forEach((t) => {
      const li = el("li", { class: "demo-chip" });
      li.appendChild(el("span", {}, t));
      const b = el("button", { type: "button", class: "demo-chip-x", "aria-label": `Remove the term ${t}` }, "×");
      b.addEventListener("click", () => remove(i, t));
      li.appendChild(b);
      ul.appendChild(li);
    });
  }
  function change() {
    D.save();
    terms().forEach((_, i) => renderBlock(i));
    return renderResult();
  }
  function remove(i, term) {
    const b = terms()[i];
    if (!b || !b.includes(term)) return null;
    b.splice(b.indexOf(term), 1);
    say(i, `Removed ${term}.`);
    return change();
  }
  function add(i, term) {
    const b = terms()[i], t = String(term || "").trim().replace(/\s+/g, " ").toLowerCase();
    if (!b) return null;
    if (!t || !/[a-z0-9]/.test(t)) { say(i, "Type a word first."); return null; }
    if (t.length > 60) { say(i, "That is longer than a search term usually is (60 characters at most here)."); return null; }
    if (b.includes(t)) { say(i, `${t} is already in this block.`); return null; }
    b.push(t);
    say(i, `Added ${t}.`);
    return change();
  }
  function restore() {
    D.state.overlay.terms = original.map((b) => b.slice());
    terms().forEach((_, i) => say(i, ""));
    return change();
  }

  function build() {
    const anchor = doc.getElementById("q-strings"), host = anchor && (anchor.parentNode || anchor.parent);
    if (!host || doc.getElementById("demo-terms")) return;
    panel = el("section", { id: "demo-terms", class: "card demo-terms", "aria-labelledby": "demo-terms-title" });
    panel.appendChild(el("h2", { id: "demo-terms-title" }, "Demo: try other search terms"));
    panel.appendChild(el("p", { class: "muted small" },
      "In WIDER you change terms on the Concepts page and save them as a new version. Here you can try a change and see at once what it would find. A paper must use a term from each block; a * at the end finds every ending (suggestion* finds suggestion and suggestions)."));
    DATA.blocks.forEach((blk, i) => {
      const fs = el("fieldset", { class: "demo-terms-block" });
      fs.appendChild(el("legend", {}, blk.name));
      fs.appendChild(el("ul", { id: `demo-terms-chips-${i}`, class: "demo-chips", "aria-label": `Terms in ${blk.name}` }));
      const form = el("form", { class: "row" });
      const label = el("label", { class: "small", for: `demo-terms-add-${i}` }, "Add a term");
      const input = el("input", { id: `demo-terms-add-${i}`, type: "text", maxlength: "80", autocomplete: "off", spellcheck: "false" });
      form.appendChild(label);
      form.appendChild(input);
      form.appendChild(el("button", { type: "submit", class: "secondary" }, "Add"));
      form.addEventListener("submit", (e) => {
        e.preventDefault();
        if (add(i, input.value)) input.value = "";
      });
      fs.appendChild(form);
      fs.appendChild(el("p", { id: `demo-terms-msg-${i}`, class: "muted small", role: "status" }));
      panel.appendChild(fs);
    });
    panel.appendChild(el("div", { id: "demo-terms-result", "aria-live": "polite" }));
    panel.appendChild(el("p", { class: "muted small demo-terms-note" }, NOTE));
    const reset = el("button", { type: "button", class: "demo-quiet" }, "Back to the first terms");
    reset.addEventListener("click", restore);
    panel.appendChild(reset);
    host.insertBefore(panel, anchor);
    change();
  }

  build();
  W.DemoTerms = { found, pattern, result, add, remove, restore, note: NOTE, empty: EMPTY };
})();
