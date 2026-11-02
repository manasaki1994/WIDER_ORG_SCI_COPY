"""The guide panel's "Show" links (shell.js showButton, guide-assistant D13, task 1.3), under Node on the small DOM of
``a11y_dom`` with the whole page body: a control is scrolled to, outlined and focused but never clicked; a heading is
outlined and not focused; a name that is not on the page gives null and the panel says so. No project, no network."""
from __future__ import annotations

from tests.unit.a11y_dom import run_dom
from tests.unit.test_a11y_node import BODY, SHELL_PAGE_PRE

SCRIPTS = ("common.js", "jobs.js", "backends.js", "shell.js")
# a stand-in for the top bar's main button and a page heading on Deduplication, plus a hidden twin of the button
EXTRA = """
  currentPage = "dedup"; $("#page-dedup").hidden = false;
  const page = $("#page-dedup");
  page.insertAdjacentHTML("beforeend", '<button type="button" id="t-run">Run deduplication</button><h3 id="t-head">Pairs to check</h3>');
  let clicks = 0; $("#t-run").addEventListener("click", () => { clicks++; });
"""


def _run(body: str) -> dict:
    return run_dom(EXTRA + body, html=BODY, scripts=SCRIPTS, pre=SHELL_PAGE_PRE)


def test_guide_show_focuses_never_clicks():
    """Show on a control scrolls it to the middle, outlines it and moves the focus to it; it is never pressed."""
    r = _run("""
      const got = await showButton("dedup", "Run deduplication");
      const b = $("#t-run");
      out({ got, scrolled: b.scrolled, outlined: b.classList.contains("guide-shown"), focused: document.activeElement === b, clicks });
    """)
    assert r == {"got": "control", "scrolled": [{"block": "center"}], "outlined": True, "focused": True, "clicks": 0}


def test_guide_show_heading_outlined_not_focused():
    """A name that matches only a heading outlines the heading and leaves the focus where it was."""
    r = _run("""
      const before = document.activeElement;
      const got = await showButton("dedup", "Pairs to check");
      const t = $("#t-head");
      out({ got, scrolled: t.scrolled.length, outlined: t.classList.contains("guide-shown"), kept: document.activeElement === before });
    """)
    assert r == {"got": "text", "scrolled": 1, "outlined": True, "kept": True}


def test_guide_show_missing_button():
    """A hidden control counts as missing: showButton gives null, and the panel's Show link writes
    "<name> is not on the page now." in its answer, without pressing anything."""
    r = _run("""
      $("#t-run").hidden = true;
      const got = await showButton("dedup", "Run deduplication");
      const panel = $("#guide-panel"); panel.hidden = false;
      panel.innerHTML = '<dl><div class="gp"><dt>What to do</dt><dd>Run it.<p class="guide-show">Show: '
        + '<button type="button" class="link-btn" data-show="Run deduplication">Run deduplication</button></p>'
        + '<p class="guide-show-note" role="status"></p></dd></div></dl>';
      panel.querySelector("[data-show]").click();
      for (let i = 0; i < 6; i++) await flush();
      out({ got, note: panel.querySelector(".guide-show-note").textContent, clicks, outlined: $("#t-run").classList.contains("guide-shown") });
    """)
    assert r == {"got": None, "note": "Run deduplication is not on the page now.", "clicks": 0, "outlined": False}


def test_guide_show_card_link_by_its_title():
    """ui-tester F1 (31 Oct 2026): a plan card's link is named by its whole text (title, state lines); Show finds it by
    its title line, outlines and focuses it, and never presses it. A control named exactly still wins."""
    r = _run("""
      page.insertAdjacentHTML("beforeend", '<a href="#setup?task=seeds" id="t-card"><span class="pc-title">Seed papers</span>'
        + '<span class="pc-line">6 seed papers</span><span class="pc-state">Done</span></a>');
      let cardClicks = 0; $("#t-card").addEventListener("click", () => { cardClicks++; });
      const got = await showButton("dedup", "Seed papers");
      const a = $("#t-card");
      const card = { got, outlined: a.classList.contains("guide-shown"), focused: document.activeElement === a, cardClicks };
      page.insertAdjacentHTML("beforeend", '<button type="button" id="t-exact">Seed papers</button>');
      await showButton("dedup", "Seed papers");
      out({ card, exact: document.activeElement === $("#t-exact") });
    """)
    assert r == {"card": {"got": "control", "outlined": True, "focused": True, "cardClicks": 0}, "exact": True}
