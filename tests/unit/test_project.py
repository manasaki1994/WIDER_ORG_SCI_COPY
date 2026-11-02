
import threading

import pytest
import yaml

from wider.core.models import BenchmarkPaper
from wider.core.project import PROJECT_FILE, Project


def test_init_creates_layout_and_config(tmp_path):
    root = tmp_path / "myreview"
    Project.init(root, title="Test review", questions=["Does X affect Y?"],
                     platforms=["ebsco", "scopus", "wos"])
    assert (root / PROJECT_FILE).exists()
    assert (root / "benchmark.csv").exists()
    assert (root / ".gitignore").exists()
    assert "assistant/" in (root / ".gitignore").read_text().splitlines()
    assert (root / "store.sqlite").exists()
    for d in ["inputs/ris", "artifacts/anchors", "runs", "exports", "embeddings"]:
        assert (root / d).is_dir()
    cfg = yaml.safe_load((root / PROJECT_FILE).read_text())
    assert cfg["name"] == "myreview"
    assert cfg["questions"] == ["Does X affect Y?"]
    roles = [(x["key"], x["role"]) for x in cfg["platforms"]]
    assert roles == [("ebsco", "development"), ("scopus", "validation"), ("wos", "validation")]
    assert not any("api_key" in k and v and k != "api_key_env" for pr in cfg["providers"] for k, v in pr.items())


def test_init_refuses_existing_without_force(tmp_path):
    Project.init(tmp_path / "r")
    with pytest.raises(FileExistsError):
        Project.init(tmp_path / "r")
    Project.init(tmp_path / "r", exist_ok=True)


def test_load_find_and_benchmark_roundtrip(tmp_path):
    root = tmp_path / "r"
    Project.init(root)
    sub = root / "inputs" / "ris"
    p = Project.find(sub)
    assert p.root == root.resolve()
    papers = [
        BenchmarkPaper(benchmark_id="B001", title="One", doi="10.1000/one", set="Development", year=2001),
        BenchmarkPaper(benchmark_id="B002", title="Two", set="Validation"),
    ]
    p.write_benchmark(papers)
    back = Project.load(root).read_benchmark()
    assert [b.benchmark_id for b in back] == ["B001", "B002"]
    assert back[0].doi == "10.1000/one" and back[0].year == 2001
    assert back[1].set == "Validation"


def test_benchmark_tolerates_loose_headers(tmp_path):
    root = tmp_path / "r"
    p = Project.init(root)
    (root / "benchmark.csv").write_text(
        "Title,DOI,Set\nA paper,https://doi.org/10.9999/X,dev\nAnother,,validation\n", encoding="utf-8")
    back = p.read_benchmark()
    assert back[0].doi == "10.9999/x" and back[0].set == "Development" and back[0].benchmark_id == "B001"
    assert back[1].set == "Validation"


def test_provider_and_embedding_lookup(tmp_path):
    p = Project.init(tmp_path / "r")
    assert p.provider().name == "ollama"
    assert p.embedding().model == "BAAI/bge-m3"
    with pytest.raises(KeyError):
        p.provider("nope")


def test_save_is_atomic_for_a_concurrent_reader(tmp_path):
    """A page request reading project.yaml while a background job saves it never sees an empty file."""
    p = Project.init(tmp_path / "proj", "atomic")
    stop = threading.Event()

    def saver():
        while not stop.is_set():
            p.save()

    t = threading.Thread(target=saver)
    t.start()
    try:
        for _ in range(300):
            assert Project.load(p.root).config.name == "atomic"
    finally:
        stop.set()
        t.join()
    assert not list(p.root.glob(".project.yaml.*.tmp"))
