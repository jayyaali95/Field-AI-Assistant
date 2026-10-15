import pytest
from fastapi.testclient import TestClient

from copilot import llm
from copilot.answer import ModelAnswer
from copilot.api import app
from copilot.ingest import ingest


@pytest.fixture
def client(conn, corpus):
    ingest(conn, corpus({"corpus/playbook.html": ("<p>E-31 is HV work. Raise a work order.</p>", ["family-compressors"])}))
    with TestClient(app) as client:
        yield client


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_users_come_from_users_json(client):
    users = {u["id"]: u for u in client.get("/users").json()}
    assert users["u08"]["is_lead"] and not users["u03"]["is_lead"]


def test_ask_returns_answer_and_citations(client, monkeypatch):
    monkeypatch.setattr(llm, "structured", lambda s, p, schema: ModelAnswer(covered=True, answer="HV work [S1].", work_order=None))
    body = client.post("/ask", json={"user_id": "u03", "question": "How do I reset E-31?"}).json()
    assert body["covered"] and body["answer"] == "HV work [S1]."
    assert body["citations"][0]["path"] == "corpus/playbook.html"


def test_ask_unknown_user(client):
    assert client.post("/ask", json={"user_id": "nobody", "question": "E-31?"}).status_code == 404


def test_llm_failure_is_a_502(client, monkeypatch):
    def fail(*args):
        raise llm.LLMError("AuthenticationError: invalid x-api-key")

    monkeypatch.setattr(llm, "structured", fail)
    response = client.post("/ask", json={"user_id": "u03", "question": "How do I reset E-31?"})
    assert response.status_code == 502 and "invalid x-api-key" in response.json()["detail"]
