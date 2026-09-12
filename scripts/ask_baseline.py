"""scripts/ask_baseline.py — the vector RAG baseline. Two modes.

Build the index — reads every document the wiki ingested, chunks it, embeds
it, and saves the result:

    python scripts/ask_baseline.py --build

Ask a question against a previously built index:

    python scripts/ask_baseline.py "should I use multiple agents or one?"
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from google import genai

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from baseline.chunk import chunk_document
from baseline.embed import EMBEDDING_MODEL, build_index, load_index
from baseline.retrieve import answer_question

DOC_MAP_PATH = ROOT / "wiki_local" / "doc_map.json"
LOG_PATH = ROOT / "wiki_local" / "wiki" / "log.md"
INDEX_PATH = ROOT / "baseline" / "index.pkl"

GENERATION_MODEL = "gemini-2.5-pro"

# Vertex list price for gemini-2.5-pro, USD per 1M tokens.
GENERATION_INPUT_PER_MILLION = 1.25
GENERATION_OUTPUT_PER_MILLION = 10.00


# Reads the command line: either --build, or a question to ask.
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="The vector RAG baseline.")
    parser.add_argument("question", nargs="?", default=None)
    parser.add_argument("--build", action="store_true")
    return parser.parse_args()


# Finds every DOC-xxx that has an ingest entry in the wiki's log.md. The
# wiki only got through 153 of the 223 documents before that run was
# stopped, so the baseline has to look at the exact same set of documents —
# otherwise the two systems would not be compared on the same corpus. It
# works by reading log.md line by line and pulling the DOC-xxx out of every
# line that starts with an ingest entry.
def find_ingested_doc_ids() -> list[str]:
    log_text = LOG_PATH.read_text(encoding="utf-8")
    doc_ids = []

    for line in log_text.splitlines():
        match = re.match(r"^## \[.*\] ingest \| (DOC-\d+)", line)
        if match:
            doc_id = match.group(1)
            if doc_id not in doc_ids:
                doc_ids.append(doc_id)

    return doc_ids


# Turns the real Vertex client into the plain call_model function
# baseline/retrieve.py expects: a prompt string goes in, a small dict with
# the answer text and token counts comes out.
def make_call_model(client):
    def call_model(prompt: str) -> dict:
        response = client.models.generate_content(
            model=GENERATION_MODEL,
            contents=prompt,
        )
        usage = response.usage_metadata
        return {
            "text": response.text,
            "_usage": {
                "tokens_in": getattr(usage, "prompt_token_count", 0) or 0,
                "tokens_out": getattr(usage, "candidates_token_count", 0) or 0,
            },
        }

    return call_model


# Turns the real Vertex client into the plain embed_fn function
# baseline/retrieve.py expects: one piece of text goes in, one list of
# numbers comes out. This calls the embedding model directly rather than
# going through embed_texts, since embed_texts always prints its progress
# and cost — useful for embedding hundreds of chunks while building the
# index, just noise for embedding one question.
def make_embed_fn(client):
    def embed_fn(text: str) -> list[float]:
        response = client.models.embed_content(model=EMBEDDING_MODEL, contents=[text])
        return response.embeddings[0].values

    return embed_fn


# Works out roughly what an answer cost, in dollars, at Vertex list price.
def estimate_cost(tokens_in: int, tokens_out: int) -> float:
    return (
        tokens_in / 1_000_000 * GENERATION_INPUT_PER_MILLION
        + tokens_out / 1_000_000 * GENERATION_OUTPUT_PER_MILLION
    )


# ---------------------------------------------------------------
# Mode 1: --build
# ---------------------------------------------------------------


# Reads every ingested document off disk and cuts each one into chunks. It
# works by resolving each doc_id to its raw file through doc_map.json,
# reading that file, and handing the text to chunk_document.
def chunk_all_documents(doc_ids: list[str], doc_map: dict) -> list:
    all_chunks = []

    for doc_id in doc_ids:
        raw_path = ROOT / doc_map[doc_id]
        raw_text = raw_path.read_text(encoding="utf-8")
        doc_chunks = chunk_document(doc_id, raw_text)
        for chunk in doc_chunks:
            all_chunks.append(chunk)

    return all_chunks


# Builds the whole index from scratch: find the ingested documents, chunk
# them, embed them, save the result, and print a short report of what
# happened.
def run_build(client) -> None:
    start_time = time.perf_counter()

    doc_map = json.loads(DOC_MAP_PATH.read_text(encoding="utf-8"))
    doc_ids = find_ingested_doc_ids()
    print(f"documents ingested by the wiki: {len(doc_ids)}")

    all_chunks = chunk_all_documents(doc_ids, doc_map)
    print(f"chunks: {len(all_chunks)}")
    print("embedding...")

    build_index(all_chunks, client, INDEX_PATH)

    elapsed = time.perf_counter() - start_time
    print()
    print(f"documents: {len(doc_ids)}")
    print(f"chunks: {len(all_chunks)}")
    print(f"time: {elapsed:.1f}s")
    print(f"saved to {INDEX_PATH}")


# ---------------------------------------------------------------
# Mode 2: ask a question
# ---------------------------------------------------------------


def run_question(client, question: str) -> None:
    chunks, chunk_numbers = load_index(INDEX_PATH)
    call_model = make_call_model(client)
    embed_fn = make_embed_fn(client)

    result = answer_question(question, chunks, chunk_numbers, embed_fn, call_model)

    print("ANSWER")
    print("------")
    print(result.text)
    print()

    print(f"CHUNKS USED ({len(result.chunks_used)})")
    for chunk_id in result.chunks_used:
        print(f"  {chunk_id}")
    print()

    print("DOCUMENTS: " + ", ".join(result.docs_used))
    print()

    cost = estimate_cost(result.tokens_in, result.tokens_out)
    print(
        f"{result.tokens_in:,} tokens in / {result.tokens_out:,} out "
        f"| ${cost:.2f} | {result.latency_seconds:.1f}s"
    )


def main() -> None:
    load_dotenv(ROOT / ".env")
    args = parse_args()

    client = genai.Client(
        vertexai=True,
        project=os.environ["GOOGLE_CLOUD_PROJECT"],
        location="us-central1",
    )

    if args.build:
        run_build(client)
    else:
        run_question(client, args.question)


if __name__ == "__main__":
    main()
