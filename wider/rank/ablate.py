"""Ablation: rank with every anchors version × embedding set available and compare benchmark recovery. The default
ranking and the rank stage result are left as they were."""
from __future__ import annotations

from ..construct import artifacts
from ..core.project import Project
from .score import rank, validate

_SETS = {"dev": "development", "val": "validation"}
_MEASURES = {"median_rank": "median rank", "max_rank": "maximum rank", "found": "found"}
_PLAIN = {"anchors_version": "anchors version", "top_n": "top-N"}


def column_label(key: str) -> str:
    """The readable header of an ablation column, shared by `wider rank ablate` and the Ranking page:
    ``dev_median_rank`` → "development median rank", ``val_recall_at_250`` → "validation recall at 250"."""
    if key in _PLAIN:
        return _PLAIN[key]
    pre, _, rest = key.partition("_")
    if pre in _SETS and rest:
        measure = _MEASURES.get(rest) or rest.replace("recall_at_", "recall at ").replace("_", " ")
        return f"{_SETS[pre]} {measure}"
    return key.replace("_", " ")


def _peek(project: Project, include_validation: bool | None) -> bool:
    from ..search.coverage import validation_unlocked

    return bool(include_validation and project.config.protocol.identification.validation_after_freeze
                and not validation_unlocked(project))


def choice_needed(project: Project, include_validation: bool | None) -> str | None:
    """The asking notice when including the Validation set now needs the researcher's choice first (a look before the
    freeze with papers to show), else None. The CLI and the route ask before the consent checks and any ranking."""
    from ..search.coverage import look_plan

    if not _peek(project, include_validation):
        return None
    plan = look_plan(project)
    return plan["notice"] if plan["ask"] else None


def ablate(project: Project, embedding_sets: list[str] | None = None, anchors_versions: list[int] | None = None,
           top_ns=None, include_validation: bool | None = None, seen_choice: str | None = None) -> list[dict]:
    """The ablation rows (see :func:`ablation`)."""
    return ablation(project, embedding_sets, anchors_versions, top_ns, include_validation, seen_choice)["rows"]


def ablation(project: Project, embedding_sets: list[str] | None = None, anchors_versions: list[int] | None = None,
             top_ns=None, include_validation: bool | None = None, seen_choice: str | None = None) -> dict:
    """``rows``, one per embedding set × anchors version × top-N, ``validation_peeked`` and ``notice``.

    Including the Validation set while it is hidden is one look, as in coverage (:func:`wider.search.coverage.look`):
    before the first counting unlock it needs ``seen_choice`` (raises ``SeenChoiceNeeded`` before any ranking,
    nothing recorded); the look is recorded once, and every combination is validated on the papers read before it."""
    from ..search.coverage import look, set_members, validation_unlocked

    if include_validation is None:
        include_validation = validation_unlocked(project)
    peeked = _peek(project, include_validation)
    st = project.store
    top_ns = tuple(top_ns or project.config.protocol.ranking.top_n_compare)
    sets = embedding_sets or [r["set_name"] for r in st.query("SELECT set_name FROM embedding_sets")]
    versions = anchors_versions or [r["version"] for r in artifacts.list_versions(project, "anchors")]
    if not (sets and versions):   # nothing to ablate: nothing is shown, so no look either
        return {"rows": [], "validation_included": include_validation, "validation_peeked": False, "notice": None}
    members = set_members(project, include_validation)   # the sets as they were when asked for (D3)
    notice = look(project, seen_choice) if peeked else None
    out = []
    for s in sets:
        for v in versions:
            _, data = artifacts.load(project, "anchors", v)
            n_cells = len(data["anchors"])
            # a top-N above the number of cells is ranked as top-<cells>, once, and the row says which values it stands for
            asked: dict[int, list[int]] = {}
            for n in top_ns:
                asked.setdefault(min(n, n_cells), []).append(n)
            for n, wanted in asked.items():
                r = rank(project, set_name=s, anchors_version=v, top_n=n, current=False)   # the chosen ranking stays
                val = validate(project, r["name"], include_validation=include_validation, members=members)
                above = [x for x in wanted if x > n_cells]
                note = f"top-{', '.join(map(str, above))} capped at the {n_cells} cells" if above else ""
                row = {"embedding": s, "anchors_version": v, "top_n": n, "ranking": r["name"], "note": note}
                for sname, summ in val["summary"].items():
                    if sname not in ("Development", "Validation"):   # used validation papers in neither column (D1)
                        continue
                    row[f"{sname[:3].lower()}_median_rank"] = summ["median_rank"]
                    row[f"{sname[:3].lower()}_max_rank"] = summ["max_rank"]
                    row[f"{sname[:3].lower()}_recall_at_250"] = summ["recall_at_250"]
                    row[f"{sname[:3].lower()}_found"] = f"{summ['found']}/{summ['n']}"
                out.append(row)
    return {"rows": out, "validation_included": include_validation, "validation_peeked": peeked, "notice": notice}
