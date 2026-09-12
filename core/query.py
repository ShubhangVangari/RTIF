"""core/query.py — answers a question by reading pages out of the wiki.

No cloud imports here. This file does read one thing off disk — its own
prompt file, prompts/query.txt, anchored on __file__ exactly the way
core/compile.py reads prompts/compile.txt. Everything else — the index text,
how to load a page, how to talk to the model — is handed in as an argument by
the caller. That is what lets this whole file be tested with fake helpers and
no network.

Tool calling, the simple way. Gemini supports native function-calling, but
that means core/query.py would have to know about the Google SDK's
FunctionDeclaration objects, which breaks the "no cloud imports" rule. Instead
we use a plain JSON convention: the model replies with {"read_page": "slug"}
to ask for a page, or {"answer": "text"} to finish. scripts/ask.py constrains
this with a response_schema. This is the simpler of the two tool-calling
options the task offered, and it is why the file stays this short.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from core.models import Page, extract_links

ROOT = Path(__file__).resolve().parent.parent
PROMPT_PATH = ROOT / "prompts" / "query.txt"


# Gemini's reply must always match this shape: one optional field to ask for
# a page, one optional field to give the final answer.
RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "read_page": {"type": "string"},
        "answer": {"type": "string"},
    },
}


# Used only for the forced final call after the hop cap. There is no
# "read_page" property here at all, so the model has no way to ask for
# another page — an answer is the only shape it can reply with.
FINAL_ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
    },
    "required": ["answer"],
}


# What answer_question hands back: the answer itself, plus a full account of
# how it got there.
@dataclass
class Answer:
    text: str
    pages_read: list[str]
    trace: list[str]
    hops: int
    tokens_in: int
    tokens_out: int
    latency_seconds: float


# The one function this file exists for. Ask Gemini a question, let it read
# pages until it is ready, and bring back the answer plus the trail it left.
def answer_question(
    index: str,
    question: str,
    load_page: Callable[[str], Page | None],
    call_model: Callable[..., Any],
    max_hops: int = 5,
) -> Answer:
    start_time = time.perf_counter()
    pages_read: list[str] = []
    repeat_counts: dict[str, int] = {}
    trace: list[str] = []
    tokens_in = 0
    tokens_out = 0
    hops = 0
    conversation = _build_first_message(index, question)
    answer_text = None

    # -----------------------------------------------------------------
    # Main loop: ask Gemini, then either fetch a page for it or take its
    # answer and stop. Every hop gets one line in the trace, whether it
    # succeeded, failed, or was a repeat — not just the ones that worked.
    # -----------------------------------------------------------------
    for _ in range(max_hops):
        reply = call_model(conversation, RESPONSE_SCHEMA)
        hops += 1
        tokens_in, tokens_out = _tally_usage(reply, tokens_in, tokens_out)

        # Gemini wrote an answer. We are done.
        if reply.get("answer"):
            answer_text = reply["answer"]
            trace.append("answered")
            break

        # Gemini wants a page. Go get it and hand it back.
        conversation, note = _handle_page_request(
            reply.get("read_page"), conversation, pages_read, repeat_counts, load_page, index
        )
        trace.append(note)

    # -----------------------------------------------------------------
    # Ran out of hops without an answer. Ask once more, but this time with
    # a schema that has no "read_page" option at all — an answer is the
    # only shape Gemini can reply with, so this really is the last word.
    # -----------------------------------------------------------------
    if answer_text is None:
        trace.append(f"hit the {max_hops}-hop limit — asking once more")
        conversation += (
            "\n\nYou are out of hops. Answer now, using what you have "
            "already read. You cannot request another page."
        )
        reply = call_model(conversation, FINAL_ANSWER_SCHEMA)
        hops += 1
        tokens_in, tokens_out = _tally_usage(reply, tokens_in, tokens_out)
        answer_text = reply.get("answer", "")
        trace.append("answered" if answer_text else "still no answer")

    latency_seconds = time.perf_counter() - start_time
    return Answer(
        text=answer_text,
        pages_read=pages_read,
        trace=trace,
        hops=hops,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        latency_seconds=latency_seconds,
    )


# Read our own ground rules off disk, fresh, every time we build the first
# message — so an edit to prompts/query.txt takes effect without restarting
# anything. Same pattern as core/compile.py's prompt file.
def _load_instructions() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


# Put together everything Gemini sees before it has read a single page: the
# ground rules, the map of pages, and the question we want answered.
def _build_first_message(index: str, question: str) -> str:
    instructions = _load_instructions()
    return f"{instructions}\n\n{index}\n\nQuestion: {question}"


# Add this call's token counts to the running totals we hand back at the end.
def _tally_usage(reply: dict, tokens_in: int, tokens_out: int) -> tuple[int, int]:
    usage = reply.get("_usage", {})
    tokens_in += usage.get("tokens_in", 0)
    tokens_out += usage.get("tokens_out", 0)
    return tokens_in, tokens_out


# Gemini asked for a page. Work out which of three things happened, and
# answer accordingly: already read, does not exist, or here it is. Returns
# the updated conversation, plus a plain-English note of what just happened
# for the trace.
def _handle_page_request(
    slug: str | None,
    conversation: str,
    pages_read: list[str],
    repeat_counts: dict[str, int],
    load_page: Callable[[str], Page | None],
    index: str,
) -> tuple[str, str]:
    if not slug:
        nudge = "\n\nPlease ask for a page by slug, or write your answer."
        return conversation + nudge, "asked for a page but did not say which one"

    if slug in pages_read:
        repeat_counts[slug] = repeat_counts.get(slug, 0) + 1
        note = f"asked for {slug} again — already read"
        return _note_already_read(conversation, slug, repeat_counts[slug]), note

    page = load_page(slug)
    if page is None:
        note = f"asked for {slug} — no such page"
        return _note_page_missing(conversation, slug, index), note

    pages_read.append(slug)
    note = f"read {slug}"
    return _note_page_found(conversation, page), note


# Gemini asked for something it already has. The first time, a plain notice
# is enough. Asking again after that means the notice did not land, so the
# second time onward we stop suggesting and tell it directly to answer.
def _note_already_read(conversation: str, slug: str, repeat_count: int) -> str:
    if repeat_count >= 2:
        reminder = (
            f"\n\nYou already have the '{slug}' page and have asked for it "
            "more than once now. Answer now with what you have."
        )
    else:
        reminder = f"\n\nYou already have the '{slug}' page above."
    return conversation + reminder


# Gemini asked for a page that is not in this wiki. Say so plainly, and
# remind it which pages do exist so it can try again.
def _note_page_missing(conversation: str, slug: str, index: str) -> str:
    existing_slugs = extract_links(index)
    slugs_text = ", ".join(existing_slugs)
    note = f"\n\nThere is no page called '{slug}'. Pages that exist: {slugs_text}"
    return conversation + note


# Gemini asked for a page we have. Hand over its text and remember we read it.
def _note_page_found(conversation: str, page: Page) -> str:
    heading = f"\n\n--- {page.title} ({page.slug}) ---\n"
    return conversation + heading + page.body
