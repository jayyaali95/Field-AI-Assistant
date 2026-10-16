import pytest

from copilot import access
from copilot.answer import NOT_COVERED, ModelAnswer, WorkOrderDraft, answer
from copilot.ingest import ingest

PRIYA = access.User("u03", "Priya Natarajan", "Field Technician I", ("region-na", "family-compressors"))
DANIEL = access.User("u02", "Daniel Reyes", "Field Technician II", ("region-na", "family-compressors", "cert-hv"))

HV_STEPS = "E-31 reset: set P9.01 = 1 and hold RESET on the drive keypad for 3 s. Reset only after E-31 checks."


def page(text):
    return f"<div id='main-content'><p>{text}</p></div>"


class FakeLLM:
    """Records what would be sent to the provider and returns a canned answer."""

    def __init__(self, result: ModelAnswer):
        self.result, self.prompts = result, []

    def __call__(self, system, prompt):
        self.prompts.append(prompt)
        return self.result


@pytest.fixture
def library(conn, corpus):
    ingest(conn, corpus({
        "corpus/hv-proc-07.html": (page(HV_STEPS), ["cert-hv", "family-compressors"]),
        "corpus/playbook.html": (page("E-31 is HV work. Do not open the VFD cabinet. Raise a work order with HV."), ["family-compressors"]),
    }))
    return conn


def test_restricted_text_never_reaches_the_llm_but_the_certification_note_does(library):
    llm = FakeLLM(ModelAnswer(covered=True, answer="E-31 is HV work [S1].", work_order=None))
    result = answer(library, PRIYA, "How do I reset E-31?", ask_llm=llm)

    prompt = llm.prompts[0]
    assert "P9.01" not in prompt and "hv-proc-07" not in prompt
    assert "Do not open the VFD cabinet" in prompt
    assert "cert-hv" in prompt and "cannot see it" in prompt
    assert result.missing_certifications == ["cert-hv"]


def test_certified_user_gets_the_procedure_as_a_source(library):
    llm = FakeLLM(ModelAnswer(covered=True, answer="Hold RESET for 3 s [S1].", work_order=None))
    result = answer(library, DANIEL, "How do I reset E-31?", ask_llm=llm)
    assert "P9.01" in llm.prompts[0]
    assert result.missing_certifications == []


def test_no_permitted_sources_means_no_llm_call(library):
    llm = FakeLLM(ModelAnswer(covered=True, answer="made up", work_order=None))
    result = answer(library, PRIYA, "What's the warranty on a Belton BX-20?", ask_llm=llm)
    assert llm.prompts == []
    assert not result.covered and result.text == NOT_COVERED


def test_citations_must_point_at_sources_that_were_sent(library):
    llm = FakeLLM(ModelAnswer(covered=True, answer="It's HV work [S1]. Also something [S9].", work_order=None))
    result = answer(library, PRIYA, "How do I reset E-31?", ask_llm=llm)

    assert result.text == "It's HV work [S1]. Also something ."
    assert [c.marker for c in result.citations] == ["S1"]
    assert result.citations[0].path == "corpus/playbook.html"


def test_covered_answer_without_valid_citations_becomes_not_covered(library):
    llm = FakeLLM(ModelAnswer(covered=True, answer="Just restart it [S7].", work_order=None))
    result = answer(library, PRIYA, "How do I reset E-31?", ask_llm=llm)
    assert not result.covered
    assert result.text == NOT_COVERED


def test_both_citation_styles_are_checked(library):
    llm = FakeLLM(ModelAnswer(covered=True, answer="It's HV work [S1, S9].", work_order=None))
    result = answer(library, PRIYA, "How do I reset E-31?", ask_llm=llm)
    assert result.text == "It's HV work [S1]."
    assert [c.marker for c in result.citations] == ["S1"]


def test_draft_is_dropped_when_the_answer_cites_nothing_real(library):
    draft = WorkOrderDraft(
        asset_id=None, fault_code=None, summary="Restart", work_steps=[], parts=[],
        certification_required=["None"], estimated_hours=None, priority="Low", campaign_code=None,
    )
    llm = FakeLLM(ModelAnswer(covered=True, answer="Just restart it [S7].", work_order=draft))
    assert answer(library, PRIYA, "How do I reset E-31?", ask_llm=llm).work_order is None


def test_work_order_draft_is_passed_through(library):
    draft = WorkOrderDraft(
        asset_id="TAC-C-0143", fault_code="E-31", summary="E-31, needs HV reset", work_steps=["Reset per HV-PROC-07"],
        parts=[], certification_required=["HV"], estimated_hours=2.5, priority="High", campaign_code=None,
    )
    llm = FakeLLM(ModelAnswer(covered=True, answer="Needs HV [S1].", work_order=draft))
    assert answer(library, PRIYA, "How do I reset E-31?", ask_llm=llm).work_order == draft
