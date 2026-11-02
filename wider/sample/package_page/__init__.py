"""The page template of a review package (reviewer-roles-and-blinding D3): ``page.html``, ``page.js`` and
``page.css`` are inlined into one file with the package's data, under a content-security rule whose script and style
hashes are computed from the inlined text, so the page runs its own code and loads nothing else."""
from __future__ import annotations

import base64
import hashlib
import html
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _hash(text: str) -> str:
    return "sha256-" + base64.b64encode(hashlib.sha256(text.encode("utf-8")).digest()).decode("ascii")


def data_json(data: dict) -> str:
    """The data block: JSON with every ``<`` written ``\\u003c`` (so every ``</`` too), so no text in a record can end
    the script element or open another."""
    return json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")


def csp(script: str, style: str) -> str:
    return (f"default-src 'none'; script-src '{_hash(script)}'; style-src '{_hash(style)}'; img-src 'none'; "
            f"connect-src 'none'; form-action 'none'; base-uri 'none'")


def render(data: dict) -> str:
    """The whole page for ``data`` (see ``package.page_data``)."""
    page = (HERE / "page.html").read_text(encoding="utf-8")
    script = (HERE / "page.js").read_text(encoding="utf-8")
    style = (HERE / "page.css").read_text(encoding="utf-8")
    out = page.replace("{{CSP}}", csp(script, style)).replace("{{TITLE}}", html.escape(f"Review package for {data['reviewer']}"))
    out = out.replace("{{STYLE}}", style).replace("{{SCRIPT}}", script)
    return out.replace("{{DATA}}", data_json(data))   # last: a record's text is never read as a placeholder
