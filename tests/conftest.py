import os

import psycopg
import pytest

# Tests use their own database so they never touch the real index or work orders.
ADMIN_URL = os.environ.get("TEST_ADMIN_DATABASE_URL", "postgresql://copilot:copilot@localhost:5432/copilot")
TEST_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://copilot:copilot@localhost:5432/copilot_test")
os.environ["DATABASE_URL"] = TEST_URL

from copilot import db  # noqa: E402  (must import after DATABASE_URL is set)


@pytest.fixture(scope="session")
def database():
    try:
        with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
            name = TEST_URL.rsplit("/", 1)[-1]
            if not admin.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone():
                admin.execute(f'CREATE DATABASE "{name}"')
    except psycopg.OperationalError as error:
        pytest.fail(f"Postgres not reachable ({error}). Start it with: docker compose up -d db")
    with db.connect() as conn:
        db.init_schema(conn)
    return TEST_URL


@pytest.fixture
def conn(database):
    with db.connect() as conn:
        conn.execute("TRUNCATE documents, chunks, work_orders RESTART IDENTITY CASCADE")
        conn.commit()
        yield conn
