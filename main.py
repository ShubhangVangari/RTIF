"""main.py — the web server for the wiki and the baseline.

Everything else in this project only runs from the terminal. This file wraps
both systems — the wiki (core/query.py) and the vector baseline
(baseline/retrieve.py) — behind a small HTTP API, so a browser can ask them
questions and read pages instead.

Run it like this:

    python main.py

Then, from another terminal:

    curl -X POST localhost:8080/ask -H "Content-Type: application/json" \
        -d '{"question": "..."}'
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from google import genai
from google.cloud import storage

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from baseline.embed import load_index
from baseline.retrieve import answer_question as answer_with_baseline
from core.models import Page
from core.query import answer_question as answer_with_wiki

# Reusing the exact wiring scripts/ask.py and scripts/ask_baseline.py already
# use to talk to Gemini, instead of copying it a second time.
from scripts.ask import estimate_cost
from scripts.ask import make_call_model as make_wiki_call_model
from scripts.ask_baseline import make_call_model as make_baseline_call_model
from scripts.ask_baseline import make_embed_fn
from store.gcs import GCSStore

# ---------------------------------------------------------------------------
# Setup — everything below runs once, when the server starts, not per request
# ---------------------------------------------------------------------------

app = FastAPI()

# Lets a browser page served from any origin call these endpoints.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

BUCKET = os.environ["BUCKET"]
PROJECT = os.environ["GOOGLE_CLOUD_PROJECT"]

BASELINE_INDEX_PATH = ROOT / "baseline" / "index.pkl"
BASELINE_BLOB_NAME = "baseline/index.pkl"

# The wiki lives in Cloud Storage now, not on local disk — the container has
# no wiki_local/ folder.
store = GCSStore()

# One Gemini client for the whole life of the process, instead of opening a
# new one for every request.
gemini_client = genai.Client(vertexai=True, project=PROJECT, location="us-central1")

# The wiki asks Gemini for schema-constrained JSON, one hop at a time. The
# baseline asks Gemini for a single plain-text answer. Different wiring for
# each, both borrowed as-is from the scripts that already do this on the
# command line.
wiki_call_model = make_wiki_call_model(gemini_client)
baseline_call_model = make_baseline_call_model(gemini_client)
baseline_embed_fn = make_embed_fn(gemini_client)


# Makes sure the baseline's pickled index is sitting on local disk. The
# pickle is not part of the container image — it was uploaded to the bucket
# by hand — so on a fresh container this always has to download it once
# before the baseline can answer anything.
def _download_baseline_index() -> None:
    if BASELINE_INDEX_PATH.exists():
        return
    BASELINE_INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    storage_client = storage.Client()
    blob = storage_client.bucket(BUCKET).blob(BASELINE_BLOB_NAME)
    blob.download_to_filename(str(BASELINE_INDEX_PATH))


_download_baseline_index()

# Loaded once at startup and kept in memory for every question after that.
# This is read-only by design: the baseline is a fixed control group to
# compare the wiki against, not something new documents get added to, so
# there is nothing here that ever needs to change while the server runs.
baseline_chunks, baseline_chunk_numbers = load_index(BASELINE_INDEX_PATH)


# Turns a Page into a plain dictionary that can be sent back as JSON. The
# "updated" field is a datetime object, and JSON has no format for those, so
# it is turned into a plain ISO 8601 string here.
def _page_to_dict(page: Page) -> dict:
    return {
        "slug": page.slug,
        "title": page.title,
        "type": page.type,
        "summary": page.summary,
        "updated": page.updated.isoformat(),
        "flags": page.flags,
        "body": page.body,
    }


# A page is "contested" when the compiler flagged it as having a
# disagreement between sources that was never resolved. Flags are plain
# strings like "contested: some detail" — this checks the start of the
# string, since the text after the colon is different on every page.
def _is_contested(page: Page) -> bool:
    for flag in page.flags:
        if flag.startswith("contested:"):
            return True
    return False


# Builds one row of the /index endpoint's page list: the slug, the summary,
# and whether the page is contested.
def _index_entry(page: Page) -> dict:
    return {
        "slug": page.slug,
        "summary": page.summary,
        "contested": _is_contested(page),
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


# Serves the console itself. Called by a browser opening the site's root
# URL. Returns the one static HTML file the whole frontend lives in.
@app.get("/")
def home():
    return FileResponse("static/index.html")


# Tells anything checking on the server that it is up. Called by Cloud Run's
# health check, not by a browser. Returns a small fixed JSON object.
@app.get("/health")
def health():
    return {"ok": True}


# Asks the wiki a question. Called by the browser for the wiki side of the
# comparison. Reads the current page index fresh from the bucket, then lets
# core/query.py decide which pages to read before it answers. Returns the
# answer text, the trail of pages it read, and what the call cost.
@app.post("/ask")
def ask(body: dict):
    try:
        question = body["question"]
        index = store.read_index()
        answer = answer_with_wiki(
            index=index,
            question=question,
            load_page=store.read_page,
            call_model=wiki_call_model,
        )
        cost = estimate_cost(answer.tokens_in, answer.tokens_out)
        return {
            "answer": answer.text,
            "pages_read": answer.pages_read,
            "trace": answer.trace,
            "hops": answer.hops,
            "tokens_in": answer.tokens_in,
            "tokens_out": answer.tokens_out,
            "latency_seconds": answer.latency_seconds,
            "cost": cost,
        }
    except Exception as error:
        raise HTTPException(status_code=500, detail=str(error))


# Asks the vector baseline the same question. Called by the browser for the
# baseline side of the comparison. There is no index to read here and no
# hops — the baseline picks its chunks by arithmetic in one step, so there
# is no trace to show, and that absence is itself part of the comparison.
@app.post("/ask-baseline")
def ask_baseline(body: dict):
    try:
        question = body["question"]
        answer = answer_with_baseline(
            question,
            baseline_chunks,
            baseline_chunk_numbers,
            baseline_embed_fn,
            baseline_call_model,
        )
        cost = estimate_cost(answer.tokens_in, answer.tokens_out)
        return {
            "answer": answer.text,
            "chunks_used": answer.chunks_used,
            "docs_used": answer.docs_used,
            "tokens_in": answer.tokens_in,
            "tokens_out": answer.tokens_out,
            "latency_seconds": answer.latency_seconds,
            "cost": cost,
        }
    except Exception as error:
        raise HTTPException(status_code=500, detail=str(error))


# Fetches one page by slug. Called by the browser when someone clicks a
# citation in an answer — answers cite page names, and this is what turns a
# name into something a browser can actually open. Returns 404 if there is
# no page by that slug.
@app.get("/page/{page_slug}")
def get_page(page_slug: str):
    try:
        page = store.read_page(page_slug)
    except Exception as error:
        raise HTTPException(status_code=500, detail=str(error))

    if page is None:
        raise HTTPException(status_code=404, detail=f"no page called '{page_slug}'")

    return _page_to_dict(page)


# Lists every page in the wiki. Called by the browser to build the page
# list a user can browse. Each entry is built straight from the page itself
# — the slug, the summary, and whether it is contested — never by parsing
# index.md, which is a rendering of this same information, not the source
# of it.
@app.get("/index")
def get_index():
    try:
        entries = []
        for slug in store.list_pages():
            page = store.read_page(slug)
            if page is None:
                continue
            entries.append(_index_entry(page))
        return entries
    except Exception as error:
        raise HTTPException(status_code=500, detail=str(error))


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
