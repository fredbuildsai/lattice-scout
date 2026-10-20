FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock ./backend/
COPY backend/src ./backend/src
COPY backend/migrations ./backend/migrations
COPY backend/alembic.ini ./backend/alembic.ini
COPY config ./config
WORKDIR /app/backend
RUN uv sync --frozen --no-dev
ENV PATH="/app/backend/.venv/bin:$PATH" PYTHONUNBUFFERED=1
CMD ["uvicorn", "scout.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
