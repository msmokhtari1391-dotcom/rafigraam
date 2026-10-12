"""
پاک‌سازیِ داده‌های آزمایشی — یک‌جا، شمرده، و با تأییدِ دوباره.

چرا اینجا و نه دستی روی سرور؟ دیتابیس یک فایلِ SQLite روی volumeِ ریلوی است
و از بیرون دستِ کسی به آن نمی‌رسد. پس خودِ برنامه باید یک دکمه‌ی «ریست»
داشته باشد — ولی دکمه‌ای که تصادفی زده نشود:

  • فقط صاحبِ ربات (`telegram_bot.is_owner`) می‌تواند صدایش کند،
  • دو گام است: گام اول یک نشانه‌ی تصادفیِ ۶ حرفی می‌دهد که ۲ دقیقه زنده
    است، و بی همان نشانه هیچ سطری پاک نمی‌شود،
  • پیش از پاک‌کردن می‌شمارد و شمارش را برمی‌گرداند، تا معلوم باشد چه رفت.

`wipe()` همه‌ی جدول‌ها را خالی می‌کند (کاربر، چت، پیام، استوری، نشست‌ها،
تیکت‌های پشتیبانی و کدهای دعوت) و فایل‌های مدیا را هم برمی‌دارد. کدِ مدیر از
`SECRET_KEY` درمی‌آید و در دیتابیس نیست، پس بعدِ ریست هم همان است و می‌شود
دوباره حساب ساخت. تنها جدولی که می‌ماند `tg_users` است؛ چرایش سرِ `KEEP`.
"""
from __future__ import annotations

import logging
import secrets
import shutil
import time

from sqlalchemy import delete, func, select

from .config import settings
from .models import (AuthSession, Block, Chat, ChatMember, Message,
                     MessageHide, MessageReaction, ReferralCode, SavedGif,
                     Story, StoryView, SupportTicket, TgUser, User, UserPhoto)

log = logging.getLogger("rafigram.maintenance")

# ترتیب مهم است: فرزندها پیش از پدرها، تا کلیدِ بیگانه (اگر روزی روشن شد)
# سرِ راه نگذارد.
TABLES = (StoryView, Story, MessageReaction, MessageHide, SavedGif, Message,
          ChatMember, Chat, Block, AuthSession, UserPhoto, SupportTicket,
          ReferralCode, User)

# شمرده می‌شوند ولی پاک نه. `tg_users` داده‌ی برنامه نیست، دفترچه‌ی خودِ
# ربات است: نگاشتِ «نامِ کاربریِ تلگرام → شناسه» که تنها راهِ پرشدنش این است
# که کاربر *خودش* به ربات پیام بدهد. پاک‌کردنش «کدِ خودکار» را برای همه از
# کار می‌انداخت تا وقتی یکی‌یکی دوباره ربات را شروع کنند — بهایی که ریستِ
# داده‌ی آزمایشی نباید داشته باشد.
KEEP = (TgUser,)

_TOKEN_TTL = 120.0                  # ثانیه — نشانه‌ی تأیید زودگذر است
_pending: dict[str, tuple[str, float]] = {}


def arm(who: str) -> str:
    """گامِ اول: نشانه‌ی تأیید می‌سازد و برمی‌گرداند."""
    tok = secrets.token_hex(3).upper()
    _pending[str(who)] = (tok, time.time() + _TOKEN_TTL)
    return tok


def check(who: str, token: str) -> bool:
    """گامِ دوم: نشانه درست و نامنقضی است؟ (یک‌بارمصرف)"""
    got = _pending.get(str(who))
    if not got:
        return False
    tok, exp = got
    if time.time() > exp:
        _pending.pop(str(who), None)
        return False
    if not secrets.compare_digest(tok, (token or "").strip().upper()):
        return False
    _pending.pop(str(who), None)
    return True


async def counts(db) -> dict[str, int]:
    """چند سطر در هر جدول هست — برای نشان‌دادن پیش از پاک‌کردن.

    جدول‌های `KEEP` هم می‌آیند، با نشانِ «می‌ماند»، تا معلوم باشد چه چیزی
    قرار *نیست* برود.
    """
    out: dict[str, int] = {}
    for model in TABLES + KEEP:
        try:
            n = (await db.execute(
                select(func.count()).select_from(model))).scalar_one()
        except Exception as ex:                 # جدولِ نبوده؟ صفر
            log.info("count %s failed: %s", model.__tablename__, ex)
            n = 0
        if n:
            key = model.__tablename__
            out[f"{key} (می‌ماند)" if model in KEEP else key] = int(n)
    return out


async def wipe(db, media: bool = True) -> dict[str, int]:
    """همه‌ی جدول‌ها را خالی می‌کند و شمارشِ پیش از پاک‌شدن را برمی‌گرداند."""
    before = await counts(db)
    for model in TABLES:
        try:
            await db.execute(delete(model))
        except Exception as ex:
            log.warning("wipe %s failed: %s", model.__tablename__, ex)
    await db.commit()
    n_files = 0
    if media:
        for p in settings.media_dir.iterdir() if settings.media_dir.is_dir() \
                else []:
            try:
                if p.is_dir():
                    shutil.rmtree(p, ignore_errors=True)
                else:
                    p.unlink()
                n_files += 1
            except OSError as ex:
                log.info("media %s نرفت: %s", p.name, ex)
        if n_files:
            before["media_files"] = n_files
    log.warning("database wiped: %s", before)
    return before
