"""The plan card and the "Recall target and rule" section (validation.js, change validation-sample-planner task 2.5),
run under Node with a fake api. Synthetic plan output; no project data."""
from __future__ import annotations

import json

from tests.unit.test_backends_js_node import ESC, STATIC, run_node

PLAN = {"recommended": "neyman", "lines": ["frame 2000 records", "target 0.9", "plan_power 0.8"],
        "rows": [{"rule": r, "rates": rt, "allocation": {"S1": a, "S2": 300 - a}, "expected_relevant": 12.5,
                  "p_interval": 0.9, "margin": {"median": 0.05, "p10": 0.03, "p90": 0.08}, "p_clear": 0.7,
                  **({"line": "if recall is 0.95: a clear answer in 70%"} if r == "neyman" and rt == "middle" else {})}
                 for r, a in (("proportional", 100), ("neyman", 180)) for rt in ("low", "middle", "high")],
        "search": None}


def card(body: str) -> dict:
    code = (STATIC / "validation.js").read_text(encoding="utf-8")
    fake = f"""
const calls = [];
const el = () => ({{ innerHTML: "", textContent: "", hidden: true }});
const parts = {{ table: el(), msg: el(), depth: el(), targetMsg: el() }};
const api = async (path, opts) => {{ const b = JSON.parse(opts.body); calls.push({{ path, body: b }});
  if (path === "/api/validation/target") return {{ status: "Target 0.9 set before any result." }};
  return b.apply ? {{ ...{json.dumps(PLAN)}, applied: {{ lines: ["protocol.sampling.bands = S1 180, S2 120 (plan " + b.apply + ")"] }} }} : {json.dumps(PLAN)}; }};
const c = Validation.controller(parts, {{ api, author: () => "ana" }});
"""
    return run_node(f"{ESC}\n{code}\n{fake}\n{body}")


def test_plan_card_table():
    out = card("""
c.set("budget", "300"); c.set("precision", ""); c.set("rates", "S1=0.2,S2=0.01"); c.set("percentiles", "1,5");
await c.plan();
const html = parts.table.innerHTML;
const buttons = [...html.matchAll(/data-val-apply="([^"]+)"/g)].map(m => m[1]);
const sens = [...html.matchAll(/data-plan-sens="([^"]+)"/g)].map(m => m[1]);
await c.use("neyman");
console.log(JSON.stringify({ html, buttons, sens, calls, msg: parts.msg.textContent }));
""")
    assert out["buttons"] == ["proportional", "neyman"]          # one button per allocation row
    assert out["sens"] == ["proportional", "proportional", "neyman", "neyman"]   # low and high rates beneath each
    assert "S1 180, S2 120" in out["html"] and "<strong>recommended</strong>" in out["html"]
    assert "if recall is 0.95: a clear answer in 70%" in out["html"] and "0.050 (0.030 to 0.080)" in out["html"]
    assert "muted" not in out["html"]                              # decisions are not grey
    first, second = out["calls"]
    assert first["path"] == second["path"] == "/api/validation/plan"
    assert first["body"] == {"budget": 300, "precision": None, "rates": "S1=0.2,S2=0.01", "expected_recall": None,
                             "percentiles": "1,5", "allocation": None}
    assert second["body"]["apply"] == "neyman" and second["body"]["author"] == "ana" and second["body"]["budget"] == 300
    assert "(plan neyman)" in out["msg"]


def test_target_section_sends_target():
    out = card("""
c.setTarget("target", "0.9"); c.setTarget("rule", "top-strata"); const shown = !parts.depth.hidden;
c.setTarget("depth", "400"); c.setTarget("precision", "0.05");
await c.saveTarget();
c.setTarget("rule", "revise-prompt"); const hidden = parts.depth.hidden;
await c.saveTarget();
console.log(JSON.stringify({ calls, shown, hidden, msg: parts.targetMsg.textContent }));
""")
    a, b = out["calls"]
    assert a == {"path": "/api/validation/target",
                 "body": {"target": 0.9, "rule": "top-strata", "depth": 400, "precision": 0.05}}
    assert b["body"]["rule"] == "revise-prompt" and b["body"]["depth"] is None
    assert out["shown"] and out["hidden"] and out["msg"] == "Target 0.9 set before any result."
