// Settings of a step shown on the step's own page (ui-redesign 4.10): every protocol field the step uses, in named
// sections with a plain name, its explanation and default, and Set/Reset through /api/protocol/set (the same change,
// check and History entry as `wider protocol set`). No "Advanced" fold: the settings are part of the page.
(function () {
  // page -> named sections, each with the protocol paths it shows and a plain name for each
  const PAGES = {
    dedup: {
      title: "Settings of this step",
      prefixes: ["dedup."],
      sections: [
        { name: "Fuzzy title thresholds", fields: [
          ["dedup.jw_candidate", "Title similarity to compare a pair at all"],
          ["dedup.jw_auto_merge", "Title similarity to merge with a full citation match"],
          ["dedup.jw_corroborated", "Title similarity to merge when the citation agrees"],
          ["dedup.jw_strong_title", "Strong title similarity (orders the queue)"]] },
        { name: "DOI conflicts", fields: [["dedup.doi_conflict_title", "Title similarity a shared DOI needs"]] },
        { name: "Short titles", fields: [["dedup.generic_title_max_words", "Most words of a short title"]] },
      ],
    },
    samples: {
      title: "Review protocol for the development sample",
      prefixes: ["sampling.development_reviewers"],
      sections: [
        { name: "Reviewers", fields: [["sampling.development_reviewers", "Reviewers for the development sample (1 or 2)"]] },
      ],
    },
    enrich: {
      title: "Settings of this step",
      prefixes: ["corpus.thin_words", "enrichment."],
      sections: [
        { name: "Thin abstracts", fields: [["corpus.thin_words", "Words below which an abstract is thin"]] },
        { name: "Sources and their order", fields: [["enrichment.sources", "Services tried, in this order"]] },
        { name: "Title lookup", fields: [["enrichment.title_search_min_jw", "Title similarity a title-search hit needs"]] },
      ],
    },
  };
  const idOf = (path) => `ps-${path.replace(/\./g, "-")}`;
  const shown = (v) => (Array.isArray(v) ? v.join(", ") : String(v));
  function input(r) {
    const id = idOf(r.path), label = `aria-label="${esc(r.path)}"`;
    if (Array.isArray(r.default)) return `<input id="${id}" ${label} data-type="list" value="${esc(r.value.join(", "))}">`;
    if (typeof r.default === "number") return `<input id="${id}" ${label} data-type="number" type="text" inputmode="decimal" value="${esc(r.value)}">`;
    return `<input id="${id}" ${label} data-type="string" value="${esc(r.value ?? "")}">`;
  }
  // a list is typed as words separated by commas; a number field keeps what was typed so the server refuses it in words
  function parse(el) {
    const v = String(el.value || "").trim();
    if (el.dataset.type === "list") return v.split(",").map(s => s.trim()).filter(Boolean);
    if (el.dataset.type === "number") return v !== "" && Number.isFinite(Number(v)) ? Number(v) : v;
    return v;
  }
  // the page's sections; a field of the step that no section names yet still appears, under "Other settings"
  function sectionsOf(spec, rows) {
    const mine = rows.filter(r => spec.prefixes.some(p => r.path === p || r.path.startsWith(p)));
    const named = new Set(spec.sections.flatMap(s => s.fields.map(f => f[0])));
    const rest = mine.filter(r => !named.has(r.path)).map(r => [r.path, r.key.replace(/_/g, " ")]);
    return spec.sections.concat(rest.length ? [{ name: "Other settings", fields: rest }] : [])
      .map(s => ({ name: s.name, fields: s.fields.map(([path, name]) => ({ name, row: mine.find(r => r.path === path) })).filter(f => f.row) }));
  }
  function render(spec, d) {
    const sections = sectionsOf(spec, d.rows);
    return `<h2>${esc(spec.title)}</h2>
      <p class="small">A change here is checked, stored in the project folder and listed in History, the same as on the command line. <span class="small" data-ps-msg role="status"></span></p>
      ${sections.map(s => `<section class="ps-section" aria-label="${esc(s.name)}"><h3>${esc(s.name)}</h3>
        ${s.fields.map(({ name, row: r }) => `<div class="ps-field${r.changed ? " changed" : ""}" data-ps-field="${esc(r.path)}">
          <label for="${idOf(r.path)}"><b>${esc(name)}</b></label>
          <p>${esc(r.description)}</p>
          <div class="row">${input(r)}
            <button class="secondary small-btn" data-ps-set="${esc(r.path)}" aria-label="Set ${esc(name)}">Set</button>
            ${r.changed ? `<button class="secondary small-btn" data-ps-reset="${esc(r.path)}" aria-label="Reset ${esc(name)} to the default">Reset</button>` : ""}
            <span class="small">default: <b>${esc(shown(r.default))}</b>${r.changed ? " · changed from the default" : ""}</span></div>
          ${d.effects && d.effects[r.path] ? `<p data-ps-effect="${esc(r.path)}">${esc(d.effects[r.path])}</p>` : ""}
        </div>`).join("")}</section>`).join("")}
      ${cmdFold(`<code>wider protocol set ${esc(sections[0] && sections[0].fields[0] ? sections[0].fields[0].row.path : "")} VALUE</code>; <code>wider protocol explain</code> lists every setting.`)}`;
  }
  async function draw(sel, page) {
    const box = document.querySelector(sel), spec = PAGES[page];
    if (!box || !spec) return;
    let d;
    try { d = await api("/api/protocol"); }
    catch (e) { box.innerHTML = `<p class="bad">${esc(e.message)}</p>`; return; }
    box.innerHTML = render(spec, d);
    const say = (t, bad) => { const m = box.querySelector("[data-ps-msg]"); if (m) { m.textContent = t; m.className = bad ? "small bad" : "small"; } };
    const focus = (path) => { const b = box.querySelector(`[data-ps-set="${path}"]`); if (b && b.focus) b.focus(); };
    const act = async (path, reset) => {
      try {
        const r = reset ? await api("/api/protocol/reset", { method: "POST", body: JSON.stringify({ path }) })
          : await api("/api/protocol/set", { method: "POST", body: JSON.stringify({ path, value: parse(document.querySelector(`#${idOf(path)}`)) }) });
        await draw(sel, page); focus(path);
        say(`${path} ${reset ? "reset to the default" : "set"}${r && r.notice ? `; ${r.notice}` : ""}.`);
        if (typeof refreshStages === "function") refreshStages();
      } catch (e) { await draw(sel, page); focus(path); say(e.message, true); }   // a refused value does not stay in the field
    };
    box.onclick = (e) => {
      const s = e.target.closest && e.target.closest("[data-ps-set]"), r = e.target.closest && e.target.closest("[data-ps-reset]");
      if (s) act(s.dataset.psSet, false); else if (r) act(r.dataset.psReset, true);
    };
    // Enter in a field equals its Set button
    box.onkeydown = (e) => {
      if (e.key !== "Enter" || e.shiftKey || e.ctrlKey || e.metaKey || e.altKey || !e.target.id || !e.target.id.startsWith("ps-")) return;
      const f = e.target.closest && e.target.closest("[data-ps-field]");
      if (f) { e.preventDefault(); act(f.dataset.psField, false); }
    };
  }
  window.SettingsCard = { draw, PAGES };
})();
