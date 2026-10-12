"""
احراز هویت — ثبت‌نام، ورود، توکن JWT، Dependency برای فست‌ای‌پی‌آی.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import referrals
from .config import settings
from .db import get_session
from .models import AuthSession, User

router = APIRouter(prefix="/auth", tags=["auth"])


# ── ابزارها ────────────────────────────────────────
def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()


def check_password(pw: str, hashed: str) -> bool:
    return bcrypt.checkpw(pw.encode(), hashed.encode())


def create_token(user_id: str, sid: Optional[str] = None) -> str:
    payload = {
        "sub": user_id,
        "exp": datetime.now(timezone.utc)
        + timedelta(days=settings.access_token_ttl_days),
        "iat": datetime.now(timezone.utc),
    }
    if sid:
        payload["sid"] = sid
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def decode_payload(token: str) -> dict:
    """توکن رو رمزگشایی می‌کنه و کل payload رو برمی‌گردونه."""
    try:
        return jwt.decode(token, settings.secret_key, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "توکن منقضی شده — دوباره وارد شو")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "توکن نامعتبره")


def decode_token(token: str) -> str:
    """توکن رو رمزگشایی می‌کنه و user_id برمی‌گردونه."""
    return decode_payload(token)["sub"]


# ── نشست‌ها ────────────────────────────────────────
_TOUCH_EVERY = timedelta(seconds=60)  # هر چند وقت یک‌بار last_seen رو بنویسیم


async def new_session(db: AsyncSession, user_id: str, device: str) -> AuthSession:
    """برای هر ورود/ثبت‌نام یک نشست می‌سازه تا بشه بعداً لغوش کرد."""
    s = AuthSession(user_id=user_id, device=(device or "")[:200])
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return s


def device_name(request: Optional[Request]) -> str:
    """یک اسم خوانا برای دستگاه، از هدر X-Device یا User-Agent."""
    if request is None:
        return "دستگاه ناشناس"
    explicit = (request.headers.get("x-device") or "").strip()
    if explicit:
        return explicit[:200]
    ua = request.headers.get("user-agent", "") or ""
    low = ua.lower()
    if "windows" in low:
        os_name = "Windows"
    elif "android" in low:
        os_name = "Android"
    elif "iphone" in low or "ipad" in low or "ios" in low:
        os_name = "iOS"
    elif "mac" in low:
        os_name = "macOS"
    elif "linux" in low:
        os_name = "Linux"
    else:
        os_name = "نامشخص"
    app = "Rafigram" if "python" in low or "httpx" in low else "مرورگر"
    return f"{app} — {os_name}"


async def _touch(db: AsyncSession, user: User, sid: Optional[str]) -> None:
    """آخرین فعالیتِ کاربر و نشست رو تازه می‌کنه (با محدودیت نوشتن)."""
    now = datetime.now(timezone.utc)
    dirty = False
    last = user.last_seen_at
    if last is not None and last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    if last is None or now - last > _TOUCH_EVERY:
        user.last_seen_at = now
        db.add(user)
        dirty = True
    if sid:
        s = (
            await db.execute(select(AuthSession).where(AuthSession.id == sid))
        ).scalar_one_or_none()
        if s is not None:
            la = s.last_active_at
            if la is not None and la.tzinfo is None:
                la = la.replace(tzinfo=timezone.utc)
            if la is None or now - la > _TOUCH_EVERY:
                s.last_active_at = now
                db.add(s)
                dirty = True
    if dirty:
        await db.commit()


async def check_session(db: AsyncSession, sid: Optional[str]) -> None:
    """اگه نشست لغو شده باشه، دسترسی رو رد می‌کنه."""
    if not sid:
        return  # توکن‌های قدیمی (بدون sid) همچنان معتبرن
    s = (
        await db.execute(select(AuthSession).where(AuthSession.id == sid))
    ).scalar_one_or_none()
    if s is None or s.revoked:
        raise HTTPException(401, "این نشست لغو شده — دوباره وارد شو")


# ── Dependency فست‌ای‌پی‌آی ─────────────────────────
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    cred: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
    session: AsyncSession = Depends(get_session),
) -> User:
    """توکن رو از هدر Authorization: Bearer xxx می‌خونه."""
    if cred is None:
        raise HTTPException(401, "لاگین نکردی")
    payload = decode_payload(cred.credentials)
    user_id = payload["sub"]
    sid = payload.get("sid")
    await check_session(session, sid)
    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(401, "کاربر پیدا نشد")
    # بَنِ زمان‌دار: کاربرِ بنشده هیچ مسیرِ احرازشده‌ای ندارد. 403 با
    # بدنه‌ی JSON تا کلاینت شمارشِ معکوس را بسازد.
    from . import bans
    b = await bans.active_ban(user.id)
    if b is not None:
        raise HTTPException(403, detail=bans.denial_payload(b))
    await _touch(session, user, sid)
    return user


async def get_current_sid(
    cred: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
) -> Optional[str]:
    """شناسه‌ی نشستِ توکنِ همین درخواست (برای علامت‌زدن «این دستگاه»)."""
    if cred is None:
        return None
    try:
        return decode_payload(cred.credentials).get("sid")
    except HTTPException:
        return None


# ── اسکیماهای درخواست/پاسخ ─────────────────────────
class RegisterRequest(BaseModel):
    username: str
    display_name: str
    password: str
    registration_code: Optional[str] = None
    # کدِ دعوت (از رباتِ تلگرام یا کدِ مدیر)
    referral_code: Optional[str] = None


class LoginRequest(BaseModel):
    username: str
    password: str


class AuthResponse(BaseModel):
    token: str
    user_id: str
    username: str
    display_name: str


# ── اندپوینت‌ها ────────────────────────────────────
@router.post("/register", response_model=AuthResponse)
async def register(
    body: RegisterRequest,
    request: Request = None,
    db: AsyncSession = Depends(get_session),
):
    # چک کردن کد دعوت (اگه تنظیم شده باشه)
    if settings.registration_code:
        if body.registration_code != settings.registration_code:
            raise HTTPException(403, "کد دعوت اشتباهه")

    # ── کدِ دعوتِ یک‌بارمصرف (ربات تلگرام) یا کدِ مدیر ──
    # کلاینتِ قدیمی فقط `registration_code` می‌فرستد، پس اگر `referral_code`
    # نیامد همان را می‌خوانیم — تا نسخه‌های قبلی هم کار کنند.
    ref = (body.referral_code or body.registration_code or "")
    if settings.require_referral:
        if not await referrals.is_valid(db, ref):
            raise HTTPException(
                403, "کدِ دعوت نامعتبر یا مصرف‌شده است — از ربات تلگرام کدِ تازه بگیر")

    # چک تکراری نبودن یوزرنیم
    exists = await db.execute(select(User).where(User.username == body.username))
    if exists.scalar_one_or_none():
        raise HTTPException(409, "این یوزرنیم قبلاً گرفته شده")

    user = User(
        username=body.username,
        display_name=body.display_name,
        password_hash=hash_password(body.password),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    # کد را همین‌جا می‌سوزانیم. اگر همان لحظه کسِ دیگری با همین کد ثبت‌نام
    # کرده باشد، `burn` دروغ برمی‌گردانَد و این حساب پس گرفته می‌شود.
    if settings.require_referral:
        if not await referrals.burn(db, ref, user.id):
            await db.delete(user)
            await db.commit()
            raise HTTPException(403, "این کد همین حالا مصرف شد — کدِ تازه بگیر")

    sess = await new_session(db, user.id, device_name(request))
    return AuthResponse(
        token=create_token(user.id, sess.id),
        user_id=user.id,
        username=user.username,
        display_name=user.display_name,
    )


@router.post("/login", response_model=AuthResponse)
async def login(
    body: LoginRequest,
    request: Request = None,
    db: AsyncSession = Depends(get_session),
):
    result = await db.execute(select(User).where(User.username == body.username))
    user = result.scalar_one_or_none()
    if not user or not check_password(body.password, user.password_hash):
        raise HTTPException(401, "یوزرنیم یا رمز اشتباهه")
    # ورودِ کاربرِ بنشده: رمز درست است ولی 403 با شمارش معکوس برمی‌گردد
    from . import bans
    b = await bans.active_ban(user.id)
    if b is not None:
        raise HTTPException(403, detail=bans.denial_payload(b))
    sess = await new_session(db, user.id, device_name(request))
    return AuthResponse(
        token=create_token(user.id, sess.id),
        user_id=user.id,
        username=user.username,
        display_name=user.display_name,
    )


@router.get("/me")
async def me(user: User = Depends(get_current_user)):
    return {
        "user_id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "bio": user.bio or "",
        "avatar_url": user.avatar_url or "",
    }


# ── WebSocket auth helper ──────────────────────────
async def ws_authenticate(token: str, db: AsyncSession) -> User:
    """توکن WebSocket رو اعتبارسنجی می‌کنه و کاربر برمی‌گردونه."""
    payload = decode_payload(token)
    await check_session(db, payload.get("sid"))
    result = await db.execute(select(User).where(User.id == payload["sub"]))
    user = result.scalar_one_or_none()
    if not user:
        raise ValueError("کاربر پیدا نشد")
    from . import bans
    b = await bans.active_ban(user.id)
    if b is not None:
        raise HTTPException(403, detail=bans.denial_payload(b))
    return user
