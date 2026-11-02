"""Plain-text rendering of import results and sanity verdicts (shared by the CLI and tests)."""
from __future__ import annotations

VERDICT_COLOURS = {"ok": "green", "capped": "yellow", "off_topic": "red", "off topic": "red", "empty": "red",
                   "duplicate": "yellow", "ok, one check low": "yellow"}
SANITY_COLUMNS = ("platform", "file", "format", "records", "benchmark", "vocabulary", "verdict", "note")


def sanity_rows(report: dict) -> list[tuple[str, ...]]:
    """One tuple per file in the order of :data:`SANITY_COLUMNS`."""
    from .sanity import pct, verdict_label

    rows = []
    for r in report["files"]:
        bench = "-" if r.get("benchmark_hits") is None else f"{r['benchmark_hits']}/{r['benchmark_n']}"
        vocab = "-" if r.get("vocab_overlap") is None else f"{pct(r['vocab_overlap'])}%"
        fmt = r.get("format") or "?"
        if r.get("profile") and not str(r["profile"]).startswith("ris:"):
            fmt += f" ({r['profile']})"
        rows.append((r["platform"], r["file"], fmt, str(r["n"]), bench, vocab, verdict_label(r), r.get("message", "")))
    return rows


def import_line(r: dict) -> str:
    """One line per import result, without markup."""
    if r["status"] == "imported":
        return (f"{r['platform']}/{r['file']}: {r['n']} records ({r.get('format')}) "
                f"(no title {r['no_title']}, no abstract {r['no_abstract']}, no DOI {r['no_doi']})"
                + (f"; keywords copied to {', '.join(r['keywords_copied'])}" if r.get("keywords_copied") else ""))
    if r["status"] == "error":
        return f"{r['platform']}/{r['file']}: could not be read: {r.get('error')}"
    return f"{r['platform']}/{r['file']}: already imported ({r['n']})"
