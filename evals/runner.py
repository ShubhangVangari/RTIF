"""evals/runner.py — runs every eval question through both systems, grades
each answer blind, and saves the results as it goes.

The wiki system is wired up exactly the way scripts/ask.py does it, and the
baseline exactly the way scripts/ask_baseline.py does it, so both are asked
in precisely the way they are actually used. Grading is wired to Claude
Sonnet 4.5 through evals/grader.py.

Run it like this:

    python evals/runner.py
    python evals/runner.py --only synthesis
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

import anthropic
import yaml
from dotenv import load_dotenv
from google import genai
from google.genai import types

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from baseline.embed import EMBEDDING_MODEL, load_index
from baseline.retrieve import answer_question as ask_baseline
from core.query import answer_question as ask_wiki
from evals.grader import Grade, grade_answer
from store.local import LocalStore

QUESTIONS_PATH = ROOT / "evals" / "questions.yaml"
RESULTS_PATH = ROOT / "evals" / "results.json"
BASELINE_INDEX_PATH = ROOT / "baseline" / "index.pkl"

GENERATION_MODEL = "gemini-2.5-pro"
GRADER_MODEL = "claude-sonnet-4-5"
ATTEMPTS = 3  # one call plus two retries

# Vertex list price for gemini-2.5-pro, USD per 1M tokens. Both systems
# generate with the same model, so both use the same prices.
GENERATION_INPUT_PER_MILLION = 1.25
GENERATION_OUTPUT_PER_MILLION = 10.00


# Reads the --only flag off the command line, so one question type can be
# run on its own while iterating.
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the eval questions.")
    parser.add_argument("--only", default=None, help="run only this question type")
    return parser.parse_args()


# ---------------------------------------------------------------
# Reading the questions
# ---------------------------------------------------------------


# Loads every question from questions.yaml, keeping only the ones matching
# --only when that flag was given. This is the one place the eval set
# enters the program.
def load_questions(only_type: str | None) -> list[dict]:
    text = QUESTIONS_PATH.read_text(encoding="utf-8")
    all_questions = yaml.safe_load(text)

    if only_type is None:
        return all_questions

    matching = []
    for question in all_questions:
        if question["type"] == only_type:
            matching.append(question)
    return matching


# ---------------------------------------------------------------
# Cost, shared by both systems since both generate with Gemini
# ---------------------------------------------------------------


# Works out what one answer cost, in dollars, at Vertex list price.
def estimate_gemini_cost(tokens_in: int, tokens_out: int) -> float:
    return (
        tokens_in / 1_000_000 * GENERATION_INPUT_PER_MILLION
        + tokens_out / 1_000_000 * GENERATION_OUTPUT_PER_MILLION
    )


# ---------------------------------------------------------------
# Wiring the wiki system — same pattern as scripts/ask.py
# ---------------------------------------------------------------


# Builds the function that talks to Gemini on behalf of the wiki system.
# Retries twice before giving up, so one flaky network call does not sink
# the whole question.
def make_wiki_call_model(client: genai.Client):
    def call_model(prompt: str, schema: dict) -> dict:
        last_error = None
        for attempt in range(ATTEMPTS):
            try:
                return _call_gemini_for_wiki(client, prompt, schema)
            except Exception as error:
                last_error = error
                if attempt < ATTEMPTS - 1:
                    time.sleep(2 * (attempt + 1))
        raise last_error

    return call_model


# One real network call to Gemini for the wiki system. The reply is
# constrained to the read_page/answer JSON shape core/query.py expects.
def _call_gemini_for_wiki(client: genai.Client, prompt: str, schema: dict) -> dict:
    response = client.models.generate_content(
        model=GENERATION_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0,
            response_mime_type="application/json",
            response_schema=schema,
        ),
    )
    reply = json.loads(response.text)

    usage = response.usage_metadata
    reply["_usage"] = {
        "tokens_in": getattr(usage, "prompt_token_count", 0) or 0,
        "tokens_out": (getattr(usage, "candidates_token_count", 0) or 0)
        + (getattr(usage, "thoughts_token_count", 0) or 0),
    }
    return reply


# ---------------------------------------------------------------
# Wiring the baseline system — same pattern as scripts/ask_baseline.py
# ---------------------------------------------------------------


# Builds the function that talks to Gemini on behalf of the baseline. The
# baseline does not use a JSON schema — it just wants plain generated text
# back, plus token counts.
def make_baseline_call_model(client: genai.Client):
    def call_model(prompt: str) -> dict:
        last_error = None
        for attempt in range(ATTEMPTS):
            try:
                return _call_gemini_for_baseline(client, prompt)
            except Exception as error:
                last_error = error
                if attempt < ATTEMPTS - 1:
                    time.sleep(2 * (attempt + 1))
        raise last_error

    return call_model


# One real network call to Gemini for the baseline. No schema here — the
# reply is plain text.
def _call_gemini_for_baseline(client: genai.Client, prompt: str) -> dict:
    response = client.models.generate_content(model=GENERATION_MODEL, contents=prompt)
    usage = response.usage_metadata
    return {
        "text": response.text,
        "_usage": {
            "tokens_in": getattr(usage, "prompt_token_count", 0) or 0,
            "tokens_out": getattr(usage, "candidates_token_count", 0) or 0,
        },
    }


# Builds the function that turns one piece of text into one embedding
# vector, for embedding each question before searching the baseline index.
def make_embed_fn(client: genai.Client):
    def embed_fn(text: str) -> list:
        response = client.models.embed_content(model=EMBEDDING_MODEL, contents=[text])
        return response.embeddings[0].values

    return embed_fn


# ---------------------------------------------------------------
# Wiring the grader — Claude Sonnet 4.5, a different model from a
# different lab than the one that wrote the answers being graded.
# ---------------------------------------------------------------


GRADER_TOOL_NAME = "submit_grade"


# Turns a JSON schema into the tool definition Claude needs to be forced
# into replying with that exact shape, instead of free-form prose.
def _schema_to_tool(schema: dict) -> dict:
    return {
        "name": GRADER_TOOL_NAME,
        "description": "Submit the grade for this answer.",
        "input_schema": schema,
    }


# Builds the function that talks to Claude Sonnet 4.5 on behalf of the
# grader. Retries twice before giving up, the same as the two model calls
# above.
def make_call_grader(anthropic_client: anthropic.Anthropic):
    def call_grader(prompt: str, schema: dict) -> dict:
        last_error = None
        for attempt in range(ATTEMPTS):
            try:
                return _call_claude_once(anthropic_client, prompt, schema)
            except Exception as error:
                last_error = error
                if attempt < ATTEMPTS - 1:
                    time.sleep(2 * (attempt + 1))
        raise last_error

    return call_grader


# One real network call to Claude. Forcing the reply through a tool call
# is what makes the score always come back as one of the three valid
# numbers, never as prose.
def _call_claude_once(anthropic_client: anthropic.Anthropic, prompt: str, schema: dict) -> dict:
    tool = _schema_to_tool(schema)
    response = anthropic_client.messages.create(
        model=GRADER_MODEL,
        max_tokens=300,
        tools=[tool],
        tool_choice={"type": "tool", "name": GRADER_TOOL_NAME},
        messages=[{"role": "user", "content": prompt}],
    )

    for block in response.content:
        if block.type == "tool_use":
            return block.input

    raise RuntimeError("Claude did not return a tool_use block")


# ---------------------------------------------------------------
# Running one question through one system
# ---------------------------------------------------------------


# Grades one answer, but never lets a grading failure crash the run. If
# call_grader itself errors, that counts against the score being graded,
# not against the whole eval.
def safe_grade(question: dict, answer_text: str, call_grader) -> Grade:
    try:
        return grade_answer(
            question=question["question"],
            must_mention=question["must_mention"],
            question_type=question["type"],
            answer_text=answer_text,
            call_grader=call_grader,
        )
    except Exception as error:
        return Grade(score=0.0, reason=f"grading error: {error}")


# Asks the wiki system one question and grades the answer. If the wiki
# system itself fails, that is recorded as a score of 0 with the error as
# the reason, and the run continues rather than crashing.
def run_wiki_system(question: dict, index: str, store, call_model, call_grader):
    try:
        answer = ask_wiki(
            index=index,
            question=question["question"],
            load_page=store.read_page,
            call_model=call_model,
        )
    except Exception as error:
        info = {"text": "", "tokens_in": 0, "tokens_out": 0, "latency_seconds": 0.0, "cost": 0.0}
        return info, Grade(score=0.0, reason=f"wiki system error: {error}")

    info = {
        "text": answer.text,
        "tokens_in": answer.tokens_in,
        "tokens_out": answer.tokens_out,
        "latency_seconds": answer.latency_seconds,
        "cost": estimate_gemini_cost(answer.tokens_in, answer.tokens_out),
    }
    grade = safe_grade(question, answer.text, call_grader)
    return info, grade


# Asks the baseline one question and grades the answer. Same failure
# handling as run_wiki_system: an error here becomes a score of 0 with the
# error as the reason, not a crash.
def run_baseline_system(
    question: dict, chunks, chunk_numbers, embed_fn, call_model, call_grader
):
    try:
        answer = ask_baseline(question["question"], chunks, chunk_numbers, embed_fn, call_model)
    except Exception as error:
        info = {"text": "", "tokens_in": 0, "tokens_out": 0, "latency_seconds": 0.0, "cost": 0.0}
        return info, Grade(score=0.0, reason=f"baseline system error: {error}")

    info = {
        "text": answer.text,
        "tokens_in": answer.tokens_in,
        "tokens_out": answer.tokens_out,
        "latency_seconds": answer.latency_seconds,
        "cost": estimate_gemini_cost(answer.tokens_in, answer.tokens_out),
    }
    grade = safe_grade(question, answer.text, call_grader)
    return info, grade


# Runs one question through both systems and packs everything worth
# keeping into one plain dictionary — the shape evals/report.py expects to
# find in results.json.
def run_one_question(question, index, store, wiki_call_model, baseline_state, call_grader):
    chunks, chunk_numbers, embed_fn, baseline_call_model = baseline_state

    wiki_info, wiki_grade = run_wiki_system(question, index, store, wiki_call_model, call_grader)
    baseline_info, baseline_grade = run_baseline_system(
        question, chunks, chunk_numbers, embed_fn, baseline_call_model, call_grader
    )

    return {
        "id": question["id"],
        "type": question["type"],
        "question": question["question"],
        "wiki_score": wiki_grade.score,
        "wiki_reason": wiki_grade.reason,
        "wiki_answer": wiki_info["text"],
        "wiki_tokens_in": wiki_info["tokens_in"],
        "wiki_tokens_out": wiki_info["tokens_out"],
        "wiki_latency_seconds": wiki_info["latency_seconds"],
        "wiki_cost": wiki_info["cost"],
        "baseline_score": baseline_grade.score,
        "baseline_reason": baseline_grade.reason,
        "baseline_answer": baseline_info["text"],
        "baseline_tokens_in": baseline_info["tokens_in"],
        "baseline_tokens_out": baseline_info["tokens_out"],
        "baseline_latency_seconds": baseline_info["latency_seconds"],
        "baseline_cost": baseline_info["cost"],
    }


# ---------------------------------------------------------------
# Saving and printing progress
# ---------------------------------------------------------------


# Writes every result gathered so far to results.json. Called after every
# single question, not just at the end, so a crash partway through a long
# run never loses the questions already finished.
def save_results(results: list[dict]) -> None:
    RESULTS_PATH.write_text(json.dumps(results, indent=2), encoding="utf-8")


# Prints one line of progress: which question just finished, its type,
# and both scores, so a long run stays visible while it works.
def print_progress(result: dict) -> None:
    print(
        f"{result['id']}  {result['type']:<16}"
        f"wiki={result['wiki_score']}  baseline={result['baseline_score']}"
    )


def main() -> None:
    load_dotenv(ROOT / ".env")
    args = parse_args()

    questions = load_questions(args.only)
    print(f"running {len(questions)} question(s)")

    store = LocalStore()
    index = store.read_index()
    chunks, chunk_numbers = load_index(BASELINE_INDEX_PATH)

    gemini_client = genai.Client(
        vertexai=True,
        project=os.environ["GOOGLE_CLOUD_PROJECT"],
        location="us-central1",
    )
    wiki_call_model = make_wiki_call_model(gemini_client)
    baseline_call_model = make_baseline_call_model(gemini_client)
    embed_fn = make_embed_fn(gemini_client)
    baseline_state = (chunks, chunk_numbers, embed_fn, baseline_call_model)

    anthropic_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    call_grader = make_call_grader(anthropic_client)

    results: list[dict] = []
    for question in questions:
        result = run_one_question(question, index, store, wiki_call_model, baseline_state, call_grader)
        results.append(result)
        save_results(results)
        print_progress(result)


if __name__ == "__main__":
    main()
