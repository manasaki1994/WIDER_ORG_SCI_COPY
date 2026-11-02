"""Leaving the Label records focus page with Esc puts the focus back on the row of the record just viewed, found again
in the re-rendered queue: with "not labelled yet", the labelled records drop out and the rows renumber, so the row at
the drawer's old place is another record (owner check, 1 Nov 2026). Synthetic titles only."""
from tests.unit.a11y_dom import run_dom, static
from tests.unit.test_focus_page_node import PRE, _drawer_html

RENDER = """
  const rows = [1, 2, 3, 4, 5, 6].map((id) => ({ id, title: "Synthetic tillage record " + id, decision: "" }));
  let view = rows.slice();
  const draw = () => { view = rows.filter((r) => !r.decision);
    $("#queue").innerHTML = view.map((r, i) => `<tr data-idx="${i}" tabindex="0"><td>${r.title}</td></tr>`).join(""); };
"""


def test_esc_returns_to_the_viewed_record_after_the_queue_renumbers():
    r = run_dom("""
      draw();
      rows[0].decision = "include"; rows[1].decision = "exclude";   // labelled earlier in the sitting
      const back = (r) => { const k = rows.indexOf(r), j = view.findIndex((x) => rows.indexOf(x) >= k); return j < 0 ? null : $(`#queue tr[data-idx="${j}"]`); };
      Focus.open({ label: "Labelling queue (blind)", table: "#queue", items: view, index: 3, notes: false, blind: true,
        render: async (r, u) => u.recordBlock(r), onClose: draw, backTo: back, full: FULL });
      for (let i = 0; i < 4; i++) await flush();
      key("Escape"); await flush();
      const a = document.activeElement;
      out({ title: a && a.textContent, idx: a && a.dataset && a.dataset.idx });
    """, html='<table><tbody id="queue"></tbody></table>' + _drawer_html(), scripts=("common.js", "drawer.js", "focus.js"),
        pre=PRE + RENDER)
    # record 4 was shown (place 3 of the old list); after the re-render it is the second row, not the fourth
    assert r == {"title": "Synthetic tillage record 4", "idx": "1"}


def test_records_js_hands_the_queue_its_way_back():
    js = static("records.js")
    assert "backTo: (r) =>" in js and "lb.view.findIndex(x => lb.rows.indexOf(x) >= k)" in js
