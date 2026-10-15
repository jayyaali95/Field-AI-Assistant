import json

from copilot import eval as copilot_eval, llm
from copilot.answer import ModelAnswer
from copilot.eval import Grade
from copilot.ingest import ingest


def test_eval_runs_questions_as_their_users_and_counts_only_correct(conn, corpus, monkeypatch, tmp_path):
    ingest(conn, corpus({"corpus/playbook.html": ("<p>E-31 is HV work. Raise a work order with HV.</p>", ["family-compressors"])}))
    judge_prompts = []

    def fake(system, prompt, schema, model=None):
        if schema is Grade:
            judge_prompts.append(prompt)
            verdict = "correct" if "Daniel" in prompt else "partially_correct"
            return Grade(verdict=verdict, missing=[], wrong=[], reasoning="ok")
        return ModelAnswer(covered=True, answer="HV work [S1].", work_order=None)

    monkeypatch.setattr(llm, "structured", fake)
    monkeypatch.chdir(tmp_path)
    summary = copilot_eval.run(["q05", "q06"])  # same question, Daniel (HV) and Priya (no HV)

    assert (summary["correct"], summary["partially_correct"], summary["total"]) == (1, 1, 2)
    assert "Reference answer: E-31 is a VFD overcurrent trip" in judge_prompts[0]
    saved = json.loads(next((tmp_path / "eval_results").glob("eval-*.json")).read_text())
    assert [r["user"] for r in saved["results"]] == ["u02", "u03"]
