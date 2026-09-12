"""evals/report.py — reads results.json and prints the comparison table.

No model calls in this file. Everything it needs is already sitting in
results.json, written by evals/runner.py as each question finished.

Run it like this:

    python evals/report.py
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS_PATH = ROOT / "evals" / "results.json"

QUESTION_TYPES = [
    "simple_lookup",
    "paper_specific",
    "exact_detail",
    "synthesis",
    "disagreement",
    "absence",
]


# Reads every recorded result out of results.json. This is the only place
# in the whole file that touches disk — everything after this just works
# with a plain list of dictionaries already sitting in memory.
def load_results() -> list[dict]:
    text = RESULTS_PATH.read_text(encoding="utf-8")
    return json.loads(text)


# Picks out only the results of one question type, in the order they
# appear in results.json. Used by both tables below so each one can be
# built one question type at a time.
def results_of_type(results: list[dict], question_type: str) -> list[dict]:
    matching = []
    for result in results:
        if result["type"] == question_type:
            matching.append(result)
    return matching


# ---------------------------------------------------------------
# Section 1: the score table, one row per question type
# ---------------------------------------------------------------


# Adds up the wiki score and the baseline score across one list of
# results. This is the one piece of arithmetic the whole score table is
# built from — every row is just this, run on a different slice of
# results.
def total_scores(results: list[dict]) -> tuple[float, float]:
    wiki_total = 0.0
    baseline_total = 0.0

    for result in results:
        wiki_total += result["wiki_score"]
        baseline_total += result["baseline_score"]

    return wiki_total, baseline_total


# Prints one row of the score table: a label, then each system's total
# score written as "total/count" so a reader can see both the score and
# how many questions it came from.
def print_score_row(label: str, wiki_total: float, baseline_total: float, count: int) -> None:
    wiki_text = f"{wiki_total:.1f}/{count}"
    baseline_text = f"{baseline_total:.1f}/{count}"
    print(f"{label:<22}{wiki_text:<14}{baseline_text}")


# Prints the whole score table: one row per question type that actually
# has results, then a total row across every question. It works by
# filtering the results down to one type at a time, adding up the scores,
# and printing a row — then doing the same again across all of them for
# the total.
def print_score_table(results: list[dict]) -> None:
    print(f"{'':<22}{'WIKI':<14}{'BASELINE'}")

    for question_type in QUESTION_TYPES:
        matching = results_of_type(results, question_type)
        if not matching:
            continue

        wiki_total, baseline_total = total_scores(matching)
        print_score_row(question_type, wiki_total, baseline_total, len(matching))

    print("-" * 50)
    grand_wiki, grand_baseline = total_scores(results)
    print_score_row("TOTAL", grand_wiki, grand_baseline, len(results))


# ---------------------------------------------------------------
# Section 2: average latency, cost, and tokens per system
# ---------------------------------------------------------------


# Works out the average of one field across every result, for whichever
# system's field name is passed in. Latency, cost, and tokens in are all
# just this same average, run on three different field names, so one
# function covers all three instead of writing the same loop three times.
def average_field(results: list[dict], field_name: str) -> float:
    total = 0.0
    for result in results:
        total += result[field_name]
    return total / len(results)


# Prints one row of the averages table, lining up under the same WIKI and
# BASELINE columns as the score table above.
def print_average_row(label: str, wiki_value: str, baseline_value: str) -> None:
    print(f"{label:<22}{wiki_value:<14}{baseline_value}")


# Prints the averages table: latency, cost, and tokens in, for both
# systems across every question.
def print_averages_table(results: list[dict]) -> None:
    print()
    print(f"{'':<22}{'WIKI':<14}{'BASELINE'}")

    wiki_latency = average_field(results, "wiki_latency_seconds")
    baseline_latency = average_field(results, "baseline_latency_seconds")
    print_average_row("avg latency", f"{wiki_latency:.1f}s", f"{baseline_latency:.1f}s")

    wiki_cost = average_field(results, "wiki_cost")
    baseline_cost = average_field(results, "baseline_cost")
    print_average_row("avg cost", f"${wiki_cost:.2f}", f"${baseline_cost:.2f}")

    wiki_tokens_in = average_field(results, "wiki_tokens_in")
    baseline_tokens_in = average_field(results, "baseline_tokens_in")
    print_average_row("avg tokens in", f"{wiki_tokens_in:,.0f}", f"{baseline_tokens_in:,.0f}")


# ---------------------------------------------------------------
# Section 3: the biggest disagreements, per question type
# ---------------------------------------------------------------


# Works out how far apart the two systems' scores were on one question. A
# big gap means one system did clearly better than the other on that
# specific question — those are the interesting cases worth reading.
def score_gap(result: dict) -> float:
    return abs(result["wiki_score"] - result["baseline_score"])


# A small helper so print_disagreements() can sort by gap size without a
# lambda: it just pulls the gap back out of a (gap, result) pair.
def _gap_of(scored_result):
    gap, result = scored_result
    return gap


# Prints, for each question type, the questions where the two systems
# disagreed most — biggest score gap first. Questions where both systems
# scored the same are skipped, since there is nothing to compare there.
def print_disagreements(results: list[dict]) -> None:
    print()
    print("BIGGEST DISAGREEMENTS, BY TYPE")

    for question_type in QUESTION_TYPES:
        matching = results_of_type(results, question_type)
        if not matching:
            continue

        scored = []
        for result in matching:
            scored.append((score_gap(result), result))
        scored.sort(key=_gap_of, reverse=True)

        print()
        print(question_type)
        for gap, result in scored:
            if gap == 0:
                continue
            print(
                f"  {result['id']}  wiki={result['wiki_score']}  "
                f"baseline={result['baseline_score']}  {result['question']}"
            )


def main() -> None:
    results = load_results()
    print_score_table(results)
    print_averages_table(results)
    print_disagreements(results)


if __name__ == "__main__":
    main()
