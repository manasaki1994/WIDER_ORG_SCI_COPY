"""A review project: one directory holding project.yaml, benchmark.csv, inputs, artifacts, store and runs."""
from __future__ import annotations

import contextlib
import csv
import fcntl
import io
import os
import re
import threading
from pathlib import Path

import yaml

from .. import __version__
from ..providers.presets import config_from_preset
from .models import BenchmarkPaper, EmbeddingConfig, Platform, ProjectConfig, ProviderConfig
from .provenance import utcnow
from .store import Store
from .text import clean_doi

PROJECT_FILE = "project.yaml"

BENCHMARK_FILE = "benchmark.csv"
# the columns of benchmark.csv; the last seven came with schema 5 and may be missing in a file edited by hand
BENCHMARK_HEADER = ["benchmark_id", "title", "doi", "set", "year", "first_author", "note", "source", "added_at",
                    "validation_since", "validation_since_unlock", "was_development", "keywords", "keywords_from"]
SUBDIRS = (
    "inputs/ris",
    "prior_reviews",
    "artifacts/cells",
    "artifacts/codebook",
    "artifacts/synonyms",
    "artifacts/anchors",
    "artifacts/near_miss",
    "artifacts/queries",
    "artifacts/prompts",
    "embeddings",
    "runs",
    "exports",
)

PROJECT_GITIGNORE = """# WIDER review project: raw exports, abstracts and model responses stay local.
inputs/
prior_reviews/
store.sqlite*
embeddings/
runs/
exports/
review-packages/
reporting/
assistant/
.env
"""

DEFAULT_PROVIDERS = [
    ProviderConfig(name="ollama", kind="ollama", model="qwen3:8b", base_url="http://127.0.0.1:11434",
                   timeout_s=1800, extra={"think": False}),
    ProviderConfig(name="openai", kind="openai_compat", model="gpt-4o-mini",
                   base_url="https://api.openai.com/v1", api_key_env="OPENAI_API_KEY"),
    ProviderConfig(name="anthropic", kind="anthropic", model="claude-sonnet-5-5", api_key_env="ANTHROPIC_API_KEY"),
    config_from_preset("claude-code"),
]

DEFAULT_EMBEDDINGS = [
    EmbeddingConfig(name="openai-large-1024", kind="openai", model="text-embedding-3-large",
                    api_key_env="OPENAI_API_KEY", dim=1024),
    EmbeddingConfig(name="bge-m3", kind="sentence_transformers", model="BAAI/bge-m3", batch_size=16),  # 568M params, 8k tokens: 64 swaps on 16 GB
    EmbeddingConfig(name="bge-base", kind="sentence_transformers", model="BAAI/bge-base-en-v1.5"),
    EmbeddingConfig(name="minilm", kind="sentence_transformers", model="sentence-transformers/all-MiniLM-L6-v2"),
    EmbeddingConfig(name="ollama-nomic", kind="ollama", model="nomic-embed-text", base_url="http://127.0.0.1:11434"),
]


def write_atomic(path: Path, text: str) -> None:
    """Write `text` to `path` through a temporary file and a rename, so a reader never sees a half-written file."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


class ProjectNotFound(FileNotFoundError):
    """The folder (and its parents) holds no project.yaml."""


class ProjectBroken(RuntimeError):
    """The project folder cannot be read as it is (bad project.yaml, a damaged or missing store); the message names
    the file and what to do (final check 1 Nov 2026)."""


class FileChangedSince(RuntimeError):
    """project.yaml or benchmark.csv was changed by another window or command after this one read it; writing now
    would undo that change (final check 1 Nov 2026)."""


@contextlib.contextmanager
def file_lock(path: Path):
    """An exclusive lock beside ``path`` (``.<name>.lock``) for one read-check-write, across processes."""
    lock = path.with_name(f".{path.name}.lock")
    with open(lock, "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _check_unchanged(path: Path, seen: str | None) -> None:
    if seen is None or not path.exists():
        return
    if path.read_text(encoding="utf-8-sig") != seen:
        raise FileChangedSince(f"{path.name} was changed by another window or command since this one read it; "
                               "nothing was saved. Reload and try again.")


class Project:
    def __init__(self, root: str | Path, config: ProjectConfig):
        self.root = Path(root).resolve()
        self._config = config
        self._store: Store | None = None
        self._seen: dict[str, str | None] = {}  # file name -> text as last read, for FileChangedSince

    @property
    def config(self) -> ProjectConfig:
        return self._config

    @config.setter
    def config(self, value: ProjectConfig) -> None:
        """Putting a whole config in place (an undo re-reading project.yaml) takes the file as it now stands."""
        self._config = value
        f = self.root / PROJECT_FILE
        if PROJECT_FILE in self._seen and f.exists():
            self._seen[PROJECT_FILE] = f.read_text(encoding="utf-8-sig")

    # ----- lifecycle -----
    @classmethod
    def init(
        cls,
        root: str | Path,
        name: str | None = None,
        title: str = "",
        questions: list[str] | None = None,
        platforms: list[str] | None = None,
        exist_ok: bool = False,
    ) -> Project:
        root = Path(root).resolve()
        if (root / PROJECT_FILE).exists() and not exist_ok:
            raise FileExistsError(f"{root / PROJECT_FILE} already exists")
        from ..search.catalogue import platform_labels  # lazy: the catalogue module imports this one

        labels = platform_labels()
        keys = [k.lower().strip() for k in platforms or []]
        if unknown := [k for k in keys if k not in labels]:
            raise ValueError(f"unknown platform {', '.join(unknown)}; choose from {', '.join(labels)} "
                             "(custom platforms are added on the Search page)")
        root.mkdir(parents=True, exist_ok=True)
        for d in SUBDIRS:
            (root / d).mkdir(parents=True, exist_ok=True)
        plats = []
        for i, key in enumerate(keys):
            plats.append(Platform(key=key, label=labels[key],
                                  role="development" if i == 0 else "validation"))
        config = ProjectConfig(
            name=name or root.name,
            title=title,
            questions=questions or [],
            platforms=plats,
            providers=list(DEFAULT_PROVIDERS),
            default_provider="ollama",
            embeddings=list(DEFAULT_EMBEDDINGS),
            default_embedding="bge-m3",
            created_at=utcnow(),
            wider_version=__version__,
        )
        p = cls(root, config)
        p.save()
        gi = root / ".gitignore"
        if not gi.exists():
            gi.write_text(PROJECT_GITIGNORE, encoding="utf-8")
        bm = root / BENCHMARK_FILE
        if not bm.exists():
            bm.write_text(",".join(BENCHMARK_HEADER) + "\n", encoding="utf-8")
        p.store  # create store.sqlite with the schema
        p.close()
        return p

    @classmethod
    def load(cls, root: str | Path) -> Project:
        root = Path(root).resolve()
        f = root / PROJECT_FILE
        if not f.exists():
            raise ProjectNotFound(f"no {PROJECT_FILE} in {root}; start a project there with `wider init {root}`")
        from pydantic import ValidationError

        text = f.read_text(encoding="utf-8")
        try:
            data = yaml.safe_load(text) or {}
        except yaml.YAMLError as e:
            mark = getattr(e, "problem_mark", None)
            where = f" at line {mark.line + 1}" if mark else ""
            raise ProjectBroken(f"{f} is not valid YAML{where}; fix the file by hand or restore it from a backup "
                                "(History lists each change to it)") from None
        if not isinstance(data, dict):
            raise ProjectBroken(f"{f} does not hold project settings; restore it from a backup") from None
        try:
            config = ProjectConfig.model_validate(data)
        except ValidationError as e:
            first = e.errors()[0]
            field = ".".join(str(x) for x in first["loc"]) or "the file"
            raise ProjectBroken(f"{f}: {field}: {first['msg']} ({e.error_count()} problem(s)); fix the file by hand "
                                "or restore it from a backup") from None
        p = cls(root, config)
        p._seen[PROJECT_FILE] = text
        return p

    @classmethod
    def find(cls, start: str | Path | None = None) -> Project:
        """Walk up from ``start`` (default cwd) to the nearest directory with a project.yaml."""
        here = Path(start or Path.cwd()).resolve()
        for d in (here, *here.parents):
            if (d / PROJECT_FILE).exists():
                return cls.load(d)
        raise ProjectNotFound(f"no WIDER project here: no {PROJECT_FILE} in {here} or its parents. Give the project folder with --dir, or start one with `wider init {here}`")

    def save(self) -> None:
        """Write project.yaml; every change to it is journaled (history, undo) once the store exists."""
        data = self.config.model_dump(mode="json")
        f = self.root / PROJECT_FILE
        after = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100)
        with file_lock(f):
            _check_unchanged(f, self._seen.get(PROJECT_FILE))
            before = f.read_text(encoding="utf-8") if f.exists() else None
            write_atomic(f, after)
            self._seen[PROJECT_FILE] = after
        from .journal import config_saved
        config_saved(self, before, after)

    # ----- paths -----
    @property
    def store_path(self) -> Path:
        return self.root / "store.sqlite"

    @property
    def store(self) -> Store:
        if self._store is None:
            import sqlite3

            art = self.root / "artifacts"
            if not self.store_path.exists() and art.is_dir() and any(x.is_file() for x in art.rglob("*")):
                raise ProjectBroken(f"{self.store_path} is missing, although this project has saved work in {art}. "
                                    "Restore store.sqlite from a backup; WIDER did not create an empty one, so the "
                                    "record is not replaced by a blank store.")
            try:
                self._store = Store(self.store_path)
            except sqlite3.DatabaseError as e:
                raise ProjectBroken(f"{self.store_path} cannot be read ({e}); restore it from a backup") from None
            # once per project: result sets of a project from before C2 (offline, idempotent through a meta flag)
            from ..screen.result_sets import migrate_legacy
            migrate_legacy(self)
            self._benchmark_csv_v5()
        return self._store

    def _benchmark_csv_v5(self) -> None:
        """Once, after the store moved to schema 5: back benchmark.csv up as benchmark.v4.csv and rewrite it with
        BENCHMARK_HEADER and the migration's values (added at the migration time, Validation since then, before every
        unlock, Development papers marked), so the file and the store agree."""
        st = self._store
        flag = st.get_meta("benchmark_csv_v5")
        if not flag or not flag.startswith("pending "):
            return
        now = flag.removeprefix("pending ")
        f = self.root / BENCHMARK_FILE
        if f.exists():
            import shutil
            backup = self.root / "benchmark.v4.csv"
            if not backup.exists():
                shutil.copy2(f, backup)
            papers = self.read_benchmark()
            for b in papers:
                b.added_at = b.added_at or now
                b.validation_since = (b.validation_since or now) if b.set == "Validation" else ""
                b.validation_since_unlock = 0
                b.was_development = b.set == "Development"
            self.write_benchmark(papers)
        st.set_meta("benchmark_csv_v5", f"done {now}")

    def close(self) -> None:
        """Close the store; the next access to ``store`` reopens it."""
        if self._store is not None:
            self._store.close()
            self._store = None

    def path(self, *parts: str) -> Path:
        return self.root.joinpath(*parts)

    def artifact_dir(self, kind: str) -> Path:
        return self.root / "artifacts" / kind

    def run_dir(self, kind: str, stamp: str) -> Path:
        # a folder name made from names the researcher typed (pass, sample) stays one folder inside runs/<kind>/
        stamp = re.sub(r"[^A-Za-z0-9_.-]", "_", stamp).lstrip(".") or "run"
        d = self.root / "runs" / kind / stamp
        d.mkdir(parents=True, exist_ok=True)
        return d

    # ----- benchmark -----
    def read_benchmark(self) -> list[BenchmarkPaper]:
        f = self.root / BENCHMARK_FILE
        if not f.exists():
            return []
        out = []
        self._seen[BENCHMARK_FILE] = f.read_text(encoding="utf-8-sig")
        with open(f, newline="", encoding="utf-8-sig") as fh:
            for i, row in enumerate(csv.DictReader(fh), 1):
                row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
                title = row.get("title", "")
                if not title and not row.get("doi"):
                    continue
                s = row.get("set", "Development").strip().lower()
                s = "Validation" if s.startswith("val") else "Development"
                since_unlock = row.get("validation_since_unlock", "")
                out.append(BenchmarkPaper(
                    benchmark_id=row.get("benchmark_id") or f"B{i:03d}",
                    title=title,
                    doi=clean_doi(row.get("doi")),
                    set=s,
                    year=int(row["year"]) if row.get("year", "").isdigit() else None,
                    first_author=row.get("first_author") or None,
                    note=row.get("note", ""),
                    source=row.get("source") or "researcher",
                    added_at=row.get("added_at", ""),
                    validation_since=row.get("validation_since", ""),
                    # missing columns (a file edited by hand) take the conservative defaults: before every unlock
                    # and once in Development, so no paper can pass as new validation
                    validation_since_unlock=int(since_unlock) if since_unlock.isdigit() else 0,
                    was_development=(row["was_development"].lower() in ("1", "true", "yes")
                                     if "was_development" in row else True),
                    keywords=row.get("keywords", ""),
                    keywords_from=row.get("keywords_from", ""),
                ))
        return out

    def write_benchmark(self, papers: list[BenchmarkPaper]) -> None:
        f = self.root / BENCHMARK_FILE
        buf = io.StringIO(newline="")
        w = csv.writer(buf)
        w.writerow(BENCHMARK_HEADER)
        for p in papers:
            w.writerow([p.benchmark_id, p.title, p.doi or "", p.set, p.year or "", p.first_author or "", p.note,
                        p.source, p.added_at, p.validation_since, p.validation_since_unlock,
                        1 if p.was_development else 0, p.keywords, p.keywords_from])
        with file_lock(f):
            _check_unchanged(f, self._seen.get(BENCHMARK_FILE))
            tmp = f.with_name(f".{f.name}.{os.getpid()}.{threading.get_ident()}.tmp")
            tmp.write_bytes(buf.getvalue().encode("utf-8"))
            os.replace(tmp, f)
            self._seen[BENCHMARK_FILE] = buf.getvalue().replace("\r\n", "\n")

    def provider(self, name: str | None = None) -> ProviderConfig:
        name = name or self.config.default_provider
        for p in self.config.providers:
            if p.name == name:
                return p
        raise KeyError(f"provider {name!r} not in project.yaml")

    def provider_for(self, stage: str) -> ProviderConfig:
        """Provider configured for a stage (``config.stage_providers``), falling back to the default provider."""
        override = self.config.stage_providers.get(stage)
        if override:
            try:
                return self.provider(override)
            except KeyError:
                pass  # a provider that was removed since: fall back instead of blocking the stage
        return self.provider()

    def embedding(self, name: str | None = None) -> EmbeddingConfig:
        name = name or self.config.default_embedding
        for e in self.config.embeddings:
            if e.name == name:
                return e
        raise KeyError(f"embedding {name!r} not in project.yaml")
