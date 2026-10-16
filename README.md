# Field Service Copilot

A copilot for Corvane field technicians. It answers questions from Corvane's documents, only from the ones the
technician is allowed to see, cites a source for every claim, and drafts a work order for a supervisor to approve.

See [plan.md](plan.md) for the build plan and [decisions.md](decisions.md) for the choices and tradeoffs.

```
ui (Streamlit, :8501)  ->  api (FastAPI, :8000)  ->  db (Postgres)
                                  |
                                  +-> LLM (any provider, through LangChain)
```

Everything runs in Docker. Nothing needs to be installed on the host except Docker with compose.

## Data

The candidate package is not in git. Put it in `./data` so it looks like this:

```
data/
  corpus/...
  eval/questions.json
  permissions.json
  users.json
```

## Run

```bash
cp .env.example .env          # set LLM_MODEL and the provider's API key
docker compose up --build
```

- UI: http://localhost:8501
- API: http://localhost:8000/docs

On first start the api indexes `data/corpus` (about a minute, most of it OCR of the scanned manual). After that it
reuses the index. To rebuild it, for example after adding documents and their entries in `permissions.json`:

```bash
docker compose run --rm api python -m copilot.ingest
```

The output lists every file that was skipped and why (empty, not in permissions.json, duplicate, unreadable pages).

## API

- `GET /users`: the technicians from `users.json`
- `POST /ask` with `{"user_id": "u03", "question": "TAC-C-0143 tripped on E-31. How do I reset it?"}`: returns
  `covered`, `answer` (with `[S#]` markers), `citations` (title, location, path, excerpt per marker),
  `missing_certifications` and the `work_order` draft it saved, or null
- `GET /work-orders?user_id=u03`: newest first. Team leads see all of them, a technician sees only their own
- `POST /work-orders/{id}/decision` with `{"user_id": "u08", "approve": true}`: approve or reject a draft. Only team
  leads (`role-lead`: u07, u08) may decide, and only once (403 / 409 otherwise)

```bash
curl -s localhost:8000/ask -H 'content-type: application/json' \
  -d '{"user_id": "u03", "question": "TAC-C-0143 tripped on E-31. How do I reset it?"}'
```

## LLM

Set `LLM_MODEL` in `.env` as `<provider>:<model>` and the provider's key, for example:

```
LLM_MODEL=anthropic:claude-opus-5-5
ANTHROPIC_API_KEY=...
```

or `LLM_MODEL=openai:gpt-5` with `OPENAI_API_KEY`. Other providers need their `langchain-<provider>` package added to
`requirements.txt`.

## Eval

```bash
docker compose run --rm api python -m copilot.eval            # all 10 questions
docker compose run --rm api python -m copilot.eval q05 q06    # some of them
```

Each question goes through the same code path as the UI, asked as the technician named in `questions.json`. An LLM
judge compares the answer with the reference answer:

- **correct**: every fact the technician needs to act correctly and safely is there (values, part numbers, which
  bulletin or revision applies, conditions, required certification, escalation, the "don't do X" parts), nothing
  contradicts the reference, and nothing the reference rules out (e.g. reset steps for someone without HV). Wording
  and extra correct detail don't matter.
- **partially correct**: right direction, nothing unsafe, but a required fact is missing.
- **incorrect**: a required action is missing or wrong, or a value contradicts the reference.

Only "correct" counts toward the score. The judge's reasons, the answers, citations and work order drafts are saved
to `eval_results/eval-<timestamp>.json`. The judge defaults to the answering model; set `JUDGE_MODEL` to grade with a
different one.

Last runs with `openai:gpt-5`: 9/10, 8/10, 8/10 correct. Scores vary by one or two questions between runs. The run history, what each
miss was and what changed is in [decisions.md](decisions.md#eval).

## Tests

```bash
docker compose up -d db
docker compose run --rm api pytest
```

Tests use a separate `copilot_test` database, so they don't touch the real index or work orders.

`tests/test_retrieval_corpus.py` indexes the real corpus and checks that each eval question retrieves the sources its
reference answer needs. It takes about two minutes (OCR). For a quick run:

```bash
docker compose run --rm api pytest --ignore tests/test_retrieval_corpus.py
```

## Where AI was used

- **Writing the code and docs.** Claude Code (Claude Opus) wrote the code, tests and docs, one PR at a time from
  [plan.md](plan.md). Each PR was reviewed and committed by hand before the next one started. Some calls were made
  in review, not by the AI: Docker only (nothing installed on the host), provider-agnostic LLM access instead of
  one hard-coded provider, and keeping the candidate data out of git.
- **Reading the corpus.** The AI read the whole data package to find the traps (the unlisted unofficial HV copy,
  the empty checklist, the duplicate manual, the corrupt bulletin page, the scanned manual, the conflicting
  sources). The file problems are handled at ingest and tested; the conflicts are handled by the precedence rules in
  the prompt.
- **Tuning.** Search settings were picked by checking retrieval for each eval question (`tests/test_retrieval_corpus.py`).
  Prompt changes came from reading the misses in eval runs. Both are listed in [decisions.md](decisions.md).
- **In the product.** An LLM writes the answer and the work order draft from the retrieved sources, and grades the
  eval. Permissions, the citation check and work order approval are plain code, not the model.
