// Card "Names in shared files" on the Recall page (researcher-attribution D8): the attribution mode of the report,
// the share bundle and the replication package, the researcher list with Confirm and "Same researcher as", and the
// mode line beside "Write share bundle" and the package button. Names go in the request body, never the URL.
(function () {
  const q = (s) => document.querySelector(s);
  const h = esc;
  const MODES = [
    ["pseudonyms", "Pseudonyms"],
    ["names", "Real names"],
    ["none", "No names"],
  ];
  const EXPLAIN = "What readers of the report, the share bundle and the replication package see. Pseudonyms: each " +
    "researcher as Researcher 1, Researcher 2 ... in the order they first appear. Real names: names a researcher typed " +
    "or confirmed are written as recorded. No names: no name and no pseudonym; labels.csv numbers each sample's " +
    "readers 1, 2 instead. The list linking names and pseudonyms stays on this computer and is never written into a " +
    "shared file. Names WIDER inferred from this computer (its login name, a reviewer name set on this computer, an imported file) are never " +
    "written as names: they stay pseudonyms until a researcher confirms them. Files already written are not changed. " +
    "Sharing files written in different modes lets readers match pseudonyms to names.";
  const ROWS = "Your name is the one shown beside \"Working as\" in the top bar. Confirm makes a name WIDER inferred " +
    "a declared one; \"Same researcher as\" counts two names as one researcher. Both are changes you can undo on the " +
    "History page.";
  let shown = "";   // the state the last message belongs to: a list changed elsewhere (an undo) clears the message
  const key = (d) => JSON.stringify([d.mode, d.researchers]);
  const say = (t, bad) => { q("#rs-msg").innerHTML = bad ? `<span class="bad">${h(t)}</span>` : h(t); };

  function modeLine(line) {
    for (const s of ["#rp-mode", "#out-package-mode"]) q(s).textContent = line;
  }

  function draw(d) {
    q("#rs-modes").innerHTML = MODES.map(([v, label]) => `<label class="choice"><input type="radio" name="rs-mode" value="${v}" data-mode="${v}"${d.mode === v ? " checked" : ""}> ${h(label)}</label>`).join("");
    q("#rs-explain").textContent = EXPLAIN;
    q("#rs-verify").textContent = d.not_verified + " " + ROWS;
    const open = d.researchers.filter(r => !r.joined_into).map(r => r.name);
    const row = (r) => {
      const others = open.filter(n => n !== r.name);
      const join = r.joined_into ? `joined into ${h(r.joined_into)}`
        : others.length ? `<select data-join="${h(r.name)}" aria-label="Same researcher as, for ${h(r.name)}"><option value="">Same researcher as…</option>${others.map(n => `<option value="${h(n)}">${h(n)}</option>`).join("")}</select>` : "";
      const confirm = r.status === "inferred" && !r.joined_into ? `<button class="secondary small-btn" data-confirm="${h(r.name)}">Confirm</button>` : "";
      return `<tr><td>${h(r.name)}</td><td>${h(r.pseudonym)}</td><td>${h(r.status)}</td><td class="small">${h(r.source)}</td><td>${confirm}</td><td>${join}</td></tr>`;
    };
    q("#rs-table").innerHTML = d.researchers.length
      ? `<tr><th>name</th><th>pseudonym</th><th>status</th><th>source</th><th></th><th></th></tr>` + d.researchers.map(row).join("")
      : `<tr><td>No researcher recorded yet.</td></tr>`;
    modeLine(d.mode_line);
    q("#rs-modes").querySelectorAll("[data-mode]").forEach(b => { b.onchange = () => post("/api/researchers/attribution", { mode: b.dataset.mode }); });
    q("#rs-table").querySelectorAll("[data-confirm]").forEach(b => { b.onclick = () => post("/api/researchers/confirm", { name: b.dataset.confirm }); });
    q("#rs-table").querySelectorAll("[data-join]").forEach(s => {
      s.onchange = () => { if (s.value) post("/api/researchers/join", { name: s.dataset.join, into: s.value }); };
    });
  }

  async function post(url, body) {
    try {
      const d = await api(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      draw(d); say(d.message || ""); shown = key(d);
      if (window.afterAction) afterAction();
    } catch (e) { say(e.message, true); await load(true); }
  }

  async function load(keepMessage) {
    let d;
    try { d = await api("/api/researchers"); } catch (e) { say(e.message, true); return; }
    if (keepMessage) shown = key(d); else if (key(d) !== shown) say("");
    draw(d);
  }

  window.Researchers = { load, EXPLAIN, ROWS };
})();
