# Open Alpha Arena

This is a project inspired by [nof1 Alpha Arena](https://nof1.ai), you can setup AI trading bot on crypto market.

## Star History

<a href="https://www.star-history.com/#etrobot/open-alpha-arena&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=RaymondVoldemortFu/open-alpha-arena-bench&type=date&theme=dark&legend=top-left" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=RaymondVoldemortFu/open-alpha-arena-bench&type=date&legend=top-left" />
   <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=RaymondVoldemortFu/open-alpha-arena-bench&type=date&legend=top-left" />
 </picture>
</a>

## Getting Started

### Prerequisites
- Node.js 18+ and pnpm
- Python 3.10+ and uv

### Install
```bash
# install JS deps and sync Python env
pnpm run install:all
```

### Development
By default, the workspace scripts launch:
- Backend on port 5611
- Frontend on port 5621

Start both dev servers:
```bash
pnpm run dev
```
Open:
- Frontend: http://localhost:5621
- Backend WS: ws://localhost:5611/ws

Important: The frontend source is currently configured for port 5621. To use the workspace defaults (5611), update the following in frontend/app/main.tsx:
- WebSocket URL: ws://localhost:5611/ws
- API_BASE: http://127.0.0.1:5611

Alternatively, run the backend on 5611:
```bash
# from repo root
cd backend
uv sync
uv run uvicorn main:app --reload --port 5611 --host 0.0.0.0
```

### Build
```bash
# build frontend; backend has no dedicated build step
pnpm run build
```
Static assets for the frontend are produced by Vite. The backend is a standard FastAPI app that can be run with Uvicorn or any ASGI server.

### Docker Deploy
1) Prepare environment variables:

```bash
cp backend/.env.example backend/.env
# then edit backend/.env and fill in at least:
# API_KEY=your-key
# BASE_URL=https://your-endpoint/v1
```

2) Build and start service:

```bash
docker compose up -d --build
```

3) View logs / stop:

```bash
docker compose logs -f app
docker compose down
```

Linux one-click deploy script:

```bash
./scripts/linux_dev_one_click_deploy.sh
```

Notes:
- `docker-compose.yml` starts a MySQL service by default and injects `DATABASE_URL` into app container.
- `backend/.env.example` includes DB pool parameters for 25+ concurrent agents.
- The compose file also mounts `/var/run/docker.sock` so backend sandbox container features can work.

### Batch Account Initialization
From `backend/`:

```bash
uv run python script/create_accounts_from_env.py --mode all-combinations
```

Note:
- The script auto-initializes missing database tables (including `users` / `accounts`) on first run.

Optional flags:
- Update existing same-name accounts:

```bash
uv run python script/create_accounts_from_env.py --update-existing
```

- Create all model accounts for all account-config combinations from `.env`:

```bash
uv run python script/create_accounts_from_env.py --mode all-combinations
```

Combination mode config:
- Runtime API config still uses `API_KEY` and `BASE_URL`.
- Account combinations come from CSV via `.env` var `ACCOUNT_COMBO_CSV_PATH`.
- One CSV row equals one combination, then script creates one account per model for each row.
- Required CSV columns:
  - `account_type,agent_type,memory_enabled,tool_routing_enabled,enable_rule_aware,is_active`

## License
MIT
