FROM python:3.12-slim
# git is only needed to install llmrouter-free from a git URL.
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock ./backend/
COPY backend/src ./backend/src
COPY backend/migrations ./backend/migrations
COPY backend/alembic.ini ./backend/alembic.ini
COPY backend/tests/fixtures/llm ./backend/tests/fixtures/llm
COPY config ./config
COPY prompts ./prompts
COPY tests/fixtures/openalex ./tests/fixtures/openalex
COPY scripts ./scripts
WORKDIR /app/backend
RUN uv sync --frozen --no-dev
# The real LLM router (not in the lock file, DECISIONS 3). Empty = skip, then only LLM_BACKEND=fake works.
ARG LLM_ROUTER_SOURCE=git+https://github.com/fredbuildsai/llmrouter-free
RUN if [ -n "$LLM_ROUTER_SOURCE" ]; then uv pip install --python /app/backend/.venv/bin/python "$LLM_ROUTER_SOURCE"; fi
ENV PATH="/app/backend/.venv/bin:$PATH" PYTHONUNBUFFERED=1
CMD ["uvicorn", "scout.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
