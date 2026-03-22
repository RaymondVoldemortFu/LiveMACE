# Open Alpha Arena

This is a project inspired by [nof1 Alpha Arena](https://nof1.ai), you can setup AI trading bot on crypto market.

## Star History

<a href="https://www.star-history.com/#etrobot/open-alpha-arena&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=RaymondVoldemortFu/open-alpha-arena-bench&type=date&theme=dark&legend=top-left" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=RaymondVoldemortFu/open-alpha-arena-bench&type=date&legend=top-left" />
   <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=etrobot/open-alpha-arena&type=date&legend=top-left" />
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
# create backend/.env and fill in at least:
# API_KEY=...
# BASE_URL=...
```

2) Prepare sqlite file on host (for persistence):

```bash
touch backend/data.db
```

3) Build and start service:

```bash
docker compose up -d --build
```

4) View logs / stop:

```bash
docker compose logs -f app
docker compose down
```

Notes:
- `docker-compose.yml` mounts `./backend/data.db` to `/app/data.db`, matching `DATABASE_URL=sqlite:///./data.db`.
- The compose file also mounts `/var/run/docker.sock` so backend sandbox container features can work.

### Batch Account Initialization
From `backend/`:

```bash
uv run python create_accounts_from_env.py
```

Optional flags:
- Update existing same-name accounts:

```bash
uv run python create_accounts_from_env.py --update-existing
```

- Create all model accounts for all `API_KEY*` x `BASE_URL*` combinations:

```bash
uv run python create_accounts_from_env.py --mode all-combinations
```

Combination mode env naming:
- Base variables: `API_KEY`, `BASE_URL`
- Optional suffixed variables: `API_KEY_<SUFFIX>`, `BASE_URL_<SUFFIX>`
- The script creates Cartesian products of all discovered API keys and base URLs, then creates one account per model for each combination.

## License
MIT
