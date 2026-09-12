"""scripts/ask.py — asks the wiki a question and prints the answer.

Run it like this:

    python scripts/ask.py "should I use multiple agents or one?"

Add --store gcs to read from Cloud Storage instead of the local
wiki_local/wiki/ folder.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.models import extract_links
from core.query import answer_question
from store.local import LocalStore

MODEL = "gemini-2.5-pro"
ATTEMPTS = 3  # one call plus two retries

# Vertex list price for gemini-2.5-pro at <=200k context, USD per 1M tokens.
INPUT_PER_MILLION = 1.25
OUTPUT_PER_MILLION = 10.00


# Read the question and the --store flag off the command line.
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ask the wiki a question.")
    parser.add_argument("question", help="the question to ask")
    parser.add_argument("--store", choices=["local", "gcs"], default="local")
    return parser.parse_args()


# Open the store the user asked for: local disk, or the GCS bucket.
def open_store(kind: str):
    if kind == "gcs":
        from store.gcs import GCSStore

        return GCSStore()
    return LocalStore()


# Build the function that actually talks to Gemini. Retries twice before
# giving up, so one flaky network call does not sink the whole question.
def make_call_model(client: genai.Client):

    # This is the call_model that core/query.py will call, over and over,
    # once per hop.
    def call_model(prompt: str, schema: dict) -> dict:
        last_error = None
        for attempt in range(ATTEMPTS):
            try:
                return _call_gemini_once(client, prompt, schema)
            except Exception as error:
                last_error = error
                if attempt < ATTEMPTS - 1:
                    time.sleep(2 * (attempt + 1))
        raise last_error

    return call_model


# One real network call to Gemini. No retry logic lives in here — that is
# the caller's job — so this function only has to do the one thing.
def _call_gemini_once(client: genai.Client, prompt: str, schema: dict) -> dict:
    response = client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0,
            response_mime_type="application/json",
            response_schema=schema,
        ),
    )
    reply = json.loads(response.text)

    # Gemini tells us how many tokens this call used. Stuff that into the
    # reply under "_usage" so core/query.py can add it to the running total.
    usage = response.usage_metadata
    reply["_usage"] = {
        "tokens_in": getattr(usage, "prompt_token_count", 0) or 0,
        "tokens_out": (getattr(usage, "candidates_token_count", 0) or 0)
        + (getattr(usage, "thoughts_token_count", 0) or 0),
    }
    return reply


# Work out what this answer cost, in dollars, at Vertex list price.
def estimate_cost(tokens_in: int, tokens_out: int) -> float:
    return (
        tokens_in / 1_000_000 * INPUT_PER_MILLION
        + tokens_out / 1_000_000 * OUTPUT_PER_MILLION
    )


# Figure out which pages in the trail were reached by following a [[link]]
# in an earlier page, rather than picked straight off the index.
def find_linked_pages(pages_read: list[str], store) -> set[str]:
    reached_via_link = set()
    links_seen_so_far = set()

    # Walk the trail in order. A page counts as "followed a link" if some
    # earlier page in the trail already mentioned it by slug.
    for slug in pages_read:
        if slug in links_seen_so_far:
            reached_via_link.add(slug)

        page = store.read_page(slug)
        if page is not None:
            links_seen_so_far.update(extract_links(page.body))

    return reached_via_link


# Print the answer, the trail of pages behind it, and the bill.
def print_report(answer, store) -> None:
    print("ANSWER")
    print("------")
    print(answer.text)
    print()

    linked_pages = find_linked_pages(answer.pages_read, store)
    print(f"PAGES READ ({len(answer.pages_read)})")
    for position, slug in enumerate(answer.pages_read, start=1):
        note = "        <- followed a link" if slug in linked_pages else ""
        print(f"  {position}. {slug}{note}")
    print()

    # The pages list only shows what succeeded. The trace shows every hop —
    # what the model tried, not just what landed.
    print("TRACE")
    for position, step in enumerate(answer.trace, start=1):
        print(f"  {position}. {step}")
    print()

    cost = estimate_cost(answer.tokens_in, answer.tokens_out)
    print(
        f"hops: {answer.hops} | {answer.tokens_in:,} tokens in / "
        f"{answer.tokens_out:,} out | ${cost:.2f} | {answer.latency_seconds:.1f}s"
    )


def main() -> None:
    # Step 1: load .env, read the question off the command line.
    load_dotenv(ROOT / ".env")
    args = parse_args()

    # Step 2: open the store.
    store = open_store(args.store)

    # Step 3: read the current index.md out of that store.
    index = store.read_index()

    # Step 4: build the real call_model, wired to Gemini 2.5 Pro on Vertex.
    client = genai.Client(
        vertexai=True,
        project=os.environ["GOOGLE_CLOUD_PROJECT"],
        location="us-central1",
    )
    call_model = make_call_model(client)

    # Step 5: hand everything to answer_question and let it do the thinking.
    answer = answer_question(
        index=index,
        question=args.question,
        load_page=store.read_page,
        call_model=call_model,
    )

    # Step 6: print what happened.
    print_report(answer, store)


if __name__ == "__main__":
    main()
