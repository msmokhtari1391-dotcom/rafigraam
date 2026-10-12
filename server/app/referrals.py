"""
دعوت‌نامه‌ها — کدِ یک‌بارمصرفِ ربات + کدِ همیشگیِ مدیر.

دو گونه کد داریم:
  • کدِ ربات: رباتِ تلگرام برای هر درخواست یک ردیف در `referral_codes`
    می‌سازد. سرِ ثبت‌نام «سوزانده» می‌شود و دیگر پذیرفته نمی‌شود.
  • کدِ مدیر: در `ADMIN_REFERRAL_CODE` (env) است، ردیفی ندارد و بی‌نهایت‌بار
    کار می‌کند. برای خودت و مهمان‌های ویژه.

سوزاندن با یک `UPDATE ... WHERE used = 0` انجام می‌شود، پس دو ثبت‌نامِ
همزمان با یک کد هم امکان ندارد (هر کدام دیگری را نمی‌بیند، ولی فقط یکی
واقعاً یک ردیف را عوض می‌کند).
"""
import secrets
import time
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .models import ReferralCode

router = APIRouter(prefix="/referral", tags=["referral"])

# حروف و رقم‌های بی‌ابهام (بی 0/O و 1/I) تا خواندن و تایپِ دستی آسان باشد
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
# ۵ حرف — خواسته‌ی کاربر. با این الفبا ۳۳٫۵ میلیون حالت دارد و چون هر کد
# یک‌بارمصرف است و در جدول یکتا بررسی می‌شود، کوتاهیِ آن مشکلی نمی‌سازد.
CODE_LEN = 5


def norm(code) -> str:
    """یکسان‌سازی: بی‌فاصله، بزرگ، بی خط‌تیره — تا «رفیق‌پسند» باشد."""
    return "".join(
        ch for ch in (code or "").strip().upper() if ch.isalnum()
    )[:32]


def is_admin_code(code) -> bool:
    ac = norm(settings.admin_referral_code)
    return bool(ac) and norm(code) == ac


def _gen() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(CODE_LEN))


# ── ساخت ───────────────────────────────────────────
async def create_code(db: AsyncSession, tg_user_id=None,
                      tg_username=None) -> ReferralCode:
    """یک کدِ یک‌بارمصرفِ تازه می‌سازد و ذخیره می‌کند."""
    for _ in range(8):                      # برخوردِ تصادفی؟ دوباره بزن
        code = _gen()
        exists = (await db.execute(
            select(ReferralCode).where(ReferralCode.code == code)
        )).scalar_one_or_none()
        if exists is None:
            break
    else:
        raise HTTPException(500, "ساختِ کد نشد، دوباره تلاش کن")
    row = ReferralCode(
        code=code,
        tg_user_id=(str(tg_user_id) if tg_user_id is not None else None),
        tg_username=(tg_username or None),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def unused_of(db: AsyncSession, tg_user_id) -> list[ReferralCode]:
    """کدهای استفاده‌نشده‌ی یک کاربرِ تلگرام (برای سهمیه و یادآوری)."""
    if tg_user_id is None:
        return []
    rows = (await db.execute(
        select(ReferralCode)
        .where(ReferralCode.tg_user_id == str(tg_user_id))
        .where(ReferralCode.used == False)          # noqa: E712
        .order_by(ReferralCode.created_at)
    )).scalars().all()
    return list(rows)


# ── سقفِ حساب (نسخه‌ی ۱.۵) ──────────────────────────
# پیش‌تر تنها سنجه `REFERRAL_QUOTA` بود: «چند کدِ استفاده‌نشده‌ی همزمان».
# آن سقف در عمل هیچ حدی نمی‌گذاشت — کاربر کد را مصرف می‌کرد، شمارنده صفر
# می‌شد و باز کدِ تازه می‌گرفت؛ بی‌نهایت حساب. سقفِ تازه روی *مصرف‌شده‌ها*
# است، پس واقعاً می‌بندد.
async def accounts_of(db: AsyncSession, tg_user_id) -> int:
    """چند حساب تا حالا با کدهای این کاربرِ تلگرام ساخته شده."""
    if tg_user_id is None:
        return 0
    n = (await db.execute(
        select(func.count())
        .select_from(ReferralCode)
        .where(ReferralCode.tg_user_id == str(tg_user_id))
        .where(ReferralCode.used == True)           # noqa: E712
    )).scalar_one()
    return int(n or 0)


def account_cap() -> int:
    return max(1, int(settings.referral_account_cap or 1))


async def quota_state(db: AsyncSession, tg_user_id) -> dict:
    """وضعیتِ سهمیه‌ی یک کاربر — هم برای ربات، هم برای پیامِ خطا.

    `left` = چند حسابِ دیگر می‌تواند بسازد؛ کدهای دستِ‌نخورده‌ی همین حالا هم
    از سهمیه کم می‌شوند، وگرنه کاربر می‌توانست چهار کد انبار کند، مصرفشان
    کند و باز چهار کدِ دیگر بگیرد.
    """
    used = await accounts_of(db, tg_user_id)
    unused = await unused_of(db, tg_user_id)
    cap = account_cap()
    return {"used": used, "unused": len(unused), "cap": cap,
            "left": max(0, cap - used - len(unused)),
            "codes": [r.code for r in unused]}


async def issue_for(db: AsyncSession, tg_user_id, tg_username=None,
                    exempt: bool = False) -> tuple[str | None, str]:
    """کدِ کاربر را می‌دهد و علتش را هم می‌گوید: `(کد، دلیل)`.

    دلیل‌ها: `new` (تازه ساخته شد) | `existing` (کدِ استفاده‌نشده‌ی قبلی) |
    `capped` (سقفِ حساب پر است، کد نداریم). `exempt=True` برای مدیر است:
    نه سقفِ حساب دارد نه سقفِ همزمان.
    """
    have = await unused_of(db, tg_user_id)
    if exempt:
        if len(have) >= max(1, settings.referral_quota):
            return have[0].code, "existing"
        row = await create_code(db, tg_user_id=tg_user_id,
                                tg_username=tg_username)
        return row.code, "new"
    st = await quota_state(db, tg_user_id)
    if st["left"] <= 0:
        # سهمیه تمام است. اگر کدِ دستِ‌نخورده‌ای مانده همان را برگردان
        # (کدی که خودش قبلاً گرفته را نباید از او دریغ کنیم)، وگرنه هیچ.
        return (have[0].code, "existing") if have else (None, "capped")
    if len(have) >= max(1, settings.referral_quota):
        return have[0].code, "existing"
    row = await create_code(db, tg_user_id=tg_user_id,
                            tg_username=tg_username)
    return row.code, "new"


# ── اعتبارسنجی و سوزاندن ───────────────────────────
async def is_valid(db: AsyncSession, code) -> bool:
    c = norm(code)
    if not c:
        return False
    if is_admin_code(c):
        return True
    row = (await db.execute(
        select(ReferralCode).where(ReferralCode.code == c)
    )).scalar_one_or_none()
    return bool(row and not row.used)


async def burn(db: AsyncSession, code, user_id: Optional[str]) -> bool:
    """کد را مصرف می‌کند. `True` یعنی پذیرفته شد.

    کدِ مدیر همیشه `True` است و مصرف نمی‌شود. کدِ ربات فقط اگر همان لحظه
    استفاده‌نشده باشد سوزانده می‌شود؛ شرطِ `used == False` داخلِ خودِ
    `UPDATE` است تا مسابقه‌ی دو ثبت‌نامِ همزمان پیش نیاید.
    """
    c = norm(code)
    if not c:
        return False
    if is_admin_code(c):
        return True
    res = await db.execute(
        update(ReferralCode)
        .where(ReferralCode.code == c)
        .where(ReferralCode.used == False)          # noqa: E712
        .values(used=True, used_by=user_id,
                used_at=datetime.now(timezone.utc))
    )
    await db.commit()
    return bool(res.rowcount)


# ── اندپوینت‌ها ────────────────────────────────────
@router.get("/info")
async def referral_info():
    """کلاینت با این می‌فهمد کدِ دعوت لازم است و از کجا بگیرد."""
    from . import telegram_bot as bot
    return {
        "required": bool(settings.require_referral),
        "bot": settings.telegram_bot_username,
        "bot_link": (f"https://t.me/{settings.telegram_bot_username}"
                     if settings.telegram_bot_username else ""),
        "code_len": CODE_LEN,
        # «دریافت کد» فقط وقتی به کار می‌آید که ربات روشن باشد و صاحبش را
        # بشناسیم؛ وگرنه کلاینت دکمه را نشان می‌دهد و کاربر بی‌خود می‌زند.
        "can_request": bool(bot.enabled() and bot.owner_id()),
        # مسیرِ خودکار: کاربر نامِ تلگرامش را می‌دهد و کد به تلگرامِ *خودش*
        # می‌رسد. صاحبِ ربات لازم نیست شناخته شده باشد.
        "can_otp": bool(bot.enabled()),
        "account_cap": account_cap(),
        # لینکِ «ارتباط با ما» (تنظیمات → پشتیبانی)
        "support_link": (
            f"https://t.me/{settings.telegram_bot_username}?start=support"
            if settings.telegram_bot_username else ""),
    }



class CheckRequest(BaseModel):
    code: str


@router.post("/check")
async def referral_check(body: CheckRequest):
    """درستیِ کد را بی‌مصرف‌کردنش می‌گوید (برای بازخوردِ فوریِ فرمِ ثبت‌نام)."""
    from .db import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        ok = await is_valid(db, body.code)
    return {"ok": ok, "admin": is_admin_code(body.code)}


class IssueRequest(BaseModel):
    admin_code: str
    count: int = 1


@router.post("/issue")
async def referral_issue(body: IssueRequest):
    """با کدِ مدیر، چند کدِ یک‌بارمصرفِ تازه می‌سازد (بی‌نیاز به تلگرام)."""
    if not is_admin_code(body.admin_code):
        raise HTTPException(403, "کدِ مدیر درست نیست")
    n = max(1, min(20, int(body.count or 1)))
    from .db import AsyncSessionLocal
    out = []
    async with AsyncSessionLocal() as db:
        for _ in range(n):
            row = await create_code(db, tg_user_id=None, tg_username="admin")
            out.append(row.code)
    return {"codes": out}


# ── «دریافت کد» از خودِ برنامه ──────────────────────
# فرمِ ثبت‌نام این را می‌زند: سرور یک کدِ یک‌بارمصرف می‌سازد و همان لحظه به
# تلگرامِ صاحبِ ربات می‌فرستد. کد در پاسخِ HTTP نمی‌آید — وگرنه هر کسی
# می‌توانست بی‌تلگرام برای خودش کد بگیرد و «دعوتی‌بودن» بی‌معنی می‌شد.
_last_req: dict[str, float] = {}
COOLDOWN = 45.0            # ثانیه، برای هر IP
GLOBAL_COOLDOWN = 3.0      # ثانیه، روی همه‌ی درخواست‌ها (ضدِ سیلِ پیام)


def _client_ip(request: Request) -> str:
    """IP واقعیِ کاربر — نه IP پروکسی.

    سرور روی Railway است و دامنه/وب از Vercel رد می‌شود؛ پس
    `request.client.host` آدرسِ خودِ پروکسی است. اگر با همان محدود کنیم،
    همه‌ی کاربران یک صندوقِ ۴۵ ثانیه‌ایِ مشترک می‌گیرند و «دریافت کد» برای
    نفرِ دوم به بعد ۴۲۹ می‌دهد. هدرهای پروکسی مقدم‌اند.
    """
    for h in ("x-forwarded-for", "x-real-ip", "cf-connecting-ip"):
        v = (request.headers.get(h) or "").split(",")[0].strip()
        if v:
            return v
    return (request.client.host if request.client else "?") or "?"


def _throttle(key: str) -> float:
    """۰ = آزاد، وگرنه ثانیه‌های ماندهٔ انتظار."""
    now = time.monotonic()
    for k, ts in list(_last_req.items()):        # پاک‌سازیِ کهنه‌ها
        if now - ts > 3600:
            _last_req.pop(k, None)
    for k, gap in ((key, COOLDOWN), ("*", GLOBAL_COOLDOWN)):
        left = gap - (now - _last_req.get(k, 0.0))
        if left > 0:
            return left
    _last_req[key] = now
    _last_req["*"] = now
    return 0.0


@router.post("/request")
async def referral_request(request: Request):
    """کدِ تازه بساز و به تلگرامِ صاحبِ ربات بفرست."""
    from . import telegram_bot as bot

    if not bot.enabled():
        raise HTTPException(503, "ربات تلگرام تنظیم نشده")
    owner = bot.owner_id()
    if not owner:
        raise HTTPException(
            409, "اول در تلگرام به ربات /start بده تا شناخته شود")
    ip = _client_ip(request)
    left = _throttle(ip)
    if left > 0:
        raise HTTPException(429, f"چند لحظه صبر کن ({int(left) + 1} ثانیه)")

    from .db import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        row = await create_code(db, tg_user_id=owner, tg_username="app")
    sent = await bot.send_code(owner, row.code)
    if not sent:
        raise HTTPException(502, "فرستادن به تلگرام نشد؛ دوباره بزن")
    return {"ok": True, "sent": True, "code_len": CODE_LEN,
            "bot": settings.telegram_bot_username}


# ── مسیرِ خودکار: کد یک‌راست به تلگرامِ خودِ کاربر ───
# فرقش با `/request`: آن‌جا کد به چتِ *مدیر* می‌رفت و مدیر باید دستی
# می‌رساندش. این‌جا کاربر نامِ تلگرامش را می‌دهد و کد به تلگرامِ *خودش*
# می‌رسد. محدودیتِ تلگرام: ربات به کسی که خودش `/start` نزده پیام نمی‌دهد
# (نه با شناسه، نه با نامِ کاربری). پس اگر نشناسیمش، صادقانه می‌گوییم اول
# ربات را شروع کن — نه این‌که ادایِ فرستادن دربیاوریم.
class OtpRequest(BaseModel):
    username: str


def norm_user(name) -> str:
    """`@Ali_92` → `ali_92`. تلگرام به بزرگی/کوچکی حساس نیست."""
    return "".join(
        ch for ch in (name or "").strip().lstrip("@") if ch.isalnum() or ch == "_"
    )[:64].lower()


@router.post("/otp")
async def referral_otp(body: OtpRequest, request: Request):
    """کدِ تازه بساز و به تلگرامِ *خودِ* کاربر بفرست (با سقفِ حساب)."""
    from . import telegram_bot as bot
    from .db import AsyncSessionLocal

    if not bot.enabled():
        raise HTTPException(503, "ربات تلگرام تنظیم نشده")
    uname = norm_user(body.username)
    if len(uname) < 4:
        raise HTTPException(422, "نامِ کاربریِ تلگرام درست نیست")
    left = _throttle(f"otp:{_client_ip(request)}")
    if left > 0:
        raise HTTPException(429, f"چند لحظه صبر کن ({int(left) + 1} ثانیه)")

    async with AsyncSessionLocal() as db:
        tg_id = await bot.resolve_username(db, uname)
        if not tg_id:
            raise HTTPException(
                404,
                "این نامِ کاربری را نمی‌شناسیم. اول در تلگرام به ربات "
                f"@{settings.telegram_bot_username or 'ما'} پیام بده (/start)، "
                "بعد این‌جا دوباره بزن.")
        exempt = bot.is_owner(tg_id)
        st = await quota_state(db, tg_id)
        code, why = await issue_for(db, tg_id, tg_username=uname,
                                   exempt=exempt)
    if not code:
        raise HTTPException(
            403,
            f"سهمیه‌ی حسابت پر شده: {st['used']} حساب از {st['cap']}. "
            "برای بیشتر، از «ارتباط با ما» در تنظیمات پیام بده.")
    if not await bot.send_code(tg_id, code, personal=True):
        raise HTTPException(502, "فرستادن به تلگرام نشد؛ دوباره بزن")
    return {"ok": True, "sent": True, "to": f"@{uname}", "fresh": why == "new",
            "code_len": CODE_LEN, "left": max(0, st["left"] - 1),
            "cap": st["cap"], "bot": settings.telegram_bot_username}

