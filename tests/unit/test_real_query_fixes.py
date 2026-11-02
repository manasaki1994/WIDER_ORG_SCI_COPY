"""Slips found while building real queries with a strong model (31 Oct 2026): a proximity group written as JSON
text, an unquoted multi-word term, a query too long for one OpenAlex request, and Web of Science strings the import
refused. Synthetic, topic-neutral terms only."""
import json

import pytest

from wider.construct import artifacts
from wider.construct.propose import ProposalError, propose
from wider.core.models import BenchmarkPaper, ProviderConfig
from wider.core.project import Project
from wider.search.ir import Near, Query, tidy_terms
from wider.search.lint import lint
from wider.search.parse_boolean import parse
from wider.search.render import render
from wider.search.render.openalex import render_openalex
from wider.search.string_check import check_string

NEAR_TEXT = json.dumps({"near": [["team*", "crew*"], ["drift*", "lag*"]], "distance": 3})
SLIPPED = {"blocks": [{"key": "pace", "name": "Pace", "terms": ["tempo*", NEAR_TEXT, "slow part*"]},
                      {"key": "unit", "name": "Unit", "terms": ["workgroup*", '"work unit*"']}],
           "logic": {"and": ["pace", "unit"]}}


# ----------------------------------------------------------------- 1 and 4: tidy the proposed terms
def test_tidy_terms_decodes_json_near_and_quotes_phrases():
    data, fixes = tidy_terms(SLIPPED)
    terms = data["blocks"][0]["terms"]
    assert terms[1] == {"near": [["team*", "crew*"], ["drift*", "lag*"]], "distance": 3}
    assert terms[2] == '"slow part*"'
    assert len(fixes) == 2 and "JSON text" in fixes[0] and '"slow part*"' in fixes[1]
    assert SLIPPED["blocks"][0]["terms"][1] == NEAR_TEXT   # the input is not changed
    assert not [f for f in lint(Query.model_validate(data)) if f["level"] == "error"]


def test_tidy_terms_refuses_a_broken_json_group_clearly():
    bad = {"blocks": [{"key": "pace", "name": "Pace", "terms": ['{"near": ["only one"], "distance": 3}']}],
           "logic": "pace"}
    with pytest.raises(ValueError, match="proximity group written as JSON text, and not a valid one"):
        tidy_terms(bad)


def test_saved_json_near_text_reads_as_a_group_and_renders():
    q = Query.model_validate(SLIPPED)   # a version saved before the fix
    assert isinstance(q.blocks[0].terms[1], Near)
    for s in (render(q, "ebsco"), render(q, "wos"), render_openalex(q)):
        assert '{"near"' not in s and "team*" in s and "drift*" in s


def _fake(monkeypatch, answer):
    from wider.providers import fake as fakemod

    orig = fakemod.FakeProvider.__init__

    def patched(self, cfg, responder_=None):
        orig(self, cfg, lambda req: answer)

    monkeypatch.setattr(fakemod.FakeProvider, "__init__", patched)


def _project(tmp_path):
    p = Project.init(tmp_path / "r", questions=["How do work groups keep pace?"])
    p.config.providers.append(ProviderConfig(name="fake", kind="fake", model="fake"))
    p.save()
    p.write_benchmark([BenchmarkPaper(benchmark_id="B1", title="Paper one", doi="10.1000/one", set="Development",
                                      abstract=" ".join(["w"] * 30))])
    return p


def test_propose_synonyms_fixes_and_records_the_slips(tmp_path, monkeypatch):
    p = _project(tmp_path)
    _fake(monkeypatch, SLIPPED)
    row = propose(p, "synonyms", provider_name="fake")
    _, data = artifacts.load(p, "synonyms", row["version"])
    assert data["blocks"][0]["terms"][1]["near"] == [["team*", "crew*"], ["drift*", "lag*"]]
    assert data["blocks"][0]["terms"][2] == '"slow part*"'
    assert len(row["fixes"]) == 2 and "WIDER fixed 2 term(s)" in row["note"]
    assert (p.root / row["run_dir"] / "fixes.txt").read_text().count("\n") == 2


def test_propose_synonyms_refuses_a_broken_json_group(tmp_path, monkeypatch):
    p = _project(tmp_path)
    bad = {"blocks": [{"key": "pace", "name": "Pace", "terms": ['{"near": ["one"]}', "tempo*"]}], "logic": "pace"}
    _fake(monkeypatch, bad)
    with pytest.raises(ProposalError, match="not a valid one"):
        propose(p, "synonyms", provider_name="fake")
    assert artifacts.latest(p, "synonyms") is None


# ----------------------------------------------------------------- 2: a query too long for one OpenAlex request
def _long_project(tmp_path, monkeypatch, calls, limit=8190):
    """A synonyms version 2 whose OpenAlex request is about 11,000 characters (as a strong model writes). The mock
    OpenAlex refuses a URL over ``limit`` bytes as OpenAlex does, counts 10 works per term of the "work" block sent,
    and finds the seed DOIs dev1 and dev2."""
    import httpx

    from tests.unit.helpers import mock_client
    from tests.unit.test_search_provenance import _oa_proj

    def works(request):
        if len(str(request.url).encode()) > limit:
            return httpx.Response(400, json={"error": "Request URL too long", "message": "Request URL too long"})
        flt = request.url.params.get("filter", "")
        if flt.startswith("doi:"):
            asked = flt[4:].split(",", 1)[0].split("|")
            return httpx.Response(200, json={"meta": {"count": 1}, "results": [
                {"doi": f"https://doi.org/{d}"} for d in asked if d in ("10.1000/dev1", "10.1000/dev2")][:1]})
        return httpx.Response(200, json={"meta": {"count": 10 * flt.count("labour")}, "results": []})

    p = _oa_proj(tmp_path, monkeypatch, calls)
    monkeypatch.setattr("wider.enrich.sources._client", lambda *a, **k: mock_client(works, calls))
    work = [f'"labour form{i:03d} pattern*"' for i in range(380)]
    artifacts.save_new_version(p, "synonyms", {"blocks": [
        {"key": "work", "name": "Work", "terms": work},
        {"key": "place", "name": "Place", "terms": ["office*", "site*", '"shop floor"']}],
        "logic": {"and": ["work", "place"]}}, origin="manual")
    return p


def test_long_query_is_sent_in_parts_and_gives_a_range(tmp_path, monkeypatch):
    from tests.unit.test_dry_run import _cli
    from wider.search.render.openalex import rendering
    from wider.search.term_yield import URL_LIMIT, query_parts, url_bytes

    calls: list = []
    p = _long_project(tmp_path, monkeypatch, calls)
    assert _cli(p, "lookups", "terms", "allow").exit_code == 0
    _, data = artifacts.load(p, "synonyms", 2)
    assert len(rendering(Query.model_validate(data))["query"]) > 10000
    r = _cli(p, "query", "yield", "--query", "--version", "2")
    out = " ".join(r.output.split())
    assert r.exit_code == 0, r.output
    assert len(calls) >= 2 and all(len(str(c.url).encode()) <= URL_LIMIT for c in calls)
    n, counts = len(calls), [10 * c.url.params["filter"].count("labour") for c in calls]
    assert sum(counts) == 3800
    assert f"between {max(counts)} and 3800 works on OpenAlex" in out and f"sent in {n} parts" in out
    assert "the largest part's count is the lower bound" in out and "counts of the parts: part 1:" in out
    # the parts share the long block's terms out, each term once, and keep the other block whole
    q = Query.model_validate(data)
    parts = query_parts(q, lambda x: url_bytes({"filter": render_openalex(x)}) <= 8000)
    assert [t for x in parts for t in x.block("work").terms] == q.block("work").terms
    assert all(x.block("place").terms == q.block("place").terms for x in parts)


def test_refused_request_says_why(tmp_path, monkeypatch):
    from tests.unit.test_dry_run import _cli

    calls: list = []
    p = _long_project(tmp_path, monkeypatch, calls, limit=2000)   # OpenAlex refuses what WIDER thought fits
    assert _cli(p, "lookups", "terms", "allow").exit_code == 0
    r = _cli(p, "query", "yield", "--query", "--version", "2")
    assert r.exit_code == 0 and "count: not available: Request URL too long (HTTP 400)" in r.output


def test_query_that_cannot_be_cut_sends_nothing(tmp_path, monkeypatch):
    from tests.unit.test_dry_run import _cli
    from wider.search.term_yield import query_parts

    calls: list = []
    p = _long_project(tmp_path, monkeypatch, calls)
    _, data = artifacts.load(p, "synonyms", 2)
    data["logic"] = {"and": ["place", {"not": "work"}]}   # a NOT block cut in parts would not add up
    artifacts.save_new_version(p, "synonyms", data, origin="manual")
    assert query_parts(Query.model_validate(data), lambda x: len(render_openalex(x)) < 5000) is None
    assert _cli(p, "lookups", "terms", "allow").exit_code == 0
    r = _cli(p, "query", "yield", "--query", "--version", "3")
    out = " ".join(r.output.split())
    assert r.exit_code == 0 and calls == []
    assert "not available: the query is too long for one OpenAlex request" in out and "cannot be sent in parts" in out


def test_seed_check_of_a_long_query_stays_under_the_limit(tmp_path, monkeypatch):
    from tests.unit.test_dry_run import _cli
    from wider.search.term_yield import BATCH, URL_LIMIT, query_yield

    calls: list = []
    p = _long_project(tmp_path, monkeypatch, calls)
    p.write_benchmark(p.read_benchmark() + [
        BenchmarkPaper(benchmark_id=f"C{i:03d}", title=f"Extra {i}", doi=f"10.1000/extra-seed-{i:03d}",
                       set="Development") for i in range(120)])
    assert _cli(p, "lookups", "allow").exit_code == 0 and _cli(p, "lookups", "terms", "allow").exit_code == 0
    res = query_yield(Project.find(p.root), version=2, seeds=True)
    doi_calls = [c for c in calls if c.url.params["filter"].startswith("doi:")]
    assert doi_calls and all(len(str(c.url).encode()) <= URL_LIMIT for c in doi_calls)
    assert all(len(c.url.params["filter"][4:].split(",", 1)[0].split("|")) <= BATCH for c in doi_calls)
    rows = {x["benchmark_id"]: x["openalex"] for x in res["seeds"]["rows"]}
    assert rows["B001"] == "hit" and rows["B003"] == "miss" and rows["C000"] == "miss"


# ----------------------------------------------------------------- 3: Web of Science strings the import refused
# the same shapes as a published Web of Science string (nested groups in one tag, a tag before untagged groups, two
# words with no operator), with synthetic terms
WOS = ('TS=((delay* OR "slow pace" OR late arriv*) AND (team* OR crew*)) '
       'AND AB=(backlog* OR queue*) AND (office* OR site*)')


def test_wos_nested_groups_in_one_tag():
    q = parse('TS=((delay* OR backlog*) AND (team* OR crew*))', "wos")
    assert q.model_dump(mode="json")["logic"] == {"and": ["delay", "team"]}
    assert [(b.terms, b.fields) for b in q.blocks] == [
        (["delay*", "backlog*"], ["title", "abstract", "keywords"]), (["team*", "crew*"], ["title", "abstract", "keywords"])]


def test_wos_tag_before_untagged_groups_carries_and_is_reported():
    s = "AB=(delay* OR backlog*) AND (team* OR crew*)"
    q = parse(s, "wos")
    assert [b.fields for b in q.blocks] == [["abstract"], ["abstract"]]
    assert [f["message"] for f in check_string(s, "wos")] == [
        "(team* OR crew*) has no field tag: read with the tag before it (AB=), as Web of Science does"]
    assert not check_string("TI=(delay*) AND AB=(team*)", "wos")   # each group tagged: nothing to say


def test_wos_words_without_operator_are_anded_and_reported():
    s = "TS=(tempo* OR late arriv*)"
    q = parse(s, "wos")
    assert q.model_dump(mode="json")["logic"] == {"or": ["tempo", {"and": ["late", "arriv"]}]}
    assert [f["message"] for f in check_string(s, "wos")] == [
        "no operator between late and arriv*: read as AND, as Web of Science does"]
    with pytest.raises(ValueError):   # other platforms still refuse two words with no operator
        parse("(tempo* OR late arriv*)", "pubmed")


def test_wos_string_imports_on_the_cli(tmp_path):
    from tests.unit.test_dry_run import _cli
    p = Project.init(tmp_path / "w")
    f = tmp_path / "wos.txt"
    f.write_text(WOS, encoding="utf-8")
    r = _cli(p, "query", "import", "--platform", "wos", str(f))
    assert r.exit_code == 0, r.output
    out = " ".join(r.output.split())
    assert "no operator between late and arriv*: read as AND" in out
    assert "(office* OR site*) has no field tag: read with the tag before it (AB=)" in out
    _, content = artifacts.load(p, "synonyms")
    q = Query.model_validate(content)
    assert {b.terms[0]: b.fields for b in q.blocks}["office*"] == ["abstract"]
    assert not [x for x in lint(q) if x["level"] == "error"]
    # what WIDER renders for Web of Science reads back as the same query
    again = parse(render(q, "wos"), "wos")
    assert again.model_dump(mode="json")["logic"] == q.model_dump(mode="json")["logic"]
    assert [b.terms for b in again.blocks] == [b.terms for b in q.blocks]


# ----------------------------------------------------------------- 5: a dry run of a long query
def test_dry_run_of_many_truncated_terms_compiles_and_tokenises_once(monkeypatch):
    """More distinct truncated pieces than ``re``'s cache holds were recompiled for every token, and each record was
    tokenised again for every term: a dry run of a long query ran for over 25 minutes."""
    import time

    from wider.search import evaluate as ev
    terms = [f"form{i:03d}x*" for i in range(350)] + [f'"labour pat{i:03d}*"' for i in range(350)]
    q = Query.model_validate({"blocks": [{"key": "a", "name": "A", "terms": terms},
                                         {"key": "b", "name": "B", "terms": terms[::-1]}],
                              "logic": {"and": ["a", "b"]}})
    words = " ".join(f"labour pat{i:03d}ing form{i:03d}y" for i in range(0, 700, 7))
    docs = [ev.SeedDoc(f"s{i}", title=f"form{i:03d}xa", abstract=words) for i in range(5)]
    calls = {"tokens": 0}
    real = ev._tokens

    def counted(*a, **k):
        calls["tokens"] += 1
        return real(*a, **k)
    monkeypatch.setattr(ev, "_tokens", counted)
    ev._compiled.cache_clear()
    profile = ev.load_profile("ebsco")
    t = time.monotonic()
    results = [ev.evaluate(q, d, profile) for d in docs]
    assert time.monotonic() - t < 60   # loose: the counts below are the real check
    assert calls["tokens"] == 2 * len(docs)   # title and abstract, once each per record
    assert ev._compiled.cache_info().misses <= len(terms)   # each piece compiled once
    assert results[0].state == "hit" and "form000x*" in results[0].per_block["a"].terms
    assert '"labour pat000*"' in results[0].per_block["a"].terms


# ----------------------------------------------------------------- 6: OpenAlex budget and API key
def test_openalex_key_is_sent_and_kept_out_of_cache_logs_and_errors(tmp_path, monkeypatch):
    import httpx

    from tests.unit.helpers import mock_client
    from wider.core.lookups import OPENALEX_KEY_ENV, LoggingClient
    from wider.enrich.sources import LookupFailed, _json

    secret = "synthetic-key-0000"
    monkeypatch.setenv(OPENALEX_KEY_ENV, secret)
    calls: list = []
    p = Project.init(tmp_path / "k")
    c = LoggingClient(mock_client(lambda r: httpx.Response(401, json={"error": "bad key"}), calls), p, "term_yield")
    r = c.get("https://api.openalex.org/works", params={"search": "tempo", "per-page": 1})
    c.get("https://api.crossref.org/works", params={"query": "tempo"})
    assert calls[0].url.params["api_key"] == secret            # sent to OpenAlex
    assert "api_key" not in calls[1].url.params                # never to another service
    assert secret not in str(r.request.url)                    # the answer's request has it removed
    with pytest.raises(httpx.HTTPStatusError) as e:
        r.raise_for_status()
    assert secret not in str(e.value)
    with pytest.raises(LookupFailed, match=f"OpenAlex refused the API key in {OPENALEX_KEY_ENV}"):
        _json(r)
    assert all(secret not in v for v in c.items["openalex"])
    ok = LoggingClient(mock_client(lambda r: httpx.Response(200, json={"meta": {"count": 3}}), calls), p, "term_yield")
    ok.get("https://api.openalex.org/works", params={"search": "tempo", "per-page": 1})
    dump = "\n".join(p.store.conn.iterdump())
    assert [ln for ln in dump.splitlines() if secret in ln] == []   # the answer is cached, the key is not


def test_spent_budget_and_missing_key_are_said_plainly(tmp_path, monkeypatch):
    import datetime as dt

    import httpx

    from tests.unit.helpers import mock_client
    from tests.unit.test_dry_run import _cli
    from wider.core.lookups import OPENALEX_KEY_ENV, openalex_refusal

    noon = dt.datetime(2026, 10, 31, 22, 30, tzinfo=dt.UTC)
    spent = openalex_refusal(429, noon)
    assert "free daily budget is spent" in spent and "midnight UTC (in 1 h 30 min)" in spent
    assert OPENALEX_KEY_ENV in spent and "shared by everyone on this network" in spent
    assert "asks for an API key" in openalex_refusal(401) and openalex_refusal(404) == ""
    monkeypatch.setenv(OPENALEX_KEY_ENV, "synthetic-key-0000")
    assert "budget for the API key in OPENALEX_API_KEY is spent" in openalex_refusal(429, noon)
    monkeypatch.delenv(OPENALEX_KEY_ENV)
    # on the CLI: the whole-query count and a single term both say it
    budget = {"error": "Insufficient budget", "message": "Insufficient budget. This request has no API key"}
    p = Project.init(tmp_path / "b")
    monkeypatch.setattr("wider.enrich.sources._client",
                        lambda *a, **k: mock_client(lambda r: httpx.Response(429, json=budget)))
    assert _cli(p, "lookups", "terms", "allow").exit_code == 0
    r = _cli(p, "query", "yield", "tempo*")
    assert "not available: OpenAlex's free daily budget is spent" in " ".join(r.output.split())
    artifacts.save_new_version(p, "synonyms", {"blocks": [{"key": "pace", "name": "Pace", "terms": ["tempo*"]}],
                                               "logic": "pace"}, origin="manual")
    r = _cli(p, "query", "yield", "--query")
    out = " ".join(r.output.split())
    assert "count: not available: OpenAlex's free daily budget is spent" in out and "(HTTP 429)" in out


def test_suggested_edits_evaluate_the_whole_query_once_per_seed(tmp_path, monkeypatch):
    """Each candidate edit changes one block: only that block is evaluated again, so the whole query is evaluated once
    per platform and seed (the base run), not once per candidate; the edits listed stay the same."""
    import wider.search.evaluate as ev
    from tests.unit.test_dry_run import _bench
    from wider.search import diagnostics

    p = _bench(tmp_path, [("B001", "Development", "Telework and team learning", "Telework teams that learn.", ""),
                          ("B002", "Development", "Distributed teams learning from errors",
                           "Virtual work groups learning from errors.", "virtual work; learning; distributed teams")],
               name="e", platforms=("ebsco",))
    syn = {"blocks": [{"key": "a", "name": "Remote", "terms": ["telework"]},
                      {"key": "b", "name": "Learning", "terms": ["learning"]}], "logic": {"and": ["a", "b"]}}
    artifacts.save_new_version(p, "synonyms", syn, origin="manual")
    calls = []
    real = ev.evaluate
    monkeypatch.setattr(ev, "evaluate", lambda *a, **k: calls.append(1) or real(*a, **k))
    res = diagnostics.suggested_edits(p)
    assert len(res["edits"]) >= 2, res["edits"]
    assert len(calls) == 2, len(calls)   # one platform, two development seeds
    e = next(x for x in res["edits"] if x["label"] == 'add "virtual work" to block a')
    assert e["consequence"] == "+1 seed on ebsco, +0 records (no import for this version yet)", e


def test_truncation_report_tries_only_words_with_the_literal_start():
    """The term report looks up each truncated piece among the words that start with its literal start, and finds
    exactly the words a scan of the whole vocabulary finds (a leading wildcard still scans every word)."""
    from wider.search import diagnostics
    from wider.search.evaluate import SeedDoc, load_profile

    pr = load_profile("scopus")
    words = sorted({"team", "teams", "teamwork", "tea", "crew", "crews", "screw", "delay", "delayed", "delays",
                    "organisation", "organization", "organisational", "queue", "queues", "qu"})
    for piece in ("team*", "tea*", "crew*", "delay*", "organi?ation*", "qu*", "*ew", "zz*", "te?m*"):
        rx = diagnostics._pattern_re(piece, pr)
        assert diagnostics._matching(words, piece, pr) == [w for w in words if rx.fullmatch(w)], piece
    doc = SeedDoc("B1", "Development", "Teams and crews", "Delayed queues in teamwork", "")
    q = Query.model_validate({"blocks": [{"key": "a", "name": "A", "terms": ["team*", "crew*", "delay*", "queue*"]}],
                              "logic": "a"})
    rep = {r["term"]: r["stems"][0] for r in diagnostics.truncation_report(q, [doc], pr)}
    assert [w["word"] for w in rep["team*"]["words"]] == ["teams", "teamwork"]
    assert rep["team*"]["shorter"]["pattern"] == "tea*" and rep["team*"]["shorter"]["gains"] == []
    assert [x["pattern"] for x in rep["team*"]["longer"]] == ["teams*", "teamw*"]


def test_an_edited_block_state_matches_a_full_block_evaluation():
    """Suggested edits match only the terms an edit adds and take the rest from the base run; the state is the one a
    full evaluation of the edited block gives, for every kind of edit, seed and platform."""
    from wider.search import diagnostics
    from wider.search.evaluate import SeedDoc, evaluate_block, load_profile

    docs = [SeedDoc("B1", "Development", "Crew delays", "Teams wait in queues", "backlog"),
            SeedDoc("B2", "Development", "Office work", "", ""),   # no abstract: a block naming it is uncertain
            SeedDoc("B3", "Development", "Site crews and teamwork", "Late arrivals at the site", ""),
            SeedDoc("B4", "Development", "Nothing related", "Plain words only", "")]
    data = {"blocks": [{"key": "a", "name": "A", "terms": ["team*", "crew*", '"late arrival*"', "queue"]}],
            "logic": "a"}
    changes = [{"op": "add_term", "block": "a", "term": "backlog*"}, {"op": "add_term", "block": "a", "term": "team*"},
               {"op": "drop_term", "block": "a", "term": "team*"}, {"op": "drop_term", "block": "a", "term": "crew*"},
               {"op": "replace_piece", "block": "a", "term": "team*", "old": "team*", "new": "teamw*"},
               {"op": "add_field", "block": "a", "field": "keywords"}]
    for key in ("scopus", "ebsco", "pubmed"):
        pr = load_profile(key)
        q = Query.model_validate(data)
        for c in changes:
            new = Query.model_validate(diagnostics.edited_data(data, c))
            for d in docs:
                want = evaluate_block(new.block("a"), d, pr).state
                got = diagnostics._edited_state(q.block("a"), new.block("a"), evaluate_block(q.block("a"), d, pr), d, pr)
                assert got == want, (key, c, d.benchmark_id)


# ----------------------------------------------------------------- 7: import sanity check speed
def test_vocabulary_overlap_agrees_with_a_plain_regex_scan():
    import random

    from wider.core.text import norm_text
    from wider.ingest.sanity import term_pattern, vocabulary_overlap
    rng = random.Random(7)
    words = ["tempo", "tempos", "atempo", "drift", "drifted", "crew", "screw", "lag", "flag", "unit", "units"]
    terms = ["tempo*", "drift", '"crew lag*"', "lag", '"un-it*"', "screw*", '"tempo drift*"', "u*"]
    blocks = {f"b{i}": {"role": "required", "patterns": [term_pattern(t) for t in terms[i::3]]} for i in range(3)}
    vocab = {"version": 1, "status": "approved", "note": None, "blocks": blocks}
    rows = [{"title": " ".join(rng.choice(words) for _ in range(rng.randint(0, 3))),
             "abstract": "-".join(rng.choice(words) for _ in range(rng.randint(0, 4)))} for _ in range(400)]
    rows += [{"title": "un-its first"}, {"title": "network"}, {"title": "Crew  lagging"}]
    plain = {k: sum(any(p.search(norm_text(f"{r.get('title') or ''} {r.get('abstract') or ''} ")) for p in b["patterns"])
                    for r in rows) / len(rows) for k, b in blocks.items()}
    ov = vocabulary_overlap(rows, vocab)
    assert ov["blocks"] == {k: round(v, 3) for k, v in plain.items()}
    assert 0 < ov["blocks"]["b0"] < 1 and 0 < ov["blocks"]["b1"] < 1


def test_vocabulary_overlap_skips_a_term_whose_first_word_is_absent():
    from wider.ingest.sanity import _found, term_pattern

    class Counted:
        def __init__(self, pat):
            self.pat, self.calls = pat, 0

        def search(self, text):
            self.calls += 1
            return self.pat.search(text)

        def match(self, text, pos):
            self.calls += 1
            return self.pat.match(text, pos)

    pat = Counted(term_pattern('"team drift*"'))
    assert not _found(" network latency in a steam room", " team", pat) and pat.calls == 0
    assert _found(" a team drifting apart", " team", pat) and pat.calls == 1
    assert not _found(" team spirit and team lag", " team", pat) and pat.calls == 3
