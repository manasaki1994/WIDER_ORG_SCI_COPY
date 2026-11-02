// The review page of a review package (reviewer-roles-and-blinding D3): one file, opened from disk, that asks
// nothing of the network. It shows the records and the criteria the file holds, keeps the reviewer's work in this
// browser when storage works, and exports the decisions as one JSON file for the researcher to import. Every text is
// set with textContent; no HTML from the data is ever parsed.
(function () {
  "use strict";
  const D = JSON.parse(document.getElementById("data").textContent);
  const KEY = "wider-package:" + D.package_id;
  const DECISIONS = [["include", "Include", "1"], ["exclude", "Exclude", "2"], ["maybe", "Maybe", "3"]];
  const TICKS = ["yes", "no", "unclear"];
  const $ = (id) => document.getElementById(id);
  const make = (tag, text, attrs) => {
    const e = document.createElement(tag);
    if (text != null) e.textContent = String(text);
    for (const [k, v] of Object.entries(attrs || {})) e.setAttribute(k, v);
    return e;
  };
  const slug = (s) => String(s || "").replace(/[^A-Za-z0-9_-]+/g, "-").replace(/^-+|-+$/g, "") || "reviewer";

  // ---- storage: written and read back once; a page that cannot keep the work says so once
  let canStore = false;
  try {
    localStorage.setItem(KEY + ":test", "1");
    canStore = localStorage.getItem(KEY + ":test") === "1";
    localStorage.removeItem(KEY + ":test");
  } catch (e) { canStore = false; }
  let state = { entries: {}, export_seq: 0 };
  if (canStore) {
    try {
      const s = JSON.parse(localStorage.getItem(KEY) || "null");
      if (s && typeof s === "object" && s.entries) state = { entries: s.entries, export_seq: +s.export_seq || 0 };
    } catch (e) { /* unreadable: start empty */ }
  }
  const save = () => {
    if (!canStore) return;
    try { localStorage.setItem(KEY, JSON.stringify(state)); } catch (e) { /* storage full: export still works */ }
  };

  let cur = 0;
  const N = D.items.length;
  const entry = (it) => state.entries[it.item] || (state.entries[it.item] = { decision: "", reason: "", ticks: {}, tags: [], note: "", decided_at: "" });
  const decidedCount = () => D.items.filter((it) => (state.entries[it.item] || {}).decision).length;
  const say = (t) => { $("rp-msg").textContent = t || ""; };

  // ---- the head: once each
  document.title = "Review package for " + D.reviewer;
  $("rp-title").textContent = "Review package for " + D.reviewer;
  $("rp-project").textContent = (D.project_title ? D.project_title + ". " : "") + "Criteria version " + D.codebook_version +
    (D.not_frozen ? ". " + D.not_frozen : ".");
  $("rp-notice").textContent = "This page holds no other person's decisions and no model output." +
    (canStore ? " Your work stays in this browser until you export it." : "");
  if (!canStore) {
    $("rp-storage").textContent = "This browser does not keep your work in this page. Export your decisions before you close it.";
    $("rp-storage").hidden = false;
  }
  $("rp-limits").textContent = D.limits_text;

  // ---- the criteria panel
  function criteria() {
    const box = $("rp-criteria");
    box.textContent = "";
    box.appendChild(make("h2", "Criteria (version " + D.codebook_version + ")"));
    if (D.questions.length) {
      box.appendChild(make("h3", "Research questions"));
      const ul = make("ul");
      D.questions.forEach((q) => ul.appendChild(make("li", q)));
      box.appendChild(ul);
    }
    if (D.target) { box.appendChild(make("h3", "What is looked for")); box.appendChild(make("p", D.target)); }
    if (D.constructs.length) {
      box.appendChild(make("h3", "Definitions"));
      const dl = make("dl");
      D.constructs.forEach((c) => { dl.appendChild(make("dt", c.name)); dl.appendChild(make("dd", c.definition)); });
      box.appendChild(dl);
    }
    box.appendChild(make("h3", "Elements"));
    D.elements.forEach((e) => {
      box.appendChild(make("h4", e.name));
      box.appendChild(make("p", e.rule));
      if (e.counts.length) box.appendChild(make("p", "Counts: " + e.counts.join("; ")));
      if (e.does_not_count.length) box.appendChild(make("p", "Does not count: " + e.does_not_count.join("; ")));
    });
    if (D.exclusion_reasons.length) {
      box.appendChild(make("h3", "Exclusion reasons"));
      const ul = make("ul");
      D.exclusion_reasons.forEach((r) => ul.appendChild(make("li", r.code + ": " + r.text)));
      box.appendChild(ul);
    }
  }
  $("rp-criteria-toggle").addEventListener("click", () => {
    const box = $("rp-criteria"), open = box.hidden;
    box.hidden = !open;
    $("rp-criteria-toggle").setAttribute("aria-expanded", String(open));
    box.parentNode.classList.toggle("with-criteria", open);
  });

  // ---- the list and the open item
  function list() {
    const ol = $("rp-items");
    ol.textContent = "";
    D.items.forEach((it, i) => {
      const li = make("li");
      const b = make("button", (i + 1) + ". " + (it.title || "(no title)"), { type: "button", "data-i": String(i) });
      if ((state.entries[it.item] || {}).decision) b.classList.add("rp-done");
      if (i === cur) b.setAttribute("aria-current", "true");
      b.addEventListener("click", () => open(i, true));
      li.appendChild(b);
      ol.appendChild(li);
    });
    $("rp-progress").textContent = decidedCount() + " of " + N + " decided";
  }

  function touch(it) {
    const e = entry(it);
    e.decided_at = new Date().toISOString();
    save();
  }

  function decide(d) {
    const it = D.items[cur];
    if (!it) return;
    entry(it).decision = d;
    touch(it);
    item();
    list();
  }

  function field(labelText, input) {
    const l = make("label", labelText);
    l.appendChild(input);
    return l;
  }

  function item() {
    const box = $("rp-item");
    box.textContent = "";
    const it = D.items[cur];
    if (!it) { box.appendChild(make("p", "This package holds no record.")); return; }
    const e = entry(it);
    box.appendChild(make("h2", it.title || "(no title)", { id: "rp-item-title" }));
    const meta = [it.authors, it.year, it.journal].filter((x) => x !== null && x !== undefined && x !== "").join(" · ");
    const mp = make("p", meta, { class: "rp-meta" });
    if (it.doi) {
      if (meta) mp.appendChild(document.createTextNode(" · "));
      const a = make("a", "DOI " + it.doi, { href: "https://doi.org/" + encodeURI(it.doi), rel: "noreferrer noopener", target: "_blank" });
      mp.appendChild(a);
    }
    box.appendChild(mp);
    box.appendChild(make("p", it.abstract || "(no abstract)", { class: "rp-abstract" }));
    const mark = make("button", "abstract: " + it.abstract_source, { type: "button", class: "rp-source", "aria-expanded": "false" });
    const prov = make("p", "Source: " + it.abstract_source + ", " + it.abstract_words + " words" +
      (it.abstract_date ? ", retrieved " + it.abstract_date : ""), { class: "rp-provenance" });
    prov.hidden = true;
    mark.addEventListener("click", () => { prov.hidden = !prov.hidden; mark.setAttribute("aria-expanded", String(!prov.hidden)); });
    box.appendChild(mark);
    box.appendChild(prov);

    const bar = make("div", null, { class: "rp-decide", role: "group", "aria-label": "Decision" });
    DECISIONS.forEach(([d, word, key]) => {
      const b = make("button", word + " (" + key + ")", { type: "button", "data-decision": d, "aria-pressed": String(e.decision === d) });
      b.addEventListener("click", () => decide(d));
      bar.appendChild(b);
    });
    box.appendChild(bar);

    // the exclusion reason: the criteria's list, or the reviewer's own
    const sel = make("select", null, { id: "rp-reason" });
    sel.appendChild(make("option", "(no reason)", { value: "" }));
    D.exclusion_reasons.forEach((r) => sel.appendChild(make("option", r.code + ": " + r.text, { value: r.code })));
    sel.appendChild(make("option", "other (write it below)", { value: "other" }));
    const known = D.exclusion_reasons.some((r) => r.code === e.reason);
    sel.value = !e.reason ? "" : known ? e.reason : "other";
    const other = make("input", null, { type: "text", id: "rp-reason-other" });
    other.value = e.reason && !known ? e.reason : "";
    const otherField = field("Your own reason", other);
    otherField.hidden = sel.value !== "other";
    sel.addEventListener("change", () => {
      otherField.hidden = sel.value !== "other";
      e.reason = sel.value === "other" ? other.value : sel.value;
      touch(it);
    });
    other.addEventListener("input", () => { e.reason = other.value; touch(it); });
    box.appendChild(field("Exclusion reason", sel));
    box.appendChild(otherField);

    D.elements.forEach((el) => {
      const fs = make("fieldset");
      fs.appendChild(make("legend", el.name));
      TICKS.forEach((t) => {
        const r = make("input", null, { type: "radio", name: "tick-" + el.id, value: t });
        r.checked = e.ticks[el.id] === t;
        r.addEventListener("change", () => { e.ticks[el.id] = t; touch(it); });
        const l = make("label");
        l.appendChild(r);
        l.appendChild(document.createTextNode(" " + t));
        fs.appendChild(l);
      });
      box.appendChild(fs);
    });

    const tags = make("input", null, { type: "text", id: "rp-tags" });
    tags.value = (e.tags || []).join(", ");
    tags.addEventListener("input", () => { e.tags = tags.value.split(",").map((s) => s.trim()).filter(Boolean); touch(it); });
    box.appendChild(field("Your tags (comma separated)", tags));
    const note = make("textarea", null, { id: "rp-note" });
    note.value = e.note || "";
    note.addEventListener("input", () => { e.note = note.value; touch(it); });
    box.appendChild(field("Note", note));

    const nav = make("div", null, { class: "rp-nav" });
    const prev = make("button", "Previous (k)", { type: "button", id: "rp-prev" });
    const next = make("button", "Next (j)", { type: "button", id: "rp-next" });
    prev.disabled = cur === 0;
    next.disabled = cur >= N - 1;
    prev.addEventListener("click", () => open(cur - 1, false));
    next.addEventListener("click", () => open(cur + 1, false));
    nav.appendChild(prev);
    nav.appendChild(next);
    box.appendChild(nav);
  }

  function open(i, focusTitle) {
    if (i < 0 || i >= N) return;
    cur = i;
    item();
    list();
    if (focusTitle) { const h = $("rp-item-title"); if (h) { h.setAttribute("tabindex", "-1"); h.focus(); } }
  }

  // keys 1, 2, 3 decide and j, k move; never while typing in a field or choosing in a select
  document.addEventListener("keydown", (ev) => {
    const t = ev.target || {};
    const tag = (t.tagName || "").toUpperCase();
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || t.isContentEditable) return;
    if (ev.ctrlKey || ev.metaKey || ev.altKey) return;
    const d = DECISIONS.find((x) => x[2] === ev.key);
    if (d) { ev.preventDefault(); decide(d[0]); return; }
    if (ev.key === "j") { ev.preventDefault(); open(cur + 1, false); }
    if (ev.key === "k") { ev.preventDefault(); open(cur - 1, false); }
  });

  // ---- export and continue
  function decisionsFile() {
    state.export_seq = (+state.export_seq || 0) + 1;
    save();
    const items = D.items.filter((it) => (state.entries[it.item] || {}).decision).map((it) => {
      const e = state.entries[it.item];
      return { item: it.item, decision: e.decision, reason: e.reason || "", ticks: e.ticks || {}, tags: e.tags || [],
               note: e.note || "", decided_at: e.decided_at || "" };
    });
    return { package_id: D.package_id, project_id: D.project_id, reviewer: D.reviewer, codebook_version: D.codebook_version,
             export_seq: state.export_seq, exported_at: new Date().toISOString(), items: items };
  }
  $("rp-export").addEventListener("click", () => {
    const out = decisionsFile();
    const blob = new Blob([JSON.stringify(out, null, 2)], { type: "application/json" });
    const a = make("a", null, { download: "wider-decisions-" + slug(D.reviewer) + "-" + D.package_id + ".json" });
    a.href = URL.createObjectURL(blob);
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    say("Exported " + out.items.length + " decisions (export " + out.export_seq + "). Send the file to the researcher.");
  });
  $("rp-continue").addEventListener("change", (ev) => {
    const f = ev.target.files && ev.target.files[0];
    if (!f) return;
    const r = new FileReader();
    r.onload = () => {
      let d;
      try { d = JSON.parse(String(r.result)); } catch (e) { say("This file is not a decisions file."); return; }
      if (!d || d.package_id !== D.package_id) { say("This decisions file belongs to another package."); return; }
      const tokens = new Set(D.items.map((it) => it.item));
      let n = 0;
      (d.items || []).forEach((x) => {
        if (!x || !tokens.has(x.item)) return;
        state.entries[x.item] = { decision: x.decision || "", reason: x.reason || "", ticks: x.ticks || {},
                                  tags: Array.isArray(x.tags) ? x.tags : [], note: x.note || "", decided_at: x.decided_at || "" };
        n++;
      });
      state.export_seq = Math.max(+state.export_seq || 0, +d.export_seq || 0);
      save();
      item();
      list();
      say("Continued from the file: " + n + " decisions restored.");
    };
    r.readAsText(f);
  });

  criteria();
  open(0, false);
})();
