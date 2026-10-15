import psycopg
from psycopg.rows import dict_row

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id              SERIAL PRIMARY KEY,
    path            TEXT UNIQUE NOT NULL,     -- relative to the data dir, as in permissions.json
    title           TEXT NOT NULL,
    source_system   TEXT NOT NULL,
    required_groups TEXT[] NOT NULL,          -- user must hold all of them
    note            TEXT NOT NULL DEFAULT '', -- e.g. "last modified Mar 21, 2024"
    sha256          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chunks (
    id          SERIAL PRIMARY KEY,
    document_id INT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    location    TEXT NOT NULL,  -- page, section, case number...
    text        TEXT NOT NULL,
    tsv         TSVECTOR NOT NULL
);
CREATE INDEX IF NOT EXISTS chunks_tsv_idx ON chunks USING GIN (tsv);

CREATE TABLE IF NOT EXISTS work_orders (
    id           SERIAL PRIMARY KEY,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    requested_by TEXT NOT NULL,
    question     TEXT NOT NULL,
    draft        JSONB NOT NULL,
    sources      JSONB NOT NULL DEFAULT '[]',
    status       TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'approved', 'rejected')),
    decided_by   TEXT,
    decided_at   TIMESTAMPTZ
);
"""


def connect() -> psycopg.Connection:
    return psycopg.connect(config.DATABASE_URL, row_factory=dict_row)


def init_schema(conn: psycopg.Connection) -> None:
    conn.execute(SCHEMA)
    conn.commit()
