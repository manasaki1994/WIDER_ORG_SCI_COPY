import httpx

from wider.enrich import sources


def test_openalex_inverted_index_and_order(monkeypatch):
    def handler(request: httpx.Request):
        if "openalex" in request.url.host:
            return httpx.Response(200, json={"title": "T", "publication_year": 2001,
                                             "abstract_inverted_index": {"world": [1], "Hello": [0]}})
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    found, log = sources.fetch_abstract("10.1000/x", min_words=1, client=client)
    assert found.source == "openalex" and found.abstract == "Hello world" and found.year == 2001
    assert log[0]["status"] == "hit"


def test_fallback_chain_and_short_abstracts(monkeypatch):
    def handler(request: httpx.Request):
        h = request.url.host
        if "openalex" in h:
            return httpx.Response(404)
        if "crossref" in h:
            return httpx.Response(200, json={"message": {"abstract": "<jats:p>Short one.</jats:p>", "title": ["T"]}})
        if "ebi.ac.uk" in h:
            return httpx.Response(200, json={"resultList": {"result": [{"abstractText": "A " * 30, "pubYear": "1999"}]}})
        return httpx.Response(500)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    found, log = sources.fetch_abstract("https://doi.org/10.1000/Y", min_words=20, client=client)
    assert found.source == "europepmc" and found.words == 30 and found.year == 1999
    assert [x["status"] for x in log] == ["miss", "hit", "hit"]


def test_no_doi():
    found, log = sources.fetch_abstract(None)
    assert found is None and log[0]["status"] == "no_doi"
