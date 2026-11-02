from typer.testing import CliRunner

from wider.cli import app

runner = CliRunner()


def test_version():
    r = runner.invoke(app, ["--version"])
    assert r.exit_code == 0 and "wider" in r.stdout


def test_init_and_status(tmp_path):
    r = runner.invoke(app, ["init", str(tmp_path / "rev"), "-q", "Q1?", "-p", "ebsco", "-p", "scopus"])
    assert r.exit_code == 0, r.stdout
    r = runner.invoke(app, ["status", "--dir", str(tmp_path / "rev")])
    assert r.exit_code == 0, r.stdout
    assert "benchmark: 0 papers" in r.stdout
    r = runner.invoke(app, ["init", str(tmp_path / "rev")])
    assert r.exit_code == 1


NEW_TITLES = ["Review plan", "Concepts and synonyms", "Search strings", "Search results", "Deduplication",
              "Missing abstracts", "Ranking", "Samples and labelling", "Screening", "Recall and report"]


def test_stages():
    """`wider stages` prints the page titles in order with their section; the stages not built yet are "later"."""
    r = runner.invoke(app, ["stages"], env={"COLUMNS": "200"})
    assert r.exit_code == 0 and "construct" in r.stdout
    rows = [line for line in r.stdout.splitlines() if "│" in line]
    cells = [[c.strip() for c in line.strip("│ ").split("│")] for line in rows]
    available = [c for c in cells if c[-1] == "available"]
    assert [c[1] for c in available] == NEW_TITLES
    assert [c[0] for c in available] == ["setup", "construct", "query", "import", "dedup", "enrich", "rank", "sample",
                                        "screen", "recall"]
    assert [c[2] for c in available] == ["1 Literature search"] * 4 + ["2 Data cleaning"] * 2 + \
        ["3 Model tuning and validation"] * 2 + ["4 Screening and outputs"] * 2
    later = {c[0]: c[2] for c in cells if c[-1] == "extension"}
    assert later == {"fulltext": "later", "extract": "later", "synthesis": "later"}



def test_stages_lists_each_stage_s_commands(tmp_path):
    """accessibility-and-naming 6.1: `wider stages` lists every command of a page (setup: init, plan, benchmark, protocol,
    providers; sample: sample, label; screen: screen, review), a stage without `commands:` lists its one `command`,
    and every listed command exists in the CLI."""
    import yaml

    from wider.core.stages import load_stages

    r = runner.invoke(app, ["stages"], env={"COLUMNS": "200"})
    assert r.exit_code == 0
    cells = {c[0]: c for c in ([x.strip() for x in line.strip("│ ").split("│")] for line in r.stdout.splitlines() if "│" in line)}
    assert cells["setup"][3] == "init, plan, benchmark, protocol, providers"
    assert cells["sample"][3] == "sample, label" and cells["screen"][3] == "screen, review"
    assert cells["construct"][3] == "construct" and cells["recall"][3] == "recall"
    import typer.main
    names = set(typer.main.get_command(app).commands)
    for st in load_stages():
        if st["status"] == "available":
            assert set(st["commands"]) <= names, st["key"]
    # the default: a registry entry without `commands:` gets [command]
    f = tmp_path / "stages.yaml"
    f.write_text(yaml.safe_dump({"sections": [{"number": 1, "title": "S"}],
                                 "stages": [{"key": "a", "title": "A", "section": 1, "command": "alpha", "status": "available"}]}))
    assert load_stages(f)[0]["commands"] == ["alpha"]

def test_cost_and_check_help_current():
    from typer.testing import CliRunner

    from wider.cli import app as cli_app

    r = CliRunner().invoke(cli_app, ["costs", "--help"])
    assert r.exit_code == 0
    assert "zero for local backends" not in r.output and "not priced" in " ".join(r.output.split())
    r = CliRunner().invoke(cli_app, ["check-backends", "--help"])
    assert r.exit_code == 0
    flat = " ".join(r.output.split())
    assert "Probe the default LLM backend" not in flat and "stage override" in flat


def test_number_options_refuse_in_plain_words(tmp_path):
    """manual-review D8: a fraction, a word or a negative number on the command line is refused with the page's own
    sentence and exit code 1, never the parser's "Invalid value" / "No such option"; nothing is run or saved."""
    root = tmp_path / "rev"
    assert runner.invoke(app, ["init", str(root), "-q", "Q1?", "-p", "ebsco"]).exit_code == 0
    yaml = (root / "project.yaml").read_text(encoding="utf-8")
    cases = [(["rank", "run", "--top-n", "2.5"], "top-N must be a whole number from 1 to the number of cells"),
             (["rank", "run", "--top-n", "abc"], "top-N must be a whole number from 1 to the number of cells"),
             (["screen", "run", "corpus", "--limit", "2.5"], "the record limit must be a whole number"),
             (["screen", "protocol", "corpus", "--limit", "abc"], "the record limit must be a whole number"),
             (["screen", "second", "--limit", "-3"], "the record limit must be at least 1"),
             (["benchmark", "split", "--dev-share", "abc"], "the development share must be a number from 0 to 1"),
             (["protocol", "set", "screening.max_tokens", "-5"], "screening.max_tokens was not changed: input should be greater than or equal to 200")]
    for args, said in cases:
        r = runner.invoke(app, [*args, "--dir", str(root)], env={"COLUMNS": "200"})
        out = " ".join(r.output.split())
        assert r.exit_code == 1 and said in out, (args, r.exit_code, r.output)
        assert not any(w in r.output for w in ("Invalid value", "is not a valid", "No such option")), (args, r.output)
    assert (root / "project.yaml").read_text(encoding="utf-8") == yaml
