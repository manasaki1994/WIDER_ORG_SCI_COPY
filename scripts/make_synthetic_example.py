#!/usr/bin/env python3
"""Generate examples/synthetic_project: about 80 invented records on three fake platforms, with planted duplicates,
a few thin abstracts, a benchmark set of invented seed papers and hand-written artifacts, so the whole pipeline
runs offline with the fake provider and fake embeddings.

The example construct is **employee voice and silence**: a broad, multi-vocabulary construct (voice, speaking up,
whistleblowing, issue selling, upward communication, dissent, employee silence) with adjacent literatures that the
same search terms retrieve (participation and industrial democracy, grievance procedures, organisational citizenship
behaviour, psychological safety). Every title, abstract and author below is invented; no real paper is quoted.
"""
from __future__ import annotations

import importlib.util
import random
import sys
from pathlib import Path

import yaml

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "examples" / "synthetic_project"
rng = random.Random(7)

# Words that mark an on-topic record in the synthetic corpus. The e2e test's oracle and the near-miss records
# are written against this list: every TOPIC title contains one, no NEAR or OFF text contains any.
TOPIC_MARKERS = ("voice", "speaking up", "speak up", "silence", "whistleblow", "issue selling", "upward communication", "dissent")

TOPIC = [
    ("Speaking up to supervisors: antecedents of promotive voice in service teams",
     "employee voice speaking up supervisors promotive suggestions antecedents service teams leader openness survey"),
    ("Why employees stay silent: fear, futility and employee silence in hospitals",
     "employee silence withholding concerns fear futility nurses hospital upward communication antecedents interviews"),
    ("Whistleblowing intentions after observing accounting irregularities",
     "whistleblowing intentions reporting irregularities internal channels external regulator auditors antecedents vignette"),
    ("Issue selling by middle managers: framing and the uptake of proposals",
     "issue selling middle managers framing upward proposals top management uptake consequences field study"),
    ("Manager reactions to prohibitive voice: credit, blame and career outcomes",
     "voice behaviour prohibitive manager reactions performance ratings career consequences speaking up experiment"),
    ("Upward communication and error reporting in airline crews",
     "upward communication error reporting crew members captains speaking up safety consequences simulator"),
    ("Dissent in project teams: when disagreeing with the leader helps",
     "dissent disagreement project teams leader decision quality consequences voice panel data"),
    ("Does voice pay? Speaking up and promotion in a longitudinal sample",
     "employee voice promotion salary longitudinal speaking up consequences personnel records"),
    ("Silence after speaking up: the aftermath of unheeded suggestions",
     "employee silence after speaking up unheeded suggestions withdrawal consequences voice diary study"),
]
NEAR = [
    ("Works councils and firm productivity in manufacturing",
     "works councils participation scheme industrial democracy productivity firms codetermination establishment data"),
    ("Grievance filing and arbitration outcomes in unionised plants",
     "grievance procedure filing arbitration outcomes unionised plants formal process case records"),
    ("Organisational citizenship behaviour and unit performance: a meta-analysis",
     "organisational citizenship behaviour helping compliance unit performance meta-analysis composite scale"),
    ("Psychological safety climate and team learning in surgical units",
     "psychological safety climate team learning surgical units interpersonal risk survey"),
    ("Customer complaint handling and repurchase intentions",
     "customer complaint handling service recovery repurchase intentions consumers retail"),
    ("Citizen participation in municipal budgeting",
     "citizen participation municipal budgeting public administration deliberation residents"),
    ("Information sharing in distributed software teams",
     "information sharing distributed software teams knowledge exchange peers coordination"),
]
OFF = [
    ("Phytoplankton blooms and sea-surface temperature anomalies", "plankton ocean temperature bloom chlorophyll satellite"),
    ("A new catalyst for ammonia synthesis", "catalyst ammonia synthesis nitrogen pressure"),
    ("Soil carbon under no-till agriculture", "soil carbon tillage agriculture sequestration"),
    ("Deep learning for protein structure prediction", "protein structure prediction neural network folding"),
    ("Monetary policy transmission in small open economies", "monetary policy interest rates exchange rate inflation"),
    ("Urban heat islands and tree canopy", "urban heat island tree canopy temperature city"),
    ("Microplastics in freshwater fish", "microplastics freshwater fish contamination"),
]
THIN_ABSTRACTS = ["Conference abstract only.", "Abstract not available for this record.", "Summary to follow."]
SURNAMES = ["Smith", "Lee", "Garcia", "Okafor", "Novak", "Rossi", "Haddad", "Petrov", "Nakamura", "Bakker"]


def sentence_pad(core: str, n: int) -> str:
    words = core.split()
    out: list[str] = []
    while len(out) < n:
        out.extend(words)
        out.append(rng.choice(["Results", "The study", "Participants", "We find", "Implications", "Data"]))
    return " ".join(out[:n])


def record(title, abstract, year, doi, plat_style):
    authors = [f"{rng.choice(SURNAMES)}, {rng.choice('ABCDEF')}." for _ in range(rng.randint(1, 3))]
    journal = f"Journal of {rng.choice(['Work', 'Behaviour', 'Studies', 'Organisations'])}"
    lines = ["TY  - JOUR"]
    if plat_style == "proquest":
        lines += [f"T1  - {title}"] + [f"AU  - {a}" for a in authors] + [f"JF  - {journal}", f"Y1  - {year}-01-01",
                                                                       f"DO  - https://doi.org/{doi}" if doi else "", f"AB  - {abstract}" if abstract else ""]
    else:
        lines += [f"TI  - {title}"] + [f"AU  - {a}" for a in authors] + [f"T2  - {journal}", f"PY  - {year}",
                                                                       f"DO  - {doi}" if doi else "", f"AB  - {abstract}" if abstract else "", "KW  - synthetic"]
    lines += ["ER  -", ""]
    return "\n".join(ln for ln in lines if ln != "")


def build_pool() -> list[dict]:
    pool = []
    i = 0
    for group, kind in ((TOPIC, "topic"), (NEAR, "near"), (OFF, "off")):
        for title, core in group:
            for v in range(2):
                i += 1
                t = title if v == 0 else title + (" (extended)" if kind != "off" else " (replication)")
                thin = kind != "topic" and i % 9 == 4
                ab = rng.choice(THIN_ABSTRACTS) if thin else sentence_pad(core, rng.randint(40, 90)) + "."
                pool.append({"i": i, "title": t, "abstract": ab, "year": 2005 + (i * 7) % 20, "doi": f"10.9999/syn.{i:03d}",
                             "kind": kind, "thin": thin})
    return pool


def write_ris(pool: list[dict]) -> dict[str, list[dict]]:
    plats: dict[str, list[dict]] = {"alpha": [], "beta": [], "gamma": []}
    for rec in pool:
        for plat in plats:
            p_keep = 1.0 if (rec["kind"] == "topic" and plat == "alpha") else (0.7 if rec["kind"] == "topic" else 0.5)
            if rng.random() < p_keep:
                plats[plat].append(rec)
    # planted duplicates: the same paper on several platforms (above), copies without a DOI (below),
    # and one wrong-year copy with a trailing period in the title
    for name, recs in plats.items():
        style = "proquest" if name == "beta" else "default"
        texts = [record(r["title"], r["abstract"], r["year"], r["doi"] if rng.random() < 0.85 else "", style) for r in recs]
        if name == "gamma":
            r = pool[1]
            texts.append(record(r["title"] + ".", r["abstract"], r["year"] + 1, "", style))
        (OUT / "inputs" / "ris" / name / f"{name}_export.ris").write_text("\n".join(texts) + "\n", encoding="utf-8")
    return plats


def write_benchmark(pool: list[dict]) -> None:
    bench = [pool[0], pool[2], pool[4], pool[6], pool[8], pool[10]]  # the first six on-topic titles (v=0 variants)
    with open(OUT / "benchmark.csv", "w", encoding="utf-8") as f:
        f.write("benchmark_id,title,doi,set\n")
        for j, b in enumerate(bench, 1):
            f.write(f"B{j},\"{b['title']}\",{b['doi']},{'Development' if j <= 4 else 'Validation'}\n")


def anchor(text: str) -> str:
    return " ".join([text] * 2)


ANCHORS = {
    "voice_antecedent": anchor(
        "This study examines what leads employees to speak up with suggestions, concerns or ideas to their supervisor or "
        "senior managers rather than keep them to themselves. We compare individual, leader and climate predictors of "
        "promotive and prohibitive voice, issue selling and upward communication, measured through surveys, diaries and "
        "vignettes. The question is which conditions make employees more likely to raise issues upward."),
    "voice_consequence": anchor(
        "This study examines what happens after employees speak up: how supervisors and senior managers react to "
        "suggestions, concerns and proposals, and what follows for the speaker's performance ratings, reputation, career "
        "and well-being, and for the unit's decisions and improvement. We compare outcomes of voice that is heeded with "
        "voice that is ignored. The question is what the consequences of speaking up are."),
    "silence_antecedent": anchor(
        "This study examines why employees withhold concerns, information or opinions from people who could act on them. "
        "We compare motives for employee silence such as fear of consequences, a sense of futility, deference and the wish "
        "to protect colleagues, and the leader behaviours and climates that strengthen or weaken them. The question is "
        "what predicts employee silence."),
    "silence_consequence": anchor(
        "This study examines what follows when employees stay silent about problems, errors or ideas: effects on the "
        "individual's stress, engagement and intention to leave, and on the team's error detection, learning and "
        "performance. We compare units and periods with more and less employee silence. The question is what the "
        "consequences of withholding voice are."),
    "whistleblowing_antecedent": anchor(
        "This study examines what leads employees who observe irregularities, safety problems or rule breaking at work "
        "to report them to an internal authority, an external regulator or the public. We compare individual, situational "
        "and organisational predictors of whistleblowing intentions and actual reporting, including channel choice and "
        "anonymity. The question is which conditions make reporting more likely."),
    "whistleblowing_consequence": anchor(
        "This study examines what happens to employees after they report irregularities or safety problems to an "
        "internal or external authority: retaliation, career outcomes, well-being and the organisational response to the "
        "report, as well as whether the problem was corrected. We compare reporters who used different channels. The "
        "question is what the consequences of whistleblowing are."),
}

CELLS = {
    "construct": "Employee voice and silence: discretionary upward communication, or its withholding, of suggestions, concerns and problems by employees.",
    "dimensions": [
        {"name": "form", "values": [
            {"key": "voice", "label": "Voice", "definition": "An employee raises suggestions, concerns or proposals with someone inside the organisation who could act on them."},
            {"key": "silence", "label": "Silence", "definition": "An employee withholds concerns, information or opinions from people who could act on them."},
            {"key": "whistleblowing", "label": "Whistleblowing", "definition": "An employee reports irregularities or safety problems to an internal or external authority."}]},
        {"name": "focus", "values": [
            {"key": "antecedent", "label": "Antecedents", "definition": "What predicts whether the behaviour occurs."},
            {"key": "consequence", "label": "Consequences", "definition": "What follows for the employee, the receiver or the organisation once the behaviour has occurred."}]},
    ],
    "cells": [
        {"key": "voice_antecedent", "labels": {"form": "voice", "focus": "antecedent"}, "question": "what leads employees to speak up with suggestions or concerns", "benchmark_ids": ["B1"]},
        {"key": "voice_consequence", "labels": {"form": "voice", "focus": "consequence"}, "question": "how receivers react to voice and what follows for the speaker", "benchmark_ids": ["B4", "B5"]},
        {"key": "silence_antecedent", "labels": {"form": "silence", "focus": "antecedent"}, "question": "why employees withhold concerns or opinions", "benchmark_ids": ["B2"]},
        {"key": "silence_consequence", "labels": {"form": "silence", "focus": "consequence"}, "question": "what follows when employees stay silent"},
        {"key": "whistleblowing_antecedent", "labels": {"form": "whistleblowing", "focus": "antecedent"}, "question": "what leads employees to report irregularities to an authority", "benchmark_ids": ["B3"]},
        {"key": "whistleblowing_consequence", "labels": {"form": "whistleblowing", "focus": "consequence"}, "question": "what happens to employees after they report irregularities", "benchmark_ids": ["B6"]},
    ],
    "not_a_criterion": ["setting or industry", "occupation", "country", "research method"],
}

CODEBOOK = {
    "question": "What leads employees to speak up, stay silent or report problems upward or outward at work, and what follows when they do?",
    "target_description": "Empirical studies of employees who voluntarily raise suggestions, concerns or problems with someone who could act on them "
                          "(a supervisor, senior management, an internal or external authority), or who withhold such input, where the study measures "
                          "what predicts that behaviour or what follows from it.",
    "elements": [
        {"id": "e1", "name": "Voice, silence or reporting behaviour", "rule": "The study measures employees speaking up, withholding input, or reporting problems.",
         "counts": ["voice behaviour", "speaking up", "issue selling", "upward communication", "dissent", "employee silence", "whistleblowing"],
         "does_not_count": ["a climate or culture measure alone", "a composite citizenship scale in which voice is not analysed separately"]},
        {"id": "e2", "name": "Discretionary and directed upward or outward", "rule": "The behaviour is the employee's own choice and is directed at someone who could act on it.",
         "counts": ["informal suggestions to a supervisor", "reports to a regulator or hotline"],
         "does_not_count": ["formal participation schemes such as works councils", "grievance procedures as the only channel", "sharing information with peers only"]},
        {"id": "e3", "name": "Antecedents or consequences are studied", "rule": "The study relates the behaviour to predictors, reactions or outcomes.",
         "counts": ["predictors of voice or silence", "receiver reactions", "career, well-being or performance outcomes"],
         "does_not_count": ["scale development with no substantive relation tested"]},
        {"id": "e4", "name": "Employees in work organisations", "rule": "The actors are employees, members or contractors of an organisation.",
         "counts": ["employees", "nurses", "crew members", "students in a work placement"],
         "does_not_count": ["customers or consumers", "citizens addressing government", "patients"]},
    ],
    "exclusion_reasons": [
        {"code": "a", "text": "no voice, silence or reporting behaviour measured", "element": "e1"},
        {"code": "b", "text": "not discretionary or not directed at someone who could act", "element": "e2"},
        {"code": "c", "text": "no antecedent or consequence studied", "element": "e3"},
        {"code": "d", "text": "actors are not employees", "element": "e4"},
        {"code": "e", "text": "not empirical"},
        {"code": "f", "text": "too little information"},
    ],
    "adjacent_literatures": [
        {"name": "participation schemes and industrial democracy (works councils, codetermination)", "strains_element": "e2", "decision": "exclude", "reason_code": "b"},
        {"name": "grievance procedures as a formal channel", "strains_element": "e2", "decision": "maybe", "reason_code": "b", "note": "include when the decision to file is studied as the employee's choice"},
        {"name": "organisational citizenship behaviour composites", "strains_element": "e1", "decision": "maybe", "reason_code": "a", "note": "include when voice is analysed as a separate dimension"},
        {"name": "psychological safety as a climate construct", "strains_element": "e1", "decision": "maybe", "reason_code": "a", "note": "include when voice or silence is also measured"},
        {"name": "customer and consumer complaining", "strains_element": "e4", "decision": "exclude", "reason_code": "d"},
        {"name": "citizen participation and political voice", "strains_element": "e4", "decision": "exclude", "reason_code": "d"},
        {"name": "peer information sharing and team communication", "strains_element": "e2", "decision": "exclude", "reason_code": "b"},
    ],
    "checks": [
        {"key": "voice_subscale_only", "question": "Is voice only one sub-dimension of a broader scale that is not analysed separately?", "if_yes": "maybe"},
        {"key": "formal_scheme_only", "question": "Does the study examine only a formal participation scheme or procedure?", "if_yes": "maybe"},
    ],
    "edge_cases": [
        {"key": "non_empirical", "case": "reviews and conceptual papers on the topic", "default": "maybe"},
        {"key": "qualitative_only", "case": "qualitative studies with no measured antecedent or outcome", "default": "maybe"},
    ],
    "labels": [{"group": "target", "values": ["supervisor", "senior_management", "external_body", "peers"]}],
    "thin_abstract_rule": "A record with no usable abstract is maybe unless the title alone settles it.",
}

NEAR_MISS = {"near_miss": [
    {"key": "participation_schemes", "breaks": "e2", "decision": "exclude",
     "note": "formal representation, not the employee's own discretionary communication",
     "text": anchor("This study examines whether works councils and other formal participation schemes raise establishment productivity "
                    "and lower turnover. We compare firms with and without codetermination bodies using matched establishment data. "
                    "The question is whether formal representation improves firm outcomes.")},
    {"key": "grievance_procedures", "breaks": "e2", "decision": "maybe",
     "note": "a formal channel; include when filing is studied as the employee's choice",
     "text": anchor("This study examines grievance filing rates and arbitration outcomes in unionised plants. We compare plants by "
                    "procedure design, steward presence and case type, using case records over ten years. The question is what "
                    "shapes the volume and resolution of formal grievances.")},
    {"key": "citizenship_composite", "breaks": "boundary", "decision": "maybe",
     "note": "composite citizenship scales; include when the relevant dimension is analysed separately",
     "text": anchor("This study examines the relation between organisational citizenship behaviour and unit performance across "
                    "published studies. We compare helping, compliance and civic virtue as a composite and test moderators such "
                    "as industry and measurement source. The question is how strongly citizenship relates to performance.")},
    {"key": "psychological_safety", "breaks": "e1", "decision": "maybe",
     "note": "a climate construct; include when the behaviour itself is also measured",
     "text": anchor("This study examines psychological safety climate and team learning behaviour in surgical units. We compare "
                    "units by leader coaching and team tenure and relate the climate to learning and error rates. The question "
                    "is whether a safe interpersonal climate supports team learning.")},
    {"key": "customer_voice", "breaks": "e4", "decision": "exclude",
     "note": "customers, not employees",
     "text": anchor("This study examines how firms handle customer complaints and whether service recovery restores repurchase "
                    "intentions. We compare compensation, apology and speed of response in a retail setting using a scenario "
                    "experiment with consumers. The question is which recovery tactics retain complaining customers.")},
    {"key": "citizen_participation", "breaks": "e4", "decision": "exclude",
     "note": "citizens addressing government, not employees",
     "text": anchor("This study examines citizen participation in municipal budgeting and its effect on residents' trust in local "
                    "government. We compare municipalities with and without deliberative budgeting forums using panel surveys of "
                    "residents. The question is whether participatory budgeting builds trust.")},
    {"key": "peer_information_sharing", "breaks": "e2", "decision": "exclude",
     "note": "lateral exchange among peers, not directed at someone who could act",
     "text": anchor("This study examines information sharing among members of distributed software teams and its relation to "
                    "coordination quality. We compare teams by time-zone spread and tool use, using logs and surveys. The question "
                    "is what supports knowledge exchange between peers.")},
]}

# Hand-written search terms, so a build need not wait for a model to propose them; every TOPIC_MARKERS word is covered
SYNONYMS = {
    "blocks": [
        {"key": "voice", "name": "Speaking up, staying silent or reporting",
         "description": "Words for raising ideas, concerns or problems with someone who could act on them, or for keeping them back.",
         "role": "required",
         "terms": ["voice", "speaking up", "speak up", "spoke up", "upward communication", "issue selling", "selling issues",
                   "raising concerns", "dissent", "silence", "silent", "withhold*", "whistleblow*", "whistle-blow*",
                   "report* irregularities"],
         "fields": ["title", "abstract", "keywords"]},
        {"key": "work", "name": "At work",
         "description": "Words for the people and places of paid work.",
         "role": "required",
         "terms": ["employee*", "staff", "worker*", "workplace", "organisation*", "organization*", "manager*", "supervisor*", "subordinate*",
                   "nurse*", "team*"],
         "fields": ["title", "abstract", "keywords"]},
    ],
    "logic": {"and": [{"or": ["voice"]}, {"or": ["work"]}]},
    "notes": "Written by hand for the synthetic example (scripts/make_synthetic_example.py). A paper must use a word from "
             "each block.",
}


# The full text of seed paper B1 as a two-page PDF (invented text), so seed mining has a text to read. It is written
# when the example is generated and is not committed (no binary enters the repository; see .gitignore).
SEED_PDF = ("B1", (
    ["Journal of Invented Workplace Studies 2026",
     "Abstract",
     "We study speaking up to supervisors in service teams and the leader open-",
     "ness that invites it.",
     "Keywords: employee voice; speaking up; promotive voice",
     "Introduction",
     'Employee voice, also called upward voice, is the discretionary expression of ideas. The "prosocial voice" view',
     'treats it as help to the team; "prosocial voice" recurs in later work.'],
    ["Journal of Invented Workplace Studies 2026",
     "Discussion",
     "Leader openness remains open.",
     "References",
     "Abara, K. (2019). Promotive voice in service teams. Journal of Invented Workplace Studies, 12(3), 1-10.",
     "Bello, T. (2020). Speaking up and leader openness. Journal of Invented Workplace Studies, 13(1), 11-20.",
     "Corvin, D. (2021). Upward voice at scale. Proceedings on Invented Management, 4, 21-30."]))


def write_seed_pdf() -> Path:
    """Write seed_pdfs/B1.pdf with the fixture's PDF writer (pypdf, the dev extra)."""
    path = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "seeds" / "make_synthetic_pdf.py"
    spec = importlib.util.spec_from_file_location("make_synthetic_pdf", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    bid, pages = SEED_PDF
    return mod.make_pdf(OUT / "seed_pdfs" / f"{bid}.pdf", pages)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for d in ("inputs/ris/alpha", "inputs/ris/beta", "inputs/ris/gamma", "artifacts/cells", "artifacts/anchors", "artifacts/codebook", "artifacts/near_miss", "artifacts/synonyms"):
        (OUT / d).mkdir(parents=True, exist_ok=True)
    pool = build_pool()
    assert all(any(m in r["title"].lower() for m in TOPIC_MARKERS) for r in pool if r["kind"] == "topic")
    assert not any(any(m in (r["title"] + " " + r["abstract"]).lower() for m in TOPIC_MARKERS) for r in pool if r["kind"] != "topic")
    plats = write_ris(pool)
    write_benchmark(pool)
    write_seed_pdf()
    (OUT / "artifacts" / "anchors" / "v1.yaml").write_text(yaml.safe_dump({"style": "aims only", "anchors": ANCHORS}, width=110), encoding="utf-8")
    (OUT / "artifacts" / "cells" / "v1.yaml").write_text(yaml.safe_dump(CELLS, width=110, sort_keys=False), encoding="utf-8")
    (OUT / "artifacts" / "codebook" / "v1.yaml").write_text(yaml.safe_dump(CODEBOOK, width=110, sort_keys=False), encoding="utf-8")
    (OUT / "artifacts" / "near_miss" / "v1.yaml").write_text(yaml.safe_dump(NEAR_MISS, width=110, sort_keys=False), encoding="utf-8")
    (OUT / "artifacts" / "synonyms" / "v1.yaml").write_text(yaml.safe_dump(SYNONYMS, width=110, sort_keys=False), encoding="utf-8")
    n_records = sum(len(v) for v in plats.values()) + 1
    (OUT / "README.md").write_text(
        "# Synthetic example project\n\n"
        "An invented review of **employee voice and silence** (voice, speaking up, whistleblowing, issue selling, upward communication, "
        "dissent, employee silence), with adjacent literatures that the same search terms retrieve (participation schemes, grievance "
        f"procedures, citizenship behaviour, psychological safety). {n_records} invented records (no real papers, invented authors) on three "
        f"fake platforms with planted duplicates and {sum(r['thin'] for r in pool)} thin abstracts, a benchmark set of six invented seed papers "
        "and hand-written artifacts (cells, anchors, codebook, near-miss texts, synonyms). `tests/e2e` runs the whole pipeline on it with the fake "
        "provider and fake embeddings.\n\n"
        "The RIS files under `inputs/` and the seed PDF `seed_pdfs/B1.pdf` (the invented full text of B1) are not committed "
        "(the repository ignores bibliographic exports and PDFs); regenerate them with "
        "`python3 scripts/make_synthetic_example.py`.\n", encoding="utf-8")
    print("wrote", OUT, "records:", n_records, "unique:", len(pool), "thin:", sum(r["thin"] for r in pool))


if __name__ == "__main__":
    main()
