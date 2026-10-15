from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import db


@asynccontextmanager
async def lifespan(app: FastAPI):
    with db.connect() as conn:
        db.init_schema(conn)
    yield


app = FastAPI(title="Field Service Copilot", lifespan=lifespan)


@app.get("/health")
def health() -> dict:
    with db.connect() as conn:
        conn.execute("SELECT 1")
    return {"status": "ok"}
