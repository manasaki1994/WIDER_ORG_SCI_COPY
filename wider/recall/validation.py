"""Validation of recall on the probability sample: target, outcome at each look, and the words every surface prints
(change validation-sample-planner, design D4, D5, D7, D8, D10, D15, D16)."""
from __future__ import annotations

import re
from datetime import UTC


def judge(bounds: dict | None, target: float | None) -> str | None:
    """"met" when the lower bound reaches ``target``, "not met" when the upper bound is below it, else "cannot tell";
    None without a target or bounds. Only the exact bounds of :func:`wider.recall.anytime.recall_bounds` are passed
    here, never the model-based supplement (D5, D17)."""
    if target is None or not bounds:
        return None
    if bounds["low"] >= target:
        return "met"
    if bounds["high"] < target:
        return "not met"
    return "cannot tell"


# ------------------------------------------------------------------------------------------- target and rule (D4)
RULES = ("maybes-as-includes", "top-strata", "revise-prompt")
TARGET_FIELDS = ("target", "low_recall_rule", "top_strata_depth", "precision")
DISCLOSED_FIELDS = ("primary_variant", "ci_level")
DEPTH_NEEDED = "the top-strata rule needs --depth: the rank down to which humans screen every record"
OUTSIDE = "Recall target set outside WIDER (time unknown); it may have been set after a recall result was seen"
NO_TARGET = "No recall target was set in advance."
# the line looks stored before 1 Nov 2026 carry: it named a command, which does not belong in manuscript text
OLD_NO_TARGET = "No recall target was set before the results; set one with wider validation target."
CHANGED_WORDS = {"target": "Recall target set", "primary_variant": "Primary variant changed",
                 "ci_level": "Interval level changed"}


class TargetError(ValueError):
    pass


def rule_words(rule: str | None, depth: int | None = None) -> str:
    if rule == "maybes-as-includes":
        return "treat maybes as includes"
    if rule == "top-strata":
        return f"humans screen every record ranked 1 to {depth}"
    if rule == "revise-prompt":
        return "revise the prompt"
    return "none"


def _day(at: str | None) -> str:
    return str(at or "")[:10]


def first_look(project) -> dict | None:
    """The first recall result seen on any probability sample ({sample, at}), or None (D7)."""
    try:
        r = project.store.one("SELECT s.name, l.first_at FROM validation_looks l JOIN samples s ON s.sample_id=l.sample_id "
                              "ORDER BY l.first_at LIMIT 1")
    except Exception:  # noqa: BLE001 - a store from before the looks table holds no look
        return None
    return {"sample": r[0], "at": r[1]} if r else None


def _recall_of(text: str | None) -> dict:
    from ..core.journal import _effective

    return ((_effective(text).get("protocol") or {}).get("recall") or {}) if text is not None else {}


def _entries(project) -> list[dict]:
    """The recall_target entries in effect, newest first, each with the recall fields before and after."""
    import json

    from ..core.journal import undone_now

    out = []
    for r in project.store.query("SELECT * FROM history WHERE kind='recall_target' AND undo_of IS NULL "
                                 "ORDER BY change_id DESC"):
        d = dict(r)
        if undone_now(project, d):
            continue
        yaml_item = next((i for i in json.loads(d["items"]) if i.get("path") == "project.yaml"), None)
        if yaml_item is None:
            continue
        out.append({"at": d["at"], "author": d["author"], "before": _recall_of(yaml_item.get("before")),
                    "after": _recall_of(yaml_item.get("after"))})
    return out


def _depth_limit(project) -> int | None:
    """The default ranking's length, or None without one."""
    try:
        from ..sample.design import _ranking

        rk = _ranking(project, None)
        return int(project.store.scalar("SELECT COUNT(*) FROM ranking_rows WHERE ranking_id=?", (rk["ranking_id"],)) or 0)
    except (KeyError, ValueError, LookupError):
        return None


def _depth_error(project) -> TargetError:
    n = _depth_limit(project)
    return TargetError(f"depth must be between 1 and {n or 'the length of the ranking'}")


def check_target(project, protocol) -> None:
    """Refuse a top-strata rule without a depth between 1 and the ranking's length."""
    rc = protocol.recall
    if rc.low_recall_rule != "top-strata":
        return
    if rc.top_strata_depth is None:
        raise TargetError(DEPTH_NEEDED)
    n = _depth_limit(project)
    if rc.top_strata_depth < 1 or (n and rc.top_strata_depth > n):
        raise _depth_error(project)


def save_protocol(project, old, author: str | None = None, always: bool = False) -> list[str]:
    """Save ``project.config.protocol`` after a write that started from ``old``. Every write path (wider validation
    target, wider protocol set|reset, the protocol routes, the Setup page) ends here, so a change of the target, rule,
    depth, precision, primary variant or interval level is one timed ``recall_target`` History entry, and a change
    after a recall result was seen is said. Restores ``old`` and raises :class:`TargetError` on a bad rule."""
    from ..core.journal import recording

    new = project.config.protocol
    changed = [f for f in TARGET_FIELDS + DISCLOSED_FIELDS if getattr(old.recall, f) != getattr(new.recall, f)]
    if not changed and not always:
        project.save()
        return []
    try:
        check_target(project, new)
    except TargetError:
        project.config.protocol = old
        raise
    summary = "; ".join(f"{f} {_v(getattr(old.recall, f))} → {_v(getattr(new.recall, f))}" for f in changed) or \
        f"target {_v(new.recall.target)} confirmed, unchanged"
    if changed:
        with recording(project, "recall_target", summary, author):
            project.save()
    else:   # a confirmation changes no file but is still timed, so a target written outside WIDER is no longer unknown
        from ..core.journal import _store_change, current_author

        project.save()
        text = (project.root / "project.yaml").read_text(encoding="utf-8")
        _store_change(project, "recall_target", summary, [{"t": "file", "path": "project.yaml", "before": text,
                                                         "after": text}], current_author(author), None)
    look = first_look(project)
    lines = [summary]
    if look:
        what = "this target was set" if set(changed) & set(TARGET_FIELDS) else "this change"
        lines.append(f"a recall result on {look['sample']} was seen on {_day(look['at'])}, before {what}; every "
                     "output will say so")
    return lines


def _v(x) -> str:
    return "empty" if x is None else (f"{x:.2f}" if isinstance(x, float) else str(x))


def as_fraction(x: float | None) -> float | None:
    """A recall target typed as a percentage (95) is read as 0.95; a fraction stays as it is (inspection 1 Nov 2026)."""
    return x / 100 if x is not None and 1 < x < 100 else x


def set_target(project, target: float | None, rule: str | None = None, depth: int | None = None,
               precision: float | None = None, author: str | None = None) -> list[str]:
    """``wider validation target`` and ``POST /api/validation/target``: store the target, the rule (with its depth)
    and the planned margin, timed in History; returns the lines."""
    target = as_fraction(target)
    if target is not None and not 0 < target < 1:
        raise TargetError("the target must be a fraction between 0 and 1 (e.g. 0.95) or a percentage above 1 and below "
                          "100 (e.g. 95)")
    if rule is not None and rule not in RULES:
        raise TargetError(f"unknown rule {rule}; use one of {', '.join(RULES)}")
    if precision is not None and not 0 < precision < 1:
        raise TargetError("the margin must be between 0 and 1")
    if rule == "top-strata" and depth is not None and depth < 1:
        raise _depth_error(project)
    old = project.config.protocol
    new = old.set("recall.target", target).set("recall.low_recall_rule", rule)
    new = new.set("recall.top_strata_depth", depth if rule == "top-strata" else None)
    if precision is not None:
        new = new.set("recall.precision", precision)
    project.config.protocol = new
    lines = save_protocol(project, old, author, always=True)
    return lines + [status_line(project)]


def timing_lines(project) -> list[str]:
    """What every recall output adds about when the target, rule, primary variant or level was set (D4)."""
    look = first_look(project)
    entries = _entries(project)
    out = []
    rc = project.config.protocol.recall
    if rc.target is not None and (not entries or entries[0]["after"].get("target") != rc.target):
        out.append(OUTSIDE)
    if look:
        for key, fields in (("target", TARGET_FIELDS), ("primary_variant", ("primary_variant",)),
                            ("ci_level", ("ci_level",))):
            e = next((e for e in entries if any(e["before"].get(f) != e["after"].get(f) for f in fields)), None)
            if e and e["at"] > look["at"]:
                out.append(f"{CHANGED_WORDS[key]} on {_day(e['at'])}, after a recall result was seen "
                           f"({_day(look['at'])})")
    return out


def status_line(project) -> str:
    rc = project.config.protocol.recall
    if rc.target is None:
        return NO_TARGET
    entries = [e for e in _entries(project) if any(e["before"].get(f) != e["after"].get(f) for f in TARGET_FIELDS)]
    look = first_look(project)
    when = ""
    if entries and entries[0]["after"].get("target") == rc.target:
        at = entries[0]["at"]
        when = (f"; set {_day(at)} " + ("before any recall result was seen" if not look or at < look["at"] else
                                        f"after a recall result was seen ({_day(look['at'])})"))
    line = f"Recall target {rc.target:.2f}, rule: {rule_words(rc.low_recall_rule, rc.top_strata_depth)}{when}"
    if rc.precision is not None:
        line += f"; planned margin {rc.precision:.2f}"
    return line


def status(project, sample: str | None = None) -> dict:
    """``wider validation status`` and ``GET /api/validation/status``: the target, and the outcome and lines of the last
    look on ``sample`` as the estimate stored them (``last_validation_lines:<sample>``); records no look."""
    import json

    from ..sample.plan import refresh_warning

    sample = sample or project.config.protocol.sampling.probability_sample
    rc = project.config.protocol.recall
    lines = [status_line(project)] + timing_lines(project)
    last = json.loads(project.store.get_meta(f"last_validation_lines:{sample}") or "{}")
    stored, oc = [NO_TARGET if x == OLD_NO_TARGET else x for x in last.get("lines", [])], last.get("outcome")
    was = judged_target(last)
    stale = bool(last) and ((was is None) != (rc.target is None)
                            or (was is not None and round(was, 2) != round(rc.target, 2)))
    if stale:   # judged against another target (or none): only the current target is shown (audit C2)
        stored, oc = [x for x in stored if not _judges_target(x)], None
        lines.append(retarget_line(sample, last, rc.target))
    return {"target": rc.target, "rule": rc.low_recall_rule, "depth": rc.top_strata_depth, "precision": rc.precision,
            "sample": sample, "look": last.get("look"), "outcome": oc, "target_stale": stale,
            "bounds": last.get("bounds"), "validation_lines": stored, "stops": _stops(project, sample),
            "second_reviewer": second_reviewer_coverage(project, project.store.scalar(
                "SELECT sample_id FROM samples WHERE name=?", (sample,))),
            "lines": lines + refresh_warning(project, sample, [x for x in stored
                                                                 if not x.startswith("Validation was ") and x not in lines])
            + sealed_lines(project, sample, last.get("at")) + population_lines(project, sample, last)
            + continue_lines(project, sample)}


_VERDICT = re.compile(r"^(?:Target (\d\.\d\d) (?:met|not met)|Inconclusive for the target (\d\.\d\d)):")


def _judges_target(line: str) -> bool:
    """A stored look line that depends on the target: the verdict, the rule's line, or that none was set."""
    return line in (NO_TARGET, OLD_NO_TARGET) or line.startswith("Your rule:") or bool(_VERDICT.match(line))


def judged_target(last: dict) -> float | None:
    """The target a stored look was judged against: stored with it, else read from its verdict line (older looks)."""
    if "target" in last:
        return last["target"]
    for x in last.get("lines", []):
        m = _VERDICT.match(x)
        if m:
            return float(m.group(1) or m.group(2))
    return None


def retarget_line(sample: str, last: dict, target: float | None) -> str:
    was = "without a target" if judged_target(last) is None else f"against the target {_f2(judged_target(last))}"
    now = ("no target is set now, so it gives no verdict" if target is None else
           f"estimate recall again for a verdict on the target {_f2(target)}")
    return f"The last recall result on {sample} ({_day(last.get('at'))}) was judged {was}; {now}."


def sealed_lines(project, sample: str | None, at: str | None) -> list[str]:
    """After the stored lines of ``wider validation status`` (D6): the change line, and, when the stored result is
    older than a correction of a sealed reference, that it is stale."""
    from ..sample.labels import corrected_since, sealed_change_line, sealed_disagreement_line

    dis = sealed_disagreement_line(project, sample)   # also without a corrected reference (recall-final-check-fixes D6)
    line = sealed_change_line(project, sample)
    if line is None:
        return [dis] if dis else []
    n = corrected_since(project, sample) if at else 0
    stale = (f"This result is from {_day(at)}; {n} reference label{'s were' if n != 1 else ' was'} corrected since. "
             "Estimate recall again.") if n else None
    return [line] + ([stale] if stale else []) + ([dis] if dis else [])


POPULATION_STALE = ("The stored recall result of {sample} was worked out before records drawn into another sample after "
                    "it were counted in its bands; estimate recall again (the same labels add no look).")


def population_lines(project, sample: str | None, last: dict | None) -> list[str]:
    """recall-final-check-fixes D9: the stored result ``last`` (``last_validation_lines:<sample>``) lacks the
    population marker and a band of ``sample`` now has records another sample took after the draw, so its bounds and
    verdict may have been worked out the old way."""
    from ..sample.plan import sample_cells
    from .curve import POPULATION

    if not sample or not last or last.get("population") == POPULATION or not last.get("result_set_key"):
        return []
    sc = sample_cells(project, sample, last["result_set_key"])
    if sc is None or not any(b["in_other"] for b in sc["bands"]):
        return []
    return [POPULATION_STALE.format(sample=sample)]


# ------------------------------------------------------------------------------------------------- outcome (D5)
OUTCOME_KEYS = {"met": "met", "not met": "not_met", "cannot tell": "cannot_tell"}
# the words each outcome key is said with (recall-certification-workload D1): the key cannot_tell stays in --json, the
# API and stored looks, and is said "inconclusive", which is not evidence that the target was missed
OUTCOME_WORDS = {"met": "met", "not_met": "not met", "cannot_tell": "inconclusive"}
MAYBES_KEPT = "Your primary estimate already counts maybes as kept, so the rule changes nothing."


def _f2(x) -> str:
    return f"{x:.2f}"


def _rule_head(rule: str, depth: int | None, missed: int | None) -> str:
    if rule == "top-strata":
        return (f"Your rule: humans screen every record ranked 1 to {depth} ({missed if missed is not None else 'K'} "
                "records the screening excluded there).")
    return f"Your rule: {rule_words(rule)}."


def outcome(primary: dict, protocol, look: int, rule_est: dict | None = None, sample: str = "validation") -> dict:
    """The outcome at a look (D5), judged on the primary variant's exact bounds (D15) only.

    ``primary`` and ``rule_est`` are ``{"estimate", "low", "high"}`` (the exact bounds at ``ci_level``); ``rule_est``
    is the rule's own estimate on the same labels (the lenient variant, or the top-strata re-run with ``missed``),
    computed by the caller. Returns ``{"outcome", "basis", "more_validation", "lines"}``."""
    rc = protocol.recall
    level = f"{round(rc.ci_level * 100)}%"
    lo, hi = primary["low"], primary["high"]
    span = f"at look {look}, the exact bounds valid at every look ({level}), {_f2(lo)} to {_f2(hi)}"
    out = {"outcome": None, "basis": "exact", "more_validation": False, "lines": []}
    if rc.target is None:
        out["lines"].append(NO_TARGET)
        est = primary.get("estimate")
        margin = None if est is None or lo is None else est - lo
        if rc.precision is not None and margin is not None and margin > rc.precision:
            out["more_validation"] = True
            out["lines"].append(f"The margin is wider than planned: the lower bound is {_f2(margin)} below the "
                                f"estimate, more than the {_f2(rc.precision)} planned.")
        return out
    t = _f2(rc.target)
    verdict = judge(primary, rc.target)
    out["outcome"] = OUTCOME_KEYS[verdict]
    if verdict == "met":
        out["lines"].append(f"Target {t} met: {span}, lie at or above it.")
        return out
    if verdict == "not met":
        out["lines"].append(f"Target {t} not met: {span}, lie below it.")
    else:
        out["more_validation"] = True
        out["lines"].append(f"Inconclusive for the target {t}: {span}, contain it. This is not evidence that the "
                            f"target was missed; the labels so far cannot tell met from not met.")
    rule = rc.low_recall_rule
    if rule == "revise-prompt":
        out["lines"].append(f"Your rule: revise the prompt. This uses up validation: draw a fresh sample before "
                            f"estimating again (wider sample draw probability --name NAME --fresh-from {sample}).")
    elif rule == "maybes-as-includes" and rc.primary_variant in ("lenient", "cautious"):
        out["lines"].append(f"{_rule_head(rule, None, None)} {MAYBES_KEPT}")
    elif rule and rule_est:
        v = judge(rule_est, rc.target)
        tail = {"met": "target met under your rule", "not met": "target not met under your rule",
                "cannot tell": "inconclusive"}[v]
        out["rule_outcome"] = OUTCOME_KEYS[v]
        out["lines"].append(f"{_rule_head(rule, rc.top_strata_depth, rule_est.get('missed'))} With it, recall is "
                            f"{_f2(rule_est['estimate'])} (exact bounds {_f2(rule_est['low'])} to "
                            f"{_f2(rule_est['high'])}): {tail}.")
    return out


# ------------------------------------------------------------------------------------------------ looks (D7, D15)
LOOKS_BEFORE = ("Recall results seen before this version are not recorded; a target set now may come after a result "
                "was seen.")


def labels_checksum(rows) -> str:
    """The checksum of a sample's final labels (record, decision), which names one look (D15)."""
    import hashlib

    text = ";".join(f"{r['record_id']}:{r['human']}" for r in sorted(rows, key=lambda r: r["record_id"]))
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def record_look(project, sample: str, key: str, rows, level: float | None = None, primary: str | None = None
                ) -> dict | None:
    """Record that recall on ``sample`` was seen for result set ``key`` (D7), and the look its labels make (D15).
    Probability samples only; returns ``{"index", "first"}`` or None. The first look at a (sample, set) is a
    permanent ``sample_look`` History entry; later looks update ``last_at`` and ``n`` only. The records of ``rows``
    are sealed at this look (label-completion-and-sealing D5)."""
    import json

    from ..core.journal import _store_change, current_author
    from ..screen.result_sets import row_by_key

    st = project.store
    s = st.query("SELECT sample_id, kind FROM samples WHERE name=?", (sample,))
    if not s or s[0]["kind"] != "probability":
        return None
    from ..sample.labels import sample_by_name, seal_look, sealed_records

    sealed_records(project, sample_by_name(project, sample))   # an older sample's seals come before this look's
    sid, now = s[0]["sample_id"], _now()
    srow = row_by_key(project, key) or {}
    settings = srow.get("settings")
    if isinstance(settings, str):
        try:
            settings = json.loads(settings)
        except ValueError:
            settings = None
    pv = settings.get("prompt_version") if isinstance(settings, dict) else None
    old = st.query("SELECT look_id, n FROM validation_looks WHERE sample_id=? AND result_set_key=?", (sid, key))
    first = not old or not old[0]["n"]
    if old and not first:
        st.execute("UPDATE validation_looks SET last_at=?, n=n+1 WHERE look_id=?", (now, old[0]["look_id"]))
    elif old:   # the row a choice made before the first look (D8)
        who = current_author()
        st.execute("UPDATE validation_looks SET first_at=?, last_at=?, n=1, first_by=? WHERE look_id=?",
                   (now, now, who, old[0]["look_id"]))
        _store_change(project, "sample_look", f"recall on {sample} seen for result set {key}",
                      [{"t": "look", "sample": sample, "result_set_key": key}], who, None)
    else:
        who = current_author()
        st.execute("INSERT INTO validation_looks(sample_id,result_set_key,settings_hash,prompt_version,first_at,last_at,"
                   "n,first_by) VALUES(?,?,?,?,?,?,1,?)", (sid, key, srow.get("settings_hash"), pv, now, now, who))
        _store_change(project, "sample_look", f"recall on {sample} seen for result set {key}",
                      [{"t": "look", "sample": sample, "result_set_key": key}], who, None)
    cs = labels_checksum(rows)
    have = st.query("SELECT look_index FROM validation_look_states WHERE sample_id=? AND labels_checksum=?", (sid, cs))
    if have:
        idx = int(have[0]["look_index"])
    else:
        idx = int(st.scalar("SELECT COALESCE(MAX(look_index),0) FROM validation_look_states WHERE sample_id=?",
                            (sid,))) + 1
        st.execute("INSERT INTO validation_look_states(sample_id,labels_checksum,look_index,first_at,level,set_key) "
                   "VALUES(?,?,?,?,?,?)", (sid, cs, idx, now, level, key))
    seal_look(project, sample, [r["record_id"] for r in rows], idx, primary)
    return {"index": idx, "first": first, "checksum": cs}


# ------------------------------------------------------------------------- a changed configuration after a look (D8)
CHOICES = ("used", "not_informed")
CHOOSE_SAMPLE = "Choose one: --sample-used or --not-informed. Nothing was done."
CHOOSE_BOTH = "Choose one of --sample-used and --not-informed, not both. Nothing was done."
NOT_INDEPENDENT = ("Not independent: the configuration was changed after recall on {sample} was seen. Draw a fresh "
                   "sample for an independent estimate.")


class SampleChoiceNeeded(Exception):
    """A result set made after a look, with other settings, needs the researcher's choice before it is estimated (D8).
    Not a ValueError, so callers that skip unknown names do not swallow it."""

    def __init__(self, notice: str, sample: str = "", key: str = ""):
        super().__init__(notice)
        self.notice, self.sample, self.key = notice, sample, key


class LabelsIncomplete(Exception):
    """Recall on a probability sample is revealed only once every sampled record has a final label (D4 of
    label-completion-and-sealing); raised before anything is recorded. Not a ValueError, like SampleChoiceNeeded."""

    def __init__(self, notice: str, sample: str = "", completeness: dict | None = None):
        super().__init__(notice)
        self.notice, self.sample, self.completeness = notice, sample, completeness or {}


def incomplete_answer(e: LabelsIncomplete, protocol: str) -> dict:
    """What ``GET /api/recall`` and ``wider recall --json`` answer for :class:`LabelsIncomplete` (D4): the notice and
    the completeness counts, without the id lists."""
    from ..sample.labels import completeness_counts

    return {"labels_incomplete": True, "notice": e.notice, "completeness": completeness_counts(e.completeness),
            "sample": e.sample, "protocol": protocol}


def pending_line(sample: str, n: int) -> str:
    return f"Recall on {sample} for set {n} is not given until you choose: sample used or not informed."


def _looks(project, sample: str):
    return project.store.query("SELECT l.* FROM validation_looks l JOIN samples s ON s.sample_id=l.sample_id "
                               "WHERE s.name=? AND s.kind='probability' ORDER BY l.first_at, l.look_id", (sample,))


def needs_choice(project, sample: str, key: str) -> str | None:
    """The notice when set ``key`` was made after the first look on ``sample``, its settings hash matches no set
    looked at there (a set without one never matches) and no choice is recorded for it; else None."""
    import json

    from ..screen.result_sets import changed_fields, row_by_key, split_key

    rows = _looks(project, sample)
    own = [r for r in rows if r["result_set_key"] == key]
    if own and (own[0]["n"] or own[0]["choice"]):
        return None
    seen = [r for r in rows if r["n"] and r["result_set_key"] != key]
    srow = row_by_key(project, key) or {}
    # both stamps are to the second: a set made in the second of the first look counts as made before it (a result
    # cannot be seen and a new screening run finished within one second, while a set and an estimate made by one
    # script can share it)
    if not seen or not srow or (srow.get("created_at") or "") <= seen[0]["first_at"]:
        return None
    h = srow.get("settings_hash")
    if h and h in {r["settings_hash"] for r in seen}:
        return None
    proto, n = split_key(key)
    if h:
        def _s(row):
            v = (row or {}).get("settings")
            try:
                return json.loads(v) if isinstance(v, str) else v
            except ValueError:
                return None
        newest = row_by_key(project, seen[-1]["result_set_key"])
        fields = changed_fields(_s(newest), _s(srow)) or ["settings"]
        how = f"its settings differ ({', '.join(fields)})"
    else:
        how = "its settings are unknown (imported)"
    return (f"Set {n} of {proto} was made after recall on {sample} was seen ({_day(seen[0]['first_at'])}), and {how}. "
            "Sample used: this estimate is marked not independent, and a fresh sample is recommended. Not informed: "
            "you state the change was not informed by the validation result; every output says so. The choice is final.")


def record_choice(project, sample: str, key: str, choice: str, author: str | None = None) -> bool:
    """Record ``choice`` (``used`` | ``not_informed``) for (sample, set) when it is needed; True when recorded. The
    choice is final: a permanent ``sample_used`` / ``sample_not_informed`` History entry (D8)."""
    from ..core.journal import _store_change, current_author
    from ..screen.result_sets import row_by_key

    if choice not in CHOICES:
        raise ValueError(f"unknown choice {choice!r}: used or not_informed")
    if needs_choice(project, sample, key) is None:
        return False
    st, now, who = project.store, _now(), current_author(author)
    sid = st.scalar("SELECT sample_id FROM samples WHERE name=?", (sample,))
    srow = row_by_key(project, key) or {}
    if st.query("SELECT 1 FROM validation_looks WHERE sample_id=? AND result_set_key=?", (sid, key)):
        st.execute("UPDATE validation_looks SET choice=?, choice_by=?, choice_at=? WHERE sample_id=? AND "
                   "result_set_key=?", (choice, who, now, sid, key))
    else:   # n=0 until the estimate that follows records the look
        st.execute("INSERT INTO validation_looks(sample_id,result_set_key,settings_hash,first_at,last_at,n,choice,"
                   "choice_by,choice_at) VALUES(?,?,?,?,?,0,?,?,?)",
                   (sid, key, srow.get("settings_hash"), now, now, choice, who, now))
    kind = "sample_used" if choice == "used" else "sample_not_informed"
    words = "used" if choice == "used" else "not informed by the result"
    _store_change(project, kind, f"{sample}: result set {key} recorded as {words}",
                  [{"t": "sample_choice", "sample": sample, "result_set_key": key, "choice": choice}], who, None)
    return True


def choice_lines(project, sample: str, key: str) -> list[str]:
    """The not-independent line (a "used" choice on this set or an earlier one) and this set's not-informed statement."""
    from ..screen.result_sets import split_key

    rows = _looks(project, sample)
    seen = [r for r in rows if r["n"]]

    def made(k):   # sets in the order they were made (their stamps are to the second)
        r = project.store.query("SELECT created_at, rowid FROM screen_result_sets WHERE key=?", (k,))
        return (r[0]["created_at"] or "", r[0]["rowid"]) if r else ("", 0)
    when = made(key)
    out = []
    for r in rows:
        if r["choice"] == "used" and made(r["result_set_key"]) <= when:
            out.append(NOT_INDEPENDENT.format(sample=sample))
            break
    own = [r for r in rows if r["result_set_key"] == key and r["choice"] == "not_informed"]
    if own:
        proto, n = split_key(key)
        first = min((r["first_at"] for r in seen if r["result_set_key"] != key), default=own[0]["choice_at"])
        out.append(f"The researcher ({own[0]['choice_by'] or 'unknown'}) states that set {n} of {proto} was not "
                   f"informed by the recall result on {sample} seen on {_day(first)}.")
    return out


def _now() -> str:
    from datetime import datetime

    return datetime.now(UTC).isoformat(timespec="seconds")


def model_selection_line(project, sample: str) -> str | None:
    """The line for looks on one sample at result sets with two or more settings (D7)."""
    rows = project.store.query("SELECT l.result_set_key, l.settings_hash FROM validation_looks l JOIN samples s "
                               "ON s.sample_id=l.sample_id WHERE s.name=? AND l.n>0 ORDER BY l.look_id", (sample,))
    # only looks seen, and a set without a settings hash (an import) is its own setting, as in needs_choice
    hashes = {r["settings_hash"] or ("set", r["result_set_key"]) for r in rows}
    if len(rows) < 2 or len(hashes) < 2:
        return None
    from ..screen.result_sets import split_key

    sets = ", ".join(str(split_key(r["result_set_key"])[1]) for r in rows)
    how = "different settings" if all(r["settings_hash"] for r in rows) else "different or unknown settings"
    return (f"Recall on {sample} was seen for {len(rows)} result sets with {how} (sets {sets}); "
            "choosing between them on this sample uses it for model selection.")


def looks_before_line(project) -> str | None:
    return LOOKS_BEFORE if project.store.get_meta("recall_looks_before_v7") is not None else None


# ---------------------------------------------------------------------- exact bounds at a look, and stop (D15, D16)
FIRST_DRAW = ("The first draw is systematic and is treated as random within each band; the guarantee at every look "
              "assumes a random draw (an open question).")
CONSERVATIVE = ("The bounds are conservative: the error is split over every band and screening decision and each is "
                "bounded on its own, so they may need substantially more labels than a sharper method to show the same "
                "result.")
# the sentence stored with looks made before recall-certification-workload; still matched in a report from them
CONSERVATIVE_OLD = ("The bounds are conservative: the error is split over every band and screening decision, so they "
                    "need more labels than a single exact test.")
NOTHING_TO_STOP = "no recall result on {sample} yet; nothing to stop"


def _n(x) -> str:
    return f"{int(x):,}"


def exact_cells(project, sample: str, key: str, primary: str | None = None) -> list[dict] | None:
    """The cells of :func:`wider.recall.anytime.recall_bounds` ({band, decision, N, t, k}) for ``sample`` under result
    set ``key``, from :func:`wider.sample.plan.sample_cells`; None for a sample without a ranked frame."""
    from ..sample.plan import sample_cells

    sc = sample_cells(project, sample, key, primary)
    if sc is None:
        return None
    return [{"band": b["name"], "decision": c, "N": v["N"], "t": v["n"], "k": v["a"]}
            for b in sc["bands"] for c, v in b["cells"].items()]


def method_lines(bounds: dict, n_cells: int) -> list[str]:
    """The method sentences of the exact bounds (D15): what they are, what they rest on, the dominant band."""
    level = f"{round(bounds['level'] * 100)}%"
    out = [f"Exact bounds valid at every look ({level}): the relevant records of each band's kept and not-kept "
           "records are bounded with a confidence sequence for sampling without replacement (Waudby-Smith and Ramdas "
           f"2020), the error split equally over the {n_cells} cells; the guarantee holds per result set and recall "
           "variant, whatever is added between looks.", FIRST_DRAW, CONSERVATIVE]
    d = bounds.get("dominant")
    if d and d["unseen"] > 0:
        out.append(f"Band {d['band']} ({_n(d['N'])} records not kept, {d['k']} of {_n(d['t'])} labelled relevant) leaves "
                   f"up to {_n(d['unseen'])} relevant papers unseen among its records not kept; its share of the bound is "
                   f"{round(d['share'] * 100)}%.")
    return out


def nothing_excluded_lines(project, key: str, estimate_out: dict) -> list[str]:
    """Beside the verdict when the screening excluded no record of the set (inspection 1 Nov 2026): every record is
    kept, so a lenient recall of 1 and a met target say nothing about the screening; strict recall says more."""
    from ..screen.decisions import summary

    r = summary(project, key)   # counts only, as the estimate reads them
    if not r["n"] or r.get("exclude"):
        return []
    strict = (estimate_out["variants"].get("strict") or {}).get("recall")
    tail = f" Strict recall (includes only) is {strict:.3f}." if strict is not None else ""
    return [f"The screening excluded none of the {_n(r['n'])} records of this set: every record is kept as include or "
            "maybe, so recall that counts maybes as kept is high by construction and the screening saved no reading."
            + tail]


def zero_band_lines(bounds: dict) -> list[str]:
    """One line per sampled band that shows no relevant paper among its labelled records (D17)."""
    out = []
    for b in dict.fromkeys(c["band"] for c in bounds["cells"]):
        cs = [c for c in bounds["cells"] if c["band"] == b]
        t, k = sum(c["t"] for c in cs), sum(c["k"] for c in cs)
        if t > 0 and k == 0:
            out.append(f"Band {b} shows no relevant paper in {_n(t)} labelled records of {_n(sum(c['N'] for c in cs))}; "
                       f"it may still hold up to {_n(sum(c['high'] for c in cs))} relevant papers (exact bound), "
                       "counting its kept and not-kept records together.")
    return out


def look_block(project, sample: str, key: str, look: dict | None, estimate_out: dict) -> dict | None:
    """The exact bounds, outcome and words at a look (D15, D5, D17), for a probability sample with a ranked frame:
    ``{"bounds", "outcome", "lines"}``, or None. The verdict reads the exact bounds only; the model interval is a
    supplement shown where the design-based interval is missing."""
    from .anytime import recall_bounds
    from .uncertainty import model_interval, model_interval_line

    rp = project.config.protocol.recall
    cells = exact_cells(project, sample, key)
    if not cells:
        return None
    b = recall_bounds(cells, rp.ci_level)
    pv = estimate_out["variants"].get(rp.primary_variant) or {}
    primary = {"estimate": pv.get("recall"), "low": b["low"], "high": b["high"]}
    rule_est = None
    if rp.low_recall_rule == "maybes-as-includes" and rp.primary_variant == "strict":
        lc = exact_cells(project, sample, key, "lenient")
        if lc:
            lb = recall_bounds(lc, rp.ci_level)
            rule_est = {"estimate": (estimate_out["variants"].get("lenient") or {}).get("recall"),
                        "low": lb["low"], "high": lb["high"]}
    if rule_est and rule_est["estimate"] is None:
        rule_est = None
    index = (look or {}).get("index") or 1
    res = outcome(primary, project.config.protocol, index, rule_est, sample)
    lines = list(res["lines"]) + nothing_excluded_lines(project, key, estimate_out) + zero_band_lines(b) \
        + method_lines(b, len(cells))
    st = project.store
    sid = st.scalar("SELECT sample_id FROM samples WHERE name=?", (sample,))
    n_rows = int(st.scalar("SELECT COUNT(*) FROM sample_rows WHERE sample_id=?", (sid,)) or 0)
    left = n_rows - sum(c["t"] for c in cells)
    if pv.get("interval") is None:
        m = model_interval(cells, rp.ci_level, seed=project.config.seed)
        b["model_interval"] = m
        lines.append(model_interval_line(m))
    cont = continue_lines(project, sample)
    lines += extension_lines(project, sample) + cont + review_lines(project, sample)
    stopped = any(c.startswith("Validation was stopped") for c in cont)
    sg = None
    if res.get("more_validation") and look is not None and not stopped:
        from ..sample.plan import suggest

        sg = suggest(project, sample, key, look)
        if sg:
            lines += sg.get("lines", [])
    return {"bounds": b, "outcome": res, "look": {"index": index}, "lines": lines, "suggestion": sg,
            "stopped": stopped, "unlabelled": max(left, 0)}


# the second-reviewer items of a sample: the blind queue's and those of second-mode review packages (blinding D7)
SECOND_ITEMS = ("SELECT record_id, reviewer FROM queue_items WHERE sample_id=? AND mode='second' UNION "
                "SELECT i.record_id, p.reviewer FROM review_package_items i JOIN review_packages p "
                "ON p.package_id=i.package_id WHERE i.sample_id=? AND p.mode='second'")


def rechecks(project, sample: str) -> list[dict]:
    """The re-check revisions of a sample; a review-package re-review is not a re-check (blinding D7)."""
    from ..sample import labels as lb
    return [r for r in lb.revisions(project, sample) if (r.get("cause") or "recheck") == "recheck"]


def review_lines(project, sample: str) -> list[str]:
    """The re-check count and the second-reviewer recommendation on validation includes and maybes (D11, D12)."""

    st = project.store
    sid = st.scalar("SELECT sample_id FROM samples WHERE name=?", (sample,))
    if sid is None:
        return []
    out = []
    revs = rechecks(project, sample)
    if revs:
        v = revs[-1]["new_codebook_version"]
        changed = sum(r["old_decision"] != r["new_decision"] for r in revs)
        out.append(f"{len(revs)} labels re-checked under v{v}; {changed} changed")
    first = st.query("SELECT l.record_id, l.reviewer_id, l.decision FROM labels l WHERE l.sample_id=? AND l.label_id="
                     "(SELECT MIN(label_id) FROM labels m WHERE m.sample_id=l.sample_id AND m.record_id=l.record_id)",
                     (sid,))
    im = [r for r in first if r["decision"] in ("include", "maybe")]
    if not im:
        return out
    have = sum(1 for r in im if st.scalar("SELECT 1 FROM labels WHERE sample_id=? AND record_id=? AND reviewer_id<>?",
                                          (sid, r["record_id"], r["reviewer_id"])))
    if have < len(im):
        out.append("A second reviewer is recommended on validation includes and maybes: recall is only as good as "
                   f"the labels. {have} of {len(im)} have one.")
    else:
        ex = {r["record_id"] for r in first if r["decision"] == "exclude"}
        k = len({r["record_id"] for r in st.query(SECOND_ITEMS, (sid, sid))} & ex)
        out.append(f"{have} of {len(im)} have a second reviewer" + (f" (queue enriched with {k} excludes)" if k else ""))
    return out


def extension_lines(project, sample: str) -> list[str]:
    """The scope lines of the development sample and one sentence per extension made after a look (D6)."""
    import json

    from ..sample.design import AFTER_LOOK, scope_lines

    d = project.store.scalar("SELECT design FROM samples WHERE name=?", (sample,))
    exts = json.loads(d or "{}").get("extensions", [])
    out = scope_lines(project, sample)
    days = sorted({e["at"][:10] for e in exts if e.get("after_look")})
    out += [AFTER_LOOK.format(name=sample, day=x) for x in days]
    return out


def _stops(project, sample: str) -> list[dict]:
    st = project.store
    sid = st.scalar("SELECT sample_id FROM samples WHERE name=?", (sample,))
    return [dict(r) for r in st.query("SELECT at, author, look, outcome, note FROM validation_stops WHERE sample_id=? "
                                      "ORDER BY stop_id", (sid,))] if sid is not None else []


def continue_lines(project, sample: str) -> list[str]:
    """"Validation was continued on DATE after it was stopped on DATE." for a look after the last stop (D16)."""
    stops = _stops(project, sample)
    if not stops:
        return []
    st = project.store
    sid = st.scalar("SELECT sample_id FROM samples WHERE name=?", (sample,))
    after = st.scalar("SELECT MIN(first_at) FROM validation_look_states WHERE sample_id=? AND look_index>?",
                      (sid, stops[-1]["look"] or 0))
    import json
    exts = json.loads(st.scalar("SELECT design FROM samples WHERE sample_id=?", (sid,)) or "{}").get("extensions", [])
    ext_after = [e["at"] for e in exts if e.get("at", "") > stops[-1]["at"]]
    if ext_after and (not after or min(ext_after) < after):
        after = min(ext_after)
    if not after:
        return [f"Validation was stopped on {_day(stops[-1]['at'])} at look {stops[-1]['look']}."]
    return [f"Validation was continued on {_day(after)} after it was stopped on {_day(stops[-1]['at'])}."]


def stop(project, sample: str | None = None, note: str | None = None, author: str | None = None) -> dict:
    """Record the decision to stop validation on ``sample`` at its latest look (D16): a ``validation_stops`` row and a
    permanent ``validation_stop`` History entry. Refused before any look."""
    sample = sample or project.config.protocol.sampling.probability_sample
    import json

    from ..core.journal import _store_change, current_author

    st = project.store
    sid = st.scalar("SELECT sample_id FROM samples WHERE name=?", (sample,))
    if sid is None:
        raise ValueError(f"unknown sample {sample!r}")
    last = st.one("SELECT look_index, labels_checksum FROM validation_look_states WHERE sample_id=? "
                  "ORDER BY look_index DESC LIMIT 1", (sid,))
    if last is None:
        raise ValueError(NOTHING_TO_STOP.format(sample=sample))
    meta = json.loads(st.get_meta(f"last_validation_lines:{sample}") or "{}")
    res = (meta.get("outcome") or {}).get("outcome")
    now, who = _now(), current_author(author)
    note = (note or "").strip() or None
    st.execute("INSERT INTO validation_stops(sample_id,at,author,look,outcome,note) VALUES(?,?,?,?,?,?)",
               (sid, now, who, int(last["look_index"]), res, note))
    _store_change(project, "validation_stop", f"validation on {sample} stopped at look {last['look_index']}"
                  + (f": {OUTCOME_WORDS.get(res, res)}" if res else "") + (f" ({note})" if note else ""),
                  [{"t": "validation_stop", "sample": sample}], who, None)
    line = (f"Validation of {sample} stopped at look {last['look_index']}"
            + (f", outcome {OUTCOME_WORDS.get(res, res)}" if res else "") + f", by {who}.")
    return {"sample": sample, "look": int(last["look_index"]), "outcome": res, "author": who, "note": note,
            "lines": [line]}


# ------------------------------------------------------------------------- benchmark papers apart and scope (D13)
APART_NOTE = "Known papers, not a random sample; not pooled into the estimate."
HIDDEN_SET = "Validation seed papers: hidden until the query is frozen"
APART_NAMES = {"Development": "Development", "Validation": "Validation", "used": "Validation (used)"}


def benchmark_apart(project, key: str, level: float | None = None) -> dict:
    """The benchmark papers in the corpus per set (Development; Validation only while shown, as ``_coverage_by_set``;
    the used Validation papers apart, not independent) with the result set ``key``'s decision on their records. The
    counts cover every paper of a set, so this is not a look; papers from the query's results are left out."""
    from ..core.matching import CorpusRecord, locate_benchmark
    from ..screen.decisions import decisions as screen_decisions
    from ..search.coverage import NOT_COUNTED, USED, set_members, validation_unlocked
    from .uncertainty import interval_words, recall_uncertainty

    level = project.config.protocol.recall.ci_level if level is None else level
    papers = project.read_benchmark()
    shown = validation_unlocked(project) or not project.config.protocol.identification.validation_after_freeze
    members = [(g, b) for g, b in set_members(project, shown, papers) if g != NOT_COUNTED]
    corpus = [CorpusRecord(str(x["record_id"]), x["title"] or "", x["doi"])
              for x in project.store.query("SELECT record_id,title,doi FROM records")]
    rec = {m.benchmark_id: int(m.record_id) for m in locate_benchmark(
        [(b.benchmark_id, b.title, b.doi) for _, b in members], corpus, project.config.thresholds.jw_benchmark)
        if m.record_id}
    dec = screen_decisions(project, key)
    sets, lines = [], []
    for g in ("Development", "Validation", USED):
        if g == "Validation" and not shown:
            if any(b.set == "Validation" for b in papers):
                sets.append({"set": "Validation", "hidden": True})
                lines.append(HIDDEN_SET)
            continue
        ids = [rec[b.benchmark_id] for gg, b in members if gg == g and b.benchmark_id in rec]
        if not ids:
            continue
        c = {k: sum(1 for r in ids if dec.get(r, "unscreened") == k) for k in ("include", "maybe", "exclude")}
        c["unscreened"] = len(ids) - sum(c.values())
        k = c["include"] + c["maybe"]
        u = recall_uncertainty(k, len(ids), level=level)
        name = APART_NAMES["used" if g == USED else g]
        parts = [f"{c['include']} include", f"{c['maybe']} maybe", f"{c['exclude']} excluded"]
        if c["unscreened"]:
            parts.append(f"{c['unscreened']} unscreened")
        lead = "Not independent: these papers were seen. " if g == USED else ""
        lines.append(f"{name} seed papers: {k} of {len(ids)} kept ({', '.join(parts)}), {interval_words(u)}. "
                     f"{lead}{APART_NOTE}")
        sets.append({"set": name, "n": len(ids), "kept": k, **c, "interval": list(u["interval"]) if u["interval"] else None,
                     "independent": g != USED})
    return {"sets": sets, "lines": lines}


def _join_or(parts: list[str]) -> str:
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + ", or " + parts[-1]


def scope_line(project, sample: str) -> str | None:
    """"This estimate covers N ranked records (not ...), screened on title and abstract only; ..." from the draw's
    design log (D13); None for a sample that is not a probability sample."""
    import json

    s = project.store.one("SELECT kind, design FROM samples WHERE name=?", (sample,))
    if s is None or s["kind"] != "probability":
        return None
    d = json.loads(s["design"] or "{}")
    if "frame" not in d:
        return None
    nots = []
    if d.get("thin_excluded"):
        nots.append(f"the {d['thin_excluded']} records with too short an abstract")
    if d.get("benchmark_excluded"):
        nots.append(f"the {d['benchmark_excluded']} seed papers")
    if d.get("excluded_used"):
        nots.append(f"the {d['excluded_used']} records of {', '.join(d.get('chain') or [d.get('fresh_from')])} (used)")
    if d.get("development_excluded"):
        nots.append(f"the {d['development_excluded']} development records")
    paren = f" (not {_join_or(nots)})" if nots else ""
    return (f"This estimate covers {d['frame']} ranked records{paren}, screened on title and abstract only; it says "
            "nothing about full-text screening.")


def used_sample_lines(project, sample: str, key: str, primary: str | None = None) -> list[str]:
    """One line per sample ``sample`` was drawn fresh from (D9): its records, relevant and kept by this set, apart."""
    import json

    from ..sample.design import fresh_chain
    from .estimate import VARIANTS, _rows

    prev = json.loads(project.store.scalar("SELECT design FROM samples WHERE name=?", (sample,)) or "{}").get("fresh_from")
    if not prev:
        return []
    relevant, kept = VARIANTS[primary or project.config.protocol.recall.primary_variant]
    out = []
    for used in fresh_chain(project, prev):
        n = project.store.scalar("SELECT COUNT(*) FROM sample_rows sr JOIN samples s ON s.sample_id=sr.sample_id "
                                 "WHERE s.name=?", (used,))
        rows = _rows(project, used, key, primary)
        rel = [r for r in rows if r["human"] in relevant]
        k = sum(1 for r in rel if r["screen"] in kept)
        out.append(f"The {n} records of {used} (used) are not in this estimate: {len(rel)} relevant, {k} kept by this "
                   "set. Not independent; not pooled.")
    return out


# ------------------------------------------------------------------------------- the technical report (D14)
NO_PLAN = "No validation plan was used."


def open_result_line(project, sample: str | None, rec: dict | None) -> str | None:
    """The sentence for an inconclusive last outcome (recall-certification-workload D6): validation ended there after
    a stop at or after that look that was not continued since (spec-checker F1), or the result is inconclusive so far;
    None for met, not met or no look."""
    rec = rec or {}
    oc, b = rec.get("outcome") or {}, rec.get("bounds") or {}
    target = project.config.protocol.recall.target
    if not sample or oc.get("outcome") != "cannot_tell" or b.get("low") is None or target is None:
        return None
    st = project.store
    sid = st.scalar("SELECT sample_id FROM samples WHERE name=?", (sample,))
    look = st.scalar("SELECT MAX(look_index) FROM validation_look_states WHERE sample_id=?", (sid,)) if sid else None
    if look is None:
        return None
    level = f"{round((b.get('level') or project.config.protocol.recall.ci_level) * 100)}%"
    between = (f"recall lies between {_f2(b['low'])} and {_f2(b['high'])} (exact bounds valid at every look, {level})")
    continued = any(x.startswith("Validation was continued") for x in continue_lines(project, sample))
    if not continued and any((s.get("look") or 0) >= look for s in _stops(project, sample)):
        return (f"Validation ended at look {look} with an inconclusive result: {between}; the target {_f2(target)} was "
                "neither shown met nor shown missed.")
    return (f"At look {look} the result is inconclusive: {between}; the target {_f2(target)} has been neither shown "
            "met nor shown missed.")


def stop_line(s: dict) -> str:
    note = f" Note: {s['note']}." if s.get("note") else ""
    out = OUTCOME_WORDS.get(s["outcome"], (s["outcome"] or "none").replace("_", " "))   # an old stop row too
    return f"Validation stopped on {_day(s['at'])} at look {s['look']}; outcome: {out}.{note}"


def validation_checks(project, sample: str | None, rec: dict | None = None) -> dict:
    """``validation_checks`` of report.json and the lines of "Recall: method" (D14), in the spec's order: the plan, the
    target and rule with when set, the method of the bounds, extensions, stops, looks and choices, label counts by
    criteria state, re-checks and second-reviewer coverage; ``model_interval`` and ``outcome.basis`` from ``rec``."""
    import json

    from ..sample import labels as lb

    st, rc = project.store, project.config.protocol.recall
    rec = rec or {}
    plan = json.loads(st.get_meta("validation_plan") or "null")
    lines = []
    if plan:
        rec_txt = f"; recommended: {plan['recommended']}" if plan.get("recommended") else ""
        lines.append(f"Validation plan: used: {plan['used']}{rec_txt}.")
    else:
        lines.append(NO_PLAN)
    target = {"target": rc.target, "rule": rc.low_recall_rule, "depth": rc.top_strata_depth,
              "lines": [status_line(project)] + timing_lines(project)}
    lines += target["lines"]
    method = [x for x in rec.get("validation_lines") or []
              if x.startswith("Exact bounds") or x in (FIRST_DRAW, CONSERVATIVE, CONSERVATIVE_OLD)]
    lines += method
    out = {"plan": plan, "target": target, "method": method, "extensions": [], "stops": [], "looks": [],
           "criteria": None, "assessment": None, "revisions": [], "rechecked": 0, "revised": 0,
           "second_reviewer": None, "model_interval": (rec.get("bounds") or {}).get("model_interval"),
           "outcome_basis": (rec.get("outcome") or {}).get("basis"), "lines": lines}
    sid = st.scalar("SELECT sample_id FROM samples WHERE name=?", (sample,)) if sample else None
    if sid is None:
        return out
    d = json.loads(st.scalar("SELECT design FROM samples WHERE sample_id=?", (sid,)) or "{}")
    out["extensions"] = d.get("extensions", [])
    for e in out["extensions"]:
        after = ", after a look" if e.get("after_look") else ""
        lines.append(f"{sample} extended on {_day(e.get('at'))} by {e.get('n') or sum((e.get('bands') or {}).values())} records{after}.")
    out["stops"] = _stops(project, sample)
    lines += [stop_line(s) for s in out["stops"]]
    orl = open_result_line(project, sample, rec)
    if orl:
        lines.append(orl)
    out["looks"] = [dict(r) for r in st.query(
        "SELECT look_index, first_at, level, set_key FROM validation_look_states WHERE sample_id=? ORDER BY look_index",
        (sid,))]
    lines += [f"Look {x['look_index']} on {_day(x['first_at'])}." for x in out["looks"]]
    changed = lb.sealed_change_line(project, sample)   # sealed references corrected since (D6)
    if changed:
        lines.append(changed)
    dis = lb.sealed_disagreement_line(project, sample)   # recall-final-check-fixes D6
    if dis:
        lines.append(dis)
    lines += population_lines(project, sample, json.loads(st.get_meta(f"last_validation_lines:{sample}") or "{}"))
    out["choices"] = [dict(r) for r in st.query(
        "SELECT result_set_key, first_at, choice, choice_by, choice_at FROM validation_looks WHERE sample_id=? "
        "AND choice IS NOT NULL ORDER BY look_id", (sid,))]
    lines += [f"Result set {c['result_set_key']}: {c['choice'].replace('_', ' ')}, chosen by {c['choice_by'] or 'unknown'} "
              f"on {_day(c['choice_at'])}." for c in out["choices"]]
    cc = lb.criteria_counts(project, sample)
    out["criteria"] = {k: cc[k] for k in ("n", "before", "after", "not_recorded", "after_look", "rechecked")}
    lines.append(f"Labels: {cc['n']}; before the criteria were frozen: {cc['before']}; after: {cc['after']}; "
                 f"not recorded: {cc['not_recorded']}; saved after a look: {cc['after_look']}.")
    a = lb.latest_assessment(project, sample)
    if a:
        out["assessment"] = {k: a.get(k) for k in ("substantive", "scope", "recommended", "n_affected",
                                                   "n_unidentifiable", "note", "author", "at")}
        lines.append(f"Criteria change assessed on {_day(a['at'])} by {a.get('author') or 'unknown'}: substantive "
                     f"{a['substantive']}, re-check scope {a['scope']}.")
    revs = rechecks(project, sample)
    out["revisions"] = [{k: r[k] for k in ("record_id", "reviewer", "old_decision", "new_decision",
                                            "old_codebook_version", "new_codebook_version", "at")} for r in revs]
    out["rechecked"] = len(revs)
    out["revised"] = sum(r["old_decision"] != r["new_decision"] for r in revs)
    lines += review_lines(project, sample)
    out["second_reviewer"] = second_reviewer_coverage(project, sid)
    return out


def second_reviewer_coverage(project, sid: int | None) -> dict | None:
    """{n, done}: the second-reviewer items of a sample (blind queue and second-mode review packages) and how many have
    that reviewer's label; None without any."""
    if sid is None:
        return None
    q = project.store.query(f"SELECT DISTINCT q.record_id, q.reviewer, EXISTS (SELECT 1 FROM labels l WHERE l.sample_id=? "
                            f"AND l.record_id=q.record_id AND l.reviewer_id=q.reviewer) done FROM ({SECOND_ITEMS}) q",
                            (sid, sid, sid))
    return {"n": len(q), "done": sum(r["done"] for r in q)} if q else None
