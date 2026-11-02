"""outputs.js under Node with a small fake DOM: a methods draft or package for a recall set waiting for the choice of
D8 shows the notice and the two choices, and a press repeats the call with that choice (validation-sample-planner)."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[2] / "wider" / "ui" / "static"
NODE = shutil.which("node")

HARNESS = r"""
const els = {};
function el(id) {
  if (!els[id]) els[id] = { id, value: "", innerHTML: "", onclick: null, addEventListener() {},
    querySelectorAll(sel) { return [...this.innerHTML.matchAll(/data-choice="([^"]+)"/g)].map(m => {
      const b = { dataset: { choice: m[1] }, onclick: null }; (this.buttons ||= []).push(b); return b; }); } };
  return els[id];
}
global.document = { querySelector: (s) => el(s.replace(/^#/, "")) };
global.window = { addEventListener() {} };
global.location = { hash: "" };
global.askConfirm = async () => true;
global.esc = (s) => String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const calls = [];
global.api = async (url) => {
  calls.push(url);
  const pending = !url.includes("choice=");
  if (url.includes("/methods/draft")) return pending ? { draft: "Pending.", choice_needed: true, notice: "Set 2 <changed>." } : { draft: "Done." };
  return pending ? { choice_needed: true, notice: "Set 2 <changed>." } : { path: "exports/p.zip", n_files: 3, size_bytes: 2048 };
};
el("rc-protocol").value = "main";
"""


def test_pending_choice_on_methods_and_package():
    if not NODE:
        pytest.skip("node is not installed")
    code = HARNESS + (STATIC / "outputs.js").read_text(encoding="utf-8") + r"""
(async () => {
  const out = {};
  for (const [btn, msg] of [["out-methods-draft", "out-methods-msg"], ["out-package", "out-package-msg"]]) {
    await els[btn].onclick();
    out[btn + "_notice"] = els[msg].innerHTML;
    await els[msg].buttons.find(b => b.dataset.choice === "not_informed").onclick();
    await new Promise(r => setTimeout(r, 0));
    out[btn + "_after"] = els[msg].innerHTML;
  }
  out.calls = calls; out.draft = els["out-methods-text"].value;
  process.stdout.write(JSON.stringify(out));
})();
"""
    run = subprocess.run([NODE, "-"], input=code, capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stderr
    r = json.loads(run.stdout)
    for btn in ("out-methods-draft", "out-package"):
        assert r[btn + "_notice"].startswith("<p>Set 2 &lt;changed&gt;.</p>")
        assert 'data-choice="used">Sample used<' in r[btn + "_notice"] and 'data-choice="not_informed">Not informed<' in r[btn + "_notice"]
    assert "draft from the project's facts" in r["out-methods-draft_after"] and r["draft"] == "Done."
    assert "3 files" in r["out-package_after"]
    assert [("choice=not_informed" in c) for c in r["calls"]] == [False, True, False, True]
