"""Data shapes and markdown+YAML serialisation for the wiki. See SCHEMA.md.

No cloud imports here, ever — this module must be testable without a cloud.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class Page:
    """A concept page. SCHEMA.md §1.1."""

    slug: str
    title: str
    type: str
    summary: str
    body: str
    updated: datetime
    flags: list[str] = field(default_factory=list)


@dataclass
class SourcePage:
    """One page per ingested document. SCHEMA.md §1.3."""

    doc_id: str
    title: str
    source: str
    published: str
    authors: list[str]
    source_type: str
    sha256: str
    ingested: datetime
    body: str


@dataclass
class Decision:
    """Per-page compiler output, streamed to the UI decision log. SCHEMA.md §5.5."""

    path: str
    page: str
    why: str
    classification: str | None = None
    at_issue: str | None = None
    aligns_with: str | None = None
    new_position: str | None = None
    positions: list[dict] | None = None


@dataclass
class Issue:
    """A single finding reported by check.py."""

    kind: str
    page: str
    detail: str
    severity: str


# ---------------------------------------------------------------------------
# Derivation
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_CITATION_RE = re.compile(r"\[(DOC-\d+)\]")
_LINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]*)?\]\]")


def _strip_code_blocks(body: str) -> str:
    return _FENCE_RE.sub("", body)


def extract_citations(body: str) -> list[str]:
    """Find `[DOC-041]` citations, in order of first appearance, deduplicated."""
    text = _strip_code_blocks(body)
    seen: set[str] = set()
    result: list[str] = []
    for doc_id in _CITATION_RE.findall(text):
        if doc_id not in seen:
            seen.add(doc_id)
            result.append(doc_id)
    return result


def extract_links(body: str) -> list[str]:
    """Find `[[slug]]` / `[[slug|display text]]` links, returning slugs only."""
    text = _strip_code_blocks(body)
    seen: set[str] = set()
    result: list[str] = []
    for slug in _LINK_RE.findall(text):
        if slug not in seen:
            seen.add(slug)
            result.append(slug)
    return result


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------


class _Folded(str):
    """Marker subclass: dump as a folded block scalar (`>-`), per SCHEMA.md §1.1."""


def _folded_representer(dumper: yaml.Dumper, data: str) -> yaml.Node:
    return dumper.represent_scalar("tag:yaml.org,2002:str", str(data), style=">")


yaml.add_representer(_Folded, _folded_representer, Dumper=yaml.SafeDumper)

_DT_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n\n(.*)\Z", re.DOTALL)


def _format_dt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime(_DT_FORMAT)


def _parse_dt(text: str) -> datetime:
    return datetime.strptime(text, _DT_FORMAT).replace(tzinfo=timezone.utc)


def _dump_frontmatter(fields: dict[str, Any]) -> str:
    return yaml.safe_dump(
        fields, sort_keys=False, allow_unicode=True, default_flow_style=False
    )


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    match = _FRONTMATTER_RE.match(text)
    if not match:
        raise ValueError("malformed page: missing '---' frontmatter delimiters")
    front_text, body = match.group(1), match.group(2)
    try:
        front = yaml.safe_load(front_text)
    except yaml.YAMLError as exc:
        raise ValueError(f"malformed page: invalid YAML frontmatter: {exc}") from exc
    if not isinstance(front, dict):
        raise ValueError("malformed page: frontmatter is not a mapping")
    return front, body


def page_to_markdown(page: Page) -> str:
    fields: dict[str, Any] = {
        "slug": page.slug,
        "title": page.title,
        "type": page.type,
        "summary": _Folded(page.summary),
        "updated": _format_dt(page.updated),
    }
    if page.flags:
        fields["flags"] = list(page.flags)
    return f"---\n{_dump_frontmatter(fields)}---\n\n{page.body}"


def markdown_to_page(text: str) -> Page:
    front, body = _split_frontmatter(text)
    try:
        return Page(
            slug=front["slug"],
            title=front["title"],
            type=front["type"],
            summary=front["summary"],
            body=body,
            updated=_parse_dt(front["updated"]),
            flags=list(front.get("flags") or []),
        )
    except KeyError as exc:
        raise ValueError(f"malformed page: missing field {exc}") from exc


def source_page_to_markdown(page: SourcePage) -> str:
    fields: dict[str, Any] = {
        "doc_id": page.doc_id,
        "title": page.title,
        "source": page.source,
        "published": page.published,
        "authors": list(page.authors),
        "source_type": page.source_type,
        "sha256": page.sha256,
        "ingested": _format_dt(page.ingested),
    }
    return f"---\n{_dump_frontmatter(fields)}---\n\n{page.body}"


def markdown_to_source_page(text: str) -> SourcePage:
    front, body = _split_frontmatter(text)
    try:
        return SourcePage(
            doc_id=front["doc_id"],
            title=front["title"],
            source=front["source"],
            published=front["published"],
            authors=list(front["authors"]),
            source_type=front["source_type"],
            sha256=front["sha256"],
            ingested=_parse_dt(front["ingested"]),
            body=body,
        )
    except KeyError as exc:
        raise ValueError(f"malformed source page: missing field {exc}") from exc
