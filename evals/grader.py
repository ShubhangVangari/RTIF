"""evals/grader.py — grades one answer against what it needed to say.

Uses Claude Sonnet 4.5 through the Anthropic API, not Gemini. Gemini wrote
both answers this file grades, and a model tends to score its own writing
more kindly than a neutral judge would — this project's own corpus has
papers about exactly that bias (see llm-as-a-judge-bias-and-reliability).
Grading with a different model family from a different lab is the cheapest
fix available.

No cloud imports here. The real Anthropic call is injected as call_grader,
the same pattern core/compile.py and core/query.py use for their model
calls — that is what lets this file be tested without a network.
"""

from __future__ import annotations

from dataclasses import dataclass

# call_grader must always reply with exactly one of these three scores,
# plus one sentence explaining why.
GRADE_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "number", "enum": [1.0, 0.5, 0.0]},
        "reason": {"type": "string"},
    },
    "required": ["score", "reason"],
}


# What grade_answer hands back: the score, and the one-sentence reason
# behind it.
@dataclass
class Grade:
    score: float
    reason: str


# Builds the extra instruction that only applies to absence questions. On
# those, a confident answer is the failure, not a bonus — so the grader
# needs to be told plainly what "correct" looks like here before it reads
# the answer, or it may reward a plausible-sounding invented answer.
def _absence_instruction(question_type: str) -> str:
    if question_type != "absence":
        return ""

    return (
        "This question asks about something the source material does not "
        "cover. The correct answer says plainly that this is not covered. "
        "A confident, detailed answer that does not say this is wrong, "
        "even if the individual facts in it sound plausible, because they "
        "were invented rather than found in a source."
    )


# Builds the grading prompt. This is where blind grading is enforced: the
# prompt never says which system produced the answer, never uses the
# words "wiki" or "baseline" anywhere, and the grader only ever sees a
# question, what the answer needed to mention, and a plain block of text.
def _build_prompt(
    question: str, must_mention: str, question_type: str, answer_text: str
) -> str:
    extra = _absence_instruction(question_type)

    return (
        "You are grading one answer to one question. You do not know, and "
        "must not guess, what produced this answer — grade only what is "
        "written below.\n\n"
        f"Question: {question}\n\n"
        f"The answer must mention: {must_mention}\n\n"
        f"{extra}\n\n"
        f"Answer to grade:\n{answer_text}\n\n"
        "Check three things, in order:\n"
        "1. Does the answer contain what it must mention? Yes, partly, "
        "or no.\n"
        "2. Is anything in the answer false or invented — not something "
        "a real source would actually say?\n"
        "3. If this is an absence question, did the answer correctly say "
        "the material is not covered, rather than confidently answering "
        "anyway?\n\n"
        "Score 1.0 if the required content is present and nothing is "
        "false or invented. Score 0.5 if the required content is only "
        "partly there. Score 0.0 if the required content is missing, the "
        "answer is wrong, or it invents something. Give one sentence "
        "explaining the score."
    )


# Grades one answer. This is the only function evals/runner.py calls. It
# builds the blind prompt above, sends it to call_grader along with the
# schema that only allows the three valid scores, and turns whatever comes
# back into a Grade.
def grade_answer(
    question: str,
    must_mention: str,
    question_type: str,
    answer_text: str,
    call_grader,
) -> Grade:
    prompt = _build_prompt(question, must_mention, question_type, answer_text)
    reply = call_grader(prompt, GRADE_SCHEMA)
    return Grade(score=reply["score"], reason=reply["reason"])
