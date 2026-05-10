import os
from typing import List

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

import crud
import models
import schemas

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL") or "sqlite+aiosqlite:///./hos_local.db"

_engine_kwargs = {"future": True, "echo": False}
if DATABASE_URL.startswith("postgresql"):
    _engine_kwargs["pool_pre_ping"] = True

engine = create_async_engine(DATABASE_URL, **_engine_kwargs)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

app = FastAPI(title="HOS API")

# Browsers on Vercel (*.vercel.app) need to match; local dev uses localhost.
# Set CORS_ORIGIN_REGEX in Railway to tighten (e.g. your exact Vercel URL only).
_default_cors = (
    r"https?://(localhost|127\.0\.0\.1):\d+"
    r"|https://[\w.-]+\.vercel\.app"
)
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=os.getenv("CORS_ORIGIN_REGEX", _default_cors),
    allow_credentials=True,
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
                        <li><a href="/api/health">Health check</a></li>
                        <li><a href="/api/users">Users API</a></li>
                        <li><a href="/api/notifications">Notifications API</a></li>
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


@app.on_event("startup")
async def startup():
    try:
        async with engine.begin() as conn:
            await conn.run_sync(models.Base.metadata.create_all)
    except Exception as e:
        print(f"WARN: Database init skipped: {e}")
        return

    if os.getenv("HOS_SEED_NOTIFICATIONS", "1") not in ("0", "false", "False"):
        try:
            async with AsyncSessionLocal() as session:
                await crud.seed_demo_notifications_if_empty(session)
        except Exception as e:
            print(f"WARN: Could not seed notifications: {e}")
