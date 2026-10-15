# Plan

Field service copilot for Corvane technicians. Ask a question as a given technician, get an answer built only from
documents that technician may see, with a source on every claim, and a draft work order when follow-up is needed.
A supervisor approves the work order before it goes anywhere.

## Shape

```
ui (Streamlit)  ->  api (FastAPI)  ->  db (Postgres: documents, chunks, full-text index, work orders)
                         |
                         +-> Claude API (answer + work order draft, eval grading)
```

Three containers in docker compose. Ingestion is a CLI in the api package and runs once on api start if the index is empty.

## How a question is answered

1. Look up the user and their groups (users.json).
2. If the question names an asset (e.g. `FRS-P-0031`), pull its fleet register row and add its model and serial to the query.
3. Full-text search in Postgres, filtered in SQL to documents whose required groups are all in the user's groups.
   Max 4 chunks per document so one big CSV can't push everything else out.
4. Run the same search without the filter, only to find out whether restricted documents also match. The model is told
   which groups are missing, never what those documents say.
5. Claude gets the permitted chunks, numbered `[S1]..[Sn]`, and returns JSON: covered or not, answer with `[S#]` after
   each claim, optional work order draft.
6. Citations are checked against what was actually sent. A "covered" answer with no valid citation is turned into
   "not covered".
7. A work order draft is saved with status `draft`. Only a team lead (`role-lead`) can approve or reject it.

## PR sequence

1. **Skeleton**: repo layout, docker compose (db, api, ui), config, schema, README stub, plan.md, decisions.md.
2. **Parsers and ingestion**: PDF (with OCR for scanned pages), DOCX, Confluence HTML, EML, Teams JSON, Salesforce
   JSON, CSV. Permissions applied at ingest. Skip and report: empty files, files not in permissions.json, exact duplicates.
   Tests for each parser and the skip rules.
3. **Access control and search**: group check, Postgres full-text search with code normalization, per-document cap,
   asset lookup, restricted-match check. Tests that a user never gets chunks from documents they can't see.
4. **Answering**: prompt, structured output, citation validation, no-coverage path. Tests with a fake LLM.
5. **Work orders**: draft storage, approve/reject API, lead-only approval. Tests.
6. **UI**: user picker, ask screen with sources, work order list with approve/reject.
7. **Eval**: run the 10 questions, grade with Claude against the reference answers, print a score and save results.
8. **Docs**: README run steps (docker and virtualenv), where AI helped, scaling notes.
