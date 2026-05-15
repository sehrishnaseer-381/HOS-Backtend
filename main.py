import os
from typing import List, Optional

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

import crud
import models
import schemas

load_dotenv()


def normalize_database_url(raw: str) -> str:
    """
    Railway sets DATABASE_URL as postgres:// or postgresql:// — SQLAlchemy async
    MUST use postgresql+asyncpg:// or it tries psycopg2 (not installed → crash).

    Handles BOM, uppercase schemes, and accidental postgresql+psycopg2 URLs.
    """
    s = (raw or "").strip().strip("\ufeff")
    if not s or "://" not in s:
        return s

    scheme, rest = s.split("://", 1)
    scheme_lower = scheme.lower()

    if scheme_lower.startswith("sqlite"):
        return s
    if scheme_lower == "postgresql+asyncpg":
        return s
    # Wrong sync/async drivers SQLAlchemy might infer → force asyncpg
    if scheme_lower in ("postgresql+psycopg2", "postgresql+psycopg"):
        return f"postgresql+asyncpg://{rest}"

    if scheme_lower in ("postgres", "postgresql"):
        return f"postgresql+asyncpg://{rest}"

    return s


# Railway may expose DATABASE_URL or (older templates) POSTGRES_URL / DATABASE_PRIVATE_URL
_raw_db = (
    os.getenv("DATABASE_URL")
    or os.getenv("DATABASE_PRIVATE_URL")
    or os.getenv("POSTGRES_URL")
    or "sqlite+aiosqlite:///./hos_local.db"
)
DATABASE_URL = normalize_database_url(_raw_db)

_engine_kwargs = {"future": True, "echo": False}
if "postgresql" in DATABASE_URL:
    _engine_kwargs["pool_pre_ping"] = True

engine = create_async_engine(DATABASE_URL, **_engine_kwargs)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

app = FastAPI(title="HOS API")

# CORS from Vercel / any domain fails if this is too strict or if Railway has a bad
# CORS_ORIGIN_REGEX from older deploys — use allow_origins * by default (no cookie auth).
# Override on Railway with CORS_ALLOW_ORIGINS=https://yourapp.vercel.app,http://localhost:5173
_cors_env = (os.getenv("CORS_ALLOW_ORIGINS") or "*").strip()
if _cors_env == "*":
    _cors_origins = ["*"]
else:
    _cors_origins = [x.strip() for x in _cors_env.split(",") if x.strip()]
    if not _cors_origins:
        _cors_origins = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(IntegrityError)
async def integrity_error_handler(request, exc):
    return JSONResponse(
        status_code=409,
        content={"detail": "Database constraint failed (e.g. duplicate email)."},
    )


@app.exception_handler(DBAPIError)
async def dbapi_error_handler(request, exc):
    print(f"DBAPIError: {exc!r}")
    return JSONResponse(
        status_code=503,
        content={
            "detail": "Database error. Check DATABASE_URL, that Postgres is running, and credentials."
        },
    )


@app.get("/", response_class=HTMLResponse)
async def home():
    return """
        <!doctype html>
        <html>
            <head>
                <meta charset="utf-8" />
                <meta name="viewport" content="width=device-width, initial-scale=1" />
                <title>Simple HOS API</title>
                <style>
                    body { font-family: Arial, sans-serif; margin: 40px; line-height: 1.6; }
                    .card { max-width: 600px; padding: 24px; border: 1px solid #ddd;
                            border-radius: 12px; }
                    a { color: #0b5fff; }
                </style>
            </head>
            <body>
                <div class="card">
                    <h1>Simple HOS API is running</h1>
                    <p>This backend provides API endpoints, not the full frontend UI.</p>
                    <ul>
                        <li><a href="/docs">API docs (Swagger)</a></li>
                        <li><a href="/health">Health (root)</a></li>
                        <li><a href="/api/health">Health (API prefix)</a></li>
                        <li><a href="/api/users">Users API</a></li>
                        <li><a href="/api/notifications">Notifications API</a></li>
                        <li><a href="/api/db-check">Database check (Postgres/SQLite)</a></li>
                    </ul>
                    <p><small>Run from the <code>backend</code> folder:
                    <code>uvicorn main:app --reload --port 8000</code></small></p>
                </div>
            </body>
        </html>
        """


async def get_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


class Health(BaseModel):
    status: str


class DbCheckResponse(BaseModel):
    ok: bool
    backend: str
    users_count: int = 0
    notifications_count: int = 0
    hint: Optional[str] = None


def _database_backend_label() -> str:
    url = DATABASE_URL or ""
    if "postgresql" in url or "postgres" in url:
        return "postgres"
    if "sqlite" in url:
        return "sqlite"
    return "unknown"


@app.get("/api/db-check", response_model=DbCheckResponse)
async def db_check(session: AsyncSession = Depends(get_session)):
    """Runs SELECT 1 and counts rows — use to verify Postgres from /docs or the dashboard."""
    try:
        await session.execute(text("SELECT 1"))
        uc = await session.scalar(select(func.count(models.User.id)))
        nc = await session.scalar(select(func.count(models.Notification.id)))
        return DbCheckResponse(
            ok=True,
            backend=_database_backend_label(),
            users_count=int(uc or 0),
            notifications_count=int(nc or 0),
        )
    except Exception:
        return DbCheckResponse(
            ok=False,
            backend=_database_backend_label(),
            hint="Cannot reach database — fix DATABASE_URL and redeploy, or confirm Postgres allows this host.",
        )


@app.get("/health", response_model=Health)
async def health_root():
    """Same payload as /api/health — some gateways strip path prefixes."""
    return {"status": "ok"}


@app.get("/api/health", response_model=Health)
async def health():
    return {"status": "ok"}


@app.post("/api/users", response_model=schemas.User)
async def create_user(
    user: schemas.UserCreate, session: AsyncSession = Depends(get_session)
):
    return await crud.create_user(session, user)


@app.get("/api/users", response_model=List[schemas.User])
async def list_users(session: AsyncSession = Depends(get_session)):
    return await crud.get_users(session)


@app.get(
    "/api/notifications/unread-count",
    response_model=schemas.UnreadCountResponse,
)
async def notifications_unread_count(session: AsyncSession = Depends(get_session)):
    count = await crud.count_unread(session)
    return {"count": count}


@app.get("/api/notifications", response_model=List[schemas.NotificationResponse])
async def list_notifications(
    unread_only: bool = False,
    session: AsyncSession = Depends(get_session),
):
    return await crud.list_notifications(session, unread_only=unread_only)


@app.post(
    "/api/notifications",
    response_model=schemas.NotificationResponse,
    status_code=201,
)
async def create_notification(
    payload: schemas.NotificationCreate,
    session: AsyncSession = Depends(get_session),
):
    return await crud.create_notification(session, payload)


@app.patch(
    "/api/notifications/{notification_id}",
    response_model=schemas.NotificationResponse,
)
async def patch_notification(
    notification_id: int,
    patch: schemas.NotificationPatch,
    session: AsyncSession = Depends(get_session),
):
    obj = await crud.patch_notification(session, notification_id, patch)
    if obj is None:
        raise HTTPException(status_code=404, detail="Notification not found")
    return obj


class MarkAllReadResponse(BaseModel):
    updated: int


@app.post(
    "/api/notifications/mark-all-read",
    response_model=MarkAllReadResponse,
)
async def mark_all_notifications_read(session: AsyncSession = Depends(get_session)):
    n = await crud.mark_all_read(session)
    return {"updated": n}


def _mask_db_url(url: str) -> str:
    """Log-friendly: hide password if present."""
    if "@" not in url or "://" not in url:
        return url
    try:
        scheme, rest = url.split("://", 1)
        if "@" in rest:
            creds, hostpart = rest.rsplit("@", 1)
            if ":" in creds:
                user, _ = creds.split(":", 1)
                return f"{scheme}://{user}:***@{hostpart}"
        return url
    except Exception:
        return "<database url>"


@app.on_event("startup")
async def startup():
    print(f"HOS API startup — database: {_database_backend_label()} — {_mask_db_url(DATABASE_URL)}")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(models.Base.metadata.create_all)
        print("HOS API: tables ensured (create_all OK).")
    except Exception as e:
        print(f"WARN: Database init skipped: {e}")
        return

    if os.getenv("HOS_SEED_NOTIFICATIONS", "1") not in ("0", "false", "False"):
        try:
            async with AsyncSessionLocal() as session:
                await crud.seed_demo_notifications_if_empty(session)
        except Exception as e:
            print(f"WARN: Could not seed notifications: {e}")
