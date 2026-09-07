# Backend Skeleton

Day 8 backend skeleton for the public opinion analysis refactor.

## Stack

- FastAPI
- SQLAlchemy
- SQLite
- httpx
- Pydantic

## Local Run

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

## Zhihu Hot List Smoke Test

Set the official Zhihu Data Open Platform secret locally. Do not commit real
secrets.

```powershell
cd backend
$env:ZHIHU_ACCESS_SECRET="<your-access-secret>"
python scripts/zhihu_smoke_test.py --limit 10
```

## Scope

This skeleton only initializes project structure, configuration, API routing, database session setup, and test entry points. Agent task tables, business models, tool registry, planning, and state machine are implemented in later days.
