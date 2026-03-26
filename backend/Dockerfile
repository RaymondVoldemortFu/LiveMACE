FROM python:3.13-slim

RUN pip install uv

WORKDIR /app

# Copy dependency manifest first for better layer caching.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen

# Copy backend source code.
COPY . ./

# Ensure package discovery works in all directories with Python files.
RUN find . -name "*.py" -exec dirname {} \; | xargs -I {} touch {}/__init__.py

ENV VIRTUAL_ENV=/app/.venv
ENV PATH="$VIRTUAL_ENV/bin:$PATH"
ENV PYTHONPATH=/app

EXPOSE 5611

CMD ["uv", "run", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "5611"]
