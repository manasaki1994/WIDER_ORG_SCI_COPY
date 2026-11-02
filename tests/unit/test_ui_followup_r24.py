"""Tests asked for by the twenty-fourth round of C1 checks (spec-checker): Up on a platform redraws the table with
that platform moved, and an unknown default embedding is refused without saving anything of the same request."""
import yaml
from fastapi.testclient import TestClient

from tests.unit.test_drawer_keys_node import STATIC, run_node
from wider.core.project import Project
from wider.ui.app import create_app

SETUP = (STATIC / "setup.js").read_text(encoding="utf-8")


def test_up_on_the_second_platform_puts_it_first():
    handler = SETUP[SETUP.index('box.querySelectorAll("[data-move]")'):]
    handler = handler[:handler.index("}, msg));") + 9]
    out = run_node(r"""
const d = { chosen: [{ key: "scopus" }, { key: "wos" }, { key: "ebsco" }] };
const posts = [], focus = [];
const post = async (path, body) => { posts.push([path, body]); };
const loadDatabases = async () => { d.chosen = posts[posts.length - 1][1].keys.map((key) => ({ key })); };
const focusMoved = (box, key, dir) => focus.push([key, dir]);
const wrap = (fn) => fn, msg = null, q = () => null;
const btn = (move, dir) => ({ dataset: { move, dir } });
const buttons = [btn("scopus", "-1"), btn("wos", "-1"), btn("ebsco", "1")];   // the rows as first drawn
const box = { querySelectorAll: () => buttons };
""" + handler + r"""
(async () => {
  await buttons[1].onclick();                      // Up on wos
  const drawn = d.chosen.map((c) => c.key);
  await buttons[1].onclick();                      // Up on wos, now first: nothing is sent
  await buttons[2].onclick();                      // Down on the last one: nothing is sent
  console.log(JSON.stringify({ posts, drawn, focus }));
})();
""")
    assert out["posts"] == [["/api/platforms/order", {"keys": ["wos", "scopus", "ebsco"]}]]
    assert out["drawn"] == ["wos", "scopus", "ebsco"] and out["focus"] == [["wos", "-1"]]


def test_an_unknown_default_embedding_is_refused_unsaved(tmp_path):
    p = Project.init(tmp_path / "proj", title="T", questions=["Q?"], platforms=["scopus"])
    c = TestClient(create_app(p))
    before = (p.root / "project.yaml").read_text(encoding="utf-8")
    for field in ("default_embedding", "default_provider"):
        r = c.patch("/api/project", json={"title": "changed", "questions": ["other"], field: "nope"})
        assert r.status_code == 400, field
        assert (p.root / "project.yaml").read_text(encoding="utf-8") == before   # nothing of the request is kept
    assert yaml.safe_load(before)["title"] == "T" and c.get("/api/project").json()["config"]["title"] == "T"
