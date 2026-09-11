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

## Weibo Heat Minimal Collector

The minimal Weibo path has two layers:

- RSSHub `/weibo/search/hot` for hot topic seeds.
- Optional `weibo-cli search statuses/limited --type 1 --count 10` enrichment for a few topics.

Start RSSHub locally:

```powershell
docker run -d --name rsshub -p 1200:1200 diygod/rsshub
```

Fetch RSSHub-only Weibo topics:

```powershell
cd backend
python scripts/weibo_heat_minimal.py --base-url http://localhost:1200 --json
```

Install and authenticate Weibo CLI for optional enrichment:

```powershell
npm install -g @weibo-ai/weibo-cli@0.9.1
weibo-cli auth login
weibo-cli doctor
weibo-cli commands list --available --output json
```

Run with CLI enrichment:

```powershell
python scripts/weibo_heat_minimal.py --base-url http://localhost:1200 --with-cli --cli-topic-limit 3 --json
```

Docker compose flow:

```powershell
docker compose -f ../docker-compose.weibo.yml up -d rsshub
docker compose -f ../docker-compose.weibo.yml build weibo-heat
docker compose -f ../docker-compose.weibo.yml run --rm weibo-heat python scripts/weibo_heat_minimal.py --json
```

Docker device-code login for CLI enrichment:

```powershell
docker compose -f ../docker-compose.weibo.yml run --rm weibo-heat weibo-cli auth login --device
docker compose -f ../docker-compose.weibo.yml run --rm -e WEIBO_CLI_ENABLED=true weibo-heat python scripts/weibo_heat_minimal.py --with-cli --json
```

For CI or unattended Docker runs, inject `WEIBO_CLI_TOKEN` or
`WEIBO_CLI_REFRESH_TOKEN` instead of running `auth login`.

## Scope

This skeleton only initializes project structure, configuration, API routing, database session setup, and test entry points. Agent task tables, business models, tool registry, planning, and state machine are implemented in later days.
