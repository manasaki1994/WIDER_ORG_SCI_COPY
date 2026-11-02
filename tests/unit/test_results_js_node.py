"""results.js runs under Node (no browser): the Results dashboard page draws the dict of `dashboard()` (the one
`wider results --json` prints) with the CLI's headings and sentences, in normal text; its selects only call
GET /api/results; "Every set at once" on the Export records card calls the CLI's twin. Synthetic, topic-neutral
records only (the 600-record ranking of test_validation_suggest)."""
import json
import re

from tests.unit.test_agreement import fill, proj, rules  # noqa: F401 - pytest fixture
from tests.unit.test_backends_js_node import ESC, STATIC, run_node
from tests.unit.test_recall_curve import _bands, _sampled
from tests.unit.test_results_dashboard import COUNTED, SECTIONS, _models, _set2, _store_partly
from tests.unit.test_validation_suggest import _drawn
from wider.recall import curve
from wider.recall import dashboard as dash
from wider.recall.estimate import estimate
from wider.sample.labels import label, sealed_change_line

RESULTS = (STATIC / "results.js").read_text(encoding="utf-8")


def page(body: str) -> dict:
    """results.js with the real escaping; ``body`` runs after it."""
    return run_node(f"{ESC}\n{RESULTS}\n{body}")


def when(t: str) -> str:
    """A stored time as the pages show it (fmtWhen of common.js)."""
    return page(f"console.log(JSON.stringify(fmtWhen({json.dumps(t)})));")


def html_of(d: dict) -> str:
    return page(f"console.log(JSON.stringify(Results.pageHtml({json.dumps(d)})));")


def text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]*>", " ", html)).replace("&#39;", "'").replace("&amp;", "&").strip()


# a controller on fake parts: `api` records each call and answers from `replies` (an Error for a string reply)
CTRL = r"""
const calls = [];
const mk = () => ({ innerHTML: "", value: "", focus() { this.focused = true; } });
const parts = { protocol: mk(), set: mk(), sample: mk(), body: mk() };
let replies = [];
const api = async (path, opts) => { calls.push([path, (opts && opts.method) || "GET"]); const r = replies.shift();
  if (typeof r === "string") throw new Error(r); return r; };
const c = Results.controller(parts, { api });
"""


def test_dashboard_sections(tmp_path):
    """The heading (set and models), then the seven sections in the CLI's order with its headings; the recall lines,
    the counts and the reference sets as `wider results` prints them."""
    p = _drawn(tmp_path)
    _set2(p)
    _models(p, "main", 1)
    estimate(p, "validation", "main", B=50)
    d = dash.dashboard(p, "main")
    html = html_of(d)
    assert '<h2 data-results-title>main (set 1)</h2>' in html
    assert "<p data-results-models>Pass A: a; pass B: b; third read: c</p>" in html
    pos = [re.search(rf"<h3( tabindex=\"-1\")?>{re.escape(s)}</h3>", html).start() for s in SECTIONS]
    assert pos == sorted(pos)
    lines = dash.dashboard_lines(d)
    body = text(html)
    for line in d["recall"]["lines"] + [f"as of {when(d['recall']['as_of'])}"]:
        assert line in body
    for key, lab in dash.COUNT_LINES:
        if key == "not_checked" and not d["counts"][key]:      # shown only above zero (D32 F4)
            assert lab not in body and not any(x.startswith(lab) for x in lines)
            continue
        assert f"{lab}: {d['counts'][key]:,}" in lines and f"{lab} {d['counts'][key]:,}" in body
    for s in d["reference_sets"]["sets"]:
        assert f'data-ref-set="{s["set"]}"' in html
    # the agreement from the same lines the CLI prints: sentences, then the routes as a list
    assert "<p>No record was read by both passes.</p>" in html and "<li>both passes agreed: 0</li>" in html
    # important text is never grey
    assert "muted" not in html


def test_agreement_table(proj):  # noqa: F811
    """Records read by both passes: the CLI's "|" lines become one table, its header row the "pass A \\ pass B" row, each cell
    the CLI's text."""
    fill(proj, "main", COUNTED, rules())
    d = dash.dashboard(proj, "main")
    rows = [line for line in d["agreement"]["lines"] if line.startswith("|") and not line.startswith("|---")]
    html = page(f"console.log(JSON.stringify(Results.linesHtml({json.dumps(d['agreement']['lines'])})));")
    assert html.count("<table") == 1 and "<th>pass A \\ pass B</th>" in html
    cells = re.findall(r"<t[hd]>([^<]*)</t[hd]>", html)
    want = [c.strip().replace("\\\\", "\\") for r in rows for c in r.strip("|").split("|")]
    assert cells == want
    assert text(html).startswith(d["agreement"]["lines"][0])


def test_nothing_screened():
    """The route's "no screening decisions yet": that sentence in normal text with the way to the Screening page."""
    out = page(CTRL + "replies = ['no screening decisions yet']; await c.load({});"
               "console.log(JSON.stringify({body: parts.body.innerHTML, calls}));")
    assert out["body"].startswith("<p data-results-nothing>no screening decisions yet.")
    assert '<a href="#screen">Screening page</a>' in out["body"]
    assert "class=" not in out["body"]
    assert out["calls"] == [["/api/results", "GET"]]
    # any other error is shown in the error style
    out = page(CTRL + "replies = ['no protocol nosuch']; await c.load({protocol: 'nosuch'});"
               "console.log(JSON.stringify({body: parts.body.innerHTML, calls}));")
    assert out["body"] == '<p class="bad">no protocol nosuch</p>' and out["calls"] == [["/api/results?protocol=nosuch", "GET"]]


def test_recall_none_yet_black_text(tmp_path):
    """No estimate yet: the recall section is the CLI's one sentence, plain (no class), and the chart section says
    there is no chart."""
    p = _drawn(tmp_path)
    d = dash.dashboard(p, "main")
    html = html_of(d)
    sec = html[html.index('data-results-section="recall"'):]
    sec = sec[:sec.index("</section>")]
    assert f"<p data-recall-sentence>{d['recall']['sentence']}</p>" in sec and "class=" not in sec.split("</h3>", 1)[1]
    lines = dash.dashboard_lines(d)
    assert lines[lines.index("Recall") + 1] == d["recall"]["sentence"]
    assert "<p data-curve-sentence>No chart: no recall estimate is shown.</p>" in html


def _curve_sec(d: dict) -> str:
    html = html_of(d)
    return html[html.index('data-results-section="curve"'):html.index('data-results-section="counts"')]


def _svg(sec: str) -> str:
    return sec[sec.index("<svg class=\"results-chart\""):sec.index("</svg>", sec.index("<svg class=\"results-chart\"")) + 6]


def _y(svg: str, name: str) -> list[float]:
    d = re.search(rf'<path data-series="{name}" d="([^"]+)"', svg).group(1)
    return [float(v) for v in re.findall(r"[ML][\d.]+,([\d.]+)", d)]


def test_chart_from_series(tmp_path):
    """Scenario "Chart and table from the stored series": three lines through the band ends 50, 200 and 400 (blue,
    green dashed, violet dotted) with a dot at each, boundary lines at 50 and 200 only, the three legend names and the
    label, no target or stopping line; the points sentence under it; the table is the CLI's (same head and cells)."""
    p = _bands(tmp_path)
    estimate(p, "validation", "main", B=20)
    d = dash.dashboard(p, "main")
    c = d["curve"]
    assert [e["end"] for e in c["ends"]] == [50, 200, 400]
    sec = _curve_sec(d)
    svg = _svg(sec)
    assert re.search(r'<path data-series="relevant" d="M[^"]+" fill="none" stroke="var\(--blue\)" stroke-width="2"/>', svg)
    assert re.search(r'<path data-series="kept" d="M[^"]+"[^>]*stroke="var\(--green\)"[^>]*stroke-dasharray', svg)
    assert re.search(r'<path data-series="missed" d="M[^"]+"[^>]*stroke="var\(--violet\)"[^>]*stroke-dasharray', svg)
    for name in ("relevant", "kept", "missed"):
        assert len(_y(svg, name)) == 4                 # the origin and the three band ends, straight segments
    assert [int(x) for x in re.findall(r'data-band-boundary="(\d+)"', svg)] == [50, 200]
    assert [int(x) for x in re.findall(r'data-band-mark="(\d+)"', svg)] == [50, 200, 400]
    assert svg.count("<circle") == 9
    legend = sec[sec.index("<p data-curve-legend>"):]
    legend = text(legend[:legend.index("</p>")])
    for name in dash.CURVE_LEGEND:
        assert name in legend
    assert "target" not in svg.lower() and "stop" not in svg.lower() and "threshold" not in svg.lower()
    assert f"<p data-curve-label>{dash.CURVE_LABEL}</p>" in sec and f"<p>{dash.CURVE_POINTS}</p>" in sec
    table = sec[sec.index("<table class=\"rec-table\" data-curve-table>"):]
    table = table[:table.index("</table>")]
    assert '<tr><th colspan="5">observed</th><th colspan="6">estimated</th></tr>' in table
    assert re.findall(r"<th>([^<]*)</th>", table) == list(dash.TABLE_HEAD)
    body = [[_unesc(x) for x in re.findall(r"<td[^>]*>([^<]*)</td>", r)] for r in re.findall(r"<tr>(<td.*?)</tr>", table)]
    want = []
    for r in c["rows"]:
        cells = dash.table_cells(r)
        want += [cells[:11]] + [[x] for x in cells[11:]]
    assert body == want
    # the CLI prints the same rows; the last row's kept / relevant is the stored primary estimate
    lines = dash.dashboard_lines(d)
    for r in c["rows"]:
        assert " | ".join(dash.table_cells(r)) in lines
    last = c["ends"][-1]
    assert abs(last["missed"] - (last["rel"] - last["kept"])) < 1e-6


def _unesc(s: str) -> str:
    return s.replace("&#39;", "'").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')


def test_chart_accessible_name_and_axes(tmp_path):
    """Scenario "Accessible chart": role="group" named by its <title> and <desc> (the last point and "the table
    below"); one focusable role="img" mark per band end whose name is the row's tooltip; lines, bars, boundaries and
    axes inside an aria-hidden group; the axis titles in normal-size text in the current (black) colour."""
    p = _bands(tmp_path)
    estimate(p, "validation", "main", B=20)
    d = dash.dashboard(p, "main")
    c = d["curve"]
    svg = _svg(_curve_sec(d))
    assert re.match(r'<svg class="results-chart" role="group" aria-labelledby="curve-title curve-desc"', svg)
    assert f'<title id="curve-title">{dash.CURVE_TITLE}</title>' in svg
    desc = _unesc(re.search(r'<desc id="curve-desc">([^<]*)</desc>', svg).group(1))
    e = c["ends"][-1]
    assert desc == c["desc"] and desc.startswith(f"Up to rank 400: {dash._num(e['rel'])} estimated relevant, "
                                                 f"{dash._num(e['kept'])} kept by screening, {dash._num(e['missed'])} missed.")
    assert "table below" in desc
    marks = re.findall(r'<g data-band-mark="(\d+)" role="img" tabindex="0" aria-label="([^"]*)">', svg)
    assert [(int(m), _unesc(a)) for m, a in marks] == [(r["end"], r["tooltip"]) for r in c["rows"]]
    hidden = svg[svg.index('<g aria-hidden="true">'):svg.index("<g data-band-mark")]
    for part in ("<path data-series", "<rect data-range", "data-band-boundary", 'data-axis="x"'):
        assert part in hidden, part
    assert "tabindex" not in hidden and "<path" not in svg[svg.index("<g data-band-mark"):]
    x = re.search(r'<text [^>]*font-size="(\d+)" fill="currentColor"[^>]*data-axis="x">([^<]*)</text>', svg)
    y = re.search(r'<text [^>]*font-size="(\d+)" fill="currentColor"[^>]*data-axis="y"[^>]*>([^<]*)</text>', svg)
    assert x.group(2) == "Position in the ranking (1 = highest score)" and y.group(2) == "Cumulative estimated relevant records"
    assert int(x.group(1)) >= 13 and int(y.group(1)) >= 13


def _bar(svg: str, name: str):
    return [(float(a), float(b), float(h)) for a, b, h in
            re.findall(rf'<rect data-range="{name}" x="([\d.]+)" y="([\d.]+)" width="6" height="([\d.]+)"', svg)]


def test_range_bars_from_bounds(tmp_path):
    """Scenario "Ranges from the exact bounds": one 6-wide shaded bar per point of each line from its low to its high
    bound, to the right of the point (not centred), no cap lines; the legend names the range; the ranges sentence
    with the stored level; with no bounds stored, no bar, no range legend and the no-ranges sentence."""
    p = _bands(tmp_path)
    estimate(p, "validation", "main", B=20)
    d = dash.dashboard(p, "main")
    c = d["curve"]
    sec = _curve_sec(d)
    svg = _svg(sec)
    xs = [float(v) for v in re.findall(r'<circle cx="([\d.]+)"', svg)][:3]
    H, B, T = 300, 56, 16
    ymax = max(1, *(max(e[k], e[k + "_high"]) for e in c["ends"] for k in ("rel", "kept", "missed")))
    to_y = lambda v: H - B - (H - T - B) * v / ymax   # noqa: E731
    for name, k in (("relevant", "rel"), ("kept", "kept"), ("missed", "missed")):
        bars = _bar(svg, name)
        assert len(bars) == 3, name
        for (bx, by, bh), px, e in zip(bars, xs, c["ends"], strict=True):
            assert bx > px                                             # beside the point, not centred on it
            assert abs(by - to_y(e[k + "_high"])) < 0.2 and abs(by + bh - to_y(e[k + "_low"])) < 0.3 or bh == 1
    assert "<line" not in "".join(re.findall(r"<rect data-range[^>]*/>", svg))
    assert dash.CURVE_RANGE_LEGEND in text(sec) and f"<p>{dash.CURVE_RANGES.format(level='95')}</p>" in sec
    # no bounds stored: no bars, the no-ranges sentence
    rec = curve.stored(p, "validation")
    p.store.set_meta("last_recall_curve:validation", json.dumps({**rec, "has_bounds": False}))
    d = dash.dashboard(p, "main")
    sec = _curve_sec(d)
    assert "data-range" not in sec and dash.CURVE_RANGE_LEGEND not in sec and f"<p>{dash.CURVE_NO_RANGES}</p>" in _unesc(sec)
    assert "data-curve-method" not in sec


def test_point_outside_range_sentence_node(tmp_path):
    """A point above its range (D14, the spec's 3-band sample): drawn where computed (the kept line's first point at
    the stored kept, above its bar), the outside sentence under the chart, in that band's tooltip and as a table row,
    in black (no class); the closed method line with its two sentences."""
    p = _bands(tmp_path)
    estimate(p, "validation", "main", B=20)
    d = dash.dashboard(p, "main")
    c = d["curve"]
    assert c["outside"] and c["rows"][0]["outside"]
    sec = _curve_sec(d)
    svg = _svg(sec)
    ky = _y(svg, "kept")[1]
    bx, by, bh = _bar(svg, "kept")[0]
    assert ky < by                                                   # above its range bar, not moved into it
    assert f"<p data-curve-outside>{dash.CURVE_OUTSIDE}</p>" in sec
    assert dash.CURVE_OUTSIDE in _unesc(re.search(r'data-band-mark="50" [^>]*aria-label="([^"]*)"', svg).group(1))
    assert f'<tr><td colspan="11">{dash.CURVE_OUTSIDE}</td></tr>' in sec
    m = re.search(r"<details data-curve-method><summary>([^<]*)</summary><p>([^<]*)</p></details>", sec)
    assert m and m.group(1) == dash.CURVE_METHOD[0] and _unesc(m.group(2)) == dash.CURVE_METHOD[1]
    assert "<details data-curve-method open" not in sec and 'class="' not in sec.split("data-curve-outside", 1)[1].split("</p>")[0]


# a fake DOM for bindTip: one root that keeps its listeners, the tip, and marks with closest/getAttribute
TIP = r"""
const on = {}; const tip = { hidden: true, textContent: "" };
const root = { addEventListener: (k, f) => { on[k] = f; }, querySelector: (s) => (s === "[data-curve-tip]" ? tip : null) };
const mark = (label) => { const m = { getAttribute: (a) => (a === "aria-label" ? label : null) }; m.closest = (s) => (s === "[data-band-mark]" ? m : null); return m; };
const other = { closest: () => null };
Results.bindTip(root);
const seen = []; const snap = (what) => seen.push([what, tip.hidden, tip.textContent]);
"""


def test_band_tooltip_on_focus_and_hover(tmp_path):
    """Scenario "Tooltip and table row of a band": focus (Tab) or hover on the 51-200 mark shows the one tooltip with
    its text (the mark's name, the spec's sentence); Escape, blur or leaving it hides it; hovering another element does
    not show it."""
    p = _bands(tmp_path)
    estimate(p, "validation", "main", B=20)
    d = dash.dashboard(p, "main")
    row = next(r for r in d["curve"]["rows"] if r["ranks"] == "51-200")
    assert row["tooltip"].startswith("Ranks 51-200: 150 records, 10 sampled, 10 labelled, 2 relevant observed "
                                     "(few relevant observed). Estimated in this band: 30 relevant (range ")
    sec = _curve_sec(d)
    assert '<div class="curve-tip" data-curve-tip aria-hidden="true" hidden></div>' in sec and sec.count("data-curve-tip") == 1
    label = _unesc(re.search(r'data-band-mark="200" [^>]*aria-label="([^"]*)"', sec).group(1))
    assert label == row["tooltip"]
    out = page(TIP + f"const m = mark({json.dumps(label)});"
               "on.focusin({target: m}); snap('focus'); on.keydown({key: 'Escape'}); snap('escape');"
               "on.focusin({target: m}); on.focusout({target: m}); snap('blur');"
               "on.mouseover({target: m}); snap('hover'); on.mouseout({target: m}); snap('leave');"
               "on.mouseover({target: other}); snap('other'); on.keydown({key: 'a'}); snap('key');"
               "console.log(JSON.stringify(seen));")
    assert out == [["focus", False, label], ["escape", True, label], ["blur", True, label], ["hover", False, label],
                   ["leave", True, label], ["other", True, label], ["key", True, label]]


def test_band_without_labels_sentence(tmp_path):
    """Scenario "A band with no labelled record": the lines end at rank 200 (marks at 50 and 200 only), the 201-400 row
    reads "no labelled record: not estimated" across the estimated columns, and the black sentence is the CLI's."""
    p = _bands(tmp_path, unlabelled=(0, 0, 10))
    _store_partly(p)                                   # the series as stored on the labelled rows (task 4.4)
    d = dash.dashboard(p, "main")
    sec = _curve_sec(d)
    svg = _svg(sec)
    assert [int(x) for x in re.findall(r'data-band-mark="(\d+)"', svg)] == [50, 200]
    assert len(_y(svg, "relevant")) == 3
    s = ("Ranks 201-400 hold no labelled record, so nothing is estimated from rank 201 on; label that band's sampled "
         "records and estimate recall again.")
    assert d["curve"]["stop"]["sentence"] == s and f"<p data-curve-stop>{s.replace(chr(39), '&#39;')}</p>" in sec
    assert re.search(r'<tr><td>201-400</td><td>200</td><td>10</td><td>0</td><td>0 \(few relevant observed\)</td>'
                     r'<td colspan="6">no labelled record: not estimated</td></tr>', sec)
    assert s in dash.dashboard_lines(d)
    # the first band unlabelled: no line at all, only the table and the sentence
    q = _bands(tmp_path / "first", unlabelled=(10, 0, 0))
    _store_partly(q)
    sec = _curve_sec(dash.dashboard(q, "main"))
    assert "<svg class=\"results-chart\"" not in sec and "data-curve-stop" in sec and "data-curve-table" in sec


def test_label_link_node(tmp_path):
    """Scenario "A band partly labelled": the sentence is followed by the link "Label them on Samples and labelling"
    to #sample (the Samples and labelling page of app.js); the lines end at rank 50."""
    p = _bands(tmp_path, unlabelled=(0, 5, 0))
    _store_partly(p)                                   # the series as stored on the labelled rows (task 4.4)
    d = dash.dashboard(p, "main")
    sec = _curve_sec(d)
    s = ("Ranks 51-200: 5 of 10 sampled records are not labelled yet, so nothing is estimated from rank 51 on; label "
         "them and estimate recall again.")
    assert f'<p data-curve-stop>{s}</p><p><a href="#sample" data-curve-label-link>Label them on Samples and labelling</a></p>' in sec
    assert [int(x) for x in re.findall(r'data-band-mark="(\d+)"', sec)] == [50]
    assert "5 of 10 sampled unlabelled: not estimated" in sec
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    pages = next(line for line in app.splitlines() if line.startswith("const PAGES = "))
    assert re.search(r'\bsample: "page-sample"', pages)
    index = (STATIC / "index.html").read_text(encoding="utf-8")
    assert re.search(r'<section id="page-sample"[^>]*>\s*<div class="page-head"><h1>Samples and labelling</h1>', index)


def test_estimate_again_button_node(tmp_path):
    """Scenario "Stale chart estimated again": the stale chart shows the button and the CLI command; pressing it sends
    the Recall page's GET /api/recall (protocol, sample and result_set, as the Recall page sends it) and reads the
    dashboard again, focus on the chart's heading; that request on the real route adds one look and the dashboard is
    current again; a choice notice is asked as the Recall page asks it, and Cancel does nothing."""
    p = _drawn(tmp_path)
    estimate(p, "validation", "main", B=20)
    rid = _sampled(p)[0]
    lab = p.store.one("SELECT decision FROM labels WHERE record_id=? AND reviewer_id='r1'", (rid,))["decision"]
    label(p, "validation", rid, "r1", "exclude" if lab == "include" else "include",
          correction_reason="synthetic correction")
    d = dash.dashboard(p, "main")
    sec = _curve_sec(d)
    assert (f"<p data-curve-sentence>{dash.CURVE_STALE}</p><p><button class=\"secondary\" data-curve-again>Estimate recall again</button></p>"
            '<p data-curve-msg role="status"></p><details class="cmdline"><summary>Command line</summary>'
            '<p class="small"><code>wider recall main --sample validation</code></p></details>') in sec
    els = "const els = {}; parts.find = (s) => (els[s] = els[s] || mk());"
    out = page(CTRL + els + f"replies = [{json.dumps(d)}]; await c.load({{}});"
               f"replies = [{{variants: {{}}}}, {json.dumps(d)}]; await c.estimateAgain();"
               "const h = els['[data-results-section=\"curve\"] h3'];"
               "console.log(JSON.stringify({calls, focused: !!(h && h.focused)}));")
    assert out["focused"]
    out["calls"] = [x for x in out["calls"] if not x[0].startswith("/api/outputs/")]   # the card's own calls
    assert out["calls"][1:] == [["/api/recall?protocol=main&sample=validation&result_set=1", "GET"],
                                ["/api/results?protocol=main&result_set=1&sample=validation", "GET"]]
    looks = p.store.scalar("SELECT SUM(n) FROM validation_looks")
    from tests.unit.test_results_dashboard import api, fresh
    assert api(p).get(out["calls"][1][0]).status_code == 200
    assert fresh(p).store.scalar("SELECT SUM(n) FROM validation_looks") == looks + 1
    c2 = dash.dashboard(fresh(p), "main")["curve"]
    assert c2["state"] == "current" and "data-curve-again" not in _curve_sec(dash.dashboard(fresh(p), "main"))
    # a set that is not active is named; the choice is asked, then sent; Cancel leaves "Nothing was done."
    d2 = {**d, "curve": {**d["curve"], "estimate_again": {"protocol": "main", "sample": "validation", "result_set": 2}}}
    ask = "const asked = []; const c2 = Results.controller(parts, { api, ask: async (n) => { asked.push(n); return asked.length === 1 ? 'not_informed' : null; } });"
    out = page(CTRL + els + ask + f"replies = [{json.dumps(d2)}]; await c2.load({{}});"
               f"replies = [{{choice_needed: true, notice: 'N'}}, {{variants: {{}}}}, {json.dumps(d2)}]; await c2.estimateAgain();"
               "replies = [{choice_needed: true, notice: 'N'}]; await c2.estimateAgain();"
               "console.log(JSON.stringify({calls, asked, msg: els['[data-curve-msg]'].innerHTML}));")
    out["calls"] = [x for x in out["calls"] if not x[0].startswith("/api/outputs/")]
    assert [x[0] for x in out["calls"][1:]] == ["/api/recall?protocol=main&sample=validation&result_set=2",
                                                 "/api/recall?protocol=main&sample=validation&result_set=2&choice=not_informed",
                                                 "/api/results?protocol=main&result_set=1&sample=validation",
                                                 "/api/recall?protocol=main&sample=validation&result_set=2"]
    assert out["asked"] == ["N", "N"] and out["msg"] == "Nothing was done."
    # the Recall page builds the same query: protocol, sample, then result_set
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    assert 'new URLSearchParams({ protocol: $("#rc-protocol").value, sample: $("#rc-sample").value });' in app
    assert 'q.set("result_set", $("#rc-set").value)' in app


def test_estimate_again_while_incomplete(tmp_path):
    """label-completion-and-sealing D4 (task 6.2): Estimate recall again while a sampled record has no final label;
    GET /api/recall answers {labels_incomplete, notice}: the notice is written, escaped, in [data-curve-msg], the
    dashboard is not read again and estimateAgain returns null. The reply is the real route's."""
    from tests.unit.test_results_dashboard import api
    p = _bands(tmp_path, unlabelled=(0, 5, 0))
    _store_partly(p)
    d = dash.dashboard(p, "main")
    d = {**d, "curve": {**d["curve"], "estimate_again": {"protocol": "main", "sample": "validation", "result_set": 1}}}
    looks = p.store.scalar("SELECT COALESCE(SUM(n), 0) FROM validation_looks")
    g = api(p).get("/api/recall", params=d["curve"]["estimate_again"])
    assert g.status_code == 200 and g.json()["labels_incomplete"] is True and g.json()["notice"]
    assert p.store.scalar("SELECT COALESCE(SUM(n), 0) FROM validation_looks") == looks   # nothing recorded
    els = "const els = {}; parts.find = (s) => (els[s] = els[s] || mk());"
    for reply, want in ((g.json(), g.json()["notice"]),
                        ({"labels_incomplete": True, "notice": "Not yet: <b>5</b> & more."},
                         "Not yet: &lt;b&gt;5&lt;/b&gt; &amp; more.")):
        out = page(CTRL + els + f"replies = [{json.dumps(d)}]; await c.load({{}});"
                   f"replies = [{json.dumps(reply)}]; const r = await c.estimateAgain();"
                   "console.log(JSON.stringify({calls, r, msg: els['[data-curve-msg]'].innerHTML}));")
        calls = [x for x in out["calls"] if not x[0].startswith("/api/outputs/")]
        assert out["r"] is None and calls[1:] == [["/api/recall?protocol=main&sample=validation&result_set=1", "GET"]]
        assert out["msg"] == (want if "<" in reply["notice"] or "&" in reply["notice"] else
                              page(f"console.log(JSON.stringify(esc({json.dumps(want)})));"))


def test_every_set_at_once_on_card_node(tmp_path):
    """Scenario "All sets written": **Every set at once** sits on the Export records card with the CLI command and is
    the dashboard's only such button; it calls POST /api/outputs/references?all=true for the shown set (set 2 named, as
    it is not active), links the index, shows it as a table ("no files" for an empty set), then reads the dashboard
    again for the Documents section only."""
    p = _drawn(tmp_path)
    _set2(p)
    d2 = dash.dashboard(p, "main", 2)
    page_html = html_of(d2)
    assert '<button class="secondary" data-export-all>Every set at once</button></p>' in page_html
    assert re.search(r'<details class="cmdline"><summary>Command line</summary><p class="small"><code>wider export '
                     r'selection[^<]*</code>, <code>wider export references --all[^<]*</code></p></details></div>', page_html)
    assert page_html.count("data-export-all") == 1 and "data-results-write-all" not in page_html
    card = page_html.index("<div data-export-card>")
    assert page_html.index('data-results-section="reference_sets"') < card < page_html.index("data-export-all")
    written = {"index": "exports/references/main_set2/index.md", "sets": [{"set": "include", "records": 1200, "files": ["a"]},
                                                                         {"set": "<b>x</b>", "records": 1, "files": ["a&b.ris"]},
                                                                         {"set": "maybe", "records": 0, "files": []}]}
    els = "const els = {}; parts.find = (s) => (els[s] = els[s] || mk()); const el = (n) => els['[data-export-' + n + ']'] || mk();"
    # load without the card's own calls: the card is driven in test_outputs.py; here only Every set at once
    out = page(CTRL + els + f"replies = [{json.dumps(d2)}];"
               "const f = parts.find; parts.find = null; await c.load({protocol: 'main', result_set: '2'}); parts.find = f;"
               f"replies = [{json.dumps(written)}, {json.dumps(d2)}]; await c.writeAll();"
               "console.log(JSON.stringify({calls, msg: el('msg').innerHTML, index: el('index').innerHTML,"
               " docs: els['[data-results-section=\"documents\"]'].innerHTML}));")
    assert out["calls"] == [["/api/results?protocol=main&result_set=2", "GET"],
                            ["/api/outputs/references?all=true&protocol=main&result_set=2", "POST"],
                            ["/api/results?protocol=main&result_set=2&sample=validation", "GET"]]
    assert out["msg"].startswith("2 sets written: <a href=\"/api/outputs/file?path=exports%2Freferences%2Fmain_set2%2Findex.md\" download>")
    idx = out["index"]
    assert 'data-refs-set="include"><td>include</td><td>1,200</td>' in idx
    assert re.search(r'data-refs-set="maybe"><td>maybe</td><td>0</td><td>no files</td>', idx)
    assert "&lt;b&gt;x&lt;/b&gt;" in idx and "<b>x</b>" not in idx and "a%26b.ris" in idx
    assert out["docs"].startswith(f"<h3>{d2['headings']['documents']}</h3>")
    # the active set: no result_set in the call, as `wider export references --all` without --set
    d1 = dash.dashboard(p, "main")
    out = page(CTRL + els + f"replies = [{json.dumps(d1)}];"
               "const f = parts.find; parts.find = null; await c.load({}); parts.find = f;"
               f"replies = [{json.dumps(written)}, {json.dumps(d1)}]; await c.writeAll(); console.log(JSON.stringify({{calls}}));")
    assert out["calls"][1] == ["/api/outputs/references?all=true&protocol=main", "POST"]


def test_selects_send_choice_without_activating(tmp_path):
    """The selects are filled from the choices (the active set marked); choosing set 2 or another sample only calls
    GET /api/results with protocol, result_set and sample, never a route that changes the active set."""
    p = _drawn(tmp_path)
    _set2(p)
    d1, d2 = dash.dashboard(p, "main"), dash.dashboard(p, "main", 2)
    out = page(CTRL + f"replies = [{json.dumps(d1)}, {json.dumps(d2)}, {json.dumps(d2)}, {json.dumps(d1)}];"
               "await c.load({}); const sets = parts.set.innerHTML;"
               "parts.set.value = '2'; await c.changed('set');"
               "parts.sample.value = 'other'; await c.changed('sample');"
               "parts.protocol.value = 'main'; await c.changed('protocol');"
               "console.log(JSON.stringify({calls, sets, set: parts.set.value}));")
    assert out["calls"] == [["/api/results", "GET"],
                            ["/api/results?protocol=main&result_set=2&sample=validation", "GET"],
                            ["/api/results?protocol=main&result_set=2&sample=other", "GET"],
                            ["/api/results?protocol=main&sample=validation", "GET"]]   # the shown sample
    assert all(m == "GET" and path.startswith("/api/results") for path, m in out["calls"])
    assert '<option value="1" selected>set 1 (active)</option>' in out["sets"] and '<option value="2">set 2</option>' in out["sets"]
    assert out["set"] == "1"


def test_stale_and_missing_series_sentences(tmp_path):
    """A label changed after the estimate: the recall section shows the CLI's stale sentence above the earlier lines
    and the chart section its redraw sentence, both plain; a series not stored: the CLI's missing sentence."""
    p = _drawn(tmp_path)
    estimate(p, "validation", "main", B=50)
    rid = _sampled(p)[0]
    lab = p.store.one("SELECT decision FROM labels WHERE record_id=? AND reviewer_id='r1'", (rid,))["decision"]
    label(p, "validation", rid, "r1", "exclude" if lab == "include" else "include",
          correction_reason="synthetic correction")
    d = dash.dashboard(p, "main")
    assert d["curve"]["state"] == "stale"
    html = html_of(d)
    assert (f"<p data-recall-sentence>{d['recall']['sentence']}</p><p data-recall-headline><strong>"
            f"{d['recall']['headline']}</strong></p><p>as of {when(d['recall']['as_of'])}</p>") in html
    assert f"<p data-curve-sentence>{dash.CURVE_STALE}</p>" in html and "<svg" not in html
    lines = dash.dashboard_lines(d)
    assert dash.CURVE_STALE in lines and d["recall"]["sentence"] in lines
    p.store.execute("DELETE FROM meta WHERE key='last_recall_curve:validation'")
    label(p, "validation", rid, "r1", lab, correction_reason="synthetic correction undone")
    d = dash.dashboard(p, "main")
    assert f"<p data-curve-sentence>{dash.CURVE_MISSING}</p>" in html_of(d) and dash.CURVE_MISSING in dash.dashboard_lines(d)


def test_results_hash_opens_page():
    """#results is a page of app.js (page-results, loaded by loadResults); index.html has the section, its heading,
    the three selects and the script before shell.js."""
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    pages = next(line for line in app.splitlines() if line.startswith("const PAGES = "))
    assert 'results: "page-results"' in pages and "results: window.loadResults" in app
    index = (STATIC / "index.html").read_text(encoding="utf-8")
    sec = index[index.index('<section id="page-results"'):]
    sec = sec[:sec.index("</section>")]
    assert "<h1>Results dashboard</h1>" in sec
    for sel in ("rs-protocol", "rs-set", "rs-sample", "rs-body"):
        assert f'id="{sel}"' in sec
    assert index.index('src="/static/results.js"') < index.index('src="/static/shell.js"')
    assert '<symbol id="i-results"' in index
    assert "window.loadResults" in RESULTS


def test_chart_is_a_named_picture_with_two_axis_titles(tmp_path):
    """Scenario "Dashboard chart, coverage plot and PRISMA diagram", the dashboard half: the chart sits under the
    heading "Relevant found along the ranking", the heading `wider results` prints above the same points as a table;
    the SVG is named (a group, since its band marks are focusable pictures of their own) and titles both axes, the y
    axis with "estimated relevant records"."""
    from typer.testing import CliRunner

    from wider.cli import app as cli_app

    p = _bands(tmp_path)
    estimate(p, "validation", "main", B=20)
    d = dash.dashboard(p, "main")
    sec = _curve_sec(d)
    svg = _svg(sec)
    assert "Relevant found along the ranking" in sec
    assert re.match(r'<svg class="results-chart" role="group" aria-labelledby="curve-title curve-desc"', svg)
    assert re.search(r'<title id="curve-title">[^<]+</title>', svg)
    x = re.search(r'data-axis="x"[^>]*>([^<]+)<', svg).group(1)
    y = re.search(r'data-axis="y"[^>]*>([^<]+)<', svg).group(1)
    assert x and "estimated relevant records" in y.lower()
    r = CliRunner().invoke(cli_app, ["results", "main", "--dir", str(p.root)], env={"COLUMNS": "250"})
    assert r.exit_code == 0, r.output
    lines = r.output.splitlines()
    at = lines.index("Relevant found along the ranking")
    assert any(str(e["end"]) in ln for e in d["curve"]["ends"] for ln in lines[at + 1:at + 12])


def _command_fields(x, key=""):
    """Every value of a field named ``command`` or ``*_command`` in the dashboard dict."""
    if isinstance(x, dict):
        return [v for k, w in x.items() for v in ([w] if (k == "command" or k.endswith("_command")) and isinstance(w, str)
                                                  else _command_fields(w, k))]
    return [v for w in x for v in _command_fields(w)] if isinstance(x, list) else []


def test_dashboard_commands_in_folds(tmp_path):
    """Scenario "No command in the page text": with no recall target, a stale chart and no document written, every
    command the server sends in its own field (`again_command`, `all_command`, a document's `command`) is drawn only
    inside a closed "Command line" fold; the server sentence NO_TARGET keeps its words, `wider validation target`
    included."""
    from wider.recall.validation import NO_TARGET
    p = _drawn(tmp_path)
    p.config.protocol.recall.target = None
    p.save()
    estimate(p, "validation", "main", B=20)
    rid = _sampled(p)[0]
    lab = p.store.one("SELECT decision FROM labels WHERE record_id=? AND reviewer_id='r1'", (rid,))["decision"]
    label(p, "validation", rid, "r1", "exclude" if lab == "include" else "include",
          correction_reason="synthetic correction")
    d = dash.dashboard(p, "main")
    fields = _command_fields(d)
    assert d["curve"]["again_command"] in fields and d["reference_sets"]["all_command"] in fields
    assert any(not doc["exists"] and doc["command"] in fields for doc in d["documents"])
    html = html_of(d)
    folds = re.findall(r'<details class="cmdline"><summary>Command line</summary>(.*?)</details>', html)
    outside = re.sub(r'<details class="cmdline">.*?</details>', "", html)
    for cmd in fields:
        assert any(cmd.replace("<", "&lt;").replace(">", "&gt;") in f for f in folds), cmd
    assert '<details class="cmdline" open' not in html
    # outside the folds only the server's own sentences name a command
    assert NO_TARGET in text(outside)
    rest = text(outside).replace(NO_TARGET, "")
    # the correction of a sealed record adds the server's change line, which names `wider label revisions` (D6)
    change = sealed_change_line(p, "validation")
    assert change and change in rest
    assert not re.search(r"\bwider [a-z][a-z-]+", rest.replace(change, ""))


def test_blinded_review_line_once(tmp_path):
    from tests.unit.test_results_dashboard import _packages
    p = _drawn(tmp_path)
    assert "data-results-blinded" not in html_of(dash.dashboard(p, "main"))
    _packages(p)
    d = dash.dashboard(p, "main")
    html = html_of(d).replace("&#39;", "'")
    assert html.count(d["blinded_review"]["line"]) == 1
    assert f'<p data-results-blinded>{d["blinded_review"]["line"]}</p>' in html
    assert html.index("data-results-blinded") < html.index("data-results-title")


def test_adjudication_card_counts_then_imports(tmp_path):
    """Scenario "The page card" (partial-adjudication-import D13): the card "Import adjudicator's decisions" sits below
    Export records with "Adjudicator" beside its field and "Import" off; a chosen file is posted as a dry run with the
    shown set and the name, its counts and rows left out shown in normal text and the conflict in red; "Import" then
    posts the same file without the dry run, the page is read again at the new active set and the result's lines are
    shown."""
    p = _drawn(tmp_path)
    d = dash.dashboard(p, "main")
    html = html_of(d)
    card = html.index("<div data-adj-card>")
    assert html.index("<div data-export-card>") < card
    assert "<h4 id=\"import-adjudication\">Import adjudicator's decisions</h4>" in html
    assert "Into set 1 of main, the set shown." in text(html[card:])
    assert '<label class="inline">Adjudicator <input type="text" data-adj-field="adjudicator"' in html
    assert '<button class="secondary" data-adj-go disabled>Import</button>' in html
    assert "wider screen import-adjudication main FILE --adjudicator NAME --set 1 --dry-run" in html
    dry = {"counts": {"imported": 2},
           "lines": ["Dry run, nothing written: adj.csv: 2 rows used (1 changed, 1 confirmed), 3 blank rows "
                     "(those records keep their decision), 1 rows left out",
                     "row 5: a second row for record 9 says exclude; row 4 counts",
                     "Import would make set 2 of main active (adjudicated from set 1); set 1 stays unchanged."],
           "conflicts": [{"text": "row 5: a second row for record 9 says exclude; row 4 counts"}]}
    done = dict(dry, lines=["adj.csv: 2 rows used (1 changed, 1 confirmed)",
                            "row 5: a second row for record 9 says exclude; row 4 counts",
                            "Set 2 of main is now active (adjudicated from set 1: 1 changed, 1 confirmed). Set 1 is "
                            "unchanged; wider screen sets use main 1 makes it active again."])
    js = r"""
const calls = [], sent = [];
const mk = () => ({ innerHTML: "", value: "", disabled: true });
const parts = { protocol: mk(), set: mk(), sample: mk(), body: mk() };
let replies = [];
const api = async (path, opts) => {
  calls.push([path, (opts && opts.method) || "GET"]);
  if (opts && opts.body) { const f = opts.body.get("file"); sent.push({ file: f.name, size: f.size,
    fields: Object.fromEntries([...opts.body.entries()].filter(([k]) => k !== "file")) }); }
  const r = replies.shift(); if (typeof r === "string") throw new Error(r); return r; };
const c = Results.controller(parts, { api });
const els = {}; const el = (n) => els['[data-' + n + ']'];
""" + f"""
replies = [{json.dumps(d)}];
await c.load({{}}); parts.find = (s) => (els[s] = els[s] || mk());
await c.adjChoose("adjudicator", "Ana");
const before = calls.length;
const file = new File(["record_id,adjudication\\n1,include\\n"], "adj.csv", {{ type: "text/csv" }});
replies = [{json.dumps(dry)}];
await c.adjChoose("file", file);
const dryMsg = el("adj-msg").innerHTML, off = el("adj-go").disabled;
replies = [{json.dumps(done)}, {json.dumps(d)}];
await c.adjImport();
console.log(JSON.stringify({{ before, calls, sent, dryMsg, off, msg: el("adj-msg").innerHTML }}));
"""
    out = page(js)
    assert out["before"] == 1   # the name alone posts nothing: no file yet
    assert out["calls"][1:4] == [["/api/screen/adjudication", "POST"], ["/api/screen/adjudication", "POST"],
                                 ["/api/results?protocol=main&sample=validation", "GET"]]
    one, two = out["sent"]
    assert one["fields"] == {"protocol": "main", "result_set": "1", "adjudicator": "Ana", "column": "adjudication",
                             "dry_run": "true"}
    assert two["fields"] == dict(one["fields"], dry_run="false")
    assert one["file"] == two["file"] == "adj.csv" and one["size"] == two["size"] > 0
    assert out["off"] is False
    dm = out["dryMsg"]
    assert "<p>Dry run, nothing written: adj.csv: 2 rows used" in dm
    assert '<p class="bad">row 5: a second row for record 9 says exclude; row 4 counts</p>' in dm
    assert "small" not in dm and "muted" not in dm
    assert "<p>Set 2 of main is now active (adjudicated from set 1" in out["msg"]


def test_adjudication_card_keeps_only_the_newest_count(tmp_path):
    """ui-tester finding (bad-input user): when a count answers after a newer one, the older answer neither shows its
    lines nor turns "Import" on beside the newer refusal; typing in a field turns "Import" off until it is left."""
    p = _drawn(tmp_path)
    d = dash.dashboard(p, "main")
    ok = {"counts": {"imported": 2}, "lines": ["Dry run, nothing written: a.csv: 2 rows used"], "conflicts": []}
    js = r"""
const mk = () => ({ innerHTML: "", value: "", disabled: true });
const parts = { protocol: mk(), set: mk(), sample: mk(), body: mk() };
const els = {}; const el = (n) => els['[data-' + n + ']'];
let first = true, release;
const gate = new Promise((r) => { release = r; });
const api = async (path, opts) => {
  if (!opts || !opts.body) return """ + json.dumps(d) + r""";
  if (first) { first = false; await gate; return """ + json.dumps(ok) + r"""; }
  throw new Error("no row of b.csv has a usable decision in column adjudication; nothing was imported"); };
const c = Results.controller(parts, { api });
await c.load({}); parts.find = (s) => (els[s] = els[s] || mk());
await c.adjChoose("adjudicator", "Ana");
const slow = c.adjChoose("file", new File(["record_id,adjudication\n1,include\n"], "a.csv"));
await c.adjChoose("file", new File(["record_id,adjudication\n1,\n"], "b.csv"));
release(); await slow;
const raced = { msg: el("adj-msg").innerHTML, off: el("adj-go").disabled };
el("adj-go").disabled = false; c.adjEditing();
console.log(JSON.stringify({ raced, typing: el("adj-go").disabled }));
"""
    out = page(js)
    assert "no row of b.csv" in out["raced"]["msg"] and "a.csv" not in out["raced"]["msg"]
    assert out["raced"]["off"] is True and out["typing"] is True


def test_documents_out_of_date_in_red(proj):  # noqa: F811
    """A file written before the review's data last changed: the reason once above the list, a short mark beside the
    file, both in red (whole-app inspection, 1 Nov 2026); the recall headline in bold above the as-of line."""
    import os
    fill(proj, "main", COUNTED, rules())
    f = proj.path("exports") / "report.md"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("x", encoding="utf-8")
    os.utime(f, (1_600_000_000, 1_600_000_000))
    proj.store.execute("INSERT INTO history(at, author, kind, summary, items) "
                       "VALUES('2026-11-01T10:00:00+00:00', 'Tester A', 'label', 'x', '[]')")
    d = dash.dashboard(proj, "main")
    html = html_of(d)
    assert '<p class="warn-line" data-docs-stale>' in html
    assert text(html).replace("&quot;", '"').count(dash.STALE_LINE) == 1
    assert '<p class="warn-line" data-doc-stale>may be out of date; write it again with wider report</p>' in html
    d["recall"].update(state="current", sentence="", headline="Recall: 90.0%", as_of=None, lines=[])
    assert "<p data-recall-headline><strong>Recall: 90.0%</strong></p>" in html_of(d)
