// Adjacent-literature loop: card on the construct page (name, propose, find, approve/edit/reject) and the
// per-literature cases section on the screen page. Wraps loadConstruct/loadScreen from app.js.
const adjEsc = esc;

async function loadAdjacent() {
  const msg = $("#adj-msg");
  try {
    const s = await api("/api/adjacent");
    const lits = s.literatures || [];
    msg.textContent = s.row ? `adjacent v${s.row.version} · ${s.approved.length} approved of ${lits.length}` : "no literatures yet";
    if (!lits.length) { $("#adj-list").innerHTML = "<p class='muted small'>Nothing listed yet. Add one above, seed from the codebook, or ask the model.</p>"; return; }
    $("#adj-list").innerHTML = `<table><tr><th>status</th><th>literature</th><th>differs / cue</th><th>origin</th><th></th></tr>` + lits.map(e => `
      <tr data-key="${adjEsc(e.key)}" class="${e.status === "rejected" ? "muted" : ""}">
        <td><b>${adjEsc(e.status)}</b></td>
        <td><b>${adjEsc(e.name)}</b> <span class="muted small">${adjEsc(e.key)}</span><div class="small">${adjEsc(e.description)}</div>
            ${(e.example_phrases || []).length ? `<div class="muted small">e.g. ${e.example_phrases.map(adjEsc).join(" · ")}</div>` : ""}
            ${e.evidence && e.evidence.quotes ? `<div class="muted small">cluster ${adjEsc(e.evidence.cluster)}: ${e.evidence.quotes.map(q => "“" + adjEsc(q.quote) + "”").join(" ")}</div>` : ""}</td>
        <td class="small">${adjEsc(e.differs_from_construct)}${e.discriminating_cue ? `<div><i>cue:</i> ${adjEsc(e.discriminating_cue)}</div>` : ""}</td>
        <td class="small">${adjEsc(e.origin)}</td>
        <td class="small">${e.status !== "approved" ? `<button class="small-btn secondary" data-act="approved">approve</button>` : ""}
            ${e.status !== "rejected" ? `<button class="small-btn secondary" data-act="rejected">reject</button>` : ""}
            ${e.status === "rejected" ? `<button class="small-btn secondary" data-act="proposed">restore</button>` : ""}
            <button class="small-btn secondary" data-act="edit">edit</button></td></tr>`).join("") + "</table>";
    document.querySelectorAll("#adj-list button[data-act]").forEach(b => b.onclick = async () => {
      const key = b.closest("tr").dataset.key, act = b.dataset.act;
      try {
        if (act === "edit") {
          const e = lits.find(x => x.key === key);
          const f = await askFields(`Edit ${e.name}`, [
            { key: "name", label: "Name", value: e.name },
            { key: "description", label: "Description (what these studies examine)", value: e.description || "" },
            { key: "cue", label: "Discriminating cue", value: e.discriminating_cue || "" },
            { key: "differs", label: "How it differs from the construct", value: e.differs_from_construct || "" }], "Save");
          if (!f) return;
          await api(`/api/adjacent/${encodeURIComponent(key)}/edit`, { method: "POST", body: JSON.stringify({ name: f.name, description: f.description, discriminating_cue: f.cue, differs_from_construct: f.differs }) });
        } else {
          await api(`/api/adjacent/${encodeURIComponent(key)}/status/${act}`, { method: "POST" });
        }
        await loadAdjacent();
      } catch (e) { msg.textContent = e.message; }
    });
  } catch (e) { msg.textContent = e.message; }
}

$("#adj-add").onclick = async () => {
  const name = $("#adj-name").value.trim();
  if (!name) { $("#adj-msg").textContent = "give the literature a name"; return; }
  try {
    await api("/api/adjacent/add", { method: "POST", body: JSON.stringify({ name, description: $("#adj-desc").value.trim(), discriminating_cue: $("#adj-cue").value.trim() }) });
    $("#adj-name").value = ""; $("#adj-cue").value = ""; $("#adj-desc").value = "";
    await loadAdjacent();
  } catch (e) { $("#adj-msg").textContent = e.message; }
};
const adjCall = (id, path, label) => { $(id).onclick = async () => {
  $("#adj-msg").textContent = `${label}…`; $(id).disabled = true;
  try { const r = await api(path, { method: "POST" }); $("#adj-msg").textContent = `${label}: ${(r.added || []).length} added`; await loadAdjacent(); }
  catch (e) { $("#adj-msg").textContent = e.message; }
  $(id).disabled = false;
}; };
adjCall("#adj-seed", "/api/adjacent/seed", "seeded from codebook");
adjCall("#adj-propose", "/api/adjacent/propose", "proposed from question");
adjCall("#adj-find", "/api/adjacent/find", "found in ranking");

async function loadAdjacentCases() {
  const run = $("#ev-run").value, sample = $("#ev-sample").value;
  if (!run) { $("#adj-cases-msg").textContent = "no run selected in the evaluation card"; return; }
  try {
    const d = await api(`/api/adjacent/cases/${encodeURIComponent(run)}${sample ? "?sample=" + encodeURIComponent(sample) : ""}`);
    const keys = Object.keys(d.by_adjacent || {});
    $("#adj-cases-msg").textContent = keys.length ? `${d.cases.length} records tied to ${keys.length} literature(s)` : "no record of this run is tied to an adjacent literature";
    $("#adj-cases-table").innerHTML = keys.length ? `<table><tr><th>literature</th><th>n</th><th>drawn</th><th>model-labelled</th><th>MISS</th><th>FALSE INCLUDE</th><th>FALSE MAYBE</th><th>agree</th></tr>` +
      keys.map(k => { const v = d.by_adjacent[k]; return `<tr><td>${adjEsc(k)}</td><td>${v.n || 0}</td><td>${v.n_drawn || 0}</td><td>${v.n_model || 0}</td><td class="${v.MISS ? "bad" : ""}">${v.MISS || 0}</td><td>${v["FALSE INCLUDE"] || 0}</td><td>${v["FALSE MAYBE"] || 0}</td><td>${v.agree || 0}</td></tr>`; }).join("") + "</table>" : "";
    $("#adj-cases").innerHTML = d.cases.length ? `<table><tr><th>category</th><th>drawn for</th><th>model said</th><th>human</th><th>model</th><th>title</th><th>quotes</th></tr>` +
      d.cases.map(r => `<tr><td class="${r.category === "MISS" ? "bad" : ""}">${adjEsc(r.category)}</td><td class="small">${adjEsc(r.drawn_literature || "")}</td><td class="small">${adjEsc(r.model_literature || "")}</td>
        <td>${adjEsc(r.human)} ${adjEsc(r.human_reason || "")}</td><td>${adjEsc(r.model)}</td><td>${adjEsc(r.title)}<div class="muted small">${adjEsc(r.summary || "")}</div></td>
        <td class="small">${Object.entries(r.quotes || {}).map(([k, q]) => `${adjEsc(k)}: “${adjEsc(q)}”`).join("<br>")}</td></tr>`).join("") + "</table>" : "";
  } catch (e) { $("#adj-cases-msg").textContent = e.message; }
}
$("#adj-cases-btn").onclick = loadAdjacentCases;
$("#adj-rerun-btn").onclick = async () => {  // re-run the development evaluation of the selected run, then refresh the cases
  $("#adj-cases-msg").textContent = "evaluating…";
  try { $("#ev-run-btn").click(); await loadAdjacentCases(); } catch (e) { $("#adj-cases-msg").textContent = e.message; }
};

const adjOrigLoadConstruct = loadConstruct, adjOrigLoadScreen = loadScreen;
loadConstruct = async function () { await adjOrigLoadConstruct(); await loadAdjacent(); };
loadScreen = async function () { await adjOrigLoadScreen(); };
