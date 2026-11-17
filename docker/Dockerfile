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
# The real LLM router (not in the lock file, DECISIONS 3). Always installed; see the fallback below.
ARG LLM_ROUTER_SOURCE=git+https://github.com/fredbuildsai/llmrouter-free
# A value that is not a git URL, URL or path (empty, or a stray "# comment" from a .env line) falls back to the default.
RUN src="$LLM_ROUTER_SOURCE"; \
    case "$src" in git+*|http*|/*) ;; *) src="git+https://github.com/fredbuildsai/llmrouter-free" ;; esac; \
    uv pip install --python /app/backend/.venv/bin/python "$src"
ENV PATH="/app/backend/.venv/bin:$PATH" PYTHONUNBUFFERED=1
CMD ["uvicorn", "scout.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
