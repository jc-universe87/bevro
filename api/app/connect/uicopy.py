"""What an app says to its own users, as evidence of what it is for.

A project with no README still tells the people who use it what it does:
the description in its page's <meta>, the caption under a search box, the
heading of a form. Those words were written for a person, which makes them
better material for "what is this for" than anything written for a program.

Read only from the project's own files, a bounded amount, the safe way
(Project.read_text). Only literal text is taken - never anything built at
run time, which could carry a value - and only short, sentence-like pieces.
"""

from __future__ import annotations

import re
from typing import Any

MAX_PIECES = 6
MAX_PIECE = 200

# <meta name="description" content="..."> in a page the project ships.
_META = re.compile(r"""<meta\s+[^>]*name=["']description["'][^>]*content=["']([^"']{12,300})["']""", re.IGNORECASE)
# Text a Python UI framework puts on screen: st.caption("..."), st.title("..."),
# gr.Markdown("..."), and the description= of a Gradio interface. Plain
# string literals only: an f-string or a variable is not copy.
_LITERAL = r"""(?:"(?P<dq>[^"{}\n]{12,300})"|'(?P<sq>[^'{}\n]{12,300})')"""
_PY_UI = re.compile(r"\b(?:st|gr)\.(?:title|header|subheader|caption|markdown|Markdown)\(\s*" + _LITERAL)
_PY_DESC = re.compile(r"\bdescription\s*=\s*" + _LITERAL)
_PAGES = ("index.html", "public/index.html", "frontend/index.html", "web/index.html", "templates/index.html", "static/index.html", "frontend/public/index.html")


def _clean(text: str) -> str | None:
    text = re.sub(r"[*_`#>]+", "", text)
    text = re.sub(r":[a-z_]+:", "", text)  # emoji shortcodes
    text = "".join(ch for ch in text if ch.isalnum() or ch.isspace() or ch in ".,;:!?'’()-—/&")
    text = " ".join(text.split()).strip(" -|:")
    if len(text) < 12 or not re.search(r"[a-z]{3}", text):
        return None
    return text[:MAX_PIECE]


def ui_copy(project: Any, sources: list[str]) -> list[str]:
    """Up to a few sentences the app shows its users, most descriptive first."""
    pieces: list[str] = []
    for rel in _PAGES:
        text = project.read_text(rel, 32_000)
        if text:
            pieces += [m.group(1) for m in _META.finditer(text)]
    for text in sources:
        for pattern in (_PY_UI, _PY_DESC):
            pieces += [m.group("dq") or m.group("sq") for m in pattern.finditer(text)]
    out: list[str] = []
    for piece in pieces:
        clean = _clean(piece)
        # A sentence says what something does; a label ("Settings") does not.
        if clean and clean not in out and (" " in clean and len(clean.split()) >= 4):
            out.append(clean)
        if len(out) >= MAX_PIECES:
            break
    # Longer, sentence-like pieces say more about purpose than headings do.
    return sorted(out, key=lambda t: (not t.endswith((".", "?", "!")), -len(t)))
