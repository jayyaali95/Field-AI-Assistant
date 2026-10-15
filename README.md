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

## LLM

Set `LLM_MODEL` in `.env` as `<provider>:<model>` and the provider's key, for example:

```
LLM_MODEL=anthropic:claude-opus-5-5
ANTHROPIC_API_KEY=...
```

or `LLM_MODEL=openai:gpt-5` with `OPENAI_API_KEY`. Other providers need their `langchain-<provider>` package added to
`requirements.txt`.

## Tests

```bash
docker compose up -d db
docker compose run --rm api pytest
```

Tests use a separate `copilot_test` database, so they don't touch the real index or work orders.
