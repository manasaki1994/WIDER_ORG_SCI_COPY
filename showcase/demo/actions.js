/* WIDER interactive demo: the simulated actions (openspec change interactive-demo, design D4, D5).
 *
 * Loaded after demo.js and before the app's scripts. Each handler answers one of the app's writes from the demo's
 * overlay (window.Demo.state.overlay) and each patch changes a recorded answer so the app's pages show the visitor's
 * own labels and duplicate decisions. Nothing here calls a model or leaves the page. The wording copies the app's
 * own (wider/dedup/engine.py flow_message, wider/core/stage_summaries.py summarize_dedup, wider/core/journal.py).
 */
(function () {
  "use strict";
  const W = window, D = W.Demo;
  if (!D) return;
  const SIM = " (simulated)";
  const REVIEWER = "Reviewer A";
  const timing = { rank: 3000, draw: 1000 };   // how long a simulated run takes, in ms (the tests set 0)
  const ONLY_DEMO = "Only changes made in the demo can be undone here.";
  const GONE = "This change was made before the demo moved on to the next stage, so it can no longer be undone here.";
  const LABEL_DECISIONS = ["include", "exclude", "maybe"];
  const PAIR_DECISIONS = { duplicate: "manual_same", not_duplicate: "manual_different", unsure: "manual_uncertain", uncertain: "manual_uncertain" };
  const PAIR_WORDS = { manual_same: "Duplicate", manual_different: "Not duplicate", manual_uncertain: "Unsure" };
  // the summary key a confirmed pair adds to, by the reason it was queued (engine.py MANUAL_METHODS)
  const MANUAL_METHODS = { exact_doi: "exact_doi_conflict_manual_confirmed", exact_normalized_title: "exact_title_manual_confirmed",
                           fuzzy_title: "fuzzy_title_manual_confirmed" };

  const ok = (json) => ({ status: 200, json });
  const refuse = (status, detail) => ({ status, json: { detail } });
  const plural = (n, one, many) => `${n} ${n === 1 ? one : (many || one + "s")}`;
  const wait = (ms) => new Promise((done) => setTimeout(done, ms));
  const now = () => new Date().toISOString().slice(0, 19) + "+00:00";
  const at = (name) => D.state.checkpoint === name;

  function overlay() {
    const s = D.state;
    if (!s.overlay || typeof s.overlay !== "object") s.overlay = {};
    const o = s.overlay;
    o.labels = o.labels || {};      // "sample_id:record_id" -> {decision, reason, labels, note, updated_at}
    o.pairs = o.pairs || {};        // "raw_a-raw_b" -> {decision: manual_*, by, at}
    o.changes = o.changes || [];    // the demo's journal, oldest first
    o.results = o.results || {};    // stage -> {base, result}: the simulated runs' results, as the app shows them
    return o;
  }
  const newId = () => `demo-${(D.state.seq = (D.state.seq || 0) + 1)}`;
  const slot = (kind) => (kind === "label" ? overlay().labels : overlay().pairs);
  function put(kind, key, value) { if (value == null) delete slot(kind)[key]; else slot(kind)[key] = value; }
  function record(kind, key, after, summary, author) {
    const before = slot(kind)[key] || null, id = newId();
    put(kind, key, after);
    overlay().changes.push({ id, kind, key, before, after, summary, author, checkpoint: D.state.checkpoint });
    return id;
  }

  // ---- labels (D4) ------------------------------------------------------------------------------------------------
  // the page's queue is blind (an opaque item per row, no sample); the demo's own bookkeeping reads the queue recorded
  // with blind=false, which names each row's sample (record ids are unique across the demo's samples)
  const QUEUE = `/api/labels/queue?reviewer=${encodeURIComponent(REVIEWER)}`;
  const FULL = `${QUEUE}&blind=false`;
  const recordedQueue = (checkpoint) => D.recorded(FULL, checkpoint) || [];
  // a blind row's or a posted item's record, through the blind queue of any checkpoint
  function itemRecord(item) {
    for (const cp of [D.state.checkpoint, ...D.order()]) {
      const r = (D.recorded(QUEUE, cp) || []).find((x) => x.item === item);
      if (r) return r.record_id;
    }
    return null;
  }
  const sampleOf = (rid) => { const r = recordedQueue().find((x) => x.record_id === rid); return r ? r.sample_id : null; };
  const labelKey = (sid, rid) => `${sid}:${rid}`;
  const counts = (rows) => {
    const c = {};
    rows.forEach((r) => { if (r.decision) c[r.decision] = (c[r.decision] || 0) + 1; });
    return c;
  };
  const sameCounts = (a, b) => LABEL_DECISIONS.every((d) => (a[d] || 0) === (b[d] || 0));
  // sample id -> name: the queue gives ids, the Samples page names; matched by Reviewer A's recorded counts
  // (checkpoint: as recorded there instead of here)
  function sampleNames(checkpoint) {
    const rows = recordedQueue(checkpoint), samples = (D.recorded("/api/samples", checkpoint) || {}).samples || {};
    const out = {};
    const ids = [...new Set(rows.map((r) => r.sample_id))];
    const left = new Set(Object.keys(samples));
    for (const id of ids) {
      const mine = rows.filter((r) => r.sample_id === id), c = counts(mine);
      const match = [...left].find((n) => {
        const p = samples[n].progress || {}, rc = (p.reviewers || {})[REVIEWER] || {};
        return samples[n].n === mine.length && sameCounts(rc, c);
      });
      if (match) { out[id] = match; left.delete(match); }
    }
    return out;
  }
  function withLabels(rows) {
    const o = overlay().labels;
    return rows.map((r) => {
      const v = o[labelKey(r.sample_id !== undefined ? r.sample_id : sampleOf(r.record_id), r.record_id)];
      return v ? Object.assign({}, r, v) : r;
    });
  }

  // ui-redesign D4: the open records of a purposive (development) sample, not labelled here or by Reviewer A yet
  function devOpen() {
    const names = sampleNames(), samples = (D.recorded("/api/samples") || {}).samples || {}, o = overlay().labels;
    return recordedQueue().filter((r) => (samples[names[r.sample_id]] || {}).kind === "purposive" && !r.decision &&
      !o[labelKey(r.sample_id, r.record_id)]).length;
  }

  // the tour's Quick tour (change demo-chapters-and-video, D3): the open development records get Reviewer A's label,
  // the one recorded at the next checkpoint, through the same POST the labelling page sends; returns how many
  async function fillDev() {
    const names = sampleNames(), samples = (D.recorded("/api/samples") || {}).samples || {}, o = overlay().labels, later = {};
    recordedQueue(after(D.state.checkpoint)).forEach((r) => { later[labelKey(r.sample_id, r.record_id)] = r.decision; });
    let n = 0;
    for (const r of recordedQueue()) {
      const k = labelKey(r.sample_id, r.record_id);
      if ((samples[names[r.sample_id]] || {}).kind !== "purposive" || r.decision || o[k] || !LABEL_DECISIONS.includes(later[k])) continue;
      const res = await D.answer("POST", "/api/labels", { sample_id: r.sample_id, record_id: r.record_id, decision: later[k], reviewer: REVIEWER });
      if (res && res.status === 200) n++;
    }
    return n;
  }

  D.handle("POST /api/labels", ({ body }) => {
    let b = body || {};
    if (b.item !== undefined) {
      const rid = itemRecord(b.item), sid = rid === null ? null : sampleOf(rid);
      if (sid === null) return refuse(404, "unknown item");
      b = Object.assign({}, b, { sample_id: sid, record_id: rid });
    }
    if (!LABEL_DECISIONS.includes(b.decision)) return refuse(400, "decision must be one of ('include', 'exclude', 'maybe')");
    const row = recordedQueue().find((r) => r.sample_id === b.sample_id && r.record_id === b.record_id);
    const name = sampleNames()[b.sample_id];
    // a locked sample (the validation sample after screening) takes no change without a reason, like the app
    // (label-completion-and-sealing D2, D9); the reposted correction is not kept: the demo's labels are recorded
    const correction = (was, saved) => {
      if (saved && saved.decision === b.decision) return ok({ ok: true, change_id: null });
      if (!String(b.correction_reason || "").trim()) {
        return { status: 409, json: { detail: `sample '${was}' is locked: a change to a saved label needs a reason for the correction`, correction_needed: true } };
      }
      return refuse(400, "The demo keeps its recorded validation labels; in WIDER this saves a documented correction with your reason.");
    };
    if (!row || !name) {
      // a locked sample leaves the queue: the app still knows the sample and the saved label
      const cp = D.order().find((c) => sampleNames(c)[b.sample_id]);
      const was = cp && sampleNames(cp)[b.sample_id];
      const now = was && (((D.recorded("/api/samples") || {}).samples || {})[was] || {});
      if (!now || !now.locked) return refuse(404, "unknown sample");
      return correction(was, recordedQueue(cp).find((r) => r.sample_id === b.sample_id && r.record_id === b.record_id));
    }
    if (row.locked) return correction(name, row);
    const who = String(b.reviewer || REVIEWER).trim().slice(0, 80) || REVIEWER;
    const value = { decision: b.decision, reason: b.reason || "", labels: JSON.stringify(b.labels || {}), note: b.note || "", updated_at: now() };
    const id = record("label", labelKey(b.sample_id, b.record_id), value, `${who} labelled record ${b.record_id} in sample ${name}`, who);
    return ok({ ok: true, change_id: id });
  });

  // any reviewer name gets Reviewer A's queue (D4), with the visitor's labels
  D.patch("GET /api/labels/queue", (json) => (Array.isArray(json) ? withLabels(json) : json));

  D.patch("GET /api/samples", (json) => {
    const o = overlay().labels;
    if (!json || !json.samples || !Object.keys(o).length) return json;
    const names = sampleNames(), base = recordedQueue();
    for (const r of base) {
      const v = o[labelKey(r.sample_id, r.record_id)], name = names[r.sample_id];
      const s = name && json.samples[name];
      if (!v || !s || !s.progress) continue;
      const revs = s.progress.reviewers = s.progress.reviewers || {};
      const c = revs[REVIEWER] = Object.assign({}, revs[REVIEWER]);
      delete c.done;
      if (r.decision) { c[r.decision] -= 1; if (!c[r.decision]) delete c[r.decision]; }
      c[v.decision] = (c[v.decision] || 0) + 1;
      c.done = LABEL_DECISIONS.reduce((n, d) => n + (c[d] || 0), 0);
    }
    return json;
  });

  // ---- duplicate pairs (D4) ---------------------------------------------------------------------------------------
  const pairKey = (a, b) => `${Math.min(a, b)}-${Math.max(a, b)}`;
  const recordedDedup = () => D.recorded("/api/dedup");
  // the pairs as decided now (visitor's overlay over the recording) and as applied by the last "Apply" press
  function pairState(queue) {
    const o = overlay().pairs, applied = overlay().applied || {};
    return queue.map((r) => {
      const k = pairKey(r.raw_a, r.raw_b), v = o[k];
      const recordedDecision = (r.decision || "").startsWith("manual_") ? r.decision : null;
      return { row: r, key: k, now: v ? v.decision : recordedDecision, by: v ? v.by : r.signed_by, at: v ? v.at : r.signed_at,
               applied: k in applied ? applied[k] : recordedDecision };
    });
  }
  // the summary a run with these decisions writes (engine.py), from the recorded run with no manual decision
  function appliedSummary(base, pairs) {
    const s = Object.assign({}, base);
    const same = pairs.filter((p) => p.applied === "manual_same"), diff = pairs.filter((p) => p.applied === "manual_different");
    const unsure = pairs.filter((p) => p.applied === "manual_uncertain").length, open = pairs.filter((p) => !p.applied).length;
    Object.keys(s).filter((k) => k.startsWith("duplicate_records_removed_by_") && k !== "duplicate_records_removed_by_exact_doi")
      .forEach((k) => delete s[k]);
    const auto = base.duplicate_records_removed - (base.manually_confirmed_duplicate_pairs || 0);
    same.forEach((p) => {
      const k = "duplicate_records_removed_by_" + (MANUAL_METHODS[(p.row.diagnostics || {}).candidate_reason] || "exact_title_manual_confirmed");
      s[k] = (s[k] || 0) + 1;
    });
    s.duplicate_records_removed = auto + same.length;
    s.unique_records = s.total_records_imported - s.duplicate_records_removed;
    s.manually_confirmed_duplicate_pairs = same.length;
    s.confirmed_not_duplicate_pairs = diff.length;
    s.uncertain_manual_component_pairs = unsure;
    s.unresolved_manual_component_pairs_retained = open;
    return s;
  }
  function flowMessage(f) {
    if (!f.has_run) return "No deduplication run yet. Press Run deduplication to find the duplicates.";
    if (f.to_decide) return (f.to_decide !== 1 ? `${f.to_decide} pairs need your decision.` : "1 pair needs your decision.") +
      " The record count stays provisional until every pair is decided.";
    if (f.decided_since) return "Every pair is decided. Press Apply decisions and check again to update the counts.";
    if (f.imported_since_run) return (f.imported_since_run !== 1 ? `${f.imported_since_run} records were` : "1 record was") +
      " imported since the last run. Press Apply decisions and check again to include them.";
    if (f.provisional) return `${f.left} records left, provisional while ` + (f.unsure !== 1 ? `${f.unsure} pairs are` : "1 pair is") + " Unsure.";
    return `Deduplication is done: ${f.left} records left.`;
  }
  const removedLine = (f) => (f.removed == null ? "" :
    `${f.removed} duplicates removed (${f.removed_auto} by WIDER, ${f.removed_manual} by your decisions)`);
  function dedupResult(s, pending) {
    const uncertain = s.uncertain_manual_component_pairs || 0, warnings = [];
    if (pending) warnings.push(`${pending} pairs wait for your decision in the queue (rerun after deciding).`);
    if (uncertain) warnings.push(`${uncertain} pairs are decided uncertain; decide them as duplicate or not duplicate and rerun.`);
    if (pending || uncertain) warnings.push("The PRISMA count of records remaining is provisional until every queued pair is decided.");
    const rows = Object.keys(s).filter((k) => k.startsWith("duplicate_records_removed_by_"))
      .map((k) => ({ rule: k.replace("duplicate_records_removed_by_", ""), "records removed": s[k] }));
    return {
      stage: "dedup", action: "run", ok: true, tone: warnings.length ? "warning" : "success", usage: null, ran_at: now(),
      headline: `${s.unique_records} unique records from ${s.total_records_imported} imported; ${s.duplicate_records_removed} duplicates merged.` + SIM,
      metrics: { "unique records": s.unique_records, "duplicates merged": s.duplicate_records_removed, "pending pairs": pending,
                 "manual decisions applied": s.manually_confirmed_duplicate_pairs + s.confirmed_not_duplicate_pairs },
      rows, warnings, outputs: ["exports/dedup/"], next_stage: "enrich",
    };
  }
  // the dedup answer with the visitor's decisions: rows, live counts and, after Apply, the applied counts
  function dedupWithOverlay(json) {
    const pairs = pairState(json.queue || []), o = overlay();
    pairs.forEach((p) => {
      if (!p.now) return;
      const words = PAIR_WORDS[p.now];
      Object.assign(p.row, { decision: p.now, status: p.now, status_words: words, signed_by: p.by, signed_at: p.at,
                             decided_by: p.by, decided_at: p.at, source: null, text: `${words} (${p.by})` });
    });
    const f = json.flow = Object.assign({}, json.flow);
    f.to_decide = pairs.filter((p) => !p.now).length;
    f.decided = pairs.length - f.to_decide;
    f.unsure = pairs.filter((p) => p.now === "manual_uncertain").length;
    f.decided_since = pairs.filter((p) => p.now !== p.applied).length;
    if (o.applied) {
      const s = json.summary = appliedSummary(json.summary, pairs);
      f.left = s.unique_records;
      f.removed = s.duplicate_records_removed;
      f.removed_manual = s.manually_confirmed_duplicate_pairs;
      f.removed_auto = f.removed - f.removed_manual;
      f.provisional = !!(s.unresolved_manual_component_pairs_retained || s.uncertain_manual_component_pairs);
      f.removed_line = removedLine(f);
      if (o.ranAt) json.run_at = o.ranAt;
      const last = (json.prisma || []).length - 1;
      if (last >= 2) {
        json.prisma[last - 1] = ["Duplicate records removed before screening", s.duplicate_records_removed];
        json.prisma[last] = [f.provisional ? "Provisional records remaining after automated deduplication" :
          "Records remaining for title and abstract screening", s.unique_records];
      }
    }
    f.message = flowMessage(f);
    json.pending = f.to_decide;
    json.decided_since_run = f.decided_since;
    return json;
  }
  const dedupOpen = () => at(D.order()[0]);   // pairs are decided before ranking only: later pages follow Reviewer A

  D.handle("POST /api/dedup/decide", ({ body }) => {
    const b = body || {}, m = PAIR_DECISIONS[b.decision];
    if (!m) return refuse(400, "decision must be duplicate, not_duplicate or uncertain (or unsure, the same)");
    if (!dedupOpen()) return refuse(409, "In the demo, duplicate pairs are decided before ranking. Reset (in the demo bar) starts again.");
    const k = pairKey(b.raw_a, b.raw_b), d = recordedDedup();
    if (!d || !(d.queue || []).some((r) => pairKey(r.raw_a, r.raw_b) === k)) return refuse(400, `raw record ${b.raw_a} does not exist`);
    const by = String(b.by || REVIEWER).trim().slice(0, 80) || REVIEWER;
    const id = record("pair", k, { decision: m, by, at: now() }, `raw records ${k.replace("-", " and ")}: ${b.decision.replace("_", " ")}`, by);
    return ok({ ok: true, change_id: id });
  });

  D.handle("POST /api/dedup/run", () => {
    const d = recordedDedup();
    if (!d) return undefined;
    if (!dedupOpen()) {   // a re-run after ranking changes nothing: the recorded result
      const r = ((D.recorded("/api/stages") || {}).last_results || {}).dedup;
      return ok({ summary: d.summary, stage_result: r ? Object.assign({}, r, { headline: r.headline + SIM, ran_at: now() }) : null });
    }
    const o = overlay(), applied = {};
    pairState(d.queue || []).forEach((p) => { if (p.now) applied[p.key] = p.now; });
    o.applied = applied;
    o.ranAt = now();
    const json = dedupWithOverlay(d);
    return ok({ summary: json.summary, stage_result: keep("dedup", dedupResult(json.summary, json.flow.to_decide)) });
  });

  D.patch("GET /api/dedup", (json) => (json && dedupOpen() ? dedupWithOverlay(json) : json));
  // the other pages and notices that count the open pairs were recorded before any decision: while pairs are decided
  // here, they say the count the Deduplication page shows (a pair decided in the demo is no longer "to decide")
  const pairsOpen = () => pairState((recordedDedup() || {}).queue || []).filter((p) => !p.now).length;
  function livePairs(json) {
    if (!json || !dedupOpen()) return json;
    const n = pairsOpen(), all = ((recordedDedup() || {}).queue || []).length;
    if (n === all) return json;
    const pairs = (one, many) => (n === 1 ? `1 pair ${one}` : `${n} pairs ${many}`);
    const say = (s) => s
      .replace(/\b\d+ pairs? waits? for your decision\. Decide them, then press Apply decisions and check again\./g, () =>
        (n ? `${pairs("waits", "wait")} for your decision. Decide ${n === 1 ? "it" : "them"}, then press Apply decisions and check again.`
          : "Every pair is decided. Press Apply decisions and check again to update the counts."))
      .replace(/\b\d+ pairs? to decide\b/g, () => (n ? pairs("to decide", "to decide") : "every pair decided, not yet applied"))
      .replace(/\b\d+ pairs? waits? for your decision in the queue \(rerun after deciding\)/g, () =>
        (n ? `${pairs("waits", "wait")} for your decision in the queue (rerun after deciding)` : "every pair is decided (rerun to apply)"));
    const walk = (x) => {
      if (typeof x === "string") return say(x);
      if (Array.isArray(x)) return x.map(walk);
      if (x && typeof x === "object") { Object.keys(x).forEach((k) => { x[k] = walk(x[k]); }); return x; }
      return x;
    };
    return walk(json);
  }
  ["GET /api/guide/dedup", "GET /api/home", "GET /api/project/summary", "GET /api/notebook/dedup", "GET /api/stages",
   "GET /api/stages/dedup/results"].forEach((e) => D.patch(e, livePairs));

  // a simulated run's result stays on its page (the app reads /api/stages again after a run) for as long as the
  // recording shows that same run: on later checkpoints too, until the recording has a newer run of the stage. A run
  // is known by its headline and figures (two screening rounds can have the same headline; each checkpoint was
  // recorded apart, so the same run has another time on each)
  const runId = (r) => (r && r.headline ? JSON.stringify([r.headline, r.metrics || null]) : null);
  const recordedRun = (stage) => runId((((D.recorded("/api/stages") || {}).last_results || {})[stage]));
  // (saved at once: the result survives a reload, and the tour sees the step done)
  function keep(stage, result) {
    if (result) { overlay().results[stage] = { base: recordedRun(stage), result }; D.save(); }
    return result;
  }
  D.patch("GET /api/stages", (json) => {
    const res = overlay().results;
    if (json && json.last_results) {
      Object.keys(res).forEach((k) => {
        if (res[k].base && runId(json.last_results[k]) === res[k].base) json.last_results[k] = res[k].result;
      });
    }
    if (json && json.project_key) {   // the app opens the Home page, not the Model page, when it has no last page (D7)
      try {
        const k = "wider.lastPage." + json.project_key;
        if (!W.localStorage.getItem(k)) W.localStorage.setItem(k, "home");
      } catch (e) { /* the hash still names the page */ }
    }
    return json;
  });

  // ---- undo and redo of the demo's own changes (D4) ---------------------------------------------------------------
  const find = (id) => overlay().changes.find((c) => c.id === id);
  const original = (c) => (c.of ? find(c.of) : c);
  const same = (a, b) => JSON.stringify(a || null) === JSON.stringify(b || null);
  function undo(c, author) {
    if (c.gone || c.checkpoint !== D.state.checkpoint) return refuse(409, GONE);
    if (!same(slot(c.kind)[c.key], c.after)) return refuse(409, "A later change to the same item stands in the way; undo that one first.");
    const verb = c.verb === "undo" ? "redo" : "undo", base = original(c);
    if (!c.verb && c.undone) return refuse(409, "That change is already undone.");
    const id = newId();
    put(c.kind, c.key, c.before);
    overlay().changes.push({ id, kind: c.kind, key: c.key, before: c.after, after: c.before, summary: base.summary,
                             author: author || base.author, checkpoint: c.checkpoint, of: base.id, verb });
    base.undone = verb === "undo";
    return ok({ ok: true, change_id: id, summary: `${verb}: ${base.summary}` });
  }
  D.handle("POST /api/history/{change_id}/undo", ({ url }) => {
    const id = decodeURIComponent(url.pathname.split("/")[3] || ""), c = find(id);
    return c ? undo(c) : refuse(409, ONLY_DEMO);
  });
  D.handle("POST /api/history/undo-last", ({ url }) => {
    const who = (url.searchParams.get("author") || "").trim();
    if (!who) return refuse(400, "undo-last needs the reviewer's name");
    // the caller's own newest change that is not an undo or redo and is in effect, as in WIDER
    const live = overlay().changes.filter((x) => !x.verb && !x.undone && !x.gone);
    if (!live.length) return refuse(409, ONLY_DEMO);
    const c = live.slice().reverse().find((x) => x.author === who);
    return c ? undo(c, who) : refuse(409, `nothing to undo by ${who}`);
  });

  // ---- runs that move the demo to its next checkpoint (D5) --------------------------------------------------------
  // what the demo filled in for the visitor, one line each, when a run moves on with Reviewer A's decisions (D4)
  function filledIn() {
    const lines = [], o = overlay();
    if (dedupOpen()) {
      const d = recordedDedup(), next = D.recorded("/api/dedup", D.order()[1]);
      if (d && next) {
        const theirs = {};
        (next.queue || []).forEach((r) => { theirs[pairKey(r.raw_a, r.raw_b)] = r.decision; });
        const pairs = pairState(d.queue || []);
        const differ = pairs.filter((p) => p.now && p.now !== theirs[p.key]).length, open = pairs.filter((p) => !p.now).length;
        if (differ) lines.push(`You decided ${plural(differ, "pair")} differently from Reviewer A.`);
        if (open) lines.push(`The ${plural(open, "pair")} you left open ${open === 1 ? "was" : "were"} decided as Reviewer A decided ${open === 1 ? "it" : "them"}.`);
      }
    }
    // Reviewer A's label is the one recorded at the next checkpoint (a record still open here is labelled there)
    const rows = recordedQueue(), later = {};
    recordedQueue(after(D.state.checkpoint)).forEach((r) => { later[labelKey(r.sample_id, r.record_id)] = r.decision; });
    const theirs = (r) => later[labelKey(r.sample_id, r.record_id)] || r.decision;
    const mine = rows.filter((r) => o.labels[labelKey(r.sample_id, r.record_id)]);
    const differ = mine.filter((r) => o.labels[labelKey(r.sample_id, r.record_id)].decision !== theirs(r)).length;
    const open = rows.filter((r) => !r.decision && !o.labels[labelKey(r.sample_id, r.record_id)]).length;
    if (differ) lines.push(`You labelled ${plural(differ, "record")} differently from Reviewer A.`);
    if (open && rows.length) lines.push(`The ${plural(open, "record")} you left unlabelled ${open === 1 ? "was" : "were"} labelled as Reviewer A labelled ${open === 1 ? "it" : "them"}.`);
    if (lines.length) lines.push("The demo continues with Reviewer A's decisions so the numbers on every page agree; in WIDER your own decisions would be used.");
    return lines;
  }
  // the next checkpoint: the visitor's overlay is folded into Reviewer A's recording, its changes can no longer be undone
  function moveTo(name) {
    const o = overlay();
    o.labels = {}; o.pairs = {};
    delete o.applied; delete o.ranAt;
    o.changes.forEach((c) => { c.gone = true; });
    D.setCheckpoint(name);
  }
  function recordedResult(stage, checkpoint, extra) {
    const r = ((D.recorded("/api/stages", checkpoint) || {}).last_results || {})[stage];
    if (!r) return null;
    const out = Object.assign({}, r, { headline: r.headline + SIM, ran_at: now() });
    if (extra && extra.length) out.warnings = (r.warnings || []).concat(extra);
    return out;
  }
  const after = (name) => D.order()[D.order().indexOf(name) + 1];
  const before = (name) => D.order().indexOf(D.state.checkpoint) < D.order().indexOf(name);

  D.handle("POST /api/rank/run", () => {
    const here = D.state.checkpoint, next = after(here);
    if (!dedupOpen() || !next) return ok({ stage_result: keep("rank", recordedResult("rank", here)) });   // the same order again
    const notes = filledIn();
    return wait(timing.rank).then(() => {
      moveTo(next);
      return ok({ stage_result: keep("rank", recordedResult("rank", next, notes)) });
    });
  });

  D.handle("POST /api/samples/draw", () => {
    const ranked = D.order()[1], sampled = D.order()[2];
    if (!ranked || !sampled) return undefined;
    if (before(ranked)) return refuse(409, "Rank the records first (Ranking page).");
    if (!at(ranked)) return refuse(409, "The samples are already drawn. In the demo, Reset (in the demo bar) starts again.");
    return wait(timing.draw).then(() => {
      moveTo(sampled);
      return ok({ samples: {}, stage_result: keep("sample", recordedResult("sample", sampled)) });
    });
  });

  // the Samples page's step marks (D32 F6) read the target and plan; the demo project sets neither, so step 1 is next
  D.handle("GET /api/validation/status", () => ok({ target: null, planned: false }));

  // ---- the model, replayed (task 2.6) -----------------------------------------------------------------------------
  // the demo has no key: a setting that names one says its answers are recorded, not that the key is missing
  const REPLAYED = "in the demo: recorded answers are replayed, nothing is sent";
  function replayed(row) {
    if (!row || !row.api_key_env) return row;
    return Object.assign({}, row, { api_key_env: null, key_set: false, destination: `${row.destination || "?"} (${REPLAYED})` });
  }
  D.patch("GET /api/providers", (json) => {
    if (json && Array.isArray(json.providers)) json.providers = json.providers.map(replayed);
    return json;
  });
  // the Home page's and the summary's backend chips ("text is sent to ...")
  const remote = (b) => (b && b.locality !== "local" && b.destination ? Object.assign({}, b, { destination: `${b.destination} (${REPLAYED})` }) : b);
  const chips = (p) => { if (p && p.backends) Object.keys(p.backends).forEach((k) => { p.backends[k] = remote(p.backends[k]); }); return p; };
  D.patch("GET /api/home", (json) => { if (json && Array.isArray(json.projects)) json.projects.forEach(chips); return json; });
  D.patch("GET /api/project/summary", chips);
  D.patch("GET /api/embeddings", (json) => {
    if (json && Array.isArray(json.embeddings)) json.embeddings = json.embeddings.map(replayed);
    return json;
  });

  // shared with the screening layer (screening.js), which runs the jobs that move the demo on
  W.DemoActions = { timing, filledIn, sampleNames, devOpen, fillDev, overlay, keep, moveTo, recordedResult, after, before, at, ok, refuse, plural, SIM };
})();
