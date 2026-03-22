#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

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

mkdir -p "backend"
touch "backend/data.db"

echo "[INFO] Validating compose config..."
docker compose config >/dev/null

echo "[INFO] Building and starting services..."
docker compose up -d --build

echo "[INFO] Deployment done."
echo "[INFO] Service status:"
docker compose ps

echo "[INFO] Follow logs with:"
echo "  docker compose logs -f app"
