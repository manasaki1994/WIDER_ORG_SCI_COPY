"""Recall-first decision rule (the review's rule-v3), generalised to any codebook.

- An "absent" grade whose quote is not found verbatim in the title+abstract becomes "unclear".
- An exclude stands only with grounds: target_fit unrelated, or a verified absent element; it needs a reason;
  and it is overridden to maybe when a check that implies include says yes.
- An include becomes maybe when a check that implies maybe or absent says yes, or when an edge case says maybe.
- Thin records are flagged, not changed (the prompt says when a title-only exclude is allowed).
- With ``protocol.screening.recall_first`` off the first two points are skipped.
"""
from __future__ import annotations

from ..core.matching import quote_verified
from ..core.text import norm_text


def apply_rule(parsed: dict, text: str, spec: dict, thin: bool = False, quote_min_chars: int = 8,
               recall_first: bool | None = None) -> dict:
    """``recall_first`` (default: the spec's ``recall_first``, else on) off keeps the model's exclusions and its
    unverified absent grades; unverified quotes are still flagged and the include → maybe checks still apply."""
    if recall_first is None:
        recall_first = spec.get("recall_first", True)
    elements = spec["elements"]
    d0 = parsed.get("decision")
    d = d0
    flags: list[str] = []
    hay = norm_text(text)
    verified = {}
    eff = {}
    for e in elements:
        g = parsed.get(e)
        q = parsed.get(f"{e}_quote") or ""
        v = quote_verified(q, hay, min_fragment=quote_min_chars)
        verified[e] = v
        eff[e] = "unclear" if (g == "absent" and not v and recall_first) else g
        if g == "absent" and not v:
            flags.append(f"absent_unverified:{e}")
    if d0 == "exclude" and recall_first:
        grounds = parsed.get("target_fit") == "unrelated" or any(v == "absent" for v in eff.values())
        if not grounds:
            flags.append("override:no_grounds")
        if parsed.get("exclusion_reason", "none") in ("none", "", None):
            flags.append("override:no_reason")
        if any(parsed.get(c) is True for c in spec.get("include_checks", [])):
            flags.append("override:ruling")
        if any(f.startswith("override") for f in flags):
            d = "maybe"
    if d0 == "include":
        if any(parsed.get(c) is True for c in spec.get("maybe_checks", []) + spec.get("absent_checks", [])):
            d = "maybe"
            flags.append("override:check_contradicts_include")
        if parsed.get("edge_case") in spec.get("maybe_edge_cases", []):
            d = "maybe"
            flags.append(f"edge:{parsed.get('edge_case')}")
        for flag in spec.get("maybe_flags", []):
            if parsed.get(flag) is True:
                d = "maybe"
                flags.append(f"flag:{flag}")
    if thin:
        flags.append("thin")
    return {"raw": d0, "final": d, "flags": ";".join(flags), "verified": verified, "effective": eff}
