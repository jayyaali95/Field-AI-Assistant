"""Load the corpus into Postgres.

    python -m copilot.ingest            rebuild the index
    python -m copilot.ingest --if-empty only if nothing is indexed yet (used on api start)

Work orders are never touched. Files are skipped, with a reason, when they are empty, missing from
permissions.json (fail closed), exact duplicates of another file, or unparseable.
"""
import argparse
import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import psycopg

from . import config, db
from .parsers import parse_file
from .text import normalize

log = logging.getLogger(__name__)


@dataclass
class Report:
    indexed: dict[str, int] = field(default_factory=dict)  # path -> number of chunks
    skipped: dict[str, str] = field(default_factory=dict)  # path -> reason
    warnings: dict[str, list[str]] = field(default_factory=dict)


def ingest(conn: psycopg.Connection, data_dir: Path) -> Report:
    permissions = json.loads((data_dir / "permissions.json").read_text())["documents"]
    report = Report()

    files = []
    for path in sorted((data_dir / "corpus").rglob("*")):
        if not path.is_file() or path.name.startswith("."):
            continue
        rel = path.relative_to(data_dir).as_posix()
        if rel not in permissions:
            report.skipped[rel] = "not in permissions.json, nobody may see it"
        elif path.stat().st_size == 0:
            report.skipped[rel] = "empty file"
        else:
            files.append((rel, path))
    # When two files are identical, keep the manual library copy over a stray SharePoint export.
    files.sort(key=lambda f: (permissions[f[0]]["source_system"] != "manual_library", f[0]))

    conn.execute("TRUNCATE documents RESTART IDENTITY CASCADE")
    seen: dict[tuple, str] = {}  # (sha256, groups) -> path
    for rel, path in files:
        meta = permissions[rel]
        groups = sorted(meta["required_groups"])
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        if (sha, tuple(groups)) in seen:
            report.skipped[rel] = f"duplicate of {seen[(sha, tuple(groups))]}"
            continue
        seen[(sha, tuple(groups))] = rel

        try:
            parsed = parse_file(path)
        except Exception as error:
            report.skipped[rel] = f"could not parse: {error}"
            continue
        if parsed.warnings:
            report.warnings[rel] = parsed.warnings
        if not parsed.chunks:
            report.skipped[rel] = "no text found"
            continue

        doc_id = conn.execute(
            "INSERT INTO documents (path, title, source_system, required_groups, note, sha256)"
            " VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (rel, meta["title"], meta["source_system"], groups, parsed.note, sha),
        ).fetchone()["id"]
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO chunks (document_id, location, text, tsv) VALUES (%s, %s, %s,"
                " setweight(to_tsvector('english', %s), 'A') || to_tsvector('english', %s))",
                [
                    (doc_id, c.location, c.text, normalize(f"{meta['title']} {c.location}"), normalize(c.text))
                    for c in parsed.chunks
                ],
            )
        report.indexed[rel] = len(parsed.chunks)
    conn.commit()
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--if-empty", action="store_true", help="skip if documents are already indexed")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    with db.connect() as conn:
        db.init_schema(conn)
        if args.if_empty and conn.execute("SELECT 1 FROM documents LIMIT 1").fetchone():
            log.info("Index already populated, skipping ingest.")
            return
        report = ingest(conn, config.DATA_DIR)

    log.info("Indexed %d documents, %d chunks.", len(report.indexed), sum(report.indexed.values()))
    for rel, reason in report.skipped.items():
        log.info("SKIPPED %s: %s", rel, reason)
    for rel, warnings in report.warnings.items():
        for warning in warnings:
            log.info("WARNING %s: %s", rel, warning)


if __name__ == "__main__":
    main()
