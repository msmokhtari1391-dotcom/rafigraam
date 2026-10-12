"""
تنظیمات ماندگارِ کلاینت (زبان، پوسته، اندازه‌ی متن، اعلان‌ها).

در Flet 0.86 چیزی به اسم `page.client_storage` نداریم؛ سرویسِ
`ft.SharedPreferences` جایگزینشه. مقدارها در حافظه هم کش می‌شن تا
خوندنشون در ساختِ UI همگام (بدون await) باشه.
"""
import asyncio

import flet as ft

DEFAULTS = {
    "lang": "fa",
    "theme": "crimson",
    "font_scale": "1.0",
    "sound": "1",
    "badges": "1",
    # لرزشِ کوتاه سرِ اعلان — روی گوشی. رومیزی خودش لرزشی ندارد و این کلید
    # آن‌جا بی‌اثر است، پس پیش‌فرض روشن می‌ماند.
    "vibrate": "1",
    # اعلانِ سیستمی برای پیامِ تازه (توستِ ویندوز / اعلانِ مرورگر / نوارِ
    # درون‌برنامه‌ای). خاموشش کنی، هیچ‌کدام نمی‌آید.
    "notify": "1",
    # آخرین پوسته‌ی تاریک — کلیدِ ماه/خورشید به همین برمی‌گرده
    "dark_theme": "crimson",
    # پوسته‌ی دلخواه: پایه + سه رنگِ اصلی
    "c_base": "crimson",
    "c_acc": "",
    "c_sent": "",
    "c_bg": "",
    # مدلِ آماده‌ای که در سازنده‌ی تم انتخاب شده
    "c_preset": "",
    # کاغذدیواریِ صفحه‌ی چت (آدرسِ /media/... ) + تیرگی و تاریِ آن
    "chat_bg": "",
    "chat_bg_dim": "0.35",
    "chat_bg_blur": "0",
    # کاشیِ نقش‌دارِ پس‌زمینه‌ی چت
    "pattern": "1",
    # رمزنگاریِ محلیِ متنِ پیام‌ها + کلیدِ آن
    "enc": "0",
    "enc_key": "",
    # ایموجی‌های تازه‌استفاده‌شده (با فاصله جدا می‌شن)
    "emoji_recent": "",
    # شدتِ شیشه‌ایِ منوها و نوارها: 0 = تخت، 1 = پیش‌فرض، 1.6 = پرشیشه
    "glass": "1.0",
    # آدرسِ سرور (خالی = همان پیش‌فرضِ Railway در config.py)
    "server": "",
    # ── نشستِ ماندگار: تا «خروج از حساب» نزنی، لاگین می‌مانی ──
    "token": "",
    "user_id": "",
    "username": "",
    "display_name": "",
}

_prefs: ft.SharedPreferences | None = None
_cache: dict[str, str] = dict(DEFAULTS)


def attach(prefs: ft.SharedPreferences) -> None:
    global _prefs
    _prefs = prefs


async def load(timeout: float = 10.0) -> dict[str, str]:
    """همه‌ی کلیدها را *همزمان* می‌خواند و کش را پر می‌کند.

    پیش‌تر یک حلقه‌ی `await` پشتِ‌سرِهم بود: ۴۰ کلید × یک رفت‌وبرگشت با
    لایه‌ی Flutter. روی مرورگرِ گوشی هر رفت‌وبرگشت گاهی چند دهم ثانیه است،
    پس پیش از دیدنِ اولین قاب چند ثانیه تلف می‌شد و برنامه «باز نمی‌شد».
    `asyncio.wait` همه را یک‌جا می‌فرستد و مهلت هم می‌گذارد: اگر پلی وا رفت،
    هر چه رسیده نگه داشته می‌شود و بقیه پیش‌فرض می‌گیرند — هرگز بی‌پایان
    منتظر نمی‌مانیم.
    """
    if _prefs is None:
        return _cache
    tasks = {}
    for k in DEFAULTS:
        tasks[asyncio.ensure_future(_prefs.get(f"rafigram.{k}"))] = k
    done, pending = await asyncio.wait(list(tasks), timeout=timeout)
    for task in pending:
        task.cancel()
    for task in done:
        k = tasks[task]
        try:
            v = task.result()
        except Exception:
            v = None
        _cache[k] = str(v) if v not in (None, "") else DEFAULTS[k]
    for task in pending:
        _cache.setdefault(tasks[task], DEFAULTS[tasks[task]])
    return _cache


def get(key: str, default: str | None = None) -> str:
    return _cache.get(key, default if default is not None else DEFAULTS.get(key, ""))


def flag(key: str) -> bool:
    return get(key) not in ("0", "false", "False", "")


def scale() -> float:
    try:
        return max(0.85, min(1.35, float(get("font_scale"))))
    except ValueError:
        return 1.0


def glass() -> float:
    """شدتِ شیشه‌ایِ منوها: ۰ تا ۱٫۶ (پیش‌فرض ۱)."""
    try:
        return max(0.0, min(1.6, float(get("glass"))))
    except ValueError:
        return 1.0


# ── همگام‌سازی با سرویسِ اعلانِ بومی ─────────────────
# سرویسِ بومیِ اندروید (که با برنامه‌ی بسته هم اعلان می‌آورد) از دنیای پایتون
# فقط یک فایل می‌بیند: `push.json`. این پنج کلید همه‌ی چیزی‌ست که در آن فایل
# می‌رود، پس هر بار یکی‌شان عوض شود فایل از نو نوشته می‌شود — این‌جا، در
# تنها دروازه‌ی نوشتنِ تنظیمات، تا هیچ مسیری از قلم نیفتد.
_PUSH_KEYS = ("token", "notify", "sound", "vibrate", "server")


def _push_sync() -> None:
    # وارد کردنِ تنبل: `services.push` خودش `prefs` را می‌خواند
    try:
        from services import push as _p
        _p.sync_from_prefs()
    except Exception:
        pass


async def put(key: str, value) -> None:
    """کش رو فوری به‌روز می‌کنه و در پس‌زمینه روی دیسک می‌نویسه."""
    _cache[key] = str(value)
    if key in _PUSH_KEYS:
        _push_sync()
    if _prefs is not None:
        try:
            await _prefs.set(f"rafigram.{key}", str(value))
        except Exception:
            pass


# ── کلیدهای آزاد (انبارِ محلیِ چت‌ها و پیام‌ها) ─────────
# اینها در `DEFAULTS` نیستند و در `load()` هم خوانده نمی‌شوند: حجمشان زیاد
# است و فقط وقتی لازم می‌شوند خوانده می‌شوند (وگرنه شروعِ برنامه کند می‌شد).
async def raw_get(key: str) -> str:
    if _prefs is None:
        return ""
    try:
        v = await _prefs.get(f"rafigram.{key}")
    except Exception:
        return ""
    return str(v) if v not in (None, "") else ""


async def raw_put(key: str, value: str) -> None:
    if _prefs is None:
        return
    try:
        await _prefs.set(f"rafigram.{key}", str(value or ""))
    except Exception:
        pass


# ── نشستِ ماندگار (ورودِ خودکار) ─────────────────────
_AUTH_KEYS = ("token", "user_id", "username", "display_name")


def session() -> dict[str, str]:
    """نشستِ ذخیره‌شده؛ بی‌توکن یعنی «هنوز وارد نشده»."""
    return {k: get(k) for k in _AUTH_KEYS}


async def save_session(data: dict) -> None:
    """توکن و مشخصاتِ کاربر را ماندگار می‌کند (بعد از ورود/ثبت‌نام)."""
    for k in _AUTH_KEYS:
        await put(k, data.get(k) or "")


async def clear_session() -> None:
    """فقط با «خروج از حساب» یا توکنِ باطل‌شده صدا می‌خورد."""
    for k in _AUTH_KEYS:
        await put(k, "")
