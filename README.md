# public-opinion-analysis-refactor

Refactor one of my graduate school projects.

## Current Scope

This repository is following `实施计划-v0.3.md`.

Days 8-10 have initialized the backend skeleton and persistence models:

- FastAPI application entry point
- SQLAlchemy SQLite session setup
- Pydantic settings
- Basic API routing
- `/health` health check
- Test entry point
- Agent runtime tables
- Business data tables

## Backend

```bash
cd backend
python -m venv .venv
. .venv/Scripts/activate
pip install -e ".[dev]"
uvicorn app.main:app --reload
```

Health check:

```text
GET /health
```

Run tests:

```bash
cd backend
pytest
```
