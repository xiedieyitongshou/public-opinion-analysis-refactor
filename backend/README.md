# Public Opinion Analysis Backend

The backend provides a tool-driven workflow for collection, event construction,
and platform-local heat and trend analysis. The HTTP application remains at
`app/main.py`; running the API does not automatically trigger collection.

## Hotspot Analysis Workflow

Run from `backend` with the project's Python environment:

```powershell
python -m app.analysis_cli --sources zhihu_hot_list --limit 5 --interval-minutes 120
```

This performs a real collection and writes analysis data. Configure source access
locally first. `--interval-minutes` declares the actual intended collection cadence;
the command itself runs once. Without sampling configuration, duration and trend
remain unknown. Use a new `--run-id` for each observation round; reusing an ID
replays its recorded collection and updates the same observations.

The workflow keeps Planning, Tool Calling, Structured Output, Guardrails and
Human Review. Its nine steps reuse existing business tools and add two adapters:
`prepare_source_signals` and `classify_events`. Daily briefing, image rendering,
evaluation and Mode B placeholder tools remain registered for later work.

For contracts, replay, persistence and failure behavior, see
[the workflow interface guide](../docs/hotspot-analysis-workflow.md).

The [2026-09-28 live validation](../reports/live-analysis-validation-2026-09-28.md)
reached all nine steps with real source responses, and a later Weibo CLI search
succeeded. Stale official feeds and false official-support matches remain open;
the current classification results have not passed business validation.

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

The analysis CLI initializes its database and runs the three implemented stages.
API routes currently provide health and Ops capabilities. Briefing publication,
frontend display APIs, scheduled execution and Evaluation Runner remain separate
work. Offline integration tests do not establish live source availability.
