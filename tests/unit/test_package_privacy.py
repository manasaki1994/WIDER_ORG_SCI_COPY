"""reviewer-roles-and-blinding, group 8: a review package and a decisions file leave the computer only as the
researcher sends them (privacy-and-data-policy). Synthetic records only."""
from __future__ import annotations

import importlib.util
import json
import os
import re
from pathlib import Path

import httpx

from tests.unit.test_package import _cli, _project
from tests.unit.test_package_page_node import run_page
from wider.core.project import PROJECT_GITIGNORE, Project
from wider.sample import package as pk

spec = importlib.util.spec_from_file_location(
    "guard", Path(__file__).resolve().parents[2] / "scripts" / "check_no_private_data.py")
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


def test_guard_refuses_review_packages(tmp_path, monkeypatch):
    p = _project(tmp_path)
    res = pk.make(p, "R3", ["validation"], "second", out=tmp_path / "r3.html")
    kept = Path(res["path"])
    assert any("review-packages" in x for x in guard.check(kept))
    outside = tmp_path / "work" / "r3.html"
    outside.parent.mkdir()
    outside.write_text((tmp_path / "r3.html").read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert any("review package page" in x for x in guard.check(Path("work/r3.html")))
    dec = tmp_path / "work" / f"wider-decisions-R3-{res['package_id']}.json"
    dec.write_text("{}", encoding="utf-8")
    assert any("decisions file" in x for x in guard.check(Path("work") / dec.name))
    t = tmp_path / "tests" / dec.name   # fixtures under tests/ are allowed
    t.parent.mkdir()
    t.write_text("{}", encoding="utf-8")
    assert guard.check(Path("tests") / dec.name) == []
    assert "review-packages/" in PROJECT_GITIGNORE.splitlines()
    fresh = Project.init(tmp_path / "new", platforms=["scopus"])
    assert "review-packages/" in (fresh.root / ".gitignore").read_text().splitlines()


def test_nothing_sent_nothing_local_inside(tmp_path, monkeypatch):
    sent = []

    def no_send(self, request, *a, **k):
        sent.append(str(request.url))
        raise AssertionError("a request was sent")
    monkeypatch.setattr(httpx.Client, "send", no_send)
    monkeypatch.setattr(httpx.AsyncClient, "send", no_send)
    monkeypatch.setenv("WIDER_SECRET_FOR_TEST", "s3cr3t-value-xyz")
    monkeypatch.setenv("USER", "loginname-xyz")
    p = _project(tmp_path)
    out = _cli(p, "blinding", "package", "R3", "--sample", "validation", "--mode", "second", "--out", str(tmp_path / "r3.html"))
    assert out.exit_code == 0, out.output
    res = run_page(tmp_path / "r3.html", """press("1"); press("j"); press("2"); $("#rp-export").click(); out({ file: lastExport() });""")
    dec = tmp_path / "dec.json"
    dec.write_text(json.dumps(res["file"]), encoding="utf-8")
    imp = _cli(p, "blinding", "import", str(dec))
    assert imp.exit_code == 0, imp.output
    pid = pk.status(p)["packages"][0]["package_id"]
    withdrawn = _cli(p, "blinding", "withdraw", pid, "--reason", "done")
    assert withdrawn.exit_code == 0 and sent == []
    home = os.path.expanduser("~")
    for f in (tmp_path / "r3.html", dec):
        text = f.read_text(encoding="utf-8")
        for bad in (str(p.root), str(tmp_path), home + "/", "loginname-xyz", "s3cr3t-value-xyz"):
            assert bad not in text, (f.name, bad)
        assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text)
    assert p.store.scalar("SELECT COUNT(*) FROM external_requests") == 0
