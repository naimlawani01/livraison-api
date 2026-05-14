# Sonaiyaa Backend - FastAPI

This directory contains the main API for the Sonaiyaa project.

## Tech Stack
- **Framework**: FastAPI (Python 3.x)
- **Database**: PostgreSQL via SQLAlchemy (async)
- **Migrations**: Alembic
- **Real-time**: WebSockets with Redis Pub/Sub (multi-worker scaling)
- **Cache/Background**: Redis
- **Storage**: Cloudflare R2 via `boto3` (S3-compatible)
- **Payments**: GeniusPay (Mobile Money checkout + driver payouts)
- **SMS**: PasseInfo (OTP authentication — provider local Guinée)
- **Push Notifications**: Firebase FCM (`firebase-admin`)
- **Deployment**: Runs via Docker locally (`docker-compose`), but is **hosted on Railway** for production. Reachable at `https://ample-mindfulness-production.up.railway.app`, `https://api.sonaiyaa.fr`, and `https://api.sonaiyaa.com` (all three route to the same instance).

## Common Commands
- **Start dev server**: `uvicorn app.main:app --reload --host 0.0.0.0 --port 8000`
- **Create a migration**: `alembic revision --autogenerate -m "description"`
- **Apply migrations**: `alembic upgrade head`
- **Virtual Environment**: Activate via `source ../.venv/bin/activate` (or similar depending on your local python env setup).

## Key Environment Variables
- `SECRET_KEY` — JWT signing secret
- `DATABASE_URL` — PostgreSQL connection string
- `REDIS_URL` — Redis connection string
- `PUBLIC_BASE_URL` — Full backend URL used in SMS/link generation (production: `https://api.sonaiyaa.fr`)
- `CORS_ORIGINS` — Comma-separated list of allowed origins. Must include the live frontend domains (`https://sonaiyaa.fr`, `https://sonaiyaa.com`, and the future admin-web origin).
- `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET_NAME` (`sonaiyaa-documents`), `R2_PUBLIC_URL` — Cloudflare R2
- `GENIUSPAY_API_KEY`, `GENIUSPAY_API_SECRET`, `GENIUSPAY_WEBHOOK_SECRET`, `GENIUSPAY_BASE_URL`, `GENIUSPAY_WALLET_ID` — Payment gateway
- `PASSEINFO_API_KEY`, `PASSEINFO_CLIENT_ID`, `PASSEINFO_SENDER_NAME` — PasseInfo SMS OTP
- `FIREBASE_CREDENTIALS` (inline JSON) or `FIREBASE_CREDENTIALS_PATH` — Push notifications
- `MAX_COURSES_SIMULTANEES` (default: 2) — Max parallel deliveries per driver
- `DEFAULT_SEARCH_RADIUS_KM`, `MAX_SEARCH_RADIUS_KM`, `MIN_SEARCH_RADIUS_KM` — Geolocation for driver matching
- `SENTRY_DSN` — Optionnel. Si renseigné, Sentry capture les exceptions FastAPI + SQLAlchemy. Laisser vide pour désactiver. `send_default_pii=False` est forcé (évite de fuiter password/OTP).
- `SENTRY_TRACES_SAMPLE_RATE` (default `0.1`) — Fraction des requêtes tracées (perf monitoring).
- `ENVIRONMENT` (default `production`) — Identifie le déploiement dans Sentry.

## Specific Rules & Architecture
- **Dates & Times**: Never use `datetime.utcnow()` (deprecated). Always use `datetime.now(timezone.utc)`. In SQLAlchemy models, use `DateTime(timezone=True)`.
- **Cryptographically random codes**: Never use `random.randint()` for OTPs, delivery codes, or any token that an attacker could try to guess. Use `secrets` instead — wrappers ready in `app/core/security.py` (`generate_otp()`, `generate_delivery_code()`).
- **Concurrent wallet operations**: Any code that reads-then-writes the livreur `solde_disponible` MUST use `select(Livreur).where(...).with_for_update()` to lock the row (cf. `wallet.py:demander_retrait`). Without it, two concurrent requests can dual-debit the same balance — costs real money.
- **Logs**: Logging is JSON-structured via `python-json-logger` (see `main.py`). Pass `extra={"key": value}` so the fields are searchable in Railway / Sentry. Avoid bare `print()`.
- **Sentry**: Errors are auto-captured if `SENTRY_DSN` is set in env. Use `sentry_sdk.set_user({"id": str(user.id)})` in middlewares/dependencies if you want richer context.
- **Rate limiting**: `slowapi` is wired in `app/main.py` with Redis as storage backend (shared across uvicorn workers). To rate-limit a new endpoint:
  ```python
  from ....core.rate_limit import limiter
  @router.post("/my-endpoint")
  @limiter.limit("10/minute")  # by client IP
  async def my_endpoint(request: Request, ...):
      ...
  ```
  ⚠️ `request: Request` **must** be the first parameter — slowapi needs it to read the client IP. Existing limits:
  | Endpoint | Limit | Reason |
  |---|---|---|
  | `/auth/login` | 10/min | anti brute-force |
  | `/auth/register` | 5/hour | anti spam |
  | `/auth/request-otp` | 10/hour | + the custom `_check_otp_rate_limit` (3/5min by phone) |
  | `/auth/verify-otp` | 20/hour | + custom OTP rate limit |
  | `/loc/{token}/submit` | 20/min | public endpoint, anti flood |
  | (everything else) | 120/min default | global guardrail |
- **DB composite indexes**: hot query paths have composite indexes (see `app/models/commande.py:__table_args__` and `wallet_transaction.py`). Migration `017_add_composite_indexes.py` applies them in prod. When adding a query that filters on multiple columns at once, check if an index covers it — otherwise add one (in the model + a migration).
- **Authentication**: Secure endpoints using the `get_current_user`, `get_current_livreur` or `get_current_partenaire` dependencies (from `app/api/dependencies.py`). JWT tokens expire after 8h (refresh: 30 days). Login is phone + OTP via PasseInfo SMS.
- **State Machine**: Order statuses must strictly follow the flow defined in the `TRANSITIONS_VALIDES` dictionary (in `app/api/v1/endpoints/commandes.py`). No wild/unauthorized transitions.
- **WebSockets**: The WS connection requires the JWT token as a query parameter `?token=xxx`. The `ConnectionManager` uses Redis Pub/Sub for broadcasting across multiple workers.
- **Pagination**: Queries listing orders use pagination returning a dictionary: `{ "total": N, "page": P, "pages": X, "commandes": [...] }`.

## Services Layer (`app/services/`)
- **`storage_service.py`**: Cloudflare R2 uploads. Driver docs go to `livreurs/{uuid}.{ext}`, merchant photos to `partenaires/{uuid}.{ext}`. Generates presigned URLs for admin review.
- **`genius_pay_service.py`**: GeniusPay integration. `initier_paiement()` creates a checkout URL; `initier_payout()` sends driver withdrawals. Webhook verification uses HMAC-SHA256 (`timestamp.payload`) with 5-minute replay protection.
- **`sms_service.py`**: PasseInfo SMS — OTP dispatch. Endpoint `POST /v1/message/single_message`, auth via headers `api_key` + `client_id`. Numéros au format `6XXXXXXXX` (sans indicatif).
- **`notification_service.py`**: Firebase FCM — push notifications to mobile apps.

## Key API Endpoints
- `POST /api/v1/livreurs/upload-document` — Upload driver documents (piece_identite, vehicule_doc, photo_profil) to R2
- `POST /api/v1/partenaires/me/upload-document` — Upload merchant storefront photo to R2
- `GET /api/v1/livreurs/me/wallet` — Driver wallet summary (balance, gains, completed deliveries)
- `GET /api/v1/livreurs/me/wallet/transactions` — Paginated transaction history
- `POST /api/v1/livreurs/me/retrait` — Request withdrawal to Mobile Money (montant, methode, numero_telephone)
- `POST /api/v1/payments/commandes/{id}/relancer` — Regenerate expired payment link
- `POST /api/v1/payments/webhooks/geniuspay` — GeniusPay webhook receiver
- `GET /api/v1/admin/stats` — Platform stats (users, drivers, orders, revenue, completion rate)
- `GET /api/v1/admin/livreurs/en-attente` — Drivers pending verification
- `POST /api/v1/admin/test-accounts` — Create pre-verified Partenaire/Livreur test accounts (used for Apple/Play store reviewers and internal QA). Phones must start with `+224600` (unallocated GN prefix). Auto-marks the user/profile as `is_verified=true` so the reviewer can sign in immediately.
- `GET /api/v1/admin/test-accounts` — List all test accounts (filtered by `+224600` prefix)
- `DELETE /api/v1/admin/test-accounts/{user_id}` — Remove a test account (refuses non-test phones for safety)
- `GET /health` — Health check endpoint (used by Docker & Railway)
