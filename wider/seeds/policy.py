"""What the model pass of seed-paper mining may send (task 3.5): the ``seed_pdfs`` settings in ``project.yaml``
(``passages_to_cloud``, ``passage_tokens``, the loopback ``grobid_url`` of D26) and which backends were allowed passages. ``wider seeds policy`` and the
Settings "Seed papers" card call these; ``wider lookups status`` prints :func:`line`."""
from __future__ import annotations

from typing import Any

from ..core.project import Project

LETS_OUT = ("extracted passages of seed PDFs, never the file: one passage of at most {tokens:,} tokens per model call, "
            "to the construct backend when it is not local and you allowed it for passages")
NOTHING = "no seed-paper passage leaves this computer; a local backend still gets them"


def status(project: Project) -> dict[str, Any]:
    """The switch, the passage size, what it lets out, the construct backend with where it runs (no request: the
    backend row's locality) and the backends allowed passages. Never sends anything."""
    from ..core import backend_consent as bc

    sp = project.config.seed_pdfs
    backend = None
    try:
        cfg = project.provider_for("construct")
        st = bc.status_of(project, cfg, probe=False)
        backend = {"name": cfg.name, "locality": st["locality"], "locality_label": st["locality_label"],
                   "needs_switch": st["locality"] not in ("local", "test"),
                   "passages_allowed": "passages" in st["operations"]}
    except (KeyError, TypeError):
        pass
    if backend:   # one sentence, the same in `wider seeds policy` and the Settings card
        backend["state"] = ("local, so it gets passages and the switch does not apply" if not backend["needs_switch"]
                            else "gets no passage while the switch is off" if not sp.passages_to_cloud
                            else "allowed for passages" if backend["passages_allowed"]
                            else "asks once before the first passage; WIDER remembers your choice")
    allowed = sorted(n for n, rec in project.config.backend_consent.items() if "passages" in rec.operations)
    return {"passages_to_cloud": sp.passages_to_cloud, "passage_tokens": sp.passage_tokens,
            "lets_out": LETS_OUT.format(tokens=sp.passage_tokens) if sp.passages_to_cloud else NOTHING,
            "backend": backend, "passages_allowed": allowed, "grobid_url": sp.grobid_url}


def line(project: Project) -> str:
    s = status(project)
    on = "on" if s["passages_to_cloud"] else "off"
    allowed = f"; allowed for passages: {', '.join(s['passages_allowed'])}" if s["passages_allowed"] else ""
    return f"seed-paper passages to a backend that is not local: {on} ({s['lets_out']}){allowed}"


def set_policy(project: Project, passages_to_cloud: bool | None = None,
               passage_tokens: int | None = None, grobid_url: str | None = None) -> dict[str, Any]:
    """Change the switch, the passage size and/or the GROBID server (saved to ``project.yaml``, journalled with the
    other settings). ``ValueError`` for a size outside 100-8,000 tokens or a GROBID URL not on this computer;
    ``grobid_url`` ``"off"`` or empty clears it."""
    from ..core.models import SeedPdfs
    from .references import check_grobid_url

    sp = project.config.seed_pdfs
    new = sp.model_dump()
    if passages_to_cloud is not None:
        new["passages_to_cloud"] = bool(passages_to_cloud)
    if passage_tokens is not None:
        new["passage_tokens"] = int(passage_tokens)
    if grobid_url is not None:
        off = grobid_url.strip().lower() in ("", "off")
        new["grobid_url"] = "" if off else check_grobid_url(grobid_url)
    try:
        project.config.seed_pdfs = SeedPdfs(**new)
    except ValueError as e:
        raise ValueError("the passage size must be between 100 and 8,000 tokens") from e
    if project.config.seed_pdfs != sp:
        project.save()
    return status(project)
