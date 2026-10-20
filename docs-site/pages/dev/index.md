# Developer guide

Stub. The full guide is written in milestone M8. Until then see `docs/ARCHITECTURE.md`, `docs/IMPLEMENTATION_PLAN.md` and the module READMEs under `backend/src/scout/`.

## Quick start

```bash
make db          # start PostgreSQL 16 and create the scout / scout_test databases
make migrate     # apply Alembic migrations
make dev         # api + worker (+ frontend when installed)
make check       # everything CI runs
```
