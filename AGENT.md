# AGENT.md

## What Open Alpha Arena Is

Open Alpha Arena is a crypto/US-stock paper-trading bench for LLM agents. The runtime shape is:
**Vite/React frontend -> FastAPI backend -> scheduler + market data + LLM trading agents + order simulator + DB/Redis/Docker sandbox.**

The backend is the source of truth. It owns accounts, positions, orders, trades, AI decision logs, agent traces, evaluation checkpoints, market/kline caches, WebSocket snapshots, and the periodic auto-trading loop. The frontend is a dashboard and control plane over `/api` and `/ws`; it does not execute trading logic.

Useful first reads for a new session:

- `README.md` for setup and deployment commands.
- `docs/agent_architecture.md` for the high-level agent model.
- `backend/main.py` for FastAPI startup side effects and route registration.
- `backend/services/startup.py` for scheduler/cache/container initialization.
- `backend/services/trading_commands.py` and `backend/services/agent/trade_execution_tool.py` for the actual order execution paths.
- `frontend/app/lib/api.ts` and `frontend/vite.config.ts` for frontend/backend API boundaries.

## Workspace Layout

- `backend/` - FastAPI app managed by `uv`. Entry point is `backend/main.py`.
- `backend/api/` - HTTP and WebSocket routes. Keep route behavior thin; use services/repositories for stateful logic.
- `backend/database/` - SQLAlchemy engine/session/model definitions. Default local fallback is `sqlite:///./data.db`; Docker deploy uses MySQL.
- `backend/repositories/` - DB access helpers for accounts, orders, positions, klines, users.
- `backend/services/` - business logic: agent execution, scheduler, market data, order matching/execution, evaluation, cache, security, startup.
- `backend/services/agent/` - LLM agent runtime, tools, prompts, memory, sandbox interaction, public API tools.
- `backend/config/` - runtime constants and env-driven settings.
- `backend/test/` - pytest tests. Some are local unit-style tests; integration tests may require external API keys, Redis, MySQL, Pinecone, or model endpoints.
- `frontend/` - Vite + React + Tailwind app.
- `frontend/app/components/` - dashboard UI grouped by domain: portfolio, trading, compliance, memory, layout.
- `docs/` - architecture, tool, compliance, evaluation, model, and market docs.
- `scripts/` - deploy/cache helper scripts.
- `eval_results/` and `backend/services/evaluation/eval_results/` - checked-in sample/eval artifacts; avoid adding bulky generated results unless intended.

## Commands

```bash
pnpm run install:all        # pnpm install + backend uv sync
pnpm run dev                # backend on 5611 + frontend on 5621
pnpm run dev:backend        # uvicorn main:app --reload --port 5611
pnpm run dev:frontend       # Vite on 5621
pnpm run build              # frontend Vite build; backend build is a placeholder
pnpm run build:frontend
```

Backend-only commands from `backend/`:

```bash
uv sync
uv run uvicorn main:app --reload --port 5611 --host 0.0.0.0
uv run pytest
uv run pytest test/test_scheduler_first_decision_timing.py
uv run python script/create_accounts_from_env.py --mode all-combinations
```

Docker deploy from repo root:

```bash
cp backend/.env.example backend/.env
docker compose up -d --build
docker compose logs -f frontend backend
docker compose down
```

There is no Python packaging build step in practice. `backend/pyproject.toml` exists for dependency management; app verification is by pytest and running the ASGI app.

## Required Setup

- Node 18+ and pnpm 10.
- Python 3.10+ and `uv`.
- Redis/Valkey is mandatory for backend startup. `services.startup.initialize_services()` calls `tool_cache.ensure_ready()`, and `TOOL_CACHE_ENABLED=false` is treated as an error.
- Docker is needed for agent sandbox tools (`execute_shell_command`, `read_file`, `write_file`, `run_python_script`, kline file analysis). The compose backend mounts `/var/run/docker.sock` for this.
- MySQL is recommended for production/concurrency. SQLite remains a local fallback but does not behave like MySQL under high concurrency.
- `.env` is loaded from the working tree for direct `uv` runs. Start from `backend/.env.example`.

Important env vars:

- `API_KEY`, `BASE_URL` - default OpenAI-compatible runtime LLM credentials.
- `DATABASE_URL` - defaults to SQLite if unset; compose injects MySQL.
- `TOOL_CACHE_REDIS_URL` - defaults to `redis://localhost:6379/0`.
- `AI_TRADE_INTERVAL_SECONDS` and `AI_TRADE_FIRST_EXECUTION_TIME` - control the auto-trading schedule.
- `AGENT_MAX_CONCURRENCY` - cap for concurrent agent decision collection.
- `PUBLIC_API_TOOL_LIMIT`, `LLM_REQUEST_MAX_RETRIES`, `MAX_CONTEXT_TOKENS` - agent/tool runtime limits.
- `API_KEY_CIPHER_KEY` - enables encrypted-at-rest account API keys; without it, understand fallback behavior before changing security code.
- `PINECONE_API_KEY` - needed when account memory uses the current default Pinecone backend.
- `ALPACA_KEY`, `ALPACA_SECRET` - needed for US market data paths.
- `AUDIT_*`, `EVAL_LLM_*`, `GEMINI_OPENAI_COMPAT_*` - only needed for specific compliance/eval/integration tests.

## Runtime Startup Side Effects

Starting `backend/main.py` is not a harmless import-style operation:

- `Base.metadata.create_all()` creates tables.
- SQLite gets lightweight `ALTER TABLE` migrations for known missing columns.
- MySQL startup tries to widen selected columns (`ai_decision_logs.reason`, `agent_traces.*`) and logs warnings if already applied or unavailable.
- A default user and default account are created if missing.
- Legacy plaintext account API keys are migrated through `encrypt_api_key()` when possible.
- `initialize_services()` starts Redis cache validation, Docker sandbox service, APScheduler jobs, market tasks, asset-curve backfill, automatic trading reset, price-cache cleanup, margin monitor, order scheduler, and evaluation checkpoint jobs.

When writing tests or scripts, avoid importing/running `main:app` unless you actually want those services. Prefer testing service functions directly when possible.

## Backend Architecture

The main data flow for live trading:

```
APScheduler job
  -> services.trading_commands.place_ai_driven_crypto_order()
     load active AI + baseline accounts
     fetch latest market prices
     collect decisions concurrently with isolated DB sessions
     ReAct/multi-agent path or legacy JSON path
     execute trade immediately or process returned decision
     save AIDecisionLog / AgentTrace / Order / Trade / Position updates
     WebSocket snapshots expose updated state to frontend
```

Two decision protocols coexist:

- Legacy direct JSON decision: `call_ai_for_decision()` asks a chat-completions endpoint for one JSON object, then `trading_commands._process_account_decision_payload()` validates and executes it.
- Agent/tool protocol: `call_agent_for_decision()` builds a `ReActAgent` or multi-agent variant. The agent can call tools, including `execute_trade`, which executes immediately. The returned decision carries `protocol: "tool"` or `executed_trades`; `trading_commands` must not execute it again.

Do not collapse these paths casually. Duplicate execution is the highest-risk regression in this codebase.

## Agent Runtime

Core files:

- `backend/services/agent/react.py` - ReAct loop, tool-call guardrails, active tool routing, `<TRADE_DONE>` termination handling.
- `backend/services/agent/env_wrapper.py` - registers default tools for one account/session.
- `backend/services/agent/tools.py` - tool registry contract.
- `backend/services/agent/tool_selector.py` - dynamic tool routing meta-tool.
- `backend/services/agent/trade_execution_tool.py` - model-facing immediate trade tool.
- `backend/services/agent/prompts/` - system/sub-agent/multi-agent prompt text.
- `backend/services/agent/llm_client.py` - OpenAI-compatible client wrapper and provider-specific tool-call normalization.
- `backend/services/agent/memory_*.py`, `memory_tools.py` - account-scoped memory.

Default exposed tools include market snapshot, kline history saved into the sandbox, account state, search sub-agent, sandbox shell/file/python helpers, memory tools when enabled, and `execute_trade`.

`AgentConfig.USE_AGENT = True` by default. Per-account fields such as `agent_type`, `memory_enabled`, `tool_routing_enabled`, and `enable_rule_aware` matter; preserve their string/bool compatibility because the DB stores several flags as strings (`"true"` / `"false"`).

The ReAct loop expects the model to eventually output `<TRADE_DONE>`. It will remind near `STEP_REMINDER_THRESHOLD` and stops at `MAX_STEPS`.

## Trading Semantics

Supported crypto symbols are centralized around `AI_TRADING_SYMBOLS` / `SUPPORTED_CRYPTO_SYMBOLS`: `BTC`, `ETH`, `SOL`, `BNB`, `XRP`, `DOGE`. US symbols are gated by the Alpaca/US market support list.

Use the established order paths:

- `services.order_matching.create_order()` and `check_and_execute_order()` for normal order creation/fill behavior.
- `services.order_executor_leverage.place_and_execute_crypto()` for leverage-aware crypto execution.
- `services.agent.trade_execution_tool.execute_trade_tool()` for model tool-mode trades.

Important rules already enforced in `execute_trade_tool()`:

- `operation` is `open`, `close`, `hold`, `all_in`, or `close_all`.
- `market` is `CRYPTO` or `US`.
- US market trades are rejected when market status is closed and leverage is forced to 1.
- Crypto shorts require leverage > 1.
- Only one directional crypto position per symbol is allowed; adding with different side or leverage is rejected.
- Closing defaults to the existing position side if the model supplies the wrong direction.
- Sizing supports `portion`, `usd`, `all_in`, and close ratios.

When changing trading logic, update tests around execution semantics and duplicate execution. Start with:

```bash
cd backend
uv run pytest test/trade_execution_semantics_test.py
uv run pytest test/test_tool_selector_execute_trade.py
uv run pytest test/test_trading_loop_lock_isolation.py
```

## Frontend Boundary

The frontend is Vite + React 18 + Tailwind + Radix UI + Chart.js/lightweight-charts.

Development proxy:

- Vite serves on `5621`.
- `frontend/vite.config.ts` proxies `/api` and `/ws` to backend `localhost:5611`.
- `frontend/app/lib/api.ts` uses `/api`, not a hardcoded backend port.

Docker/Nginx proxy:

- `frontend/nginx.conf` proxies `/api/` to `http://backend:5611/api/`.
- `/ws` upgrades to `http://backend:5611/ws`.

Keep frontend API types in `frontend/app/lib/api.ts` aligned with backend route responses. Many backend booleans are persisted as strings but exposed to the UI as booleans in some endpoints; check the route serializer before changing types.

WebSocket snapshots are account-scoped. `api/ws.py` registers a snapshot job per connected account and sends `snapshot_fast` most of the time; full asset curves are only included periodically to avoid expensive recalculation.

## Database and Migrations

SQLAlchemy models live in `backend/database/models.py`. There is no Alembic migration system. Existing schema changes are handled by:

- `Base.metadata.create_all()` for new tables.
- ad hoc startup migrations in `backend/main.py`.
- maintenance scripts under `backend/script/`.

For schema changes, update the SQLAlchemy model, startup migration/backfill path if needed, serializers/routes, and focused tests. Be explicit about SQLite vs MySQL behavior. MySQL text capacity matters for agent traces and decision reasons; this repo already widened trace fields to `LONGTEXT`.

`database/connection.py` auto-creates the MySQL database if the configured DB user has permission. It uses `NullPool` for SQLite and a tuned QueuePool for other DBs.

## Cache, Market Data, and External APIs

Redis tool cache is not optional. `RedisToolCache` stores per-decision-round tool results and lock keys under `TOOL_CACHE_KEY_PREFIX`. Tool cache failures during startup should fail fast; individual read/write failures inside the tool path are logged and degraded where possible.

Market data flows through `services.market_data`, `hyperliquid_market_data`, `alpaca_market_data`, `market_kline_service`, and repository cache tables. Be careful with fallback semantics: a price of `0` or `None` is usually a skip/reject condition, not a valid trade price.

Search/public API tools can call external services and use API keys from `.env`. Tests that hit real providers should be marked or skipped when credentials are absent.

## Development Conventions

- Keep stateful trading changes small and covered by tests. A small rounding or side-mapping change can affect cash, margin, liquidation, orders, and evaluation.
- Do not bypass repositories/services to mutate account, position, order, or trade state unless the existing local module already owns that invariant.
- Do not duplicate order execution after a tool-mode agent decision. Check for `protocol == "tool"` or `executed_trades`.
- Keep prompt/tool schema changes in `backend/services/agent/prompts/`, `env_wrapper.py`, or tool schema modules, and update LLM/tool round-trip tests.
- Preserve account-level feature flags: `agent_type`, `memory_enabled`, `tool_routing_enabled`, `enable_rule_aware`, `is_active`.
- Treat `current_cash`, `frozen_cash`, quantities, and avg costs as financial state. Use `Decimal` where surrounding code uses it; avoid float-only rewrites in execution code.
- Close DB sessions created in worker threads. The concurrent decision collector intentionally opens one `SessionLocal()` per account.
- Avoid long blocking work in WebSocket send paths and scheduler jobs unless the existing scheduler constraints (`max_instances=1`, `coalesce=True`) make it safe.
- Do not add generated runtime files to git: `backend/data.db`, `backend/logs/`, `backend/static/`, `frontend/dist/`, local `.env`, cache/vector DB folders, Docker artifacts.

## Testing Guidance

General local checks:

```bash
cd backend
uv run pytest
```

Useful focused tests:

```bash
uv run pytest test/test_scheduler_first_decision_timing.py
uv run pytest test/test_tool_call_guardrails.py
uv run pytest test/test_tool_use_dynamic_schema.py
uv run pytest test/test_llm_tool_signature_roundtrip.py
uv run pytest test/test_agent_tool_edge_cases.py
uv run pytest test/test_baselines.py
uv run pytest test/test_asset_curve_cache_limits.py
uv run pytest test/test_kline_repo_upsert.py
```

Integration-style tests may require configured credentials or infrastructure:

- `test_gemini_openai_compat_integration.py`, `test_grok_openai_compat_integration.py`, `test_account_llm_connection.py` need model endpoints/keys.
- `test_memory_pinecone_init.py` needs Pinecone config.
- `kline_tool_cache_hit_test.py` needs Redis/tool cache.
- MySQL-specific trace tests need a MySQL `DATABASE_URL`.

Frontend verification:

```bash
pnpm run build:frontend
```

Full app smoke:

```bash
pnpm run dev
# then open http://localhost:5621 and check backend /api/health
```

Remember that starting the backend starts background trading/scheduler services. For narrow service tests, prefer pytest over a live dev server.

## Non-Obvious Things To Know

- `backend/main.py` registers the SPA catch-all route last. Add API routes before the catch-all.
- The default user is `default`; frontend code assumes this for paper trading.
- `api_key` values may be plaintext legacy, encrypted, hashed/default placeholders, or resolved from fallback env. Use `services.security.api_key_security.resolve_runtime_api_key()`.
- `verify=False` is currently used in the legacy direct requests path for custom OpenAI-compatible endpoints. Do not change this without considering local gateway compatibility.
- Agent sandbox containers are network-disabled and resource-limited. Public web/search tools run from backend services, not from the sandbox shell.
- `ContainerService` is a singleton that builds `agent-sandbox:latest` from `backend/services/agent/docker` if missing and cleans up stale managed containers on pool init.
- Kline tool output is written to `/workspace/*.json` inside the account sandbox, then inspected with `read_file` or `run_python_script`.
- `run_python_script` executes a file, not a REPL. The script must `print()` output; a final bare expression is not shown.
- Asset equity uses margin/leverage semantics. `total_assets` is cash + positions market equity, while notional exposure is tracked separately for risk display.
- `AI_TRADE_FIRST_EXECUTION_TIME` is timezone-sensitive ISO 8601. Scheduler tests cover first-run catch-up behavior; do not rewrite scheduling date math casually.
- The project has no Alembic. If you add columns, add startup migration or a script and document whether existing SQLite/MySQL DBs are handled.
