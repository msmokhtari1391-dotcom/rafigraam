"""مسدودسازیِ زمان‌دارِ حساب — هسته‌ی مشترکِ بات و احرازِ هویت.

دو مصرف‌کننده دارد:
  • `telegram_bot`  → بن/آزادسازی/فهرست (فرمان‌های مدیر)
  • `auth` و `push` → ردِ درخواستِ کاربرِ بنشده، با شمارشِ معکوس

قاعده‌ها:
  • «بَن فعال» = ردیفِ بنِ بنشده‌ای که `until` آن خالی (دائمی) یا در آینده است.
  • آزادسازی ردیف را نمی‌پاکد: `lifted_at` می‌نویسد تا تاریخچه بماند.
  • پیامِ خطا یک کلیدِ JSON برمی‌گرداند نه جمله‌ی فارسیِ قالب‌زده؛ کلاینت
    خودش زمان را با ارقامِ زبانش نشان می‌دهد (تا روی وب/اندروید تازه بماند).
"""
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select

from .db import AsyncSessionLocal
from .models import Ban

log = logging.getLogger("rafigram.bans")

# واحدِ شمارش برای نمایشِ کلاینت — ثانیه
GRANULARITY = {
    "m": 60,
    "h": 3600,
    "d": 86400,
    "w": 604800,
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime) -> datetime:
    """SQLite زمان را naive برمی‌گرداند؛ برای مقایسه با `_now()` آگاهش می‌کنیم."""
    if dt is None:
        return dt
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


async def active_ban(user_id: str) -> Optional[Ban]:
    """آخرین بنِ معتبرِ کاربر، یا None."""
    if not user_id:
        return None
    try:
        async with AsyncSessionLocal() as db:
            rows = (await db.execute(
                select(Ban)
                .where(Ban.user_id == str(user_id), Ban.lifted_at.is_(None))
                .order_by(Ban.created_at.desc())
                .limit(5)
            )).scalars().all()
            now = _now()
            for b in rows:
                if b.until is None or _aware(b.until) > now:
                    return b
        return None
    except Exception as ex:                     # جدولِ کهنه؟ برنامه را نمی‌بندیم
        log.warning("active_ban failed: %s", ex)
        return None


def seconds_left(ban: Ban) -> float:
    """چند ثانیه تا پایانِ بن؛ دائمی ⇒ بازگشتِ بی‌نهایت (float inf نمی‌خواهیم:
    کلاینت با کلیدِ `permanent` می‌فهمد)."""
    if ban is None:
        return 0.0
    if ban.until is None:
        return -1.0                             # قرارداد: منفی = دائمی
    until = _aware(ban.until)
    return max(0.0, (until - _now()).total_seconds())


def denial_payload(ban: Ban) -> dict:
    """بدنه‌ی خطای 403 برای کاربرِ بنشده — کلیدها، نه جمله."""
    left = seconds_left(ban)
    return {
        "code": "banned",
        "permanent": ban.until is None,
        "seconds_left": int(left) if left > 0 else 0,
        "reason": (ban.reason or "")[:300],
    }


async def add_ban(user_id: str, minutes: Optional[int] = None,
                  reason: str = "", by_tg_id: str = "") -> Ban:
    """کاربر را می‌بندد. `minutes=None` یعنی دائمی. نشست‌هایش هم باطل می‌شوند."""
    from .models import AuthSession

    until = (_now() + timedelta_minutes(minutes)) if minutes else None
    async with AsyncSessionLocal() as db:
        ban = Ban(user_id=str(user_id), until=until, reason=reason[:300],
                  by_tg_id=str(by_tg_id or ""))
        db.add(ban)
        # نشست‌های فعال بی‌درنگ بی‌اعتبار — تا سوکتِ باز کار نکند
        for s in (await db.execute(
                select(AuthSession).where(
                    AuthSession.user_id == str(user_id),
                    AuthSession.revoked == False,   # noqa: E712
                ))).scalars().all():
            s.revoked = True
            db.add(s)
        await db.commit()
        await db.refresh(ban)
        return ban


async def lift_ban(user_id: str) -> int:
    """همه‌ی بن‌های فعال را آزاد می‌کند؛ شمارِ آزادشده‌ها."""
    from .models import utcnow as _utcnow

    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(Ban).where(Ban.user_id == str(user_id),
                              Ban.lifted_at.is_(None))
        )).scalars().all()
        now = _now()
        n = 0
        for b in rows:
            if b.until is None or _aware(b.until) > now:
                b.lifted_at = now
                db.add(b)
                n += 1
        if n:
            await db.commit()
        return n


def timedelta_minutes(m: int):
    from datetime import timedelta
    return timedelta(minutes=int(m))
