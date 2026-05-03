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
- **Deployment**: Runs via Docker locally (`docker-compose`), but is **hosted on Railway** for production.

## Common Commands
- **Start dev server**: `uvicorn app.main:app --reload --host 0.0.0.0 --port 8000`
- **Create a migration**: `alembic revision --autogenerate -m "description"`
- **Apply migrations**: `alembic upgrade head`
- **Virtual Environment**: Activate via `source ../.venv/bin/activate` (or similar depending on your local python env setup).

## Key Environment Variables
- `SECRET_KEY` — JWT signing secret
- `DATABASE_URL` — PostgreSQL connection string
- `REDIS_URL` — Redis connection string
- `PUBLIC_BASE_URL` — Full backend URL used in SMS/link generation (production: `https://ample-mindfulness-production.up.railway.app`)
- `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET_NAME` (`sonaiyaa-documents`), `R2_PUBLIC_URL` — Cloudflare R2
- `GENIUSPAY_API_KEY`, `GENIUSPAY_API_SECRET`, `GENIUSPAY_WEBHOOK_SECRET`, `GENIUSPAY_BASE_URL`, `GENIUSPAY_WALLET_ID` — Payment gateway
- `PASSEINFO_API_KEY`, `PASSEINFO_CLIENT_ID`, `PASSEINFO_SENDER_NAME` — PasseInfo SMS OTP
- `FIREBASE_CREDENTIALS` (inline JSON) or `FIREBASE_CREDENTIALS_PATH` — Push notifications
- `MAX_COURSES_SIMULTANEES` (default: 2) — Max parallel deliveries per driver
- `DEFAULT_SEARCH_RADIUS_KM`, `MAX_SEARCH_RADIUS_KM`, `MIN_SEARCH_RADIUS_KM` — Geolocation for driver matching

## Specific Rules & Architecture
- **Dates & Times**: Never use `datetime.utcnow()` (deprecated). Always use `datetime.now(timezone.utc)`. In SQLAlchemy models, use `DateTime(timezone=True)`.
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
- `GET /health` — Health check endpoint (used by Docker & Railway)
