# FinAlly — AI Trading Workstation

A visually stunning AI-powered trading workstation that streams live market data, simulates portfolio trading, and integrates an LLM chat assistant that can analyze positions and execute trades via natural language.

Built entirely by coding agents as a capstone project for an agentic AI coding course.

## Features

- **Live price streaming** via SSE with green/red flash animations
- **Simulated portfolio** — $10k virtual cash, market orders, instant fills
- **Portfolio visualizations** — heatmap (treemap), P&L chart, positions table
- **AI chat assistant** — analyzes holdings, suggests and auto-executes trades
- **Watchlist management** — track tickers manually or via AI
- **Dark terminal aesthetic** — Bloomberg-inspired, data-dense layout

## Architecture

Single Docker container serving everything on port 8000:

- **Frontend**: Next.js (static export) with TypeScript and Tailwind CSS
- **Backend**: FastAPI (Python/uv) with SSE streaming
- **Database**: SQLite with lazy initialization
- **AI**: LiteLLM → OpenRouter (Cerebras inference) with structured outputs
- **Market data**: Built-in GBM simulator (default) or Massive API (optional)

## Quick Start

```bash
cp .env.example .env          # add your OPENROUTER_API_KEY
./scripts/start_mac.sh        # Windows: .\scripts\start_windows.ps1
# → http://localhost:8000
./scripts/stop_mac.sh         # data persists in the finally-data volume
```

Or directly with Docker:

```bash
docker build -t finally .
docker run -v finally-data:/app/db -p 8000:8000 --env-file .env finally
```

## Local Development

**Backend** (FastAPI + uv) — serves the API on :8000, plus the frontend if `backend/static/` exists:

```bash
cd backend
uv sync --extra dev
uv run uvicorn app.main:app --reload --port 8000
uv run pytest                 # unit + API tests
```

**Frontend** (Next.js) — dev server on :3000, proxies `/api/*` to the backend on :8000:

```bash
cd frontend
npm install
npm run dev
npm test                      # Vitest + React Testing Library
```

To serve the production UI from the backend without Docker:

```bash
(cd frontend && npm run build) && rm -rf backend/static && cp -r frontend/out backend/static
cd backend && uv run uvicorn app.main:app --port 8000
```

## E2E Tests

Playwright tests in `test/` run against the app with `LLM_MOCK=true`:

```bash
docker compose -f test/docker-compose.test.yml up --build --abort-on-container-exit --exit-code-from playwright
```

Or against a locally running backend (`LLM_MOCK=true`, fresh `DB_PATH`):

```bash
cd test && npm install && npx playwright install chromium
BASE_URL=http://localhost:8000 npx playwright test
```

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `OPENROUTER_API_KEY` | Yes | OpenRouter API key for AI chat |
| `MASSIVE_API_KEY` | No | Massive (Polygon.io) key for real market data; omit to use simulator |
| `LLM_MOCK` | No | Set `true` for deterministic mock LLM responses (testing) |

## Project Structure

```
finally/
├── frontend/    # Next.js static export
├── backend/     # FastAPI uv project
├── planning/    # Project documentation and agent contracts
├── test/        # Playwright E2E tests
├── db/          # SQLite volume mount (runtime)
└── scripts/     # Start/stop helpers
```

## License

See [LICENSE](LICENSE).
| `DB_PATH` | No | SQLite file location (default `db/finally.db`; `/app/db/finally.db` in Docker) |
