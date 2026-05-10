from typing import List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import models
import schemas


async def create_user(db: AsyncSession, user: schemas.UserCreate):
    obj = models.User(name=user.name, email=user.email)
    db.add(obj)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise
    await db.refresh(obj)
    return obj


async def get_users(db: AsyncSession):
    result = await db.execute(select(models.User))
    return result.scalars().all()


# —— Notifications ——


async def list_notifications(
    db: AsyncSession, unread_only: bool = False
) -> List[models.Notification]:
    q = select(models.Notification).order_by(models.Notification.created_at.desc())
    if unread_only:
        q = q.where(models.Notification.read.is_(False))
    result = await db.execute(q)
    return list(result.scalars().all())


async def count_unread(db: AsyncSession) -> int:
    q = select(func.count(models.Notification.id)).where(
        models.Notification.read.is_(False)
    )
    result = await db.execute(q)
    return int(result.scalar_one())


async def create_notification(
    db: AsyncSession, payload: schemas.NotificationCreate
) -> models.Notification:
    obj = models.Notification(title=payload.title, body=payload.body, read=False)
    db.add(obj)
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    await db.refresh(obj)
    return obj


async def patch_notification(
    db: AsyncSession, notification_id: int, patch: schemas.NotificationPatch
) -> Optional[models.Notification]:
    result = await db.execute(
        select(models.Notification).where(models.Notification.id == notification_id)
    )
    obj = result.scalar_one_or_none()
    if obj is None:
        return None
    if patch.read is not None:
        obj.read = patch.read
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    await db.refresh(obj)
    return obj


async def mark_all_read(db: AsyncSession) -> int:
    rows = await db.execute(
        select(models.Notification).where(models.Notification.read.is_(False))
    )
    objs = rows.scalars().all()
    n = 0
    for o in objs:
        o.read = True
        n += 1
    if n:
        try:
            await db.commit()
        except Exception:
            await db.rollback()
            raise
    return n


async def seed_demo_notifications_if_empty(db: AsyncSession) -> Tuple[int, int]:
    """Returns (existing_count, inserted_count)."""
    total = await db.scalar(select(func.count(models.Notification.id)))
    total = int(total or 0)
    if total > 0:
        return (total, 0)
    samples = [
        models.Notification(
            title="HOS compliance reminder",
            body="Review driver logs weekly to stay audit-ready.",
            read=False,
        ),
        models.Notification(
            title="Database connected",
            body="Notifications are stored in your configured database (SQLite or Postgres).",
            read=False,
        ),
    ]
    for s in samples:
        db.add(s)
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    return (0, len(samples))
