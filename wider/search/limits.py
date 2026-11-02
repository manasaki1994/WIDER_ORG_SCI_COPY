"""Limits the researcher applied in a database (vision change 2a, D10): a date range, languages and the reason, kept
per platform on ``Platform.limits`` in project.yaml and journalled ``platform_limits``. The CLI (`wider query limits`),
the Search strings page (POST /api/query/limits), 2b's ``LIMIT`` answer applier and 2c's PRISMA-S report all go
through :func:`set_limits` and :func:`limits`, so the four agree.
"""
from __future__ import annotations

import re
from datetime import date

_DATE = re.compile(r"\d{4}(?:-\d{2}(?:-\d{2})?)?")
DATES_HINT = "write it as 2010..2024 or 2010-01-01..2024-12-31; leave one side open for no bound (2010..)"


class LimitsError(ValueError):
    """A limit that cannot be stored (a range that does not parse or ends before it starts, an unknown platform)."""


def _real(d: str) -> bool:
    """A date that exists: month 1 to 12, a day the month has, year 1 or later (2010-13-45 and 0000 do not)."""
    y, m, dd = (d.split("-") + ["01", "01"])[:3]
    try:
        date(int(y), int(m), int(dd))
    except ValueError:
        return False
    return True


def parse_dates(text: str) -> tuple[str | None, str | None]:
    """``"2010..2024"`` → ``("2010", "2024")``; years, year-months and full dates; one side may be open."""
    a, sep, b = text.strip().partition("..")
    a, b = a.strip(), b.strip()
    if not sep or not (a or b) or any(x and not (_DATE.fullmatch(x) and _real(x)) for x in (a, b)):
        raise LimitsError(f"date range {text!r} does not parse; {DATES_HINT}")
    if a and b and a[:len(b)] > b[:len(a)]:   # compare on the shorter precision: 2024-05..2024 is fine
        raise LimitsError(f"date range {text!r} ends before it starts")
    return a or None, b or None


def _platform(project, key: str):
    for p in project.config.platforms:
        if p.key == key:
            return p
    chosen = ", ".join(p.key for p in project.config.platforms) or "none chosen yet"
    raise LimitsError(f"{key!r} is not one of the project's platforms ({chosen}); add it in Setup first")


def limits(project) -> dict[str, dict]:
    """Every chosen platform's limits (an empty dict for a platform without any), in the project's order."""
    return {p.key: dict(p.limits) for p in project.config.platforms}


def limits_line(lim: dict) -> str:
    """One line for the CLI, the page under the rendered string and the render header."""
    parts = []
    if lim.get("dates"):
        parts.append(f"dates {lim['dates']}")
    if lim.get("languages"):
        parts.append(f"languages {', '.join(lim['languages'])}")
    if lim.get("reason"):
        parts.append(f"reason: {lim['reason']}")
    return "limits: " + ("; ".join(parts) if parts else "none")


def set_limits(project, platform: str, dates: str | None = None, languages: list[str] | str | None = None,
               reason: str | None = None, by: str | None = None) -> dict:
    """Set the limits given (None leaves one as it is, an empty value clears it), journalled ``platform_limits``.
    Everything is checked before anything is written, so a refused range changes nothing. Returns the platform's
    limits, its line and the change id (None when nothing changed)."""
    from ..core import journal

    plat, new = _new_limits(project, platform, dates, languages, reason)
    cid = None
    if new != plat.limits:
        with journal.recording(project, "platform_limits", f"{platform} {limits_line(new)}", author=by) as rec:
            _apply_limits(project, plat, new)
        cid = rec.change_id
    return {"platform": platform, "limits": dict(plat.limits), "line": limits_line(plat.limits), "change_id": cid}


def _apply_limits(project, plat, new: dict) -> None:
    """Write checked limits to the platform and save project.yaml (inside the caller's journalled change: a
    ``refine_limit`` answer is one ``answer`` change, F3)."""
    plat.limits = new
    project.save()


def _new_limits(project, platform: str, dates: str | None = None, languages: list[str] | str | None = None,
                reason: str | None = None) -> tuple:
    """The platform and its limits after the change, checked; nothing is written."""
    plat = _platform(project, platform)
    new = dict(plat.limits)
    if dates is not None:
        if dates.strip():
            parse_dates(dates)
            new["dates"] = dates.strip()
        else:
            new.pop("dates", None)
    if languages is not None:
        items = languages.split(",") if isinstance(languages, str) else languages
        langs = [x.strip() for x in items if x.strip()]
        if langs:
            new["languages"] = langs
        else:
            new.pop("languages", None)
    if reason is not None:
        if reason.strip():
            new["reason"] = reason.strip()
        else:
            new.pop("reason", None)
    return plat, new
