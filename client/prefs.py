"""
تنظیمات ماندگارِ کلاینت (زبان، پوسته، اندازه‌ی متن، اعلان‌ها).

در Flet 0.86 چیزی به اسم `page.client_storage` نداریم؛ سرویسِ
`ft.SharedPreferences` جایگزینشه. مقدارها در حافظه هم کش می‌شن تا
خوندنشون در ساختِ UI همگام (بدون await) باشه.
"""
import asyncio
import json

import flet as ft

DEFAULTS = {
    "lang": "fa",
    "theme": "telegram",
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
    "dark_theme": "telegram",
    # پوسته‌ی دلخواه: پایه + سه رنگِ اصلی
    "c_base": "telegram",
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
    # سرعتِ انیمیشن‌ها: full = کامل، reduced = کاهش‌یافته، off = خاموش
    "anim_level": "full",
    # قفلِ محلی برنامه (PIN) و زمانِ قفل خودکار (برحسب دقیقه، 0 = غیرفعال)
    "app_lock_pin": "",
    "app_lock_timeout": "0",
    # دانلود خودکار رسانه‌ها در وای‌فای و دیتا
    "dl_wifi": "1",
    "dl_cellular": "1",
    # نمایش تب‌های پوشه در بالای لیست چت‌ها
    "show_folders": "0",
    # اینتر = ارسالِ پیام (رومیزی). خاموش ⇒ اینتر خطِ تازه (موبایل همیشه
    # دکمه‌ی ارسال دارد و اینترش خطِ تازه است).
    "send_enter": "1",
    # آدرسِ سرور (خالی = همان پیش‌فرضِ Railway در config.py)
    "server": "",
    # ── نشستِ ماندگار: تا «خروج از حساب» نزنی، لاگین می‌مانی ──
    "token": "",
    "user_id": "",
    "username": "",
    "display_name": "",
    "avatar_url": "",
    "saved_accounts": "[]",
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
_AUTH_KEYS = ("token", "user_id", "username", "display_name", "avatar_url")


def session() -> dict[str, str]:
    """نشستِ ذخیره‌شده؛ بی‌توکن یعنی «هنوز وارد نشده»."""
    return {k: get(k) for k in _AUTH_KEYS}


def get_accounts() -> list[dict]:
    """فهرست حساب‌های ذخیره‌شده روی این دستگاه (Multi-account تلگرام)."""
    raw = get("saved_accounts", "[]")
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return data
    except Exception:
        pass
    return []


async def save_account_to_list(acc: dict) -> None:
    """حساب را در لیست حساب‌های ذخیره‌شده ثبت یا به‌روز می‌کند."""
    uid = str(acc.get("user_id") or "")
    if not uid or not acc.get("token"):
        return
    lst = get_accounts()
    found = False
    for i, item in enumerate(lst):
        if str(item.get("user_id")) == uid or (item.get("username") and item.get("username") == acc.get("username")):
            lst[i] = {
                "token": acc.get("token") or item.get("token"),
                "user_id": uid,
                "username": acc.get("username") or item.get("username", ""),
                "display_name": acc.get("display_name") or item.get("display_name", ""),
                "avatar_url": acc.get("avatar_url") or item.get("avatar_url", "")
            }
            found = True
            break
    if not found:
        lst.append({
            "token": acc.get("token", ""),
            "user_id": uid,
            "username": acc.get("username", ""),
            "display_name": acc.get("display_name", ""),
            "avatar_url": acc.get("avatar_url", "")
        })
    await put("saved_accounts", json.dumps(lst, ensure_ascii=False))


async def remove_account_from_list(uid: str) -> None:
    lst = [a for a in get_accounts() if str(a.get("user_id")) != str(uid)]
    await put("saved_accounts", json.dumps(lst, ensure_ascii=False))


async def save_session(data: dict) -> None:
    """توکن و مشخصاتِ کاربر را ماندگار می‌کند (بعد از ورود/ثبت‌نام)."""
    for k in _AUTH_KEYS:
        await put(k, data.get(k) or "")
    await save_account_to_list(data)


async def clear_session() -> None:
    """خروج از حساب جاری؛ اکانت از لیست مولتی‌اکانت هم پاک می‌شود."""
    uid = get("user_id")
    if uid:
        await remove_account_from_list(uid)
    for k in _AUTH_KEYS:
        await put(k, "")
