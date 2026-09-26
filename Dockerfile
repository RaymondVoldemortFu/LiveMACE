FROM node:20-alpine AS frontend-build

WORKDIR /app
RUN npm install -g pnpm@10.22.0
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY frontend/package.json frontend/package.json
RUN pnpm install --frozen-lockfile
COPY frontend/ frontend/
ARG VITE_ENABLE_PAPER_TRADING=false
ENV VITE_ENABLE_PAPER_TRADING=$VITE_ENABLE_PAPER_TRADING
RUN pnpm --dir frontend run build

FROM python:3.13-slim

RUN pip install --no-cache-dir uv==0.8.22
WORKDIR /app/backend

COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/ ./
COPY examples/extensions /app/examples/extensions
RUN uv sync --frozen --no-dev

COPY --from=frontend-build /app/frontend/dist /app/backend/static

ENV PATH="/app/backend/.venv/bin:$PATH" PYTHONPATH=/app/backend
EXPOSE 5611
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "5611", "--workers", "1"]
