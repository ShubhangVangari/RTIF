"""The ingest compiler: one raw document in, pages and decisions out.

No cloud imports. The model call is injected as `call_model`, so the whole
compile path runs offline against a fake. See SCHEMA.md §2.1 (paths), §5
(conflict policy), §6 (provenance).

Compilation is two stages so the prompt never carries all 31 pages:
  1. selection — index + document, returns up to 8 slugs (a retrieval step)
  2. compilation — schema + those page bodies + document, returns decisions
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from core.models import (
    Decision,
    Page,
    SourcePage,
    extract_links,
    markdown_to_page,
    page_to_markdown,
)

ROOT = Path(__file__).resolve().parent.parent
PROMPT_PATH = ROOT / "prompts" / "compile.txt"
STAGE_SEPARATOR = "=== STAGE 2 ==="

MAX_SELECTED_PAGES = 8
MAX_CONFUSION_SIBLINGS = 5

# Slug tokens too common to imply a topic family. "llm" prefixes a third of the
# index, so overlap on it would make every page everyone's sibling.
_CONNECTOR_TOKENS = {
    "a", "an", "and", "as", "at", "by", "for", "in", "of", "on", "the", "to",
    "vs", "llm",
}

_H1_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
_WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]*)?\]\]")
_RAW_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)


class CompileError(Exception):
    """The model returned something unusable. Names the doc_id and the stage."""


@dataclass
class CompileResult:
    decisions: list[Decision] = field(default_factory=list)
    pages: list[Page] = field(default_factory=list)
    source_page: SourcePage | None = None
    tokens_in: int = 0
    tokens_out: int = 0


SELECTION_SCHEMA = {
    "type": "object",
    "properties": {
        "slugs": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["slugs"],
}

COMPILE_SCHEMA = {
    "type": "object",
    "properties": {
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "enum": ["A", "B", "C"]},
                    "page": {"type": "string"},
                    "why": {"type": "string"},
                    "classification": {
                        "type": "string",
                        "enum": ["contradiction", "scope-difference", "supersession"],
                    },
                    "classification_reasoning": {"type": "string"},
                    "at_issue": {"type": "string"},
                    "aligns_with": {"type": "string"},
                    "new_position": {"type": "string"},
                    "positions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {"type": "string"},
                                "sources": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                            },
                            "required": ["label", "sources"],
                        },
                    },
                },
                "required": [
                    "path",
                    "page",
                    "why",
                    "classification",
                    "classification_reasoning",
                ],
            },
        },
        "pages": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "slug": {"type": "string"},
                    "body": {"type": "string"},
                    "summary": {"type": "string"},
                    "flags": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["slug", "body", "summary", "flags"],
            },
        },
        "source_page_body": {"type": "string"},
    },
    "required": ["decisions", "pages", "source_page_body"],
}


def document_sha256(document: str) -> str:
    return hashlib.sha256(document.encode("utf-8")).hexdigest()


def compile_document(
    schema: str,
    index: str,
    doc_id: str,
    document: str,
    load_page: Callable[[str], Page | None],
    call_model: Callable[[str, dict], dict],
    log: str = "",
) -> CompileResult:
    """Compile one document into page updates and decisions.

    `call_model` takes (prompt, response_schema) and returns parsed JSON. It may
    report usage under a `_usage` key ({"tokens_in": n, "tokens_out": n}); the
    key is consumed here and never reaches the payload validation.

    `log` is the current log.md. If the document's SHA-256 already appears in it
    the document has been ingested and an empty result is returned (idempotency).
    """
    digest = document_sha256(document)
    if digest in log:
        return CompileResult()

    stage1_prompt, stage2_prompt = _load_prompts()
    known_slugs = extract_links(index)

    selected, tokens_in, tokens_out = _select_pages(
        stage1_prompt, index, doc_id, document, known_slugs, call_model
    )

    # Load every known page once: the bodies of the selected ones go in the
    # prompt, and the rest are needed only to tell populated slugs from empty
    # ones (§4.3 — the compiler links more readily to a page it knows has content).
    known_pages: dict[str, Page] = {}
    for slug in known_slugs:
        page = load_page(slug)
        if page is not None:
            known_pages[slug] = page

    loaded = {slug: known_pages[slug] for slug in selected if slug in known_pages}
    confusion = {
        slug: known_pages[slug]
        for slug in _confusion_set(list(loaded), known_slugs, MAX_CONFUSION_SIBLINGS)
        if slug in known_pages
    }
    populated = [slug for slug, page in known_pages.items() if page.body.strip()]

    prompt = (
        stage2_prompt.replace("{{SCHEMA}}", schema)
        .replace("{{SLUGS}}", ", ".join(known_slugs) or "(none)")
        .replace("{{POPULATED}}", ", ".join(populated) or "(none yet)")
        .replace("{{PAGES}}", _render_pages(loaded))
        .replace("{{CONFUSION}}", _render_confusion(confusion))
        .replace("{{DOC_ID}}", doc_id)
        .replace("{{DOCUMENT}}", document)
    )
    payload, stage2_in, stage2_out = _call(
        call_model, prompt, COMPILE_SCHEMA, doc_id, "stage 2"
    )
    tokens_in += stage2_in
    tokens_out += stage2_out

    now = datetime.now(timezone.utc)
    decisions = _build_decisions(payload, doc_id)
    pages = _build_pages(payload, loaded, doc_id, now)

    body = payload.get("source_page_body")
    if not isinstance(body, str) or not body.strip():
        raise CompileError(f"{doc_id}: stage 2 returned no source_page_body")

    return CompileResult(
        decisions=decisions,
        pages=pages,
        source_page=_build_source_page(document, doc_id, body, digest, now),
        tokens_in=tokens_in,
        tokens_out=tokens_out,
    )


def _load_prompts() -> tuple[str, str]:
    text = PROMPT_PATH.read_text(encoding="utf-8")
    if STAGE_SEPARATOR not in text:
        raise CompileError(f"{PROMPT_PATH} has no '{STAGE_SEPARATOR}' separator")
    stage1, stage2 = text.split(STAGE_SEPARATOR, 1)
    return stage1.strip(), stage2.strip()


def _call(
    call_model: Callable[[str, dict], dict],
    prompt: str,
    schema: dict,
    doc_id: str,
    stage: str,
) -> tuple[dict, int, int]:
    try:
        payload = call_model(prompt, schema)
    except CompileError:
        raise
    except Exception as exc:
        raise CompileError(f"{doc_id}: {stage} model call failed: {exc}") from exc
    if not isinstance(payload, dict):
        raise CompileError(
            f"{doc_id}: {stage} returned {type(payload).__name__}, expected an object"
        )
    usage = payload.get("_usage") or {}
    return payload, int(usage.get("tokens_in", 0)), int(usage.get("tokens_out", 0))


def _select_pages(
    prompt_template: str,
    index: str,
    doc_id: str,
    document: str,
    known_slugs: list[str],
    call_model: Callable[[str, dict], dict],
) -> tuple[list[str], int, int]:
    prompt = (
        prompt_template.replace("{{INDEX}}", index)
        .replace("{{DOC_ID}}", doc_id)
        .replace("{{DOCUMENT}}", document)
    )
    payload, tokens_in, tokens_out = _call(
        call_model, prompt, SELECTION_SCHEMA, doc_id, "stage 1"
    )
    slugs = payload.get("slugs")
    if not isinstance(slugs, list):
        raise CompileError(f"{doc_id}: stage 1 returned no 'slugs' list")

    known = set(known_slugs)
    selected: list[str] = []
    for slug in slugs:
        if isinstance(slug, str) and slug in known and slug not in selected:
            selected.append(slug)
    return selected[:MAX_SELECTED_PAGES], tokens_in, tokens_out


def _topic_tokens(slug: str) -> set[str]:
    return {t for t in slug.split("-") if t and t not in _CONNECTOR_TOKENS}


def _confusion_set(selected: list[str], known_slugs: list[str], limit: int) -> list[str]:
    chosen = set(selected)
    selected_tokens = [_topic_tokens(s) for s in selected]
    scored: list[tuple[int, str]] = []
    for slug in known_slugs:
        if slug in chosen:
            continue
        tokens = _topic_tokens(slug)
        if not tokens:
            continue
        overlap = max((len(tokens & other) for other in selected_tokens), default=0)
        if overlap:
            scored.append((-overlap, slug))
    scored.sort()
    return [slug for _, slug in scored[:limit]]


def _render_pages(pages: dict[str, Page]) -> str:
    if not pages:
        return "(none selected — every concept in this document is a Path B candidate)"
    chunks = []
    for slug, page in pages.items():
        chunks.append(
            f"--- PAGE: {slug}\n"
            f"title: {page.title}\n"
            f"summary: {page.summary}\n"
            f"flags: {page.flags or '(none)'}\n"
            f"body:\n{page.body or '(empty — seeded from the survey, no body yet)'}"
        )
    return "\n\n".join(chunks)


def _render_confusion(pages: dict[str, Page]) -> str:
    if not pages:
        return "(none)"
    return "\n".join(f"- {slug}: {page.summary}" for slug, page in pages.items())


def _build_decisions(payload: dict, doc_id: str) -> list[Decision]:
    raw = payload.get("decisions")
    if not isinstance(raw, list) or not raw:
        raise CompileError(f"{doc_id}: stage 2 returned no decisions")

    decisions = []
    for item in raw:
        if not isinstance(item, dict):
            raise CompileError(f"{doc_id}: stage 2 decision is not an object")
        for key in ("path", "page", "why"):
            if not item.get(key):
                raise CompileError(f"{doc_id}: stage 2 decision missing '{key}'")
        if item["path"] not in ("A", "B", "C"):
            raise CompileError(
                f"{doc_id}: stage 2 decision has path {item['path']!r}, expected A, B or C"
            )
        decisions.append(
            Decision(
                path=item["path"],
                page=item["page"],
                why=item["why"],
                classification=item.get("classification"),
                classification_reasoning=item.get("classification_reasoning"),
                at_issue=item.get("at_issue"),
                aligns_with=item.get("aligns_with"),
                new_position=item.get("new_position"),
                positions=item.get("positions"),
            )
        )
    return decisions


def _build_pages(
    payload: dict, existing: dict[str, Page], doc_id: str, now: datetime
) -> list[Page]:
    raw = payload.get("pages")
    if not isinstance(raw, list):
        raise CompileError(f"{doc_id}: stage 2 returned no 'pages' list")

    pages = []
    for item in raw:
        if not isinstance(item, dict):
            raise CompileError(f"{doc_id}: stage 2 page entry is not an object")
        slug = item.get("slug")
        body = item.get("body")
        summary = item.get("summary")
        if not slug or not isinstance(slug, str):
            raise CompileError(f"{doc_id}: stage 2 page entry has no slug")
        if not body or not isinstance(body, str):
            raise CompileError(f"{doc_id}: stage 2 page {slug!r} has no body")
        if not summary or not isinstance(summary, str):
            raise CompileError(f"{doc_id}: stage 2 page {slug!r} has no summary")

        prior = existing.get(slug)
        page = Page(
            slug=slug,
            title=prior.title if prior else _title_from_body(body, slug),
            type="concept",
            summary=_unlink_summary(summary),
            body=body,
            updated=now,
            flags=[str(f) for f in (item.get("flags") or [])],
        )
        _assert_renders(page, doc_id)
        pages.append(page)
    return pages


def _unlink_summary(summary: str) -> str:
    """§4.1: links never appear in frontmatter, and a summary is frontmatter.

    Keeps the slug rather than the display text so the boundary clause stays
    verifiable by §3.7's "sibling slug resolves to an existing page" check.
    """
    return _WIKILINK_RE.sub(lambda match: match.group(1).strip(), summary)


def _title_from_body(body: str, slug: str) -> str:
    match = _H1_RE.search(body)
    if match:
        return match.group(1).strip()
    return slug.replace("-", " ").title()


def _assert_renders(page: Page, doc_id: str) -> None:
    try:
        reparsed = markdown_to_page(page_to_markdown(page))
    except Exception as exc:
        raise CompileError(
            f"{doc_id}: stage 2 page {page.slug!r} does not render: {exc}"
        ) from exc
    if reparsed.body != page.body or reparsed.summary != page.summary:
        raise CompileError(
            f"{doc_id}: stage 2 page {page.slug!r} does not round-trip through markdown"
        )


def _raw_frontmatter(document: str) -> dict[str, str]:
    """Parse a raw document's frontmatter as flat `key: value` lines.

    Deliberately not yaml.safe_load: nearly half the corpus has an unquoted
    colon or LaTeX in its title ("360$^\\circ$REA: Towards ..."), which YAML
    rejects. The block is flat by construction, so splitting on the first colon
    is both sufficient and far more robust.
    """
    match = _RAW_FRONTMATTER_RE.match(document)
    if not match:
        return {}
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        key, separator, value = line.partition(":")
        if separator and key.strip() and not key.startswith((" ", "\t", "#")):
            fields[key.strip()] = value.strip()
    return fields


def _split_authors(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(a).strip() for a in value if str(a).strip()]
    text = str(value or "").strip()
    if not text or text.lower() == "unknown":
        return []
    return [author.strip() for author in text.split(",") if author.strip()]


def _build_source_page(
    document: str, doc_id: str, body: str, digest: str, now: datetime
) -> SourcePage:
    front = _raw_frontmatter(document)
    raw_type = front.get("type", "").strip().lower()
    # Never guess these. source_type in particular decides whether a source is
    # academic or practitioner, and §5.3 forbids resolving by source authority
    # in either direction — a wrong label here is not a cosmetic defect.
    for required in ("title", "type"):
        if not front.get(required):
            raise CompileError(
                f"{doc_id}: raw document frontmatter has no '{required}'"
            )
    if raw_type not in ("arxiv", "web"):
        raise CompileError(
            f"{doc_id}: raw document has unknown type {raw_type!r}, expected arxiv or web"
        )
    return SourcePage(
        doc_id=doc_id,
        title=front["title"],
        source=front.get("source", ""),
        published=front.get("published", ""),
        authors=_split_authors(front.get("authors")),
        source_type="academic" if raw_type == "arxiv" else "practitioner",
        sha256=digest,
        ingested=now,
        body=body,
    )
