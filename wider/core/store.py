"""SQLite store (WAL) for everything mutable in a review project. One writer path for CLI and UI."""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from datetime import UTC
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 12

# Tables added by schema 5 (the query refinement loop and its two follow-ups); part of SCHEMA and run again by
# _migrate_v5, so a new store and an upgraded one have the same tables.
V5_TABLES = """
-- every unlock of the frozen query or of final uploads; never undone (what was seen before the query changed)
CREATE TABLE IF NOT EXISTS query_unlocks (
  unlock_no INTEGER PRIMARY KEY, cause TEXT NOT NULL, from_version INTEGER, new_version INTEGER, frozen_no INTEGER,
  by TEXT, at TEXT NOT NULL, seen_json TEXT, final_before TEXT, counts_as_seen INTEGER NOT NULL DEFAULT 1,
  n_shown INTEGER NOT NULL DEFAULT 0,   -- the papers in the validation set at that moment (the note's k)
  seen_choice TEXT   -- the researcher's choice: count | keep; NULL when nothing was asked (no paper shown, or before v11)
);

-- a round is a synonyms version (round_no = version); accepted is the acceptance sequence (0 = never accepted)
CREATE TABLE IF NOT EXISTS query_rounds (
  round_no INTEGER PRIMARY KEY, accepted INTEGER NOT NULL DEFAULT 0, accepted_by TEXT, accepted_at TEXT,
  accepted_how TEXT, lost_json TEXT, note TEXT, hashes_json TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS round_counts (
  round_no INTEGER NOT NULL, platform TEXT NOT NULL, n INTEGER, count_source TEXT NOT NULL, source_id INTEGER,
  by TEXT, at TEXT, UNIQUE(round_no, platform)
);
CREATE TABLE IF NOT EXISTS dry_runs (
  round_no INTEGER NOT NULL, platform TEXT NOT NULL, benchmark_id TEXT NOT NULL, state TEXT NOT NULL,
  blocks_json TEXT, reasons_json TEXT, PRIMARY KEY (round_no, platform, benchmark_id)
);
CREATE TABLE IF NOT EXISTS cluster_marks (
  platform TEXT NOT NULL, version INTEGER NOT NULL, signature TEXT NOT NULL, mark TEXT NOT NULL, by TEXT, at TEXT,
  PRIMARY KEY (platform, version, signature)
);
CREATE TABLE IF NOT EXISTS seed_indexing (
  benchmark_id TEXT NOT NULL, platform TEXT NOT NULL, verdict TEXT NOT NULL, by TEXT, at TEXT,
  PRIMARY KEY (benchmark_id, platform)
);

-- used by the seed-paper and interview change (2b)
CREATE TABLE IF NOT EXISTS seed_files (
  benchmark_id TEXT NOT NULL, sha256 TEXT NOT NULL, added_at TEXT, PRIMARY KEY (benchmark_id, sha256)
);
CREATE TABLE IF NOT EXISTS decisions (
  decision_id INTEGER PRIMARY KEY, kind TEXT NOT NULL, dedupe_key TEXT, evidence_json TEXT, options_json TEXT,
  "default" TEXT, chosen TEXT, mode TEXT, consequence TEXT, author TEXT, at TEXT, round_no INTEGER
);
CREATE TABLE IF NOT EXISTS stream_labels (
  venue_norm TEXT PRIMARY KEY, label TEXT NOT NULL, by TEXT, at TEXT
);

-- used by the query checking and provenance change (2c)
CREATE TABLE IF NOT EXISTS seed_chain_candidates (
  candidate_id INTEGER PRIMARY KEY, doi TEXT, title TEXT, title_norm TEXT, year INTEGER, direction TEXT,
  linking_seeds_json TEXT, n_linking INTEGER, retrieved_raw_id INTEGER, status TEXT, benchmark_id TEXT,
  fetched_at TEXT, by TEXT, at TEXT,
  openalex_id TEXT, abstract TEXT, keywords_json TEXT, origins_json TEXT, chain_round INTEGER   -- v6
);
CREATE TABLE IF NOT EXISTS lookup_cache (key TEXT PRIMARY KEY, body TEXT, fetched_at TEXT);
CREATE TABLE IF NOT EXISTS review_findings (
  finding_id INTEGER PRIMARY KEY, version INTEGER, platform TEXT, reader TEXT, item TEXT, quote TEXT, note TEXT,
  severity TEXT, at TEXT
);
"""

# Tables added by schema 6 (papers beyond the query); part of SCHEMA and run again by _migrate_v6
V6_TABLES = """
-- one seed paper's reference list per route (openalex | pdf); tier says how a PDF entry was parsed
CREATE TABLE IF NOT EXISTS seed_references (
  benchmark_id TEXT NOT NULL, ref_index INTEGER NOT NULL, raw_text TEXT, openalex_id TEXT, doi TEXT, title TEXT,
  year INTEGER, venue TEXT, route TEXT NOT NULL, tier TEXT, matched_by TEXT, at TEXT,
  UNIQUE(benchmark_id, route, ref_index)
);
-- one row per candidate import from a file (who, when, which file, the counts); new_ids_json lists the rows it created
CREATE TABLE IF NOT EXISTS candidate_imports (
  import_no INTEGER PRIMARY KEY, origin TEXT NOT NULL, file_name TEXT, sha256 TEXT, n_records INTEGER,
  n_new INTEGER, n_merged INTEGER, n_skipped INTEGER, new_ids_json TEXT, by TEXT, at TEXT
);
"""

# Tables added by schema 7 (validation-sample-planner D7, D11, D12, D15, D16); part of SCHEMA and run again by
# _migrate_v7
V7_TABLES = """
-- one row per (probability sample, result set) whose recall was seen; later looks update last_at and n
CREATE TABLE IF NOT EXISTS validation_looks (
  look_id INTEGER PRIMARY KEY, sample_id INTEGER NOT NULL, result_set_key TEXT NOT NULL, settings_hash TEXT,
  prompt_version INTEGER, prompt_sha256 TEXT, codebook_version INTEGER, first_at TEXT NOT NULL, last_at TEXT NOT NULL,
  n INTEGER NOT NULL DEFAULT 1, first_by TEXT, choice TEXT, choice_by TEXT, choice_at TEXT,
  UNIQUE(sample_id, result_set_key)
);
-- one row per distinct state of a sample's final labels seen in an estimate: the look index counted per sample
CREATE TABLE IF NOT EXISTS validation_look_states (
  sample_id INTEGER NOT NULL, labels_checksum TEXT NOT NULL, look_index INTEGER NOT NULL, first_at TEXT NOT NULL,
  level REAL, set_key TEXT, suggestion TEXT,
  PRIMARY KEY(sample_id, labels_checksum)
);
CREATE TABLE IF NOT EXISTS validation_stops (
  stop_id INTEGER PRIMARY KEY, sample_id INTEGER NOT NULL, at TEXT NOT NULL, author TEXT, look INTEGER,
  outcome TEXT, note TEXT
);
-- opaque tokens of the re-check and second-reviewer queues: the token alone names the record
CREATE TABLE IF NOT EXISTS queue_items (
  token TEXT PRIMARY KEY, sample_id INTEGER NOT NULL, record_id INTEGER NOT NULL, mode TEXT NOT NULL,
  reviewer TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS criteria_assessments (
  assessment_id INTEGER PRIMARY KEY, sample_id INTEGER NOT NULL, from_versions TEXT, to_version INTEGER,
  substantive TEXT NOT NULL, scope TEXT NOT NULL, recommended TEXT, n_affected INTEGER, n_unidentifiable INTEGER,
  note TEXT, author TEXT, at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS label_revisions (
  revision_id INTEGER PRIMARY KEY, label_id INTEGER, sample_id INTEGER NOT NULL, record_id INTEGER NOT NULL,
  reviewer TEXT, old_decision TEXT, old_reason TEXT, old_labels TEXT, old_codebook_version INTEGER,
  new_decision TEXT, new_reason TEXT, new_labels TEXT, new_codebook_version INTEGER, assessment_id INTEGER,
  at TEXT NOT NULL,
  cause TEXT NOT NULL DEFAULT 'recheck', package_id TEXT   -- v9: recheck | package re-review
);
"""

# Columns added by schema 7 to existing tables (the criteria state of a label, D11), declared exactly as in SCHEMA
V7_COLUMNS = {
    "labels": ["codebook_version INTEGER", "criteria_frozen INTEGER", "after_look INTEGER", "recheck_from INTEGER"],
    "adjudications": ["codebook_version INTEGER", "criteria_frozen INTEGER", "after_look INTEGER",
                      "recheck_from INTEGER"],
}

# Configuration comparisons (screening-config-comparison, design D1): part of SCHEMA with no version step, so every
# open creates them, also after older code wrote its own schema number back
# Table added by schema 8 (researcher-attribution D1): one entry per name recorded as an author, with its stable
# pseudonym number (never reused, joined entries included); part of SCHEMA and run again by _migrate_v8
V8_TABLES = """
CREATE TABLE IF NOT EXISTS researchers (
  name TEXT PRIMARY KEY, number INTEGER UNIQUE NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('declared','inferred')), source TEXT, first_seen TEXT,
  joined_into TEXT
);
"""

# Tables added by schema 9 (reviewer-roles-and-blinding D8, D12): review packages and their items, and every abstract
# a record has had; part of SCHEMA and run again by _migrate_v9. None declares REFERENCES records: a dedup drop must
# neither delete nor refuse them (D12)
V9_TABLES = """
CREATE TABLE IF NOT EXISTS review_packages (
  package_id TEXT PRIMARY KEY, reviewer TEXT NOT NULL, mode TEXT NOT NULL, kind TEXT NOT NULL, samples TEXT NOT NULL,
  codebook_version INTEGER, frozen INTEGER, earlier TEXT, excluded_label_ids TEXT, n_items INTEGER NOT NULL,
  file TEXT NOT NULL, made_at TEXT NOT NULL, imported_at TEXT, last_export_seq INTEGER, last_entries TEXT,
  withdrawn_at TEXT, withdraw_reason TEXT
);
CREATE TABLE IF NOT EXISTS review_package_items (
  package_id TEXT NOT NULL, token TEXT NOT NULL UNIQUE, sample_id INTEGER NOT NULL, record_id INTEGER NOT NULL,
  abstract_version INTEGER, repeat INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (package_id, sample_id, record_id)
);
CREATE TABLE IF NOT EXISTS record_abstracts (
  record_id INTEGER NOT NULL, version INTEGER NOT NULL, abstract TEXT, source TEXT NOT NULL, words INTEGER, at TEXT,
  PRIMARY KEY (record_id, version)
);
"""

# Columns added by schema 9 to existing tables, declared exactly as in SCHEMA
V9_COLUMNS = {
    "labels": ["made_in TEXT NOT NULL DEFAULT 'wider'", "abstract_version INTEGER", "tags TEXT"],
    "screen_calls": ["abstract_version INTEGER"],
    "label_revisions": ["cause TEXT NOT NULL DEFAULT 'recheck'", "package_id TEXT"],
}

# Columns added by schema 10 to existing tables (ui-redesign D5: ranking versions and their input hash), declared
# exactly as in SCHEMA
V10_COLUMNS = {
    "rankings": ["base_name TEXT", "version INTEGER", "input_hash TEXT", "current_run INTEGER", "embedding_model TEXT"],
}

# Columns added by schema 11 (unlock-seen-choice D2: the researcher's choice on each unlock), declared exactly as in
# SCHEMA
V11_COLUMNS = {
    "query_unlocks": ["seen_choice TEXT"],
}

# v12 (openalex-profile-and-sampled-exports D4): the size of the result a file was randomly sampled from; NULL is a
# complete export
V12_COLUMNS = {
    "sources": ["sample_of INTEGER"],
}

# The records change counter (ui-redesign D5, Risks): raised by every insert, update and delete of a record, so the
# input hash of the current texts is computed again only after the records changed; part of SCHEMA and run again by
# _migrate_v10
V10_TRIGGERS = """
CREATE TRIGGER IF NOT EXISTS records_changes_ins AFTER INSERT ON records BEGIN
  INSERT INTO meta(key, value) VALUES('records_changes', '1')
    ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1;
END;
CREATE TRIGGER IF NOT EXISTS records_changes_upd AFTER UPDATE ON records BEGIN
  INSERT INTO meta(key, value) VALUES('records_changes', '1')
    ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1;
END;
CREATE TRIGGER IF NOT EXISTS records_changes_del AFTER DELETE ON records BEGIN
  INSERT INTO meta(key, value) VALUES('records_changes', '1')
    ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1;
END;
"""

# Label corrections, seals and stated independence (label-completion-and-sealing D2, D5, D11): part of SCHEMA with no
# version step, so every store has them from its next open. None declares REFERENCES records: a dedup merge moves their
# rows (UPDATE OR IGNORE) and a drop leaves them, as review_package_items
LABEL_SEAL_TABLES = """
CREATE TABLE IF NOT EXISTS label_corrections (
  correction_id INTEGER PRIMARY KEY, sample_id INTEGER NOT NULL, record_id INTEGER NOT NULL,
  target TEXT NOT NULL, reviewer TEXT,
  old_decision TEXT, old_reason TEXT, old_labels TEXT, old_note TEXT, old_by TEXT,
  new_decision TEXT, new_reason TEXT, new_labels TEXT, new_note TEXT,
  why TEXT NOT NULL, author TEXT NOT NULL, at TEXT NOT NULL,
  locked INTEGER NOT NULL, sealed INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS label_seals (
  sample_id INTEGER NOT NULL, record_id INTEGER NOT NULL, first_look INTEGER, sealed_at TEXT NOT NULL,
  first_change_id INTEGER NOT NULL, last_look INTEGER, last_change_id INTEGER NOT NULL,
  decision TEXT, reason TEXT, labels TEXT, source TEXT,
  sealed_decision TEXT, corrected_at TEXT, corrected_by TEXT,
  PRIMARY KEY (sample_id, record_id)
);
CREATE TABLE IF NOT EXISTS label_independence (
  sample_id INTEGER NOT NULL, record_id INTEGER NOT NULL, reviewer_id TEXT NOT NULL, basis TEXT NOT NULL,
  by TEXT NOT NULL, at TEXT NOT NULL, PRIMARY KEY (sample_id, record_id, reviewer_id)
);
"""

# Author columns read by _migrate_v8 after History, in this order (researcher-attribution D7)
V8_AUTHOR_COLUMNS = [
    ("reviewers", "reviewer_id"), ("labels", "reviewer_id"), ("label_revisions", "reviewer"),
    ("queue_items", "reviewer"), ("adjudications", "decided_by"), ("criteria_assessments", "author"),
    ("record_notes", "author"), ("record_tags", "author"), ("query_unlocks", "by"), ("query_rounds", "accepted_by"),
    ("round_counts", "by"), ("cluster_marks", "by"), ("seed_indexing", "by"), ("decisions", "author"),
    ("stream_labels", "by"), ("seed_chain_candidates", "by"), ("candidate_imports", "by"),
    ("validation_looks", "first_by"), ("validation_looks", "choice_by"), ("validation_stops", "author"),
    ("screen_comparisons", "author"), ("screen_call_reuse", "reused_by"), ("screen_result_sets", "created_by"),
    ("dedup_pairs", "decided_by"), ("questions", "answered_by"), ("clarifications", "author"),
]
# names WIDER itself records that are not people (researcher-attribution D2)
NOT_PEOPLE = ("", "migration", "import", "original")

COMPARISON_TABLES = """
-- configs: [{provider, prompt_version, temperature, identity}]; record_ids: the sample's ids at the first run
CREATE TABLE IF NOT EXISTS screen_comparisons (
  comparison_id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, sample TEXT NOT NULL, configs TEXT NOT NULL,
  repeats INTEGER NOT NULL, record_ids TEXT, author TEXT, created_at TEXT NOT NULL, finished_at TEXT
);
CREATE TABLE IF NOT EXISTS screen_comparison_runs (
  comparison_id INTEGER NOT NULL, config_index INTEGER NOT NULL, repeat INTEGER NOT NULL, run_id TEXT NOT NULL,
  PRIMARY KEY(comparison_id, config_index, repeat)
);
"""

# Columns added by schema 6 to existing tables, declared exactly as in SCHEMA and in the same order
V6_COLUMNS = {
    "seed_chain_candidates": ["openalex_id TEXT", "abstract TEXT", "keywords_json TEXT", "origins_json TEXT",
                              "chain_round INTEGER"],
}

# Columns added by schema 5 to existing tables, declared exactly as in SCHEMA (PRAGMA table_info must match)
V5_COLUMNS = {
    "sources": ["included INTEGER NOT NULL DEFAULT 1"],
    "benchmark_papers": ["source TEXT NOT NULL DEFAULT 'researcher'", "added_at TEXT", "validation_since TEXT",
                         "validation_since_unlock INTEGER NOT NULL DEFAULT 0",
                         "was_development INTEGER NOT NULL DEFAULT 0", "keywords TEXT", "keywords_from TEXT"],
    "artifacts": ["frozen_no INTEGER"],
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

-- one row per imported export file (RIS, CSV/TSV, XLS/XLSX, BibTeX); included = 0 keeps a file listed but out of
-- deduplication (v5)
CREATE TABLE IF NOT EXISTS sources (
  source_id INTEGER PRIMARY KEY,
  platform TEXT NOT NULL,
  file_name TEXT NOT NULL,
  sha256 TEXT NOT NULL UNIQUE,
  n_records INTEGER NOT NULL,
  imported_at TEXT NOT NULL,
  query_version INTEGER,
  format TEXT,        -- ris | csv | tsv | xlsx | xls | bib
  profile TEXT,       -- column profile used (profiles.yaml): scopus_csv and the like
  included INTEGER NOT NULL DEFAULT 1,
  sample_of INTEGER   -- the file is a random sample of this many results (v12); NULL: a complete export
);

-- every record as exported, before deduplication
CREATE TABLE IF NOT EXISTS raw_records (
  raw_id INTEGER PRIMARY KEY,
  source_id INTEGER NOT NULL REFERENCES sources(source_id),
  platform TEXT NOT NULL,
  position INTEGER NOT NULL,
  type TEXT, title TEXT, authors TEXT, year INTEGER, journal TEXT,
  volume TEXT, issue TEXT, pages TEXT, doi TEXT, abstract TEXT, keywords TEXT,
  url TEXT, language TEXT, extra TEXT,
  title_norm TEXT, doi_norm TEXT, first_author TEXT
);
CREATE INDEX IF NOT EXISTS ix_raw_doi ON raw_records(doi_norm);
CREATE INDEX IF NOT EXISTS ix_raw_title ON raw_records(title_norm);

CREATE TABLE IF NOT EXISTS dedup_pairs (
  pair_id INTEGER PRIMARY KEY,
  raw_a INTEGER NOT NULL, raw_b INTEGER NOT NULL,
  stage TEXT NOT NULL, rule TEXT NOT NULL, score REAL,
  diagnostics TEXT, decision TEXT NOT NULL DEFAULT 'auto', -- auto | manual_same | manual_different | queued
  decided_by TEXT, decided_at TEXT,
  UNIQUE(raw_a, raw_b)
);

CREATE TABLE IF NOT EXISTS dedup_clusters (
  raw_id INTEGER PRIMARY KEY REFERENCES raw_records(raw_id),
  cluster_id INTEGER NOT NULL,
  is_master INTEGER NOT NULL DEFAULT 0
);

-- unique records after deduplication (masters with field provenance)
CREATE TABLE IF NOT EXISTS records (
  record_id INTEGER PRIMARY KEY,
  cluster_id INTEGER NOT NULL UNIQUE,
  key TEXT NOT NULL UNIQUE,
  title TEXT, authors TEXT, year INTEGER, journal TEXT, doi TEXT, abstract TEXT, keywords TEXT, url TEXT,
  n_sources INTEGER NOT NULL DEFAULT 1, platforms TEXT,
  field_sources TEXT,
  abstract_words INTEGER, thin INTEGER NOT NULL DEFAULT 0,
  abstract_source TEXT NOT NULL DEFAULT 'export'
);
CREATE INDEX IF NOT EXISTS ix_records_doi ON records(doi);

CREATE TABLE IF NOT EXISTS enrichment_log (
  id INTEGER PRIMARY KEY,
  record_id INTEGER NOT NULL REFERENCES records(record_id),
  source TEXT NOT NULL, status TEXT NOT NULL, words INTEGER, fetched_at TEXT NOT NULL, note TEXT
);

CREATE TABLE IF NOT EXISTS embedding_sets (
  set_name TEXT PRIMARY KEY,
  model TEXT NOT NULL, kind TEXT NOT NULL, dim INTEGER NOT NULL, n INTEGER NOT NULL,
  text_hash TEXT NOT NULL, file TEXT NOT NULL, created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rankings (
  ranking_id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  set_name TEXT NOT NULL, anchors_version INTEGER NOT NULL, top_n INTEGER NOT NULL,
  n INTEGER NOT NULL, md5 TEXT NOT NULL, created_at TEXT NOT NULL, note TEXT,
  base_name TEXT, version INTEGER, input_hash TEXT, current_run INTEGER, embedding_model TEXT   -- v10
);
CREATE TABLE IF NOT EXISTS ranking_rows (
  ranking_id INTEGER NOT NULL REFERENCES rankings(ranking_id),
  record_id INTEGER NOT NULL REFERENCES records(record_id),
  rank INTEGER NOT NULL, score REAL NOT NULL, best_cell TEXT, cell_scores TEXT,
  PRIMARY KEY (ranking_id, record_id)
);
CREATE INDEX IF NOT EXISTS ix_ranking_rank ON ranking_rows(ranking_id, rank);

CREATE TABLE IF NOT EXISTS benchmark_matches (
  benchmark_id TEXT NOT NULL,
  scope TEXT NOT NULL,        -- 'platform:<key>' | 'corpus'
  record_ref INTEGER,         -- raw_id for a platform scope, record_id for the corpus
  method TEXT NOT NULL, score REAL NOT NULL, confirmed INTEGER NOT NULL DEFAULT 0,
  checked_at TEXT NOT NULL,
  PRIMARY KEY (benchmark_id, scope)
);

CREATE TABLE IF NOT EXISTS samples (
  sample_id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE, kind TEXT NOT NULL,  -- probability | purposive
  ranking_id INTEGER REFERENCES rankings(ranking_id),
  design TEXT NOT NULL, seed INTEGER, n INTEGER NOT NULL, created_at TEXT NOT NULL, locked INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sample_rows (
  sample_id INTEGER NOT NULL REFERENCES samples(sample_id),
  record_id INTEGER NOT NULL REFERENCES records(record_id),
  queue_pos INTEGER NOT NULL, band TEXT, weight REAL, pool TEXT, rank INTEGER,
  PRIMARY KEY (sample_id, record_id)
);

CREATE TABLE IF NOT EXISTS reviewers (
  reviewer_id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS labels (
  label_id INTEGER PRIMARY KEY,
  sample_id INTEGER NOT NULL REFERENCES samples(sample_id),
  record_id INTEGER NOT NULL REFERENCES records(record_id),
  reviewer_id TEXT NOT NULL REFERENCES reviewers(reviewer_id),
  decision TEXT NOT NULL,     -- include | exclude | maybe
  reason TEXT, labels TEXT, note TEXT, updated_at TEXT NOT NULL,
  codebook_version INTEGER, criteria_frozen INTEGER, after_look INTEGER, recheck_from INTEGER,   -- v7
  made_in TEXT NOT NULL DEFAULT 'wider', abstract_version INTEGER, tags TEXT,   -- v9: wider | blind queue | package id
  UNIQUE(sample_id, record_id, reviewer_id)
);
CREATE TABLE IF NOT EXISTS adjudications (
  sample_id INTEGER NOT NULL, record_id INTEGER NOT NULL,
  decision TEXT NOT NULL, reason TEXT, labels TEXT, note TEXT, decided_by TEXT, decided_at TEXT NOT NULL,
  codebook_version INTEGER, criteria_frozen INTEGER, after_look INTEGER, recheck_from INTEGER,   -- v7
  PRIMARY KEY (sample_id, record_id)
);

CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY, kind TEXT NOT NULL, stamp TEXT NOT NULL, spec_hash TEXT NOT NULL,
  provider TEXT, model TEXT, prompt_version INTEGER, status TEXT NOT NULL, n_items INTEGER, n_ok INTEGER,
  started_at TEXT NOT NULL, finished_at TEXT, dir TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS screen_calls (
  call_id INTEGER PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(run_id),
  record_id INTEGER NOT NULL REFERENCES records(record_id),
  pass TEXT NOT NULL, input_sha256 TEXT NOT NULL, ok INTEGER NOT NULL,
  raw_decision TEXT, rule_decision TEXT, parsed TEXT, usage TEXT, attempts INTEGER, error TEXT, at TEXT NOT NULL,
  backend_hash TEXT, provider TEXT, model TEXT, model_reported TEXT,   -- NULL before v4: see runs.provider/model
  eval_key TEXT,       -- sha256 of the call's effective inputs (Decision 23); NULL before v4: never reused
  abstract_version INTEGER,   -- v9: the record_abstracts version the call read; NULL: not recorded
  UNIQUE(run_id, record_id, pass)
);
-- an evaluation of an earlier run used by this run without a model call (the source call is never copied)
CREATE TABLE IF NOT EXISTS screen_call_reuse (
  run_id TEXT NOT NULL, record_id INTEGER NOT NULL REFERENCES records(record_id), pass TEXT NOT NULL,
  source_call_id INTEGER NOT NULL, reused_at TEXT NOT NULL, reused_by TEXT,
  PRIMARY KEY (run_id, record_id, pass)
);
-- screening rows of a record merged away by deduplication that clashed with the surviving record's (kept, not counted)
CREATE TABLE IF NOT EXISTS merge_set_aside (
  id INTEGER PRIMARY KEY, at TEXT NOT NULL, from_table TEXT NOT NULL, record_id INTEGER NOT NULL,
  kept_record_id INTEGER NOT NULL, row TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS screen_decisions (
  protocol TEXT NOT NULL, record_id INTEGER NOT NULL REFERENCES records(record_id),
  decision TEXT NOT NULL, votes TEXT, cell TEXT, labels TEXT, reason TEXT,
  decided_at TEXT NOT NULL,
  vote_calls TEXT,     -- JSON list of screen_calls.call_id in vote order; NULL for imports and pre-v4 rows
  decided_by TEXT,     -- the passes whose votes equal the final (A,B), split, researcher, import, carried from P
  origin TEXT,         -- JSON {kind, set, decided_by, decided_at, run_id, model}; NULL: made in this set by its runs
  PRIMARY KEY (protocol, record_id)
);
-- the result sets of a protocol: set 1 keeps the key <protocol>, set n >= 2 stores its decisions under <protocol>#<n>
CREATE TABLE IF NOT EXISTS screen_result_sets (
  protocol TEXT NOT NULL, result_set INTEGER NOT NULL, key TEXT NOT NULL UNIQUE, label TEXT,
  source TEXT NOT NULL, created_at TEXT NOT NULL, created_by TEXT, settings TEXT, settings_hash TEXT, runs TEXT,
  import_file TEXT, import_sha256 TEXT, source_protocol TEXT, source_set INTEGER, scope TEXT, role TEXT,
  derived_from TEXT, mixed INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (protocol, result_set)
);
-- each distinct backend identity (run snapshot) once; screen_calls.backend_hash points here
CREATE TABLE IF NOT EXISTS backend_snapshots (
  backend_hash TEXT PRIMARY KEY, snapshot TEXT NOT NULL, first_used TEXT NOT NULL
);
-- a run that continued on a changed backend
CREATE TABLE IF NOT EXISTS run_backend_changes (
  change_id INTEGER PRIMARY KEY, run_id TEXT NOT NULL, at TEXT NOT NULL, from_hash TEXT, to_hash TEXT,
  from_provider TEXT, from_model TEXT, to_provider TEXT, to_model TEXT, changed TEXT,
  confirmed_via TEXT NOT NULL, records_done INTEGER
);

CREATE TABLE IF NOT EXISTS benchmark_papers (
  benchmark_id TEXT PRIMARY KEY,
  title TEXT NOT NULL, doi TEXT, set_name TEXT NOT NULL, year INTEGER,
  abstract TEXT, abstract_source TEXT, abstract_words INTEGER, fetched_at TEXT, fetch_log TEXT,
  source TEXT NOT NULL DEFAULT 'researcher', added_at TEXT, validation_since TEXT,
  validation_since_unlock INTEGER NOT NULL DEFAULT 0, was_development INTEGER NOT NULL DEFAULT 0,
  keywords TEXT, keywords_from TEXT
);

-- frozen_no orders the freezes (v5): the highest is the version frozen most recently
CREATE TABLE IF NOT EXISTS artifacts (
  kind TEXT NOT NULL, version INTEGER NOT NULL, path TEXT NOT NULL, sha256 TEXT NOT NULL,
  status TEXT NOT NULL, origin TEXT NOT NULL, parent_version INTEGER, note TEXT, created_at TEXT NOT NULL,
  frozen_no INTEGER,
  PRIMARY KEY (kind, version)
);

CREATE TABLE IF NOT EXISTS prior_reviews (
  review_id INTEGER PRIMARY KEY,
  label TEXT NOT NULL, kind TEXT NOT NULL,     -- 'list' (reference export) | 'pdf' | 'text' (pasted)
  file_name TEXT, sha256 TEXT, n_papers INTEGER NOT NULL DEFAULT 0,
  added_at TEXT NOT NULL, note TEXT
);

CREATE TABLE IF NOT EXISTS prior_review_papers (
  paper_id INTEGER PRIMARY KEY,
  review_id INTEGER NOT NULL REFERENCES prior_reviews(review_id) ON DELETE CASCADE,
  position INTEGER NOT NULL,
  title TEXT, title_norm TEXT, doi TEXT, doi_norm TEXT, year INTEGER, first_author TEXT, raw_ref TEXT,
  confidence TEXT NOT NULL,                    -- 'doi' | 'parsed' | 'heuristic'
  openalex_id TEXT, resolved_at TEXT, chained_backward_at TEXT, chained_forward_at TEXT,
  UNIQUE (review_id, position)
);
CREATE INDEX IF NOT EXISTS ix_prp_doi ON prior_review_papers(doi_norm);

CREATE TABLE IF NOT EXISTS prior_review_matches (
  paper_id INTEGER NOT NULL REFERENCES prior_review_papers(paper_id) ON DELETE CASCADE,
  scope TEXT NOT NULL,        -- 'platform:<key>' | 'corpus'
  record_ref INTEGER, method TEXT NOT NULL, score REAL NOT NULL, checked_at TEXT NOT NULL,
  PRIMARY KEY (paper_id, scope)
);

CREATE TABLE IF NOT EXISTS citation_chain (
  paper_id INTEGER NOT NULL REFERENCES prior_review_papers(paper_id) ON DELETE CASCADE,
  direction TEXT NOT NULL,    -- 'backward' (referenced by the paper) | 'forward' (cites the paper)
  openalex_id TEXT NOT NULL, doi TEXT, title TEXT, year INTEGER, authors TEXT, journal TEXT, abstract TEXT,
  fetched_at TEXT NOT NULL, exported_at TEXT,
  PRIMARY KEY (paper_id, direction, openalex_id)
);

-- notes and tags a reviewer attaches to a unique record (record drawer, `wider notes`); merged when dedup merges records
CREATE TABLE IF NOT EXISTS record_notes (
  note_id INTEGER PRIMARY KEY,
  record_id INTEGER NOT NULL REFERENCES records(record_id),
  author TEXT NOT NULL, text TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_record_notes_record ON record_notes(record_id);
CREATE TABLE IF NOT EXISTS record_tags (
  record_id INTEGER NOT NULL REFERENCES records(record_id),
  tag TEXT NOT NULL, author TEXT NOT NULL, created_at TEXT NOT NULL,
  PRIMARY KEY (record_id, tag, author)
);

-- one row per model call (every stage), for tokens and costs; prompts and answers are not stored here
CREATE TABLE IF NOT EXISTS llm_usage (
  usage_id INTEGER PRIMARY KEY,
  at TEXT NOT NULL, stage TEXT NOT NULL, run_ref TEXT, job_id TEXT,
  provider TEXT NOT NULL, kind TEXT NOT NULL, model TEXT, locality TEXT NOT NULL,
  ok INTEGER NOT NULL, prompt_tokens INTEGER NOT NULL DEFAULT 0, completion_tokens INTEGER NOT NULL DEFAULT 0,
  cost_usd REAL,           -- NULL: a cloud backend without prices
  cached_tokens INTEGER, cache_write_tokens INTEGER   -- NULL: not reported (or logged before v4)
);
CREATE INDEX IF NOT EXISTS ix_llm_usage_run ON llm_usage(run_ref);
CREATE INDEX IF NOT EXISTS ix_llm_usage_job ON llm_usage(job_id);

-- background runs started from the UI (screening, embedding, enrichment, proposals)
CREATE TABLE IF NOT EXISTS jobs (
  job_id TEXT PRIMARY KEY, kind TEXT NOT NULL, stage TEXT NOT NULL, label TEXT NOT NULL, params TEXT,
  status TEXT NOT NULL,       -- running | done | failed | cancelled | interrupted
  n_done INTEGER, n_total INTEGER, n_start INTEGER, phase_at TEXT, message TEXT,
  started_at TEXT NOT NULL, updated_at TEXT NOT NULL, finished_at TEXT, error TEXT, result TEXT
);

-- every stage result ever recorded (the run selector on stage pages); meta last_result:<stage> holds the latest
CREATE TABLE IF NOT EXISTS stage_results (
  result_id INTEGER PRIMARY KEY, stage TEXT NOT NULL, action TEXT, ran_at TEXT NOT NULL, result TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_stage_results_stage ON stage_results(stage, result_id);

-- questions the tool raises when in doubt (queued, or asked at once), with the researcher's answer and what it changed
CREATE TABLE IF NOT EXISTS questions (
  question_id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL, stage TEXT NOT NULL, dedupe_key TEXT NOT NULL,
  run_ref TEXT, job_id TEXT, record_id INTEGER, ref TEXT,
  title TEXT NOT NULL, prompt TEXT NOT NULL, options TEXT NOT NULL, context TEXT,
  status TEXT NOT NULL DEFAULT 'open',   -- open | answered | dismissed
  answer TEXT, answered_by TEXT, applied TEXT, created_at TEXT NOT NULL, answered_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_questions_status ON questions(status, question_id);

-- clarifications the researcher gave in answers; construct proposals read them as context
CREATE TABLE IF NOT EXISTS clarifications (
  clarification_id INTEGER PRIMARY KEY, scope TEXT NOT NULL, text TEXT NOT NULL, author TEXT,
  question_id INTEGER, created_at TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1
);

-- every manual change (decisions, labels, notes, statuses, settings): rows or files before and after, for history and undo
CREATE TABLE IF NOT EXISTS history (
  change_id INTEGER PRIMARY KEY,
  at TEXT NOT NULL, author TEXT, kind TEXT NOT NULL, summary TEXT NOT NULL, record_id INTEGER,
  items TEXT NOT NULL, undone_by INTEGER, undo_of INTEGER
);
CREATE INDEX IF NOT EXISTS ix_history_record ON history(record_id);
-- quality-control samples of a set's settled decisions (manual-review D11): one row per drawn record; seed, pool size and
-- the sha256 of the pool's sorted record ids let a draw be repeated and checked
CREATE TABLE IF NOT EXISTS review_qc (
  draw_id INTEGER NOT NULL, protocol TEXT NOT NULL, set_key TEXT NOT NULL, record_id INTEGER NOT NULL,
  seed INTEGER NOT NULL, n INTEGER NOT NULL, pool_n INTEGER NOT NULL, pool_sha256 TEXT NOT NULL, by TEXT, at TEXT NOT NULL,
  PRIMARY KEY (draw_id, record_id)
);

-- local audit log of outside lookup requests: one row per lookup run and service (no URL, no query, no body)
CREATE TABLE IF NOT EXISTS external_requests (
  id INTEGER PRIMARY KEY, at TEXT NOT NULL, service TEXT NOT NULL,
  purpose TEXT NOT NULL, n_items INTEGER NOT NULL, n_failed INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_external_requests_at ON external_requests(at);
""" + V5_TABLES + V6_TABLES + V7_TABLES + COMPARISON_TABLES + V8_TABLES + V9_TABLES + V10_TRIGGERS + LABEL_SEAL_TABLES


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.migrate()

    # ----- schema -----
    def migrate(self) -> None:
        self.conn.executescript(SCHEMA)
        cur = int(self.get_meta("schema_version") or SCHEMA_VERSION)
        if cur < 2:
            self._migrate_v2()
        if cur < 3:
            self._migrate_v3()
        if cur < 4:
            self._migrate_v4()
        if cur < 5:
            self._migrate_v5()
        if cur < 6:
            self._migrate_v6()
        if cur < 7:
            self._migrate_v7()
        if cur < 8:
            self._migrate_v8()
        if cur < 9:
            self._migrate_v9()
        if cur < 10:
            self._migrate_v10()
        if cur < 11:
            self._migrate_v11()
        if cur < 12:
            self._migrate_v12()
        self.set_meta("schema_version", str(SCHEMA_VERSION))

    def columns(self, table: str) -> list[str]:
        return [r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")]

    def _migrate_v2(self) -> None:
        """v2: any review-specific extra columns of screen_decisions become one generic ``labels`` JSON column."""
        import json
        cols = self.columns("screen_decisions")
        if "labels" not in cols:
            self.conn.execute("ALTER TABLE screen_decisions ADD COLUMN labels TEXT")
        extra = [c for c in cols if c not in ("protocol", "record_id", "decision", "votes", "cell", "labels", "reason", "decided_at")]
        if extra:
            sel = ", ".join(extra)
            rows = self.conn.execute(f"SELECT protocol, record_id, {sel} FROM screen_decisions").fetchall()
            for r in rows:
                lab = {k: r[k] for k in extra if r[k]}
                if lab:
                    self.conn.execute("UPDATE screen_decisions SET labels=? WHERE protocol=? AND record_id=?",
                                      (json.dumps(lab), r["protocol"], r["record_id"]))

    def _migrate_v3(self) -> None:
        """v3: sources.format and sources.profile (imports are no longer RIS-only)."""
        cols = self.columns("sources")
        if "format" not in cols:
            self.conn.execute("ALTER TABLE sources ADD COLUMN format TEXT")
            self.conn.execute("UPDATE sources SET format='ris' WHERE format IS NULL")
        if "profile" not in cols:
            self.conn.execute("ALTER TABLE sources ADD COLUMN profile TEXT")

    def _migrate_v4(self) -> None:
        """v4: cached tokens in llm_usage, the backend of each screening call, the calls behind each decision
        (all nullable). The new tables come from SCHEMA. Safe to run twice."""
        adds = {"llm_usage": ["cached_tokens INTEGER", "cache_write_tokens INTEGER"],
                "screen_calls": ["backend_hash TEXT", "provider TEXT", "model TEXT", "model_reported TEXT", "eval_key TEXT"],
                "screen_decisions": ["vote_calls TEXT", "decided_by TEXT", "origin TEXT"]}
        for table, cols in adds.items():
            have = self.columns(table)
            for col in cols:
                if col.split()[0] not in have:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {col}")
        # set 1 of every existing protocol (its key stays the protocol name); the source is read from the rows, since
        # decided_by is NULL on old rows. Scope, role, runs and the '#' rename: screen.result_sets.migrate_legacy.
        from .provenance import utcnow
        for r in self.conn.execute(
                "SELECT protocol, SUM(votes IS NOT NULL) voted FROM screen_decisions GROUP BY protocol").fetchall():
            name = r["protocol"]
            source = "second" if name.endswith("_second") else "screening" if r["voted"] else "import"
            self.conn.execute("INSERT OR IGNORE INTO screen_result_sets(protocol,result_set,key,source,created_at,created_by,"
                              "mixed,active) VALUES(?,1,?,?,?,'migration',0,1)", (name, name, source, utcnow()))

    def _migrate_v5(self) -> None:
        """v5: per-file inclusion, seed paper provenance, the order of freezes, the list of unlocks and rounds (and
        the empty tables of the two follow-up changes). No pre-existing paper can pass as new validation. Safe to run
        twice. benchmark.csv is rewritten by the project once it opens (meta ``benchmark_csv_v5``)."""
        from .provenance import utcnow
        self.conn.executescript(V5_TABLES)
        for table, cols in V5_COLUMNS.items():
            have = self.columns(table)
            for col in cols:
                if col.split()[0] not in have:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {col}")
        now = utcnow()
        self.conn.execute(
            "UPDATE benchmark_papers SET added_at=COALESCE(added_at, ?), "
            "validation_since=CASE WHEN set_name='Validation' THEN COALESCE(validation_since, ?) END, "
            "validation_since_unlock=0, was_development=CASE WHEN set_name='Development' THEN 1 ELSE was_development END",
            (now, now))
        for kind in [r[0] for r in self.conn.execute("SELECT DISTINCT kind FROM artifacts WHERE status='frozen'")]:
            if self.conn.execute("SELECT 1 FROM artifacts WHERE kind=? AND frozen_no IS NOT NULL", (kind,)).fetchone():
                continue
            for n, (v,) in enumerate(self.conn.execute(
                    "SELECT version FROM artifacts WHERE kind=? AND status='frozen' ORDER BY version", (kind,)).fetchall(), 1):
                self.conn.execute("UPDATE artifacts SET frozen_no=? WHERE kind=? AND version=?", (n, kind, v))
        # every saved synonyms version is a round; the newest frozen one was accepted by its freeze
        self.conn.execute("INSERT OR IGNORE INTO query_rounds(round_no, created_at) "
                          "SELECT version, created_at FROM artifacts WHERE kind='synonyms'")
        newest = self.conn.execute("SELECT MAX(version) FROM artifacts WHERE kind='synonyms' AND status='frozen'").fetchone()[0]
        if newest is not None and not self.conn.execute("SELECT 1 FROM query_rounds WHERE accepted > 0").fetchone():
            self.conn.execute("UPDATE query_rounds SET accepted=1, accepted_by='migration', accepted_at=?, "
                              "accepted_how='freeze' WHERE round_no=?", (now, newest))
        # a look logged before v5 has no list of the papers shown: only this key gives the "not recorded" line
        peeked = self.get_meta("validation_peeked_at")
        if peeked and self.get_meta("validation_peeked_before_v5") is None:
            self.set_meta("validation_peeked_before_v5", peeked)
        if self.get_meta("benchmark_csv_v5") is None:
            self.set_meta("benchmark_csv_v5", f"pending {now}")

    def _migrate_v6(self) -> None:
        """v6: the candidate list keeps where each candidate came from and its snowball round, plus the reference
        lists of seed papers and the list of candidate imports. Existing candidates came from OpenAlex in round 1.
        Safe to run twice."""
        self.conn.executescript(V6_TABLES)
        for table, cols in V6_COLUMNS.items():
            have = self.columns(table)
            for col in cols:
                if col.split()[0] not in have:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {col}")
        self.conn.execute("UPDATE seed_chain_candidates SET origins_json=COALESCE(origins_json, '[\"openalex\"]'), "
                          "chain_round=COALESCE(chain_round, 1)")

    def _migrate_v7(self) -> None:
        """v7: validation looks, look states, stops and queue items, the criteria state on labels, criteria
        assessments and label revisions (validation-sample-planner). A project with labels on a probability sample and
        a stored recall result gets ``recall_looks_before_v7``: those looks were not recorded. Safe to run twice."""
        self.conn.executescript(V7_TABLES)
        for table, cols in V7_COLUMNS.items():
            have = self.columns(table)
            for col in cols:
                if col.split()[0] not in have:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {col}")
        if self.get_meta("recall_looks_before_v7") is not None or self.scalar("SELECT COUNT(*) FROM validation_looks"):
            return
        labelled = self.scalar("SELECT COUNT(*) FROM labels l JOIN samples s ON s.sample_id=l.sample_id "
                               "WHERE s.kind='probability'")
        seen = self.conn.execute("SELECT 1 FROM meta WHERE key='last_result:recall'").fetchone()
        if labelled and seen:
            from datetime import datetime

            self.set_meta("recall_looks_before_v7", datetime.now(UTC).isoformat(timespec="seconds"))

    def _migrate_v8(self) -> None:
        """v8: the researcher list (researcher-attribution D7). Every History author becomes an inferred entry with
        source ``older version``, numbered by its first change; then every other name in the reviewer table and the
        author columns, in that order. History is not touched. Safe to run twice."""
        from .provenance import utcnow

        self.conn.executescript(V8_TABLES)
        tables = set(self.tables())
        now = utcnow()
        names: list[tuple[str, str]] = [(r[0], r[1]) for r in self.conn.execute(
            "SELECT author, MIN(at) FROM history WHERE author IS NOT NULL GROUP BY author "
            "ORDER BY MIN(at), MIN(change_id)").fetchall()]
        for table, col in V8_AUTHOR_COLUMNS:
            if table in tables and col in self.columns(table):
                names += [(r[0], now) for r in self.conn.execute(
                    f"SELECT {col} FROM {table} WHERE {col} IS NOT NULL GROUP BY {col} ORDER BY MIN(rowid)").fetchall()]
        for raw, at in names:
            name = str(raw).strip()[:80]
            if name in NOT_PEOPLE or self.conn.execute("SELECT 1 FROM researchers WHERE name=?", (name,)).fetchone():
                continue
            n = (self.conn.execute("SELECT MAX(number) FROM researchers").fetchone()[0] or 0) + 1
            self.conn.execute("INSERT INTO researchers(name, number, status, source, first_seen) "
                              "VALUES(?,?,'inferred','older version',?)", (name, n, at or now))

    def _migrate_v9(self) -> None:
        """v9: review packages, abstract versions, how each label was made (reviewer-roles-and-blinding D8, D12).
        Version 1 of each abstract is the imported one (the raw record ``field_sources`` names, else the cluster's
        longest raw abstract, else the current one when it came from an export, else empty); an enriched or pasted
        abstract becomes version 2, dated by its last ``filled`` row in ``enrichment_log``. Earlier model calls and
        labels get the version in force at their time; a label saved from the blind second-reviewer queue gets
        ``made_in`` = ``blind queue``. Safe to run twice."""
        from .provenance import utcnow
        from .text import word_count

        self.conn.executescript(V9_TABLES)
        for table, cols in V9_COLUMNS.items():
            have = self.columns(table)
            for col in cols:
                if col.split()[0] not in have:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {col}")
        now = utcnow()
        imported_at = {r[0]: r[1] for r in self.conn.execute("SELECT source_id, imported_at FROM sources")}
        for r in self.conn.execute("SELECT record_id, cluster_id, abstract, abstract_source, field_sources FROM records "
                                   "WHERE record_id NOT IN (SELECT record_id FROM record_abstracts)").fetchall():
            raws = {x[0]: (x[1], x[2]) for x in self.conn.execute(
                "SELECT r.raw_id, r.abstract, r.source_id FROM dedup_clusters dc JOIN raw_records r ON r.raw_id=dc.raw_id "
                "WHERE dc.cluster_id=?", (r["cluster_id"],))}
            try:
                named = (json.loads(r["field_sources"] or "{}") or {}).get("abstract")
            except ValueError:
                named = None
            first, at = None, None
            if named in raws:
                first, at = raws[named][0], imported_at.get(raws[named][1])
            elif raws and any(a for a, _ in raws.values()):
                a, sid = max(((a, s) for a, s in raws.values() if a), key=lambda x: len(x[0]))
                first, at = a, imported_at.get(sid)
            elif r["abstract_source"] == "export":
                first = r["abstract"]
            if at is None and raws:
                at = min((imported_at.get(s) for _, s in raws.values() if imported_at.get(s)), default=None)
            first = first or ""
            self.conn.execute("INSERT INTO record_abstracts(record_id,version,abstract,source,words,at) VALUES(?,1,?,?,?,?)",
                              (r["record_id"], first, "imported", word_count(first), at))
            if r["abstract_source"] != "export" and (r["abstract"] or "") != first:
                filled = self.conn.execute("SELECT MAX(fetched_at) FROM enrichment_log WHERE record_id=? AND status='filled'",
                                           (r["record_id"],)).fetchone()[0]
                self.conn.execute("INSERT INTO record_abstracts(record_id,version,abstract,source,words,at) "
                                  "VALUES(?,2,?,?,?,?)", (r["record_id"], r["abstract"] or "", r["abstract_source"],
                                                          word_count(r["abstract"]), filled or now))
        # the version in force at each earlier call's and label's time (a row with no time stays NULL)
        for table, when in (("screen_calls", "at"), ("labels", "updated_at")):
            self.conn.execute(
                f"UPDATE {table} SET abstract_version=(SELECT MAX(v.version) FROM record_abstracts v "
                f"WHERE v.record_id={table}.record_id AND (v.version=1 OR v.at IS NULL OR v.at<={table}.{when})) "
                f"WHERE abstract_version IS NULL AND COALESCE({table}.{when},'')<>''")
        # made_in: a label with a second-reviewer queue item whose latest labelling change is a second-reviewer save
        latest: dict[tuple, str] = {}
        for h in self.conn.execute("SELECT summary, items FROM history WHERE kind='label' ORDER BY change_id").fetchall():
            try:
                items = json.loads(h["items"] or "[]")
            except ValueError:
                continue
            for it in items:
                if it.get("t") == "rows" and it.get("table") == "labels" and len(it.get("params") or []) == 3:
                    latest[tuple(it["params"])] = h["summary"] or ""
        for q in self.conn.execute("SELECT DISTINCT sample_id, record_id, reviewer FROM queue_items WHERE mode='second'").fetchall():
            if latest.get((q[0], q[1], q[2]), "").endswith("saved a second-reviewer label"):
                self.conn.execute("UPDATE labels SET made_in='blind queue' WHERE sample_id=? AND record_id=? AND reviewer_id=? "
                                  "AND made_in='wider'", (q[0], q[1], q[2]))

    def _migrate_v10(self) -> None:
        """v10: ranking versions (ui-redesign D5). Adds ``base_name``, ``version``, ``input_hash``, ``current_run`` and
        ``embedding_model`` to ``rankings`` and the records change counter; no table is rebuilt, so ``ranking_rows``
        and ``samples.ranking_id`` keep their links. An existing ranking becomes version 1 of its own name, with the
        model of its embedding set; its input hash stays empty (made before hashes were stored: never marked "needs
        review"). Safe to run twice: columns are checked first and only rows without a base name are filled."""
        have = self.columns("rankings")
        for col in V10_COLUMNS["rankings"]:
            if col.split()[0] not in have:
                self.conn.execute(f"ALTER TABLE rankings ADD COLUMN {col}")
        self.conn.executescript(V10_TRIGGERS)
        default = self.get_meta("default_ranking")
        self.conn.execute(
            "UPDATE rankings SET base_name=name, version=1, current_run=(CASE WHEN name=? THEN 1 ELSE NULL END), "
            "embedding_model=(SELECT e.model FROM embedding_sets e WHERE e.set_name=rankings.set_name) "
            "WHERE base_name IS NULL", (default,))

    def _migrate_v11(self) -> None:
        """v11: ``query_unlocks.seen_choice`` (unlock-seen-choice D2). Existing rows stay NULL ("not asked"); their
        ``counts_as_seen`` keeps its effect. Safe to run twice."""
        have = self.columns("query_unlocks")
        for col in V11_COLUMNS["query_unlocks"]:
            if col.split()[0] not in have:
                self.conn.execute(f"ALTER TABLE query_unlocks ADD COLUMN {col}")

    def _migrate_v12(self) -> None:
        """v12: ``sources.sample_of`` (openalex-profile-and-sampled-exports D4). Existing files stay NULL, a complete
        export, so their numbers do not change. Safe to run twice."""
        have = self.columns("sources")
        for col in V12_COLUMNS["sources"]:
            if col.split()[0] not in have:
                self.conn.execute(f"ALTER TABLE sources ADD COLUMN {col}")

    def get_meta(self, key: str) -> str | None:
        r = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return r["value"] if r else None

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                          (key, value))

    # ----- helpers -----
    def tx(self):
        return _Tx(self.conn)

    @staticmethod
    def _p(params: Any) -> Any:
        return params if isinstance(params, dict) else tuple(params)

    def execute(self, sql: str, params: Any = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, self._p(params))

    def executemany(self, sql: str, rows: Iterable[Any]) -> None:
        self.conn.executemany(sql, [self._p(r) for r in rows])

    def query(self, sql: str, params: Any = ()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, self._p(params)).fetchall()

    def one(self, sql: str, params: Any = ()) -> sqlite3.Row | None:
        return self.conn.execute(sql, self._p(params)).fetchone()

    def scalar(self, sql: str, params: Any = ()) -> Any:
        r = self.one(sql, params)
        return r[0] if r else None

    def tables(self) -> list[str]:
        return [r[0] for r in self.query("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]

    def counts(self) -> dict[str, int]:
        return {t: self.scalar(f"SELECT COUNT(*) FROM {t}") for t in self.tables()}

    def close(self) -> None:
        self.conn.close()

    @staticmethod
    def j(obj: Any) -> str:
        return json.dumps(obj, ensure_ascii=False, default=str)

    @staticmethod
    def unj(s: str | None) -> Any:
        return json.loads(s) if s else None


class _Tx:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def __enter__(self):
        # IMMEDIATE takes the write lock at the start: two writers at once (a double click) then wait for each other
        # (connect timeout) instead of both reading and the second failing with "database is locked" on its first write
        self.conn.execute("BEGIN IMMEDIATE")
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.conn.execute("COMMIT")
        else:
            self.conn.execute("ROLLBACK")
        return False
