"""The database catalogue (platforms.yaml) and the platforms a project has chosen from it.

A platform not in the catalogue can be added as a custom entry with a ``syntax`` key naming the catalogue
family whose renderer is used (for example a host that accepts EBSCO-style strings).
"""
from __future__ import annotations

from ..core.models import Platform
from ..core.project import Project
from .ir import Query
from .render import OTHER_SOURCES, RENDERERS, platforms, render

CATALOGUE_FIELDS = ("label", "style", "description", "coverage", "export_formats", "export_cap", "cap_note", "paste",
                    "proximity", "truncation", "max_length")
ROLES = ("development", "validation")


def display_cap(caps: dict | None) -> int | None:
    """The tightest known per-format export cap, for display; None when no cap is known."""
    vals = [int(c) for c in (caps or {}).values() if c]
    return min(vals) if vals else None


def cap_text(caps: dict | None) -> str:
    """The per-format caps in words, the tightest first ("2,000 per CSV export; 20,000 per RIS or BIB export"); one
    number when every format has the same cap; "" when none is known. The tightest cap alone hid that other formats
    allow ten times more (inspection 1 Nov 2026)."""
    by: dict[int, list[str]] = {}
    for f, c in (caps or {}).items():
        if c:
            by.setdefault(int(c), []).append(f.upper())
    if len(by) == 1:
        return f"{next(iter(by)):,}"
    def names(fs: list[str]) -> str:
        return fs[0] if len(fs) == 1 else ", ".join(fs[:-1]) + " or " + fs[-1]
    return "; ".join(f"{c:,} per {names(fs)} export" for c, fs in sorted(by.items()))


def catalogue() -> list[dict]:
    """Every catalogue platform with its description, coverage, export formats and caps, in catalogue order.

    ``export_cap`` is the tightest per-format cap (an int or None); ``export_caps`` is the per-format dict the
    import sanity check uses.
    """
    out = []
    for key, v in platforms().items():
        row = {"key": key, **{f: v.get(f) for f in CATALOGUE_FIELDS}}
        row["export_caps"] = dict(v.get("export_cap") or {})
        row["export_cap"] = display_cap(row["export_caps"])
        row["cap_text"] = cap_text(row["export_caps"])
        out.append(row)
    return out


def platform_labels() -> dict[str, str]:
    """Display label for every catalogue key (and the other import sources such as ``citation_chain``)."""
    return {**{c["key"]: c["label"] or c["key"] for c in catalogue()}, **OTHER_SOURCES}


def syntax_families() -> list[str]:
    """Style keys a custom platform can render with."""
    return list(RENDERERS)


def style_for(platform: Platform) -> str:
    """Renderer key for a platform: its own catalogue style, or the ``syntax`` family of a custom entry."""
    if platform.key in platforms():
        return platforms()[platform.key].get("style") or platform.key
    if platform.syntax and platform.syntax in RENDERERS:
        return platform.syntax
    raise ValueError(f"platform {platform.key!r} is not in the catalogue and has no known syntax family "
                     f"(choose one of {', '.join(RENDERERS)})")


def project_platforms(project: Project) -> list[dict]:
    """The chosen platforms in search order with their role, catalogue entry and renderer style.

    The first platform is the development platform unless a role was set explicitly; the rest are validation
    platforms, checked only after the query is frozen.
    """
    plats = project.config.platforms
    explicit_dev = any(p.role == "development" for p in plats)
    out = []
    for i, p in enumerate(plats):
        entry = platforms().get(p.key, {})
        role = p.role if explicit_dev else ("development" if i == 0 else "validation")
        try:
            style = style_for(p)
        except ValueError:
            style = None
        caps = dict(entry.get("export_cap") or {})
        out.append({"key": p.key, "label": p.label or entry.get("label") or p.key, "role": role, "syntax": p.syntax,
                    "style": style, "custom": p.key not in platforms(), "position": i,
                    **{f: entry.get(f) for f in CATALOGUE_FIELDS if f not in ("label", "style", "export_cap")},
                    "export_caps": caps, "export_cap": display_cap(caps), "cap_text": cap_text(caps)})
    return out


def render_project(project: Project, query: Query, pretty: bool = True) -> dict[str, str]:
    """Search strings for the chosen platforms only, keyed by platform key (custom entries use their syntax family)."""
    out = {}
    for p in project.config.platforms:
        out[p.key] = render(query, style_for(p), pretty=pretty)
    return out


# ----------------------------------------------------------------- editing the choice
def _find(project: Project, key: str) -> int:
    for i, p in enumerate(project.config.platforms):
        if p.key == key:
            return i
    raise KeyError(f"platform {key!r} is not among the project's platforms")


def add_platform(project: Project, key: str, label: str | None = None, syntax: str | None = None,
                 role: str | None = None) -> Platform:
    """Add a catalogue platform, or a custom one (with ``syntax``); an existing entry is updated in place."""
    key = key.strip().lower().replace(" ", "_")
    if not key:
        raise ValueError("platform key is empty")
    entry = platforms().get(key)
    if entry is None and not syntax:
        raise ValueError(f"{key!r} is not in the catalogue; add it with a syntax family ({', '.join(RENDERERS)})")
    if syntax and syntax not in RENDERERS:
        raise ValueError(f"unknown syntax family {syntax!r}; choose one of {', '.join(RENDERERS)}")
    if role and role not in ROLES:
        raise ValueError("role must be development or validation")
    plats = project.config.platforms
    try:
        i = _find(project, key)
        p = plats[i]
        if label:
            p.label = label
        if syntax:
            p.syntax = syntax
        if role:
            p.role = role
    except KeyError:
        if role is None:
            role = "development" if not plats else "validation"
        p = Platform(key=key, label=label or (entry or {}).get("label") or key, role=role,
                     syntax=None if entry else syntax)
        plats.append(p)
    if p.role == "development":
        for other in plats:
            if other is not p:
                other.role = "validation"
    project.save()
    return p


def remove_platform(project: Project, key: str) -> None:
    i = _find(project, key)
    was_dev = project.config.platforms[i].role == "development"
    del project.config.platforms[i]
    if was_dev and project.config.platforms:
        project.config.platforms[0].role = "development"
    project.save()


def order_platforms(project: Project, keys: list[str]) -> list[Platform]:
    """Reorder: listed keys first in the given order, unlisted ones keep their relative order after them."""
    plats = project.config.platforms
    by_key = {p.key: p for p in plats}
    unknown = [k for k in keys if k not in by_key]
    if unknown:
        raise KeyError(f"not among the project's platforms: {', '.join(unknown)}")
    seen = set()
    ordered = []
    for k in keys:
        if k not in seen:
            ordered.append(by_key[k])
            seen.add(k)
    ordered += [p for p in plats if p.key not in seen]
    project.config.platforms = ordered
    project.save()
    return ordered


def set_role(project: Project, key: str, role: str) -> Platform:
    """Make one platform the development platform (the others become validation) or a validation platform."""
    if role not in ROLES:
        raise ValueError("role must be development or validation")
    i = _find(project, key)
    plats = project.config.platforms
    plats[i].role = role
    if role == "development":
        for j, other in enumerate(plats):
            if j != i:
                other.role = "validation"
    elif not any(p.role == "development" for p in plats):
        plats[0].role = "development"  # there is always exactly one development platform
    project.save()
    return plats[i]
