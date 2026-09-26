FROM python:3.13-slim

RUN pip install --no-cache-dir uv==0.8.22
WORKDIR /app/backend

COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/ ./
COPY examples/extensions /app/examples/extensions
RUN uv sync --frozen --no-dev

ENV PATH="/app/backend/.venv/bin:$PATH" PYTHONPATH=/app/backend
EXPOSE 5611
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "5611", "--workers", "1"]
