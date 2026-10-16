"""Run the eval questions through the copilot and grade them against the reference answers.

    python -m copilot.eval              all questions
    python -m copilot.eval q05 q06      some of them

Each question goes through answer(), the same path as the UI, as the user who asked it. An LLM judge grades the
answer against the expert reference. Only "correct" counts toward the score. Full results, with the judge's reasons,
are written to eval_results/ so a person can check the grading.
"""
import argparse
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from . import access, config, db, llm
from .answer import answer

JUDGE_SYSTEM = """You grade answers from a field service copilot against a reference answer written by the
company's senior engineers. A technician will act on the answer at a machine, so grade like that engineer would.

correct: the answer contains every fact from the reference that the technician needs to act correctly and safely:
values with units, part numbers, which bulletin or revision applies and why, thresholds and conditions, required
certifications, escalation rules, and anything the reference says to do first or not to do. Wording, order and
extra correct detail don't matter. If the reference asks for a work order, the draft must carry the fields the
reference names.
partially_correct: right direction and nothing unsafe, but at least one required fact is missing.
incorrect: a required action is missing or wrong, a value contradicts the reference, or the answer contains something
the reference rules out (for example steps the technician isn't certified for, or a value for a different model).

A statement about something the reference doesn't mention is not wrong unless it conflicts with the reference.
The copilot cites the company's records, so extra history can be true even if the reference leaves it out.
A work order is for whoever will do the job: naming the procedure number a certified technician must follow is
not giving the steps, as long as the answer doesn't spell those steps out.

If the reference says no source covers the question, the answer is correct only if it says so and does not offer
the wrong value as the answer."""


class Grade(BaseModel):
    verdict: Literal["correct", "partially_correct", "incorrect"]
    missing: list[str] = Field(description="Required facts from the reference that the answer lacks.")
    wrong: list[str] = Field(description="Statements that contradict the reference or that it rules out.")
    reasoning: str = Field(description="Two or three sentences explaining the verdict.")


def grade(question: dict, user: access.User, result) -> Grade:
    work_order = result.work_order.model_dump_json(indent=1) if result.work_order else "none"
    prompt = "\n".join([
        f"Question (asked by {user.name}, groups: {', '.join(user.groups)}): {question['question']}",
        "", f"Reference answer: {question['reference_answer']}",
        "", f"Copilot says covered by sources: {result.covered}",
        f"Missing certifications it was told about: {result.missing_certifications or 'none'}",
        f"Copilot answer:\n{result.text}",
        "", f"Work order draft: {work_order}",
    ])
    return llm.structured(JUDGE_SYSTEM, prompt, Grade, model=config.JUDGE_MODEL)


def run(ids: list[str]) -> dict:
    questions = json.loads((config.DATA_DIR / "eval" / "questions.json").read_text())
    questions = [q for q in questions if not ids or q["id"] in ids]
    users = access.users()
    rows = []
    with db.connect() as conn:
        for q in questions:
            user = users[q["user_id"]]
            started = time.monotonic()
            try:
                result = answer(conn, user, q["question"])
                verdict = grade(q, user, result)
                row = {
                    "verdict": verdict.verdict, "missing": verdict.missing, "wrong": verdict.wrong,
                    "reasoning": verdict.reasoning, "covered": result.covered, "answer": result.text,
                    "citations": [f"{c.marker}: {c.title} | {c.location}" for c in result.citations],
                    "missing_certifications": result.missing_certifications,
                    "work_order": result.work_order.model_dump() if result.work_order else None,
                }
            except llm.LLMError as error:
                row = {"verdict": "error", "error": str(error)}
            rows.append({"id": q["id"], "user": user.id, "question": q["question"],
                         "seconds": round(time.monotonic() - started, 1), **row})
            print(f"{q['id']}  {user.id}  {row['verdict']:<17} {rows[-1]['seconds']:>5}s  {q['question'][:70]}", flush=True)

    correct = sum(r["verdict"] == "correct" for r in rows)
    summary = {
        "model": config.LLM_MODEL, "judge": config.JUDGE_MODEL, "run_at": datetime.now().isoformat(timespec="seconds"),
        "correct": correct, "partially_correct": sum(r["verdict"] == "partially_correct" for r in rows),
        "total": len(rows), "results": rows,
    }
    out = Path("eval_results") / f"eval-{datetime.now():%Y%m%d-%H%M%S}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(summary, indent=2))

    print(f"\nScore: {correct}/{len(rows)} correct ({summary['partially_correct']} partially correct)")
    for r in rows:
        if r["verdict"] != "correct":
            print(f"\n{r['id']} {r['verdict']}: {r.get('reasoning') or r.get('error')}")
            for point in r.get("missing", []):
                print(f"  missing: {point}")
            for point in r.get("wrong", []):
                print(f"  wrong:   {point}")
    print(f"\nFull results: {out}")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("ids", nargs="*", help="question ids, e.g. q05 q06 (default: all)")
    run(parser.parse_args().ids)


if __name__ == "__main__":
    main()
