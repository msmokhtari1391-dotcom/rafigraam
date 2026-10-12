"""
حریم خصوصی و امنیت — نشست‌های فعال، کاربران بلاک‌شده، نمایش آخرین بازدید.
"""
from datetime import timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import get_current_sid, get_current_user
from .db import get_session
from .models import AuthSession, Block, User

router = APIRouter(prefix="/privacy", tags=["privacy"])


def _iso(dt) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


# ── نشست‌های فعال ──────────────────────────────────
class SessionOut(BaseModel):
    session_id: str
    device: str
    created_at: str
    last_active_at: str
    current: bool = False


@router.get("/sessions", response_model=list[SessionOut])
async def list_sessions(
    user: User = Depends(get_current_user),
    sid: Optional[str] = Depends(get_current_sid),
    db: AsyncSession = Depends(get_session),
):
    rows = (
        await db.execute(
            select(AuthSession)
            .where(AuthSession.user_id == user.id, AuthSession.revoked == False)  # noqa: E712
            .order_by(desc(AuthSession.last_active_at))
        )
    ).scalars().all()
    return [
        SessionOut(
            session_id=s.id,
            device=s.device or "دستگاه ناشناس",
            created_at=_iso(s.created_at) or "",
            last_active_at=_iso(s.last_active_at) or "",
            current=(s.id == sid),
        )
        for s in rows
    ]


@router.delete("/sessions/{session_id}")
async def revoke_session(
    session_id: str,
    user: User = Depends(get_current_user),
    sid: Optional[str] = Depends(get_current_sid),
    db: AsyncSession = Depends(get_session),
):
    if session_id == sid:
        raise HTTPException(400, "نشست فعلی رو نمی‌تونی از اینجا ببندی — «خروج» رو بزن")
    s = (
        await db.execute(
            select(AuthSession).where(
                AuthSession.id == session_id, AuthSession.user_id == user.id
            )
        )
    ).scalar_one_or_none()
    if not s:
        raise HTTPException(404, "نشست پیدا نشد")
    s.revoked = True
    db.add(s)
    await db.commit()
    return {"ok": True}


@router.post("/sessions/revoke_others")
async def revoke_other_sessions(
    user: User = Depends(get_current_user),
    sid: Optional[str] = Depends(get_current_sid),
    db: AsyncSession = Depends(get_session),
):
    rows = (
        await db.execute(
            select(AuthSession).where(
                AuthSession.user_id == user.id, AuthSession.revoked == False  # noqa: E712
            )
        )
    ).scalars().all()
    n = 0
    for s in rows:
        if s.id != sid:
            s.revoked = True
            db.add(s)
            n += 1
    if n:
        await db.commit()
    return {"ok": True, "revoked": n}


# ── بلاک ───────────────────────────────────────────
class BlockedOut(BaseModel):
    user_id: str
    username: str
    display_name: str
    avatar_url: Optional[str] = None


class BlockRequest(BaseModel):
    username: str


async def is_blocked(db: AsyncSession, blocker_id: str, blocked_id: str) -> bool:
    row = (
        await db.execute(
            select(Block).where(
                Block.user_id == blocker_id, Block.blocked_id == blocked_id
            )
        )
    ).scalar_one_or_none()
    return row is not None


async def blocked_between(db: AsyncSession, a_id: str, b_id: str) -> bool:
    """آیا یکی از این دو نفر اون یکی رو بلاک کرده؟"""
    row = (
        await db.execute(
            select(Block).where(
                ((Block.user_id == a_id) & (Block.blocked_id == b_id))
                | ((Block.user_id == b_id) & (Block.blocked_id == a_id))
            )
        )
    ).scalars().first()
    return row is not None


@router.get("/blocks", response_model=list[BlockedOut])
async def list_blocks(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    rows = (
        await db.execute(select(Block).where(Block.user_id == user.id))
    ).scalars().all()
    out = []
    for b in rows:
        u = (
            await db.execute(select(User).where(User.id == b.blocked_id))
        ).scalar_one_or_none()
        if u:
            out.append(
                BlockedOut(
                    user_id=u.id, username=u.username,
                    display_name=u.display_name, avatar_url=u.avatar_url,
                )
            )
    return out


@router.post("/blocks")
async def add_block(
    body: BlockRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    uname = (body.username or "").strip().lstrip("@").lower()
    target = (
        await db.execute(select(User).where(User.username == uname))
    ).scalar_one_or_none()
    if not target:
        raise HTTPException(404, "کاربر پیدا نشد")
    if target.id == user.id:
        raise HTTPException(400, "خودت رو نمی‌تونی بلاک کنی")
    if await is_blocked(db, user.id, target.id):
        return {"ok": True, "already": True}
    db.add(Block(user_id=user.id, blocked_id=target.id))
    await db.commit()
    return {"ok": True}


@router.delete("/blocks/{username}")
async def remove_block(
    username: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    uname = (username or "").strip().lstrip("@").lower()
    target = (
        await db.execute(select(User).where(User.username == uname))
    ).scalar_one_or_none()
    if not target:
        raise HTTPException(404, "کاربر پیدا نشد")
    b = (
        await db.execute(
            select(Block).where(
                Block.user_id == user.id, Block.blocked_id == target.id
            )
        )
    ).scalar_one_or_none()
    if not b:
        raise HTTPException(404, "بلاک نیست")
    await db.delete(b)
    await db.commit()
    return {"ok": True}


# ── تنظیمات حریم خصوصی ────────────────────────────
class PrivacySettings(BaseModel):
    show_last_seen: bool = True


@router.get("/settings", response_model=PrivacySettings)
async def get_privacy(user: User = Depends(get_current_user)):
    return PrivacySettings(show_last_seen=bool(user.show_last_seen))


@router.put("/settings", response_model=PrivacySettings)
async def set_privacy(
    body: PrivacySettings,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    user.show_last_seen = bool(body.show_last_seen)
    db.add(user)
    await db.commit()
    return PrivacySettings(show_last_seen=user.show_last_seen)
