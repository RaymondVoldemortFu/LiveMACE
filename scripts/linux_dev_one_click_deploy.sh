#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

FRESH=0
for arg in "$@"; do
  case "${arg}" in
    --fresh) FRESH=1 ;;
    -h|--help)
      echo "Usage: $0 [--fresh]"
      echo "  --fresh  Remove MySQL volume (wipe DB), rebuild stack, run create_accounts_from_env.py (default all-combinations mode)"
      echo "           (requires ACCOUNT_COMBO_CSV_PATH in backend/.env, e.g. ./config/account_combinations.example.csv)"
      exit 0
      ;;
  esac
done

echo "[INFO] Repo root: ${REPO_ROOT}"

if ! command -v docker >/dev/null 2>&1; then
  echo "[ERROR] docker not found. Please install Docker first."
  exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "[ERROR] docker compose not available. Please install Docker Compose v2."
  exit 1
fi

if [[ ! -f "backend/.env" ]]; then
  echo "[WARN] backend/.env not found. Creating from backend/.env.example ..."
  cp "backend/.env.example" "backend/.env"
  echo "[ERROR] Please edit backend/.env with real API credentials, then rerun this script."
  exit 1
fi

echo "[INFO] Validating compose config..."
docker compose config >/dev/null

if [[ "${FRESH}" -eq 1 ]]; then
  echo "[INFO] --fresh: stopping stack and removing volumes (MySQL data will be deleted)..."
  docker compose down -v
fi

echo "[INFO] Building and starting services (MySQL, Redis/Valkey, backend, frontend)..."
docker compose up -d --build

if [[ "${FRESH}" -eq 1 ]]; then
  echo "[INFO] Waiting for backend /api/health ..."
  for _ in $(seq 1 90); do
    if curl -sf "http://127.0.0.1:5611/api/health" >/dev/null 2>&1; then
      break
    fi
    sleep 2
  done
  if ! curl -sf "http://127.0.0.1:5611/api/health" >/dev/null 2>&1; then
    echo "[ERROR] Backend did not become healthy on :5611. Check: docker compose logs backend"
    exit 1
  fi
  echo "[INFO] Running account batch script (default all-combinations mode; API_KEY/BASE_URL + ACCOUNT_COMBO_CSV_PATH from backend/.env)..."
  docker compose exec -T backend uv run python script/create_accounts_from_env.py
fi

echo "[INFO] Deployment done."
echo "[INFO] Service status:"
docker compose ps

echo "[INFO] Ports are published on all interfaces (0.0.0.0): HTTP 80, API 5611, MySQL 3306, Redis 6379."
echo "[INFO] For public access, open these ports in your cloud security group / firewall if needed."
echo "[INFO] Follow logs with:"
echo "  docker compose logs -f backend"
