# Sönaiya Backend - FastAPI

This directory contains the main API for the Sönaiya project.

## Tech Stack
- **Framework**: FastAPI (Python 3.x)
- **Database**: PostgreSQL via SQLAlchemy (async)
- **Migrations**: Alembic
- **Real-time**: WebSockets (with JWT Token authentication)
- **Cache/Background**: Redis
- **Deployment**: Runs via Docker locally (`docker-compose`), but is **hosted on Railway** for production.

## Common Commands
- **Start dev server**: `uvicorn app.main:app --reload --host 0.0.0.0 --port 8000`
- **Create a migration**: `alembic revision --autogenerate -m "description"`
- **Apply migrations**: `alembic upgrade head`
- **Virtual Environment**: Activate via `source ../.venv/bin/activate` (or similar depending on your local python env setup).

## Specific Rules & Architecture
- **Dates & Times**: Never use `datetime.utcnow()` (deprecated). Always use `datetime.now(timezone.utc)`. In SQLAlchemy models, use `DateTime(timezone=True)`.
- **Authentication**: Secure endpoints using the `get_current_user`, `get_current_livreur` or `get_current_partenaire` dependencies (from `app/api/dependencies.py`).
- **State Machine**: Order statuses must strictly follow the flow defined in the `TRANSITIONS_VALIDES` dictionary (in `app/api/v1/endpoints/commandes.py`). No wild/unauthorized transitions.
- **WebSockets**: The WS connection requires the JWT token to be passed explicitly as a query parameter `?token=xxx`.
- **Pagination**: Queries listing orders use pagination returning a dictionary: `{ "total": N, "page": P, "pages": X, "commandes": [...] }`.
