"""Database catalogue: catalogue fields, the project's ordered choice, roles and custom platforms that reuse a syntax family."""
import pytest

from wider.core.project import Project
from wider.search import catalogue
from wider.search.ir import Block, Query
from wider.search.render import RENDERERS, platforms, render


def test_catalogue_has_descriptions_and_keeps_renderer_keys():
    cat = {c["key"]: c for c in catalogue.catalogue()}
    assert set(cat) == set(platforms()) and set(RENDERERS) <= set(cat)
    for c in cat.values():
        assert c["label"] and c["description"] and c["coverage"] and isinstance(c["export_formats"], list)
        assert c["export_cap"] is None or isinstance(c["export_cap"], int)
        assert c["cap_note"] and c["paste"]
    # the keys the renderers depended on before the catalogue fields were added are still present
    # (2a moved Scopus's field_wrap into field_codes, task 2.3; the rendered string is unchanged and pinned)
    assert platforms()["scopus"]["field_codes"]["title,abstract"]["code"] == "TITLE-ABS"
    assert platforms()["ovid"]["truncation"] == "$"


def _query():
    return Query(blocks=[Block(key="a", terms=["alpha", "beta gamma"]), Block(key="b", terms=["delta*"])],
                 logic={"and": ["a", "b"]})


def test_project_platforms_order_role_custom(tmp_path):
    p = Project.init(tmp_path / "r", platforms=["ebsco", "scopus"])
    rows = catalogue.project_platforms(p)
    assert [(r["key"], r["role"], r["custom"]) for r in rows] == [("ebsco", "development", False), ("scopus", "validation", False)]
    assert rows[0]["export_cap"] == 200 and rows[0]["style"] == "ebsco"
    with pytest.raises(ValueError):
        catalogue.add_platform(p, "library_host")  # not in the catalogue, no syntax family
    with pytest.raises(ValueError):
        catalogue.add_platform(p, "library_host", syntax="nosuch")
    pl = catalogue.add_platform(p, "Library Host", label="Our library host", syntax="ebsco")
    assert pl.key == "library_host" and pl.role == "validation" and pl.syntax == "ebsco"
    catalogue.add_platform(p, "wos")
    p = Project.load(tmp_path / "r")
    assert [x.key for x in p.config.platforms] == ["ebsco", "scopus", "library_host", "wos"]
    custom = catalogue.project_platforms(p)[2]
    assert custom["custom"] is True and custom["label"] == "Our library host" and custom["style"] == "ebsco"
    # rendering only the chosen platforms; the custom one renders with the ebsco family
    out = catalogue.render_project(p, _query())
    assert set(out) == {"ebsco", "scopus", "library_host", "wos"}
    assert out["library_host"] == render(_query(), "ebsco") and "TITLE-ABS" in out["scopus"]
    assert "pubmed" not in out
    # reorder and roles
    catalogue.order_platforms(p, ["wos", "library_host"])
    assert [x.key for x in Project.load(tmp_path / "r").config.platforms] == ["wos", "library_host", "ebsco", "scopus"]
    with pytest.raises(KeyError):
        catalogue.order_platforms(p, ["wos", "nope"])
    catalogue.set_role(p, "scopus", "development")
    roles = {x.key: x.role for x in Project.load(tmp_path / "r").config.platforms}
    assert roles == {"wos": "validation", "library_host": "validation", "ebsco": "validation", "scopus": "development"}
    catalogue.set_role(p, "scopus", "validation")  # never zero development platforms: the first one takes over
    roles = {x.key: x.role for x in Project.load(tmp_path / "r").config.platforms}
    assert roles["wos"] == "development" and roles["scopus"] == "validation"
    with pytest.raises(ValueError):
        catalogue.set_role(p, "wos", "primary")
    catalogue.remove_platform(p, "wos")
    p = Project.load(tmp_path / "r")
    assert [(x.key, x.role) for x in p.config.platforms][0] == ("library_host", "development")
    with pytest.raises(KeyError):
        catalogue.remove_platform(p, "wos")


def test_style_for_custom_without_syntax_raises(tmp_path):
    p = Project.init(tmp_path / "r", platforms=["ebsco"])
    from wider.core.models import Platform

    p.config.platforms.append(Platform(key="mystery", label="Mystery", role="validation"))
    assert catalogue.project_platforms(p)[1]["style"] is None
    with pytest.raises(ValueError):
        catalogue.render_project(p, _query())


def test_init_help_and_labels_come_from_the_catalogue(tmp_path):
    from typer.testing import CliRunner

    from wider.cli import app

    r = CliRunner().invoke(app, ["init", "--help"], env={"COLUMNS": "200"})
    text = " ".join(r.output.split())
    for key in ("scopus", "wos", "ebsco", "proquest", "ovid", "pubmed", "gscholar", "citation_chain", *platforms()):
        assert key in text, key
    assert "the first is the development platform" in text
    p = Project.init(tmp_path / "r", platforms=["Scopus", "citation_chain"])
    assert [(x.key, x.label, x.role) for x in p.config.platforms] == [
        ("scopus", platforms()["scopus"]["label"], "development"), ("citation_chain", "Citation chain (prior reviews via OpenAlex)", "validation")]
    with pytest.raises(ValueError, match="unknown platform nosuch"):
        Project.init(tmp_path / "x", platforms=["scopus", "nosuch"])
    assert not (tmp_path / "x").exists(), "nothing is created for an unknown key"
    r = CliRunner().invoke(app, ["init", str(tmp_path / "y"), "-p", "nosuch"])
    assert r.exit_code == 1 and "unknown platform nosuch" in r.output and not (tmp_path / "y").exists()
