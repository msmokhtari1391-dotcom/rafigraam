"""
گیف‌های ذخیره‌شده‌ی کاربر — «گیف‌های من».

هر کاربر می‌تونه گیفی که در چت دیده رو به فهرستِ خودش اضافه کنه و بعداً
از پنلِ گیفِ نوارِ نوشتن، بی‌آپلودِ دوباره بفرستدش.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import get_current_user
from .db import get_session
from .models import SavedGif, User

router = APIRouter(prefix="/gifs", tags=["gifs"])

MAX_GIFS = 100


class GifRequest(BaseModel):
    url: str


def _norm(raw: Optional[str]) -> str:
    """فقط آدرسِ مدیای همین سرور یا یک URL کاملِ ساده پذیرفته می‌شه."""
    u = (raw or "").strip()
    if not u:
        raise HTTPException(400, "آدرس گیف لازمه")
    if len(u) > 500:
        raise HTTPException(400, "آدرس گیف خیلی بلنده")
    if not (u.startswith("/media/") or u.startswith("http://")
            or u.startswith("https://")):
        raise HTTPException(400, "آدرس گیف معتبر نیست")
    return u


async def _mine(db: AsyncSession, user_id: str) -> list[SavedGif]:
    rows = (await db.execute(
        select(SavedGif).where(SavedGif.user_id == user_id)
        .order_by(SavedGif.created_at.desc())
    )).scalars().all()
    return list(rows)


@router.get("")
async def list_gifs(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    return {"urls": [g.url for g in await _mine(db, user.id)]}


@router.post("/add")
async def add_gif(
    body: GifRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    url = _norm(body.url)
    rows = await _mine(db, user.id)
    if any(g.url == url for g in rows):
        return {"urls": [g.url for g in rows], "added": False}
    if len(rows) >= MAX_GIFS:
        # قدیمی‌ترین رو دور بریز تا جا باز شه
        await db.delete(rows[-1])
    db.add(SavedGif(user_id=user.id, url=url))
    await db.commit()
    return {"urls": [g.url for g in await _mine(db, user.id)], "added": True}


@router.delete("")
async def remove_gif(
    url: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    row = (await db.execute(
        select(SavedGif).where(SavedGif.user_id == user.id,
                               SavedGif.url == url)
    )).scalar_one_or_none()
    if row is not None:
        await db.delete(row)
        await db.commit()
    return {"urls": [g.url for g in await _mine(db, user.id)]}
