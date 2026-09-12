"""baseline/retrieve.py — finds the closest chunks by arithmetic, then asks
a language model to answer using them.

No cloud imports here. embed_fn turns a question into numbers, and
call_model talks to the language model — both are handed in as arguments by
the caller, the same pattern core/compile.py and core/query.py use. That is
what lets this whole file be tested with fake functions and no network.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from baseline.chunk import Chunk

PROMPT_TEMPLATE = (
    "Here are some excerpts from a document collection. Answer the question "
    "using only these excerpts, and cite the DOC id after every claim, like "
    "this: [DOC-041]. If the excerpts do not answer the question, say so "
    "plainly instead of guessing.\n\n"
    "{excerpts}\n\n"
    "Question: {question}"
)


# What answer_question hands back: the answer itself, plus which chunks and
# documents it came from. This mirrors Answer in core/query.py field for
# field, so the two systems' answers can be compared side by side.
@dataclass
class BaselineAnswer:
    text: str
    chunks_used: list[str]
    docs_used: list[str]
    tokens_in: int
    tokens_out: int
    latency_seconds: float


# Measures how similar two lists of numbers are, from -1 (opposite) to 1
# (identical direction). This is the one piece of arithmetic the whole
# baseline is built on: two chunks of text that mean similar things end up
# with numbers pointing in a similar direction, and cosine similarity
# measures how close that direction is. It works by multiplying each pair
# of matching numbers together and adding those up, then dividing by the
# length of each list of numbers, so a longer list of numbers does not
# automatically score higher just for being longer.
def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot_product = 0.0
    for i in range(len(a)):
        dot_product += a[i] * b[i]

    length_a = 0.0
    for value in a:
        length_a += value * value
    length_a = length_a**0.5

    length_b = 0.0
    for value in b:
        length_b += value * value
    length_b = length_b**0.5

    if length_a == 0 or length_b == 0:
        return 0.0

    return dot_product / (length_a * length_b)


# A small helper so find_closest_chunks() can sort by score without a
# lambda: it just pulls the score back out of a (score, chunk) pair.
def _score_of(scored_chunk):
    score, chunk = scored_chunk
    return score


# ---------------------------------------------------------------
# No model runs here — this is arithmetic. The chunks are chosen
# before the language model is ever involved. That is the whole
# contrast with the wiki system: here, retrieval is a similarity
# score computed by hand; there, retrieval is a model deciding what
# to read next.
# ---------------------------------------------------------------


# Finds the chunks whose numbers are closest to the question's numbers.
# This is the baseline's entire "retrieval" step — deciding which pieces of
# text to hand the language model, before the model ever sees the
# question. It works by scoring every chunk against the question with
# cosine_similarity, sorting those scores from highest to lowest, and
# keeping the top few.
def find_closest_chunks(
    question_numbers: list[float],
    chunks: list[Chunk],
    chunk_numbers: list[list[float]],
    top_k: int = 5,
) -> list[Chunk]:
    scored_chunks = []

    for i in range(len(chunks)):
        # Compare the question's numbers against this one chunk's numbers.
        score = cosine_similarity(question_numbers, chunk_numbers[i])
        scored_chunks.append((score, chunks[i]))

    scored_chunks.sort(key=_score_of, reverse=True)

    top_chunks = []
    for score, chunk in scored_chunks[:top_k]:
        top_chunks.append(chunk)

    return top_chunks


# Builds one prompt string out of the chosen chunks, labelling each excerpt
# with the document it came from so the language model can cite it.
def _format_excerpts(chunks: list[Chunk]) -> str:
    pieces = []
    for chunk in chunks:
        piece = f"[{chunk.doc_id}]\n{chunk.text}"
        pieces.append(piece)
    return "\n\n".join(pieces)


# Answers a question with the baseline pipeline, start to finish: embed the
# question, find the closest chunks by arithmetic, then ask a language
# model to answer using only those chunks. This is the one function
# scripts/ask_baseline.py calls to get a full answer. It works by chaining
# those three steps together and timing how long the whole thing takes.
def answer_question(
    question: str,
    chunks: list[Chunk],
    chunk_numbers: list[list[float]],
    embed_fn,
    call_model,
) -> BaselineAnswer:
    start_time = time.perf_counter()

    question_numbers = embed_fn(question)
    top_chunks = find_closest_chunks(question_numbers, chunks, chunk_numbers)

    excerpts = _format_excerpts(top_chunks)
    prompt = PROMPT_TEMPLATE.format(excerpts=excerpts, question=question)
    reply = call_model(prompt)

    chunk_ids = []
    for chunk in top_chunks:
        chunk_ids.append(chunk.chunk_id)

    docs_used = []
    for chunk in top_chunks:
        if chunk.doc_id not in docs_used:
            docs_used.append(chunk.doc_id)

    usage = reply.get("_usage", {})
    latency_seconds = time.perf_counter() - start_time

    return BaselineAnswer(
        text=reply.get("text", ""),
        chunks_used=chunk_ids,
        docs_used=docs_used,
        tokens_in=usage.get("tokens_in", 0),
        tokens_out=usage.get("tokens_out", 0),
        latency_seconds=latency_seconds,
    )
