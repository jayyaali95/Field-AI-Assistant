import pytest
from fastapi.testclient import TestClient

from copilot import access, llm, workorders
from copilot.answer import ModelAnswer, WorkOrderDraft
from copilot.api import app
from copilot.ingest import ingest

DRAFT = WorkOrderDraft(
    asset_id="TAC-C-0143", fault_code="E-31", summary="E-31 after grid dip, panel reset disabled. Requesting HV reset.",
    work_steps=["Export event log", "Reset per HV-PROC-07"], parts=[], certification_required=["HV"],
    estimated_hours=2.5, priority="High", campaign_code=None,
)


@pytest.fixture
def client(conn, corpus, monkeypatch):
    ingest(conn, corpus({"corpus/playbook.html": ("<p>E-31 is HV work. Raise a work order with HV.</p>", [])}))
    monkeypatch.setattr(llm, "structured", lambda s, p, schema: ModelAnswer(covered=True, answer="HV work [S1].", work_order=DRAFT))
    with TestClient(app) as client:
        yield client


def ask(client, user_id="u03"):
    return client.post("/ask", json={"user_id": user_id, "question": "TAC-C-0143 tripped E-31, how do I reset it?"}).json()


def decide(client, work_order_id, user_id, approve=True):
    return client.post(f"/work-orders/{work_order_id}/decision", json={"user_id": user_id, "approve": approve})


def test_answer_with_follow_up_saves_a_draft(client):
    work_order = ask(client)["work_order"]
    assert work_order["status"] == "draft" and work_order["requested_by"] == "u03"
    assert work_order["draft"]["certification_required"] == ["HV"]
    assert work_order["sources"] == [{"title": "playbook", "location": "Intro", "path": "corpus/playbook.html"}]
    assert [w["id"] for w in client.get("/work-orders").json()] == [work_order["id"]]


def test_technician_cannot_approve_even_their_own_draft(client):
    work_order = ask(client)["work_order"]
    response = decide(client, work_order["id"], "u03")
    assert response.status_code == 403
    assert client.get("/work-orders").json()[0]["status"] == "draft"


def test_team_lead_approves_once(client):
    work_order = ask(client)["work_order"]
    approved = decide(client, work_order["id"], "u08").json()
    assert approved["status"] == "approved" and approved["decided_by"] == "u08" and approved["decided_at"]
    assert decide(client, work_order["id"], "u07", approve=False).status_code == 409  # already decided


def test_team_lead_can_reject(client):
    work_order = ask(client)["work_order"]
    assert decide(client, work_order["id"], "u07", approve=False).json()["status"] == "rejected"


def test_unknown_work_order(client):
    assert decide(client, 999, "u08").status_code == 409


def test_answer_without_follow_up_saves_nothing(client, monkeypatch):
    monkeypatch.setattr(llm, "structured", lambda s, p, schema: ModelAnswer(covered=True, answer="Clean the strainer [S1].", work_order=None))
    assert ask(client)["work_order"] is None
    assert client.get("/work-orders").json() == []


def test_decide_checks_role_before_touching_the_row(conn):
    tech = access.users()["u03"]
    with pytest.raises(workorders.NotAllowed):
        workorders.decide(conn, 1, tech, approve=True)
