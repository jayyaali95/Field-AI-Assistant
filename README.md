# Field Service Copilot

A copilot for Corvane field technicians. It answers questions from Corvane's documents, only from the ones the
technician is allowed to see, cites a source for every claim, and drafts a work order for a supervisor to approve.

See [plan.md](plan.md) for the build plan and [decisions.md](decisions.md) for the choices and tradeoffs.

```
ui (Streamlit, :8501)  ->  api (FastAPI, :8000)  ->  db (Postgres)
                                  |
                                  +-> Claude API
```

## Run with Docker

```bash
cp .env.example .env        # then put your Anthropic key in .env
docker compose up --build
```

- UI: http://localhost:8501
- API: http://localhost:8000/docs

## Run in a virtualenv

Needs Python 3.10+ and a Postgres. The easiest Postgres is the one in compose:

```bash
docker compose up -d db

python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r ui/requirements.txt

uvicorn copilot.api:app --port 8000
streamlit run ui/app.py      # in a second terminal
```

## Tests

Tests need Postgres running (`docker compose up -d db`). They use a separate `copilot_test` database.

```bash
pytest
```
