import logging
from contextlib import asynccontextmanager
from dataclasses import asdict

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from . import access, db, workorders
from .answer import answer
from .llm import LLMError

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    with db.connect() as conn:
        db.init_schema(conn)
    yield


app = FastAPI(title="Field Service Copilot", lifespan=lifespan)


class Question(BaseModel):
    user_id: str
    question: str


def get_user(user_id: str) -> access.User:
    user = access.users().get(user_id)
    if user is None:
        raise HTTPException(404, f"Unknown user {user_id}")
    return user


@app.get("/health")
def health() -> dict:
    with db.connect() as conn:
        conn.execute("SELECT 1")
    return {"status": "ok"}


@app.get("/users")
def list_users() -> list[dict]:
    return [asdict(u) | {"is_lead": u.is_lead} for u in access.users().values()]


@app.post("/ask")
def ask(body: Question) -> dict:
    user = get_user(body.user_id)
    if not body.question.strip():
        raise HTTPException(422, "Question is empty")
    with db.connect() as conn:
        try:
            result = answer(conn, user, body.question)
        except LLMError as error:
            log.exception("LLM call failed")
            raise HTTPException(502, f"The language model call failed: {error}") from error
        work_order = None
        if result.work_order:  # saved as a draft; nothing is sent until a supervisor approves it
            work_order = workorders.save_draft(conn, user, body.question, result.work_order, result.citations)
    return {
        "covered": result.covered,
        "answer": result.text,
        "citations": [asdict(c) for c in result.citations],
        "missing_certifications": result.missing_certifications,
        "work_order": work_order,
    }


class Decision(BaseModel):
    user_id: str
    approve: bool


@app.get("/work-orders")
def list_work_orders(user_id: str) -> list[dict]:
    user = get_user(user_id)
    with db.connect() as conn:
        return workorders.list_for(conn, user)


@app.post("/work-orders/{work_order_id}/decision")
def decide(work_order_id: int, body: Decision) -> dict:
    user = get_user(body.user_id)
    with db.connect() as conn:
        try:
            return workorders.decide(conn, work_order_id, user, body.approve)
        except workorders.NotAllowed as error:
            raise HTTPException(403, str(error)) from error
        except workorders.NotPending as error:
            raise HTTPException(409, str(error)) from error
