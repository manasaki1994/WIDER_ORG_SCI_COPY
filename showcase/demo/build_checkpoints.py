#!/usr/bin/env python3
"""Build the interactive demo's free checkpoints (design D3): imported, ranked and sampled.

Usage: .venv/bin/python showcase/demo/build_checkpoints.py [OUT]           (OUT defaults to ~/wider-showcase-data/demo)
       .venv/bin/python showcase/demo/build_checkpoints.py --paid [OUT]    (only finish OUT/screened and OUT/screened2)

Each checkpoint is a fresh project made by showcase/build_project.py with the same options as the paid run
(build_demo_project.sh: --provider openai --model gpt-4o-mini, the local bge-m3 embeddings), stopped before the first
model call, so the rank order and the samples agree with the screened checkpoints. No model is called and no key is
needed. Every change is signed "Reviewer A"; any other History author (a project made before that rule) is rewritten
too, so the recorded pages never show a computer's login name.

--paid finishes the copies of the paid run (build_demo_project.sh): it signs their History as Reviewer A and, in a
`screened` copy made before the script configured the second setting first, adds that setting (`openai-t07`) and its
consent, so the Screening page at `screened` offers it. Both are local settings; no model is called.
"""
from __future__ import annotations

import getpass
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
CHECKPOINTS = ("imported", "ranked", "sampled")
PAID = ("screened", "screened2")
SECOND = "openai-t07"
AUTHOR = "Reviewer A"


def build(name: str, out: Path, work: Path, example: Path) -> None:
    proj = work / name / "reviews" / "employee-voice"
    proj.parent.mkdir(parents=True)
    env = dict(os.environ, WIDER_HOME=str(work / name / "home"), WIDER_REVIEWER=AUTHOR,
               PYTHONPATH=str(REPO))                # this checkout's code, not wherever the venv points
    env.pop("OPENAI_API_KEY", None)                      # nothing here may reach a model
    subprocess.run([sys.executable, str(REPO / "showcase" / "build_project.py"), str(proj), "--example", str(example),
                    "--provider", "openai", "--model", "gpt-4o-mini", "--stop-at", name],
                   check=True, env=env, cwd=REPO)
    sign_history(proj)
    dest = out / name
    shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree(proj, dest)


def sign_history(proj: Path) -> int:
    """Rewrite every History author other than Reviewer A (for example the login name) to Reviewer A."""
    db = sqlite3.connect(proj / "store.sqlite")
    try:
        cols = {r[1] for r in db.execute("pragma table_info(history)")}
        if "author" not in cols:
            return 0
        n = db.execute("update history set author = ? where author is not null and author != ?",
                       (AUTHOR, AUTHOR)).rowcount
        db.commit()
        return n
    finally:
        db.close()


def add_second_setting(proj: Path) -> bool:
    """Add the second screening setting and its consent to a project that lacks it (the same local steps as
    build_demo_project.sh). True when something was added."""
    import yaml
    path = proj / "project.yaml"
    cfg = yaml.safe_load(path.read_text())
    if any(p["name"] == SECOND for p in cfg["providers"]):
        return False
    base = next(p for p in cfg["providers"] if p["name"] == "openai")
    cfg["providers"].append(dict(base, name=SECOND, temperature=0.7, seed=2))
    path.write_text(yaml.safe_dump(cfg, sort_keys=False))
    env = dict(os.environ, WIDER_REVIEWER=AUTHOR, PYTHONPATH=str(REPO))
    env.pop("OPENAI_API_KEY", None)
    subprocess.run([sys.executable, "-m", "wider", "providers", "allow", SECOND, "--for", "probe", "--for", "research",
                    "--dir", str(proj)], check=True, env=env, cwd=REPO)
    return True


def finish_paid(out: Path) -> None:
    for name in PAID:
        proj = out / name
        if not (proj / "store.sqlite").exists():
            sys.exit(f"{proj} is missing: run build_demo_project.sh first")
        if name == "screened" and add_second_setting(proj):
            print(f"{name}: added the second setting {SECOND} and its consent")
        print(f"{name}: {sign_history(proj)} History entries signed {AUTHOR}")


def main() -> None:
    args = sys.argv[1:]
    paid = "--paid" in args
    args = [x for x in args if x != "--paid"]
    out = Path(args[0]) if args else Path.home() / "wider-showcase-data" / "demo"
    out.mkdir(parents=True, exist_ok=True)
    if paid:
        finish_paid(out)
        names = PAID
    else:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            example = work / "example"
            subprocess.run([sys.executable, str(HERE / "make_demo_example.py"), str(example)], check=True)
            for name in CHECKPOINTS:
                build(name, out, work, example)
        names = CHECKPOINTS
    user = getpass.getuser()
    for name in names:
        db = sqlite3.connect(out / name / "store.sqlite")
        left = db.execute("select count(*) from history where author = ?", (user,)).fetchone()[0]
        db.close()
        if left:
            sys.exit(f"{name}: {left} History entries still name {user!r}")
    print(f"done: {', '.join(str(out / n) for n in names)}")


if __name__ == "__main__":
    main()
