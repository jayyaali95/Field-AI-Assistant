"""Permission-filtered full-text search over the chunks.

The permission check is in the SQL: a chunk comes back only if the user holds every group its document requires.
Nothing a user can't see ever leaves this module, except the names of missing certifications in
Retrieval.missing_certifications.
"""
import re
from dataclasses import dataclass, field

import psycopg

from . import config
from .text import normalize

ASSET_ID = re.compile(r"\b[A-Z]{3}-[A-Z]-\d{4}\b", re.I)

# BM25 (k1 = 1.2, b = 0.2) in SQL. Postgres' own ts_rank has no IDF, so "case" (stemmed from "casing")
# counted as much as "hcp200". Term frequency comes from the positions stored in the tsvector.
# A term that is also in the document title or section heading (weight A) counts double, so "HCP-200"
# prefers the HCP-200 manual over HCP-350 pages that happen to say "clearance" more often.
# b is low on purpose: chunks range from one CSV row to a whole procedure section, and the full
# procedure is often the answer. Chunks are capped at 6,000 characters, so long ones can't run away.
# The parameters were checked against the sources each eval question needs (see tests/test_retrieval_corpus.py).
SELECT_HITS = """
WITH terms AS (
    SELECT word, ln(1 + (s.total - ndoc + 0.5) / (ndoc + 0.5)) AS idf
    FROM term_stats, corpus_stats s
    WHERE word = ANY(%(terms)s)
),
scored AS (
    SELECT c.id,
           sum(t.idf * u.tf * 2.2 / (u.tf + 1.2 * (0.8 + 0.2 * length(c.tsv) / s.avg_len))
               * CASE WHEN 'A' = ANY(u.weights) THEN 2 ELSE 1 END) AS score
    FROM chunks c
    JOIN documents d ON d.id = c.document_id
    CROSS JOIN corpus_stats s
    CROSS JOIN LATERAL (
        SELECT lexeme, weights, coalesce(array_length(positions, 1), 1) AS tf FROM unnest(c.tsv)
    ) u
    JOIN terms t ON t.word = u.lexeme
    WHERE c.tsv @@ to_tsquery('simple', %(query)s)
      AND (%(groups)s::text[] IS NULL OR d.required_groups <@ %(groups)s::text[])
    GROUP BY c.id
)
SELECT c.id, d.path, d.title, d.source_system, d.note, d.required_groups, c.location, c.text, scored.score
FROM scored JOIN chunks c ON c.id = scored.id JOIN documents d ON d.id = c.document_id
ORDER BY scored.score DESC
LIMIT 300
"""


@dataclass
class Hit:
    id: int
    path: str
    title: str
    source_system: str
    note: str
    required_groups: list[str]
    location: str
    text: str
    score: float = 0.0


@dataclass
class Retrieval:
    hits: list[Hit]
    missing_certifications: list[str] = field(default_factory=list)  # e.g. ["cert-hv"]


def retrieve(conn: psycopg.Connection, question: str, groups: list[str], limit: int = 20) -> Retrieval:
    assets = asset_rows(conn, question, groups)
    # "FRS-P-0031" alone doesn't find the HCP-350 bulletin; its model and serial do.
    # People also write units by their number only ("0142 tripped again"), so search that too.
    extra = re.findall(r"(?:model|serial): ([^;]+)", " ".join(a.text for a in assets))
    extra += [a[-4:] for a in ASSET_ID.findall(question)]
    query = f"{question} {' '.join(extra)}"
    hits = search(conn, query, groups, limit=limit)
    known = {h.id for h in hits}

    return Retrieval(
        hits=[a for a in assets if a.id not in known] + hits,
        missing_certifications=missing_certifications(conn, query, groups, best=hits[0].score if hits else 0.0),
    )


def missing_certifications(conn: psycopg.Connection, query: str, groups: list[str], best: float) -> list[str]:
    """Certifications the user lacks for documents that match the question better than anything they can see.

    Only certifications are reported: they're the restrictions that change what the technician should do
    (stop, get a certified colleague). Region, family and team-lead restrictions are not announced.
    """
    missing = set()
    for hit in search(conn, query, None, limit=5, per_doc=1):
        if hit.score > best:
            missing |= {g for g in hit.required_groups if g.startswith("cert-") and g not in groups}
    return sorted(missing)


def search(conn: psycopg.Connection, text: str, groups: list[str] | None, limit: int = 20, per_doc: int = 4) -> list[Hit]:
    """Top chunks for `text`. groups=None searches everything (only used to detect restricted matches)."""
    terms = conn.execute(
        "SELECT tsvector_to_array(to_tsvector('english', %s)) AS terms", (normalize(text),)
    ).fetchone()["terms"]
    if not terms:
        return []
    query = " | ".join("'" + t.replace("'", "''") + "'" for t in terms)
    rows = conn.execute(
        SELECT_HITS, {"terms": terms, "query": query, "groups": None if groups is None else list(groups)}
    ).fetchall()

    hits, per_document = [], {}
    for row in rows:
        # Cap chunks per document so one big CSV export can't fill the whole context.
        if per_document.get(row["path"], 0) >= per_doc:
            continue
        per_document[row["path"]] = per_document.get(row["path"], 0) + 1
        hits.append(Hit(**row))
        if len(hits) == limit:
            break
    return hits


def asset_rows(conn: psycopg.Connection, question: str, groups: list[str]) -> list[Hit]:
    """Fleet register rows for asset IDs named in the question, if the user may see the register."""
    ids = sorted({a.upper() for a in ASSET_ID.findall(question)})
    if not ids:
        return []
    rows = conn.execute(
        """SELECT c.id, d.path, d.title, d.source_system, d.note, d.required_groups, c.location, c.text
           FROM chunks c JOIN documents d ON d.id = c.document_id
           WHERE d.path = %s AND c.location = ANY(%s) AND d.required_groups <@ %s::text[]""",
        (config.FLEET_REGISTER, [f"asset_id {i}" for i in ids], list(groups)),
    ).fetchall()
    return [Hit(**row) for row in rows]
