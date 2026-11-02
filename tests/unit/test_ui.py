from fastapi.testclient import TestClient

from wider.core.project import Project
from wider.ui.app import create_app


def test_setup_page_and_patch(tmp_path):
    p = Project.init(tmp_path / "r", platforms=["ebsco", "scopus"])
    c = TestClient(create_app(p))
    assert c.get("/").status_code == 200
    d = c.get("/api/project").json()
    assert d["config"]["platforms"][0]["role"] == "development"
    assert all("api_key" not in k or k in ("api_key_env",) for pr in d["config"]["providers"] for k in pr)
    r = c.patch("/api/project", json={"questions": ["Q?"], "platforms": ["wos", "ebsco"], "title": "T"})
    assert r.status_code == 200
    d = c.get("/api/project").json()["config"]
    assert d["questions"] == ["Q?"] and d["platforms"][0]["key"] == "wos" and d["title"] == "T"
    assert c.patch("/api/project", json={"default_provider": "nope"}).status_code == 400
    assert c.get("/api/stages").json()["stages"][0]["key"] == "setup"
    assert c.post("/api/providers/nope/probe").status_code == 404
