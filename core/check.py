"""core/check.py — the wiki audit. Six checks, each looking for one specific
way the compiled wiki can go wrong.

No cloud imports here, no file paths. The pages, the index text, and the list
of document IDs are all handed in as arguments by the caller. That is what
lets every check in this file be tested with a small fake wiki and no store,
the same pattern as core/compile.py and core/query.py.
"""

from __future__ import annotations

import re

from core.models import Issue, Page, extract_citations, extract_links

MIN_SENTENCE_LENGTH = 40


# The one function this file exists for. Runs all six checks over the wiki
# and hands back every Issue they found, in one flat list.
def check_wiki(
    pages: dict[str, Page],
    index: str,
    all_doc_ids: list[str],
    excluded_doc_ids: list[str],
    max_words: int = 2000,
) -> list[Issue]:
    issues: list[Issue] = []
    issues.extend(broken_links(pages))
    issues.extend(orphan_pages(pages))
    issues.extend(uncited_sentences(pages))
    issues.extend(untraced_documents(pages, all_doc_ids, excluded_doc_ids))
    issues.extend(index_mismatch(pages, index))
    issues.extend(oversized_pages(pages, max_words))
    return issues


# ---------------------------------------------------------------
# Check 1: broken links
# ---------------------------------------------------------------


# Looks for a [[slug]] on some page that points at a page which does not
# exist. This is a problem because the query step would try to open that
# page and waste a model call on nothing. It works by collecting the name of
# every page that actually exists, then checking every outbound link on
# every page against that set.
def broken_links(pages: dict[str, Page]) -> list[Issue]:
    existing_slugs = set(pages)
    issues: list[Issue] = []

    for slug, page in pages.items():
        for target in extract_links(page.body):
            if target not in existing_slugs:
                issues.append(
                    Issue(kind="broken_link", page=slug, detail=target, severity="error")
                )

    return issues


# ---------------------------------------------------------------
# Check 2: orphan pages
# ---------------------------------------------------------------


# Looks for a page that no other page links to. This is a problem because a
# page like that can only ever be reached by picking it straight off the
# index — the multi-hop link-following the whole system depends on can never
# land there. It works by gathering every link from every page into one big
# set, then finding page names that never show up in it.
def orphan_pages(pages: dict[str, Page]) -> list[Issue]:
    linked_to: set[str] = set()
    for page in pages.values():
        linked_to.update(extract_links(page.body))

    issues: list[Issue] = []
    for slug in pages:
        if slug not in linked_to:
            issues.append(
                Issue(
                    kind="orphan_page",
                    page=slug,
                    detail="no other page links to this one",
                    severity="warning",
                )
            )

    return issues


# ---------------------------------------------------------------
# Check 3: sentences with no source behind them
# ---------------------------------------------------------------


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\[])")
_LIST_MARKER_RE = re.compile(r"^[-*]\s+|^\d+\.\s+")
_BOLD_LABEL_RE = re.compile(r"^\*\*[^*]+\*\*[.:]?$")
_DISAGREEMENT_MARKER_RE = re.compile(r"^\*\*(At issue|Not in dispute):\*\*")
_LINK_ONLY_RE = re.compile(r"^(see |also see |see also )?(\[\[[^\]]*\]\][.,;]?\s*)+$", re.IGNORECASE)

SIGNPOST_LENGTH = 80


# Cuts a page body into units to check one at a time — usually a sentence,
# but a whole bullet point stays in one piece. A bullet's citation usually
# sits at the very end, so sentence-splitting inside it flags its earlier
# clauses as uncited even though the bullet as a whole is sourced. Heading
# lines are dropped before any of this, since a heading is a title, not a
# claim. It works by walking the body line by line: a line starting with a
# list marker pulls in every line after it up to the next marker or a blank
# line, and that whole span becomes one unit; every other line is still
# split into individual sentences as before.
def _split_into_sentences(body: str) -> list[str]:
    units: list[str] = []
    lines = body.splitlines()
    i = 0

    while i < len(lines):
        line = lines[i].strip()
        if not line or line.startswith("#"):
            i += 1
            continue

        if _LIST_MARKER_RE.match(line):
            bullet_lines = [line]
            i += 1
            while i < len(lines) and lines[i].strip() and not _LIST_MARKER_RE.match(lines[i].strip()):
                bullet_lines.append(lines[i].strip())
                i += 1
            units.append(" ".join(bullet_lines))
            continue

        for piece in _SENTENCE_SPLIT_RE.split(line):
            piece = piece.strip()
            if piece:
                units.append(piece)
        i += 1

    return units


# Spots a sentence that is really just a bolded label, such as a "**Position
# — ...**" lead-in, or a list item like "- **Direct Comparison**:". Labels
# like this introduce a position or a section; they do not claim anything on
# their own, so they need no citation. It works by stripping off any leading
# list marker, then checking whether what is left is nothing but a single
# bolded phrase.
def _is_bare_label(sentence: str) -> bool:
    without_marker = _LIST_MARKER_RE.sub("", sentence, count=1).strip()
    return bool(_BOLD_LABEL_RE.match(without_marker))


# Spots the two fixed Disagreement markers, "**At issue:**" and "**Not in
# dispute:**". Unlike a Position label, SCHEMA.md's format puts the actual
# proposition right after these on the same line with no citation of its own
# — At issue states the question, Not in dispute states the common ground,
# and the citations live on the Position lines instead. It works by matching
# the fixed marker text at the start of the sentence.
def _is_disagreement_marker(sentence: str) -> bool:
    return bool(_DISAGREEMENT_MARKER_RE.match(sentence))


# Spots a sentence that is nothing but a pointer to another page, such as
# "See [[contamination-detection-methods]]." Pointing a reader elsewhere is
# not a claim, so it needs no citation. It works by matching the whole
# sentence against an optional "see" and one or more [[links]], with nothing
# else in between.
def _is_link_only(sentence: str) -> bool:
    return bool(_LINK_ONLY_RE.match(sentence.strip()))


# Spots a short signpost sentence built around a link, such as "For methods
# to find contamination, see [[contamination-detection-methods]]." It is not
# purely a link like _is_link_only checks for — there are a few words of
# connective text — but it is still just routing the reader, not making a
# claim, as long as it stays short. It works by checking the sentence is
# under the signpost length and contains at least one [[link]] anywhere.
def _is_short_signpost_with_link(sentence: str) -> bool:
    return len(sentence) < SIGNPOST_LENGTH and bool(extract_links(sentence))


# Decides whether a sentence is the kind of thing that ought to carry a
# citation at all. Very short fragments, bare labels, and link-only pointers
# make no factual claim, so flagging them as uncited would just be noise.
def _needs_a_citation(sentence: str) -> bool:
    if len(sentence) < MIN_SENTENCE_LENGTH:
        return False
    if _is_bare_label(sentence):
        return False
    if _is_disagreement_marker(sentence):
        return False
    if _is_link_only(sentence):
        return False
    if _is_short_signpost_with_link(sentence):
        return False
    return True


# Looks for a sentence in a page body with no [DOC-xxx] citation in it. This
# is a problem because SCHEMA.md forbids writing anything the corpus does not
# support — an uncited sentence is either the model's own training knowledge,
# or a claim nobody can trace back to a source. It works by splitting every
# page into sentences, skipping the ones that make no claim, and flagging
# whatever is left that has no citation attached.
def uncited_sentences(pages: dict[str, Page]) -> list[Issue]:
    issues: list[Issue] = []

    for slug, page in pages.items():
        for sentence in _split_into_sentences(page.body):
            if not _needs_a_citation(sentence):
                continue
            if extract_citations(sentence):
                continue
            issues.append(
                Issue(kind="uncited_sentence", page=slug, detail=sentence, severity="warning")
            )

    return issues


# ---------------------------------------------------------------
# Check 4: documents that left no trace
# ---------------------------------------------------------------


# Looks for a document ID that is cited on no page at all. This is a problem
# because the compiler read that document and produced nothing from it — a
# silent failure that nothing else would ever surface. It works by gathering
# every [DOC-xxx] cited anywhere in the wiki, then checking every known
# document ID against that set, skipping the ones on the excluded list.
def untraced_documents(
    pages: dict[str, Page], all_doc_ids: list[str], excluded_doc_ids: list[str]
) -> list[Issue]:
    cited: set[str] = set()
    for page in pages.values():
        cited.update(extract_citations(page.body))

    excluded = set(excluded_doc_ids)
    issues: list[Issue] = []

    for doc_id in all_doc_ids:
        if doc_id in cited or doc_id in excluded:
            continue
        issues.append(
            Issue(kind="untraced_document", page="", detail=doc_id, severity="error")
        )

    return issues


# ---------------------------------------------------------------
# Check 5: index mismatch
# ---------------------------------------------------------------


# Looks for a page file with no row in index.md, or an index row pointing at
# a page file that does not exist. This is a problem because the index is
# the only thing a query ever reads to pick a page — a page missing from it
# is invisible, and a row with no page behind it is a broken promise. It
# works by comparing the set of page files against the set of slugs listed
# in the index.
def index_mismatch(pages: dict[str, Page], index: str) -> list[Issue]:
    indexed_slugs = set(extract_links(index))
    page_slugs = set(pages)
    issues: list[Issue] = []

    for slug in sorted(page_slugs - indexed_slugs):
        issues.append(
            Issue(
                kind="index_mismatch",
                page=slug,
                detail="page file exists but is missing from index.md",
                severity="error",
            )
        )

    for slug in sorted(indexed_slugs - page_slugs):
        issues.append(
            Issue(
                kind="index_mismatch",
                page=slug,
                detail="index.md has a row for this slug but no page file exists",
                severity="error",
            )
        )

    return issues


# ---------------------------------------------------------------
# Check 6: oversized pages
# ---------------------------------------------------------------


# Looks for a page whose body has grown past the review threshold. This is
# not a mistake by itself — SCHEMA.md says a large page that still passes the
# summary test stays large — but it is a nudge for a human to go look and
# decide whether the page has quietly started doing two jobs. It works by
# counting the words in each page's body and comparing that to the limit.
def oversized_pages(pages: dict[str, Page], max_words: int) -> list[Issue]:
    issues: list[Issue] = []

    for slug, page in pages.items():
        word_count = len(page.body.split())
        if word_count > max_words:
            detail = f"{word_count:,} words"
            issues.append(
                Issue(kind="oversized_page", page=slug, detail=detail, severity="review")
            )

    return issues
