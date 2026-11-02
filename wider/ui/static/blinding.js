// Card "Review packages" on Samples and labelling (reviewer-roles-and-blinding D2-D6, D10): make a package for a
// second reviewer (the dialog says once what the file holds and that it leaves the computer when sent), download it
// again, import the reviewer's decisions file, and withdraw a package with a reason. Twin of `wider blinding
// package|package-file|import|status|withdraw`. The two texts on one or two reviewers and on the limits are said once,
// in normal text.
(function () {
  const q = (s) => document.querySelector(s);
  const h = esc;
  const MODES = [["second", "Second reviewer: the first reviewer's includes and maybes and as many excludes (validation sample)"],
    ["independent", "Independent: every record of the samples"]];
  let state = { packages: [], texts: [] };

  const say = (t, bad) => { q("#bp-msg").innerHTML = bad ? `<span class="bad">${h(t)}</span>` : h(t); };

  function draw(st) {
    state = st;
    q("#bp-reviewers").textContent = st.texts[0] || "";
    q("#bp-limits").textContent = st.texts[1] || "";
    const rows = st.packages.map((r) => `<tr><td>${h(r.package_id)}</td><td>${h(r.reviewer)}</td><td>${h(r.mode)}</td>` +
      `<td>${h(r.samples.join(", "))}</td><td>${h(r.n_items)}</td><td>${h(r.codebook_version)}</td><td>${h(r.state)}` +
      `${r.lines.map((x) => `<div>${h(x)}</div>`).join("")}</td>` +
      `<td><a class="btn-quiet small-btn" href="/api/blinding/package/${encodeURIComponent(r.package_id)}/file" download="${h(r.download_name)}">Download again</a>` +
      `${r.withdrawn_at ? "" : ` <button type="button" class="secondary small-btn" data-withdraw="${h(r.package_id)}">Withdraw</button>`}</td></tr>`);
    q("#bp-list").innerHTML = rows.length
      ? `<table class="rec-table"><thead><tr><th>package</th><th>reviewer</th><th>mode</th><th>samples</th><th>items</th>` +
        `<th>criteria version</th><th>state</th><th></th></tr></thead><tbody>${rows.join("")}</tbody></table>`
      : `<p>No review package yet.</p>`;
    q("#bp-list").querySelectorAll("[data-withdraw]").forEach((b) => { b.onclick = () => withdraw(b.dataset.withdraw); });
  }

  async function load(samples) {
    if (samples) {
      q("#bp-samples").innerHTML = Object.keys(samples).map((n) =>
        `<label class="inline"><input type="checkbox" data-bp-sample="${h(n)}"> ${h(n)}</label>`).join("") || "no samples yet";
    }
    try { draw(await api("/api/blinding/status")); } catch (e) { say(e.message, true); }
  }

  function body() {
    return { reviewer: q("#bp-reviewer").value.trim(), mode: q("#bp-mode").value,
             samples: [...document.querySelectorAll("[data-bp-sample]")].filter((c) => c.checked).map((c) => c.dataset.bpSample) };
  }

  const title = (pre) => `Review package for ${pre.reviewer}: ${pre.n_items} records`;

  function dialogHtml(pre) {
    const versions = pre.versions || [];
    const leaving = pre.leaving || (versions.length ? pre.leaving_by_version[String(versions[0])] : "");
    const earlier = pre.earlier ? `<p>${h(pre.earlier.page_message)}</p><p>${h(pre.earlier.meanings)}</p>` +
      `<label class="dlg-field">Earlier labels <select data-field="earlier" id="bp-earlier"><option value="">choose</option>` +
      `<option value="retain">Retain</option><option value="re-review">Re-review</option><option value="exclude">Exclude</option></select></label>` : "";
    const ver = versions.length ? `<label class="dlg-field">Codebook version <select data-field="codebook_version" id="bp-version">` +
      versions.map((v) => `<option value="${h(v)}">${h(v)}</option>`).join("") + `</select></label>` : "";
    const notes = (pre.notes || []).filter((n) => !n.includes("--codebook-version")).map((n) => `<p>${h(n)}</p>`).join("");
    return `<h3 id="choice-title">${h(title(pre))}</h3>${earlier}${ver}${notes}` +
      `<p id="bp-leaving">${h(leaving)}</p>` +
      `<div class="row"><button type="button" data-act="ok">Download</button><button type="button" class="secondary" data-act="cancel">Cancel</button></div>`;
  }

  async function make() {
    const b = body();
    if (!b.reviewer) { say("Give the reviewer's name.", true); return; }
    if (!b.samples.length) { say("Choose at least one sample.", true); return; }
    let pre;
    try { pre = await api("/api/blinding/package/preview", { method: "POST", body: JSON.stringify(b) }); } catch (e) { say(e.message, true); return; }
    const opened = askChoice(dialogHtml(pre));
    // the count and the sentence follow the choices (a re-review adds the earlier labels' records); Download waits for
    // an earlier-labels choice when one is needed
    const ok = q('dialog.choice-dialog [data-act="ok"]'), ver = q("#bp-version"), ear = q("#bp-earlier");
    const need = () => { if (ok) ok.disabled = !!ear && !ear.value; };
    const again = async () => {
      need();
      try {
        const p2 = await api("/api/blinding/package/preview", { method: "POST", body: JSON.stringify({ ...b,
          earlier: ear && ear.value ? ear.value : null, codebook_version: ver && ver.value ? +ver.value : null }) });
        const t = q("dialog.choice-dialog h3"); if (t) t.textContent = title(p2);
        if (q("#bp-leaving")) q("#bp-leaving").textContent = p2.leaving || "";
      } catch (e) { if (q("#bp-leaving")) q("#bp-leaving").textContent = e.message; }
    };
    for (const s of [ver, ear]) if (s) s.addEventListener("change", again);
    need();
    const r = await opened;
    if (!r) return;
    const send = { ...b, earlier: r.fields.earlier || null,
                   codebook_version: r.fields.codebook_version ? +r.fields.codebook_version : null };
    let made;
    try { made = await api("/api/blinding/package", { method: "POST", body: JSON.stringify(send) }); } catch (e) { say(e.message, true); return; }
    download(made.package_id, made.download_name);
    say(`Review package ${made.package_id} for ${made.reviewer}: ${made.n_items} records. Send the file to the reviewer.`);
    await load();
  }

  function download(id, name) {
    const a = document.createElement("a");
    a.href = `/api/blinding/package/${encodeURIComponent(id)}/file`;
    a.setAttribute("download", name);
    document.body.appendChild(a);
    a.click();
    a.remove();
  }

  async function importFile(input) {
    const f = input.files && input.files[0];
    if (!f) return;
    const fd = new FormData();
    fd.append("file", f);
    try {
      const r = await api("/api/blinding/import", { method: "POST", body: fd });
      say((r.lines || []).join(" "));
      if (r.change_id && window.offerUndo) offerUndo(r.change_id, "Decisions imported.");
      if (window.afterAction) afterAction();
    } catch (e) { say(e.message, true); }
    input.value = "";
    await load();
  }

  async function withdraw(id) {
    const f = await askFields(`Withdraw review package ${id}`, [{ key: "reason", label: "Reason (recorded in History)" }], "Withdraw");
    if (!f) return;
    try {
      const r = await api(`/api/blinding/package/${encodeURIComponent(id)}/withdraw`, { method: "POST", body: JSON.stringify({ reason: f.reason }) });
      say(`Review package ${id} withdrawn: ${r.withdraw_reason}`);
      if (r.change_id && window.offerUndo) offerUndo(r.change_id, "Package withdrawn.");
    } catch (e) { say(e.message, true); }
    await load();
    focusAfter(document.querySelector(`[data-withdraw="${CSS.escape(id)}"]`), "#bp-make");   // the row's button is redrawn
  }

  function init() {
    if (!q("#bp-card")) return;
    q("#bp-mode").innerHTML = MODES.map(([v, l]) => `<option value="${v}">${h(l)}</option>`).join("");
    q("#bp-make").onclick = make;
    q("#bp-import").onchange = () => importFile(q("#bp-import"));
  }
  init();
  window.Blinding = { load, make, withdraw, importFile, MODES };
})();
