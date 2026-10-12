"""پلِ اعلانِ بیرونِ برنامه — چیزی که پایتون می‌نویسد و کاتلین می‌خواند.

روی اندروید یک سرویسِ بومی (`PushService.kt`، در قالبِ وصله‌خورده‌ی
`.tmp/tpl`) مستقل از موتورِ فلاتر زنده می‌ماند و خودش به سرور long-poll
می‌زند؛ پس اعلان با برنامه‌ی *بسته* هم می‌آید. آن سرویس هیچ راهی به دنیای
پایتون ندارد — نه MethodChannel، نه پلاگین — جز دو فایلِ کوچک:

    <پوشه‌ی داده>/rafigram/push.json    ← این‌جا می‌نویسیم، کاتلین می‌خواند
    <پوشه‌ی داده>/rafigram/open.json    ← کاتلین می‌نویسد، این‌جا می‌خوانیم

چرا فایل؟ چون ساده‌ترین چیزی است که *همیشه* کار می‌کند: هر دو طرف همان
پوشه را می‌بینند (`FLET_APP_STORAGE_DATA` روی اندروید همان
`filesDir/data` است)، و بی موتورِ فلاتر هم خواندنی است — درست همان حالتی
که برنامه بسته است و MethodChannel وجود ندارد.

`push.json`: توکن، نشانی‌های سرور و سه کلیدِ اعلان/صدا/لرزش. سرویس هر دور
از نو می‌خوانَدش، پس «خروج از حساب» همان لحظه اعلان را می‌بندد و «ورود»
همان لحظه بازش می‌کند — بی ری‌استارتِ سرویس.

`open.json`: کاربر روی اعلان زد ⇒ کاتلین شناسه‌ی چت را می‌نویسد و برنامه
سرِ بالا آمدن (یا برگشت از پس‌زمینه) همان چت را باز می‌کند.

روی ویندوز و وب این فایل‌ها خواننده‌ای ندارند؛ نوشتنشان چند صد بایت است و
شرطِ سکو نمی‌گذاریم — یک راهِ کد برای سه سکو.

⚠️ توکن در این فایل *خام* است. روی اندروید پوشه‌ی خصوصیِ برنامه است و هیچ
برنامه‌ی دیگری نمی‌تواند بخواندش (همان جایی که کشِ چت‌ها هم هست). روی
ویندوز هم در پوشه‌ی کاربر می‌نشیند، مثلِ خودِ نشست در SharedPreferences.
"""
import json
import os
import time

import config

_NAME = "push.json"
_OPEN = "open.json"
_MARK = "native.json"
_TAP_WINDOW = 600.0      # ضربه‌ی کهنه‌تر از ۱۰ دقیقه را باز نمی‌کنیم


def _dir() -> str:
    """همان پوشه‌ای که `main._data_dir()` می‌سازد.

    این‌جا از نو نوشته شده و از `main` وارد نمی‌شود، چون `main` خودش
    `services` را وارد می‌کند و حلقه‌ی import می‌شد.
    """
    android = os.getenv("FLET_APP_STORAGE_DATA")
    if android:
        return os.path.join(android, "rafigram")
    root = (os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA")
            or os.path.join(os.path.expanduser("~"), ".local", "share"))
    return os.path.join(root, "Rafigram")


def available() -> bool:
    """هسته‌ی بومیِ اعلان روی این دستگاه هست؟

    `MainActivity.kt` سرِ هر باز شدن `native.json` را می‌گذارد. یعنی این
    فایل *گواهیِ* APKِ ساخته‌شده با قالبِ وصله‌خورده است؛ نسخه‌ی وب و ویندوز
    و APKِ قدیمی آن را ندارند و تنظیمات همان‌جا صادقانه می‌گوید «فقط وقتی
    برنامه باز است».
    """
    try:
        return os.path.isfile(os.path.join(_dir(), _MARK))
    except Exception:
        return False


def sync(token: str = "", *, notify: bool = True, sound: bool = True,
         vibrate: bool = True) -> bool:
    """`push.json` را از نو می‌نویسد. `False` = نشد (و مهم هم نیست).

    نوشتن *اتمی* است (فایلِ موقت + `replace`): سرویس ممکن است همان لحظه
    بخوانَد و نیمه‌فایل یعنی JSONِ خراب. کاتلین هم استثنا را می‌بلعد و دورِ
    بعد از نو می‌خواند، ولی درست انجامش دادن ارزانی است.
    """
    try:
        d = _dir()
        os.makedirs(d, exist_ok=True)
        blob = {
            "token": str(token or ""),
            "bases": config.http_candidates(),
            "notify": bool(notify),
            "sound": bool(sound),
            "vibrate": bool(vibrate),
            "at": int(time.time()),
        }
        p = os.path.join(d, _NAME)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(blob, f, ensure_ascii=False)
        os.replace(tmp, p)
        return True
    except Exception:
        return False


def sync_from_prefs() -> bool:
    """همان `sync()` ولی با خواندنِ خودکارِ نشست و کلیدها از `prefs`.

    `prefs` را همین‌جا (و نه بالای فایل) وارد می‌کنیم چون خودِ `prefs`
    برای هر تغییرِ تنظیمات این تابع را صدا می‌زند — حلقه‌ی import.
    """
    try:
        import prefs
        return sync(prefs.get("token"), notify=prefs.flag("notify"),
                    sound=prefs.flag("sound"), vibrate=prefs.flag("vibrate"))
    except Exception:
        return False


def take_tap() -> str:
    """شناسه‌ی چتی که کاربر روی اعلانش زد — یک‌بار، و بعد پاک.

    پاک‌کردن لازم است وگرنه هر بار که برنامه از پس‌زمینه برگردد همان چت
    باز می‌شود. ضربه‌های خیلی کهنه هم رد می‌شوند: اگر برنامه سرِ ضربه
    کشته شده باشد و کاربر ساعت‌ها بعد خودش بازش کند، پریدن به آن چت
    گیج‌کننده است.
    """
    p = os.path.join(_dir(), _OPEN)
    try:
        with open(p, "r", encoding="utf-8") as f:
            blob = json.load(f)
    except Exception:
        # نمی‌شود خواند ⇒ فایل را هم *پاک نمی‌کنیم*. شاید کاتلین همین لحظه
        # وسطِ نوشتنش است و نیم‌فایل دستمان آمده؛ پاک‌کردنش یعنی گم‌شدنِ
        # ضربه‌ی کاربر. اگر واقعاً خراب باشد، ضربه‌ی بعدی رویش می‌نویسد.
        return ""
    try:
        os.remove(p)
    except OSError:
        pass
    try:
        chat = str(blob.get("chat_id") or "")
        at = float(blob.get("at") or 0) / 1000.0     # کاتلین میلی‌ثانیه می‌دهد
    except Exception:
        return ""
    if not chat:
        return ""
    if at and abs(time.time() - at) > _TAP_WINDOW:
        return ""
    return chat


# ── نسخه‌ی وب: ضربه از راهِ نشانیِ صفحه ─────────────
# در مرورگر فایلی در میان نیست. ورکرِ اعلان (`rafigram-push-sw.js`) سرِ ضربه
# `?chat=<id>` را در نشانی می‌گذارد و صفحه با همان بالا می‌آید؛ فلت خودش
# نشانیِ صفحه را به پایتون می‌دهد، پس همان‌جا می‌خوانیمش.
#
# چرا فقط یک‌بار: نشانی تا پایانِ نشست عوض نمی‌شود، پس بی این پرچم هر بار که
# برنامه از پس‌زمینه برمی‌گشت همان چت از نو باز می‌شد.
_url_used = False


def take_url_tap(url: str) -> str:
    """`?chat=<id>` از نشانیِ صفحه — یک‌بار در هر اجرا."""
    global _url_used
    if _url_used:
        return ""
    _url_used = True
    src = (url or "")
    if "chat=" not in src:
        return ""
    try:
        from urllib.parse import parse_qs, urlparse
        q = parse_qs(urlparse(src).query)
        return str((q.get("chat") or [""])[0] or "").strip()
    except Exception:
        return ""
