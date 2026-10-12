"""Rafigram — مسنجر خصوصی رفقا (Flet 0.86)"""
import asyncio
import hashlib
import inspect
# ⚠️ `json` و `logging` تا نسخه‌ی ۱.۵ وارد نشده بودند، در حالی که `_offline_save`
# و `_offline_load` از `json` استفاده می‌کردند. هر دو تابع بدنه‌شان در
# `except Exception` بود، پس `NameError` بی‌صدا خورده می‌شد و کشِ آفلاین
# *هرگز* کار نکرده بود — نه چیزی نوشته می‌شد نه خوانده. علتِ نیمی از
# «وابستگیِ رابط به شبکه» همین یک خطِ نبوده بود.
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

import flet as ft

import config
import crypto
import editor
import emoji_data as ED
import geo
import i18n
import prefs
import store
import ui
import updater
from i18n import t
from services import notify, push
from services.api import api
from services.ws_client import ws_client
from ui import AL, BR, EASE, FAST, MED, MG, OFF, P, PAD, SLOW, W, anim, fs, op

try:  # پلاگین‌های صدا — اگه نصب نباشن، برنامه بدون صدا کار می‌کنه
    import flet_audio as fa
except Exception:  # pragma: no cover
    fa = None
try:
    import flet_audio_recorder as far
except Exception:  # pragma: no cover
    far = None
try:  # موقعیتِ جغرافیایی — روی دسکتاپ/وب ممکن است نباشد
    import flet_geolocator as fgl
except Exception:  # pragma: no cover
    fgl = None
try:  # مجوزهای اندروید (گالری، آهنگ، موقعیت) — روی دسکتاپ بی‌کار است
    import flet_permission_handler as fph
except Exception:  # pragma: no cover
    fph = None

APP_VERSION = "1.6"

log = logging.getLogger("rafigram.client")

# قلمِ وزیرمتن — فلاتر فقط ttf/otf می‌خونه (woff2 لود نمی‌شه).
# قلم‌ها *درونِ برنامه* هستند (client/assets/fonts) نه روی CDN: کاربرانِ ایران
# ممکن است jsdelivr را نداشته باشند و آن‌وقت کلِ متن با قلمِ پیش‌فرضِ فلاتر
# نوشته می‌شود — که فارسی را زشت و ناهم‌تراز نشان می‌دهد. مسیرِ نسبی مثلِ
# آیکون‌ها از `assets_dir` حل می‌شود، پس در EXE و APK و وب هر سه کار می‌کند.
# سرچشمه‌ی همین سه فایل، اگر روزی خواستی تازه‌شان کنی:
#   https://cdn.jsdelivr.net/gh/rastikerdar/vazirmatn@v33.003/fonts/ttf/
FONTS = {
    ui.FONT: "fonts/Vazirmatn-Regular.ttf",
    ui.FONT_MED: "fonts/Vazirmatn-Medium.ttf",
    ui.FONT_BOLD: "fonts/Vazirmatn-Bold.ttf",
}

_picker = None    # سرویس FilePicker (در main ساخته می‌شه)
_last_pick = {"path": None}   # مسیرِ محلیِ آخرین فایلِ انتخاب‌شده (پیش‌نمایش)
_recorder = None  # سرویس AudioRecorder
_rec_ext = "m4a"  # پسوندِ فایلِ ضبط، هم‌خوانِ رمزگذارِ انتخاب‌شده
# سنجشِ رمزگذارِ صدا: «آغاز شد» و «تمام شد» را جدا نگه می‌داریم. پیش‌تر یک
# پرچمِ `done` بود که *پیش از* حلقه‌ی سنجش ست می‌شد؛ پس اگر کاربر وسطِ سنجشِ
# آغازین میکروفون را می‌زد، `_start_rec` فوری از آن رد می‌شد و ضبط با
# رمزگذارِ نسنجیده آغاز می‌شد — همان چیزی که روی بعضی گوشی‌ها برنامه را
# می‌بست. حالا دومین فراخوان *منتظرِ* اولی می‌ماند.
_enc = {"started": False, "plan": []}
_enc_done = asyncio.Event()
_clip = None      # سرویس Clipboard
_geo = None       # سرویس Geolocator (موقعیتِ جغرافیایی)
_perm = None      # سرویس PermissionHandler (مجوزهای اندروید)
_url = None       # سرویس UrlLauncher (بازکردنِ پیوند بیرونِ برنامه)
# مرورگر یا برنامه‌ی نصب‌شده؟ ضبطِ صدا و فایلِ موقت در مرورگر قانونِ دیگری
# دارند (مرورگر مسیرِ فایل ندارد؛ خروجیِ ضبط یک Blob URL است).
_web = {"on": False}
# «ضبط را همین حالا رها کن» — صفحه‌ی چت پرش می‌کند، چرخه‌ی عمرِ برنامه
# صدایش می‌زند. اندروید میکروفونِ پروسه‌ی پس‌زمینه را می‌گیرد و ضبط‌کننده‌ی
# رهانشده سرِ برگشت، پروسه را می‌کشد.
_rec_abort = {"fn": None}

# ── نشانیِ دانلودِ مستقیمِ برنامه ─────────────────────────────────────────
# `tools/deploy.py` هر دو فایل را کنارِ خودِ سایت می‌گذارد (`dist/web/dist/`)
# و در `web/vercel.json` بررسیِ «فایل هست؟» *پیش* از بازنویسیِ همه‌چیز به
# کارگرِ کلادفلر می‌آید ⇒ این دو مسیر ایستا سرو می‌شوند، نه به سرور بروند.
# ریشه باید همان `config.DOWNLOAD_BASE` باشد تا اگر دامنه عوض شد، هم
# به‌روزرسانیِ درون‌برنامه‌ای و هم این دو با هم جابه‌جا شوند.
_DL_BASE = (getattr(config, "DOWNLOAD_BASE", "") or "").rstrip("/")
DL_APK = f"{_DL_BASE}/dist/Rafigram.apk" if _DL_BASE else ""
DL_EXE = f"{_DL_BASE}/dist/Rafigram.exe" if _DL_BASE else ""

EMOJIS = ["😂", "❤️", "🔥", "👍", "😍", "😭", "🙏", "😅", "🤣", "😊",
          "😎", "🥲", "😱", "🤔", "😴", "🤝", "💪", "👌", "✌️", "🫡",
          "🎉", "💯", "✨", "⚡", "🌹", "☕", "🍕", "⚽", "🎵", "💀"]

# ── ابزارهای کوچک ──────────────────────────────────
def _asset(*parts):
    """مسیرِ واقعیِ یک فایلِ assets — هم در اجرای معمولی، هم داخلِ exe."""
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(
        os.path.abspath(__file__))
    return os.path.join(base, "assets", *parts)


def _win_icon():
    """آیکونِ پنجره: ویندوز فقط .ico را قبول می‌کند (png بی‌صداً نادیده می‌ره).

    مسیرِ مطلق می‌دهیم تا در exه‌ی بسته‌بندی‌شده هم پیدا شود؛ اگر نبود،
    نامِ نسبیِ assets را برمی‌گردانیم.
    """
    for name in ("icon.ico", "logo.ico", "icon.png"):
        p = _asset(name)
        if os.path.isfile(p):
            return p
    return "icon.ico"


def _window_setup(page, phone: bool) -> bool:
    """اندازه و آیکونِ پنجره — فقط روی رومیزی.

    اندروید/آی‌اواس پنجره‌ای ندارند؛ ست‌کردنِ `page.window.*` روی گوشی
    بی‌اثر یا مخرّبِ چیدمان است (تصویر جا‌به‌جا می‌شود)، پس رد می‌شود.
    """
    if phone:
        return False
    page.window.width = 460
    page.window.height = 860
    page.window.min_width = 380
    page.window.min_height = 560
    try:                                   # آیکونِ پنجره (هواپیمای رفیگرام)
        page.window.icon = _win_icon()
    except Exception:
        pass
    _window_focus(page)
    return True


# رویدادهایی که یعنی «پنجره دیگر جلوی چشم نیست» و برعکسش
_WIN_OUT = ("BLUR", "HIDE", "MINIMIZE")
_WIN_IN = ("FOCUS", "SHOW", "RESTORE")


def _window_focus(page) -> None:
    """تمرکزِ پنجره ⇒ خبر به `notify` — همان که «هیچ نوتیفی نمی‌آمد».

    ⚠️ ریشه‌ی باگِ اعلان روی نسخه‌ی رومیزی. `notify._on_ws` توستِ ویندوز را
    *تنها* وقتی می‌فرستد که برنامه جلوی چشم نباشد، و آن پرچم را فقط
    `page.on_app_lifecycle_state_change` عوض می‌کرد. روی ویندوز آن رویداد
    برای «رفتن روی برنامه‌ی دیگر» نمی‌آید — پنجره باز است، فقط تمرکز ندارد —
    پس پرچم همیشه True می‌ماند و هیچ توستی فرستاده نمی‌شد؛ تنها نوارِ
    درون‌برنامه‌ای می‌نشست، پشتِ پنجره‌ی برنامه‌ی دیگر، نادیده.

    `WindowEventType.BLUR/FOCUS` از خودِ لایه‌ی پنجره می‌آید و همین را حل
    می‌کند. مقایسه با *متن* است نه با عضوِ Enum، تا اگر نسخه‌ی دیگری از Flet
    رشته‌ی خام بدهد هم کار کند.
    """
    def _ev(e=None):
        raw = getattr(e, "type", None)
        s = str(getattr(raw, "name", raw) or "").upper().replace("-", "_")
        if s in _WIN_OUT:
            notify.set_foreground(False)
        elif s in _WIN_IN:
            notify.set_foreground(True)

    try:
        page.window.on_event = _ev
    except Exception:
        pass


def _kcount(n) -> str:
    """۱۲۳۴ → «۱.۲ هزار» / 1.2k — مثلِ شمارِ مشترکِ تلگرام."""
    try:
        n = int(n or 0)
    except Exception:
        n = 0
    if n < 1000:
        return i18n.num(n)
    unit, div = (t("k_suffix"), 1000.0)
    if n >= 1_000_000:
        unit, div = t("m_suffix"), 1_000_000.0
    s = f"{n / div:.1f}".rstrip("0").rstrip(".")
    return f"{i18n.num(s)}{unit}"


async def _copy_text(page, text):
    """کپی در کلیپ‌بورد — در Flet 0.86 سرویسِ Clipboard و async است."""
    try:
        if _clip is not None:
            await _clip.set(text)
            return True
    except Exception:
        pass
    try:
        await page.clipboard.set(text)
        return True
    except Exception:
        return False


def _focus(page, ctrl):
    """focus() در Flet 0.86 کوروتین است؛ بدونِ await هیچ اتفاقی نمی‌افتد."""
    if ctrl is None:
        return
    try:
        res = ctrl.focus()
        if inspect.isawaitable(res):
            page.run_task(_await_quiet, res)
    except Exception:
        pass


async def _await_quiet(aw):
    """یک awaitable را بی‌صدا اجرا می‌کند (خطاهای UI نباید برنامه را بترکاند)."""
    try:
        await aw
    except Exception:
        pass


def _media(url):
    """آدرس نسبی /media/... رو کامل می‌کنه تا ft.Image بتونه لودش کنه."""
    if not url:
        return None
    if url.startswith(("http://", "https://")):
        return url
    return f"{config.api_base()}/{url.lstrip('/')}"


_HEX32 = re.compile(r"^[0-9a-f]{32}$")


def _fname(url):
    """نامِ خواندنی برای فایل — نامِ تصادفیِ سرور می‌شه «فایل PDF»."""
    base = os.path.basename((url or "").split("?")[0])
    stem, ext = os.path.splitext(base)
    if _HEX32.match(stem.lower()):
        ext = ext.lstrip(".").upper()
        return f"{t('file')} {ext}" if ext else t("file")
    return base or t("file")


def _fname_raw(url):
    """نامِ فایل برای نوشتن روی دیسک — همیشه با پسوندِ درست."""
    base = os.path.basename((url or "").split("?")[0]) or "file.bin"
    stem, ext = os.path.splitext(base)
    if _HEX32.match(stem.lower()):
        return f"rafigram-{stem[:8]}{ext or '.bin'}"
    return base


def _pictures_dir():
    for p in (os.path.join(os.path.expanduser("~"), "Pictures"),
              os.path.join(os.path.expanduser("~"), "Downloads")):
        if os.path.isdir(p):
            return p
    return os.path.expanduser("~")


def _downloads_dir():
    """پوشه‌ی ذخیره‌ی رفیگرام — «ذخیره در دانلود» اینجا می‌نویسه.

    یک پیاده‌سازیِ واحد در `updater.downloads_dir()` است، چون هم مدیای دانلودی
    و هم فایلِ به‌روزرسانی باید یک‌جا بنشینند (`…/Download/Rafigram`) و
    منطقِ «کدام پوشه روی این اندروید نوشتنی است» نباید دو جا تکرار شود.
    نسخه‌ی قدیمیِ این تابع فقط `~/Downloads` را می‌شناخت که روی اندروید
    مسیرِ بی‌معنایی است.
    """
    try:
        return updater.downloads_dir()
    except Exception:
        p = os.path.join(os.path.expanduser("~"), "Downloads")
        try:
            os.makedirs(p, exist_ok=True)
            return p
        except OSError:
            return os.path.expanduser("~")


# مجوزهایی که در *این اجرا* یک‌بار پرسیده شده‌اند. بعضی مجوزها (مثلِ «دسترسی به
# همه‌ی فایل‌ها») پنجره‌ی ساده نیستند و کاربر را به صفحه‌ی تنظیمات می‌برند؛
# پرسیدنِ دوباره‌شان در هر ذخیره، آزار است.
_perm_asked: dict[str, bool] = {}


# ── کشِ آفلاین ──────────────────────────────────────
# خواسته‌ی کاربر: بی اینترنت هم تاریخچه دیده شود (مثل تلگرام). ساده‌ترین
# لایه‌ی بادوام که روی هر سه پلتفرم (ویندوز/اندروید/وب) یکسان کار می‌کند:
# JSON در پوشه‌ی داده‌ی برنامه. «آخرین لیستِ خوب» را نگه می‌داریم؛ سرور که
# جواب داد بازنویسی می‌شود.
_offline_dir_ready = False


def _data_dir() -> str:
    """پوشه‌ی داده‌ی *پایدارِ* برنامه — کشِ آفلاین و ردِ پاها اینجا می‌نشینند.

    ⚠️ این تابع تا نسخه‌ی ۱.۵ وجود نداشت ولی `_offline_dir()` صدایش می‌زد؛
    نتیجه `NameError` بود که در `except Exception` گم می‌شد و کشِ آفلاین را
    کاملاً خاموش می‌کرد. حالا هست.

    عمداً *پوشه‌ی موقت نیست* (`_scratch_dir`): سیستم پوشه‌ی موقت را هر وقت
    خواست جارو می‌کند و آن‌وقت «آخرین لیستِ خوب» و ردِ پای حالتِ ایمن با آن
    می‌رفت. روی اندروید `FLET_APP_STORAGE_DATA` جای درست است و تا حذفِ
    برنامه می‌ماند.
    """
    android = os.getenv("FLET_APP_STORAGE_DATA")
    if android:
        return os.path.join(android, "rafigram")
    base = (os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA")
            or os.path.join(os.path.expanduser("~"), ".local", "share"))
    return os.path.join(base, "Rafigram")


def _offline_dir():
    global _offline_dir_ready
    d = _data_dir()
    if not _offline_dir_ready:
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            pass
        _offline_dir_ready = True
    return d

def _offline_path(name):
    return os.path.join(_offline_dir(), f"offline_{name}.json")

def _offline_save(name, data):
    try:
        tmp = _offline_path(name) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, _offline_path(name))     # نوشتنِ اتمی
    except Exception:
        pass                                     # کش، اختیاری است

def _offline_load(name):
    try:
        with open(_offline_path(name), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


# ── حالتِ ایمن: ردِ پا پیش از هر تماسِ نیتیوِ خطرناک ────────────────
#
# چرا لازم است: بعضی شکست‌ها در لایه‌ی جاوا/نیتیوِ اندروید پروسه را *می‌کشند* و
# هیچ استثنایی به پایتون نمی‌رسد — ضبطِ صدا با `MediaRecorder` و کمبودِ حافظه‌ی
# رمزگشای عکس دو نمونه‌ی خودمان‌اند. پس با try/except نمی‌شود جلویشان را گرفت،
# و بی گوشی و logcat هم نمی‌شود فهمید کدام‌شان بود.
#
# کاری که می‌شود کرد: پیش از تماسِ خطرناک یک ردِ پا روی دیسک بگذاریم و بعدِ
# سالم برگشتن برش داریم. اگر در *اجرای بعدی* ردِ پا سرِ جایش بود، یعنی همان‌جا
# مردیم ⇒ آن راه را کنار می‌گذاریم و راهِ بعدی را می‌رویم. این‌طور یک کرشِ
# ناشناخته خودش را یک‌بار برای همیشه درمان می‌کند، بی اینکه کاربر کاری کند.
#
# `_CRASHED` عکسِ لحظه‌ی شروعِ برنامه است: ردِ پاهای مانده از اجرای قبل. همان
# اول خوانده و پاک می‌شوند، پس اجرای بعدی دوباره همین‌ها را نمی‌بیند.
_CRASHED: dict[str, str] = {}
_MARK_TAGS = ("rec", "thumbs")


def _mark_path(tag: str) -> str:
    return os.path.join(_offline_dir(), f"mark_{tag}.txt")


def _mark_set(tag: str, note: str = "1") -> None:
    """«دارم می‌روم داخلِ تماسِ خطرناک.» — روی دیسک، چون ممکن است برنگردیم."""
    try:
        with open(_mark_path(tag), "w", encoding="utf-8") as f:
            f.write(str(note))
            f.flush()
            os.fsync(f.fileno())     # بی fsync ممکن است هرگز روی دیسک نرود
    except Exception:
        pass


def _mark_clear(tag: str) -> None:
    """«سالم برگشتم.»"""
    try:
        os.remove(_mark_path(tag))
    except OSError:
        pass


def _marks_boot() -> None:
    """ردِ پاهای مانده از اجرای قبل را برمی‌دارد و پاک می‌کند (یک‌بار، در شروع)."""
    for tag in _MARK_TAGS:
        p = _mark_path(tag)
        try:
            if os.path.isfile(p):
                with open(p, "r", encoding="utf-8") as f:
                    _CRASHED[tag] = (f.read() or "1").strip()
                os.remove(p)
        except Exception:
            pass
    if _CRASHED:
        log.warning("اجرای قبلی وسطِ کارِ نیتیو مرد: %s", _CRASHED)
        # ردِ پای ضبط نامِ رمزگذار را دارد ⇒ همان‌جا مرده‌ایم، برای همیشه کنار.
        nm = _CRASHED.get("rec") or ""
        if nm and nm in REC_SPECS:
            _rec_ban(nm)


def _safe_update(*ctrls) -> None:
    """`ctrl.update()` برای هرکدام، بی اینکه یکی بقیه را زمین بزند.

    چرا نه `page.update()`: آن کلِ درخت را با سرور مقایسه و همگام می‌کند؛
    وقتی فقط یک نوارِ عکس عوض شده، این هزینه‌ی بی‌خودی است و روی گوشیِ ضعیف
    همان لگی است که کاربر می‌گوید. به‌روزرسانیِ نقطه‌ای فقط همان شاخه را
    می‌فرستد.

    چرا try/except: کنترلی که هنوز به صفحه نچسبیده یا از درخت درآمده،
    `update()` استثنا می‌دهد («Frozen controls cannot be updated» یا کنترلِ
    بی‌والد). این‌جا خطا خبرِ مهمی نیست — کنترلِ رفته را نباید تازه کرد.
    """
    for c in ctrls:
        try:
            if c is not None:
                c.update()
        except Exception:
            pass


def _repaint(page, *ctrls) -> None:
    """به‌روزرسانیِ نقطه‌ای، با تورِ ایمنیِ `page.update()`.

    این را برای مسیرهای *پرتکرار* ساختیم (رسیدنِ پیام، «داره می‌نویسه…»،
    ساعتِ ضبط، تیکِ خوانده‌شدن). آن‌جا `page.update()` کلِ درختِ صفحه — که در
    گفت‌وگو صدها حباب است — با هر رویدادِ کوچک مقایسه می‌شد و همان کندیِ
    نسخه‌ی ۱.۴ بود.

    ولی سرعت نباید به بهای درستی باشد: اگر کنترلی هنوز به صفحه نچسبیده یا
    Flet «یخ‌زده»‌اش می‌داند، `update()` استثنا می‌دهد و بی‌صدا خوردنِ آن یعنی
    کاربر صفحه‌ی کهنه ببیند — بدتر از کندی. پس هر شکستی ما را به همان مسیرِ
    قدیمیِ کلِ صفحه برمی‌گرداند: در حالتِ عادی فقط شاخه‌ها می‌روند، و در
    حالتِ بد رفتار دقیقاً همان چیزی است که پیش‌تر بود.
    """
    bad = False
    for c in ctrls:
        if c is None:
            continue
        try:
            c.update()
        except Exception:
            bad = True
    if bad:
        try:
            page.update()
        except Exception:
            pass


# ── حافظه‌ی بلندمدتِ ضبطِ صدا ───────────────────────────────
#
# دو چیز را بینِ اجراها نگه می‌داریم:
#   • `bad`  — رمزگذارهایی که *پروسه را کشتند*. ردِ پای `rec` اگر سرِ جایش
#              مانده باشد یعنی درست وسطِ `start_recording` مردیم؛ نامِ همان
#              رمزگذار در ردِ پا نوشته شده، پس برای همیشه کنارش می‌گذاریم.
#              این تنها راهِ درمانِ کرشی است که استثنا نمی‌دهد.
#   • `good` — رمزگذاری که آخرین بار *کار کرد*. دفعه‌ی بعد اول همان را
#              امتحان می‌کنیم و هفت‌تا پرسشِ پشتیبانی را هم رد می‌کنیم؛
#              هم سریع‌تر، هم کم‌ریسک‌تر (هر پرسش خودش یک تماسِ نیتیو است).
def _rec_mem() -> dict:
    d = _offline_load("rec") or {}
    return d if isinstance(d, dict) else {}


def _rec_bad() -> set:
    return {str(x) for x in (_rec_mem().get("bad") or [])}


def _rec_ban(name: str) -> None:
    """این رمزگذار کشنده است ⇒ دیگر هرگز امتحانش نکن."""
    if not name:
        return
    d = _rec_mem()
    bad = {str(x) for x in (d.get("bad") or [])}
    if name in bad:
        return
    bad.add(name)
    d["bad"] = sorted(bad)
    if d.get("good") == name:
        d.pop("good", None)
    _offline_save("rec", d)
    log.warning("رمزگذارِ %s پروسه را کشته بود ⇒ کنار گذاشته شد", name)


def _rec_good() -> str:
    return str(_rec_mem().get("good") or "")


def _rec_good_set(name: str) -> None:
    if not name or _rec_good() == name:
        return
    d = _rec_mem()
    d["good"] = name
    _offline_save("rec", d)


def _cache_dir():
    """پوشه‌ی موقتِ برنامه — «باز کردن» فایل رو اینجا می‌ذاره، نه در دانلود."""
    # اندروید: `~` مسیرِ قابل‌نوشتنِ برنامه نیست؛ Flet پوشه‌ی مخصوصِ داده را
    # در `FLET_APP_STORAGE_DATA` می‌دهد. اول همان، بعد ویندوز/لینوکس.
    android = os.getenv("FLET_APP_STORAGE_DATA")
    if android:
        p = os.path.join(android, "cache")
        try:
            os.makedirs(p, exist_ok=True)
            return p
        except OSError:
            pass
    base = (os.environ.get("LOCALAPPDATA")
            or os.path.join(os.path.expanduser("~"), ".cache"))
    p = os.path.join(base, "Rafigram", "cache")
    try:
        os.makedirs(p, exist_ok=True)
    except OSError:
        p = _scratch_dir()
    return p


# ── پیش‌بافرِ مدیا (گیف/عکس) ────────────────────────
# گیف‌ها روی هر بار باز شدنِ تخته از شبکه کشیده می‌شدند؛ روی موبایل یعنی
# چند ثانیه کادرِ خالی و بعد پخشِ کند. یک‌بار روی دیسک می‌نشینند و از آن
# پس مسیرِ محلی به کنترل داده می‌شود: بی‌درنگ باز می‌شود و حلقه هم نرم
# می‌چرخد چون رمزگشا دیگر منتظرِ شبکه نیست.
_MCACHE: dict[str, str] = {}
_MCACHE_LOCK: dict[str, asyncio.Lock] = {}


def _cache_path_for(url: str) -> str:
    d = os.path.join(_cache_dir(), "media")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        d = _cache_dir()
    ext = os.path.splitext((url or "").split("?")[0])[1][:6] or ".bin"
    key = hashlib.sha1((url or "").encode("utf-8")).hexdigest()[:20]
    return os.path.join(d, f"{key}{ext}")


def _cached_src(url: str):
    """اگر از قبل روی دیسک هست مسیرِ محلی، وگرنه همان نشانیِ شبکه."""
    full = _media(url)
    if not full:
        return None
    hit = _MCACHE.get(full)
    if hit and os.path.exists(hit):
        return hit
    return full


async def _prefetch_media(url: str):
    """فایل را (اگر نیست) می‌گیرد و مسیرِ محلی‌اش را برمی‌گردانَد."""
    full = _media(url)
    if not full:
        return None
    hit = _MCACHE.get(full)
    if hit and os.path.exists(hit):
        return hit
    dest = _cache_path_for(full)
    if os.path.exists(dest) and os.path.getsize(dest) > 64:
        _MCACHE[full] = dest
        return dest
    lock = _MCACHE_LOCK.setdefault(full, asyncio.Lock())
    async with lock:
        hit = _MCACHE.get(full)
        if hit and os.path.exists(hit):
            return hit
        tmp = dest + ".part"
        try:
            await api.download_to(full, tmp)
            os.replace(tmp, dest)
        except Exception:
            try:
                os.remove(tmp)
            except OSError:
                pass
            return full          # شبکه نشد؛ همان نشانی را بده
        _MCACHE[full] = dest
        return dest


async def _prefetch_all(urls, limit=4):
    """چند فایل را هم‌زمان (ولی حداکثر `limit` تا) پیش‌بافر می‌کند."""
    sem = asyncio.Semaphore(max(1, limit))

    async def one(u):
        async with sem:
            try:
                await _prefetch_media(u)
            except Exception:
                pass
    await asyncio.gather(*[one(u) for u in (urls or [])])


def _unique_path(folder, name):
    """مسیرِ آزاد در `folder`؛ اگه نام تکراری بود «(۱)» می‌گیره."""
    dest = os.path.join(folder, name or "file.bin")
    base, ext = os.path.splitext(dest)
    n = 1
    while os.path.exists(dest):
        dest = f"{base} ({n}){ext}"
        n += 1
    return dest


def _open_local(path):
    """فایل رو با برنامه‌ی پیش‌فرضِ سیستم باز می‌کنه."""
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)          # noqa: S606 — مسیرِ خودمان است
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
        return True
    except Exception:
        return False


# پسوندها به تفکیکِ نوع — msg_type از همین‌ها ساخته می‌شه
IMG_EXT = ["jpg", "jpeg", "png", "webp", "gif", "bmp"]
VID_EXT = ["mp4", "mkv", "webm", "mov", "avi", "m4v", "3gp"]
AUD_EXT = ["mp3", "m4a", "wav", "ogg", "opus", "flac", "aac"]
ANY_EXT = None   # بی‌محدودیت — هر فایلی مجازه
# انواعِ پیامی که «مدیا» حساب می‌شن (منوی ذخیره/باز کردن دارن)
MEDIA_KINDS = ("image", "gif", "video", "audio", "voice", "file")


def _ext_of(name):
    return name.rsplit(".", 1)[-1].lower() if "." in (name or "") else ""


def _kind_for(name):
    """پسوندِ فایل → msg_type."""
    ext = _ext_of(name)
    if ext == "gif":
        return "gif"
    if ext in IMG_EXT:
        return "image"
    if ext in VID_EXT:
        return "video"
    if ext in AUD_EXT:
        return "audio"
    return "file"


def _kind_label(kind):
    return {"image": t("photo"), "gif": t("gif"), "video": t("video"),
            "audio": t("audio"), "voice": t("voice_message")}.get(kind, t("file"))


def _rm(path):
    try:
        if path and os.path.isfile(path):
            os.remove(path)
    except Exception:
        pass


async def _read_bytes(path):
    """فایل را در یک ترد جدا می‌خواند تا حلقه‌ی رویداد قفل نشود.

    خواندنِ یک ویدیوی چند-ده‌مگابایتی با `open().read()` صدها میلی‌ثانیه
    طول می‌کشد و در همان مدت هیچ رویدادِ UI پردازش نمی‌شود — یعنی فریزِ
    محسوس. `asyncio.to_thread` همان کار را بیرونِ حلقه انجام می‌دهد.
    """
    def _read():
        with open(path, "rb") as fh:
            return fh.read()
    try:
        return await asyncio.to_thread(_read)
    except Exception:
        return None


async def _read_url_bytes(url):
    """بایت‌های یک نشانیِ مرورگری (`blob:` یا `http:`) را می‌خواند.

    در مرورگر، ضبط‌کننده فایل روی دیسک نمی‌سازد؛ `stop_recording` یک
    Blob URL برمی‌گرداند. پایتونِ برنامه هم خودش داخلِ همان مرورگر
    (Pyodide) می‌دود، پس با `fetch` خودِ مرورگر می‌شود بایت‌ها را گرفت.
    اگر هیچ‌کدام از راه‌ها نبود `None` برمی‌گردد و لایه‌ی بالا پیامِ آدمیزاد
    نشان می‌دهد — نه سکوت و نه بسته‌شدنِ برنامه.
    """
    if not url:
        return None
    try:                                    # راهِ اولِ Pyodide
        from pyodide.http import pyfetch    # type: ignore
        r = await pyfetch(url)
        return bytes(await r.bytes())
    except Exception:
        pass
    try:                                    # راهِ دومِ خامِ JS
        import js                           # type: ignore
        resp = await js.fetch(url)
        buf = await resp.arrayBuffer()
        return bytes(js.Uint8Array.new(buf).to_py())
    except Exception:
        return None


def _audio_ext(data, fallback="m4a") -> str:
    """پسوندِ درستِ صدا را از خودِ بایت‌ها می‌فهمد.

    در مرورگر، رمزگذاری که ما خواسته‌ایم تضمینی نیست: کروم معمولاً
    WebM/Opus می‌دهد و سافاری MP4. اگر پسوند با محتوا نخواند، سرور نوعِ
    اشتباه ذخیره می‌کند و پخش‌کننده صدا را باز نمی‌کند. پس امضای فایل را
    می‌خوانیم، نه حرفِ تنظیمات را.
    """
    b = bytes(data[:16] or b"")
    if b.startswith(b"OggS"):
        return "ogg"
    if b.startswith(b"\x1a\x45\xdf\xa3"):
        return "webm"
    if b.startswith(b"RIFF"):
        return "wav"
    if len(b) > 8 and b[4:8] == b"ftyp":
        return "m4a"
    if b.startswith(b"ID3") or (len(b) > 1 and b[0] == 0xFF
                                and (b[1] & 0xE0) == 0xE0):
        return "mp3"
    if b.startswith(b"fLaC"):
        return "flac"
    if b.startswith(b"#!AMR"):
        return "amr"
    return fallback


def _scratch_dir() -> str:
    """پوشه‌ی فایلِ موقت — روی اندروید باید پوشه‌ی خودِ برنامه باشد.

    `tempfile.gettempdir()` روی اندروید می‌تواند مسیری بدهد که پروسه‌ی
    برنامه اجازه‌ی نوشتن در آن را ندارد؛ ضبط‌کننده‌ی صدا آن‌وقت در لایه‌ی
    نیتیو (نه پایتون) می‌شکند و کلِ برنامه بسته می‌شود. Flet برای همین
    `FLET_APP_STORAGE_TEMP` را ست می‌کند.
    """
    for env in ("FLET_APP_STORAGE_TEMP", "FLET_APP_STORAGE_DATA"):
        d = os.getenv(env)
        if d:
            try:
                os.makedirs(d, exist_ok=True)
                return d
            except OSError:
                pass
    return tempfile.gettempdir()


def _touch_out(path: str) -> bool:
    """فایلِ خروجی را *پیش از* تماسِ نیتیو، خالی می‌سازد. `True` = ساخته شد.

    ⚠️ این تابع ریشه‌ی باگِ «صدا ضبط نشد» روی اندروید است — و درمانش.

    زنجیره‌ی خرابی: `start_recording(output_path=...)` در سمتِ دارت مسیر را
    خامْ به `getAssetSource()` می‌دهد و آن تابع (فایلِ
    `flet/lib/src/utils/images_io.dart` تابعِ `getAssetSrc`) *اول* می‌پرسد
    «آیا این فایل هست؟» با `io.File(src).existsSync()`. مسیرِ ضبطِ ما هنوز
    ساخته نشده ⇒ جواب «نه» ⇒ می‌افتد در شاخه‌ی دوم که مسیر را یک *دارایی*
    (asset) فرض می‌کند: `/` ابتدای مسیر را می‌بُرد و بقیه را زیرِ پوشه‌ی
    assets می‌چسباند. نتیجه مسیری است که پوشه‌ی پدرش وجود ندارد ⇒
    `MediaRecorder.prepare()` در جاوا `IOException` می‌دهد ⇒ پایتون استثنا
    می‌گیرد ⇒ حلقه‌ی رمزگذارها یکی‌یکی به همین دلیل رد می‌شوند ⇒ پیامِ
    «صدا ضبط نشد».

    ویندوز سالم بود چون جداکننده‌ی مسیرش `\\` است و `C:\\...` با `\\` شروع
    نمی‌شود، پس آن شاخه هیچ‌وقت اجرا نمی‌شد. برای همین فقط گوشی مشکل داشت.

    درمان: فایل را از قبل (خالی) بسازیم تا `existsSync()` بگوید «هست» و مسیر
    دست‌نخورده به ضبط‌کننده برسد. سه مسیرِ خروجیِ اندروید همه با فایلِ موجود
    مشکلی ندارند و خودشان خالی‌اش می‌کنند:
      • `MediaRecorder.setOutputFile` (حالتِ use_legacy) بازنویسی می‌کند
      • `IContainerWriter.createFile` صریحاً `setLength(0)` می‌زند
      • `MediaMuxer(path, format)` فایل را از صفر می‌نویسد
    """
    if not path:
        return False
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "wb"):
            pass
        return os.path.exists(path)
    except OSError as ex:
        log.info("فایلِ خروجیِ ضبط ساخته نشد (%s): %s", path, ex)
        return False


# ── ضبطِ صدا: نقشه‌ی رمزگذارها ───────────────────────
# هر رمزگذار نرخ‌های *قانونیِ* خودش را دارد و پیش‌فرضِ افزونه
# (channels=2, sample_rate=44100, bit_rate=128000) برای هیچ‌کدام امن نیست:
# AMR فقط تک‌کاناله و ۱۶/۸ کیلوهرتز است، و میکروفونِ خیلی از گوشی‌ها
# تک‌کاناله است پس درخواستِ دو کانال حتی AAC را هم می‌شکند. شکستنش استثنایِ
# پایتونی نیست — `MediaRecorder` در لایه‌ی جاوا `IllegalStateException`
# می‌اندازد و پروسه می‌میرد (همان «برنامه بسته شد»). پس هر تلاش با تنظیماتِ
# مخصوصِ خودِ آن رمزگذار آغاز می‌شود، نه با پیش‌فرضِ مشترک.
#            نام:  (پسوند, نرخِ نمونه, نرخِ بیت)
REC_SPECS = {
    "AACLC":  ("m4a",  44100,  64000),
    "AACHE":  ("m4a",  44100,  64000),
    "AACELD": ("m4a",  44100,  64000),
    "OPUS":   ("ogg",  48000,  64000),
    "AMRWB":  ("amr",  16000,  23850),
    "AMRNB":  ("amr",   8000,  12200),
    "WAV":    ("wav",  16000, 256000),
    "FLAC":   ("flac", 44100, 128000),
}

# ترتیب سکوآگاه است. در مرورگر `MediaRecorder`ِ کروم/اندروید AAC نمی‌دهد ولی
# WebM/Opus می‌دهد (`audio/webm;codecs=opus`)، و سافاریِ آیفون برعکس: mp4/AAC
# می‌دهد و Opus را نمی‌شناسد. پس در وب Opus اول است و AAC بعد، تا هر دو
# مرورگر چیزی گیرشان بیاید. روی برنامه‌ی نصب‌شده AAC-LC و بعد AMR-WB،
# فراگیرترین جفتِ اندروید — هر گوشی‌ای که MediaRecorder دارد یکی‌شان را دارد.
REC_ORDER_WEB = ("OPUS", "AACLC", "AACHE", "AACELD", "WAV", "AMRWB", "AMRNB")
REC_ORDER_APP = ("AACLC", "AMRWB", "AACHE", "AMRNB", "AACELD", "OPUS", "WAV")


def _rec_config(name: str):
    """`(تنظیمات، پسوند)` برای یک رمزگذار — یا None اگر این نسخه نشناسدش."""
    if far is None:
        return None
    enc = getattr(far.AudioEncoder, name, None)
    if enc is None:
        return None
    ext, rate, bits = REC_SPECS.get(name, ("m4a", 44100, 64000))
    kw = {"encoder": enc, "channels": 1, "sample_rate": rate,
          "bit_rate": bits}
    try:
        kw["android_configuration"] = far.AndroidRecorderConfiguration(
            # مسیرِ «قدیمی» همان MediaRecorderِ خودِ اندروید است و مستندِ
            # افزونه صریح می‌گوید پایدارتر؛ مسیرِ تازه (AudioRecord + رمزگذارِ
            # دستی) روی گوشی‌های ارزان و اندرویدهای قدیمی می‌شکند.
            use_legacy=True,
            # SCOِ بلوتوث روی اندروید ۱۲+ مجوزِ BLUETOOTH_CONNECT می‌خواهد که
            # در manifestِ ما نیست ⇒ SecurityException و مرگِ پروسه. خاموش.
            manage_bluetooth=False,
            audio_source=far.AndroidAudioSource.MIC)
    except Exception:
        pass                          # نسخه‌ی بی‌این‌فیلدها: پیش‌فرض بماند
    try:
        return far.AudioRecorderConfiguration(**kw), ext
    except Exception:
        pass
    try:                              # حتی اگر فیلدی نبود، خودِ رمزگذار
        return far.AudioRecorderConfiguration(encoder=enc), ext
    except Exception:
        return None


async def _pick_encoder() -> None:
    """نقشه‌ی ضبط را برای *این* دستگاه می‌چیند (`_enc["plan"]`).

    از خودِ ضبط‌کننده می‌پرسیم کدام رمزگذارها را می‌شناسد و فهرستِ مرتبِ
    `(نام، تنظیمات، پسوند)` می‌سازیم که `_start_rec` یکی‌یکی امتحانش می‌کند.
    پیش‌تر این تابع فقط `_recorder.configuration.encoder` را عوض می‌کرد؛ اگر
    همان یکی سرِ ضبط می‌شکست، دیگر جای عقب‌نشینی نبود و برنامه می‌بست.

    اگر پرسش بی‌جواب ماند یا این نسخه پرسش را نداشت، نقشه همان ترتیبِ ایستا
    می‌ماند — بی‌خطر است، چون هر تلاش در try است و تنظیماتش قانونی.

    از نسخه‌ی ۱.۵ حافظه‌ی بلندمدت هم دارد:
      • رمزگذارهایی که یک‌بار پروسه را کشتند (`_rec_bad`) از نقشه بیرون‌اند.
      • رمزگذارِ آخرین ضبطِ موفق (`_rec_good`) سرِ صف می‌رود و آن‌وقت *هیچ*
        پرسشی نمی‌کنیم؛ هفت رفت‌وبرگشتِ نیتیو در شروعِ هر اجرا خودش هم کند
        بود هم بی‌دلیل پرخطر. یعنی از دومین اجرا به بعد، ضبط بی هیچ سنجشی
        و فوری آغاز می‌شود.
    """
    global _rec_ext
    if _recorder is None or far is None:
        _enc["plan"] = []
        _enc_done.set()
        return
    if _enc["started"]:
        try:
            await asyncio.wait_for(_enc_done.wait(), timeout=6.0)
        except Exception:
            pass                # پاسخ نیامد؛ با نقشه‌ی ایستا جلو می‌رویم
        return
    _enc["started"] = True
    bad, good = _rec_bad(), _rec_good()
    order = [nm for nm in (REC_ORDER_WEB if _web["on"] else REC_ORDER_APP)
             if nm not in bad]
    if good and good in order:                 # آخرین رمزگذارِ موفق، اولِ صف
        order = [good] + [nm for nm in order if nm != good]
    cand = []
    for nm in order:
        got = _rec_config(nm)
        if got is not None:
            cand.append((nm, got[0], got[1]))
    _enc["plan"] = list(cand)           # نقشه‌ی ایستا، پیش از هر پرسش
    if cand:
        _rec_ext = cand[0][2]
    if good and cand and cand[0][0] == good:
        # می‌دانیم چه چیزی روی *همین* دستگاه کار می‌کند ⇒ پرسش لازم نیست.
        _enc_done.set()
        return
    try:
        fit = []
        for nm, cfg, ext in cand:
            try:
                ok = await asyncio.wait_for(
                    _recorder.is_supported_encoder(
                        getattr(far.AudioEncoder, nm)), timeout=2.0)
            except Exception:
                return      # پرسش پشتیبانی نمی‌شود ⇒ نقشه‌ی ایستا بماند
            if ok:
                fit.append((nm, cfg, ext))
        if fit:
            _enc["plan"], _rec_ext = fit, fit[0][2]
    finally:
        _enc_done.set()


def _local(iso):
    """ISO سرور (UTC) → datetime محلی."""
    if not iso:
        return None
    try:
        d = datetime.fromisoformat(iso)
    except Exception:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone()


def _g2j(gy, gm, gd):
    """میلادی → شمسی."""
    gdm = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    if gy > 1600:
        jy, gy = 979, gy - 1600
    else:
        jy, gy = 0, gy - 621
    gy2 = gy + 1 if gm > 2 else gy
    days = (365 * gy + (gy2 + 3) // 4 - (gy2 + 99) // 100 + (gy2 + 399) // 400
            - 80 + gd + gdm[gm - 1])
    jy += 33 * (days // 12053)
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        return jy, 1 + days // 31, 1 + days % 31
    return jy, 7 + (days - 186) // 30, 1 + (days - 186) % 30

def _hhmm(iso):
    d = _local(iso)
    return i18n.num(d.strftime("%H:%M")) if d else ""


def _mmss(seconds):
    s = max(0, int(seconds or 0))
    return i18n.num(f"{s // 60}:{s % 60:02d}")


def _daykey(iso):
    d = _local(iso)
    return d.date().isoformat() if d else ""


def _daylabel(iso):
    """برچسبِ تاریخ — شمسی در فارسی، میلادی در انگلیسی."""
    d = _local(iso)
    if not d:
        return ""
    today = datetime.now().astimezone().date()
    delta = (today - d.date()).days
    if delta == 0:
        return t("today")
    if delta == 1:
        return t("yesterday")
    if i18n.lang() == "fa":
        jy, jm, jd = _g2j(d.year, d.month, d.day)
        lbl = f"{i18n.num(jd)} {i18n.months()[jm - 1]}"
        return (lbl if jy == _g2j(today.year, today.month, today.day)[0]
                else f"{lbl} {i18n.num(jy)}")
    lbl = f"{d.day} {i18n.months()[d.month - 1]}"
    return lbl if d.year == today.year else f"{lbl} {d.year}"


def _when(iso):
    d = _local(iso)
    return f"{_daylabel(iso)} {_hhmm(iso)}" if d else ""


# نویسه‌های «چسب» ایموجی: اتصال‌دهنده‌ی صفر-پهنا، گزینه‌گرِ تصویری، و
# رنگ‌های پوست. این‌ها خودشان استیکر نیستند، بخشی از استیکرِ کنارشان‌اند.
_EMO_GLUE = {0x200D, 0xFE0F, 0xFE0E, 0x20E3}


def _is_sticker_text(s) -> bool:
    """آیا این متن یک «استیکر» است؟

    تلگرام پیامی که فقط یک‌تا‌سه ایموجی است را بزرگ و بی‌حباب نشان می‌دهد؛
    ما هم همان کار را می‌کنیم. پس استیکرِ فرستاده‌شده از تخته، بی هیچ فایل و
    دانلودی، درست مثلِ استیکر دیده می‌شود.
    """
    s = (s or "").strip()
    if not s or len(s) > 24:
        return False
    base = 0
    for ch in s:
        o = ord(ch)
        if o in _EMO_GLUE or 0x1F3FB <= o <= 0x1F3FF:
            continue
        if ch.isalnum() or ch.isspace() or o < 0x203C:
            return False
        base += 1
    return 1 <= base <= 3


def _is_online(iso):
    """آخرین بازدیدِ تازه‌تر از ۷۰ ثانیه = آنلاین (فقط برای سرورهای قدیمی)."""
    d = _local(iso)
    if not d:
        return False
    return (datetime.now().astimezone() - d).total_seconds() < 70


def _online_of(d) -> bool:
    """آنلاین‌بودن — حرفِ سرور مقدم است.

    سرور حالا `online` می‌فرستد و آن را از سوکت‌های بازِ «جلو» حساب می‌کند،
    پس برنامه‌ی بسته یا کوچک‌شده بی‌درنگ آفلاین می‌شود. حدسِ ۷۰ ثانیه‌ایِ
    `last_seen_at` فقط برای سروری می‌ماند که این کلید را ندارد.
    """
    if isinstance(d, dict):
        if "online" in d:
            return bool(d.get("online"))
        return _is_online(d.get("last_seen_at"))
    return _is_online(d)


def _is_read(read_iso, created_iso) -> bool:
    """آیا طرفِ مقابل این پیام را خوانده؟ (تیکِ دوبل)"""
    r, c = _local(read_iso), _local(created_iso)
    if not r or not c:
        return False
    return r >= c


def _seen_text(iso):
    """زیرعنوانِ «آنلاین / آخرین بازدید …» — همیشه با ساعت."""
    d = _local(iso)
    if not d:
        return ""
    if _is_online(iso):
        return t("online")
    now = datetime.now().astimezone()
    if d.date() == now.date():
        return t("last_seen_at", when=_hhmm(iso))
    return t("last_seen_at", when=_when(iso))


def _seen_of(d) -> str:
    """همان `_seen_text` ولی با احترام به پرچمِ `online` سرور."""
    if not isinstance(d, dict):
        return _seen_text(d)
    if _online_of(d):
        return t("online")
    iso = d.get("last_seen_at")
    dt = _local(iso)
    if not dt:
        return ""
    now = datetime.now().astimezone()
    if dt.date() == now.date():
        return t("last_seen_at", when=_hhmm(iso))
    return t("last_seen_at", when=_when(iso))

def _go(page, body, pattern=False, dx=0.0):
    """رفتن به یک صفحه‌ی جدید: پس‌زمینه‌ی درخشان + بدنه.

    `dx` گذارِ کشویی می‌سازد (مثلِ بازشدنِ گفت‌وگو در تلگرام)؛ صفر = فقط محو.
    """
    page.bgcolor = P["bg0"]
    page.rtl = i18n.rtl()
    page.theme = ui.flet_theme()
    page.theme_mode = (ft.ThemeMode.LIGHT if ui.is_light()
                       else ft.ThemeMode.DARK)
    ui.set_device(page)          # اندازه‌ی صفحه ممکنه عوض شده باشه (چرخشِ گوشی)
    page.clean()
    bg_img = prefs.get("chat_bg")
    if pattern and (prefs.flag("pattern") or bg_img):
        root = ft.Stack(
            [*ui.chat_backdrop(bg_src=(_media(bg_img) if bg_img else None),
                               dim=prefs.get("chat_bg_dim"),
                               blur=prefs.get("chat_bg_blur"),
                               pattern=prefs.flag("pattern")),
             ui.safe(body)], expand=True)
    else:
        root = ui.screen(page, body)
    # محوشدنِ نرمِ کلِ صفحه هنگامِ جابه‌جایی (حسِ گذارِ تلگرام)
    ui.prep_in(root, dx=dx, dy=0.0, ms=ui.MED)
    page.add(root)
    page.update()
    try:
        page.run_task(ui.enter, page, root, stagger=0.0, first_delay=0.0)
    except Exception:
        ui.play_in(root)
        page.update()


def _back(page, fn, *a):
    """دکمه‌ی برگشت — جهتش با زبان عوض می‌شه."""
    return ui.icon_btn("arrow_right" if i18n.rtl() else "arrow_left",
                       tip=t("back"),
                       on_click=lambda e: page.run_task(fn, page, *a))


def _toast(page, text, ok=False, action=None, on_action=None):
    """پیام کوتاه پایین صفحه.

    `action`/`on_action` یک دکمه‌ی متنی کنارش می‌گذارد («ذخیره شد — دیدن»)،
    همان کاری که تلگرام سرِ ذخیره و فوروارد می‌کند. بی این دو، رفتار مثل
    قبل است.
    """
    try:
        act = None
        if action and on_action:
            act = ft.SnackBarAction(label=str(action), text_color="#ffffff",
                                    on_click=lambda e: on_action())
        page.show_dialog(ft.SnackBar(
            ft.Text(text, size=fs(13), color="#ffffff"),
            bgcolor=P["ok"] if ok else P["acc"],
            behavior=ft.SnackBarBehavior.FLOATING,
            shape=ft.RoundedRectangleBorder(radius=12),
            margin=MG(12, 0, 12, 12),
            action=act,
        ))
    except Exception:
        print(f"[toast] {text}")


def _err_text(ex):
    """پیام خطای HTTP رو خوانا می‌کنه."""
    try:
        import httpx
        if isinstance(ex, httpx.HTTPStatusError):
            d = ex.response.json()
            return str(d.get("detail") or d)
    except Exception:
        pass
    return str(ex)


# ── وضعیتِ شبکه ─────────────────────────────────────
# «در حال اتصال» فقط وقتی نوشته می‌شه که واقعاً اینترنت/سرور نباشه؛ لرزشِ
# سوکت به‌تنهایی کافی نیست، چون درخواست‌های HTTP ممکنه سالم باشن.
_net = {"ok": True}


def _net_mark(ok=True):
    _net["ok"] = bool(ok)


def _net_fail(ex) -> bool:
    """آیا این خطا «قطعیِ شبکه» است؟ (نه خطای منطقیِ سرور)"""
    # مهلتِ خودمان (`_try`) هم قطعیِ شبکه است. مهلتِ httpx ۱۵ ثانیه است و در
    # ایران اتصالِ فیلترشده آویزان می‌ماند نه اینکه رد شود؛ پس بیشترِ
    # «قطعی»ها به شکلِ TimeoutError می‌آیند، نه ConnectError.
    if isinstance(ex, (asyncio.TimeoutError, TimeoutError)):
        _net_mark(False)
        return True
    try:
        import httpx
        if isinstance(ex, httpx.HTTPStatusError):
            _net_mark(True)        # سرور جواب داد، پس شبکه سالمه
            return False
        if isinstance(ex, (httpx.ConnectError, httpx.ConnectTimeout,
                           httpx.ReadTimeout, httpx.RemoteProtocolError,
                           httpx.NetworkError)):
            _net_mark(False)
            return True
    except Exception:
        pass
    return False


def _net_down() -> bool:
    """هم سوکت قطع باشه هم آخرین درخواستِ HTTP شکست خورده باشه."""
    return (not ws_client.online) and not _net["ok"]


async def _try(coro, secs: float):
    """یک درخواستِ شبکه با مهلتِ *کوتاهِ* خودمان.

    مهلتِ httpx ۱۵ ثانیه است، و روی شبکه‌های ایران اتصالِ فیلترشده آویزان
    می‌ماند نه اینکه سریع رد شود. هرجا این وسطِ راهِ رسمِ یک صفحه باشد،
    صفحه همان‌قدر سفید می‌ماند — نشانه‌اش همان «گفت‌وگو با ضربه باز نمی‌شود»
    بود. با مهلتِ کوتاه، زودتر می‌رویم سرِ نسخه‌ی محلی.
    """
    return await asyncio.wait_for(coro, secs)


# ── رمزنگاریِ محلی ─────────────────────────────────
QUICK = ["👍", "❤️", "🔥", "😂", "😮", "💩"]


def _enc_on() -> bool:
    """رمزنگاری هم روشن باشه هم کلید داشته باشیم."""
    return prefs.flag("enc") and crypto.has_key()


def _out_text(body: str):
    """(متنِ آماده‌ی ارسال، پرچمِ enc)."""
    if _enc_on():
        return crypto.encrypt(body), True
    return body, False


def _in_text(m: dict):
    """(متنِ خوانا، رمزشده‌بود؟) — قفلِ بسته با جای‌گیر نشون داده می‌شه."""
    raw = m.get("content") or ""
    if not (m.get("enc") or crypto.is_encrypted(raw)):
        return raw, False
    return crypto.show(raw, t("enc_locked"))


# ── منشن‌ها و لینک‌های عمیق ─────────────────────────
_MENTION = re.compile(r"@([A-Za-z0-9_]{2,32})")


async def _open_mention(page, handle):
    """@شناسه → چتِ کاربر، یا گروه/کانالِ متناظر (اگه لازم بود عضو می‌شیم)."""
    h = (handle or "").lstrip("@").strip().lower()
    if not h:
        return
    try:
        await api.get_profile(h)
    except Exception:
        pass
    else:
        await _open_chat_with(page, h)
        return
    try:
        hits = await api.search_all("@" + h)
    except Exception:
        hits = []
    hit = next((x for x in hits if (x.get("handle") or "").lower() == h), None)
    if hit is None:
        _toast(page, t("mention_not_found"))
        return
    if hit.get("joined") and hit.get("chat_id"):
        await chat_page(page, hit["chat_id"])
        return
    try:
        res = await api.join_chat(handle=h)
    except Exception as ex:
        _toast(page, _err_text(ex))
        return
    _toast(page, t("joined_done"), ok=True)
    await chat_page(page, res["chat_id"])


def _spans(page, body):
    """متن رو به تکه‌های ساده و @منشن‌های کلیک‌پذیر می‌شکنه."""
    out, i = [], 0
    for mt in _MENTION.finditer(body or ""):
        if mt.start() > i:
            out.append(ft.TextSpan(body[i:mt.start()]))
        out.append(ft.TextSpan(
            mt.group(0),
            style=ft.TextStyle(color=P["acc"], weight=W.W_600),
            on_click=lambda e, u=mt.group(1): page.run_task(_open_mention, page, u)))
        i = mt.end()
    if i < len(body or ""):
        out.append(ft.TextSpan(body[i:]))
    return out


def _rich(page, body, color, size, sel=True):
    """متنِ پیام با منشن‌های کلیک‌پذیر (اگه منشنی نبود، متنِ ساده)."""
    if not _MENTION.search(body or ""):
        return ft.Text(body or "", size=size, color=color, selectable=sel)
    return ft.Text(spans=_spans(page, body), size=size, color=color,
                   selectable=sel)


# ── پوسته: روز/شب و پوسته‌ی دلخواه ──────────────────
def _apply_custom():
    """پالتِ دلخواه رو از تنظیماتِ ذخیره‌شده می‌سازه."""
    ui.build_custom(base=prefs.get("c_base") or "crimson",
                    acc=prefs.get("c_acc") or None,
                    sent=prefs.get("c_sent") or None,
                    bg=prefs.get("c_bg") or None)


def _use_theme(name):
    """پوسته رو فعال می‌کنه (دلخواه اول ساخته می‌شه)."""
    if name == ui.CUSTOM:
        _apply_custom()
    ui.use_theme(name)


async def _toggle_day_night(page, after=None):
    """کلیدِ خورشید/ماه: روشن ⇄ آخرین پوسته‌ی تاریک."""
    if ui.is_light():
        name = prefs.get("dark_theme") or "crimson"
        if name == "light":
            name = "crimson"
    else:
        await prefs.put("dark_theme", ui.theme_name())
        name = "light"
    _use_theme(name)
    await prefs.put("theme", name)
    await (after or chat_list_page)(page)


def _daynight_btn(page, after=None):
    """آیکونِ روز/شبِ نوارِ بالا."""
    return ui.icon_btn("moon" if ui.is_light() else "sun",
                       tip=t("night_mode") if ui.is_light() else t("day_mode"),
                       on_click=lambda e: page.run_task(_toggle_day_night,
                                                        page, after))


def _theme_dialog(page, after=None):
    """انتخابِ سریعِ پوسته — از منویِ عکسِ پروفایلِ منویِ اصلی باز می‌شود.

    همان چهار کارتِ صفحه‌ی تنظیمات است، ولی به‌شکلِ دیالوگ؛ تا برای عوض‌کردنِ
    رنگِ برنامه لازم نباشد کاربر تا تنظیمات برود. «دلخواه» به همان پوسته‌ی
    ساخته‌شده در سازنده‌ی پوسته وصل است.
    """
    back = after or chat_list_page

    async def _set(name):
        try:
            page.pop_dialog()
        except Exception:
            pass
        if name == ui.theme_name() and name != ui.CUSTOM:
            return
        _use_theme(name)
        await prefs.put("theme", name)
        if not ui.is_light():
            await prefs.put("dark_theme", name)
        await back(page)

    def _card(name, label, c1, c2):
        on = ui.theme_name() == name
        # پهنای صریح لازم است: در دیالوگ، `Row(wrap=True)` به بچه‌ی بی‌پهنا
        # قیدِ شُل می‌دهد و کارت تا تهِ عرض کشیده می‌شود، پس هر کارت می‌افتد
        # یک سطرِ جدا. با ۷۸ پیکسل، هر چهار پوسته در یک ردیف جا می‌شوند.
        return ft.Container(
            ft.Column([ft.Container(
                width=58, height=40, border_radius=BR(11, 11, 11, 11),
                gradient=ft.LinearGradient(begin=AL.TOP_LEFT,
                                           end=AL.BOTTOM_RIGHT,
                                           colors=[c1, c2])),
                ui.spacer(h=6),
                ft.Text(label, size=fs(10), font_family=ui.FONT,
                        text_align=ft.TextAlign.CENTER,
                        color=P["txt"] if on else P["sub"])],
                spacing=0, tight=True,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER),
            width=74, padding=PAD(8, 10, 8, 8), border_radius=BR(16, 16, 16, 16),
            bgcolor=op(0.10) if on else op(0.03), ink=True, tooltip=label,
            border=ft.Border(
                *[ft.BorderSide(1.5, P["acc"] if on else op(0.06))] * 4),
            on_click=lambda e: page.run_task(_set, name))

    body = ft.Row([_card("crimson", t("theme_crimson"), "#e53935", "#3d0c0c"),
                   _card("telegram", t("theme_telegram"), "#3390ec", "#17212b"),
                   _card("light", t("theme_light"), "#3390ec", "#e9eef5"),
                   _card(ui.CUSTOM, t("theme_custom"),
                         prefs.get("c_acc") or "#9c27b0",
                         prefs.get("c_sent") or "#4a148c")],
                  spacing=8, wrap=True, run_spacing=10,
                  alignment=ft.MainAxisAlignment.CENTER)
    ui.dialog(page, t("theme"), body, width=380,
              actions=[ui.ghost_btn(t("close"),
                                    on_click=lambda e: page.pop_dialog())])


# ── پوسته‌ی صفحه‌ی ورود ────────────────────────────
# صفحه‌ی ورود/ثبت‌نام همیشه با آبیِ کلاسیکِ تلگرام باز می‌شه و پوسته‌ی
# دلخواهِ کاربر دست‌نخورده در prefs می‌مونه تا بعد از ورود برگرده.
AUTH_THEME = "telegram"
_auth_light = False       # روز/شبِ موقتِ صفحه‌ی ورود (در prefs ذخیره نمی‌شه)


def _auth_theme():
    """پوسته‌ی پیش‌فرضِ صفحه‌ی ورود را فعال می‌کند (بدون دست‌زدن به prefs)."""
    ui.use_theme("light" if _auth_light else AUTH_THEME)


def _restore_theme():
    """پوسته‌ی ذخیره‌شده‌ی خودِ کاربر را برمی‌گرداند (بعد از ورودِ موفق)."""
    global _auth_light
    _auth_light = False
    _use_theme(prefs.get("theme", "crimson"))


def _auth_daynight_btn(page, mode="login"):
    """روز/شبِ صفحه‌ی ورود — موقتی است و پوسته‌ی کاربر را عوض نمی‌کند."""
    async def _flip(pg):
        global _auth_light
        _auth_light = not _auth_light
        await auth_page(pg, mode)

    return ui.icon_btn("moon" if ui.is_light() else "sun",
                       tip=t("night_mode") if ui.is_light() else t("day_mode"),
                       on_click=lambda e: page.run_task(_flip, page))


# ── منوی شناور و تأیید ─────────────────────────────
def _mi(page, icon, text, fn, danger=False):
    """ردیفِ منو که اول منو رو می‌بنده بعد کار رو انجام می‌ده."""
    def _run(e):
        try:
            page.pop_dialog()
        except Exception:
            pass
        fn()
    return ui.menu_item(icon, text, on_click=_run, danger=danger)


def _menu_dialog(page, *items, width=250):
    """منوی شیشه‌ایِ شناور (کلیکِ راست / نگه‌داشتن).

    دیالوگ‌ها در `page._dialogs` می‌نشینند، یعنی بیرونِ درختِ اصلی، و `page.rtl`
    را به ارث نمی‌برند؛ بی این `rtl`، آیکونِ هر ردیف می‌افتد چپِ متن — وارونه‌ی
    چیدمانِ فارسیِ بقیه‌ی برنامه.
    """
    d = ft.AlertDialog(
        bgcolor=op(0.0), shadow_color=op(0.0), elevation=0,
        barrier_color=op(0.35, "#000000"),
        content_padding=PAD(0, 0, 0, 0), inset_padding=PAD(24, 50, 24, 50),
        content=ft.Container(ui.float_menu(*[i for i in items if i],
                                          width=width), width=width,
                             rtl=i18n.rtl()),
    )
    page.show_dialog(d)
    return d


def _prog_card(name, size_text, sub, prog, width=None):
    """کارتِ فایل با نوارِ پیشرفتِ زنده (خودِ ProgressBar پاس داده می‌شه)."""
    disc = ft.Container(
        ui.vic(ui.file_icon_for(name), 20, "#ffffff"), width=40, height=40,
        border_radius=BR(40, 40, 40, 40), alignment=AL.CENTER,
        bgcolor=op(0.22, P["acc"]))
    meta = " · ".join([x for x in (size_text, sub) if x])
    return ft.Container(
        ft.Row([disc, ft.Column(
            [ft.Text(name, size=fs(13.5), color=P["txt"], max_lines=1,
                     overflow=ft.TextOverflow.ELLIPSIS,
                     font_family=ui.FONT_MED),
             ft.Text(meta, size=fs(11), color=op(0.70)),
             ft.Container(prog, margin=MG(0, 4, 0, 0))],
            spacing=2, tight=True, expand=True)],
            spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        width=width, border_radius=BR(12, 12, 12, 12),
        padding=PAD(4, 4, 8, 4))


def _confirm(page, title, text, on_yes, yes_label=None):
    """پرسشِ بله/خیر برای کارهای برگشت‌ناپذیر."""
    def _yes(e):
        try:
            page.pop_dialog()
        except Exception:
            pass
        on_yes()
    ui.dialog(page, title, ft.Text(text, size=fs(13), color=P["sub"]),
              actions=[ui.ghost_btn(t("cancel"),
                                    on_click=lambda e: page.pop_dialog()),
                       ui.primary_btn(yes_label or t("confirm"), width=120,
                                      height=40, on_click=_yes)])


# ── مدیریت شنونده‌های وب‌سوکت ─────────────────────
_ws_hooks = []


def _attach_ws(fn):
    ws_client.add_global_listener(fn)
    _ws_hooks.append(fn)


def _detach_ws():
    # هر صفحه در آغازِ خودش این را صدا می‌زند، پس همین‌جا «چتِ جلوی چشم» را
    # هم پاک می‌کنیم: صفحه‌ی چت بی‌درنگ بعدش خودش را ثبت می‌کند و بقیه‌ی
    # صفحه‌ها خالی می‌مانند — وگرنه با بیرون‌رفتن از یک چت، اعلانش خفه می‌ماند.
    notify.set_active_chat(None)
    # کالبکِ «برگشت از پس‌زمینه» هم مالِ همان صفحه‌ی رفته بود؛ اگر نماند،
    # صفحه‌ی تازه که ثبتش نکند، کالبکِ کهنه روی کنترل‌های مرده اجرا می‌شد.
    notify.on_resume(None)
    while _ws_hooks:
        try:
            ws_client.remove_global_listener(_ws_hooks.pop())
        except Exception:
            pass

# ═══════════════════════════════════════════════════
#  پخشِ پیام صوتی — هر لحظه فقط یک صدا
# ═══════════════════════════════════════════════════
class _VoicePlayer:
    """حباب‌ها خودشون رو ثبت می‌کنن و با تغییرِ وضعیت تازه می‌شن.

    توجه: `await audio.play()` در ویندوز قفل می‌کنه؛ پس شروعِ پخش با
    `autoplay=True` انجام می‌شه و ادامه/توقف با `resume()`/`pause()`.
    """

    def __init__(self):
        self._audio = None
        self.key = None
        self.playing = False
        self.pos = 0.0
        self.dur = 0.0
        self.rate = 1.0
        self._cbs = {}

    def bind(self, mid, cb):
        if mid:
            self._cbs[mid] = cb

    def clear(self):
        self._cbs.clear()

    def state(self, mid):
        """(در حال پخش؟، نسبتِ پخش‌شده، ثانیه‌ی جاری)"""
        if not mid or mid != self.key:
            return False, 0.0, 0.0
        frac = (self.pos / self.dur) if self.dur > 0 else 0.0
        return self.playing, max(0.0, min(1.0, frac)), self.pos

    def info(self, mid):
        """(پخش؟، نسبت، ثانیه، مدتِ کل، سرعت) — برای پخش‌کننده‌ی موسیقی."""
        if not mid or mid != self.key:
            return False, 0.0, 0.0, 0.0, 1.0
        playing, frac, pos = self.state(mid)
        return playing, frac, pos, self.dur, self.rate

    async def seek(self, mid, frac):
        """پریدن به نسبتی از فایل (۰ تا ۱)."""
        if self._audio is None or mid != self.key or self.dur <= 0:
            return
        target = max(0.0, min(self.dur, self.dur * float(frac)))
        self.pos = target
        try:
            await self._audio.seek(int(target * 1000))
        except Exception:
            pass
        self._ping(mid)

    def set_rate(self, mid, step):
        """سرعت را یک پله جابه‌جا می‌کند و مقدارِ تازه را برمی‌گرداند."""
        if mid != self.key:
            return self.rate
        try:
            i = SPEEDS.index(self.rate)
        except ValueError:
            i = SPEEDS.index(1.0)
        i = max(0, min(len(SPEEDS) - 1, i + step))
        self.rate = SPEEDS[i]
        if self._audio is not None:
            try:
                self._audio.playback_rate = self.rate
                self._audio.update()
            except Exception:
                pass
        self._ping(mid)
        return self.rate

    def _ping(self, *mids):
        for mid in (mids or tuple(self._cbs)):
            cb = self._cbs.get(mid)
            if cb is None:
                continue
            try:
                cb()
            except Exception:
                pass

    # ── رویدادهای پلاگین ────────────────────────────
    def _on_pos(self, e):
        self.pos = (getattr(e, "position", 0) or 0) / 1000.0
        self._ping(self.key)

    def _on_dur(self, e):
        d = getattr(e, "duration", None)
        ms = getattr(d, "in_milliseconds", 0) if d is not None else 0
        self.dur = (ms or 0) / 1000.0
        self._ping(self.key)

    def _on_state(self, e):
        name = getattr(getattr(e, "state", None), "name", "") or ""
        name = name.upper()
        if "COMPLET" in name or "STOP" in name:
            self.playing, self.pos = False, 0.0
        elif "PAUSE" in name:
            self.playing = False
        elif "PLAY" in name:
            self.playing = True
        self._ping(self.key)

    async def toggle(self, mid, url):
        """پخش/توقف. برای هر پیامِ تازه، منبعِ صوتیِ نو ساخته می‌شه."""
        if fa is None or not url:
            return False
        if mid and mid == self.key and self._audio is not None:
            try:
                if self.playing:
                    await self._audio.pause()
                    self.playing = False
                else:
                    await self._audio.resume()
                    self.playing = True
            except Exception:
                return False
            self._ping(mid)
            return True
        prev = self.key
        if self._audio is not None:
            try:
                await self._audio.pause()
            except Exception:
                pass
        self._audio = None
        self.key, self.pos, self.dur, self.playing = mid, 0.0, 0.0, True
        kw = {}
        rm = getattr(fa, "ReleaseMode", None)
        if rm is not None:
            kw["release_mode"] = rm.STOP
        try:
            self._audio = fa.Audio(
                src=url, autoplay=True, playback_rate=self.rate,
                on_position_change=self._on_pos,
                on_duration_change=self._on_dur,
                on_state_change=self._on_state, **kw)
        except Exception:
            self.playing = False
            self._ping(prev, mid)
            return False
        self._ping(prev, mid)
        return True


_player = _VoicePlayer()


# ═══════════════════════════════════════════════════
#  پروفایل
# ═══════════════════════════════════════════════════
_me = {}  # پروفایل خودم (برای آواتار نوار بالا)
_pg = {}  # {"p": page} — تنها صفحه‌ی این پروسه، برای کارهای پشتِ صحنه


async def _load_me(secs: float = 6.0) -> bool:
    """پروفایلِ خودم را تازه می‌کند و در انبارِ محلی هم می‌گذارد.

    ⚠️ هیچ صفحه‌ای *پیش از کشیدنِ خودش* نباید منتظرِ این بماند. مهلت دارد
    چون httpx به‌خودی‌خود ۱۵ ثانیه صبر می‌کند و اتصالِ فیلترشده «آویزان»
    می‌شود نه رد — یعنی بی مهلت، صفحه‌ی تنظیمات ۱۵ ثانیه سفید می‌مانْد.
    """
    try:
        got = await asyncio.wait_for(api.get_my_profile(), timeout=secs)
    except Exception:
        return False
    if isinstance(got, dict) and got:
        _me.update(got)
        try:
            page = _pg.get("p")
            if page is not None:
                page.run_task(store.save_blob, "me", dict(_me))
        except Exception:
            pass
        return True
    return False


async def _seed_me() -> bool:
    """اگر `_me` خالی است، از انبارِ محلی پُرش کن (شروعِ سردِ بی‌اینترنت).

    بازگشتی: آیا حالا چیزی برای *کشیدن* داریم؟ صفحه‌ها با همین جواب تصمیم
    می‌گیرند فوری رنگ شوند (True) یا ناچار منتظرِ شبکه بمانند (False).
    """
    if _me.get("username") or _me.get("display_name"):
        return True
    got = await store.load_blob("me")
    if isinstance(got, dict):
        for k, v in got.items():
            _me.setdefault(k, v)
    return bool(_me.get("username") or _me.get("display_name"))


def _photo_viewer(page, name, avatar_url, photos, editable=False):
    """گالریِ عکس‌های پروفایل: نوارهای استوری‌وار + پیکانِ چپ/راست."""
    urls = [u for u in (photos or []) if u]
    if avatar_url and avatar_url not in urls:
        urls.insert(0, avatar_url)
    st = {"i": 0}
    slot = ft.Container(width=124, height=124, alignment=AL.CENTER,
                        animate_opacity=MED, opacity=1.0)
    bars = ft.Container(width=124, alignment=AL.CENTER)

    def _paint():
        url = urls[st["i"]] if urls else avatar_url
        slot.content = ui.avatar(name, 116, url=_media(url), ring=True)
        bars.content = ui.dots(len(urls), st["i"], on_pick=_jump, width=116)
        for c in (prev, nxt):
            c.visible = len(urls) > 1
        try:
            page.update()
        except Exception:
            pass

    def _jump(i):
        if 0 <= i < len(urls):
            st["i"] = i
            _paint()

    def _step(d):
        if len(urls) > 1:
            st["i"] = (st["i"] + d) % len(urls)
            _paint()

    def _after(res, toast_key):
        urls[:] = [u for u in (res.get("photos") or []) if u]
        st["i"] = 0
        _toast(page, t(toast_key), ok=True)
        _paint()

    async def _add(e=None):
        nm, data = await _pick(page, IMG_EXT, t("add_photo"))
        if not data:
            return
        try:
            res = await api.add_photo_bytes(nm, data)
        except Exception as ex:
            _toast(page, t("max_photos") if "400" in str(ex) else _err_text(ex))
            return
        _after(res, "photo_added")

    async def _main(e=None):
        if not urls:
            return
        try:
            res = await api.set_main_photo(urls[st["i"]])
        except Exception as ex:
            _toast(page, _err_text(ex))
            return
        _me["avatar_url"] = res.get("avatar_url")
        _after(res, "saved")

    async def _del(e=None):
        if not urls:
            return
        try:
            res = await api.delete_photo(urls[st["i"]])
        except Exception as ex:
            _toast(page, _err_text(ex))
            return
        _after(res, "photo_deleted")

    prev = ui.icon_btn("chevron_right" if i18n.rtl() else "chevron_left",
                       box=30, size=16, on_click=lambda e: _step(-1))
    nxt = ui.icon_btn("chevron_left" if i18n.rtl() else "chevron_right",
                      box=30, size=16, on_click=lambda e: _step(1))
    tools = []
    if editable:
        tools = [ui.spacer(h=6), ft.Row(
            [ui.icon_btn("plus", tip=t("add_photo"), color=P["acc"], box=34,
                         size=16, on_click=lambda e: page.run_task(_add)),
             ui.icon_btn("check", tip=t("set_main_photo"), color=P["ok"], box=34,
                         size=16, on_click=lambda e: page.run_task(_main)),
             ui.icon_btn("trash", tip=t("delete_photo"), color=P["acc2"], box=34,
                         size=16, on_click=lambda e: page.run_task(_del))],
            spacing=4, tight=True, alignment=ft.MainAxisAlignment.CENTER)]
    _paint()
    return ft.Column(
        [bars, ui.spacer(h=6),
         ft.Row([prev, slot, nxt], spacing=2, tight=True,
                alignment=ft.MainAxisAlignment.CENTER,
                vertical_alignment=ft.CrossAxisAlignment.CENTER), *tools],
        spacing=0, tight=True,
        horizontal_alignment=ft.CrossAxisAlignment.CENTER)


def _profile_dialog(page, name, username, bio, avatar_url=None,
                    is_group=False, members=None, last_seen=None,
                    blocked=False, photos=None, editable=False, kind=None,
                    count=None, hidden=False):
    head = ft.Column(
        [_photo_viewer(page, name, avatar_url, photos, editable=editable),
         ui.spacer(h=12),
         ft.Text(name, size=fs(20), weight=W.W_600, font_family=ui.FONT_BOLD,
                 color=P["txt"], text_align=ft.TextAlign.CENTER),
         ft.Text(f"@{username}" if username else " ", size=fs(13),
                 color=P["acc"], text_align=ft.TextAlign.CENTER)],
        horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=2, tight=True)
    items = [head]
    if not is_group and last_seen:
        items.append(ft.Container(
            ft.Text(_seen_of(last_seen) if isinstance(last_seen, dict)
                    else _seen_text(last_seen), size=fs(12), color=P["sub"]),
            alignment=AL.CENTER, padding=PAD(0, 6, 0, 0)))
    items += [ui.spacer(h=14),
              ui.group(ui.row_item("info", bio or t("no_bio"), t("bio")))]

    n_all = int(count) if count is not None else len(members or [])
    if is_group and kind == "channel":
        # کانال: همیشه شمارِ مشترک‌ها دیده می‌شه
        items += [ui.spacer(h=10),
                  ui.group(ui.row_item("users",
                                       t("subscriber_n", n=_kcount(n_all)),
                                       t("subscribers")))]
    if is_group and hidden and not members:
        # مشترکِ ساده‌ی کانال: فهرست خصوصیه
        items += [ui.spacer(h=10),
                  ui.group(ui.row_item("lock", t("members_private")))]

    if is_group and members:
        rows = []
        for m in members:
            dn, un = m.get("display_name") or "", m.get("username") or ""
            role = m.get("role") or "member"

            def _open(e, u=un):
                page.pop_dialog()
                page.run_task(_show_user_profile, page, u)

            tail = []
            if m.get("banned"):
                tail.append(ft.Text(t("banned"), size=fs(10), color=P["acc2"]))
            elif role in ("owner", "admin"):
                tail.append(ft.Row(
                    [ui.vic("crown" if role == "owner" else "shield", 13,
                            P["acc"]),
                     ft.Text(t(role), size=fs(10), color=P["acc"])],
                    spacing=3, tight=True))

            rows.append(ft.Container(
                ft.Row([ui.avatar(dn, 38, url=_media(m.get("avatar_url")),
                                  badge=(P["ok"]
                                         if _online_of(m)
                                         else None)),
                        ft.Column([ft.Text(dn, size=fs(14), color=P["txt"]),
                                   ft.Text(f"@{un}", size=fs(11),
                                           color=P["sub"])],
                                  spacing=1, expand=True),
                        *tail], spacing=12),
                padding=PAD(14, 8, 14, 8), on_click=_open, ink=True,
                border_radius=BR(12, 12, 12, 12)))
            ui.hover_tint(rows[-1])
        lbl = (t("channel_admins") if kind == "channel"
               else t("member_n", n=_kcount(n_all)))
        items += [ui.section(lbl), ui.group(*rows)]

    if not is_group and username and username != api.username:
        def _msg(e):
            page.pop_dialog()
            page.run_task(_open_chat_with, page, username)

        def _blk(e):
            page.pop_dialog()
            page.run_task(_toggle_block, page, username, blocked)

        items += [
            ui.spacer(h=14),
            ui.primary_btn(t("send_message"), icon="send", on_click=_msg),
            ui.spacer(h=8),
            ui.group(ui.row_item(
                "ban", t("unblock") if blocked else t("block_user"),
                on_click=_blk, icon_color=P["acc2"])),
        ]

    items += [ui.spacer(h=6),
              ft.Row([ui.ghost_btn(t("close"), on_click=lambda e: page.pop_dialog())],
                     alignment=ft.MainAxisAlignment.CENTER)]
    return ui.dialog(page, t("group") if is_group else t("profile"),
                     ft.Container(
                         ft.Column(items, spacing=0, tight=True, scroll=ft.ScrollMode.AUTO),
                         height=min(560, 240 + 78 * (1 + len(members or [])))),
                     width=360)


async def _open_chat_with(page, username):
    try:
        res = await api.create_chat(username)
    except Exception as ex:
        _toast(page, _err_text(ex))
        return
    await chat_page(page, res["chat_id"])


async def _toggle_block(page, username, blocked):
    try:
        if blocked:
            await api.unblock_user(username)
            _toast(page, t("unblocked_done"), ok=True)
        else:
            await api.block_user(username)
            _toast(page, t("blocked_done"), ok=True)
    except Exception as ex:
        _toast(page, _err_text(ex))


async def _show_user_profile(page, username):
    try:
        p = await api.get_profile(username)
    except Exception as ex:
        _toast(page, _err_text(ex))
        return
    blocked = False
    try:
        blocked = any(b.get("username") == username for b in await api.list_blocks())
    except Exception:
        pass
    _profile_dialog(page, p.get("display_name") or username, username,
                    p.get("bio"), avatar_url=p.get("avatar_url"),
                    last_seen=p, blocked=blocked,
                    photos=p.get("photos") or [],
                    editable=(username == api.username))


async def _show_my_profile(page):
    """گالریِ عکس‌های خودم — قابلِ افزودن/حذف/انتخابِ عکسِ اصلی."""
    await _load_me()
    photos = _me.get("photos") or []
    try:
        photos = (await api.my_photos()).get("urls") or photos
    except Exception:
        pass
    _profile_dialog(page, _me.get("display_name") or "?",
                    _me.get("username"), _me.get("bio"),
                    avatar_url=_me.get("avatar_url"), photos=photos,
                    editable=True)


async def _pick(page, exts, title, ftype=None):
    """پنجره‌ی بومیِ انتخاب فایل → (نام، بایت‌ها) یا (None, None).

    `exts=None` ⇒ هیچ محدودیتی روی پسوند نیست (FilePickerFileType.ANY).
    `ftype` ⇒ نوعِ آماده‌ی FilePicker (IMAGE / MEDIA / AUDIO). روی اندروید
    این همان است که «گالریِ سیستم» یا «انتخابگرِ آهنگ» را باز می‌کند و چون
    از SAF/Photo-Picker رد می‌شود، به هیچ مجوزِ زمانِ اجرا نیاز ندارد؛ پس
    نه کرش می‌کند نه پنجره‌ی مجوز می‌خواهد.
    مسیرِ محلیِ آخرین انتخاب در `_last_pick` می‌ماند تا بشود پیش‌نمایشش داد.
    """
    _last_pick["path"] = None
    if _picker is None:
        _toast(page, t("error"))
        return None, None
    if ftype is not None:
        kw = {"file_type": ftype}
    else:
        kw = ({"file_type": ft.FilePickerFileType.ANY} if not exts else
              {"file_type": ft.FilePickerFileType.CUSTOM,
               "allowed_extensions": exts})
    try:
        files = await _picker.pick_files(
            dialog_title=title,
            initial_directory=_pictures_dir(),
            with_data=True, **kw,
        )
    except Exception as ex:
        _toast(page, _err_text(ex))
        return None, None
    if not files:
        return None, None
    f = files[0]
    data = f.bytes
    if not data and f.path:
        data = await _read_bytes(f.path)
    if not data:
        return None, None
    _last_pick["path"] = f.path or None
    return (f.name or "file.bin"), data


# ── گالریِ درون‌برنامه‌ای ────────────────────────────
_PIC_EXT = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp")


def _gallery_dirs() -> list[str]:
    """پوشه‌هایی که ممکن است عکس داشته باشند (دسکتاپ و اندروید)."""
    home = os.path.expanduser("~")
    out = [os.path.join(home, "Pictures"), os.path.join(home, "Downloads"),
           os.path.join(home, "Pictures", "Screenshots"),
           "/storage/emulated/0/DCIM/Camera",
           "/storage/emulated/0/Pictures",
           "/storage/emulated/0/Download",
           "/sdcard/DCIM/Camera"]
    seen, keep = set(), []
    for p in out:
        rp = os.path.normpath(p)
        if rp in seen:
            continue
        seen.add(rp)
        try:
            if os.path.isdir(rp):
                keep.append(rp)
        except OSError:                      # مسیرِ بی‌اجازه روی اندروید
            pass
    return keep


def _recent_pics(limit=15) -> list[str]:
    """تازه‌ترین عکس‌های روی دستگاه — برای شبکه‌ی بالای برگه‌ی پیوست.

    اندروید از اندروید ۱۰ به بعد این پوشه‌ها را بی‌مجوز نمی‌دهد؛ آن‌وقت این
    فهرست خالی برمی‌گردد و برگه خودش کاربر را به گالریِ سیستم می‌فرستد.
    هرگز استثنا بالا نمی‌دهد و بیش از ۴۰۰ فایل در هر پوشه نمی‌خواند تا روی
    پوشه‌های بزرگ برنامه را قفل نکند.
    """
    found: list[tuple[float, str]] = []
    for d in _gallery_dirs():
        try:
            with os.scandir(d) as it:
                for i, ent in enumerate(it):
                    if i > 400:
                        break
                    try:
                        if not ent.is_file():
                            continue
                        if not ent.name.lower().endswith(_PIC_EXT):
                            continue
                        found.append((ent.stat().st_mtime, ent.path))
                    except OSError:
                        continue
        except OSError:
            continue
    found.sort(key=lambda x: x[0], reverse=True)
    return [p for _m, p in found[:limit]]


# ── مجوزهای زمانِ اجرا (اندروید) ─────────────────────
# ⏱ سقفِ زمانِ تماسِ نیتیو. *بی این‌ها برنامه یخ می‌زد.*
#
# `Service._invoke_method()` در Flet با `timeout=None` صدا زده می‌شود و
# `flet/messaging/session.py` می‌کند: `await asyncio.wait_for(evt.wait(),
# timeout=None)` — یعنی اگر لایه‌ی نیتیو *جواب نفرستد*، آن تسک **تا ابد**
# منتظر می‌ماند. هیچ استثنایی هم نمی‌آید که بگیریمش. این دقیقاً همان
# «می‌زنم روی سنجاق، برنامه فریز می‌شود» است: تا نسخه‌ی ۱.۴ `_ask_perm`
# بی هیچ انتظاری `True` می‌داد؛ در ۱.۵ واقعی شد و از آن‌جا فریز آمد.
PERM_T_STATUS = 4.0        # «وضعیت چیست؟» — بی پنجره، باید فوری جواب بدهد
PERM_T_ASK = 60.0          # «اجازه می‌دهی؟» — پنجره‌ی سیستم، دستِ کاربر است

# دو درخواستِ مجوزِ همزمان روی اندروید *هرگز* جواب نمی‌گیرند: افزونه‌ی
# `permission_handler` یک درخواست را در پرواز نگه می‌دارد و دومی را دور
# می‌اندازد. پس ضربه‌ی دومِ کاربر (سنجاق + میکروفون پشتِ هم) می‌توانست هر دو
# تسک را قفل کند. این قفل صف می‌سازد.
_perm_lock = None                          # asyncio.Lock | None


def _perm_gate() -> asyncio.Lock:
    """قفلِ مجوز — تنبل ساخته می‌شود تا به حلقه‌ی رویدادِ درست بچسبد."""
    global _perm_lock
    if _perm_lock is None:
        _perm_lock = asyncio.Lock()
    return _perm_lock


def _granted(st) -> bool:
    return str(getattr(st, "name", st)).upper() == "GRANTED"


async def _nat(coro, timeout: float, label: str = ""):
    """یک تماسِ نیتیو با سقفِ زمان. رد شد ⇒ `TimeoutError`.

    چرا لازم است: Flet برای `Service._invoke_method` سقفِ زمان *نمی‌گذارد*
    (`timeout=None`). اگر لایه‌ی نیتیو جواب نفرستد — که روی اندروید هنگامِ
    گرفتنِ میکروفون، دو ضبطِ همزمان، یا پنجره‌ی مجوزِ گم‌شده پیش می‌آید — آن
    تسک **تا ابد** پارک می‌شود. تسکِ پارک‌شده هم نگهبانِ `busy` را باز
    نمی‌کند، پس ضربه‌های بعدیِ کاربر هم بی‌جواب می‌مانند: همان «برنامه فریز
    شد». با سقفِ زمان بدترین حالت یک پیامِ خطاست، نه یخ‌زدنِ همیشگی.
    """
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except (asyncio.TimeoutError, TimeoutError):
        log.info("تماسِ نیتیو جواب نداد: %s (>%ss)", label or "?", timeout)
        raise


async def _perm_status(nm: str) -> str:
    """وضعیتِ یک مجوز، *بی* باز کردنِ پنجره. رشته‌ی بزرگ‌حرف یا `""`.

    برای مسیرهایی که نباید کاربر را با پنجره‌ی سیستم غافلگیر کنند
    (مثلِ برگه‌ی سنجاق) همین کافی است: می‌پرسیم، و اگر نداریم سراغِ
    انتخابگرِ سیستم می‌رویم که *بی‌مجوز* هم کار می‌کند.
    """
    if fph is None or _perm is None:
        return "GRANTED"
    p = getattr(fph.Permission, nm, None)
    if p is None:
        return ""
    try:
        st = await asyncio.wait_for(_perm.get_status(p), timeout=PERM_T_STATUS)
        return str(getattr(st, "name", st)).upper()
    except (asyncio.TimeoutError, TimeoutError):
        log.info("مجوزِ %s: پرسشِ وضعیت جواب نداد (%ss)", nm, PERM_T_STATUS)
        return ""
    except Exception as ex:
        log.info("مجوزِ %s: %s", nm, ex)
        return ""


async def _ask_perm(page, *names, strict=False) -> bool:
    """مجوزها را از گوشی می‌خواهد. `True` = می‌شود جلو رفت.

    خواسته‌ی کاربر: «از گوشی مجوز دسترسی به آهنگ‌ها و گالری و لوکیشن را باید
    بگیره». روی اندروید همین پنجره‌ی مجوزِ سیستم را باز می‌کند.
    روی دسکتاپ/وب افزونه نیست و مجوزی هم لازم نیست، پس `True` می‌دهد و کار
    عادی جلو می‌رود — نه پیامِ بی‌ربط، نه توقف.

    دو حالت دارد، چون خواسته‌ی دو دسته فراخوان یکی نیست:

      • `strict=False` (پیش‌فرض) — «بپرس و بگذر». برای گالری و انتخابگرِ فایل:
        انتخابگرِ سیستم (SAF / Photo-Picker) بی‌مجوز هم کار می‌کند، پس ردِ
        مجوز نباید مسیر را ببندد؛ فقط شبکه‌ی «عکس‌های تازه» خالی می‌ماند.

      • `strict=True` — «راست بگو». نتیجه‌ی واقعی را برمی‌گرداند. برای
        میکروفون و نصبِ به‌روزرسانی لازم است: آن‌جا بی‌مجوز *نمی‌شود* جلو رفت
        و اگر دروغ بگوییم، تماسِ نیتیوِ بعدی پروسه را می‌کشد. تا نسخه‌ی ۱.۴
        این تابع همیشه `True` می‌داد و `_start_rec` بر همان تکیه می‌کرد.

    ⏱ هر تماسِ نیتیو سقفِ زمان دارد و کلِ تابع پشتِ یک قفل است. توضیحِ چراییِ
    هر دو بالای `PERM_T_STATUS` نوشته شده — خلاصه: بی این‌ها یک جوابِ نیامده
    از لایه‌ی نیتیو، تسک را برای همیشه پارک می‌کرد و برنامه فریز می‌شد.
    """
    if fph is None or _perm is None:
        return True
    ok = True
    async with _perm_gate():
        for nm in names:
            p = getattr(fph.Permission, nm, None)
            if p is None:
                continue
            try:
                if _granted(await asyncio.wait_for(
                        _perm.get_status(p), timeout=PERM_T_STATUS)):
                    continue
                st = await asyncio.wait_for(
                    _perm.request(p), timeout=PERM_T_ASK)
                if strict and not _granted(st):
                    ok = False
            except (asyncio.TimeoutError, TimeoutError):
                # نیتیو جواب نداد. «نداریم» فرض می‌کنیم و *رد می‌شویم* —
                # مهم این است که این‌جا گیر نکنیم.
                log.info("مجوزِ %s: بی‌جواب ماند", nm)
                if strict:
                    ok = False
            except Exception:
                continue                     # پلتفرمِ بی‌پشتیبان ⇒ بی‌خیال
    return ok if strict else True


async def _device_position(page):
    """(عرض، طول) یا None — با پیامِ فارسیِ روشن در هر شکستی.

    هر تماس در try است: نه افزونه‌ی نبوده کرش می‌کند، نه ردِ مجوز، نه
    خاموش‌بودنِ GPS. خواسته‌ی کاربر: «موقع استفاده هم نباید کرش کنه».
    """
    if fgl is None or _geo is None:
        _toast(page, t("loc_unsupported"))
        return None
    _toast(page, t("loc_getting"))
    try:
        if not await _geo.is_location_service_enabled():
            _toast(page, t("loc_off"))
            return None
    except Exception:
        pass                                 # بعضی پلتفرم‌ها جواب نمی‌دهند
    try:
        perm = await _geo.request_permission()
    except Exception:
        perm = None
    if perm is not None and str(getattr(perm, "name", perm)).upper() in (
            "DENIED", "DENIED_FOREVER"):
        _toast(page, t("loc_denied"))
        return None
    try:
        pos = await _geo.get_current_position()
    except Exception:
        _toast(page, t("loc_failed"))
        return None
    lat = getattr(pos, "latitude", None)
    lon = getattr(pos, "longitude", None)
    if lat is None or lon is None:
        _toast(page, t("loc_failed"))
        return None
    return float(lat), float(lon)


# ── باز کردن / ذخیره‌ی مدیا ────────────────────────
# نگاشتِ «آدرسِ سرور → مسیرِ محلیِ کش‌شده» تا هر بار دوباره دانلود نشه
_dl_cache: dict[str, str] = {}


async def _cached_file(page, url, quiet=False):
    """فایل را در پوشه‌ی کشِ برنامه می‌گذارد و مسیرش را می‌دهد.

    این پوشه در LOCALAPPDATA است، نه در «دانلود»؛ پس بازکردنِ یک پیام
    چیزی در پوشه‌های کاربر نمی‌ریزد.
    """
    if not url:
        return None
    hit = _dl_cache.get(url)
    if hit and os.path.isfile(hit):
        return hit
    dest = os.path.join(_cache_dir(), _fname_raw(url))
    if not os.path.isfile(dest):
        try:
            await api.download_to(_media(url), dest)
        except Exception as ex:
            _rm(dest)
            if not quiet:
                _toast(page, _err_text(ex))
            return None
    _dl_cache[url] = dest
    return dest


async def _open_media(page, m):
    """مدیا را با برنامه‌ی پیش‌فرضِ سیستم باز می‌کند (بی‌ذخیره در دانلود)."""
    url = (m or {}).get("content") or ""
    if not url:
        return
    _toast(page, t("opening"))
    path = await _cached_file(page, url)
    if not path:
        return
    if not _open_local(path):
        _toast(page, t("open_failed"))


async def _save_media(page, m):
    """همان فایل را در پوشه‌ی `Download/Rafigram` ذخیره می‌کند."""
    url = (m or {}).get("content") or ""
    if not url:
        return
    # پوشه‌ی عمومیِ Download روی اندروید ۱۰+ بی مجوزِ «دسترسی به همه‌ی فایل‌ها»
    # نوشتنی نیست. آن مجوز با پنجره‌ی ساده گرفته نمی‌شود — صفحه‌ی تنظیمات باز
    # می‌شود — پس *در شروعِ برنامه* نمی‌پرسیمش (آزاردهنده و بی‌ربط است)؛ همان
    # لحظه‌ای می‌پرسیم که کاربر خودش «ذخیره» را زده، و فقط یک‌بار در هر اجرا.
    # نداد؟ ایرادی ندارد: `downloads_dir()` می‌رود سرِ پوشه‌ی برنامه.
    if not _perm_asked.get("storage") and updater.plat() == "android":
        _perm_asked["storage"] = True
        try:
            if not updater.public_downloads():
                await _ask_perm(page, "MANAGE_EXTERNAL_STORAGE")
                updater._dl_cache.pop("dir", None)   # دوباره بسنج
        except Exception:
            pass
    dest = _unique_path(_downloads_dir(), _fname_raw(url))
    src = _dl_cache.get(url)
    try:
        if src and os.path.isfile(src):
            # از کش، بی‌رفت‌وبرگشتِ شبکه — ولی کپیِ یک ویدیوی چندده‌مگابایتی
            # روی حلقه‌ی رویداد فریز می‌سازد، پس در تردِ جدا انجام می‌شود.
            await asyncio.to_thread(shutil.copyfile, src, dest)
        else:
            await api.download_to(_media(url), dest)
    except Exception as ex:
        _rm(dest)
        _toast(page, _err_text(ex))
        return
    # پیامِ راست: اگر پوشه‌ی عمومیِ Download نوشتنی نبوده (اندروید ۱۰+ بی
    # مجوزِ «دسترسی به همه‌ی فایل‌ها»)، فایل جای دیگری نشسته و کاربر باید
    # مسیرش را ببیند، نه اینکه بی‌خود در گالری دنبالش بگردد.
    try:
        pub = updater.public_downloads()
    except Exception:
        pub = True
    _toast(page, t("saved_to_downloads") if pub
           else t("saved_at_folder", p=os.path.dirname(dest)), ok=True)


# ── بازکردنِ پیوند بیرونِ برنامه ────────────────────────────────────────
#
# ⚠️ `page.launch_url()` تا نسخه‌ی ۱.۵ سه‌جا صدا زده می‌شد و *هیچ‌یک کار
# نمی‌کرد*. دو دلیلِ روی‌هم:
#
#   ۱) از فلت ۰.۹۰ این متد `async def` است و زیرِ `@deprecated` پیچیده شده.
#      صداکردنش بی `await` فقط یک کوروتینِ رهاشده می‌سازد — نه خطایی، نه
#      کاری. (بی‌صدا خراب، بدترین شکلِ خرابی.)
#   ۲) `page.run_task(page.launch_url, …)` هم چاره نیست: `run_task` با
#      `inspect.iscoroutinefunction` پالایش می‌کند و پوششِ `@deprecated` یک
#      `def` سادهٔ `functools.wraps`دار است ⇒ `TypeError`.
#
# راهِ درست همان است که خودِ پیامِ بازنشستگی می‌گوید: سرویسِ `UrlLauncher`.
# ساختنِ نمونه‌اش خودش آن را روی صفحه ثبت می‌کند، پس یکی می‌سازیم و
# نگه می‌داریم؛ وگرنه هر ضربه یک سرویسِ تازه به صفحه می‌چسباند.
_urlsvc = {}          # id(page) → UrlLauncher


def _url_svc(page):
    """سرویسِ بازکنندهٔ پیوند — همان که `main()` ساخته، وگرنه تنبل‌ساخته."""
    if _url is not None:
        return _url
    key = id(page)
    svc = _urlsvc.get(key)
    if svc is None:
        _urlsvc.clear()               # در هر پروسه یک صفحه؛ بقیه زباله‌اند
        svc = ft.UrlLauncher()
        _urlsvc[key] = svc
    return svc


def _web_target(save: bool) -> str:
    """نامِ پنجره‌ی مرورگر برای این پیوند (فقط در نسخه‌ی وب معنی دارد).

    ⚠️ درسِ گران‌بها: پایتونِ نسخه‌ی وب داخلِ یک **Web Worker** اجرا می‌شود
    (`web/python.js` ⇒ `new Worker("python-worker.js")`). آن‌جا `document`
    وجود ندارد، پس راهِ آشنای «یک `<a download>` بساز و بفشارش» از پایتون
    ممکن نیست؛ همان‌طور که `config.page_origin()` هم نوشته، در ورکر فقط
    `location` هست نه `window`. کارِ بازکردن باید از سمتِ دارت (رشته‌ی
    اصلی) انجام شود ⇒ سرویسِ `UrlLauncher`.

    و آن‌جا انتخابِ نامِ پنجره تعیین‌کننده است:
      • `"_blank"` ⇒ `window.open` با پنجره‌ی تازه. چون ضربه‌ی کاربر از
        رشته‌ی اصلی به ورکر و برگشته، «فعال‌سازیِ کاربر» تا آن لحظه از دست
        رفته و مرورگر ممکن است پاپ‌آپ را ببندد. برای پیوندهای معمولی
        (نقشه، ربات، پشتیبانی) چاره‌ای نیست و ارزشش را دارد.
      • `"_self"` ⇒ *ناوبری* است نه پاپ‌آپ، و هیچ مرورگری سدش نمی‌کند. برای
        فایلِ نصب هم دقیقاً همان چیزی است که می‌خواهیم: ورسل این دو را با
        `application/vnd.android.package-archive` و
        `application/x-msdos-program` می‌فرستد، پس مرورگر *دانلود* می‌کند و
        صفحه هم جابه‌جا نمی‌شود.
    """
    return "_self" if save else "_blank"


async def _open_url(page, url: str, save: bool = False,
                    quiet: bool = False) -> bool:
    """پیوند را بیرونِ برنامه باز می‌کند. `save=True` ⇒ دانلودِ فایل.

    شکست را قِسِر در نمی‌رود: نشانی را در نوارِ پیام می‌گذارد تا کاربر
    دست‌کم بتواند کپی‌اش کند — مگر `quiet=True` که برای *تلاشِ اول* است
    (`geo:` روی اندروید) و نباید کاربر پیامِ بی‌ربط ببیند.

    روی گوشی/رایانه `mode=EXTERNAL_APPLICATION` می‌دهیم: یعنی «بده به
    برنامه‌ی خودش». بی این، url_launcher حق دارد پیوند را در یک وب‌ویوِ
    درون‌برنامه‌ای باز کند (و برای `geo:` هم هیچ مرورگری کاری نمی‌تواند بکند).
    در نسخه‌ی وب این گزینه بی‌معنی است و جایش `web_only_window_name` است.
    """
    url = (url or "").strip()
    if not url:
        return False
    if _web["on"]:
        kw = {"web_only_window_name": _web_target(save)}
    else:
        kw = {"mode": ft.LaunchMode.EXTERNAL_APPLICATION}
    try:
        await _nat(_url_svc(page).launch_url(url, **kw), 15.0, "launch_url")
        return True
    except Exception as ex:
        log.info("پیوند باز نشد (%s): %s", url, _err_text(ex))
        if not quiet:
            _toast(page, url)
        return False


def _open_url_bg(page, url: str, save: bool = False) -> None:
    """همان بالا، از دلِ یک `on_click` همگام (کوروتینِ واقعی ⇒ run_task قبول)."""
    if not (url or "").strip():
        return
    try:
        page.run_task(_open_url, page, url, save)
    except Exception:
        _toast(page, url)


def _is_android(page) -> bool:
    """اندروید است؟ (در نسخه‌ی وب هم `page.platform` را از مرورگر می‌گیرد)"""
    try:
        return str(getattr(getattr(page, "platform", None), "name", "")
                   ).upper() in ("ANDROID", "ANDROID_TV")
    except Exception:
        return False


async def _map_out(page, lat, lon) -> None:
    """نقشه را بیرون باز کن: روی اندرویدِ بومی نخست `geo:`، بعد https."""
    if _is_android(page) and not _web["on"]:
        if await _open_url(page, geo.nav(lat, lon), quiet=True):
            return
    await _open_url(page, geo.link(lat, lon))


def _open_map(page, lat, lon):
    """نقشه را بیرونِ برنامه باز می‌کند (گوگل‌مپِ گوشی یا مرورگر)."""
    try:
        page.run_task(_map_out, page, lat, lon)
    except Exception:
        _toast(page, t("error"))


def _map_card(page, lat, lon, w):
    """کارتِ نقشه‌ی کوچکِ درونِ حباب: کاشیِ OSM + سنجاق + «دیدن روی نقشه».

    چند نکته که ساده به نظر می‌رسند و نیستند:

      • کاشیِ نقشه ۲۵۶ پیکسل است و نقطه هر جای آن می‌تواند افتاده باشد. پس
        تصویر را بزرگ‌تر از کادر می‌کشیم و با `left/top`ِ حساب‌شده می‌لغزانیم
        تا *همان نقطه* در کادر بیفتد — ولی این لغزش **چلانده** می‌شود تا کاشی
        هیچ‌وقت از کادر بیرون نزند؛ وگرنه برای نقطه‌های لبه‌ی کاشی نیمی از
        کارت خالی می‌ماند (باگِ «نقشه نصفه است» در ۱.۵). سنجاق هم دیگر وسط
        نمی‌نشیند: مختصاتِ واقعیِ خودش را می‌گیرد، پس دروغ نمی‌گوید.
      • زیرِ تصویر یک لایه‌ی جانشین هست. کاشی نرسید (شبکه‌ی فیلترشده، سرورِ
        OSM شلوغ) یا هنوز در راه است ⇒ همان لایه دیده می‌شود، نه یک کادرِ
        خالیِ شکسته. `error_content`ِ خالی می‌گذاریم تا از رویش کنار برود.
      • ساعت و تیک‌ها روی این کارت شناور *نمی‌شوند*: پاصفحه‌ی خودِ کارت
        جایشان را گرفته و کپسولِ شناور روی نوشته‌ی «دیدن روی نقشه» می‌افتاد.
    """
    h = int(w * 0.62)
    url, fx, fy = geo.tile(lat, lon)
    iw = int(w * 1.5)
    coords = geo.pretty(lat, lon)
    # جای کاشی: می‌خواهیم نقطه وسطِ کادر بیفتد (`w/2 - fx*iw`)، ولی وقتی نقطه
    # لبه‌ی کاشی است این جابه‌جایی کاشی را از کادر بیرون می‌بَرد و نیمی از
    # کارت خالی می‌ماند — همان باگِ «نقشه نصفه است». پس جابه‌جایی را در
    # بازه‌ای که کاشی هنوز کلِ کادر را می‌پوشاند می‌چلانیم و بعد سنجاق را
    # سرِ جای *واقعیِ* خودش می‌گذاریم (نه وسطِ کادر).
    #   left ∈ [w-iw, 0]  و  top ∈ [h-iw, 0]   (iw > w و iw > h)
    lx = max(w - iw, min(0, int(w / 2 - fx * iw)))
    ly = max(h - iw, min(0, int(h / 2 - fy * iw)))
    px, py = lx + fx * iw, ly + fy * iw          # نقطه، نسبت به کادر
    back = ft.Container(
        ft.Column([ui.vic("pin", 26, op(0.40)),
                   ft.Text(coords, size=fs(10), color=op(0.55),
                           font_family=ui.FONT)],
                  spacing=5, tight=True,
                  horizontal_alignment=ft.CrossAxisAlignment.CENTER),
        width=w, height=h, alignment=AL.CENTER,
        gradient=ft.LinearGradient(
            begin=AL.TOP_LEFT, end=AL.BOTTOM_RIGHT,
            colors=[op(0.14, P["acc"]), op(0.10)]))
    shot = ft.Container(
        ui.photo(url, width=iw, height=iw, fit=ft.BoxFit.COVER,
                 error=ft.Container()),
        left=lx, top=ly, width=iw, height=iw)
    pin = ft.Container(
        ft.Container(
            ui.vic("pin", 15, "#ffffff"), width=28, height=28,
            alignment=AL.CENTER, border_radius=BR(14, 14, 14, 14),
            bgcolor=P["acc"],
            border=ft.Border(*[ft.BorderSide(2, op(0.92, "#ffffff"))] * 4),
            shadow=ft.BoxShadow(blur_radius=9, spread_radius=-2,
                                color=op(0.55, "#000000"), offset=OFF(0, 3))),
        left=int(px - 14), top=int(py - 14), width=28, height=28)
    chip = ft.Container(
        ft.Container(
            ft.Text(coords, size=fs(9.5), color=op(0.95, "#ffffff"),
                    font_family=ui.FONT, no_wrap=True),
            padding=PAD(7, 3, 7, 4), border_radius=BR(9, 9, 9, 9),
            bgcolor=op(0.48, "#000000"), blur=ui.blur_of(8, heavy=True)),
        left=0, right=0, bottom=0, padding=PAD(6, 6, 6, 6),
        alignment=(AL.BOTTOM_RIGHT if i18n.rtl() else AL.BOTTOM_LEFT))
    view = ft.Container(
        ft.Stack([back, shot, pin, chip]), width=w, height=h,
        border_radius=BR(12, 12, 0, 0),
        clip_behavior=ft.ClipBehavior.ANTI_ALIAS)
    foot = ft.Container(
        ft.Row([ui.vic("pin", 13, P["acc"]),
                ft.Text(t("att_location"), size=fs(12), color=P["txt"],
                        font_family=ui.FONT_MED, no_wrap=True),
                ft.Container(expand=True),
                ft.Text(t("open_in_maps"), size=fs(11), color=P["acc"],
                        font_family=ui.FONT_MED, no_wrap=True),
                ui.vic("open", 12, P["acc"])],
               spacing=5, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        padding=PAD(9, 7, 9, 7), bgcolor=op(0.10),
        border_radius=BR(0, 0, 12, 12))
    return ft.Container(
        ft.Column([view, foot], spacing=0, tight=True),
        width=w, border_radius=BR(12, 12, 12, 12), ink=True,
        clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
        animate_scale=anim(FAST, ui.POP), tooltip=t("open_in_maps"),
        on_click=lambda e: _open_map(page, lat, lon))


def _zoom_view(src, width, height, radius=14, error=None, max_scale=6.0):
    """عکس با بزرگ‌نماییِ دو‌انگشتی و کشیدن — همان حسِ نمایشگرِ تلگرام.

    `InteractiveViewer` خودِ فلاتر است، پس بزرگ‌نمایی و کشیدن روی نخِ رابط
    فلاتر انجام می‌شود، نه در پایتون: هیچ رفت‌وبرگشتی با پایتون ندارد و
    ۶۰ فریم می‌ماند حتی وقتی شبکه کند است (خواسته‌ی «بی‌وابستگی به شبکه»).

    دو ضربه = ذره‌بین: از ۱ به ۲.۵ و برگشت. `on_double_tap` را باید یک
    `GestureDetector` بدهد؛ خودِ `InteractiveViewer` رویدادِ دو‌ضربه ندارد.

    `constrained=True` عکس را در قابِ خودش نگه می‌دارد و `boundary_margin`
    کمی هوا می‌دهد تا در بزرگ‌نمایی بشود لبه‌ها را هم دید.
    """
    img = ui.photo(src, fit=ft.BoxFit.CONTAIN, radius=radius, full=True,
                   error=error)
    iv = ft.InteractiveViewer(
        content=img, min_scale=1.0, max_scale=max_scale,
        pan_enabled=True, scale_enabled=True, constrained=True,
        boundary_margin=24,
        clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
        width=width, height=height)
    st = {"big": False}

    async def _toggle(_e=None):
        # نه `zoom()` انباشته می‌شود نه `reset()` جای انگشت را می‌داند؛ همین
        # دو گام ساده رفتارِ «دو ضربه» را می‌دهد و هیچ‌وقت گیر نمی‌کند.
        try:
            if st["big"]:
                await iv.reset(300)
            else:
                await iv.zoom(2.5)
            st["big"] = not st["big"]
        except Exception:
            st["big"] = False

    return ft.GestureDetector(content=iv, on_double_tap=_toggle)


def _media_viewer(page, m):
    """نمایشگرِ درون‌برنامه‌ایِ عکس/گیف — با کپشن و دکمه‌های ذخیره/باز کردن."""
    url = (m or {}).get("content") or ""
    kind = (m or {}).get("msg_type") or "image"
    cap = (m or {}).get("caption") or ""
    src_v = _media(url)
    w = ui.body_w(430)
    view = None
    if ui.is_video_src(src_v):
        view = ui.loop_video(src_v, width=w, height=360,
                             fit=ft.BoxFit.CONTAIN, radius=14)
    if view is None:
        # عکس (و گیفِ تصویری): بزرگ‌نماییِ دو‌انگشتی + دو‌ضربه
        view = _zoom_view(src_v, w, 360, radius=14,
                          error=ft.Container(
                              ui.vic("image", 34, op(0.5)), height=220,
                              alignment=AL.CENTER))
    body = [ft.Container(view, alignment=AL.CENTER, height=360)]
    if cap:
        body.append(ui.spacer(h=10))
        body.append(_rich(page, cap, P["txt"], fs(13)))
    return ui.dialog(
        page, _kind_label(kind),
        ft.Container(ft.Column(body, spacing=0, tight=True,
                               scroll=ft.ScrollMode.AUTO),
                     width=ui.body_w(430)),
        actions=[ui.ghost_btn(t("close"), on_click=lambda e: page.pop_dialog()),
                 ui.ghost_btn(t("open_file"),
                              on_click=lambda e: page.run_task(_open_media,
                                                               page, m)),
                 ui.primary_btn(t("save_to_downloads"), width=150, height=40,
                                on_click=lambda e: page.run_task(_save_media,
                                                                 page, m))],
        width=470)


# ═══════════════════════════════════════════════════
#  پخش‌کننده‌ی درون‌برنامه‌ای (ویدیو و موسیقی)
# ═══════════════════════════════════════════════════
SPEEDS = (0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0)
_mp = {"audio": None}      # سرویسِ صدای پخش‌کننده (ارجاعِ ماژولی لازم است)


def _secs(v):
    """هر شکلی از «مدت» را به ثانیه برمی‌گرداند (Duration، عدد، رشته)."""
    if v is None:
        return 0.0
    ms = getattr(v, "in_milliseconds", None)
    if ms is not None:
        try:
            return float(ms) / 1000.0
        except (TypeError, ValueError):
            return 0.0
    if isinstance(v, dict):
        for k in ("in_milliseconds", "milliseconds", "value", "position"):
            if k in v:
                try:
                    return float(v[k] or 0) / 1000.0
                except (TypeError, ValueError):
                    return 0.0
        return 0.0
    if isinstance(v, str):
        s = v.strip()
        if ":" in s:                      # مثلِ 0:01:30.500000
            bits = s.split(":")
            try:
                out = float(bits[-1])
                if len(bits) > 1:
                    out += float(bits[-2]) * 60
                if len(bits) > 2:
                    out += float(bits[-3]) * 3600
                return out
            except (TypeError, ValueError):
                return 0.0
        try:
            return float(s) / 1000.0
        except ValueError:
            return 0.0
    try:
        return float(v) / 1000.0
    except (TypeError, ValueError):
        return 0.0


def _rate_label(r):
    """۱× / ۱.۵× — با رقم‌های زبانِ جاری."""
    txt = f"{r:g}"
    return f"{i18n.num(txt)}×"


def _media_player(page, m):
    """ویدیو و موسیقی داخلِ خودِ برنامه پخش می‌شن — با سرعتِ قابلِ تنظیم."""
    url_raw = (m or {}).get("content") or ""
    if not url_raw:
        return None
    src = _media(url_raw)
    kind = (m or {}).get("msg_type") or "video"
    vid_mode = kind == "video" or (kind != "audio" and ui.is_video_src(src))
    if vid_mode and not ui.HAS_VIDEO:
        page.run_task(_open_media, page, m)
        return None
    if not vid_mode and fa is None:
        page.run_task(_open_media, page, m)
        return None

    pl = {"pos": 0.0, "dur": 0.0, "on": True, "rate": 1.0, "live": True}
    ctl = {"v": None}

    t_pos = ft.Text("0:00", size=fs(11), color=P["sub"],
                    font_family=ui.FONT_MED)
    t_dur = ft.Text("0:00", size=fs(11), color=P["sub"],
                    font_family=ui.FONT_MED)
    bar = ft.Slider(min=0, max=1000, value=0, expand=True,
                    active_color=P["acc"], inactive_color=op(0.16),
                    thumb_color=P["acc"])
    glyph = ui.vic("pause", 20, "#ffffff")
    rate_lbl = ft.Text(_rate_label(1.0), size=fs(12.5), color=P["txt"],
                       font_family=ui.FONT_MED, text_align=ft.TextAlign.CENTER)

    def _paint():
        t_pos.value = _mmss(pl["pos"])
        t_dur.value = _mmss(pl["dur"])
        if pl["dur"] > 0:
            bar.value = max(0, min(1000, int(pl["pos"] / pl["dur"] * 1000)))
        glyph.src = "icons/pause.svg" if pl["on"] else "icons/play.svg"
        rate_lbl.value = _rate_label(pl["rate"])
        try:
            page.update()
        except Exception:
            pass

    def _on_pos(e):
        if not pl["live"]:
            return
        raw = getattr(e, "position", None)
        pl["pos"] = _secs(raw if raw is not None else getattr(e, "data", None))
        _paint()

    def _on_dur(e):
        raw = getattr(e, "duration", None)
        pl["dur"] = _secs(raw if raw is not None else getattr(e, "data", None))
        _paint()

    def _on_state(e):
        name = str(getattr(getattr(e, "state", None), "name", "") or "").upper()
        if "COMPLET" in name or "STOP" in name:
            pl["on"], pl["pos"] = False, 0.0
        elif "PAUSE" in name:
            pl["on"] = False
        elif "PLAY" in name:
            pl["on"] = True
        _paint()

    def _on_done(e):
        pl["on"] = False
        _paint()

    # ── ساختِ پخش‌کننده ─────────────────────────────
    if vid_mode:
        view = ui.loop_video(src, width=ui.body_w(420), height=250,
                             fit=ft.BoxFit.CONTAIN, radius=14, muted=False,
                             loop=False, on_position=_on_pos,
                             on_duration=_on_dur, on_complete=_on_done)
        if view is None:
            page.run_task(_open_media, page, m)
            return None
        ctl["v"] = ui.inner_video(view)
        stage = ft.Container(view, alignment=AL.CENTER, height=250,
                             bgcolor=op(0.35, "#000000"),
                             border_radius=BR(14, 14, 14, 14))
    else:
        old = _mp.get("audio")
        if old is not None:
            try:
                page.run_task(old.pause)
            except Exception:
                pass
        kw = {}
        rm = getattr(fa, "ReleaseMode", None)
        if rm is not None:
            kw["release_mode"] = rm.STOP
        try:
            ctl["v"] = fa.Audio(src=src, autoplay=True,
                                on_position_change=_on_pos,
                                on_duration_change=_on_dur,
                                on_state_change=_on_state, **kw)
        except Exception:
            page.run_task(_open_media, page, m)
            return None
        _mp["audio"] = ctl["v"]
        stage = ft.Container(
            ft.Column([ui.vic("music", 54, op(0.55, P["acc"])),
                       ui.spacer(h=8),
                       ft.Text(_fname(url_raw), size=fs(13),
                               color=P["txt"], font_family=ui.FONT_MED,
                               max_lines=2, text_align=ft.TextAlign.CENTER,
                               overflow=ft.TextOverflow.ELLIPSIS)],
                      spacing=0, tight=True,
                      horizontal_alignment=ft.CrossAxisAlignment.CENTER),
            height=170, alignment=AL.CENTER, padding=PAD(18, 10, 18, 10),
            bgcolor=op(0.05), border_radius=BR(14, 14, 14, 14))

    # ── دکمه‌ها ────────────────────────────────────
    async def _toggle(e=None):
        c = ctl["v"]
        if c is None:
            return
        try:
            if vid_mode:
                await c.play_or_pause()
                pl["on"] = not pl["on"]
            elif pl["on"]:
                await c.pause()
                pl["on"] = False
            else:
                await c.resume()
                pl["on"] = True
        except Exception:
            return
        _paint()

    async def _seek(frac):
        c = ctl["v"]
        if c is None or pl["dur"] <= 0:
            return
        target = max(0.0, min(pl["dur"], pl["dur"] * frac))
        pl["pos"] = target
        try:
            await c.seek(int(target * 1000))
        except Exception:
            pass
        _paint()

    def _bar_done(e):
        try:
            frac = float(bar.value) / 1000.0
        except (TypeError, ValueError):
            return
        page.run_task(_seek, frac)

    async def _set_rate(step):
        c = ctl["v"]
        if c is None:
            return
        try:
            i = SPEEDS.index(pl["rate"])
        except ValueError:
            i = SPEEDS.index(1.0)
        i = max(0, min(len(SPEEDS) - 1, i + step))
        pl["rate"] = SPEEDS[i]
        try:
            c.playback_rate = pl["rate"]
            c.update()
        except Exception:
            pass
        _paint()

    bar.on_change_end = _bar_done
    bar.on_change_start = lambda e: pl.update({"live": False})
    bar.on_change = lambda e: None

    def _reset_rate(e=None):
        page.run_task(_set_rate, SPEEDS.index(1.0) - SPEEDS.index(pl["rate"]))

    play_btn = ft.Container(
        glyph, width=46, height=46, alignment=AL.CENTER,
        border_radius=BR(23, 23, 23, 23),
        gradient=ft.LinearGradient(begin=AL.CENTER_LEFT, end=AL.CENTER_RIGHT,
                                   colors=[P["acc2"], P["acc"]]),
        shadow=ft.BoxShadow(blur_radius=14, spread_radius=-4,
                            color=op(0.45, P["acc"]), offset=OFF(0, 4)),
        ink=True, tooltip=t("play_pause"),
        on_click=lambda e: page.run_task(_toggle))

    speed_box = ft.Container(
        ft.Row([ui.icon_btn("minus", box=30, size=15, tip=t("slower"),
                            on_click=lambda e: page.run_task(_set_rate, -1)),
                ft.Container(rate_lbl, width=46, alignment=AL.CENTER,
                             on_click=_reset_rate, ink=True,
                             border_radius=BR(9, 9, 9, 9),
                             tooltip=t("speed_reset")),
                ui.icon_btn("plus", box=30, size=15, tip=t("faster"),
                            on_click=lambda e: page.run_task(_set_rate, 1))],
               spacing=0, tight=True,
               vertical_alignment=ft.CrossAxisAlignment.CENTER),
        bgcolor=op(0.06), border_radius=BR(12, 12, 12, 12),
        padding=PAD(2, 2, 2, 2))

    body = ft.Column(
        [stage,
         ui.spacer(h=6),
         ft.Row([t_pos, bar, t_dur], spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER),
         ft.Row([play_btn,
                 ft.Row([ui.vic("speed", 15, P["sub"]), speed_box], spacing=6,
                        tight=True),
                 ft.Container(expand=True),
                 ui.icon_btn("download", tip=t("save_to_downloads"),
                             on_click=lambda e: page.run_task(_save_media,
                                                              page, m)),
                 ui.icon_btn("open", tip=t("open_file"),
                             on_click=lambda e: page.run_task(_open_media,
                                                              page, m))],
                spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER)],
        spacing=0, tight=True)

    cap = ((m or {}).get("caption") or "").strip()
    if cap:
        body.controls.append(ft.Container(_rich(page, cap, P["txt"], fs(13)),
                                          margin=MG(0, 10, 0, 0)))

    def _close(e=None):
        pl["live"] = False
        c = ctl["v"]
        page.pop_dialog()
        if c is None:
            return
        try:
            page.run_task(c.pause)
        except Exception:
            pass
        if not vid_mode and _mp.get("audio") is c:
            _mp["audio"] = None

    d = ui.dialog(page, t("video") if vid_mode else t("audio"),
                  ft.Container(body, width=ui.body_w(420)),
                  actions=[ui.ghost_btn(t("close"), on_click=_close)],
                  width=460)
    try:
        d.on_dismiss = _close
    except Exception:
        pass
    _paint()
    return d


async def _edit_photo(page, name, data, *, square=False):
    """ویرایشگرِ عکس را باز می‌کند و (اسم، بایتِ) نهایی را می‌دهد.

    `None` ⇒ کاربر لغو کرد؛ کلِ کار (فرستادن/آپلود) باید رها شود. اگر خودِ
    ویرایشگر بالا نیاید، همان فایلِ اصلی برمی‌گردد: ویرایش هیچ‌وقت جلوی
    فرستادنِ عکس را نمی‌گیرد. `square=True` برای عکسِ پروفایل (کادرِ ۱:۱).

    عکس را با *مسیرِ محلی* به ویرایشگر می‌دهیم اگر داشته باشیم؛ روی وب که
    مسیری نیست، خودِ بایت‌ها می‌روند (`ft.Image.src` بایت هم می‌پذیرد).
    """
    src = _last_pick.get("path")
    if not (src and os.path.isfile(src)):
        src = data
    try:
        res = await editor.edit_photo(
            page, src, name=name, square=square,
            toast=lambda msg, ok=False: _toast(page, msg, ok=ok))
    except Exception:
        log.exception("photo editor failed")
        _toast(page, t("edit_failed"))
        return name, data
    if res is None:
        return None
    if not res.get("changed"):
        return name, data
    _last_pick["path"] = res.get("path")
    return res.get("name") or name, res.get("data") or data


async def _pick_avatar(page, after=None):
    """عکس پروفایل خودم رو عوض می‌کنه (با کادرِ برشِ ۱:۱)."""
    name, data = await _pick(page, IMG_EXT, t("pick_photo"))
    if not data:
        return
    got = await _edit_photo(page, name, data, square=True)
    if got is None:                     # لغوِ ویرایشگر ⇒ آواتار عوض نمی‌شود
        return
    name, data = got
    try:
        res = await api.upload_avatar_bytes(name, data)
    except Exception as ex:
        _toast(page, _err_text(ex))
        return
    _me["avatar_url"] = res.get("avatar_url")
    _toast(page, t("avatar_changed"), ok=True)
    if after:
        await after(page)

async def _flip_lang(page, code=None, after=None):
    """زبان رو عوض می‌کنه و صفحه رو با جهتِ درست از نو می‌سازه."""
    code = code or ("en" if i18n.lang() == "fa" else "fa")
    if code == i18n.lang() and after is None:
        return
    i18n.set_lang(code)
    page.rtl = i18n.rtl()
    try:
        await prefs.put("lang", code)
    except Exception:
        pass
    await (after or auth_page)(page)


# ═══════════════════════════════════════════════════
#  آدرسِ سرور (برای گوشی و Railway)
# ═══════════════════════════════════════════════════
def server_dialog(page, after=None):
    """پنجره‌ی «آدرس سرور» — روی گوشی راهِ وصل‌شدن به Railway همین است.

    `after(page)` بعد از ذخیره صدا می‌خورد تا صفحه با آدرسِ تازه از نو
    ساخته شود (اتصالِ WebSocket هم باید از نو برقرار شود).
    """
    addr = ui.field(t("server"), hint=t("server_hint"),
                    value=config.api_base(), prefix=ui.vic("globe", 18))
    note = ft.Text("", size=fs(12), color=P["sub"])

    async def _test(e=None):
        note.value = t("wait")
        note.color = P["sub"]
        page.update()
        try:
            good = await api.health(addr.value)
        except Exception:
            good = False
        note.value = t("server_ok") if good else t("server_bad")
        note.color = P["ok"] if good else P["acc2"]
        page.update()

    async def _save(e=None):
        url = config.normalize(addr.value) or config.normalize(
            config.DEFAULT_API_BASE)
        # ⚠️ ترتیب مهم است: `prefs.put` سرویسِ اعلانِ بومی را هم از نو
        # همگام می‌کند و نشانی‌ها را از `config` می‌گیرد، پس `set_base` باید
        # *پیش* از آن انجام شود وگرنه سرویس نشانیِ کهنه را می‌گیرد.
        config.set_base(url)
        await prefs.put("server", url)
        api.rebase()
        page.pop_dialog()
        _toast(page, f"{t('server_saved')}\n{config.api_base()}", ok=True)
        try:
            await ws_client.disconnect()
        except Exception:
            pass
        if after is not None:
            await after(page)

    def _local(e=None):
        # سرورِ روی همین کامپیوتر (برای آزمایشِ محلی)
        addr.value = config.LOCAL_API_BASE
        page.update()

    def _cloud(e=None):
        # سرورِ اصلی روی Railway — همان پیش‌فرضِ برنامه
        addr.value = config.DEFAULT_API_BASE
        page.update()

    def _link(label, fn, color=None):
        return ft.TextButton(
            label, on_click=fn,
            style=ft.ButtonStyle(
                color=color or P["sub"],
                shape=ft.RoundedRectangleBorder(radius=12)))

    ui.dialog(
        page, t("server"),
        ft.Container(
            ft.Column([addr, ui.spacer(h=10),
                       ft.Text(t("server_help"), size=fs(12), color=P["sub"]),
                       ui.spacer(h=8),
                       ft.Row([_link(t("server_default"), _cloud, P["acc"]),
                               _link(t("server_local"), _local),
                               _link(t("server_test"),
                                     lambda e: page.run_task(_test), P["acc"])],
                              spacing=4, wrap=True),
                       note],
                      spacing=0, tight=True),
            width=ui.body_w(320)),
        actions=[ui.ghost_btn(t("cancel"), on_click=lambda e: page.pop_dialog()),
                 ui.primary_btn(t("save"), width=110, height=40,
                                on_click=lambda e: page.run_task(_save))],
        width=380)


# ═══════════════════════════════════════════════════
#  ورود / ثبت‌نام
# ═══════════════════════════════════════════════════
async def auth_page(page, mode="login"):
    _detach_ws()
    _player.clear()
    is_login = mode == "login"
    # صفحه‌ی ورود همیشه با پوسته‌ی پیش‌فرضِ آبیِ تلگرامی باز می‌شه؛
    # پوسته‌ی خودِ کاربر بعد از ورودِ موفق برمی‌گرده (_boot).
    _auth_theme()

    user_f = ui.field(t("username"), prefix=ui.vic("user", 18))
    name_f = ui.field(t("display_name"), prefix=ui.vic("pencil", 18))
    pass_f = ui.field(t("password"), password=True, prefix=ui.vic("lock", 18))
    # کدِ دعوت: از رباتِ تلگرام (یک‌بارمصرف) یا کدِ مدیر. فقط سرِ ثبت‌نام.
    ref_f = ui.field(t("referral_code"), prefix=ui.vic("key", 18))
    bot = {"link": "", "required": True, "can_request": False,
           "can_otp": False, "mode": "auto", "cap": 0}

    # «دریافت کد» کنارِ خودِ فیلد: سرور یک کدِ یک‌بارمصرفِ ۵ حرفی می‌سازد و
    # همان لحظه به تلگرامِ مدیر می‌فرستد. کد در پاسخِ HTTP نمی‌آید، پس این
    # دکمه راهِ دورزدنِ دعوت‌نامه نیست.
    req_lbl = ft.Text(t("request_code"), size=fs(12.5), color="#ffffff",
                      font_family=ui.FONT_MED, no_wrap=True)
    req_btn = ft.Container(
        ft.Row([ui.vic("send", 14, "#ffffff"), req_lbl], spacing=5, tight=True,
               alignment=ft.MainAxisAlignment.CENTER),
        height=44, padding=PAD(14, 0, 14, 0), alignment=AL.CENTER,
        border_radius=BR(14, 14, 14, 14), ink=True, tooltip=t("request_code"),
        gradient=ft.LinearGradient(begin=AL.CENTER_LEFT, end=AL.CENTER_RIGHT,
                                   colors=[P["acc2"], P["acc"]]))
    ref_row = ft.Row([ref_f, req_btn], spacing=8,
                     vertical_alignment=ft.CrossAxisAlignment.CENTER)

    # ── ۱.۵: دو راهِ گرفتنِ کد ───────────────────────────────────
    # «کدِ خودکار»: نامِ کاربریِ تلگرامت را می‌دهی و کد یک‌راست به تلگرامِ
    # *خودت* می‌رسد — ولی تنها اگر پیش‌تر به ربات /start داده باشی. ربات
    # نمی‌تواند به کسی که شروعش نکرده پیام بدهد؛ محدودیتِ خودِ تلگرام است،
    # نه ما، و سرور همان را با ۴۰۴ روشن می‌گوید.
    # «درخواست از مدیر»: مثلِ ۱.۴ — کد به تلگرامِ مدیر می‌رود و او می‌فرستد.
    tg_f = ui.field(t("tg_username"), hint=t("tg_username_hint"),
                    prefix=ui.vic("send", 18))
    tg_wrap = ft.Container(tg_f, visible=False)
    _seg_txt: dict = {}
    # هر فیلد در یک قابِ فاصله‌دار می‌نشیند؛ این فهرست نگه‌شان می‌دارد تا وقتی
    # فیلد پنهان شد، قابش هم پنهان شود — وگرنه فاصله‌ی زیرش جا می‌ماند.
    _wraps: list = []

    def _seg(key, label):
        txt = ft.Text(label, size=fs(12), font_family=ui.FONT_MED,
                      color=P["sub"], no_wrap=True)
        _seg_txt[key] = txt
        return ft.Container(txt, expand=True, height=34, alignment=AL.CENTER,
                            border_radius=BR(11, 11, 11, 11), ink=True,
                            animate=anim(FAST, EASE),
                            on_click=lambda e, k=key: _pick_mode(k))

    seg_auto, seg_man = _seg("auto", t("otp_auto")), _seg("manual",
                                                          t("otp_manual"))
    seg_hint = ft.Text(t("otp_auto_hint"), size=fs(10.5), color=P["dim"],
                       text_align=ft.TextAlign.CENTER)
    seg_row = ft.Container(
        ft.Column([ft.Container(ft.Row([seg_auto, seg_man], spacing=3),
                                padding=PAD(3, 3, 3, 3), bgcolor=op(0.06),
                                border_radius=BR(14, 14, 14, 14)),
                   ui.spacer(h=5), seg_hint], spacing=0, tight=True),
        visible=False)

    def _paint_mode():
        """رنگِ دو تکه و پیدا/پنهانِ فیلدِ نامِ تلگرام را با حالت هم‌آهنگ می‌کند."""
        auto = bot["mode"] == "auto"
        for key, box in (("auto", seg_auto), ("manual", seg_man)):
            on = (key == "auto") == auto
            box.bgcolor = P["acc"] if on else op(0.0)
            _seg_txt[key].color = "#ffffff" if on else P["sub"]
        seg_hint.value = t("otp_auto_hint") if auto else t("otp_manual_hint")
        tg_wrap.visible = auto and bot["required"] and bot.get("can_otp", False)
        for box, kid in _wraps:                  # فاصله‌ی فیلدهای پنهان نماند
            box.visible = kid.visible

    def _pick_mode(key):
        if bot["mode"] == key:
            return
        bot["mode"] = key
        msg.value = ""
        _paint_mode()
        try:
            page.update()
        except Exception:
            pass

    async def _request_code(e=None):
        if bot.get("busy"):
            return
        auto = bot["mode"] == "auto" and bot.get("can_otp", False)
        uname = (tg_f.value or "").strip().lstrip("@")
        if auto and not uname:
            msg.value = t("need_tg_username")
            msg.color = P["acc2"]
            _focus(page, tg_f)
            page.update()
            return
        bot["busy"] = True
        req_lbl.value = t("requesting_code")
        msg.value = ""
        try:
            page.update()
        except Exception:
            pass
        try:
            if auto:
                got = await api.referral_otp(uname)
                left, cap = got.get("left"), got.get("cap")
                tail = (f" — {t('otp_left', n=left, cap=cap)}"
                        if isinstance(left, int) and cap else "")
                msg.value = f"{t('code_sent_you')}{tail}"
            else:
                await api.referral_request()
                msg.value = f"{t('code_sent_tg')} — {t('code_sent_hint')}"
            msg.color = P["acc"]
            _focus(page, ref_f)
        except Exception as ex:
            msg.value = _err_text(ex)
            msg.color = P["acc2"]
        finally:
            bot["busy"] = False
            req_lbl.value = t("request_code")
            try:
                page.update()
            except Exception:
                pass

    req_btn.on_click = lambda e: page.run_task(_request_code)

    bot_btn = ft.TextButton(
        t("get_code_from_bot"),
        style=ft.ButtonStyle(color=P["acc"],
                             shape=ft.RoundedRectangleBorder(radius=12)))
    bot_row = ft.Container(
        ft.Row([ui.vic("send", 14, P["acc"]), bot_btn], spacing=4, tight=True,
               alignment=ft.MainAxisAlignment.CENTER),
        visible=False, margin=MG(0, 0, 0, 6))

    def _open_bot(e=None):
        if bot["link"]:
            _open_url_bg(page, bot["link"])
    bot_btn.on_click = _open_bot

    async def _load_ref_info():
        """از سرور می‌پرسد کدِ دعوت لازم است و ربات کدام است."""
        info = await api.referral_info()
        if not info:
            return
        bot["required"] = bool(info.get("required", True))
        bot["link"] = info.get("bot_link") or ""
        bot["can_request"] = bool(info.get("can_request"))
        bot["can_otp"] = bool(info.get("can_otp"))
        bot["cap"] = int(info.get("account_cap") or 0)
        ref_row.visible = bot["required"]
        # سرورِ قدیمی `can_request` ندارد؛ آن‌وقت دکمه را نشان می‌دهیم و اگر
        # واقعاً نشد، خودِ خطای سرور در `msg` می‌نشیند.
        req_btn.visible = bot["required"] and (
            bot["can_request"] or bot["can_otp"] or "can_request" not in info)
        bot_row.visible = bool(bot["link"]) and bot["required"]
        # انتخابِ خودکار/دستی را فقط وقتی نشان بده که هر دو راه باز باشد؛
        # اگر یکی بسته است، بی‌سروصدا همان راهِ باز را بگیر.
        seg_row.visible = bot["required"] and bot["can_otp"] and bot["can_request"]
        bot["mode"] = "auto" if bot["can_otp"] else "manual"
        _paint_mode()
        if info.get("bot"):
            bot_btn.text = t("get_code_from_bot_at", bot=f"@{info['bot']}")
        try:
            page.update()
        except Exception:
            pass

    msg = ft.Text("", size=fs(12), color=P["acc2"], text_align=ft.TextAlign.CENTER)

    async def _submit(e=None):
        u = (user_f.value or "").strip().lstrip("@").lower()
        p = pass_f.value or ""
        n = (name_f.value or "").strip()
        rc = (ref_f.value or "").strip()
        if not u or not p or (not is_login and not n):
            msg.value = t("fill_all")
            page.update()
            return
        if (not is_login) and bot["required"] and not rc:
            msg.value = t("need_referral")
            page.update()
            return
        msg.value = t("wait")
        page.update()
        try:
            if is_login:
                await api.login(u, p)
            else:
                await api.register(u, n, p, rc)
        except Exception as ex:
            msg.value = _err_text(ex)
            page.update()
            return
        # نشست ماندگار می‌شه: دفعه‌ی بعد بی‌ورود می‌آی تو
        await prefs.save_session({
            "token": api.token, "user_id": api.user_id,
            "username": api.username, "display_name": api.display_name})
        await _boot(page)

    fields = [user_f, pass_f] if is_login else [user_f, name_f, pass_f]

    for f in fields:
        f.on_submit = lambda e: page.run_task(_submit)
    ref_f.on_submit = lambda e: page.run_task(_submit)
    # اینتر در فیلدِ نامِ تلگرام یعنی «کد را برایم بفرست»، نه «ثبت‌نام کن»
    tg_f.on_submit = lambda e: page.run_task(_request_code)
    if not is_login:
        # اول انتخابِ راه، بعد نامِ تلگرام (اگر خودکار بود)، آخر خودِ کد
        fields = fields + [seg_row, tg_wrap, ref_row]

    def _fw(f):
        """قابِ فاصله‌دارِ هر فیلد — و ثبتش، تا پنهان‌شدنش فاصله جا نگذارد."""
        box = ft.Container(f, margin=MG(0, 0, 0, 10))
        _wraps.append((box, f))
        return box

    card = ui.glass(
        ft.Column(
            [ft.Row([ui.logo(84)], alignment=ft.MainAxisAlignment.CENTER),
             ui.spacer(h=12),
             ft.Text(t("app_name"), size=fs(26), weight=W.W_700,
                     font_family=ui.FONT_BOLD, color=P["txt"],
                     text_align=ft.TextAlign.CENTER),
             ft.Text(t("tagline"), size=fs(13), color=P["sub"],
                     text_align=ft.TextAlign.CENTER),
             ui.spacer(h=22),
             *[_fw(f) for f in fields],
             bot_row,
             ui.spacer(h=6),
             ui.primary_btn(t("login") if is_login else t("register"),
                            icon="send" if is_login else "user",
                            on_click=lambda e: page.run_task(_submit)),
             ui.spacer(h=10),
             ft.Row([ft.TextButton(
                 t("no_account") if is_login else t("have_account"),
                 on_click=lambda e: page.run_task(
                     auth_page, page, "register" if is_login else "login"),
                 style=ft.ButtonStyle(
                     color=P["acc"],
                     shape=ft.RoundedRectangleBorder(radius=12)))],
                 alignment=ft.MainAxisAlignment.CENTER),
             msg],
            spacing=0, tight=True,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER),
        radius=28, pad=PAD(26, 30, 26, 22), tint=0.07, sigma=30,
        width=ui.panel_w(380),
    )
    ui.prep_in(card, dy=0.06, scale=0.96)

    lang_btn = ui.icon_btn("globe", tip=t("language"), color=P["sub"],
                           on_click=lambda e: page.run_task(_flip_lang, page))
    # روی گوشی، آدرسِ پیش‌فرضِ ۱۲۷.۰.۰.۱ کار نمی‌کند؛ از همین‌جا عوضش کن
    srv_btn = ui.icon_btn("monitor", tip=t("server"), color=P["sub"],
                          on_click=lambda e: server_dialog(
                              page, after=lambda p: auth_page(p, mode)))
    body = ft.Stack([
        ft.Container(card, alignment=AL.CENTER, expand=True),
        ft.Container(ft.Row([_auth_daynight_btn(page, mode), lang_btn, srv_btn],
                            spacing=2, tight=True),
                     top=10, right=10),
    ], expand=True)
    _go(page, body)
    page.run_task(ui.enter, page, card)
    if not is_login:
        # پرسشِ «کدِ دعوت لازم است؟» پس از نشستنِ صفحه، تا باز شدنش کند نشود
        page.run_task(_load_ref_info)

# ═══════════════════════════════════════════════════
#  بعد از ورود
# ═══════════════════════════════════════════════════
# ═══════════════════════════════════════════════════
#  به‌روزرسانیِ درون‌برنامه‌ای
# ═══════════════════════════════════════════════════
def _mb(n) -> str:
    """بایت → «۱۲.۳ مگابایت» با رقم‌های فارسی."""
    try:
        n = float(n or 0)
    except Exception:
        return ""
    if n <= 0:
        return ""
    if n < 1024 * 1024:
        return i18n.num(f"{n / 1024:.0f} KB")
    return i18n.num(f"{n / (1024 * 1024):.1f} MB")


def _update_dialog(page, info):
    """کارتِ «نسخه‌ی تازه» — دریافت و نصب، همه درونِ برنامه."""
    ver = info.get("version") or ""
    asset = info.get("asset") or {}
    forced = bool(info.get("mandatory"))
    bar = ft.ProgressBar(value=0, width=ui.body_w(320), height=6,
                         bgcolor=op(0.14), color=P["acc"], visible=False)
    note = ft.Text(t("update_avail", v=i18n.num(ver)), size=fs(13.5),
                   color=P["txt"], no_wrap=False)
    sub = ft.Text(" · ".join([x for x in (
        _mb(asset.get("size")) and t("update_size", s=_mb(asset.get("size"))),
        (info.get("notes") or "").strip()) if x]),
        size=fs(12), color=P["sub"], no_wrap=False)
    hint = ft.Text(t("update_forced") if forced else "", size=fs(11.5),
                   color=P["acc2"], no_wrap=False)
    go = ui.primary_btn(t("update_now"))
    later = ui.ghost_btn(t("update_later"), on_click=lambda e: page.pop_dialog())
    state = {"busy": False}

    def _prog(got, total):
        bar.value = (got / total) if total else None
        note.value = t("update_downloading",
                       p=i18n.num(int(got * 100 / total) if total else 0))
        try:
            bar.update()
            note.update()
        except Exception:
            pass

    async def _run():
        if state["busy"]:
            return
        state["busy"] = True
        bar.visible = True
        go.disabled = True
        try:
            page.update()
        except Exception:
            pass
        dest = updater.dest_for(info.get("platform") or "",
                                info.get("version") or "")
        try:
            path = await updater.download(api, asset["url"], dest,
                                          on_progress=_prog,
                                          sha1=asset.get("sha1") or "")
        except ValueError:
            state["busy"] = False
            go.disabled = False
            bar.visible = False
            note.value = t("update_hash_bad")
            try:
                page.update()
            except Exception:
                pass
            return
        except Exception as ex:
            state["busy"] = False
            go.disabled = False
            bar.visible = False
            note.value = t("update_failed")
            _toast(page, _err_text(ex))
            try:
                page.update()
            except Exception:
                pass
            return
        note.value = t("update_installing")
        bar.value = 1.0
        try:
            page.update()
        except Exception:
            pass
        # مجوزِ «نصبِ برنامه‌های ناشناس» را *پیش* از فرستادنِ Intent می‌گیریم.
        # از اندروید ۸ (API 26) بی این مجوز سیستم Intentِ نصب را بی‌صدا رد
        # می‌کند؛ نشانه‌اش همان «دانلود شد ولی نصب شروع نشد» بود. داشتنش در
        # manifest کافی نیست — کاربر باید در پنجره‌ی سیستم تأییدش کند.
        # ردِ کاربر پایانِ کار نیست: فایل سرِ جایش است و مسیرش را می‌گوییم.
        ok_inst = True
        if updater.plat() == "android":
            try:
                ok_inst = await _ask_perm(page, "REQUEST_INSTALL_PACKAGES",
                                         strict=True)
            except Exception:
                ok_inst = True          # افزونه نبود ⇒ بگذار خودِ Intent بگوید
        handed = (await asyncio.to_thread(updater.install, path)
                  if ok_inst else False)
        if not handed:
            # نصبِ خودکار نشد؟ دروغ نمی‌گوییم؛ مسیرِ فایل را می‌دهیم.
            note.value = (t("update_need_install_perm") if not ok_inst
                          else t("update_open_manual"))
            sub.value = t("update_saved_at", p=path)
            bar.visible = False
            go.disabled = False
            state["busy"] = False
            try:
                page.update()
            except Exception:
                pass
            return
        note.value = t("update_restart")
        try:
            page.update()
        except Exception:
            pass
        if updater.plat() == "windows":
            await asyncio.sleep(0.8)
            try:
                page.window.close()
            except Exception:
                os._exit(0)

    go.on_click = lambda e: page.run_task(_run)
    body = ft.Column([note, sub, hint, ui.spacer(h=10), bar],
                     spacing=6, tight=True)
    acts = [go] if forced else [later, go]
    ui.dialog(page, t("update_title"), body, actions=acts)


async def _update_check(page, manual=False):
    """بررسیِ نسخه؛ خودکار در شروع (بی‌صدا) یا دستی از تنظیمات.

    در مرورگر چیزی برای نصب نیست: نسخه‌ی وب با هر بار باز شدنِ صفحه خودش
    تازه می‌شود. پیشترها همین‌جا «آخرین نسخه را داری» گفته می‌شد، که دروغِ
    سفید بود — چون `updater.plat()` روی وب `""` است و اصلاً چیزی بررسی
    نشده بود. حالا راستش را می‌گوییم و به دو ردیفِ دانلودِ بالای همین
    صفحه اشاره می‌کنیم.
    """
    if _web["on"]:
        if manual:
            _toast(page, t("update_web"), ok=True)
        return
    if manual:
        _toast(page, t("update_checking"))
    info = await updater.check(api, APP_VERSION)
    if not info:
        if manual:
            _toast(page, t("update_latest"), ok=True)
        return
    _update_dialog(page, info)


def _push_tap(page) -> bool:
    """کاربر روی اعلانِ سرویسِ بومی زده بود؟ همان چت را باز کن.

    سرویسِ کاتلین شناسه‌ی چت را در `open.json` می‌گذارد و از دنیای پایتون
    خبر ندارد؛ پس *ما* باید سراغش برویم — یک‌بار سرِ بالا آمدنِ برنامه و
    یک‌بار هر بار که از پس‌زمینه برمی‌گردیم (ضربه روی اعلان همین کار را
    می‌کند). `take_tap` خودش فایل را پاک و ضربه‌های کهنه را رد می‌کند، پس
    صدا زدنش بی‌خطر و بی‌هزینه است.
    """
    try:
        cid = push.take_tap()
    except Exception:
        return False
    if not cid and _web["on"]:
        # در مرورگر فایلی نیست: ورکرِ اعلان `?chat=` را در نشانی گذاشته
        try:
            cid = push.take_url_tap(getattr(page, "url", "") or "")
        except Exception:
            cid = ""
    if not cid or not api.token:
        return False
    try:
        page.run_task(chat_page, page, cid)
        return True
    except Exception:
        return False


async def _boot(page, me: dict | None = None):
    _restore_theme()          # پوسته‌ی ذخیره‌شده‌ی کاربر برمی‌گرده
    # `me` همان پاسخِ `verify()` است: یک رفت‌وبرگشتِ کمتر در شروعِ برنامه.
    # نبودش هم دلیلِ انتظار نیست تا وقتی نسخه‌ای در انبار داریم؛ فقط
    # شروعِ *اولِ* برنامه (انبارِ خالی) مهلت می‌گیرد، چون آن‌جا نامِ خودم را
    # از هیچ‌جای دیگری نمی‌دانم.
    if me:
        _me.update(me)
    elif not await _seed_me():
        try:
            await asyncio.wait_for(_load_me(), timeout=6.0)
        except Exception:
            pass
    try:
        await ws_client.connect(api.token)
    except Exception:
        pass
    # از این‌جا به بعد شناسه‌ی خودم دستِ اعلان‌گر است: پیامِ خودم — که سوکت
    # به همه‌ی دستگاه‌هایم پس می‌فرستد — نباید اعلان بدهد.
    notify.set_me(api.user_id or "")
    notify.on_open(lambda cid: page.run_task(chat_page, page, cid))
    await chat_list_page(page)
    # ضربه‌ی اعلانِ بومی که برنامه را *از صفر* باز کرده: تا این‌جا فهرست
    # چیده شده، پس بازکردنِ چت رویش می‌نشیند و دکمه‌ی برگشت درست کار می‌کند.
    _push_tap(page)
    # بررسیِ نسخه پس از بالا آمدنِ صفحه — بی‌صدا، تا شروع را کند نکند
    page.run_task(_update_check, page, False)


def _splash(page):
    """صفحه‌ی «یه لحظه…» تا وقتی نشستِ ذخیره‌شده سنجیده می‌شود.

    بی این، در شروعِ برنامه یک لحظه صفحه‌ی ورود می‌پرید و بعد ناپدید می‌شد؛
    کاربر باید حس کند همان‌طور که بود وارد مانده، نه اینکه دوباره لاگین شد.
    """
    _restore_theme()
    card = ft.Column(
        [ui.logo(96), ui.spacer(h=18),
         ft.Text(t("app_name"), size=fs(22), weight=W.W_700,
                 font_family=ui.FONT_BOLD, color=P["txt"]),
         ui.spacer(h=16),
         ft.ProgressRing(width=26, height=26, stroke_width=2.5,
                         color=P["acc"])],
        spacing=0, tight=True,
        horizontal_alignment=ft.CrossAxisAlignment.CENTER)
    _go(page, ft.Container(card, alignment=AL.CENTER, expand=True))


async def _verify_bg(page) -> None:
    """اعتبارِ نشست، *پشتِ صحنه* — چون برنامه با انبارِ محلی باز شده.

    سه سرانجام:
      • معتبر ⇒ `_me` تازه می‌شود و نشست از نو ذخیره (توکنِ چرخیده).
      • باطل  ⇒ `api.verify` خودش توکن را دور ریخته؛ ما هم نشست را پاک
        می‌کنیم، انبار را می‌شوییم (پیام‌های کسی روی دستگاه نماند) و
        صفحه‌ی ورود را می‌آوریم.
      • شبکه نبود ⇒ هیچ. همان نسخه‌ی محلی سرِ جایش می‌ماند؛ این حالتِ
        *معمولِ* یک اتصالِ فیلترشده است، نه خطا.
    """
    uid = api.user_id          # ⚠️ پیش از verify؛ ۴۰۱ خالی‌اش می‌کند
    try:
        me = await asyncio.wait_for(api.verify(), timeout=20.0)
    except Exception:
        return
    if me:
        _net_mark(True)
        _me.update(me)
        page.run_task(store.save_blob, "me", dict(_me))
        try:
            await prefs.save_session({
                "token": api.token, "user_id": api.user_id,
                "username": api.username, "display_name": api.display_name})
        except Exception:
            pass
        return
    if api.token:
        return          # جوابِ مشکوک ولی توکن پابرجاست ⇒ دست نگه می‌داریم
    try:
        await store.clear(uid)
    except Exception:
        pass
    await prefs.clear_session()
    _toast(page, t("session_gone"))
    await auth_page(page)


async def _resume_session(page) -> bool:
    """ورودِ خودکار: اگر نشستِ ذخیره‌شده معتبر باشد صفحه‌ی ورود رد می‌شود.

    ⚡ قاعده‌ی ۱.۶ — «نت برای داده است، نه برای رابط»: تا وقتی انبارِ محلی
    چیزی دارد، برنامه *بی هیچ انتظاری* باز می‌شود و درستیِ نشست پشتِ صحنه
    سنجیده می‌شود (`_verify_bg`). پیش از این هر بار بالا آمدنِ برنامه یک
    `/profile/me` سرِ راه بود با مهلتِ ۸ ثانیه: روی نتِ فیلترشده — که آویزان
    می‌ماند نه اینکه رد شود — هشت ثانیه فقط لوگو دیده می‌شد، حتی با نشستِ
    سالم و انبارِ پر.

    شروعِ اولِ برنامه (انبارِ خالی) همان راهِ قدیم را می‌رود، چون آن‌جا
    چیزی برای نشان‌دادن نداریم:
      • معتبر  ⇒ می‌رویم داخلِ برنامه (True)
      • ۴۰۱/۴۰۳ ⇒ نشست باطل شده، پاکش می‌کنیم و صفحه‌ی ورود (False)
      • خطای شبکه ⇒ صفحه‌ی ورود، چون هیچ نسخه‌ی محلی‌ای نیست
    """
    if not api.adopt(prefs.session()):
        return False
    _splash(page)
    if await store.load_chats():
        await _boot(page)
        page.run_task(_verify_bg, page)
        return True
    try:
        me = await asyncio.wait_for(api.verify(), timeout=8.0)
    except Exception:
        me = None
    if not me:
        if not api.token:            # verify خودش توکنِ باطل را دور ریخت
            await prefs.clear_session()
        return False
    await prefs.save_session({
        "token": api.token, "user_id": api.user_id,
        "username": api.username, "display_name": api.display_name})
    await _boot(page, me)
    return True


async def _logout(page):
    _detach_ws()
    _player.clear()
    try:
        await ws_client.disconnect()
    except Exception:
        pass
    # انبارِ محلی *پیش از* `api.logout()` پاک می‌شود: کلیدهایش به شناسه‌ی
    # کاربر بسته‌اند و بعد از خروج، `api.user_id` خالی است و دیگر پیدا
    # نمی‌شدند — یعنی پیام‌های نفرِ قبلی روی دستگاه می‌ماندند.
    try:
        await store.clear()
    except Exception:
        pass
    api.logout()
    # نشستِ ماندگار هم پاک می‌شه؛ فقط همین‌جا (خروج از حساب)
    await prefs.clear_session()
    _me.clear()
    await auth_page(page)


# ── «پیام‌های ذخیره‌شده» ─────────────────────────────
# سرور این چت را با `kind == "saved"` می‌فرستد و عنوانش را فارسی گذاشته تا
# نسخه‌های قدیمیِ برنامه هم چیزی برای نشان‌دادن داشته باشند. کلاینتِ تازه
# عنوان را خودش به زبانِ کاربر می‌نویسد، پس این‌جا هرجا اسمِ چت لازم است
# `_chat_title` صدا زده می‌شود نه `c["title"]`ِ خام.
KIND_SAVED = "saved"


def _is_saved(c) -> bool:
    """این ردیفِ چت همان دفترچه‌ی یادداشتِ خودم است؟"""
    if not isinstance(c, dict):
        return False
    return (c.get("kind") or "") == KIND_SAVED


def _chat_title(c) -> str:
    return t("saved_messages") if _is_saved(c) else (c.get("title") or "")


def _sort_chats(chats):
    rows = sorted(chats, key=lambda c: c.get("last_message_at") or "",
                  reverse=True)
    # دفترچه‌ی یادداشت همیشه بالای فهرست (مثلِ تلگرام). سرور هم همین کار را
    # می‌کند، ولی مرتب‌سازیِ محلی بعدِ هر پیامِ تازه دوباره اجرا می‌شود و
    # بی این خط، یادداشتِ چندروزه‌ی نخورده ته فهرست سُر می‌خورد.
    return ([c for c in rows if _is_saved(c)]
            + [c for c in rows if not _is_saved(c)])


def _preview(c):
    """متنِ زیرِ اسمِ چت در لیست."""
    kind = c.get("last_message_type") or "text"
    body = c.get("last_message") or ""
    if kind == "image":
        body = t("photo")
    elif kind == "voice":
        body = t("voice_message")
    elif kind == "file":
        body = _fname(body)
    elif kind == "text" and geo.parse(body):
        # پیامِ موقعیت دو خط است و خطِ دومش یک لینکِ بلندِ گوگل‌مپ. در لیستِ
        # چت‌ها همان لینک تا آخرِ سطر کشیده می‌شد و اسمی از موقعیت نمی‌ماند.
        body = f"📍 {t('att_location')}"
    if not body:
        # دفترچه‌ی یادداشتِ خالی توضیحِ خودش را نشان می‌دهد، نه سطرِ سفید.
        return t("saved_hint") if _is_saved(c) else ""
    body = body.replace("\n", " ")
    if c.get("last_sender_id") and c["last_sender_id"] == api.user_id:
        # در دفترچه‌ی یادداشت همه‌ی پیام‌ها از خودم است؛ «تو:» سرِ هرکدام
        # فقط جا می‌گیرد.
        return body if _is_saved(c) else f"{t('you')}: {body}"
    return body

# نسلِ صفحه — تسک‌های پس‌زمینه با عوض شدن صفحه خودشون رو می‌بندن
_gen = {"n": 0}


def _fresh():
    _gen["n"] += 1
    return _gen["n"]


def _alive(n):
    return _gen["n"] == n


# شمارِ کلِ نخونده‌ها — نشانِ زبانه‌ی «گفت‌وگوها» در نوارِ پایین
_unread = {"n": 0}


def _unread_total(chats=None) -> int:
    """جمعِ پیام‌های نخونده. با فهرستِ چت‌ها تازه می‌شه، بی‌آن از کَش می‌خونه."""
    if chats is not None:
        _unread["n"] = sum(int(c.get("unread") or 0) for c in chats
                           if not c.get("archived"))
    return int(_unread["n"])


# ── نوارِ پایین (چهار زبانه، مثلِ تلگرامِ اندروید) ─────
NAV_H = 74          # جای خالی‌ای که لیست‌ها باید زیرِ خودشون بذارن


def _nav(page, active):
    """نوارِ شناورِ پایین: گفت‌وگوها / مخاطبین / تنظیمات / حسابِ من."""
    n = _unread_total() if prefs.flag("badges") else 0

    def _tab(icon, label, key, fn, count=0):
        on = (active == key)
        return ui.nav_tab(icon, label, active=on, count=count,
                          on_click=(None if on
                                    else (lambda e: page.run_task(fn, page))))

    return ui.nav_bar(
        _tab("chat", t("chats"), "chats", chat_list_page, count=n),
        _tab("contacts", t("contacts"), "contacts", contacts_page),
        _tab("settings", t("settings"), "settings", settings_page),
        _tab("user", t("my_account"), "profile", profile_page))


async def _nav_sync(page, holder, active, gen):
    """در صفحه‌هایی که فهرستِ چت ندارن، نشانِ نخونده‌ها را از سرور می‌گیره."""
    old = _unread_total()
    try:
        chats = await api.list_chats()
    except Exception:
        return
    if not _alive(gen) or _unread_total(chats) == old:
        return
    holder.content = _nav(page, active).content
    try:
        page.update()
    except Exception:
        pass


# ═══════════════════════════════════════════════════
#  استوری (۲۴ساعته)
# ═══════════════════════════════════════════════════
STORY_SECS = 6.0          # مدتِ نمایشِ هر استوریِ عکسی


def _story_left(iso) -> str:
    """از ۲۴ ساعتِ استوری چقدر مانده — «۵ ساعت مانده» / «۱۲ دقیقه مانده»."""
    d = _local(iso)
    if not d:
        return ""
    secs = (d - datetime.now(timezone.utc).astimezone()).total_seconds()
    if secs <= 0:
        return ""
    if secs >= 3600:
        return t("story_left", h=i18n.num(int(secs // 3600)))
    return t("story_left_min", m=i18n.num(max(1, int(secs // 60))))


async def _story_post(page, owner_kind="user", owner_id=None, after=None):
    """عکس/ویدیو را می‌گیرد، آپلود می‌کند، و استوری را می‌گذارد."""
    name, data = await _pick(page, None, t("story_pick"))
    if not data:
        return
    kind = _kind_for(name)
    if kind not in ("image", "video"):
        _toast(page, t("story_pick"))
        return
    if kind == "image":                 # استوریِ عکسی از ویرایشگر رد می‌شود
        got = await _edit_photo(page, name, data)
        if got is None:
            return
        name, data = got
    _toast(page, t("uploading"))
    try:
        up = await api.upload_media_bytes(name, data)
        await api.add_story(up["url"], media_type=kind,
                            owner_kind=owner_kind, owner_id=owner_id)
    except Exception as ex:
        _toast(page, _err_text(ex))
        return
    _toast(page, t("story_added"), ok=True)
    if after:
        await _await_quiet(after())


def _story_viewer(page, ring, after=None):
    """نمایشگرِ استوری: نوارهای بخش‌بخش بالا، ضربه‌ی چپ/راست برای جابه‌جایی.

    استوری‌ها را خودش از سرور می‌گیرد (نه از حلقه)، پس همیشه تازه‌اند و
    «دیده‌شدن» همان‌جا ثبت می‌شود.
    """
    kind = ring.get("owner_kind") or "user"
    oid = ring.get("owner_id") or ""
    who = ring.get("title") or ""
    box_w = ui.body_w(400)
    box_h = min(max(320.0, ui.vh() * 0.56), 560.0)
    st = {"i": 0, "items": [], "gen": 0, "busy": False}

    bars = ft.Row([], spacing=4, height=3)
    head = ft.Row([], spacing=8,
                  vertical_alignment=ft.CrossAxisAlignment.CENTER)
    stage = ft.Container(width=box_w, height=box_h, alignment=AL.CENTER,
                         border_radius=BR(16, 16, 16, 16),
                         bgcolor=op(0.06),
                         clip_behavior=ft.ClipBehavior.ANTI_ALIAS)
    cap_t = ft.Text("", size=fs(13), color=P["txt"], no_wrap=False)
    cap_box = ft.Container(cap_t, visible=False, padding=PAD(2, 8, 2, 0))
    foot = ft.Row([], spacing=10,
                  vertical_alignment=ft.CrossAxisAlignment.CENTER)

    def _cur():
        items = st["items"]
        return items[st["i"]] if 0 <= st["i"] < len(items) else None

    def _paint_bars():
        n = len(st["items"])
        bars.controls = [
            ft.Container(
                expand=True, height=3, border_radius=BR(3, 3, 3, 3),
                bgcolor=(P["acc"] if k <= st["i"] else op(0.22)))
            for k in range(n)]

    def _stage_for(s):
        """عکس یا ویدیوی استوری، جادادن‌شده در قابِ ثابت (بی‌کشیدگی)."""
        src = _cached_src(s.get("media_url")) or _media(s.get("media_url"))
        if (s.get("media_type") or "image") == "video":
            vid = ui.loop_video(src, width=box_w, height=box_h,
                                fit=ft.BoxFit.CONTAIN, radius=16, muted=False)
            if vid is not None:
                return vid
        return ui.photo(src, width=box_w, height=box_h,
                        fit=ft.BoxFit.CONTAIN, radius=16,
                        error=ft.Container(
                            ui.vic("image", 34, op(0.5)), height=box_h,
                            alignment=AL.CENTER))

    async def _del(s):
        try:
            await api.delete_story(s["id"])
        except Exception as ex:
            _toast(page, _err_text(ex))
            return
        _toast(page, t("story_deleted"), ok=True)
        st["items"] = [x for x in st["items"] if x["id"] != s["id"]]
        if not st["items"]:
            page.pop_dialog()
            await _refresh()
            return
        st["i"] = min(st["i"], len(st["items"]) - 1)
        _paint()
        await _refresh()

    async def _viewers(s):
        try:
            rows = await api.story_viewers(s["id"])
        except Exception as ex:
            _toast(page, _err_text(ex))
            return
        body = [ft.Row([ui.avatar(v.get("display_name") or "?", 34,
                                 url=_media(v.get("avatar_url"))),
                        ft.Column([ft.Text(v.get("display_name") or "",
                                           size=fs(13.5), color=P["txt"],
                                           font_family=ui.FONT_MED),
                                   ft.Text("@" + (v.get("username") or ""),
                                           size=fs(11), color=P["sub"])],
                                  spacing=1, expand=True),
                        ft.Text(_hhmm(v.get("at")), size=fs(11),
                                color=P["sub"])],
                       spacing=10) for v in rows]
        if not body:
            body = [ft.Text(t("story_no_viewers"), size=fs(13),
                            color=P["sub"])]
        ui.dialog(page, t("story_viewers"),
                  ft.Container(ft.Column(body, spacing=10, tight=True,
                                         scroll=ft.ScrollMode.AUTO),
                               width=ui.body_w(360),
                               height=min(360.0, 64.0 * max(1, len(body)))),
                  actions=[ui.ghost_btn(t("close"),
                                        on_click=lambda e: page.pop_dialog())])

    def _paint():
        s = _cur()
        if s is None:
            return
        _paint_bars()
        stage.content = _stage_for(s)
        cap_t.value = s.get("caption") or ""
        cap_box.visible = bool(cap_t.value)
        who_txt = s.get("author_name") or who
        sub = " · ".join([x for x in (_hhmm(s.get("created_at")),
                                      _story_left(s.get("expires_at"))) if x])
        head.controls = [
            ui.avatar(who or who_txt, 34, url=_media(ring.get("avatar_url"))),
            ft.Column([ft.Text(who or who_txt, size=fs(14), color=P["txt"],
                               font_family=ui.FONT_MED, max_lines=1,
                               overflow=ft.TextOverflow.ELLIPSIS),
                       ft.Text(sub, size=fs(11), color=P["sub"])],
                      spacing=1, expand=True)]
        if kind == "chat" and (s.get("author_name") or ""):
            head.controls[1].controls[1].value = " · ".join(
                [x for x in (t("story_by", name=s["author_name"]), sub) if x])
        bits = [ft.Text(t("story_views_n", n=i18n.num(int(s.get("views") or 0))),
                        size=fs(11.5), color=P["sub"])] if s.get("mine") else []
        if s.get("mine"):
            bits.append(ui.icon_btn("eye", tip=t("story_viewers"), box=34,
                                    size=17,
                                    on_click=lambda e, x=s: page.run_task(
                                        _viewers, x)))
            bits.append(ui.icon_btn("trash", tip=t("delete"), box=34, size=17,
                                    color=P["acc2"],
                                    on_click=lambda e, x=s: _confirm(
                                        page, t("delete"), t("story_delete_q"),
                                        lambda: page.run_task(_del, x))))
        foot.controls = bits
        foot.visible = bool(bits)
        page.run_task(_mark_seen, s)
        try:
            page.update()
        except Exception:
            pass

    async def _mark_seen(s):
        if s.get("seen") or not s.get("id"):
            return
        s["seen"] = True
        try:
            res = await api.story_seen(s["id"])
            s["views"] = int(res.get("views") or s.get("views") or 0)
        except Exception:
            s["seen"] = False

    def _step(delta):
        """جلو/عقب؛ ردشدن از آخری = بستنِ نمایشگر."""
        nxt = st["i"] + delta
        if nxt < 0:
            return
        if nxt >= len(st["items"]):
            page.pop_dialog()
            page.run_task(_refresh)
            return
        st["i"] = nxt
        st["gen"] += 1
        _paint()
        page.run_task(_auto, st["gen"])

    async def _auto(gen):
        """پیش‌رفتنِ خودکار — ویدیو کمی بیشتر می‌ماند."""
        s = _cur()
        wait = (STORY_SECS * 2 if (s or {}).get("media_type") == "video"
                else STORY_SECS)
        await asyncio.sleep(wait)
        if gen == st["gen"] and st["items"]:
            _step(1)

    # ضربه‌ی نیمه‌ی راست = بعدی، نیمه‌ی چپ = قبلی (در RTL برعکس)
    fwd, back = ((-1, 1) if i18n.rtl() else (1, -1))
    taps = ft.Row(
        [ft.Container(expand=True, on_click=lambda e: _step(back)),
         ft.Container(expand=True, on_click=lambda e: _step(fwd))],
        spacing=0, expand=True)
    body = ft.Container(
        ft.Column([bars, ui.spacer(h=8), head, ui.spacer(h=8),
                   ft.Stack([stage, ft.Container(taps, width=box_w,
                                                 height=box_h)],
                            width=box_w, height=box_h),
                   cap_box, foot],
                  spacing=0, tight=True),
        width=box_w)
    dlg = ui.dialog(page, ring.get("title") or t("stories"), body,
                    actions=[ui.ghost_btn(t("close"),
                                          on_click=lambda e: _close())])

    def _close(e=None):
        st["gen"] += 1
        page.pop_dialog()
        page.run_task(_refresh)

    async def _refresh():
        """بعدِ بستن، نوارِ حلقه‌ها تازه می‌شود (حلقه‌ی دیده‌شده خاموش شود)."""
        if after:
            await _await_quiet(after())

    async def _load():
        try:
            st["items"] = await api.stories_of(kind, oid)
        except Exception as ex:
            _toast(page, _err_text(ex))
            page.pop_dialog()
            return
        if not st["items"]:
            _toast(page, t("story_none"))
            page.pop_dialog()
            return
        # از نخستین دیده‌نشده شروع کن (مثلِ تلگرام)
        st["i"] = next((k for k, s in enumerate(st["items"])
                        if not s.get("seen")), 0)
        st["gen"] += 1
        _paint()
        page.run_task(_prefetch_all,
                      [s.get("media_url") for s in st["items"]])
        page.run_task(_auto, st["gen"])

    page.run_task(_load)
    return dlg





async def chat_list_page(page, archived=False):
    _detach_ws()
    _player.clear()
    gen = _fresh()
    st = {"chats": [], "q": "", "open": False, "hits": [], "arch": 0,
          "reveal": False, "seq": 0, "nav": None, "nav_n": -1, "rings": [],
          "ring_ix": {}}

    lst = ft.ListView(expand=True, spacing=2,
                      padding=PAD(ui.LIST_PAD, 6, ui.LIST_PAD, 90))
    empty = ft.Container(
        ft.Column([ui.vic("smile", 46, op(0.20)),
                   ui.spacer(h=10),
                   ft.Text(t("no_chats"), size=fs(15), color=P["sub"]),
                   ft.Text(t("no_chats_hint"), size=fs(12), color=P["dim"])],
                  horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                  spacing=2, tight=True),
        alignment=AL.CENTER, expand=True, visible=False)
    sub_txt = ft.Text("", size=fs(11), color=P["acc2"])

    def _sub_sync():
        """زیرعنوانِ صادقِ نوارِ بالا.

        «در حال اتصال» تنها وقتی راهِ شبکه واقعاً بسته است، و «آخرین نسخه‌ی
        ذخیره‌شده» وقتی این لیست از کشِ دیسک آمده — کاربر می‌فهمد چیزی که
        می‌بیند ممکن است چند لحظه عقب باشد، بی اینکه چرخِ انتظاری جلوی کارش
        را بگیرد (خواسته‌ی «رابطِ بی‌وابستگی به شبکه»).
        """
        if _net_down():
            sub_txt.value = t("connecting")
            sub_txt.color = P["acc2"]
        elif getattr(api, "stale", False):
            sub_txt.value = t("offline_cached")
            sub_txt.color = P["sub"]
        else:
            sub_txt.value = ""

    def _match(c):
        q = st["q"].strip().lower().lstrip("@")
        if not q:
            return True
        un = ((c.get("other_user") or {}).get("username") or "").lower()
        hd = (c.get("handle") or "").lower()
        # `_chat_title` نه `title`ِ خام: عنوانِ دفترچه‌ی یادداشت روی سرور
        # فارسی است و در حالتِ انگلیسی با «saved» پیدا نمی‌شد.
        return (q in _chat_title(c).lower() or q in un or q in hd)

    # ── منوی ردیفِ چت (بایگانی / ترک) ────────────────
    def _chat_menu(c):
        cid = c.get("chat_id")
        on = bool(c.get("archived"))

        async def _arch():
            try:
                await api.set_archived(cid, not on)
            except Exception as ex:
                _toast(page, _err_text(ex))
                return
            _toast(page, t("unarchived_done") if on else t("archived_done"),
                   ok=True)
            await _reload()

        async def _leave():
            try:
                await api.leave_chat(cid)
            except Exception as ex:
                _toast(page, t("owner_cant_leave") if "400" in str(ex)
                       else _err_text(ex))
                return
            _toast(page, t("left_group"), ok=True)
            await _reload()

        items = [_mi(page, "archive", t("unarchive") if on else t("archive"),
                     lambda: page.run_task(_arch))]
        if c.get("is_group"):
            items.append(_mi(page, "logout",
                             t("leave_channel")
                             if (c.get("kind") or "") == "channel"
                             else t("leave_group"),
                             lambda: page.run_task(_leave), danger=True))
        _menu_dialog(page, *items)

    # ── حلقه‌ی استوری روی آواتارِ ردیف ────────────────
    # نوارِ بالای صفحه یک حلقه در هر «صاحبِ استوری» دارد؛ همان داده را روی
    # ردیف‌های لیست هم می‌نشانیم تا مثلِ تلگرام بشود. کلیدِ پیوند: چتِ خصوصی
    # با `owner_kind="user"` و شناسه‌ی طرفِ مقابل، گروه/کانال با
    # `owner_kind="chat"` و شناسه‌ی خودِ چت. (سرور در `chat/list` هیچ نشانه‌ای
    # از استوری نمی‌دهد، پس پیوند این‌جا انجام می‌شود — بی درخواستِ اضافه.)
    def _ring_of(c):
        ix = st.get("ring_ix") or {}
        if not ix:
            return None
        ou = c.get("other_user") or {}
        if not c.get("is_group") and ou.get("user_id"):
            return ix.get(("user", str(ou["user_id"])))
        return ix.get(("chat", str(c.get("chat_id") or "")))

    def _av_menu(c, r):
        """ضربه روی آواتار — نه روی خودِ ردیف (آن چت را باز می‌کند).

        با استوریِ فعال: منویِ دو‌گزینه‌ای «دیدنِ استوری / دیدنِ نمایه».
        بی استوری: مستقیم گالریِ عکسِ پروفایل.
        """
        async def _prof():
            """کارتِ نمایه — با گالریِ عکسِ پروفایل در سرش.

            `_photo_viewer` خودش دیالوگ نیست (یک ستون است که داخلِ کارتِ نمایه
            می‌نشیند)، پس مستقیم صدا زدنش هیچ‌چیز باز نمی‌کند؛ راهِ درست همان
            کارتِ نمایه است که اولین چیزش همان گالری است.
            """
            ou = c.get("other_user") or {}
            un = ou.get("username") or ""
            if un and not c.get("is_group"):
                await _show_user_profile(page, un)
                return
            try:
                p = await api.chat_profile(c.get("chat_id"))
            except Exception as ex:
                _toast(page, _err_text(ex))
                return
            _profile_dialog(
                page, p.get("title") or c.get("title") or "",
                p.get("handle"), p.get("bio"),
                avatar_url=p.get("avatar_url") or c.get("avatar_url"),
                is_group=True, members=p.get("members") or [],
                kind=p.get("kind") or c.get("kind"),
                count=p.get("members_count"),
                hidden=bool(p.get("members_hidden")))

        if not r:
            page.run_task(_prof)
            return
        _menu_dialog(
            page,
            _mi(page, "camera", t("story_view"),
                lambda: _story_viewer(page, r, after=_rings_load)),
            _mi(page, "user", t("view_profile"),
                lambda: page.run_task(_prof)))

    def _row(c):
        sav = _is_saved(c)
        title = _chat_title(c)
        ou = c.get("other_user") or {}
        online = (not c.get("is_group")) and _online_of(ou)
        unread = int(c.get("unread") or 0)
        show_badge = unread > 0 and prefs.flag("badges")
        head = [ft.Text(title, size=fs(15), weight=W.W_600,
                        font_family=ui.FONT_MED, color=P["txt"], max_lines=1,
                        overflow=ft.TextOverflow.ELLIPSIS, expand=True)]
        if sav:
            head.insert(0, ui.vic("bookmark", 14, P["acc"]))
        elif (c.get("kind") or "") == "channel":
            head.insert(0, ui.vic("hash", 14, P["sub"]))
        elif c.get("is_group"):
            head.insert(0, ui.vic("users", 14, P["sub"]))
        ring = None if sav else _ring_of(c)
        dot = P["ok"] if online else None
        if sav:
            # دفترچه‌ی یادداشت پروفایل و استوری ندارد؛ نشانِ نشانک روی
            # گرادیانِ ثابت («saved» نه عنوانِ ترجمه‌شده) تا با عوض شدنِ
            # زبان رنگش نپرد. ضربه رویش = بازکردنِ خودش، نه منوی آواتار.
            av = ui.av_slot(ui.avatar(
                "saved", ui.AV, icon="bookmark", tooltip=t("saved_messages"),
                on_click=lambda e, i=c["chat_id"]:
                    page.run_task(chat_page, page, i)))
        else:
            # هیچ حلقه‌ی رنگی‌ای دورِ آواتارِ فهرست نیست — کاربر خواست
            # برداشته شود. استوری هنوز از همین‌جا باز می‌شود (منوی آواتار)،
            # فقط نشانه‌ی دیداری‌اش رفته؛ نوارِ استوری بالای فهرست سرِ جایش
            # است. `av_slot` می‌ماند تا همه‌ی ردیف‌ها هم‌قد بمانند.
            av = ui.av_slot(ui.avatar(
                title, ui.AV, url=_media(c.get("avatar_url")), badge=dot,
                tooltip=t("avatar_menu") if ring else t("view_profile"),
                on_click=lambda e, cc=c, rr=ring: _av_menu(cc, rr)))
        body = ft.Row(
            [av,
             ft.Column(
                 [ft.Row(head, spacing=6),
                  ft.Text(_preview(c), size=fs(13),
                          color=P["txt"] if show_badge else P["sub"],
                          max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)],
                 spacing=3, expand=True,
                 alignment=ft.MainAxisAlignment.CENTER),
             ft.Column(
                 [ft.Text(_hhmm(c.get("last_message_at")), size=fs(11),
                          color=P["sub"]),
                  ui.badge(unread) if show_badge else ui.spacer(h=20)],
                 spacing=5, tight=True,
                 horizontal_alignment=ft.CrossAxisAlignment.END)],
            spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER)
        row = ft.Container(
            body, height=ui.row_h(),
            padding=PAD(ui.LIST_PAD + 2, ui.ROW_VPAD, ui.LIST_PAD + 2,
                        ui.ROW_VPAD),
            border_radius=BR(14, 14, 14, 14), ink=True,
            animate_opacity=MED, animate_offset=MED,
            on_click=lambda e, i=c["chat_id"]: page.run_task(chat_page, page, i))
        ui.hover_tint(row)
        return ft.GestureDetector(
            row, on_secondary_tap=lambda e, cc=c: _chat_menu(cc),
            on_long_press=lambda e, cc=c: _chat_menu(cc))

    # ── ردیفِ پوشه‌ی بایگانی (کشویی، مثلِ تلگرام) ─────
    #
    # ⚠️ این ظرف *یک‌بار* ساخته می‌شود و در `_fill` از نو ساخته نمی‌شود. دلیلش
    # انیمیشن است: فلت (مثلِ فلاتر) تنها چیزی را نرم تغییر می‌دهد که همان شیءِ
    # قبلی باشد. پیش‌تر ردیفِ بایگانی در هر `_fill` از نو ساخته و به لیست
    # اضافه/حذف می‌شد؛ نتیجه‌اش «پریدنِ» ردیف و تکانِ کلِ لیست بود — خواسته‌ی
    # کاربر «باز و بسته شدنِ نرم» است. پس ظرف ثابت می‌ماند و فقط `height` و
    # `opacity`ِ آن از صفر به ارتفاعِ ردیف می‌رود و برمی‌گردد.
    _arch_h = float(max(58.0, fs(14) * 1.45 + fs(12) * 1.45 + 2 + 24))
    arch_body = ft.Container(top=0, left=0, right=0, height=_arch_h)
    # چرا Stack و نه فقط یک ظرفِ ساده: ظرفی که `height` صریح دارد، قیدِ
    # *تنگ* به فرزندش می‌دهد؛ یعنی وسطِ جمع‌شدن، ردیف فشرده می‌شد و فلاتر
    # سرریزِ RenderFlex می‌داد. فرزندِ جای‌دارِ (`top/height`) یک Stack اما
    # اندازه‌ی خودش را نگه می‌دارد و Stack بیرون‌زدگی را می‌بُرد — پس ردیف
    # مثلِ پرده از بالا سُر می‌خورد، نه که لِه شود.
    arch_slot = ft.Container(
        ft.Stack([arch_body], clip_behavior=ft.ClipBehavior.HARD_EDGE),
        height=0.0, opacity=0.0,
        animate=anim(MED, ui.SOFT), animate_opacity=anim(FAST, EASE))

    def _arch_body_fill():
        """محتوای ردیف را تازه می‌کند (شمارِ بایگانی عوض می‌شود)."""
        n = st["arch"]
        arch_body.content = ui.row_item(
            "archive", t("archived_chats"),
            subtitle=(t("archived_n", n=i18n.num(n)) if n else t("no_archived")),
            trailing=(ui.badge(n, muted=True) if n else None),
            on_click=lambda e: page.run_task(chat_list_page, page, True),
            icon_color=P["acc"])

    def _arch_show(on, quiet=False):
        """پوشه‌ی بایگانی را نرم باز/بسته می‌کند (بی بازسازیِ لیست)."""
        on = bool(on) and not archived and not st["q"].strip()
        st["reveal"] = on
        if on:
            _arch_body_fill()
        arch_slot.height = _arch_h if on else 0.0
        arch_slot.opacity = 1.0 if on else 0.0
        if not quiet:
            _safe_update(arch_slot)

    # ── نتیجه‌های سراسری ─────────────────────────────
    def _hit_row(h):
        kind = h.get("kind") or "user"
        ttl = h.get("title") or ""
        tag = ("@" + h["handle"]) if h.get("handle") else (
            ("@" + h["username"]) if h.get("username") else "")
        joined = bool(h.get("joined"))

        async def _go_hit():
            if kind == "user":
                await _open_chat_with(page, h.get("username"))
                return
            if joined and h.get("chat_id"):
                await chat_page(page, h["chat_id"])
                return
            try:
                res = await api.join_chat(chat_id=h.get("chat_id"),
                                          handle=h.get("handle"))
            except Exception as ex:
                _toast(page, _err_text(ex))
                return
            _toast(page, t("joined_done"), ok=True)
            await chat_page(page, res["chat_id"])

        btn = ft.Text(t("open") if (joined or kind == "user") else t("join"),
                      size=fs(12), color=P["acc"], font_family=ui.FONT_MED)
        icon = ("hash" if kind == "channel" else
                "users" if kind == "group" else "user")
        sub = " · ".join([x for x in (tag, (h.get("bio") or "")[:40]) if x])
        row = ft.Container(
            ft.Row([ui.avatar(ttl, 44, url=_media(h.get("avatar_url"))),
                    ft.Column([ft.Row([ui.vic(icon, 13, P["sub"]),
                                       ft.Text(ttl, size=fs(14.5),
                                               color=P["txt"], max_lines=1,
                                               font_family=ui.FONT_MED,
                                               overflow=ft.TextOverflow.ELLIPSIS,
                                               expand=True)], spacing=5),
                               ft.Text(sub, size=fs(11.5), color=P["sub"],
                                       max_lines=1,
                                       overflow=ft.TextOverflow.ELLIPSIS)],
                              spacing=2, expand=True), btn],
                   spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            padding=PAD(ui.LIST_PAD + 2, 9, ui.LIST_PAD + 2, 9),
            border_radius=BR(14, 14, 14, 14), ink=True,
            animate_opacity=MED, animate_offset=MED,
            on_click=lambda e: page.run_task(_go_hit))
        ui.hover_tint(row)
        return row

    # ── نوارِ استوری (حلقه‌های درخشان بالای صفحه‌ی چت) ──
    # وقتی هیچ استوریِ فعالی نیست، کلِ نوار پنهان می‌شود تا یک وجب هم
    # ارتفاع نگیرد؛ پس چیدمانِ لیست دست‌نخورده می‌ماند.
    strip_row = ft.Row([], spacing=12, scroll=ft.ScrollMode.AUTO,
                       vertical_alignment=ft.CrossAxisAlignment.START)
    # ارتفاعِ صریح لازم است: `Row(scroll=...)` در فلاتر یک ListViewِ افقی است و
    # داخلِ یک Column بی‌ارتفاع، قیدِ عمودیِ بی‌کران می‌گیرد؛ آن‌وقت نوار تمامِ
    # بلندیِ صفحه را می‌بلعد و لیستِ چت‌ها، دکمه‌ی «+» و نوارِ پایین به ارتفاعِ
    # صفر می‌رسند — یعنی به‌محضِ آمدنِ اولین استوری، بدنه‌ی صفحه سفید/خالی
    # می‌شد. حلقه (`slot(AV_STORY)` = ۶۴) + ۴ فاصله + ۱۴ نوشته + ۱۴ حاشیه.
    _strip_h = ui.slot(ui.AV_STORY) + 4 + 16 + 14
    strip = ft.Container(strip_row, visible=False, height=_strip_h,
                         padding=PAD(ui.LIST_PAD + 4, 8, ui.LIST_PAD + 4, 6),
                         animate_opacity=anim(FAST, EASE))

    def _ring_cap(txt):
        return ft.Text(txt, size=fs(10.5), color=P["sub"], width=64,
                       max_lines=1, text_align=ft.TextAlign.CENTER,
                       overflow=ft.TextOverflow.ELLIPSIS)

    def _ring_cell(r):
        me = bool(r.get("is_self"))
        title = t("my_story") if me else (r.get("title") or "")
        ring = ui.story_ring(
            r.get("title") or title, ui.AV_STORY,
            url=_media(r.get("avatar_url")),
            unseen=bool(r.get("has_unseen")), plus=me,
            on_click=lambda e, rr=r: _story_viewer(page, rr, after=_rings_load))
        col = ft.Column([ring, _ring_cap(title)], spacing=4, tight=True,
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER)
        if not me:
            return col
        # روی حلقه‌ی خودم: ضربه = دیدن، نگه‌داشتن = گذاشتنِ استوریِ تازه
        return ft.GestureDetector(
            col,
            on_long_press=lambda e: page.run_task(_story_post, page, "user",
                                                  None, _rings_load),
            on_secondary_tap=lambda e: page.run_task(_story_post, page, "user",
                                                     None, _rings_load))

    def _add_cell():
        return ft.Column(
            [ui.story_ring(_me.get("display_name") or "?", ui.AV_STORY,
                           url=_media(_me.get("avatar_url")), unseen=False,
                           plus=True,
                           on_click=lambda e: page.run_task(
                               _story_post, page, "user", None, _rings_load)),
             _ring_cap(t("my_story"))],
            spacing=4, tight=True,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER)

    def _rings_fill():
        rings = st["rings"]
        # نمایه‌ی «صاحب ⇒ حلقه» برای `_ring_of` در ردیف‌های لیست. حلقه‌ی خودم
        # کنار می‌رود؛ چتِ ذخیره‌شده‌ی خودم حلقه نمی‌خواهد.
        st["ring_ix"] = {
            (str(r.get("owner_kind") or "user"), str(r.get("owner_id") or "")): r
            for r in rings if not r.get("is_self")
        }
        show = bool(rings) and not archived and not st["q"].strip()
        strip.visible = show
        strip.opacity = 1.0 if show else 0.0
        if not show:
            strip_row.controls = []
            return
        cells = ([] if any(r.get("is_self") for r in rings) else [_add_cell()])
        strip_row.controls = cells + [_ring_cell(r) for r in rings]

    async def _rings_load():
        if archived:
            return
        try:
            st["rings"] = await api.story_feed()
        except Exception:
            st["rings"] = []
        if not _alive(gen):
            return
        # ردیف‌ها هم از نو ساخته می‌شوند: حلقه‌ها همیشه پس از لیستِ چت‌ها
        # می‌رسند، و بی این بازسازی آواتارِ ردیف‌ها بی‌حلقه می‌ماند تا اولین
        # تازه‌سازیِ بعدی.
        if st["chats"]:
            _fill()
        else:
            _rings_fill()
        try:
            page.update()
        except Exception:
            pass

    def _fill():
        _rings_fill()
        # اعلان‌گر عنوان و آواتار را از همین لیست برمی‌دارد: فریمِ سوکت آواتار
        # ندارد و خواندنِ کشِ دیسکی برای هر پیام یک رفت‌وبرگشتِ اضافه است.
        # فهرستِ بایگانی را نمی‌دهیم — نیمه است و کشِ کامل را خراب می‌کند.
        if not archived:
            notify.set_chats(st["chats"])
        rows = []
        if not archived and not st["q"].strip():
            # ظرفِ بایگانی *همیشه* سرِ لیست است (بسته = ارتفاعِ صفر)، تا باز و
            # بسته شدنش انیمیشن بگیرد و لیست از نو ساخته نشود.
            rows.append(arch_slot)
            if st["reveal"]:
                _arch_body_fill()
        mine = [_row(c) for c in st["chats"] if _match(c)]
        if st["q"].strip() and (mine or st["hits"]):
            if mine:
                rows.append(ui.section(t("my_chats")))
            rows += mine
            if st["hits"]:
                rows.append(ui.section(t("global_results")))
                rows += [_hit_row(h) for h in st["hits"]]
        else:
            rows += mine
        lst.controls = rows
        has = bool(mine or st["hits"])
        lst.visible = bool(has or st["reveal"])
        empty.visible = not has
        if archived:
            empty.content.controls[2].value = t("no_archived")
            empty.content.controls[3].value = ""
        elif not has and st["q"].strip():
            empty.content.controls[2].value = t("nothing_found")
            empty.content.controls[3].value = ""
        else:
            empty.content.controls[2].value = t("no_chats")
            empty.content.controls[3].value = t("no_chats_hint")
        _sub_sync()
        # نشانِ نخونده‌ها روی زبانه‌ی «گفت‌وگوها» همیشه به‌روزه
        nav_box = None
        if not archived:
            n = _unread_total(st["chats"])
            if st["nav"] is not None and n != st["nav_n"]:
                st["nav_n"] = n
                st["nav"].content = _nav(page, "chats").content
                nav_box = st["nav"]
        # ⚠️ ۱.۵: اینجا `page.update()` بود و `_fill` هر ۳۰ ثانیه، سرِ هر
        # رویدادِ سوکت و با هر حرفِ جست‌وجو صدا می‌شود ⇒ کلِ درختِ صفحه هر بار
        # مقایسه می‌شد. فقط همین چند شاخه عوض می‌شوند. (همه‌ی صداها بعدِ `_go`
        # هستند، پس کنترل‌ها به صفحه چسبیده‌اند.)
        _repaint(page, lst, empty, sub_txt, strip, nav_box)

    async def _reload(first=False):
        try:
            st["chats"] = _sort_chats(await api.list_chats(archived=archived))
        except Exception as ex:
            if _net_fail(ex):          # قطعیِ شبکه: زیرعنوان را تازه کن
                _fill()
            _toast(page, _err_text(ex))
            return
        _net_mark(True)
        if not archived:
            try:
                st["arch"] = int(await api.archived_count())
            except Exception:
                st["arch"] = 0
        if not _alive(gen):
            return
        _fill()
        if not archived:
            # انبارِ محلی: باز شدنِ بعدیِ برنامه فوراً همین لیست را دارد
            page.run_task(store.save_chats, st["chats"])
            page.run_task(_rings_load)
        if first and lst.controls:
            for r in lst.controls:
                ui.prep_in(r, dy=0.05, ms=MED)
            try:
                page.update()
            except Exception:
                return
            await ui.enter(page, *lst.controls, stagger=0.028, first_delay=0.0)

    async def _from_cache():
        """لیستِ ذخیره‌شده را *پیش از* پاسخِ سرور نشان می‌دهد.

        روی نتِ کند (یا آفلاین) کاربر دیگر صفحه‌ی خالی نمی‌بیند؛ همان چیزی
        را می‌بیند که بار قبل بود و چند لحظه بعد شبکه رویش تازه می‌شود.
        اگر شبکه زودتر رسیده باشد دست نمی‌زنیم — داده‌ی کهنه روی تازه ننشیند.
        """
        if archived or st["chats"]:
            return
        rows = await store.load_chats()
        if not rows or st["chats"] or not _alive(gen):
            return
        st["chats"] = _sort_chats(rows)
        _fill()
        try:
            page.update()
        except Exception:
            pass

    # ── جست‌وجوی سراسری (با تأخیرِ کوتاه) ─────────────
    async def _search_soon(q):
        st["seq"] += 1
        mine = st["seq"]
        await asyncio.sleep(0.28)
        if mine != st["seq"] or not _alive(gen):
            return
        if not q.strip():
            st["hits"] = []
            _fill()
            return
        try:
            hits = await api.search_all(q.strip())
        except Exception:
            hits = []
        if mine != st["seq"] or not _alive(gen):
            return
        own = {c.get("chat_id") for c in st["chats"]}
        st["hits"] = [h for h in hits
                      if not (h.get("chat_id") and h["chat_id"] in own)
                      and h.get("username") != api.username]
        _fill()

    async def on_ws(data):
        if not _alive(gen):
            return
        typ = data.get("type")
        if typ == "ws_state":
            if data.get("online"):
                _net_mark(True)
            _sub_sync()
            try:
                page.update()
            except Exception:
                pass
            return
        if typ in ("resync", "chat_created", "chat_updated", "chat_removed"):
            await _reload()
            return
        if typ == "story":
            # استوریِ تازه‌ی یکی از مخاطب‌ها ⇒ حلقه‌ها را تازه کن
            await _rings_load()
            return
        if typ == "presence":
            # نقطه‌ی آنلاین بی‌درنگ عوض می‌شود (نه با تیکِ ۳۰ ثانیه‌ای)
            uid = data.get("user_id")
            hit = False
            for c in st["chats"]:
                ou = c.get("other_user") or {}
                if ou.get("user_id") == uid:
                    ou["online"] = bool(data.get("online"))
                    if data.get("last_seen_at"):
                        ou["last_seen_at"] = data["last_seen_at"]
                    hit = True
            if hit:
                _fill()
            return
        if typ == "profile_updated":
            uid = data.get("user_id")
            touched = False
            for c in st["chats"]:
                ou = c.get("other_user") or {}
                if ou.get("user_id") == uid:
                    ou["display_name"] = data.get("display_name") or ou.get("display_name")
                    ou["username"] = data.get("username") or ou.get("username")
                    ou["avatar_url"] = data.get("avatar_url")
                    c["title"] = ou["display_name"] or c.get("title")
                    c["avatar_url"] = data.get("avatar_url")
                    touched = True
            if uid == api.user_id:
                _me["display_name"] = data.get("display_name") or _me.get("display_name")
                _me["username"] = data.get("username") or _me.get("username")
                _me["avatar_url"] = data.get("avatar_url")
                _me["photos"] = data.get("photos") or []
                me_av.content = ui.avatar(
                    _me.get("display_name") or "?", 38,
                    url=_media(_me.get("avatar_url"))).content
                touched = True
            if touched:
                _fill()
            return
        if typ != "message":
            return
        cid = data.get("chat_id")
        hit = next((c for c in st["chats"] if c.get("chat_id") == cid), None)
        if hit is None:
            await _reload()   # چتِ تازه — کلِ لیست از سرور خونده می‌شه
            return
        hit["last_message"] = data.get("content") or ""
        hit["last_message_type"] = data.get("msg_type") or "text"
        hit["last_sender_id"] = data.get("sender_id")
        hit["last_message_at"] = data.get("created_at")
        if data.get("sender_id") != api.user_id:
            hit["unread"] = int(hit.get("unread") or 0) + 1
        st["chats"] = _sort_chats(st["chats"])
        _fill()

    async def _ticker():
        """هر ۳۰ ثانیه نقطه‌های آنلاین و ساعت‌ها تازه می‌شن."""
        while _alive(gen):
            await asyncio.sleep(30)
            if not _alive(gen):
                return
            _fill()

    def _q_change(e):
        st["q"] = search_f.value or ""
        _fill()
        page.run_task(_search_soon, st["q"])

    # ── کشیدن و نگه‌داشتن (۱٫۵ ثانیه) برای بایگانی ───
    # خواسته‌ی نسخه‌ی ۱.۵: «کشیدن از بالای لیست به پایین به‌مدتِ ۱٫۵ ثانیه
    # پوشه‌ی بایگانی را بالای لیست باز کند؛ اسکرول به پایین نرم جمعش کند.»
    # پیش‌تر ۳ ثانیه بود که کاربر گفت زیاد است. نوارِ راهنما تا انگشت پایین
    # نگه داشته شده پر می‌شود؛ آستانه‌ی زمانی جلوی بازشدنِ تصادفی را در
    # اسکرول‌های تند می‌گیرد.
    PULL_HOLD = 1.5
    # آستانه‌ی جمع‌شدن ≈ ارتفاعِ خودِ ردیف: همان لحظه‌ای که ردیف از بالای
    # قاب بیرون می‌رود جمع می‌شود، پس جابه‌جاییِ محتوای زیرش دیده نمی‌شود.
    PULL_HIDE = 56.0
    pull_ring = ft.ProgressRing(width=16, height=16, stroke_width=2,
                                color=P["acc"], value=0.0)
    pull_txt = ft.Text(t("hold_for_archive"), size=fs(11.5), color=P["sub"],
                       font_family=ui.FONT_MED)
    pull_hint = ft.Container(
        ft.Row([pull_ring, pull_txt], spacing=8, tight=True,
               alignment=ft.MainAxisAlignment.CENTER),
        padding=PAD(10, 6, 10, 6), visible=False, opacity=0.0,
        animate_opacity=anim(FAST, EASE), alignment=AL.CENTER)
    pull = {"t0": None, "run": False}

    def _pull_off():
        pull["t0"] = None
        if pull_hint.visible:
            pull_hint.visible = False
            pull_hint.opacity = 0.0
            pull_ring.value = 0.0
            try:
                pull_hint.update()
            except Exception:
                pass

    async def _pull_watch():
        if pull["run"]:
            return
        pull["run"] = True
        try:
            while _alive(gen) and pull["t0"] is not None:
                el = time.monotonic() - pull["t0"]
                pull_ring.value = min(1.0, el / PULL_HOLD)
                try:
                    pull_ring.update()
                except Exception:
                    pass
                if el >= PULL_HOLD:
                    if not st["reveal"]:
                        _arch_show(True)
                        _toast(page, t("archive_revealed"), ok=True)
                    _pull_off()
                    return
                await asyncio.sleep(0.08)
        finally:
            pull["run"] = False

    def _on_scroll(e):
        """کشیدنِ لیست به پایین و نگه‌داشتن ⇒ پوشه‌ی بایگانی ظاهر می‌شه."""
        if archived:
            return
        et = str(getattr(e, "event_type", "") or "").lower()
        px = e.pixels if e.pixels is not None else 0.0
        over = e.overscroll or 0.0
        if px > PULL_HIDE and st["reveal"]:
            _arch_show(False)          # نرم جمع می‌شود؛ لیست بازسازی نمی‌شود
        if "end" in et:                       # انگشت رها شد
            _pull_off()
            return
        pulling = (px < -12) or (over < -4)
        if pulling and not st["reveal"]:
            if pull["t0"] is None:
                pull["t0"] = time.monotonic()
                pull_hint.visible = True
                pull_hint.opacity = 1.0
                pull_ring.value = 0.0
                try:
                    pull_hint.update()
                except Exception:
                    pass
                page.run_task(_pull_watch)
        elif not pulling and pull["t0"] is not None and px > 4:
            _pull_off()

    lst.on_scroll = _on_scroll
    search_f = ui.field(hint=t("search_all_hint"), prefix=ui.vic("search", 18),
                        on_change=_q_change)
    title_col = ft.Column(
        [ft.Text(t("archived_chats") if archived else t("chats"), size=fs(18),
                 weight=W.W_700, font_family=ui.FONT_BOLD, color=P["txt"]),
         sub_txt],
        spacing=0, tight=True, expand=True,
        alignment=ft.MainAxisAlignment.CENTER)
    swap = ft.AnimatedSwitcher(
        title_col, duration=ft.Duration(milliseconds=200),
        transition=ft.AnimatedSwitcherTransition.FADE, expand=True)

    def _toggle_search(e=None):
        st["open"] = not st["open"]
        if st["open"]:
            swap.content = ft.Container(search_f, expand=True)
        else:
            st["q"] = ""
            st["hits"] = []
            search_f.value = ""
            swap.content = title_col
        try:
            search_btn.content.src = ("icons/close.svg" if st["open"]
                                      else "icons/search.svg")
        except Exception:
            pass
        _fill()
        if st["open"]:
            _focus(page, search_f)

    def _new_menu(e=None):
        _menu_dialog(
            page,
            _mi(page, "user", t("new_chat"), lambda: _new_chat_dialog(page)),
            _mi(page, "users", t("new_group"),
                lambda: _new_group_dialog(page, "group")),
            _mi(page, "hash", t("new_channel"),
                lambda: _new_group_dialog(page, "channel")),
            _mi(page, "camera", t("add_story"),
                lambda: page.run_task(_story_post, page, "user", None,
                                      _rings_load)))

    def _me_menu(e=None):
        """منویِ عکسِ پروفایلِ بالا-راستِ منویِ اصلی.

        خواسته‌ی کاربر: این عکس نباید مستقیم برود تنظیمات؛ باید سه کارِ خودش را
        بدهد — ویرایشِ پروفایل، عوض‌کردنِ پوسته، و ساختِ استوری. چرخ‌دنده‌ی
        تنظیمات همان‌جا در همین نوار سرِ جای خودش است، پس چیزی گم نمی‌شود.
        """
        _menu_dialog(
            page,
            _mi(page, "user", t("edit_profile"),
                lambda: page.run_task(profile_page, page)),
            _mi(page, "palette", t("theme"),
                lambda: _theme_dialog(page, chat_list_page)),
            _mi(page, "camera", t("add_story"),
                lambda: page.run_task(_story_post, page, "user", None,
                                      _rings_load)))

    me_av = ui.avatar(_me.get("display_name") or api.display_name or "?", 38,
                      url=_media(_me.get("avatar_url")),
                      tooltip=t("profile_menu"), on_click=_me_menu)
    search_btn = ui.icon_btn("search", tip=t("search"), on_click=_toggle_search)
    left_bit = (_back(page, chat_list_page) if archived else me_av)
    bar = ui.glass_bar(
        ft.Row([left_bit, swap, search_btn, _daynight_btn(page, chat_list_page),
                ui.icon_btn("settings", tip=t("settings"),
                            on_click=lambda e: page.run_task(settings_page, page))],
               spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        top=True)
    fab = ui.fab("plus", tip=t("new_chat"), on_click=_new_menu)
    nav = None if archived else _nav(page, "chats")
    st["nav"] = nav
    stack = [lst, empty]
    if not archived:
        # راهنمای «نگه‌دار برای بایگانی» — رویِ لیست شناور است تا چیدمان را
        # جابه‌جا نکند (وگرنه خودِ ظاهرشدنش اسکرول را می‌پراند).
        stack.append(ft.Container(pull_hint, left=0, right=0, top=0))
        # دکمه‌ی «+» بالای نوارِ پایین شناور می‌مونه (مثلِ تلگرام). جای
        # چیدنِ Stack در فلاتر فیزیکی است و با `page.rtl` آینه نمی‌شود، پس
        # مثلِ فلشِ «برو پایین» خودمان سمتِ درستِ زبان را می‌دهیم: در فارسی
        # پایین‌چپ، در انگلیسی پایین‌راست.
        stack.append(ft.Container(fab, bottom=NAV_H + 14,
                                  **({"left": 18} if i18n.rtl()
                                     else {"right": 18})))
        stack.append(ft.Container(nav, left=0, right=0, bottom=0))
    body = ft.Column([bar, strip,
                      ft.Container(ft.Stack(stack, expand=True), expand=True)],
                     spacing=0, expand=True)
    ui.prep_in(bar, dy=-0.25, ms=MED)
    if nav is not None:
        ui.prep_in(nav, dy=0.6, ms=MED)
    _go(page, body, dx=(0.12 if i18n.rtl() else -0.12))
    _attach_ws(on_ws)
    # برگشت از پس‌زمینه ⇒ لیست را از نو بگیر. سوکت در پس‌زمینه مرده و
    # `resync`ی که سرور می‌فرستد هم به گوشِ برنامه‌ی خوابیده نرسیده بود.
    notify.on_resume(_reload)
    page.run_task(ui.enter, page, bar, stagger=0.0)
    if nav is not None:
        page.run_task(ui.enter, page, nav, stagger=0.0, first_delay=0.05)
    page.run_task(_from_cache)
    page.run_task(_reload, True)
    page.run_task(_ticker)


# ── دیالوگِ چت جدید ─────────────────────────────────
def _new_chat_dialog(page):
    f = ui.field(t("username"), prefix=ui.vic("user", 18))
    msg = ft.Text("", size=fs(12), color=P["acc2"])

    async def _ok(e=None):
        u = (f.value or "").strip().lstrip("@").lower()
        if not u:
            msg.value = t("fill_all")
            page.update()
            return
        try:
            res = await api.create_chat(u)
        except Exception as ex:
            msg.value = _err_text(ex)
            page.update()
            return
        page.pop_dialog()
        await chat_page(page, res["chat_id"])

    f.on_submit = lambda e: page.run_task(_ok)
    ui.dialog(page, t("new_chat"),
              ft.Column([f, ui.spacer(h=6), msg], spacing=0, tight=True),
              actions=[ui.ghost_btn(t("cancel"),
                                    on_click=lambda e: page.pop_dialog()),
                       ui.primary_btn(t("create"), width=110, height=40,
                                      on_click=lambda e: page.run_task(_ok))])

# ── دیالوگِ گروه/کانالِ جدید ──────────────────────────
def _new_group_dialog(page, kind="group"):
    chan = (kind == "channel")
    title_f = ui.field(t("channel_title") if chan else t("group_title"),
                       prefix=ui.vic("hash" if chan else "users", 18))
    mem_f = ui.field(t("pick_members"), prefix=ui.vic("user", 18))
    bio_f = ui.field(t("bio"), prefix=ui.vic("info", 18), multiline=True)
    handle_f = ui.field(t("handle"), prefix=ft.Text("@", size=fs(14),
                                                   color=P["sub"]))
    msg = ft.Text("", size=fs(12), color=P["acc2"])
    pic = {"url": None}
    st = {"public": False}
    av_holder = ft.Container(
        ui.avatar(t("channel") if chan else t("group"), 68), alignment=AL.CENTER)
    hint = ft.Text(t("private_hint"), size=fs(11), color=P["dim"])

    def _set_public(v):
        st["public"] = bool(v)
        hint.value = t("public_hint") if st["public"] else t("private_hint")
        page.update()

    vis = ui.toggle_row("globe", t("public_chat"), st["public"],
                        on_change=lambda e: _set_public(e.control.value),
                        subtitle=t("public_sub"))

    async def _pick_pic(e=None):
        name, data = await _pick(page, IMG_EXT, t("group_photo"))
        if not data:
            return
        got = await _edit_photo(page, name, data, square=True)
        if got is None:
            return
        name, data = got
        try:
            res = await api.upload_media_bytes(name, data)
        except Exception as ex:
            msg.value = _err_text(ex)
            page.update()
            return
        pic["url"] = res.get("url")
        av_holder.content = ui.avatar(t("channel") if chan else t("group"), 68,
                                      url=_media(pic["url"]), ring=True)
        page.update()

    async def _ok(e=None):
        ttl = (title_f.value or "").strip()
        members = [m.strip().lstrip("@").lower()
                   for m in (mem_f.value or "").replace("،", ",").split(",")
                   if m.strip()]
        handle = (handle_f.value or "").strip().lstrip("@").lower()
        if not ttl:
            msg.value = t("fill_all")
            page.update()
            return
        if not handle and (st["public"] or chan):
            msg.value = t("handle_needed")
            page.update()
            return
        if handle and not re.fullmatch(r"[a-z0-9_]{3,32}", handle):
            msg.value = t("bad_handle")
            page.update()
            return
        try:
            res = await api.create_group(ttl, members,
                                        bio=(bio_f.value or "").strip(),
                                        avatar_url=pic["url"] or "",
                                        kind=kind, is_public=st["public"],
                                        handle=handle)
        except Exception as ex:
            msg.value = (t("handle_taken") if "409" in str(ex)
                         else _err_text(ex))
            page.update()
            return
        page.pop_dialog()
        _toast(page, t("channel_created") if chan else t("group_created"),
               ok=True)
        await chat_page(page, res["chat_id"])

    cam = ft.Container(
        ui.vic("camera", 16, "#ffffff"), width=30, height=30,
        border_radius=BR(15, 15, 15, 15), bgcolor=P["acc"], alignment=AL.CENTER,
        on_click=lambda e: page.run_task(_pick_pic), ink=True,
        tooltip=t("group_photo"))

    ui.dialog(
        page, t("new_channel") if chan else t("new_group"),
        ft.Column([ft.Row([av_holder, cam], spacing=6,
                          alignment=ft.MainAxisAlignment.CENTER,
                          vertical_alignment=ft.CrossAxisAlignment.END),
                   ui.spacer(h=12), title_f, ui.spacer(h=10), mem_f,
                   ui.spacer(h=10), bio_f, ui.spacer(h=10), handle_f,
                   ui.spacer(h=8), ui.group(vis), ui.spacer(h=6), hint,
                   ui.spacer(h=6), msg],
                  spacing=0, tight=True, scroll=ft.ScrollMode.AUTO),
        actions=[ui.ghost_btn(t("cancel"),
                              on_click=lambda e: page.pop_dialog()),
                 ui.primary_btn(t("create"), width=110, height=40,
                                on_click=lambda e: page.run_task(_ok))],
        width=380)


# ═══════════════════════════════════════════════════
#  مخاطبین
# ═══════════════════════════════════════════════════
async def contacts_page(page):
    """فهرستِ آدم‌هایی که با آن‌ها چتِ خصوصی دارم + جست‌وجوی آدمِ تازه."""
    _detach_ws()
    _player.clear()
    gen = _fresh()
    st = {"people": [], "hits": [], "q": "", "seq": 0, "nav": None, "nav_n": -1}

    lst = ft.ListView(expand=True, spacing=2,
                      padding=PAD(ui.LIST_PAD, 6, ui.LIST_PAD, NAV_H + 20))
    empty = ft.Container(
        ft.Column([ui.vic("contacts", 46, op(0.20)),
                   ui.spacer(h=10),
                   ft.Text(t("no_contacts"), size=fs(15), color=P["sub"]),
                   ft.Text(t("no_contacts_hint"), size=fs(12), color=P["dim"])],
                  horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                  spacing=2, tight=True),
        alignment=AL.CENTER, expand=True, visible=False)
    count_txt = ft.Text("", size=fs(11), color=P["sub"])

    def _person_row(u, chat_id=None):
        name = u.get("display_name") or u.get("username") or "?"
        un = u.get("username") or ""
        online = _online_of(u)
        sub = (_seen_of(u) or (f"@{un}" if un else ""))
        row = ft.Container(
            ft.Row([ui.avatar(name, ui.AV, url=_media(u.get("avatar_url")),
                              badge=P["ok"] if online else None),
                    ft.Column(
                        [ft.Text(name, size=fs(15), weight=W.W_600,
                                 font_family=ui.FONT_MED, color=P["txt"],
                                 max_lines=1,
                                 overflow=ft.TextOverflow.ELLIPSIS),
                         ft.Text(sub, size=fs(12),
                                 color=P["ok"] if online else P["sub"],
                                 max_lines=1,
                                 overflow=ft.TextOverflow.ELLIPSIS)],
                        spacing=3, expand=True,
                        alignment=ft.MainAxisAlignment.CENTER),
                    ui.icon_btn("chat", box=36, size=17, tip=t("open"),
                                on_click=lambda e, x=un: page.run_task(
                                    _open_chat_with, page, x))],
                   spacing=12,
                   vertical_alignment=ft.CrossAxisAlignment.CENTER),
            height=ui.row_h(),
            padding=PAD(ui.LIST_PAD + 2, ui.ROW_PAD - 2, ui.LIST_PAD + 2,
                        ui.ROW_PAD - 2),
            border_radius=BR(14, 14, 14, 14), ink=True,
            animate_opacity=MED, animate_offset=MED,
            on_click=(lambda e, i=chat_id, x=un: page.run_task(chat_page, page, i)
                      if i else page.run_task(_open_chat_with, page, x)))
        ui.hover_tint(row)
        return ft.GestureDetector(
            row,
            on_secondary_tap=lambda e, x=un: page.run_task(
                _show_user_profile, page, x),
            on_long_press=lambda e, x=un: page.run_task(
                _show_user_profile, page, x))

    def _match(p):
        q = st["q"].strip().lower().lstrip("@")
        if not q:
            return True
        u = p["user"]
        return (q in (u.get("display_name") or "").lower()
                or q in (u.get("username") or "").lower())

    def _fill():
        mine = [_person_row(p["user"], p.get("chat_id"))
                for p in st["people"] if _match(p)]
        rows = []
        if st["q"].strip() and (mine or st["hits"]):
            if mine:
                rows.append(ui.section(t("contacts")))
            rows += mine
            if st["hits"]:
                rows.append(ui.section(t("global_results")))
                rows += [_person_row(h) for h in st["hits"]]
        else:
            rows += mine
        lst.controls = rows
        lst.visible = bool(rows)
        empty.visible = not rows
        if st["q"].strip():
            empty.content.controls[2].value = t("nothing_found")
            empty.content.controls[3].value = t("find_people")
        else:
            empty.content.controls[2].value = t("no_contacts")
            empty.content.controls[3].value = t("no_contacts_hint")
        count_txt.value = (t("contacts_n", n=i18n.num(len(st["people"])))
                           if st["people"] else "")
        try:
            page.update()
        except Exception:
            pass

    async def _reload(first=False):
        # آفلاین‌اول: سرور که نبود، همان لیستِ محلی کافیه — این صفحه فقط
        # نام و عکسِ آدم‌ها را از ردیف‌های چت بیرون می‌کشد و هر دو در انبار
        # هستند. پیش از این تنها یک هشدارِ سرخ می‌داد و خالی می‌ماند.
        try:
            chats = await _try(api.list_chats(), 8.0)
        except Exception as ex:
            _net_fail(ex)
            chats = await store.load_chats()
            if not chats:
                _toast(page, _err_text(ex))
                return
        else:
            _net_mark(True)
        if not _alive(gen):
            return
        n = _unread_total(chats)
        if st["nav"] is not None and n != st["nav_n"]:
            st["nav_n"] = n            # نشانِ نخونده‌ها روی زبانه‌ی گفت‌وگوها
            st["nav"].content = _nav(page, "contacts").content
        seen, people = set(), []
        for c in chats:
            if c.get("is_group"):
                continue
            u = c.get("other_user") or {}
            un = (u.get("username") or "").lower()
            if not un or un in seen:
                continue
            seen.add(un)
            people.append({"user": u, "chat_id": c.get("chat_id")})
        people.sort(key=lambda p: (p["user"].get("display_name")
                                   or p["user"].get("username") or "").lower())
        st["people"] = people
        _fill()
        if first and lst.controls:
            for r in lst.controls:
                ui.prep_in(r, dy=0.05, ms=MED)
            try:
                page.update()
            except Exception:
                return
            await ui.enter(page, *lst.controls, stagger=0.028, first_delay=0.0)

    async def _search_soon(q):
        st["seq"] += 1
        mine = st["seq"]
        await asyncio.sleep(0.28)
        if mine != st["seq"] or not _alive(gen):
            return
        if not q.strip():
            st["hits"] = []
            _fill()
            return
        try:
            hits = await api.search_all(q.strip())
        except Exception:
            hits = []
        if mine != st["seq"] or not _alive(gen):
            return
        have = {(p["user"].get("username") or "").lower()
                for p in st["people"]}
        st["hits"] = [{"display_name": h.get("title"),
                       "username": h.get("username"),
                       "avatar_url": h.get("avatar_url"),
                       "last_seen_at": None}
                      for h in hits
                      if (h.get("kind") or "user") == "user"
                      and h.get("username")
                      and h["username"].lower() not in have
                      and h.get("username") != api.username]
        _fill()

    async def on_ws(data):
        if not _alive(gen):
            return
        typ = data.get("type")
        if typ in ("resync", "chat_created", "chat_removed", "profile_updated"):
            await _reload()

    async def _ticker():
        """ساعتِ «آخرین بازدید» و نقطه‌های آنلاین هر ۳۰ ثانیه تازه می‌شن."""
        while _alive(gen):
            await asyncio.sleep(30)
            if not _alive(gen):
                return
            await _reload()

    def _q_change(e):
        st["q"] = search_f.value or ""
        _fill()
        page.run_task(_search_soon, st["q"])

    search_f = ui.field(hint=t("find_people"), prefix=ui.vic("search", 18),
                        on_change=_q_change)
    bar = ui.glass_bar(
        ft.Row([_back(page, chat_list_page),
                ft.Column([ft.Text(t("contacts"), size=fs(17), weight=W.W_700,
                                   font_family=ui.FONT_BOLD, color=P["txt"]),
                           count_txt],
                          spacing=0, tight=True, expand=True,
                          alignment=ft.MainAxisAlignment.CENTER),
                _daynight_btn(page, contacts_page)],
               spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        top=True)
    nav = _nav(page, "contacts")
    st["nav"] = nav
    fab = ui.fab("plus", tip=t("new_chat"),
                 on_click=lambda e: _new_chat_dialog(page))
    body = ft.Column(
        [bar,
         ft.Container(search_f, padding=PAD(14, 8, 14, 2)),
         ft.Container(
             ft.Stack([lst, empty,
                       # سمتِ درستِ زبان — توضیحش در chats_page است
                       ft.Container(fab, bottom=NAV_H + 14,
                                    **({"left": 18} if i18n.rtl()
                                       else {"right": 18})),
                       ft.Container(nav, left=0, right=0, bottom=0)],
                      expand=True), expand=True)],
        spacing=0, expand=True)
    ui.prep_in(bar, dy=-0.25, ms=MED)
    ui.prep_in(nav, dy=0.6, ms=MED)
    _go(page, body)
    _attach_ws(on_ws)
    page.run_task(ui.enter, page, bar, stagger=0.0)
    page.run_task(ui.enter, page, nav, stagger=0.0, first_delay=0.05)
    page.run_task(_reload, True)
    page.run_task(_ticker)


# ═══════════════════════════════════════════════════
#  حسابِ من
# ═══════════════════════════════════════════════════
async def profile_page(page):
    """کارتِ حسابِ خودم — عکس، نام، شناسه، بیو و راهِ ویرایش."""
    _detach_ws()
    _player.clear()
    gen = _fresh()
    # ⚡ آفلاین‑اول: تا وقتی نسخه‌ای از پروفایل در انبار داریم، *صفر* ثانیه
    # منتظرِ شبکه نمی‌مانیم؛ همان را می‌کشیم و `_after` پشتِ صحنه به‌روز
    # می‌کند. تنها بارِ اول (انبارِ خالی) ناچاریم منتظر بمانیم.
    warm = await _seed_me()
    if not warm:
        await _load_me()
        if not _alive(gen):
            return
    photos = _me.get("photos") or []
    name = _me.get("display_name") or api.display_name or "?"
    un = _me.get("username") or ""

    cam = ft.Container(
        ui.vic("camera", 16, "#ffffff"), width=34, height=34,
        bgcolor=P["acc"], border_radius=BR(17, 17, 17, 17), alignment=AL.CENTER,
        ink=True, tooltip=t("pick_photo"),
        on_click=lambda e: page.run_task(_pick_avatar, page, profile_page))
    card = ui.glass(
        ft.Column(
            [ft.Stack([ui.avatar(name, 104, url=_media(_me.get("avatar_url")),
                                 ring=True,
                                 on_click=lambda e: page.run_task(
                                     _show_my_profile, page)),
                       ft.Container(cam, right=0, bottom=0)],
                      width=104, height=104),
             ui.spacer(h=12),
             ft.Text(name, size=fs(19), weight=W.W_700,
                     font_family=ui.FONT_BOLD, color=P["txt"]),
             ft.Text(f"@{un}" if un else "", size=fs(12.5), color=P["acc"]),
             ui.spacer(h=10),
             ui.dots(len(photos), 0, width=130)],
            spacing=0, tight=True,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER),
        radius=24, pad=PAD(18, 20, 18, 16), tint=0.06)

    info = ui.group(
        ui.row_item("dot", f"@{un}" if un else t("username"), t("username"),
                    trailing=ui.icon_btn("copy", box=34, size=15,
                                         tip=t("copy"),
                                         on_click=lambda e: page.run_task(
                                             _copy_text, page, f"@{un}"))
                    if un else None),
        ui.row_item("info", (_me.get("bio") or t("no_bio")), t("bio")))

    actions = ui.group(
        ui.row_item("pencil", t("edit_profile"),
                    trailing=ui.vic("chevron_left" if i18n.rtl()
                                    else "chevron_right", 16),
                    on_click=lambda e: page.run_task(settings_page, page)),
        ui.row_item("image", t("my_photos"),
                    subtitle=t("photos_n", n=i18n.num(len(photos)),
                               max=i18n.num(5)),
                    trailing=ui.vic("chevron_left" if i18n.rtl()
                                    else "chevron_right", 16),
                    on_click=lambda e: page.run_task(_show_my_profile, page)),
        ui.row_item("settings", t("settings"),
                    trailing=ui.vic("chevron_left" if i18n.rtl()
                                    else "chevron_right", 16),
                    on_click=lambda e: page.run_task(settings_page, page)))

    head = ui.glass_bar(
        ft.Row([_back(page, chat_list_page),
                ft.Text(t("my_account"), size=fs(17), weight=W.W_700,
                        font_family=ui.FONT_BOLD, color=P["txt"], expand=True),
                _daynight_btn(page, profile_page)],
               spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        top=True)

    blocks = [card,
              ui.section(t("profile_account")), info,
              ui.spacer(h=4), actions,
              ui.spacer(h=NAV_H + 10)]
    for b in blocks:
        ui.prep_in(b, dy=0.06, ms=MED)
    ui.prep_in(head, dy=-0.25, ms=MED)
    nav = _nav(page, "profile")
    ui.prep_in(nav, dy=0.6, ms=MED)

    body = ft.Column(
        [head,
         ft.Container(
             ft.Stack([ft.Column(blocks, spacing=0, tight=True,
                                 scroll=ft.ScrollMode.AUTO, expand=True),
                       ft.Container(nav, left=0, right=0, bottom=0)],
                      expand=True),
             padding=PAD(14, 10, 14, 0), expand=True)],
        spacing=0, expand=True)
    _go(page, body)
    page.run_task(ui.enter, page, head, *blocks, stagger=0.03)
    page.run_task(ui.enter, page, nav, stagger=0.0, first_delay=0.05)
    page.run_task(_nav_sync, page, nav, "profile", gen)

    async def _after():
        """به‌روزرسانیِ پشتِ صحنه — و بازسازی *تنها* اگر چیزی عوض شده باشد.

        چرا مقایسه؟ چون بازسازی انیمیشنِ ورود را دوباره پخش می‌کند و اگر
        هر بار انجام شود، صفحه یک تکانِ بی‌دلیل می‌خورد. کارتِ پروفایل کم
        عوض می‌شود، پس در حالتِ معمول این تابع بی‌صدا تمام می‌شود.
        """
        before = (name, un, _me.get("bio") or "",
                  _me.get("avatar_url") or "", len(photos))
        if not await _load_me() or not _alive(gen):
            return
        _net_mark(True)
        after = (_me.get("display_name") or api.display_name or "?",
                 _me.get("username") or "", _me.get("bio") or "",
                 _me.get("avatar_url") or "", len(_me.get("photos") or []))
        if after != before:
            await profile_page(page)

    if warm:
        page.run_task(_after)


# ═══════════════════════════════════════════════════
#  صفحه‌ی چت
# ═══════════════════════════════════════════════════
async def _mark_read(chat_id):
    try:
        await api.mark_read(chat_id)
    except Exception:
        pass


async def _cached_prof(chat_id):
    """پروفایلِ گفت‌وگو از انبارِ محلی — برای وقتی سرور در دسترس نیست.

    سرور که نباشد `chat/profile` می‌شکند و پیش از این کاربر را به لیست
    برمی‌گرداند؛ یعنی آفلاین هیچ گفت‌وگویی باز نمی‌شد. با ردیفی که در انبار
    داریم همان چند چیزِ لازمِ سرصفحه ساخته می‌شود (عنوان، عکس، گروه‌بودن) و
    پیام‌های ذخیره‌شده خوانده می‌شوند.
    """
    row = next((c for c in await store.load_chats()
                if str(c.get("chat_id")) == str(chat_id)), None)
    if not row:
        return {}
    ou = row.get("other_user") or {}
    if ou:
        # ⚠️ `online`ِ ذخیره‌شده کهنه است و ادعای «آنلاین» با آن دروغ می‌شود.
        # برش می‌داریم تا `_seen_of` به حدسِ زمانیِ `last_seen_at` برگردد.
        ou = {k: v for k, v in ou.items() if k != "online"}
    members = []
    if api.user_id:
        members.append({"user_id": api.user_id,
                        "display_name": api.display_name or "",
                        "username": api.username or ""})
    if ou:
        members.append(ou)
    return {"chat_id": row.get("chat_id"), "title": row.get("title") or "",
            "avatar_url": row.get("avatar_url"),
            "is_group": bool(row.get("is_group")),
            "kind": row.get("kind"), "handle": row.get("handle"),
            "members": members, "members_count": row.get("members_count"),
            "offline": True}


def _prof_key(prof) -> tuple:
    """اثرِ انگشتِ آن بخش‌هایی از پروفایل که *سرصفحه و دسترسی‌ها* را می‌سازند.

    صفحه‌ی گفت‌وگو از انبار کشیده می‌شود و بعد سرور جواب می‌دهد؛ اگر همه‌چیز
    همان باشد (حالتِ معمول) نباید هیچ تکانی بخورد. این تابع «همان باشد» را
    اندازه می‌گیرد. `my_role` را با پیش‌فرضِ `member` هم‌سطح می‌کنیم چون
    ردیفِ لیستِ چت این کلید را ندارد و وگرنه هر بار تفاوت دیده می‌شد.
    """
    p = prof or {}
    return (p.get("title") or "", p.get("avatar_url") or "",
            p.get("kind") or "", p.get("my_role") or "member",
            bool(p.get("can_post") if p.get("can_post") is not None else True),
            bool(p.get("members_hidden")),
            int(p.get("members_count") or 0),
            tuple(str(m.get("user_id")) for m in (p.get("members") or [])
                  if m.get("user_id")))


def _msgs_key(msgs) -> tuple:
    """اثرِ انگشتِ لیستِ پیام — شناسه، ویرایش، پاک‌شدن، تیک و واکنش."""
    return tuple((m.get("message_id"), bool(m.get("edited")),
                  bool(m.get("deleted")), m.get("content") or "",
                  m.get("caption") or "", len(m.get("seen_by") or []),
                  tuple(sorted((m.get("reactions") or {}).items())))
                 for m in (msgs or []))


async def chat_page(page, chat_id):
    _detach_ws()
    notify.set_active_chat(chat_id)   # اعلانِ همین چت خفه است (مثلِ تلگرام)
    _player.clear()
    gen = _fresh()
    st = {"msgs": [], "prof": {}, "reply": None, "seen": set(), "edit": None,
          "pin": None, "typers": {}, "keys": {}, "hl": None, "gifs": [],
          "bottom": True, "down": 0, "emoji": False, "wraps": {},
          "read_at": None}
    page.run_task(_mark_read, chat_id)

    # ── بازکردنِ گفت‌وگو: انبارِ محلی سرِ دست، شبکه پشتِ صحنه ─────────
    # پیش از این `chat_profile` و بعد `get_messages` سرِ راهِ رسمِ صفحه بودند
    # (۴ تا ۱۲ ثانیه + ۸ ثانیه). روی اتصالِ فیلترشده — که آویزان می‌ماند نه
    # اینکه رد شود — یعنی صفحه‌ی سفید و همان شکایتِ «میزنم رو چت، طول می‌کشه
    # تا واردش بشم». قاعده‌ی ۱.۶: اگر نسخه‌ی محلی داریم، *صفر* ثانیه انتظار؛
    # صفحه با آن کشیده می‌شود و `_sync` پشتِ صحنه به‌روزش می‌کند. تنها
    # گفت‌وگویی که هرگز باز نشده ناچار است منتظر بماند.
    prof0 = await store.load_prof(chat_id)     # پروفایلِ کاملِ بارِ پیش
    if not prof0:
        prof0 = await _cached_prof(chat_id)    # لااقل ردیفِ لیستِ چت
    msgs0 = await store.load_msgs(chat_id) if prof0 else []
    warm = bool(prof0 and msgs0)
    from_net = False
    if warm:
        st["prof"], st["msgs"] = prof0, msgs0
    else:
        if _net_down() and prof0:
            st["prof"], st["msgs"] = prof0, msgs0
            _toast(page, t("offline_hint"))
        if not st["prof"]:
            try:
                st["prof"] = await _try(api.chat_profile(chat_id),
                                        4.0 if prof0 else 12.0)
            except Exception as ex:
                _net_fail(ex)
                if not prof0:
                    _toast(page, _err_text(ex))
                    await chat_list_page(page)
                    return
                st["prof"] = prof0
                _toast(page, t("offline_hint"))
            else:
                from_net = True
                _net_mark(True)  # سرور جواب داد؛ پس «در حال اتصال» بی‌معنیه
                page.run_task(store.save_prof, chat_id, st["prof"])
    if from_net:
        try:
            st["msgs"] = await _try(api.get_messages(chat_id, limit=80), 8.0)
            # انبارِ محلی: دفعه‌ی بعد (یا آفلاین) همین‌ها نشان داده می‌شوند
            page.run_task(store.save_msgs, chat_id, st["msgs"])
        except Exception as ex:
            _net_fail(ex)
            st["msgs"] = msgs0
            if not st["msgs"]:
                _toast(page, _err_text(ex))
    st["seen"] = {m.get("message_id") for m in st["msgs"]}

    prof = st["prof"]
    is_group = bool(prof.get("is_group"))
    kind = prof.get("kind") or ("group" if is_group else "private")
    my_role = prof.get("my_role") or "member"
    can_post = prof.get("can_post")
    can_post = True if can_post is None else bool(can_post)
    can_manage = my_role in ("owner", "admin")
    is_channel = kind == "channel"
    # شمارِ اعضا از سرور می‌آید؛ در کانال برای مشترکِ ساده فهرست خالیه
    mem_count = int(prof.get("members_count")
                    or len(prof.get("members") or []))
    members_hidden = bool(prof.get("members_hidden"))
    st["read_at"] = prof.get("peer_read_at")
    is_saved = kind == KIND_SAVED
    title = t("saved_messages") if is_saved else (prof.get("title") or "")
    other = None
    if not is_group and not is_saved:
        other = next((m for m in prof.get("members") or []
                      if m.get("user_id") != api.user_id), None)
        if other:
            title = other.get("display_name") or title

    av_url = other.get("avatar_url") if other else prof.get("avatar_url")
    # auto_scroll با «پرش به پیام» می‌جنگد (هر به‌روزرسانی به تهِ لیست می‌پرید)،
    # پس خودمان اسکرول را مدیریت می‌کنیم: تهِ لیست + فلشِ برگشت مثل تلگرام.
    msg_list = ft.ListView(expand=True, spacing=1, auto_scroll=False,
                           scroll_interval=80, padding=PAD(10, 8, 10, 8))

    async def _scroll_end(smooth=True):
        st["bottom"], st["down"] = True, 0
        _sync_down()
        try:
            res = msg_list.scroll_to(offset=-1, curve=ui.EASE,
                                     duration=340 if smooth else 0)
            if inspect.isawaitable(res):
                await res
        except Exception:
            pass

    def _on_msg_scroll(e):
        try:
            px = float(getattr(e, "pixels", 0) or 0)
            mx = float(getattr(e, "max_scroll_extent", 0) or 0)
        except (TypeError, ValueError):
            return
        at_end = (mx - px) <= 90
        if at_end == st["bottom"]:
            return
        st["bottom"] = at_end
        if at_end:
            st["down"] = 0
        _sync_down()

    msg_list.on_scroll = _on_msg_scroll

    # ── فلشِ «برو پایینِ گفت‌وگو» (مثل تلگرام) ──────────
    down_num = ft.Text("", size=fs(10), color="#ffffff",
                       font_family=ui.FONT_BOLD)
    down_pill = ft.Container(
        down_num, height=18, padding=PAD(5, 0, 5, 0), alignment=AL.CENTER,
        bgcolor=P["acc"], border_radius=BR(9, 9, 9, 9), visible=False,
        border=ft.Border(*[ft.BorderSide(1.6, op(0.85, P["bg1"]))] * 4))
    down_circle = ft.Container(
        ui.vic("chevron_down", 21, P["txt"]), width=44, height=44,
        alignment=AL.CENTER, border_radius=BR(22, 22, 22, 22),
        bgcolor=op(0.94, P["bg1"]),
        border=ft.Border(*[ft.BorderSide(1, op(0.08))] * 4),
        shadow=ft.BoxShadow(blur_radius=14, spread_radius=-3,
                            color=op(0.30, "#000000"), offset=OFF(0, 4)),
        ink=True, tooltip=t("to_bottom"),
        on_click=lambda e: page.run_task(_scroll_end, True))
    down_btn = ft.Container(
        ft.Stack([ft.Container(down_circle, margin=MG(0, 8, 0, 0)),
                  ft.Container(down_pill, right=0, top=0)],
                 width=52, height=54),
        visible=False, opacity=0.0, offset=OFF(0, 0.35),
        animate_opacity=anim(FAST, EASE), animate_offset=anim(MED, ui.GLIDE))

    def _sync_down():
        n = st["down"]
        down_num.value = i18n.num(n if n < 100 else 99)
        down_pill.visible = n > 0
        show = not st["bottom"]
        if show and not down_btn.visible:
            down_btn.visible = True
            down_btn.opacity, down_btn.offset = 0.0, OFF(0, 0.35)
            try:
                page.update()
            except Exception:
                return
            down_btn.opacity, down_btn.offset = 1.0, OFF(0, 0)
        elif not show:
            down_btn.opacity, down_btn.offset = 0.0, OFF(0, 0.35)
            down_btn.visible = False
        try:
            page.update()
        except Exception:
            pass
    # جای خالیِ گفت‌وگو. در دفترچه‌ی یادداشت متنش عوض می‌شود: «اولین پیام را
    # تو بفرست» آن‌جا بی‌معنی است، و همان‌جا بهترین جا برای گفتنِ اینکه هرچه
    # این‌جا بگذاری می‌ماند.
    empty = ft.Container(
        ft.Column([ui.vic("bookmark" if is_saved else "smile", 44, op(0.18)),
                   ui.spacer(h=8),
                   ft.Text(t("saved_messages") if is_saved
                           else t("no_messages"), size=fs(14), color=P["sub"]),
                   ft.Text(t("saved_empty") if is_saved
                           else t("no_messages_hint"), size=fs(12),
                           color=P["dim"], max_lines=3,
                           text_align=ft.TextAlign.CENTER)],
                  horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                  spacing=2, tight=True, width=280),
        alignment=AL.CENTER, expand=True, visible=False,
        animate_opacity=anim(SLOW, EASE), animate_scale=anim(SLOW, ui.POP),
        opacity=1.0, scale=1.0)

    # ── حبابِ پیام صوتی ─────────────────────────────
    def _voice_row(m, me):
        mid = m.get("message_id")
        url = _media(m.get("content"))
        playing, frac, pos = _player.state(mid)
        glyph = ui.vic("pause" if playing else "play", 18, "#ffffff")
        wave = ui.wave_bars(mid, count=26, height=20, played=frac,
                            color="#ffffff", dim=op(0.35))
        clock = ft.Text(_mmss(pos), size=fs(10), color=op(0.75))
        holder = ft.Row(
            [ft.Container(glyph, width=34, height=34, alignment=AL.CENTER,
                          border_radius=BR(17, 17, 17, 17), bgcolor=op(0.18),
                          ink=True, animate=anim(FAST, EASE),
                          on_click=lambda e: page.run_task(_toggle_voice, mid, url)),
             ft.Column([wave, clock], spacing=3, tight=True)],
            spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER)

        def _refresh():
            p, f, sec = _player.state(mid)
            glyph.src = "icons/pause.svg" if p else "icons/play.svg"
            done = int(26 * f)
            for i, b in enumerate(wave.controls):
                b.bgcolor = "#ffffff" if i < done else op(0.35)
            clock.value = _mmss(sec if (p or f > 0) else 0)
            try:
                page.update()
            except Exception:
                pass

        _player.bind(mid, _refresh)
        return holder

    async def _toggle_voice(mid, url):
        if fa is None:
            _toast(page, t("voice_off"))
            return
        await _player.toggle(mid, url)

    # ── پخش‌کننده‌ی درون‌خطیِ موسیقی ────────────────────
    # مثلِ پیامِ صوتی در جای خودش پخش می‌شه؛ دیگه پنجره‌ی تمام‌صفحه
    # نمی‌گیره (پلیرِ بزرگ فقط برای ویدیو مونده).
    def _audio_row(m, me=False):
        mid = m.get("message_id")
        url = _media(m.get("content"))
        nm = _fname(m.get("content") or "")
        sz = m.get("size") or m.get("file_size")
        BW = 150                       # پهنای موجِ قابلِ لمس برای پریدن
        playing, frac, pos, dur, rate = _player.info(mid)
        col = ui.on_bubble(me)
        glyph = ui.vic("pause" if playing else "play", 17, "#ffffff")
        disc = ft.Container(
            glyph, width=38, height=38, alignment=AL.CENTER,
            border_radius=BR(19, 19, 19, 19),
            gradient=ft.LinearGradient(begin=AL.CENTER_LEFT,
                                       end=AL.CENTER_RIGHT,
                                       colors=[P["acc2"], P["acc"]]),
            ink=True, tooltip=t("play_pause"), animate=anim(FAST, EASE),
            on_click=lambda e: page.run_task(_toggle_voice, mid, url))
        wave = ui.wave_bars(mid, count=30, height=18, played=frac,
                            color=col, dim=op(0.32, col))

        def _tap(e):
            """لمس روی موج ⇒ پریدن به همان نقطه."""
            try:
                x = float(e.local_position.x)
            except Exception:
                return
            f = max(0.0, min(1.0, x / BW))
            if i18n.rtl():             # در چیدمانِ راست‌به‌چپ موج از راست پر می‌شه
                f = 1.0 - f
            page.run_task(_player.seek, mid, f)

        seek = ft.Container(wave, width=BW, height=20, alignment=AL.CENTER,
                            on_tap_down=_tap, tooltip=t("seek"))
        clock = ft.Text(f"{_mmss(pos)} / {_mmss(dur)}", size=fs(10),
                        color=op(0.75, col), font_family=ui.FONT_MED)
        rate_lbl = ft.Text(_rate_label(rate), size=fs(10.5), color=col,
                           font_family=ui.FONT_MED,
                           text_align=ft.TextAlign.CENTER)

        def _rate(step):
            _player.set_rate(mid, step)
            _refresh()

        speed = ft.Container(
            ft.Row([ui.icon_btn("minus", box=22, size=11, color=col,
                                tip=t("slower"), on_click=lambda e: _rate(-1)),
                    ft.Container(rate_lbl, width=32, alignment=AL.CENTER),
                    ui.icon_btn("plus", box=22, size=11, color=col,
                                tip=t("faster"), on_click=lambda e: _rate(1))],
                   spacing=0, tight=True,
                   vertical_alignment=ft.CrossAxisAlignment.CENTER),
            bgcolor=op(0.14, col), border_radius=BR(10, 10, 10, 10),
            padding=PAD(1, 1, 1, 1))

        head = ft.Row(
            [ft.Text(nm, size=fs(12.5), color=col, max_lines=1, expand=True,
                     overflow=ft.TextOverflow.ELLIPSIS,
                     font_family=ui.FONT_MED),
             ft.Text(ui.human_size(int(sz)) if sz else "", size=fs(10),
                     color=op(0.62, col))],
            spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER)

        def _refresh():
            p, f, sec, d, r = _player.info(mid)
            glyph.src = "icons/pause.svg" if p else "icons/play.svg"
            done = int(30 * f)
            for i, b in enumerate(wave.controls):
                b.bgcolor = col if i < done else op(0.32, col)
            clock.value = f"{_mmss(sec if (p or f > 0) else 0)} / {_mmss(d)}"
            rate_lbl.value = _rate_label(r)
            try:
                page.update()
            except Exception:
                pass

        _player.bind(mid, _refresh)
        return ft.Container(
            ft.Column([head,
                       ft.Row([disc, ft.Column([seek, clock], spacing=1,
                                               tight=True)],
                              spacing=9,
                              vertical_alignment=ft.CrossAxisAlignment.CENTER),
                       ft.Row([ui.vic("speed", 12, op(0.7, col)), speed,
                               ft.Container(expand=True),
                               ui.icon_btn("download", box=26, size=13,
                                           color=op(0.8, col),
                                           tip=t("save_to_downloads"),
                                           on_click=lambda e: page.run_task(
                                               _save_media, page, m)),
                               ui.icon_btn("open", box=26, size=13,
                                           color=op(0.8, col),
                                           tip=t("open_file"),
                                           on_click=lambda e: page.run_task(
                                               _open_media, page, m))],
                              spacing=5,
                              vertical_alignment=ft.CrossAxisAlignment.CENTER)],
                      spacing=5, tight=True),
            width=252, padding=PAD(4, 2, 4, 0))

    # ── باز کردنِ فایل با نوارِ پیشرفت ────────────────
    # زدن روی فایل «بازش می‌کند»، نه اینکه در پوشه‌ی دانلود بریزد.
    # ذخیره‌ی واقعی فقط از منویِ نگه‌داشتن → «ذخیره در دانلود» انجام می‌شود.
    async def _open_card(m, card_holder):
        url = m.get("content") or ""
        if not url:
            return
        ready = _dl_cache.get(url)
        if ready and os.path.isfile(ready):
            if not _open_local(ready):
                _toast(page, t("open_failed"))
            return
        nm = _fname(url)
        dest = os.path.join(_cache_dir(), _fname_raw(url))
        prog = ui.progress_bar(0.0, color=P["acc"])
        card_holder.content = _prog_card(nm, t("opening"), None, prog,
                                         width=250)
        try:
            page.update()
        except Exception:
            pass

        def _on(done, total):
            prog.value = (done / total) if total else None
            try:
                prog.update()
            except Exception:
                pass

        ok = os.path.isfile(dest)
        if not ok:
            try:
                await api.download_to(_media(url), dest, on_progress=_on)
                ok = True
            except Exception as ex:
                _rm(dest)
                _toast(page, _err_text(ex))
        card_holder.content = _file_card(m, card_holder)
        try:
            page.update()
        except Exception:
            pass
        if not ok:
            return
        _dl_cache[url] = dest
        if not _open_local(dest):
            _toast(page, t("open_failed"))

    def _file_card(m, holder, icon=None, play=False):
        url = m.get("content") or ""
        nm = _fname(url)
        sz = m.get("size") or m.get("file_size")
        size_text = (ui.human_size(int(sz)) if sz else t("file"))
        if play:            # موسیقی: در جای خودش پخش می‌شه (مثلِ پیامِ صوتی)
            return ui.file_card(
                nm, size_text, icon=(icon or "music"), sub=t("tap_to_play"),
                on_click=lambda e: page.run_task(_toggle_voice,
                                                 m.get("message_id"),
                                                 _media(m.get("content"))),
                trailing=ui.vic("play", 15, P["acc"]), color=P["acc"])
        return ui.file_card(
            nm, size_text, icon=(icon or ui.file_icon_for(nm)),
            sub=t("tap_to_open"),
            on_click=lambda e: page.run_task(_open_card, m, holder),
            trailing=ui.vic("open", 16, P["acc"]),
            color=P["acc"])

    # ── واکنش‌ها ─────────────────────────────────────
    async def _react(m, emoji):
        try:
            res = await api.react(chat_id, m.get("message_id"), emoji)
        except Exception as ex:
            _toast(page, _err_text(ex))
            return
        m["reactions"] = res.get("reactions") or {}
        m["my_reactions"] = res.get("my_reactions") or []
        _render()

    def _reaction_row(m):
        rx = m.get("reactions") or {}
        if not rx:
            return None
        mine = set(m.get("my_reactions") or [])
        chips = [ui.reaction_chip(
                     em, int(n), mine=(em in mine),
                     on_click=lambda e, mm=m, ee=em: page.run_task(_react, mm, ee))
                 for em, n in rx.items() if int(n) > 0]
        if not chips:
            return None
        return ft.Row(chips, spacing=4, wrap=True, run_spacing=4)

    # ── پریدن به پیامِ اصلیِ پاسخ / پیامِ سنجاق‌شده ──────
    async def _jump_to(mid):
        if not mid:
            return
        if mid not in st["keys"]:
            # پیامِ قدیمی هنوز لود نشده؛ تاریخِ بیشتری می‌گیریم و باز می‌گردیم
            try:
                more = await api.get_messages(chat_id, limit=400)
            except Exception:
                more = []
            if any(x.get("message_id") == mid for x in more):
                st["msgs"] = more
                st["seen"] = {x.get("message_id") for x in more}
                _render()
                await asyncio.sleep(0.05)
            if mid not in st["keys"]:
                _toast(page, t("jump_failed"))
                return
        # هایلایت را روی همان حبابِ ساخته‌شده می‌گذاریم (رندرِ دوباره لازم نیست،
        # چون رندر جای اسکرول را به هم می‌ریزد)
        st["hl"] = mid
        w = st["wraps"].get(mid)
        if w is not None:
            w.bgcolor = op(0.16, P["acc"])
            w.scale = 1.02
        st["bottom"] = False
        _sync_down()
        for delay in (0.02, 0.16):
            await asyncio.sleep(delay)
            try:
                res = msg_list.scroll_to(scroll_key=mid, duration=460,
                                         curve=ui.EASE)
                if inspect.isawaitable(res):
                    await res
            except Exception:
                pass
        await asyncio.sleep(1.15)
        if st.get("hl") == mid:
            st["hl"] = None
            if w is not None:
                w.bgcolor = None
                w.scale = 1.0
            try:
                page.update()
            except Exception:
                pass

    # ── منوی پیام ───────────────────────────────────
    def _msg_menu(m):
        me = m.get("sender_id") == api.user_id
        if m.get("deleted"):
            return
        txt_kind = (m.get("msg_type") or "text") == "text"
        body, was_enc = _in_text(m)
        quick = ft.Row(
            [ft.Container(ft.Text(em, size=fs(20)), width=40, height=40,
                          alignment=AL.CENTER, ink=True,
                          border_radius=BR(20, 20, 20, 20),
                          animate_scale=anim(FAST, ui.POP),
                          on_click=lambda e, ee=em: (page.pop_dialog(),
                                                     page.run_task(_react, m, ee)))
             for em in QUICK],
            spacing=2, alignment=ft.MainAxisAlignment.CENTER)
        items = [ft.Container(quick, padding=PAD(4, 4, 4, 4)),
                 ft.Container(height=1, bgcolor=op(0.08),
                              margin=MG(6, 4, 6, 4))]
        if can_post:      # در کانالِ خواندنی «پاسخ» بی‌معنیه
            items.append(_mi(page, "reply", t("reply"),
                             lambda: _set_reply(m)))
        kind_m = m.get("msg_type") or "text"
        if kind_m in ("video", "audio", "voice") and (m.get("content") or ""):
            # ویدیو پلیرِ خودی رو باز می‌کنه؛ صدا در جای خودش پخش می‌شه
            if kind_m == "video":
                items.append(_mi(page, "play", t("play_here"),
                                 lambda: _media_player(page, m)))
            else:
                items.append(_mi(page, "play", t("play_here"),
                                 lambda: page.run_task(
                                     _toggle_voice, m.get("message_id"),
                                     _media(m.get("content")))))
        if kind_m in MEDIA_KINDS and (m.get("content") or ""):
            items.append(_mi(page, "open", t("open_file"),
                             lambda: page.run_task(_open_media, page, m)))
            items.append(_mi(page, "download", t("save_to_downloads"),
                             lambda: page.run_task(_save_media, page, m)))
        if kind_m == "gif" and (m.get("content") or ""):
            items.append(_mi(page, "gif", t("add_to_gifs"),
                             lambda: page.run_task(_gif_add, m.get("content"))))
        if txt_kind and not was_enc or (txt_kind and was_enc and _enc_on()):
            items.append(_mi(page, "copy", t("copy"),
                             lambda: _copy(body)))
        elif (m.get("caption") or "").strip():
            items.append(_mi(page, "copy", t("copy"),
                             lambda: _copy(m.get("caption") or "")))
        items.append(_mi(page, "forward", t("forward"),
                         lambda: _forward_dialog(m)))
        if not is_saved:
            # در خودِ دفترچه‌ی یادداشت «ذخیره» بی‌معنی است.
            items.append(_mi(page, "bookmark", t("save_msg"),
                             lambda: page.run_task(_do_save, m)))
        if can_manage or (not is_group and me):
            items.append(_mi(page, "pin",
                             t("unpin") if m.get("pinned") else t("pin"),
                             lambda: page.run_task(_do_pin, m,
                                                   not m.get("pinned"))))
        if me and txt_kind:
            items.append(_mi(page, "pencil", t("edit"),
                             lambda: _start_edit(m)))
        items.append(_mi(page, "trash", t("delete"),
                         lambda: _delete_dialog(m, me),
                         danger=True))
        _menu_dialog(page, *items, width=268)

    def _delete_dialog(m, me):
        """مثل تلگرام می‌پرسد: فقط برای من، یا برای همه؟"""
        mid = m.get("message_id")
        rows = [_mi(page, "user", t("delete_for_me"),
                    lambda: page.run_task(_do_delete, mid, False),
                    danger=True)]
        if me or can_manage:
            rows.append(_mi(page, "trash", t("delete_for_all"),
                            lambda: page.run_task(_do_delete, mid, True),
                            danger=True))
        rows.append(ft.Container(
            ft.Text(t("delete_hint"), size=fs(11), color=P["sub"],
                    text_align=ft.TextAlign.CENTER),
            padding=PAD(10, 4, 10, 8)))
        _menu_dialog(page, ft.Container(
            ft.Text(t("delete_which"), size=fs(13.5), color=P["txt"],
                    font_family=ui.FONT_MED, text_align=ft.TextAlign.CENTER),
            padding=PAD(10, 8, 10, 6)), *rows, width=286)

    def _copy(text):
        page.run_task(_copy_text, page, text or "")
        _toast(page, t("copied"), ok=True)

    async def _do_save(m):
        """یک نسخه‌ی مستقل از این پیام در «پیام‌های ذخیره‌شده».

        سرور فایل را فیزیکی کپی می‌کند، پس از این پس پاک‌شدنِ اصل به این
        نسخه کاری ندارد. شناسه‌ی چتِ مقصد در پاسخ می‌آید، پس دکمه‌ی «دیدن»
        بی درخواستِ تازه کار می‌کند.
        """
        try:
            res = await api.save_message(chat_id, m.get("message_id"))
        except Exception as ex:
            _toast(page, _err_text(ex))
            return
        dest = (res or {}).get("chat_id")
        if dest:
            _toast(page, t("saved_ok"), ok=True, action=t("saved_go"),
                   on_action=lambda: page.run_task(chat_page, page, dest))
        else:
            _toast(page, t("saved_ok"), ok=True)

    async def _do_pin(m, on):
        try:
            await api.pin_message(chat_id, m.get("message_id"), on)
        except Exception as ex:
            _toast(page, _err_text(ex))
            return
        m["pinned"] = on
        st["pin"] = m if on else None
        _sync_pin()
        _render()
        _toast(page, t("pinned") if on else t("unpinned"), ok=True)

    async def _do_delete(mid, for_all=False):
        try:
            await api.delete_message(chat_id, mid, for_everyone=bool(for_all))
        except Exception as ex:
            _toast(page, _err_text(ex))
            return
        # مثل تلگرام: پیام کامل از فهرست می‌ره، هیچ ردی نمی‌مونه
        st["msgs"] = [x for x in st["msgs"] if x.get("message_id") != mid]
        for x in st["msgs"]:
            if x.get("reply_to") == mid:
                x["reply_to"] = None
                x["reply_to_text"] = None
        if (st["pin"] or {}).get("message_id") == mid:
            st["pin"] = None
            _sync_pin()
        _render()
        _toast(page, t("deleted"), ok=True)

    def _start_edit(m):
        st["reply"] = None
        st["edit"] = m
        body, _ = _in_text(m)
        inp.value = body
        reply_head.value = t("editing")
        reply_txt.value = body.replace("\n", " ")[:80]
        reply_bar.visible = True          # بی‌انیمیشن (خواستهٔ کاربر)
        _sync_send()
        try:
            page.update()
        except Exception:
            pass
        _focus(page, inp)

    def _forward_dialog(m):
        rows = []
        holder = ft.Column([], spacing=2, tight=True, scroll=ft.ScrollMode.AUTO,
                           height=300)

        async def _send_to(cid):
            try:
                await api.forward_message(chat_id, m.get("message_id"), cid)
            except Exception as ex:
                _toast(page, _err_text(ex))
                return
            page.pop_dialog()
            _toast(page, t("forwarded"), ok=True)

        async def _load():
            try:
                chats = await api.list_chats()
            except Exception as ex:
                _toast(page, _err_text(ex))
                return
            rows.clear()
            for c in chats:
                # عنوانِ دفترچه‌ی یادداشت ترجمه می‌شود و آواتارش نشانک است؛
                # فوروارد به آن‌جا هم مثلِ «ذخیره» نسخه‌ی مستقل می‌سازد.
                sv = _is_saved(c)
                ttl = _chat_title(c)
                rows.append(ui.row_item(
                    None, ttl,
                    subtitle=(t("saved_hint") if sv else
                              (("@" + c["handle"]) if c.get("handle")
                               else None)),
                    trailing=ui.avatar("saved" if sv else (ttl or "?"), 34,
                                       icon=("bookmark" if sv else None),
                                       url=(None if sv
                                            else _media(c.get("avatar_url")))),
                    on_click=lambda e, i=c["chat_id"]: page.run_task(_send_to, i)))
            holder.controls = rows
            try:
                page.update()
            except Exception:
                pass

        ui.dialog(page, t("forward_to"), holder,
                  actions=[ui.ghost_btn(t("cancel"),
                                        on_click=lambda e: page.pop_dialog())])
        page.run_task(_load)

    # ── دسته‌بندیِ تلگرامی ──────────────────────────
    # پیام‌های پشتِ‌سرِ هم از یک فرستنده که فاصله‌شان کم است یک «دسته»‌اند:
    # سرنامِ فرستنده یک‌بار بالای دسته می‌آید، گوشه‌ی دُم فقط روی حبابِ آخر
    # تیز می‌شود و فاصله‌ی درونِ دسته کمتر است.
    RUN_GAP = 300.0         # ثانیه — بیش از این، دسته‌ی تازه

    def _same_run(a, b):
        """آیا `b` ادامه‌ی دسته‌ی `a` است؟ (`a` بالاترِ فهرست است)"""
        if not a or not b:
            return False
        if a.get("sender_id") != b.get("sender_id"):
            return False
        d1, d2 = _local(a.get("created_at")), _local(b.get("created_at"))
        if d1 is None or d2 is None:
            return True         # ساعت خوانده نشد؟ فرستنده‌ی یکسان کافی است
        if d1.date() != d2.date():
            return False        # چیپِ تاریخ بینشان می‌افتد
        return abs((d2 - d1).total_seconds()) <= RUN_GAP

    # ── یک حبابِ پیام ───────────────────────────────
    def _bubble(m, prev=None, nxt=None):
        me = m.get("sender_id") == api.user_id
        first = not _same_run(prev, m)     # سرِ دسته
        last = not _same_run(m, nxt)       # تهِ دسته — دُم این‌جاست
        # رنگ‌های حباب از یک‌جا می‌آیند تا در هر پوسته (روشن/تاریک/دلخواه)
        # هم‌خانواده بمانند: متن، متنِ کم‌رنگ، و لهجه‌ی خوانا روی همین حباب.
        b_txt, b_dim, b_acc = ui.on_bubble(me), ui.sub_bubble(me), ui.bub_acc(me)
        # پهنای مدیا هم زیرِ سقفِ حباب می‌ماند تا روی صفحه‌ی باریک
        # از حباب بیرون نزند (پدینگِ ۱۲+۱۲ کم شده).
        MW = int(max(150, min(240, ui.bubble_max() - 26)))
        kind_m = m.get("msg_type") or "text"
        inner = []
        pic_i = None            # جای مدیا در ستون (برای کپسولِ شناورِ ساعت)
        stk_look = False        # پیامِ «استیکری»: بزرگ و بی‌حباب

        if is_group and not is_channel and not me and first:
            inner.append(ft.Text(m.get("sender_name") or "", size=fs(12),
                                 weight=W.W_600, font_family=ui.FONT_MED,
                                 color=b_acc))
        if m.get("forward_from"):
            inner.append(ft.Row(
                [ui.vic("forward", 12, b_dim),
                 ft.Text(t("forwarded_from", name=m["forward_from"]),
                         size=fs(11), color=b_dim, italic=True)],
                spacing=4, tight=True))
        if m.get("reply_to_text") or m.get("reply_to"):
            snip = m.get("reply_to_text") or ""
            if crypto.is_encrypted(snip):
                snip = t("enc_locked")
            inner.append(ft.Container(
                ft.Row([ft.Container(width=2, height=26, bgcolor=b_acc,
                                     border_radius=BR(2, 2, 2, 2)),
                        _rich(page, snip, b_dim, fs(11), sel=False)],
                       spacing=6),
                bgcolor=op(0.10), border_radius=BR(8, 8, 8, 8),
                padding=PAD(6, 5, 8, 5), margin=MG(0, 0, 0, 5), ink=True,
                on_click=lambda e, r=m.get("reply_to"): page.run_task(_jump_to, r)))

        if m.get("deleted"):
            inner.append(ft.Row([ui.vic("trash", 13, b_dim),
                                 ft.Text(t("msg_deleted"), size=fs(12.5),
                                         color=b_dim, italic=True)],
                                spacing=5, tight=True))
        elif kind_m in ("image", "gif"):
            # از کَش می‌خوانیم اگر باشد؛ وگرنه از شبکه و در پس‌زمینه روی
            # دیسک می‌نشیند تا رندرِ بعدی (و حلقه‌ی گیف) بی‌تأخیر باشد.
            src_m = _cached_src(m.get("content"))
            if src_m and str(src_m).startswith(("http://", "https://")):
                page.run_task(_prefetch_media, m.get("content"))
            pic = None
            if kind_m == "gif" and ui.is_video_src(src_m):
                # گیفِ تلگرامی در واقع mp4ِ بی‌صداست؛ با پخش‌کننده نشان می‌دهیم
                pic = ui.loop_video(src_m, width=MW, height=int(MW*0.75),
                                    fit=ft.BoxFit.COVER, radius=12)
            if pic is None:
                pic = ui.photo(src_m, width=MW, radius=12,
                               fit=ft.BoxFit.COVER,
                               error=ft.Text(_kind_label(kind_m),
                                             size=fs(12), color=op(0.7)))
            body_pic = pic
            if kind_m == "gif":
                # برچسبِ گوشه — همان‌جور که تلگرام گیف را نشان می‌دهد
                body_pic = ft.Stack([
                    pic,
                    ft.Container(ui.tag_chip("GIF"), alignment=AL.TOP_LEFT,
                                 padding=PAD(6, 6, 6, 6)),
                ])
            inner.append(ft.Container(
                body_pic, border_radius=BR(12, 12, 12, 12), ink=True,
                animate_scale=anim(FAST, ui.POP),
                tooltip=t("tap_to_open"),
                on_click=lambda e, mm=m: _media_viewer(page, mm)))
            pic_i = len(inner) - 1
        elif kind_m == "video":
            ext_v = (_ext_of(_fname_raw(m.get("content"))) or "mp4").upper()
            # قابِ اولِ ویدیو به‌جای آیکونِ خالی (پخش نمی‌شه، فقط پیش‌نمایش)
            poster = ui.loop_video(_media(m.get("content")), width=MW,
                                   height=int(MW*0.617), fit=ft.BoxFit.COVER,
                                   autoplay=False, loop=False)
            if poster is None:
                poster = ft.Container(ui.vic("film", 42, op(0.30)), width=MW,
                                      height=int(MW*0.617), alignment=AL.CENTER)
            inner.append(ft.Container(
                ft.Stack([
                    ft.Container(poster, width=MW, height=int(MW*0.617),
                                 alignment=AL.CENTER,
                                 bgcolor=op(0.30, "#000000"),
                                 border_radius=BR(12, 12, 12, 12),
                                 clip_behavior=ft.ClipBehavior.ANTI_ALIAS),
                    ft.Container(ui.play_badge(), width=MW, height=int(MW*0.617),
                                 alignment=AL.CENTER),
                    ft.Container(ui.tag_chip(ext_v), width=MW, height=int(MW*0.617),
                                 alignment=AL.BOTTOM_LEFT,
                                 padding=PAD(7, 7, 7, 7)),
                ]),
                width=MW, height=int(MW*0.617), ink=True,
                border_radius=BR(12, 12, 12, 12),
                animate_scale=anim(FAST, ui.POP), tooltip=t("tap_to_play"),
                on_click=lambda e, mm=m: _media_player(page, mm)))
            pic_i = len(inner) - 1
        elif kind_m == "audio":
            # موسیقی در جای خودش پخش می‌شه، نه در پنجره‌ی تمام‌صفحه
            inner.append(_audio_row(m, me))
        elif kind_m == "voice":
            inner.append(_voice_row(m, me))
        elif kind_m == "file":
            holder = ft.Container(animate_opacity=MED)
            holder.content = _file_card(m, holder)
            inner.append(holder)
        else:
            body, was_enc = _in_text(m)
            # موقعیتِ جغرافیایی: پیام متن است (دو خط، خطِ دومش لینکِ گوگل‌مپ)
            # ولی به‌جای آن دو خط، کارتِ نقشه می‌نشیند. `geo.parse` سخت‌گیر
            # است، پس جمله‌ای که تویش لینکِ نقشه بود متنش ناپدید نمی‌شود.
            loc = None if was_enc else geo.parse(body)
            # پیامی که فقط یک‌تا‌سه ایموجی است «استیکر» است: بزرگ و بی‌حباب
            # نشان داده می‌شود (همان‌کاری که تلگرام می‌کند). شرطِ `not inner`
            # یعنی سرنام/فوروارد/پاسخ ندارد، وگرنه حبابِ معمولی لازم است.
            if loc:
                inner.append(_map_card(page, loc[0], loc[1], MW))
            elif body and not was_enc and not inner and _is_sticker_text(body):
                stk_look = True
                inner.append(ft.Text(body, size=fs(54),
                                     text_align=ft.TextAlign.CENTER))
            elif was_enc:
                line = _rich(page, body, b_txt, fs(14))
                inner.append(ft.Row([ui.vic("lock", 12, b_dim), line],
                                    spacing=5,
                                    vertical_alignment=ft.CrossAxisAlignment.START))
            else:
                inner.append(_rich(page, body, b_txt, fs(14)))

        # ── کپشنِ زیرِ مدیا ──────────────────────────
        cap_m = (m.get("caption") or "").strip()
        if cap_m and kind_m != "text" and not m.get("deleted"):
            inner.append(ft.Container(
                _rich(page, cap_m, b_txt, fs(13.5)),
                margin=MG(0, 5, 0, 0), width=MW))

        rr = _reaction_row(m)
        if rr is not None:
            inner.append(ft.Container(rr, margin=MG(0, 3, 0, 0)))

        # روی عکس/ویدیوِ بی‌کپشن، ساعت و تیک‌ها کپسولِ شناورِ تیره می‌شن
        floaty = (pic_i is not None and not cap_m and rr is None
                  and not m.get("deleted"))
        m_col = op(0.90, "#ffffff") if floaty else b_dim
        meta = []
        if m.get("pinned"):
            meta.append(ui.vic("pin", 11, m_col))
        if m.get("edited"):
            meta.append(ft.Text(t("edited"), size=fs(9.5), color=m_col))
        meta.append(ft.Text(_hhmm(m.get("created_at")), size=fs(10.5),
                            color=m_col, no_wrap=True))
        if me:
            # یک تیک = رسیده، دو تیک = طرف خوانده (مثلِ تلگرام). تیکِ دوبل
            # پهن‌تر است، پس کمی درشت‌تر گرفته می‌شود تا هم‌وزنِ ساعت بنشیند.
            seen_by_peer = _is_read(st.get("read_at"), m.get("created_at"))
            tick = "check_double" if seen_by_peer else "check"
            meta.append(ui.vic(
                tick, 15 if seen_by_peer else 13,
                (op(0.98, "#ffffff") if floaty
                 else (b_acc if seen_by_peer else m_col))))
        # فاصله‌ی ۳ (به‌جای ۴) ساعت و تیک را مثلِ تلگرام یک واحد می‌کند؛
        # `no_wrap` هم نمی‌گذارد ساعت وسطش بشکند.
        meta_row = ft.Row(meta, spacing=3, tight=True, wrap=False,
                          alignment=ft.MainAxisAlignment.END,
                          vertical_alignment=ft.CrossAxisAlignment.CENTER)
        if floaty:
            inner[pic_i] = ft.Stack([
                inner[pic_i],
                ft.Container(
                    ft.Container(
                        meta_row, padding=PAD(8, 3, 8, 4),
                        border_radius=BR(12, 12, 12, 12),
                        bgcolor=op(0.42 if not ui.is_phone() else 0.55,
                                   "#000000"),
                        blur=ui.blur_of(10, heavy=True)),
                    alignment=(AL.BOTTOM_LEFT if i18n.rtl()
                               else AL.BOTTOM_RIGHT),
                    padding=PAD(7, 7, 7, 7)),
            ])
        else:
            pass          # ساعت/تیک در پاصفحه‌ی حباب می‌نشیند (پایین‌تر)

        lite_b = ui.is_light()
        # روی گوشی حبابِ پیام بی‌تاری و کاملاً پُر می‌شود: تاری به‌ازای هر
        # حباب یعنی دهها BackdropFilter در یک لیستِ اسکرول‌شو (منبعِ اصلیِ
        # لنگیِ اندروید) و چون پُرکننده از قبل ۹۲–۹۷٪ کدر بود، فرقی نمی‌کند.
        solid_bub = 1.0 if ui.is_phone() else (0.97 if lite_b else 0.92)
        body_col = ft.Column(inner, spacing=3, tight=True,
                             horizontal_alignment=ft.CrossAxisAlignment.START)
        # پاصفحه‌ی حباب: متن بالا، ساعت و تیک‌ها پایین-انتها.
        # `wrap=True` یعنی اگر سطرِ آخرِ متن جا داشت ساعت کنارش می‌نشیند
        # (همان‌کاری که تلگرام می‌کند) و اگر نداشت می‌افتد سطرِ بعد. هیچ‌وقت
        # روی متن نمی‌افتد و متن هم زیرش پنهان نمی‌شود. فاصله‌ی ۶ (به‌جای ۸)
        # ساعت را مثلِ تلگرام به دُمِ جمله نزدیک‌تر می‌کند.
        body = body_col if floaty else ft.Row(
            [body_col, ft.Container(meta_row, margin=MG(0, 2, 0, 0))],
            wrap=True, spacing=6, run_spacing=1,
            alignment=ft.MainAxisAlignment.END,
            vertical_alignment=ft.CrossAxisAlignment.END)
        bub = ft.Container(
            body,
            bgcolor=(op(solid_bub, P["sent"]) if me
                     else op(solid_bub, P["recv"])),
            border_radius=ui.bubble_radius(me, last),
            padding=PAD(12, 8, 12, 7),
            blur=ui.blur_of(14, heavy=True),
            border=ft.Border(*[ft.BorderSide(1, op(0.06 if lite_b else 0.07))] * 4),
            shadow=ft.BoxShadow(
                blur_radius=10 if lite_b else 12, spread_radius=-4,
                color=op(0.10 if lite_b else 0.35, "#000000"),
                offset=OFF(0, 3 if lite_b else 4)),
            animate_opacity=MED, animate_offset=MED, animate=anim(MED, EASE),
        )
        # استیکر حباب ندارد: بی‌پُرکننده، بی‌قاب، بی‌سایه، بی‌تاری — فقط
        # خودِ نویسه‌ی بزرگ روی پس‌زمینه‌ی گفت‌وگو، با ساعت و تیکِ کنارش.
        if stk_look and rr is None and not cap_m:
            bub.bgcolor = None
            bub.blur = None
            bub.border = None
            bub.shadow = None
            bub.padding = PAD(4, 0, 4, 0)
        # سقفِ پهنا (۷۵٪ عرضِ صفحه): این ظرفِ نامرئی پهنای تنگ می‌گیرد ولی
        # به حباب قیدِ شُل می‌دهد، پس حبابِ کوتاه به‌اندازه‌ی متنش جمع می‌شود
        # و حبابِ بلند سرِ سقف می‌شکند و چند سطر می‌شود (نه بریده‌شدنِ افقی).
        capped = ft.Container(
            bub, width=ui.bubble_max(),
            alignment=(AL.CENTER_RIGHT if me else AL.CENTER_LEFT),
            offset=ft.Offset(0, 0), animate_offset=anim(FAST, EASE))
        # ── کشیدن برای پاسخ ───────────────────────────
        # حباب را با انگشت می‌کشی؛ از یک آستانه که گذشت، نوارِ «پاسخ به»
        # باز می‌شود و حباب نرم به جای خودش برمی‌گردد. آستانه به پیکسل است
        # ولی `offset` کسری است، پس بر پهنای حباب تقسیم می‌شود.
        SW_MAX, SW_TRIG = 64.0, 46.0
        sw = {"dx": 0.0, "fired": False}
        rep_ic = ft.Container(
            ui.vic("reply", 16, P["acc"]),
            width=30, height=30, alignment=AL.CENTER,
            border_radius=BR(15, 15, 15, 15), bgcolor=op(0.16, P["acc"]),
            opacity=0.0, animate_opacity=anim(FAST, EASE))

        def _sw_paint():
            w = max(1.0, ui.bubble_max())
            capped.offset = ft.Offset(sw["dx"] / w, 0)
            rep_ic.opacity = min(1.0, abs(sw["dx"]) / SW_TRIG)
            try:
                capped.update()
                rep_ic.update()
            except Exception:
                pass

        def _sw_start(e):
            sw["dx"], sw["fired"] = 0.0, False

        def _sw_move(e):
            d = getattr(e, "local_delta", None)
            step = float(getattr(d, "x", 0.0) or 0.0) if d else 0.0
            if not step:
                step = float(getattr(e, "primary_delta", 0.0) or 0.0)
            sw["dx"] = max(-SW_MAX, min(SW_MAX, sw["dx"] + step))
            if not sw["fired"] and abs(sw["dx"]) >= SW_TRIG:
                sw["fired"] = True          # همان‌جا شلیک کن، نه سرِ رها کردن
                _set_reply(m)               # خودش نوارِ پاسخ را می‌کشد
            _sw_paint()

        def _sw_end(e):
            sw["dx"] = 0.0
            _sw_paint()

        tap = ft.GestureDetector(
            ft.Stack([capped,
                      ft.Container(rep_ic, alignment=AL.CENTER,
                                   top=0, bottom=0,
                                   **({"left": 0} if me else {"right": 0}))]),
            mouse_cursor=ft.MouseCursor.CLICK, drag_interval=40,
            on_secondary_tap=lambda e, mm=m: _msg_menu(mm),
            on_long_press=lambda e, mm=m: _msg_menu(mm),
            on_horizontal_drag_start=_sw_start,
            on_horizontal_drag_update=_sw_move,
            on_horizontal_drag_end=_sw_end,
            on_horizontal_drag_cancel=_sw_end)
        # حاشیه‌ی فیزیکی: حبابِ من همیشه سمتِ راستِ صفحه در فارسی
        mine = (ft.MainAxisAlignment.START if i18n.rtl()
                else ft.MainAxisAlignment.END)
        theirs = (ft.MainAxisAlignment.END if i18n.rtl()
                  else ft.MainAxisAlignment.START)
        mid_key = m.get("message_id") or ""
        # لنگرِ پرش: تنها کنترلِ «کلیددار» است و هرگز تغییر نمی‌کند.
        # (کلید روی کنترلِ قابلِ‌تغییر باعثِ خطای Frozen controls می‌شود)
        anchor = ft.Container(width=0, height=0, key=ft.ScrollKey(mid_key))
        hot = st.get("hl") == mid_key and mid_key != ""
        # فاصله‌ی عمودی: درونِ دسته تنگ، بینِ دسته‌ها باز. حاشیه‌ها در فلاتر
        # جمع می‌شوند، پس ۱+۱ می‌شود ۲ و ۴+۴ می‌شود ۸ پیکسل.
        g_top = ui.GAP_OUT if first else ui.GAP_IN
        g_bot = ui.GAP_OUT if last else ui.GAP_IN
        wrap = ft.Container(
            ft.Row(([anchor, tap] if me else [tap, anchor]),
                   alignment=mine if me else theirs),
            margin=MG(48 if me else 8, g_top, 8 if me else 48, g_bot),
            bgcolor=(op(0.16, P["acc"]) if hot else None),
            border_radius=BR(18, 18, 18, 18), animate=anim(SLOW, EASE),
            animate_opacity=anim(MED, EASE), animate_scale=anim(MED, ui.POP),
            scale=(1.02 if hot else 1.0), opacity=1.0)
        st["keys"][mid_key] = anchor
        st["wraps"][mid_key] = wrap
        return wrap

    def _render(intro=False):
        _player.clear()
        st["keys"].clear()
        st["wraps"].clear()
        rows, last_day = [], None
        msgs = st["msgs"]
        for i, m in enumerate(msgs):
            dk = _daykey(m.get("created_at"))
            if dk and dk != last_day:
                rows.append(ui.date_chip(_daylabel(m.get("created_at"))))
                last_day = dk
            rows.append(_bubble(m, msgs[i - 1] if i else None,
                                msgs[i + 1] if i + 1 < len(msgs) else None))
        msg_list.controls = rows
        msg_list.visible = bool(rows)
        empty.visible = not rows
        # ورودِ پله‌ایِ اولِ باز شدنِ گفت‌وگو (فقط دُمِ لیست — همان‌هایی که دیده می‌شوند)
        tail = rows[-14:] if (intro and rows) else []
        for c in tail:
            ui.prep_in(c, dy=0.14, ms=MED)
        # ⚠️ ۱.۵: اینجا `page.update()` بود. `_render` از هر رویدادِ کوچکی صدا
        # زده می‌شود — رسیدنِ تیکِ «خوانده شد»، یک واکنش، یک ویرایش — و هر بار
        # کلِ درختِ صفحه با سرور مقایسه می‌شد. تنها همین دو کنترل عوض می‌شوند.
        # (این صدا همیشه بعدِ `_go` است، پس کنترل‌ها به صفحه چسبیده‌اند.)
        _repaint(page, msg_list, empty)
        if tail:
            try:
                page.run_task(ui.enter, page, *tail, stagger=0.018,
                              first_delay=0.02)
            except Exception:
                for c in tail:
                    ui.play_in(c)

    def _add_msg(m):
        dk = _daykey(m.get("created_at"))
        last_day = None
        prev = None
        for x in reversed(st["msgs"]):
            last_day = _daykey(x.get("created_at"))
            prev = x
            break
        st["msgs"].append(m)
        if dk and dk != last_day:
            msg_list.controls.append(ui.date_chip(_daylabel(m.get("created_at"))))
        # اگر این پیام ادامه‌ی دسته‌ی پیامِ قبلی است، حبابِ قبلی دیگر «تهِ
        # دسته» نیست: دُمش را پس می‌گیریم و از نو می‌سازیمش. بدونِ این، دو
        # حبابِ پشتِ‌سرِ هم هر دو دُم می‌گرفتند.
        cont = _same_run(prev, m)
        if cont and msg_list.controls:
            msgs = st["msgs"]
            msg_list.controls[-1] = _bubble(
                prev, msgs[-3] if len(msgs) >= 3 else None, m)
        b = _bubble(m, prev, None)
        ui.prep_in(b, dy=0.20, ms=MED)
        msg_list.controls.append(b)
        msg_list.visible = True
        empty.visible = False
        page.run_task(ui.enter, page, b, stagger=0.0, first_delay=0.01)
        # مثل تلگرام: اگر پایینِ گفت‌وگو بودیم می‌چسبیم به پایین، وگرنه
        # فلشِ پایین با شمارشِ پیام‌های نو می‌ماند
        if st["bottom"] or m.get("sender_id") == api.user_id:
            page.run_task(_scroll_end, True)
        else:
            st["down"] += 1
            _sync_down()

    # ── نوارِ پاسخ / ویرایش ─────────────────────────
    reply_txt = ft.Text("", size=fs(12), color=P["sub"], max_lines=1,
                        overflow=ft.TextOverflow.ELLIPSIS, expand=True)
    reply_head = ft.Text(t("reply_to"), size=fs(10), color=P["acc"])
    reply_bar = ft.Container(
        ft.Row([ft.Container(width=2, height=30, bgcolor=P["acc"],
                             border_radius=BR(2, 2, 2, 2)),
                ft.Column([reply_head, reply_txt], spacing=0, tight=True,
                          expand=True),
                ui.icon_btn("close", box=32, size=16,
                            on_click=lambda e: _clear_compose())],
               spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        padding=PAD(10, 4, 6, 4), margin=MG(0, 0, 0, 6),
        bgcolor=op(0.06), border_radius=BR(12, 12, 12, 12),
        visible=False)          # بی‌انیمیشن (خواستهٔ کاربر)

    def _snippet(m):
        k = m.get("msg_type") or "text"
        if k != "text":
            cap = (m.get("caption") or "").strip()
            if k == "file":
                return cap or _fname(m.get("content"))
            return f"{_kind_label(k)}{' — ' + cap if cap else ''}"
        body, _ = _in_text(m)
        return body

    def _clear_compose():
        st["reply"] = None
        if st["edit"] is not None:
            st["edit"] = None
            inp.value = ""
        reply_bar.visible = False
        _sync_send()
        # ۱.۶: سرِ *هر* فرستادن کلِ صفحه از نو مقایسه می‌شد. حالا فقط نوارِ
        # پاسخ و کلیدهای کپسولِ نوشتن.
        _repaint(page, reply_bar, inp, send_ic, send_circle, attach_btn,
                 gif_btn)

    def _set_reply(m):
        st["edit"] = None
        st["reply"] = m
        if m:
            reply_head.value = t("reply_to")
            reply_txt.value = _snippet(m).replace("\n", " ")[:80]
            reply_bar.visible = True       # بی‌انیمیشن (خواستهٔ کاربر)
        else:
            reply_bar.visible = False
        # ۱.۶: این تابع از دستِ *کشیدنِ انگشت* (سویپ برای پاسخ) هم صدا زده
        # می‌شود؛ `page.update()` آن‌جا وسطِ حرکت یک مقایسه‌ی کاملِ درخت بود و
        # سویپ را تکان‌دار می‌کرد.
        _repaint(page, reply_bar, reply_head, reply_txt)
        if m:
            _focus(page, inp)

    # ── ورودی ───────────────────────────────────────
    # فیلدِ بی‌پس‌زمینه، چون داخلِ قرصِ شیشه‌ایِ باکسِ نوشتن می‌نشیند
    inp = ui.composer_field(hint=t("message"))

    async def _push(body, kind_out, caption=None):
        """اول با وب‌سوکت؛ اگه قطع بود، با HTTP و افزودنِ محلیِ حباب."""
        reply_id = (st["reply"] or {}).get("message_id")
        _clear_compose()
        cap = (caption or "").strip() or None
        wire, enc = ((body, False) if kind_out != "text"
                     else _out_text(body))
        if await ws_client.send(chat_id, wire, kind_out, reply_id, enc=enc,
                                caption=cap):
            return  # سرور خودش پیام رو برای فرستنده هم پس می‌فرسته
        try:
            res = await api.send_message(chat_id, wire, kind_out, reply_id,
                                         enc=enc, caption=cap)
        except Exception as ex:
            _toast(page, _err_text(ex))
            return
        mid = res.get("message_id")
        if mid and mid not in st["seen"]:
            st["seen"].add(mid)
            _add_msg(res)

    async def _apply_edit(m, body):
        wire, enc = _out_text(body)
        try:
            res = await api.edit_message(chat_id, m.get("message_id"), wire,
                                         enc=enc)
        except Exception as ex:
            _toast(page, _err_text(ex))
            return
        for x in st["msgs"]:
            if x.get("message_id") == m.get("message_id"):
                x["content"] = res.get("content", wire)
                x["enc"] = res.get("enc", enc)
                x["edited"] = True
        _render()

    async def _send_text(e=None):
        body = (inp.value or "").strip()
        if not body:
            return
        editing = st["edit"]
        inp.value = ""
        _clear_compose()                  # خودش کپسولِ نوشتن را می‌کشد
        _focus(page, inp)                 # بعدِ فرستادن، نشانگر سرِ جاش بمونه
        if editing is not None:
            await _apply_edit(editing, body)
            return
        await _push(body, "text")

    async def _send_media(url, kind_out, caption=None):
        await _push(url, kind_out, caption=caption)

    async def _upload(name, data):
        """آپلود با نوارِ پیشرفت → آدرسِ فایل روی سرور، یا None."""
        prog = ui.progress_bar(0.0, color=P["acc"])
        card = ft.Container(
            _prog_card(name, ui.human_size(len(data)), t("uploading"), prog),
            margin=MG(0, 0, 0, 6))
        up_bar.content = card
        up_bar.visible = True             # بی‌انیمیشن (خواستهٔ کاربر)
        _repaint(page, up_bar)

        def _on(done, total):
            prog.value = (done / total) if total else None
            try:
                prog.update()
            except Exception:
                pass

        url = None
        try:
            res = await api.upload_media_bytes(name, data, on_progress=_on)
            url = res.get("url")
        except Exception as ex:
            _toast(page, _err_text(ex))
        up_bar.visible = False
        _repaint(page, up_bar)
        return url

    def _local_preview(name, size_text, data=None):
        """پیش‌نمایشِ فایلِ انتخاب‌شده — عکس اگر شد، وگرنه کارتِ فایل.

        روی وب مسیرِ محلی نداریم (و پس از ویرایش هم ممکن است نداشته باشیم)؛
        آن‌جا خودِ بایت‌ها را به `ft.Image` می‌دهیم که `src` بایت هم می‌پذیرد.
        """
        kind_in = _kind_for(name)
        path = _last_pick.get("path")
        src = path if (path and os.path.isfile(path)) else (data or None)
        if kind_in in ("image", "gif") and src:
            return ft.Container(
                ui.photo(src, fit=ft.BoxFit.COVER,
                         width=ui.body_w(300), height=200, radius=12,
                         error=ft.Container(ui.vic("image", 30, op(0.5)),
                                            alignment=AL.CENTER)),
                alignment=AL.CENTER, height=200)
        ic = {"video": "film", "audio": "music", "gif": "gif"}.get(
            kind_in, ui.file_icon_for(name))
        return ui.file_card(name, size_text, icon=ic, color=P["acc"],
                            sub=_kind_label(kind_in))

    async def _compose_media(name, data, caption=""):
        """پیش‌نمایش + کپشن (+ پرسشِ «به شکلِ گیف بفرستم؟» برای ویدیو)."""
        kind_in = _kind_for(name)
        cap = ui.field(label=t("caption"), hint=t("caption_hint"),
                       multiline=True, value=caption)
        flag = {"gif": False}

        def _toggle(e):
            flag["gif"] = bool(getattr(e.control, "value", False))

        body = [_local_preview(name, ui.human_size(len(data)), data),
                ui.spacer(h=10), cap]
        if kind_in == "video":
            # ویدیو می‌تواند مثلِ تلگرام «گیف» (ویدیوی بی‌صدا و تکرارشو) باشد
            body.append(ui.spacer(h=8))
            body.append(ui.toggle_row("gif", t("send_as_gif"), False, _toggle,
                                      subtitle=t("send_as_gif_sub")))

        async def _go():
            text = (cap.value or "").strip()
            as_gif = flag["gif"]
            page.pop_dialog()
            try:
                page.update()
            except Exception:
                pass
            url = await _upload(name, data)
            if not url:
                return
            kind_out = "gif" if as_gif else kind_in
            await _send_media(url, kind_out, caption=text)

        async def _edit():
            """ویرایشگر را باز می‌کند و با همان کپشن برمی‌گردد این‌جا."""
            text = (cap.value or "").strip()
            page.pop_dialog()
            try:
                page.update()
            except Exception:
                pass
            got = await _edit_photo(page, name, data)
            n2, d2 = got if got else (name, data)
            await _compose_media(n2, d2, caption=text)

        acts = [ui.ghost_btn(t("cancel"), on_click=lambda e: page.pop_dialog())]
        if kind_in == "image":
            acts.append(ui.ghost_btn(t("edit"),
                                     on_click=lambda e: page.run_task(_edit)))
        acts.append(ui.primary_btn(t("send"), width=120, height=42,
                                   on_click=lambda e: page.run_task(_go)))

        ui.dialog(
            page, t("send_file"),
            ft.Container(ft.Column(body, spacing=0, tight=True,
                                   scroll=ft.ScrollMode.AUTO),
                              width=ui.body_w(330)),
            actions=acts, width=380)

    # ── برگه‌ی پیوست (سنجاق) ─────────────────────────
    # خواسته‌ی کاربر: «وقتی سنجاق را می‌زنم گالری بالا بیاید و آن پایین بتوانم
    # فایل یا لوکیشن یا آهنگ بفرستم». پس یک BottomSheet: بالا شبکه‌ی
    # عکس‌های تازه‌ی دستگاه، پایین ردیفِ گالری/فایل/موسیقی/موقعیت.
    # هر گزینه اول برگه را می‌بندد و بعد کارش را می‌کند؛ اگر انتخابگرِ سیستم
    # زیرِ یک پنجره‌ی مودال باز شود، روی اندروید قفل/کرش می‌شود.
    async def _attach_pic(path):
        data = await _read_bytes(path)
        if not data:
            _toast(page, t("error"))
            return
        _last_pick["path"] = path
        await _compose_media(os.path.basename(path), data)

    async def _attach_pick(ftype, title, perms=()):
        if perms:
            await _ask_perm(page, *perms)
        name, data = await _pick(page, ANY_EXT, title, ftype=ftype)
        if not data:
            return
        await _compose_media(name, data)

    async def _attach_location():
        pos = await _device_position(page)
        if not pos:
            return
        lat, lon = pos
        # قالبِ پیام در `geo.compose` یک‌جا نگه داشته شده: خطِ دوم همان
        # `www.google.com/maps?q=…` است که هم گیرنده‌ی تازه کارتِ نقشه‌اش را
        # می‌کشد، هم نسخه‌ی قدیمیِ برنامه (و هر پیام‌رسانِ دیگری) لینکِ
        # کلیک‌پذیر می‌بیند.
        await _push(geo.compose(lat, lon, t("att_location")), "text")
        _toast(page, t("loc_sent"), ok=True)

    def _att_tile(icon, label, run, c1, c2):
        circle = ft.Container(
            ui.vic(icon, 22, "#ffffff"), width=52, height=52,
            alignment=AL.CENTER, border_radius=BR(26, 26, 26, 26),
            gradient=ft.LinearGradient(begin=AL.TOP_CENTER,
                                       end=AL.BOTTOM_CENTER, colors=[c1, c2]))
        return ft.Container(
            ft.Column([circle,
                       ft.Text(label, size=fs(11.5), color=P["sub"],
                               font_family=ui.FONT, no_wrap=True)],
                      spacing=6, tight=True,
                      horizontal_alignment=ft.CrossAxisAlignment.CENTER),
            padding=PAD(8, 6, 8, 6), border_radius=BR(16, 16, 16, 16),
            ink=True, tooltip=label,
            on_click=lambda e: _att_go(run))

    def _att_go(run):
        """برگه را می‌بندد، بعد کار را در تسکِ خودش اجرا می‌کند.

        `run_task` فقط تابعِ کوروتین می‌پذیرد، پس لامبدا را در یک `async def`
        می‌پیچیم؛ همان پیچش، هر استثنایی را هم به یک نانِ تُست تبدیل می‌کند تا
        هیچ گزینه‌ای از این برگه نتواند برنامه را بیندازد.
        """
        try:
            page.pop_dialog()
        except Exception:
            pass
        # نگهبانِ سنجاق را هم باز می‌کنیم (اگر `on_dismiss` سرِ بستنِ برنامه‌ای
        # نیامد، بی این خط سنجاق دیگر جواب نمی‌داد).
        _att["open"] = False

        async def _run():
            try:
                await run()
            except Exception as ex:
                _toast(page, _err_text(ex))

        page.run_task(_run)

    # سقفِ کاشی‌های «عکس‌های تازه». پانزده‌تا بود و همان کرشِ حافظه را می‌داد؛
    # با سقفِ رمزگشاییِ `ui.photo` هشت‌تا خیلی سبک است و برای یک نوارِ افقی
    # هم به‌قدرِ کافی زیاد.
    ATT_THUMBS = 8

    # نگهبانِ «برگه‌ی سنجاق باز است؟» — ضربه‌ی دوم نباید برگه‌ی دوم بسازد.
    # (دو برگه = دو `_fill_strip` = دو درخواستِ مجوزِ همزمان = فریز.)
    _att = {"open": False}

    # سقفِ زمانِ پر شدنِ نوارِ عکس‌ها. رد شد؟ گالریِ سیستم را پیشنهاد می‌دهیم.
    ATT_FILL_T = 12.0

    async def _attach(e=None):
        """برگه‌ی پیوست — *بی‌درنگ* باز می‌شود و بعد پر می‌شود.

        ⚠️ کرشِ نسخه‌ی ۱.۴ همین‌جا بود و دو علت داشت:
          ۱) عکس‌ها بی سقفِ رمزگشایی نشان داده می‌شدند ⇒ هر عکسِ دوربین ده‌ها
             مگابایت رَم می‌گرفت و اندروید پروسه را می‌کشت («چهار عکس نشان داد
             و بست»). `ui.photo` حالا سقف می‌گذارد.
          ۲) `_recent_pics()` روی *حلقه‌ی رویداد* پوشه‌ها را می‌گشت (تا ۷ پوشه ×
             ۴۰۰ فایل، با `stat` برای هرکدام) ⇒ رابط تا پایانِ گشتن یخ می‌زد و
             برگه دیر باز می‌شد. حالا در نخِ جدا (`to_thread`) می‌گردد.

        ترتیب هم عوض شده: اول برگه را نشان می‌دهیم (تا ضربه‌ی کاربر فوری جواب
        بگیرد)، بعد در پس‌زمینه مجوز می‌گیریم و می‌گردیم و نوارِ عکس‌ها را
        جا می‌گذاریم. این‌طور «موسیقی/موقعیت/سند» هرگز پشتِ گالری گیر نمی‌کنند
        — همان چیزی که کاربر می‌خواست.

        ⚠️ فریزِ نسخه‌ی ۱.۵ (خواسته‌ی کاربر: «وقتی رو سنجاق میزنم برنامه فریز
        می‌شود») هم همین‌جا بود، و علتش *مجوز* بود نه عکس:
          • `_fill_strip` صاف `_perm.request(PHOTOS)` می‌زد. تماسِ نیتیوِ Flet
            سقفِ زمان ندارد (`timeout=None`)، پس یک جوابِ نیامده تسک را تا ابد
            پارک می‌کرد و نوار همیشه جای‌نگهدار می‌ماند.
          • ضربه‌ی دومِ کاربر روی سنجاق برگه‌ی دوم و درخواستِ مجوزِ دوم می‌ساخت؛
            اندروید دو درخواستِ همزمان را جواب *نمی‌دهد* ⇒ هر دو قفل.
        سه درمان: (۱) نگهبانِ ضربه‌ی دوم، (۲) در این مسیر مجوز را فقط
        *می‌پرسیم* و نمی‌خواهیم — انتخابگرِ سیستم بی‌مجوز هم کار می‌کند، و
        (۳) کلِ پر شدنِ نوار سقفِ زمان دارد و در بدترین حالت به گالریِ سیستم
        می‌افتد. → `_ask_perm` / `_perm_status`
        """
        if _att["open"]:
            return                    # ضربه‌ی دومِ سنجاق را می‌خوریم
        _att["open"] = True
        # جای نوارِ عکس‌ها: اول یک جای‌نگهدار، بعد پر می‌شود.
        strip = ft.Container(height=96, alignment=AL.CENTER,
                             border_radius=BR(14, 14, 14, 14),
                             bgcolor=op(0.04, "#ffffff"),
                             content=ft.Row(
                                 [ui.vic("image", 18, op(0.35)),
                                  ft.Text(t("att_recent"), size=fs(12),
                                          color=op(0.45), font_family=ui.FONT)],
                                 spacing=8,
                                 alignment=ft.MainAxisAlignment.CENTER))
        head_txt = ft.Text(t("att_recent"), size=fs(12.5), color=P["sub"],
                           font_family=ui.FONT_MED)

        def _gallery_fallback():
            """جای نوارِ عکس: یک دکمه‌ی بزرگ که گالریِ سیستم را باز می‌کند."""
            return ft.Container(
                ft.Row([ui.vic("image", 20, P["sub"]),
                        ft.Text(t("att_no_recent"), size=fs(12.5),
                                color=P["sub"], font_family=ui.FONT)],
                       spacing=8, alignment=ft.MainAxisAlignment.CENTER),
                height=96, alignment=AL.CENTER, ink=True,
                border_radius=BR(14, 14, 14, 14),
                bgcolor=op(0.05, "#ffffff"),
                on_click=lambda e: _att_go(
                    lambda: _attach_pick(ft.FilePickerFileType.IMAGE,
                                         t("att_pick_photo"),
                                         ("PHOTOS", "VIDEOS"))))

        def _to_fallback():
            """نوار را به دکمه‌ی «گالریِ سیستم» تبدیل می‌کند."""
            fb = _gallery_fallback()
            strip.bgcolor = op(0.05, "#ffffff")
            strip.content, strip.on_click = fb.content, fb.on_click
            strip.ink = True
            head_txt.value = t("att_gallery")
            _safe_update(strip, head_txt)

        async def _build_strip():
            """مجوز + گشتنِ پوشه‌ها + ساختنِ کاشی‌ها — همه بیرونِ مسیرِ باز شدن."""
            # اجرای قبلی همین‌جا مرد؟ پس این‌بار کاشی نمی‌سازیم و مستقیم
            # گالریِ سیستم را پیشنهاد می‌دهیم. کرش خودش را درمان می‌کند.
            if _CRASHED.get("thumbs"):
                _to_fallback()
                return
            # فقط *می‌پرسیم*، نمی‌خواهیم. پنجره‌ی مجوزِ سیستم روی برگه‌ی
            # نیمه‌بازِ Bottom-Sheet همان چیزی بود که برنامه را قفل می‌کرد؛ و
            # لازم هم نیست: انتخابگرِ سیستم بی‌مجوز کار می‌کند. مجوز را
            # `_attach_pick` سرِ ضربه‌ی خودِ کاربر می‌گیرد، نه این‌جا.
            if await _perm_status("PHOTOS") not in ("GRANTED", ""):
                _to_fallback()
                return
            pics = await asyncio.to_thread(_recent_pics, ATT_THUMBS)
            if not pics:
                _to_fallback()
                return
            _mark_set("thumbs", str(len(pics)))
            strip.bgcolor = None
            strip.content = ft.Row(
                [ui.media_tile(
                    p, size=96, radius=14,
                    on_click=(lambda e, q=p: _att_go(
                        lambda: _attach_pic(q))))
                 for p in pics],
                spacing=6, scroll=ft.ScrollMode.AUTO, wrap=False)
            head_txt.value = t("att_recent")
            _safe_update(strip, head_txt)
            # یک نفس بعد از رسمِ واقعی، ردِ پا را برمی‌داریم: اگر رمزگشا
            # می‌خواست پروسه را بکشد، تا الان کشته بود.
            await asyncio.sleep(1.2)
            _mark_clear("thumbs")

        async def _fill_strip():
            """پوششِ بیرونیِ `_build_strip` با سقفِ زمان.

            هیچ مسیری نباید نوار را برای همیشه «جای‌نگهدار» بگذارد: کاربر
            آن حالت را «فریز» می‌بیند. رد شد یا شکست ⇒ گالریِ سیستم.
            """
            try:
                await asyncio.wait_for(_build_strip(), timeout=ATT_FILL_T)
            except (asyncio.TimeoutError, TimeoutError):
                _mark_clear("thumbs")
                log.info("نوارِ عکس‌ها دیر کرد (>%ss)", ATT_FILL_T)
                try:
                    _to_fallback()
                except Exception:
                    pass
            except Exception as ex:
                _mark_clear("thumbs")
                log.info("نوارِ عکس‌ها ساخته نشد: %s", ex)
                try:
                    _to_fallback()
                except Exception:
                    pass

        row = ft.Row([
            _att_tile("image", t("att_gallery"),
                      lambda: _attach_pick(ft.FilePickerFileType.IMAGE,
                                           t("att_pick_photo"),
                                           ("PHOTOS", "VIDEOS")),
                      "#6f7bff", "#4a56e0"),
            _att_tile("file", t("att_file"),
                      lambda: _attach_pick(ft.FilePickerFileType.ANY,
                                           t("att_file")),
                      "#4fb8ff", "#2b8ad6"),
            _att_tile("music", t("att_music"),
                      lambda: _attach_pick(ft.FilePickerFileType.AUDIO,
                                           t("att_music"), ("AUDIO",)),
                      "#ff9a55", "#e56f2a"),
            _att_tile("pin", t("att_location"), _attach_location,
                      "#ff6b8b", "#d94360"),
        ], spacing=2, scroll=ft.ScrollMode.AUTO,
            alignment=ft.MainAxisAlignment.SPACE_AROUND)

        sheet = ft.BottomSheet(
            ft.Container(
                ft.Column([
                    head_txt,
                    ui.spacer(h=8),
                    ft.Container(strip, height=100),
                    ui.spacer(h=6),
                    ft.Divider(height=1, color=op(0.08, "#ffffff")),
                    ui.spacer(h=6),
                    row,
                ], spacing=0, tight=True),
                # برگه بیرونِ درختِ اصلی می‌نشیند و `page.rtl` را به ارث
                # نمی‌برد؛ بی این، «گالری» می‌افتد چپ و «موقعیت» راست — یعنی
                # وارونه‌ی چیدمانِ فارسیِ بقیه‌ی برنامه.
                rtl=page.rtl, padding=PAD(14, 6, 14, 10)),
            bgcolor=P["bg1"], show_drag_handle=True, use_safe_area=True,
            draggable=True, scrollable=False,
            # برگه که بسته شد نگهبان را باز می‌کنیم، وگرنه سنجاق برای همیشه
            # بی‌جواب می‌ماند — که دقیقاً «فریز» به‌نظر می‌رسد.
            on_dismiss=lambda e: _att.update(open=False),
            shape=ft.RoundedRectangleBorder(radius=BR(22, 22, 0, 0)))
        try:
            page.show_dialog(sheet)
        except Exception:
            # اگر برگه به هر دلیلی باز نشد، همان مسیرِ قدیمی: انتخابگرِ فایل
            _att["open"] = False
            await _attach_pick(ft.FilePickerFileType.ANY, t("attach"))
            return
        page.run_task(_fill_strip)

    # ── گیف‌ها ──────────────────────────────────────
    async def _gif_add(url):
        """گیف را روی حساب ذخیره می‌کند تا در «گیف‌های من» بیاید."""
        if not url:
            return
        try:
            res = await api.add_gif(url)
        except Exception as ex:
            _toast(page, _err_text(ex))
            return
        st["gifs"] = res["urls"]
        _toast(page, t("gif_added") if res["added"] else t("gif_exists"),
               ok=True)

    async def _gif_del(url, refresh):
        try:
            st["gifs"] = await api.remove_gif(url)
        except Exception as ex:
            _toast(page, _err_text(ex))
            return
        _toast(page, t("gif_removed"), ok=True)
        refresh()

    async def _gif_send(url):
        page.pop_dialog()
        await _send_media(url, "gif")

    async def _gif_upload(refresh):
        name, data = await _pick(page, ["gif"], t("upload_gif"))
        if not data:
            return
        url = await _upload(name, data)
        if not url:
            return
        try:
            st["gifs"] = (await api.add_gif(url))["urls"]
        except Exception as ex:
            _toast(page, _err_text(ex))
            return
        _toast(page, t("gif_added"), ok=True)
        refresh()

    def _gif_panel():
        grid = ft.Row([], spacing=6, wrap=True, run_spacing=6, rtl=True,
                      alignment=ft.MainAxisAlignment.START)
        holder = ft.Container(grid, height=300)
        empty_gif = ft.Container(
            ft.Column([ui.vic("gif", 40, op(0.35)),
                       ft.Text(t("no_gifs"), size=fs(14), color=P["txt"],
                               font_family=ui.FONT_MED),
                       ft.Text(t("no_gifs_sub"), size=fs(11.5), color=P["sub"],
                               text_align=ft.TextAlign.CENTER)],
                      spacing=6, tight=True,
                      horizontal_alignment=ft.CrossAxisAlignment.CENTER),
            alignment=AL.CENTER, height=300, visible=False)
        wrap_g = ft.Column([holder, empty_gif], spacing=0, tight=True,
                           scroll=ft.ScrollMode.AUTO)

        def _paint():
            urls = st.get("gifs") or []
            grid.controls = [
                ui.media_tile(
                    _cached_src(u), size=96, badge="GIF",
                    on_click=lambda e, uu=u: page.run_task(_gif_send, uu),
                    on_long_press=lambda e, uu=u: _menu_dialog(
                        page,
                        _mi(page, "trash", t("remove_from_gifs"),
                            lambda: page.run_task(_gif_del, uu, _repaint),
                            danger=True),
                        width=250))
                for u in urls]
            holder.visible = bool(urls)
            empty_gif.visible = not urls

        def _repaint():
            _paint()
            try:
                page.update()
            except Exception:
                pass

        async def _load():
            try:
                st["gifs"] = await api.list_gifs()
            except Exception as ex:
                _toast(page, _err_text(ex))
            _repaint()
            # پیش‌بافر: بارِ اول از شبکه، از آن پس از دیسک. بعد از نشستنِ
            # فایل‌ها یک‌بار دیگر می‌کشیم تا کنترل‌ها مسیرِ محلی بگیرند.
            urls = st.get("gifs") or []
            if urls:
                await _prefetch_all(urls, limit=4)
                _repaint()

        _paint()
        ui.dialog(
            page, t("my_gifs"), ft.Container(wrap_g, width=ui.body_w(340)),
            actions=[ui.ghost_btn(t("close"),
                                  on_click=lambda e: page.pop_dialog()),
                     ui.primary_btn(t("upload_gif"), width=140, height=42,
                                    on_click=lambda e: page.run_task(
                                        _gif_upload, _repaint))],
            width=390)
        page.run_task(_load)

    # ── تخته‌ی ایموجی: همه‌ی ایموجی‌ها + دسته‌بندی + جست‌وجو ──
    emo_grid = ft.GridView(max_extent=44, child_aspect_ratio=1.0, spacing=2,
                           run_spacing=2, expand=True, padding=PAD(4, 2, 4, 2))
    emo_hint = ft.Text("", size=fs(12), color=P["sub"],
                       text_align=ft.TextAlign.CENTER)
    # جعبه‌ی پیام («ایموجی‌ای پیدا نشد») ارتفاعِ ثابت دارد و وقتی چیزی برای
    # گفتن نیست کلاً پنهان می‌شود. پیش‌تر `expand=True` داشت و در همان ستون
    # کنارِ شبکه‌ی `expand=True` می‌نشست، پس ارتفاع نصف-نصف تقسیم می‌شد و
    # شبکه فقط نصفِ جای خودش را می‌گرفت (همان «تا تهش اسکرول نمی‌شود»).
    emo_hint_box = ft.Container(emo_hint, alignment=AL.CENTER, height=44,
                                visible=False)
    emo_q = ft.TextField(
        hint_text=t("search_emoji"), bgcolor=op(0.06), color=P["txt"],
        border=ft.InputBorder.NONE, filled=True, border_radius=12,
        cursor_color=P["acc"], selection_color=op(0.30, P["acc"]),
        hint_style=ft.TextStyle(color=P["dim"], size=fs(12.5)),
        text_size=fs(13), content_padding=PAD(12, 8, 12, 8), expand=True)
    emo_tabs = ft.Row([], spacing=1, scroll=ft.ScrollMode.AUTO,
                      alignment=ft.MainAxisAlignment.START)

    def _recents():
        raw = prefs.get("emoji_recent") or ""
        return [c for c in raw.split(" ") if c][:32]

    def _bump_recent(ch):
        keep = [c for c in _recents() if c != ch]
        keep.insert(0, ch)
        page.run_task(prefs.put, "emoji_recent", " ".join(keep[:32]))

    def _put_emoji(ch):
        inp.value = (inp.value or "") + ch
        _bump_recent(ch)
        _sync_send()
        # ۱.۶: `page.update()` بود ⇒ هر ضربه‌ی ایموجی کلِ درختِ چت (صدها حباب)
        # را با سرور مقایسه می‌کرد و تخته کند حس می‌شد. همان چند کنترل بس است.
        _repaint(page, inp, send_ic, send_circle, attach_btn, gif_btn)
        _focus(page, inp)                 # focus بعدِ update، وگرنه بی‌اثره

    def _emo_cell(ch):
        return ft.Container(
            ft.Text(ch, size=fs(21), text_align=ft.TextAlign.CENTER),
            alignment=AL.CENTER, border_radius=BR(10, 10, 10, 10), ink=True,
            tooltip=(ED.NAMES.get(ch) or ("", ""))[1 if i18n.rtl() else 0]
            or None,
            on_click=lambda e, c=ch: _put_emoji(c))

    def _emo_search(q):
        q = (q or "").strip().lower()
        out = []
        for ch, (en, fa) in ED.NAMES.items():
            if q in en or (fa and q in fa):
                out.append(ch)
                if len(out) >= 240:
                    break
        return out

    def _emo_paint():
        q = (emo_q.value or "").strip()
        if q:
            chars = _emo_search(q)
        elif st["ecat"] == "recent":
            chars = _recents()
        else:
            chars = ED.EMOJI.get(st["ecat"]) or []
        emo_grid.controls = [_emo_cell(c) for c in chars]
        emo_grid.visible = bool(chars)
        emo_hint.visible = not chars
        emo_hint_box.visible = not chars
        emo_hint.value = (t("no_emoji_found") if q else t("no_recent_emoji"))
        for tab in emo_tabs.controls:
            on = (tab.data == st["ecat"]) and not q
            tab.bgcolor = op(0.12) if on else None
            tab.opacity = 1.0 if on else 0.55

    def _emo_repaint(e=None):
        _emo_paint()
        # ۱.۶: با هر حرفِ جست‌وجوی ایموجی `page.update()` می‌رفت ⇒ کلِ صفحه.
        # تخته یک زیردرختِ بسته است؛ همان را می‌کشیم.
        _repaint(page, emo_panel)

    def _emo_tab(key, glyph):
        c = ft.Container(ft.Text(glyph, size=fs(17)), width=36, height=34,
                         alignment=AL.CENTER, border_radius=BR(9, 9, 9, 9),
                         ink=True, data=key, animate_opacity=FAST,
                         tooltip=t(f"emoji_g_{key}"))

        def _pick(e, k=key):
            st["ecat"] = k
            emo_q.value = ""
            _emo_repaint()
        c.on_click = _pick
        return c

    st["ecat"] = "recent" if _recents() else "smileys"
    emo_tabs.controls = [_emo_tab("recent", "🕒")] + [
        _emo_tab(k, g) for k, g in ED.GROUPS]

    # ── تخته فقط ایموجی است ────────────────────────────
    # پیش‌تر دو تخته بود (ایموجی و «استیکر»، که همان ایموجی‌های بزرگ بود) و دو
    # کلید در نوارِ نوشتن. کاربر گفت یکی بس است و آن بزرگ‌ها را نمی‌خواهد، پس
    # تخته‌ی استیکر و کلیدش برداشته شد — و در ۱.۶ ساختِ آن‌ها هم برداشته شد:
    # ۹۰ خط تابعِ صدانخورده که سرِ *هر* باز شدنِ گفت‌وگو یک شبکه و ده زبانه
    # می‌ساخت و دور می‌ریخت. نمایشِ استیکرهای *رسیده* کارِ `_is_sticker_text`
    # است و به این‌ها کاری نداشت.
    tabs_swap = ft.Container(emo_tabs, expand=True)

    def _board_paint():
        emo_q.hint_text = t("search_emoji")
        _emo_paint()

    def _board_repaint(e=None):
        _board_paint()
        _repaint(page, emo_panel)

    emo_q.on_change = _board_repaint

    emo_panel = ft.Container(
        ft.Column([
            # ذره‌بین بیرونِ فیلد است؛ prefix_icon آن را تا اندازه‌ی خانه‌ی
            # خودش بزرگ می‌کرد و prefix هم تقریباً ناپیدا می‌شد.
            ft.Row([ft.Container(ui.vic("search", 17, P["sub"]), width=26,
                                 height=26, alignment=AL.CENTER),
                    emo_q,
                    ui.icon_btn("close", box=34, size=15, tip=t("close"),
                                on_click=lambda e: _toggle_emoji(False))],
                   spacing=4,
                   vertical_alignment=ft.CrossAxisAlignment.CENTER),
            # زبانه‌های دسته‌ها؛ خودشان افقی اسکرول می‌شوند.
            ft.Container(tabs_swap, padding=PAD(0, 4, 0, 2)),
            # شبکه در یک ظرفِ با ارتفاعِ مشخص و درونِ Column می‌نشیند، نه
            # Stack. پیش‌تر `GridView(expand=True)` داخلِ Stack بود و Stack
            # ارتفاعِ کرانمند به آن نمی‌داد؛ نتیجه این بود که فقط چند ردیفِ
            # اول دیده می‌شد و اسکرول تا آخر نمی‌رفت. در Column، `expand`
            # همان ارتفاعِ ظرف را می‌گیرد و پنجره‌ی اسکرول درست بسته می‌شود.
            ft.Container(
                ft.Column([emo_grid, emo_hint_box], spacing=0, tight=False),
                height=248),
        ], spacing=2, tight=True),
        height=332, visible=False, padding=PAD(6, 6, 6, 0),
        bgcolor=op(0.05), border_radius=BR(14, 14, 14, 14),
        margin=MG(0, 0, 0, 6))

    def _toggle_emoji(on=None):
        st["emoji"] = (not st["emoji"]) if on is None else bool(on)
        emo_panel.visible = st["emoji"]
        try:
            emo_btn.content.src = ("icons/close.svg" if st["emoji"]
                                   else "icons/smile.svg")
        except Exception:
            pass
        if st["emoji"]:
            _board_paint()
        # ۱.۶: باز/بسته‌شدنِ تخته هم زیردرختِ خودش است، نه کلِ صفحه. (تغییرِ
        # `visible` روی خودِ کنترل با `control.update()` می‌رسد — همان کاری که
        # `_on_inp_change` برای گیره و کلیدِ گیف می‌کند.)
        _repaint(page, emo_panel, emo_btn)

    emo_btn = ui.icon_btn("smile", tip=t("emoji"), box=38, size=20,
                          on_click=lambda e: _toggle_emoji())

    up_bar = ft.Container(visible=False)      # بی‌انیمیشن (خواستهٔ کاربر)

    # ── ضبط صدا ─────────────────────────────────────
    rec = {"on": False, "t0": 0.0, "path": None}
    rec_dot = ft.Container(width=10, height=10, bgcolor=P["acc2"],
                           border_radius=BR(5, 5, 5, 5), opacity=1.0,
                           animate_opacity=SLOW)
    rec_time = ft.Text("0:00", size=fs(13), color=P["txt"],
                       font_family=ui.FONT_MED)
    rec_hint = ft.Text(t("tap_to_stop"), size=fs(11), color=P["sub"])

    async def _tick():
        """نقطه‌ی چشمک‌زن و ساعتِ ضبط — سه‌بار در ثانیه.

        اینجا `page.update()` نه: کلِ درختِ صفحه‌ی چت (که می‌تواند صدها حباب
        داشته باشد) سه‌بار در ثانیه با سرور مقایسه می‌شد و همان لگیِ سرِ ضبط
        از همین بود. تنها دو کنترل عوض می‌شود، پس تنها همان دو تازه می‌شود.
        بیرون‌رفتن از حلقه هم دستِ `_alive(gen)` است، نه دستِ استثنا.
        """
        while rec["on"] and _alive(gen):
            rec_dot.opacity = 0.15 if rec_dot.opacity > 0.5 else 1.0
            rec_time.value = _mmss(time.monotonic() - rec["t0"])
            _repaint(page, rec_dot, rec_time)
            await asyncio.sleep(0.35)

    async def _start_rec():
        """ضبط را می‌آغازد — و اگر نشد، *برنامه را نمی‌بندد*.

        سه نگهبان که پیش‌تر نبودند:
          • مجوزِ RECORD_AUDIO را پیش از هر تماسِ نیتیو می‌گیریم. بی آن
            MediaRecorder در جاوا SecurityException می‌دهد و پروسه می‌میرد —
            استثنا هرگز به پایتون نمی‌رسد که بشود گرفتش.
          • روی *نقشه‌ی* رمزگذارها حلقه می‌زنیم و تنظیماتِ قانونیِ هر کدام را
            صریح پاس می‌دهیم؛ اولی نشد، بعدی. پیش‌تر یک تلاش بود و تمام.
          • هر تماس در try است، پس هیچ مسیری به استثنایِ نگرفته در حلقه‌ی
            رویدادِ Flet نمی‌رسد.
        """
        if _recorder is None:
            _toast(page, t("voice_off"))
            return
        if rec.get("busy"):
            return                      # ضربه‌ی دوم روی میکروفون را می‌خوریم
        rec["busy"] = True
        try:
            # ۱) مجوزِ سیستمیِ اندروید، پیش از هر تماسِ نیتیو. روی دسکتاپ و
            #    وب بی‌اثر است و فوری True می‌دهد.
            #    `strict=True`: اگر کاربر رد کرد باید *همین‌جا* بایستیم. رفتنِ
            #    سرِ `start_recording` بی RECORD_AUDIO یعنی SecurityException
            #    در جاوا و بسته‌شدنِ برنامه — همان کرشی که کاربر می‌گوید.
            try:
                if not await _ask_perm(page, "MICROPHONE", strict=True):
                    _toast(page, t("mic_denied"))
                    return
            except Exception:
                pass
            # ۲) نقشه‌ی رمزگذارِ جورِ دستگاه (در شروعِ برنامه بی‌انتظار چیده
            #    شده؛ این‌جا فقط اگر هنوز نرسیده بوده باشد منتظر می‌ماند).
            try:
                await _pick_encoder()
            except Exception:
                pass
            # ۳) اجازه‌ی خودِ ضبط‌کننده: در مرورگر پنجره‌ی میکروفونِ مرورگر را
            #    می‌آورد، روی اندروید لایه‌ی دومِ همان مجوز است.
            try:
                allowed = await _nat(_recorder.has_permission(), 8.0,
                                     "has_permission")
            except Exception:
                _toast(page, t("mic_missing"))
                return
            if not allowed:
                _toast(page, t("mic_denied"))
                return
            # ۴) ضبطِ نیمه‌بازِ قبلی را ببند؛ دو ضبطِ همزمان روی اندروید
            #    خطای نیتیوِ کشنده می‌دهد. یک نفس هم صبر، تا میکروفون
            #    واقعاً آزاد شود (رهاشدنِ نیتیو همزمان نیست).
            try:
                if await _nat(_recorder.is_recording(), 5.0, "is_recording"):
                    await _nat(_recorder.cancel_recording(), 6.0, "cancel")
                    await asyncio.sleep(0.15)
            except Exception:
                pass
            # ۵) یکی‌یکی از نقشه. مرورگر مسیرِ فایل ندارد: باید
            #    `output_path=None` بدهیم و خروجی را از همان Blob URLی
            #    بگیریم که `stop_recording` برمی‌گرداند.
            plan = list(_enc.get("plan") or [])
            if not plan:                       # نقشه خالی؟ یک پیش‌فرضِ امن
                got = _rec_config("OPUS" if _web["on"] else "AACLC")
                plan = [("", got[0], got[1])] if got else []
            if not plan:
                _toast(page, t("voice_off"))
                return
            path, started, used = None, None, ""
            why = ""                           # علتِ آخرین شکست، برای پیام
            for _nm, cfg, ext in plan:
                path = None if _web["on"] else os.path.join(
                    _scratch_dir(),
                    f"rafi_voice_{int(time.time() * 1000)}.{ext}")
                # فایل را *از قبل* خالی می‌سازیم. بی این خط، سمتِ دارت مسیرِ
                # ناموجود را «دارایی» می‌فهمد و مچاله‌اش می‌کند ⇒ روی اندروید
                # هیچ رمزگذاری آغاز نمی‌شد و همیشه «صدا ضبط نشد» می‌گرفتیم.
                # → توضیحِ کاملِ زنجیره در `_touch_out`.
                if path and not _touch_out(path):
                    why = "no_file"            # پوشه نوشتنی نیست
                    continue
                # ⚠️ خطرناک‌ترین خطِ برنامه. `MediaRecorder` اگر رمزگذار را
                #    نپسندد در لایه‌ی جاوا می‌میرد و *استثنایی به پایتون
                #    نمی‌رسد* — نه try کمک می‌کند نه حلقه. پس نامِ رمزگذار را
                #    پیش از تماس روی دیسک می‌نویسیم: اگر برنامه بست، اجرای
                #    بعدی این ردِ پا را می‌بیند و همین رمزگذار را برای همیشه
                #    کنار می‌گذارد (`_marks_boot` → `_rec_ban`). یعنی کرش
                #    نهایتاً یک‌بار رخ می‌دهد و خودش را درمان می‌کند.
                _mark_set("rec", _nm or "?")
                try:
                    started = await _nat(_recorder.start_recording(
                        output_path=path, configuration=cfg), 12.0,
                        f"start[{_nm or '?'}]")
                except Exception as ex:
                    started = None             # این رمزگذار نشد ⇒ بعدی
                    why = _err_text(ex)
                    _mark_clear("rec")
                    _rm(path)
                    continue
                if started:
                    rec["ext"], used = ext, (_nm or "")
                    break
                _mark_clear("rec")             # رد کرد ولی نکشت
                why = why or "refused"
            if not started:
                # پیامِ یک‌کلمه‌ایِ قبلی («صدا ضبط نشد») سه شکستِ کاملاً
                # متفاوت را یکی نشان می‌داد و عیب‌یابی روی گوشی ناممکن بود.
                # حالا علت هم می‌آید.
                _toast(page, f"{t('rec_no_start')}{(' — ' + why) if why else ''}")
                return
            # زنده ماندیم ⇒ ردِ پا را برمی‌داریم و این رمزگذار را «خوب» ثبت
            # می‌کنیم تا اجرای بعدی بی هیچ سنجشی همین را اول بگیرد.
            _mark_clear("rec")
            if used:
                _rec_good_set(used)
            rec.update({"on": True, "t0": time.monotonic(), "path": path})
            rec_time.value = "0:00"
            bar_swap.content = rec_row
            # فقط همان نوارِ پایین را دوباره می‌کشیم، نه کلِ صفحه. `page.update()`
            # روی گوشی همه‌ی حباب‌های گفتگو را از نو می‌فرستد و ضربه‌ی میکروفون
            # را کُند نشان می‌دهد. → `_repaint`
            _repaint(page, bar_swap)
            page.run_task(_tick)
        finally:
            rec["busy"] = False

    async def _stop_rec(send_it=True):
        # `_start_rec` هنوز در راه است؟ ایستاندنِ ضبطی که آغاز نشده، روی
        # اندروید استثنایِ نیتیو می‌دهد (پایتون نمی‌گیردش ⇒ برنامه می‌بندد).
        # یک نفس صبر می‌کنیم تا آغاز تکلیفش روشن شود.
        for _ in range(20):
            if not rec.get("busy"):
                break
            await asyncio.sleep(0.05)
        if not rec["on"]:
            return
        rec["on"] = False
        bar_swap.content = input_row
        _repaint(page, bar_swap)               # همان یک نوار، نه کلِ صفحه
        path = rec["path"]
        if _recorder is None:
            _rm(path)
            return
        try:
            if send_it:
                out = await _nat(_recorder.stop_recording(), 20.0, "stop")
            else:
                await _nat(_recorder.cancel_recording(), 8.0, "cancel")
                out = None
        except Exception as ex:
            # ایستادن شکست خورد ⇒ میکروفون ممکن است هنوز در دستِ لایه‌ی
            # نیتیو باشد. بی این لغوِ اجباری، ضبطِ *بعدی* روی همان میکروفونِ
            # گرفته می‌نشیند و آن‌جاست که پروسه می‌میرد.
            try:
                await _nat(_recorder.cancel_recording(), 8.0, "cancel!")
            except Exception:
                pass
            _toast(page, f"{t('rec_stop_failed')} — {_err_text(ex)}")
            _rm(path)
            return
        if not send_it:
            _toast(page, t("rec_cancelled"))
            _rm(path)
            return
        path = out or path
        # مرورگر: خروجی مسیرِ دیسک نیست، یک `blob:`/`http:` است — با fetch
        # خوانده می‌شود. برنامه‌ی نصب‌شده: همان فایلِ روی دیسک.
        is_url = isinstance(path, str) and "://" in path
        data = None
        for _ in range(12):        # فایل ممکنه چند صدم ثانیه دیرتر بسته بشه
            data = (await _read_url_bytes(path) if is_url
                    else await _read_bytes(path))
            if data and len(data) > 1200:
                break
            await asyncio.sleep(0.12)

        if not data or len(data) <= 1200:
            _rm(path)
            # فایل ساخته شد ولی خالی/نیم‌بند است — یعنی ضبط آغاز شد و چیزی
            # نگرفت (میکروفونِ در دستِ برنامه‌ی دیگر، یا ضبطِ خیلی کوتاه).
            _toast(page, t("rec_empty") if not is_url else t("voice_web_off"))
            return
        name = (f"rafi_voice_{int(time.time() * 1000)}"
                f".{_audio_ext(data, rec.get('ext') or _rec_ext)}"
                if is_url or not path else os.path.basename(path))
        _toast(page, t("uploading"))
        try:
            res = await api.upload_media_bytes(name, data)
        except Exception as ex:
            _rm(path)
            _toast(page, _err_text(ex))
            return
        _rm(path)
        await _send_media(res["url"], "voice")

    def _mic_click(e=None):
        page.run_task(_stop_rec, True) if rec["on"] else page.run_task(_start_rec)

    # برنامه رفت پس‌زمینه وسطِ ضبط؟ اندروید میکروفون را از پروسه‌ی
    # پس‌زمینه می‌گیرد و ضبط‌کننده‌ی رهانشده سرِ برگشت پروسه را می‌کشد. پس
    # همان‌جا لغوش می‌کنیم. `_rec_abort` را چرخه‌ی عمرِ برنامه صدا می‌زند.
    def _on_bg():
        if rec.get("on"):
            page.run_task(_stop_rec, False)
    _rec_abort["fn"] = _on_bg

    # ── باکسِ نوشتن (شکلِ عکسِ مرجع) ──────────────────
    # یک قرصِ شیشه‌ای شامل ایموجی/گیف/متن/گیره + یک دایره‌ی لهجه‌ایِ جدا
    # کنارش که تا وقتی متنی نیست میکروفون است و با نوشتن، «فرستادن».
    SEND_IC = "send_rtl" if i18n.rtl() else "send"
    send_ic = ui.vic("mic", 20, "#ffffff")
    send_circle = ft.Container(
        send_ic, width=46, height=46, alignment=AL.CENTER,
        border_radius=BR(23, 23, 23, 23),
        gradient=ft.LinearGradient(begin=AL.CENTER_LEFT, end=AL.CENTER_RIGHT,
                                   colors=[P["acc2"], P["acc"]]),
        shadow=ft.BoxShadow(blur_radius=16, spread_radius=-4,
                            color=op(0.55, P["acc"]), offset=OFF(0, 5)),
        ink=True, tooltip=t("voice"),
        on_click=lambda e: (page.run_task(_send_text)
                            if (inp.value or "").strip() else _mic_click()))
    attach_btn = ui.icon_btn("attach", tip=t("attach"), box=38, size=20,
                             on_click=lambda e: page.run_task(_attach))
    gif_btn = ui.icon_btn("gif", tip=t("gifs"), box=38, size=20,
                          on_click=lambda e: _gif_panel())

    def _sync_send():
        """میکروفون ↔ فرستادن، و پنهان‌شدنِ گیره وقتی متن هست (مثلِ تلگرام)."""
        has = bool((inp.value or "").strip())
        want = SEND_IC if has else "mic"
        changed = False
        if getattr(send_ic, "src", "") != f"icons/{want}.svg":
            send_ic.src = f"icons/{want}.svg"
            send_circle.tooltip = t("send") if has else t("voice")
            changed = True
        if attach_btn.visible == has:
            attach_btn.visible = not has
            gif_btn.visible = not has
            changed = True
        return changed

    _sync_send()

    input_row = ft.Row(
        [ui.glass_pill(
            ft.Row([emo_btn, gif_btn, inp, attach_btn], spacing=0,
                   vertical_alignment=ft.CrossAxisAlignment.CENTER),
            radius=25, pad=PAD(4, 1, 4, 1), expand=True),
         send_circle],
        spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER)

    # نوارِ «فقط مدیرها می‌تونن بنویسن» برای کانالِ خواندنی
    locked_row = ft.Container(
        ft.Row([ui.vic("lock", 16, P["sub"]),
                ft.Text(t("channel_readonly") if kind == "channel"
                        else t("only_admins_post"),
                        size=fs(12.5), color=P["sub"])],
               spacing=8, alignment=ft.MainAxisAlignment.CENTER),
        padding=PAD(12, 12, 12, 12), border_radius=BR(14, 14, 14, 14),
        bgcolor=op(0.05))

    rec_row = ft.Row(
        [ui.icon_btn("trash", tip=t("cancel"), color=P["acc2"],
                     on_click=lambda e: page.run_task(_stop_rec, False)),
         rec_dot,
         ft.Column([ft.Row([ft.Text(t("recording"), size=fs(13),
                                    color=P["txt"], font_family=ui.FONT_MED),
                            rec_time], spacing=8),
                    rec_hint], spacing=0, tight=True, expand=True),
         ft.Container(ui.vic("stop", 20, "#ffffff"), width=42, height=42,
                      alignment=AL.CENTER, border_radius=BR(21, 21, 21, 21),
                      bgcolor=P["acc"], ink=True, tooltip=t("send"),
                      on_click=lambda e: page.run_task(_stop_rec, True))],
        spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER)

    # باکسِ نوشتن بی‌انیمیشن است (کاربر خواسته): جای AnimatedSwitcher یک
    # ظرفِ ساده که محتوایش بی‌جلوه عوض می‌شه.
    bar_swap = ft.Container(input_row if can_post else locked_row)
    inp.on_submit = lambda e: page.run_task(_send_text)

    # ── «داره می‌نویسه…» ─────────────────────────────
    async def _typing_ping():
        if is_channel:        # در کانال «داره می‌نویسه…» معنی نداره
            return
        now = time.monotonic()
        if now - st.get("t_sent", 0.0) < 2.5:
            return
        st["t_sent"] = now
        await ws_client.send_typing(chat_id)

    async def _refocus_soon():
        """اگر تازه‌کردنِ دکمه‌ها تمرکزِ کیبورد را دزدید، برش می‌گردانیم.

        بی‌قید `focus()` صدا نمی‌زنیم: روی بعضی پلتفرم‌ها نشانگر را به اولِ
        متن می‌برد و تایپ به‌هم می‌ریزد. پس یک لحظه صبر می‌کنیم تا خبرِ
        «بی‌تمرکز شد» از سمتِ فلاتر برسد و تنها آن وقت برمی‌گردانیم. اگر
        پلتفرمی این خبر را نمی‌دهد (`blur_seen` هیچ‌وقت روشن نشد)، مثلِ قبل
        محتاطانه تمرکز را برمی‌گردانیم.
        """
        await asyncio.sleep(0.18)
        if not _alive(gen):
            return
        if not st.get("inp_focus") or not st.get("blur_seen"):
            _focus(page, inp)

    def _on_inp_change(e=None):
        st["inp_focus"] = True            # تایپ یعنی تمرکز همین‌جاست
        if _sync_send():
            # `page.update()` کپسولِ نوشتن را از نو می‌سازد و تمرکزِ کیبورد
            # می‌پرد (با حرفِ اول تایپ قطع می‌شد)؛ فقط دکمه‌ها تازه می‌شن.
            for c in (send_ic, send_circle, attach_btn, gif_btn):
                try:
                    c.update()
                except Exception:
                    pass
            page.run_task(_refocus_soon)
        if (inp.value or "").strip():
            page.run_task(_typing_ping)

    def _inp_focus(e=None):
        st["inp_focus"] = True

    def _inp_blur(e=None):
        st["inp_focus"] = False
        st["blur_seen"] = True

    inp.on_focus = _inp_focus
    inp.on_blur = _inp_blur
    inp.on_change = _on_inp_change

    async def _typing_sweeper():
        """اسم‌های قدیمی از فهرستِ «می‌نویسه» پاک می‌شن."""
        while _alive(gen):
            await asyncio.sleep(1.6)
            if not _alive(gen):
                return
            now = time.monotonic()
            gone = [k for k, v in st["typers"].items() if now - v > 4.0]
            for k in gone:
                st["typers"].pop(k, None)
            if gone:
                # فقط زیرنویسِ سرصفحه عوض می‌شود؛ کلِ صفحه را تازه نکن.
                _sync_sub()
                _repaint(page, sub, dots)

    # ── نوارِ پیامِ سنجاق‌شده ─────────────────────────
    pin_txt = ft.Text("", size=fs(12), color=P["txt"], max_lines=1,
                      overflow=ft.TextOverflow.ELLIPSIS, expand=True)
    # «برداشتنِ سنجاق» فقط دستِ مدیرهاست (در کانال مشترکِ ساده نبایدش ببینه)
    pin_bits = [ft.Container(width=2, height=28, bgcolor=P["acc"],
                             border_radius=BR(2, 2, 2, 2)),
                ft.Column([ft.Text(t("pinned_message"), size=fs(10),
                                   color=P["acc"]), pin_txt],
                          spacing=0, tight=True, expand=True)]
    if can_manage or not is_group:
        pin_bits.append(ui.icon_btn("close", box=30, size=14, tip=t("unpin"),
                                    on_click=lambda e: page.run_task(
                                        _do_pin, st["pin"], False)
                                    if st["pin"] else None))
    pin_bar = ft.Container(
        ft.Row(pin_bits, spacing=8,
               vertical_alignment=ft.CrossAxisAlignment.CENTER),
        padding=PAD(10, 4, 4, 4), bgcolor=op(0.07), visible=False,
        margin=MG(10, 2, 10, 0), border_radius=BR(16, 16, 16, 16),
        border=ft.Border(*[ft.BorderSide(1, op(0.07))] * 4),
        animate_opacity=MED, animate_size=MED, ink=True,
        on_click=lambda e: page.run_task(
            _jump_to, (st["pin"] or {}).get("message_id")))

    def _sync_pin():
        p = st["pin"]
        if p:
            pin_txt.value = _snippet(p).replace("\n", " ")[:90]
            if not pin_bar.visible:
                ui.pop_in(pin_bar, page, dy=-0.45, ms=MED)
        else:
            pin_bar.visible = False
        _repaint(page, pin_bar, pin_txt)

    # ── نوارِ بالا ───────────────────────────────────
    sub = ft.Text("", size=fs(11), color=P["sub"])
    dots = ui.typing_dots()
    sub_row = ft.Row([sub, dots], spacing=6, tight=True,
                     vertical_alignment=ft.CrossAxisAlignment.CENTER)

    def _sync_sub():
        if st["typers"]:
            names = list(st["typers"].keys())
            sub.value = (t("typing_one", name=names[0]) if len(names) == 1
                         else t("typing_many", n=i18n.num(len(names))))
            sub.color = P["acc"]
            dots.visible = True
            return
        dots.visible = False
        if _net_down():
            # فقط وقتی راهِ شبکه بسته است: «در حال اتصال…»
            sub.value = t("connecting")
            sub.color = P["acc2"]
        elif is_group:
            sub.value = (t("subscriber_n", n=_kcount(mem_count))
                         if is_channel else t("member_n", n=_kcount(mem_count)))
            sub.color = P["sub"]
        elif other:
            # آنلاین، یا ساعتِ آخرین آنلاین‌بودن
            sub.value = _seen_of(other)
            sub.color = (P["ok"] if _online_of(other)
                         else P["sub"])
        elif is_saved:
            sub.value = t("saved_hint")
            sub.color = P["sub"]
        else:
            sub.value = ""

    _sync_sub()

    def _open_prof(e=None):
        if is_saved:
            return          # دفترچه‌ی یادداشت نمایه‌ای برای دیدن ندارد
        if is_group:
            _profile_dialog(page, title, prof.get("handle"), prof.get("bio"),
                            avatar_url=prof.get("avatar_url"), is_group=True,
                            members=prof.get("members") or [], kind=kind,
                            count=mem_count, hidden=members_hidden)
        elif other:
            page.run_task(_show_user_profile, page, other.get("username"))

    head_av = ui.avatar("saved" if is_saved else title, 42,
                        url=(None if is_saved else _media(av_url)),
                        icon=("bookmark" if is_saved else None),
                        on_click=_open_prof,
                        badge=(P["ok"] if (other and
                                           _online_of(other))
                               else None))

    def head_av_badge(on: bool):
        """نقطه‌ی آنلاینِ آواتارِ سرصفحه — بی‌ساختِ دوباره‌ی کلِ نوار.

        `on_click` روی پوششِ بیرونیِ آواتار است، پس عوض‌کردنِ `.content`
        کلیک را از دست نمی‌دهد.
        """
        try:
            head_av.content = ui.avatar(
                "saved" if is_saved else title, 42,
                url=(None if is_saved else _media(av_url)),
                icon=("bookmark" if is_saved else None),
                badge=(P["ok"] if on else None)).content
            head_av.update()
        except Exception:
            pass
    head_title = ft.Row(
        [ft.Text(title, size=fs(15), weight=W.W_600,
                 font_family=ui.FONT_MED, color=P["txt"],
                 max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)], spacing=5)
    if is_saved:
        head_title.controls.insert(0, ui.vic("bookmark", 14, P["acc"]))
    elif kind == "channel":
        head_title.controls.insert(0, ui.vic("hash", 14, P["sub"]))
    def _story_menu(e=None):
        """استوریِ گروه/کانال — دیدن، و اگر اجازه داری، گذاشتنِ تازه."""
        ring = {"owner_kind": "chat", "owner_id": chat_id, "title": title,
                "avatar_url": prof.get("avatar_url")}
        items = []
        if can_manage or not is_channel:
            items.append(_mi(page, "camera", t("add_story"),
                             lambda: page.run_task(_story_post, page, "chat",
                                                   chat_id, None)))
        items.append(_mi(page, "eye", t("stories"),
                         lambda: _story_viewer(page, ring)))
        _menu_dialog(page, *items)

    # نوارِ بالا مثلِ عکسِ مرجع: دکمه‌ی گردِ برگشت + قرصِ شناورِ نام/وضعیت
    # + دکمه‌های گردِ سمتِ دیگر، همه جدا از هم و شیشه‌ای.
    head_bits = [
        ui.glass_circle("arrow_right" if i18n.rtl() else "arrow_left",
                        box=46, tip=t("back"),
                        on_click=lambda e: page.run_task(chat_list_page, page)),
        ui.glass_pill(
            ft.Row([head_av,
                    ft.Container(ft.Column([head_title, sub_row], spacing=0,
                                           tight=True),
                                 expand=True, padding=PAD(2, 0, 4, 0))],
                   spacing=8,
                   vertical_alignment=ft.CrossAxisAlignment.CENTER),
            radius=25, pad=PAD(4, 3, 10, 3), expand=True,
            on_click=_open_prof, ink=True),
    ]
    head_bits.append(ui.glass_circle(
        "moon" if ui.is_light() else "sun", box=46,
        tip=t("night_mode") if ui.is_light() else t("day_mode"),
        on_click=lambda e: page.run_task(_toggle_day_night, page,
                                         chat_list_page)))
    if is_group:
        head_bits.append(ui.glass_circle("camera", box=46, tip=t("stories"),
                                         on_click=_story_menu))
    if is_group and can_manage:
        head_bits.append(ui.glass_circle(
            "pencil", box=46, tip=t("edit_group"),
            on_click=lambda e: _group_admin_dialog(page, chat_id, prof)))
    head = ft.Container(
        ft.Row(head_bits, spacing=8,
               vertical_alignment=ft.CrossAxisAlignment.CENTER),
        padding=PAD(10, 10, 10, 4))

    async def on_ws(data):
        if not _alive(gen):
            return
        typ = data.get("type")
        if typ == "ws_state":
            if data.get("online"):
                _net_mark(True)
                page.run_task(_presence_once)   # وضعیتِ طرف رو تازه کن
            _sync_sub()
            # ⚠️ ۱.۵: هر رویدادِ سوکت `page.update()` می‌زد ⇒ کلِ درختِ چت
            # (صدها حباب) با هر «داره می‌نویسه…» مقایسه می‌شد. زیرنویس که عوض
            # می‌شود، فقط زیرنویس را بفرست.
            _repaint(page, sub, dots)
            return
        if typ == "presence":
            uid = data.get("user_id")
            if other and other.get("user_id") == uid:
                other["online"] = bool(data.get("online"))
                if data.get("last_seen_at"):
                    other["last_seen_at"] = data["last_seen_at"]
                _sync_sub()
                head_av_badge(bool(data.get("online")))   # خودش تازه می‌کند
                _repaint(page, sub, dots)
            return
        if typ == "resync":
            try:
                st["msgs"] = await api.get_messages(chat_id, limit=80)
            except Exception:
                return
            st["seen"] = {m.get("message_id") for m in st["msgs"]}
            try:
                st["pin"] = await api.get_pinned(chat_id)
            except Exception:
                pass
            _sync_pin()
            _render()
            return
        if typ == "profile_updated":
            uid = data.get("user_id")
            if other and other.get("user_id") == uid:
                other["display_name"] = data.get("display_name") or other.get(
                    "display_name")
                other["username"] = data.get("username") or other.get("username")
                other["avatar_url"] = data.get("avatar_url")
                head_title.controls[-1].value = other["display_name"] or ""
                head_av.content = ui.avatar(
                    other["display_name"] or "?", 42,
                    url=_media(other.get("avatar_url"))).content
            for mem in prof.get("members") or []:
                if mem.get("user_id") == uid:
                    mem["display_name"] = data.get("display_name") or mem.get(
                        "display_name")
                    mem["avatar_url"] = data.get("avatar_url")
            nm = data.get("display_name")
            if nm:
                for m in st["msgs"]:
                    if m.get("sender_id") == uid:
                        m["sender_name"] = nm
                _render()
            _sync_sub()
            _repaint(page, head_title, head_av, sub, dots)
            return
        if typ == "chat_updated" and data.get("chat_id") == chat_id:
            await chat_page(page, chat_id)
            return
        if typ == "chat_removed" and data.get("chat_id") == chat_id:
            _toast(page, t("chat_gone"))
            await chat_list_page(page)
            return
        if data.get("chat_id") != chat_id:
            return
        if typ == "typing":
            if data.get("user_id") != api.user_id and not is_channel:
                st["typers"][data.get("name") or "?"] = time.monotonic()
                _sync_sub()
                _repaint(page, sub, dots)
            return
        if typ == "read":
            # طرف چت را خواند → تیکِ پیام‌های من دوبل می‌شه
            at = data.get("at")
            if at and (not st.get("read_at") or at > st["read_at"]):
                st["read_at"] = at
                _render()
            return
        if typ == "message_edited":
            mid = data.get("message_id")
            for m in st["msgs"]:
                if m.get("message_id") == mid:
                    m["content"] = data.get("content") or ""
                    m["enc"] = bool(data.get("enc"))
                    m["edited"] = True
            if (st["pin"] or {}).get("message_id") == mid:
                st["pin"]["content"] = data.get("content") or ""
                _sync_pin()
            _render()
            return
        if typ == "message_deleted":
            mid = data.get("message_id")
            # تلگرامی: پیام بی‌سروصدا حذف می‌شه (هیچ «پیام حذف شد»ی نمی‌مونه)
            st["msgs"] = [m for m in st["msgs"]
                          if m.get("message_id") != mid]
            for m in st["msgs"]:
                if m.get("reply_to") == mid:
                    m["reply_to"] = None
                    m["reply_to_text"] = None
            if (st["pin"] or {}).get("message_id") == mid:
                st["pin"] = None
                _sync_pin()
            _render()
            return
        if typ == "message_pinned":
            mid = data.get("message_id")
            on = bool(data.get("pinned"))
            found = None
            for m in st["msgs"]:
                if m.get("message_id") == mid:
                    m["pinned"] = on
                    found = m
                elif on:
                    m["pinned"] = False
            st["pin"] = found if on else None
            _sync_pin()
            _render()
            return
        if typ in ("reaction", "message_reaction"):
            mid = data.get("message_id")
            for m in st["msgs"]:
                if m.get("message_id") == mid:
                    m["reactions"] = data.get("reactions") or {}
                    if data.get("user_id") == api.user_id:
                        m["my_reactions"] = data.get("my_reactions") or []
            _render()
            return
        if typ != "message":
            return
        mid = data.get("message_id")
        if not mid or mid in st["seen"]:
            return
        st["typers"].pop(data.get("sender_name") or "", None)
        _sync_sub()
        st["seen"].add(mid)
        _add_msg({"message_id": mid, "sender_id": data.get("sender_id"),
                  "sender_name": data.get("sender_name") or "",
                  "content": data.get("content") or "",
                  "msg_type": data.get("msg_type") or "text",
                  "reply_to": data.get("reply_to"),
                  "reply_to_text": data.get("reply_to_text"),
                  "forward_from": data.get("forward_from"),
                  "enc": bool(data.get("enc")),
                  "caption": data.get("caption"),
                  "size": data.get("size"),
                  "reactions": {}, "my_reactions": [],
                  "created_at": data.get("created_at")})
        if data.get("sender_id") != api.user_id:
            page.run_task(_mark_read, chat_id)

    async def _presence_once():
        """یک‌بار خواندنِ «آنلاین/آخرین بازدید» طرف مقابل."""
        if is_group or not other:
            return
        try:
            p = await _try(api.get_profile(other.get("username")), 6.0)
        except Exception as ex:
            _net_fail(ex)
            _sync_sub()
            _repaint(page, sub, dots)
            return
        _net_mark(True)
        # ⚠️ `online` هم باید کپی شود، نه فقط `last_seen_at`. پیش از این تنها
        # `last_seen_at` تازه می‌شد، و `_online_of()` — که کلیدِ `online` را بر
        # حدسِ زمانی مقدم می‌داند — همان مقدارِ کهنه‌ی لحظه‌ی بازشدنِ صفحه را
        # می‌دید؛ نتیجه این بود که زیرعنوان هرگز «آنلاین» نمی‌شد و همیشه ساعت
        # نشان می‌داد. سرورِ بی‌این‌کلید ⇒ کلید را برمی‌داریم تا حدسِ زمانی
        # (`_is_online`) کار کند.
        if "online" in p:
            other["online"] = bool(p.get("online"))
        else:
            other.pop("online", None)
        other["last_seen_at"] = p.get("last_seen_at")
        _sync_sub()
        try:
            head_av_badge(_online_of(other))
        except Exception:
            pass
        _repaint(page, sub, dots)

    async def _presence():
        """آخرین بازدیدِ طرف مقابل هر ۲۰ ثانیه تازه می‌شه."""
        while _alive(gen):
            await asyncio.sleep(20)
            if not _alive(gen) or is_group or not other:
                return
            await _presence_once()

    async def _poller():
        """پشتوانه‌ی پیامِ تازه، برای وقتی سوکت وصل نیست.

        راهِ اصلی سوکت است؛ ولی اگر بسته باشد (شبکه‌ی سخت‌گیر، پروکسیِ میان‌راه،
        یا مسیری که ارتقای WebSocket را رد می‌کند) کاربر پیامِ تازه را تا
        بیرون‌رفتن و برگشتن نمی‌دید. این حلقه فقط در همان حالت می‌پرسد و با
        وصل‌شدنِ سوکت خودش کنار می‌کشد.
        """
        while _alive(gen):
            await asyncio.sleep(5)
            if not _alive(gen) or ws_client.online:
                continue
            try:
                fresh = await _try(api.get_messages(chat_id, limit=40), 8.0)
            except Exception as ex:
                _net_fail(ex)
                continue
            _net_mark(True)
            new = [m for m in fresh
                   if m.get("message_id") and m.get("message_id") not in st["seen"]]
            if not new:
                continue
            for m in new:
                st["seen"].add(m.get("message_id"))
                _add_msg(m)
            page.run_task(store.save_msgs, chat_id, st["msgs"])
            page.run_task(_mark_read, chat_id)
            _repaint(page, msg_list)        # فقط فهرستِ پیام‌ها عوض شده

    async def _sync():
        """گفت‌وگو از انبار کشیده شد؛ این‌جا با سرور هم‌گام می‌شویم.

        دو پرسش، هر دو پشتِ صحنه: پروفایل (سرصفحه و نقشِ من) و ۸۰ پیامِ آخر.
        • پیام‌ها که عوض شده باشند، لیست از نو کشیده می‌شود و اگر کاربر تهِ
          گفت‌وگو بود همان‌جا نگه داشته می‌شود.
        • سرصفحه که عوض شده باشد، صفحه بازسازی می‌شود — ولی *فقط* اگر جعبه‌ی
          نوشتن خالی باشد؛ وگرنه نوشته‌ی نیمه‌کاره‌ی کاربر پاک می‌شد و این
          خودش یک باگ است. در آن حالت تنها `st["prof"]` تازه می‌شود تا
          منوها و دسترسی‌ها درست کار کنند.
        """
        head0 = _prof_key(st["prof"])
        try:
            got = await _try(api.chat_profile(chat_id), 10.0)
        except Exception as ex:
            _net_fail(ex)
            if _net_down():
                _toast(page, t("offline_hint"))
            return
        if not _alive(gen):
            return
        _net_mark(True)
        if isinstance(got, dict) and got:
            st["prof"] = got
            st["read_at"] = got.get("peer_read_at") or st["read_at"]
            page.run_task(store.save_prof, chat_id, got)
            if (_prof_key(got) != head0
                    and not (inp.value or "").strip()
                    and not rec["on"]):
                await chat_page(page, chat_id)
                return
        try:
            fresh = await _try(api.get_messages(chat_id, limit=80), 10.0)
        except Exception as ex:
            _net_fail(ex)
            return
        if not (_alive(gen) and isinstance(fresh, list) and fresh):
            return
        page.run_task(store.save_msgs, chat_id, fresh)
        if _msgs_key(fresh) == _msgs_key(st["msgs"]):
            return
        st["msgs"] = fresh
        st["seen"] = {m.get("message_id") for m in fresh}
        at_end = st["bottom"]
        _render()
        if at_end:
            page.run_task(_scroll_end, False)
        page.run_task(_mark_read, chat_id)

    async def _pin_load():
        """سنجاق، در پس‌زمینه.

        این یک نوارِ باریکِ بالای گفت‌وگوست و ارزشِ سفیدماندنِ کلِ صفحه به
        اندازه‌ی مهلتِ یک درخواستِ آویزان را ندارد؛ پس از راهِ رسمیِ بازشدن
        برداشته شد و اینجا، بعدِ رسمِ صفحه، پر می‌شود.
        """
        if not (from_net or warm):
            return
        try:
            st["pin"] = await _try(api.get_pinned(chat_id), 6.0)
        except Exception:
            return
        if not _alive(gen):
            return
        _sync_pin()
        try:
            page.update()
        except Exception:
            pass

    # فلشِ «برو پایین» شناور روی لیست (سمتِ درستِ زبان)
    down_wrap = ft.Container(down_btn, bottom=6,
                             **({"left": 8} if i18n.rtl() else {"right": 8}))
    body = ft.Column(
        [head, pin_bar,
         ft.Container(ft.Stack([msg_list, empty, down_wrap], expand=True),
                      expand=True),
         ft.Container(ft.Column([up_bar, reply_bar, emo_panel, bar_swap],
                                spacing=0, tight=True),
                      padding=PAD(8, 6, 8, 8))],
        spacing=0, expand=True)
    ui.prep_in(head, dy=-0.25, ms=MED)
    # گفت‌وگو از لبه‌ی جلوی صفحه سُر می‌خوره تو؛ نوارِ پایین هم اینجا نیست
    _go(page, body, pattern=True, dx=(-0.12 if i18n.rtl() else 0.12))
    _render(intro=True)
    _sync_pin()
    _attach_ws(on_ws)
    page.run_task(ui.enter, page, head, stagger=0.0)
    page.run_task(_scroll_end, False)      # auto_scroll خاموشه؛ دستی می‌چسبیم
    page.run_task(_presence)
    page.run_task(_typing_sweeper)
    page.run_task(_poller)
    page.run_task(_pin_load)
    # برگشت از پس‌زمینه ⇒ همان هم‌گامیِ پشتِ صحنه: پیام‌هایی که وقتِ خواب
    # آمده‌اند از سوکت نرسیده‌اند و بی این، گفت‌وگو کهنه می‌ماند.
    notify.on_resume(_sync)
    if warm:
        page.run_task(_sync)   # صفحه از انبار آمد ⇒ هم‌گامی پشتِ صحنه

# ── مدیریتِ گروه (فقط سازنده) ─────────────────────
def _group_admin_dialog(page, chat_id, prof):
    title_f = ui.field(t("group_title"), value=prof.get("title") or "",
                       prefix=ui.vic("users", 18))
    bio_f = ui.field(t("bio"), value=prof.get("bio") or "",
                     prefix=ui.vic("info", 18), multiline=True)
    add_f = ui.field(t("add_member"), prefix=ui.vic("plus", 18))
    handle_f = ui.field(t("handle"), value=prof.get("handle") or "",
                        prefix=ft.Text("@", size=fs(14), color=P["sub"]))
    msg = ft.Text("", size=fs(12), color=P["acc2"])
    my_role = prof.get("my_role") or ("owner" if prof.get("is_admin")
                                      else "member")
    is_owner = my_role == "owner"
    vis = {"public": bool(prof.get("is_public"))}

    def _set_public(v):
        vis["public"] = bool(v)

    pub_row = ui.toggle_row("globe", t("public_chat"), vis["public"],
                            on_change=lambda e: _set_public(e.control.value),
                            subtitle=t("public_sub"))

    async def _save(e=None):
        handle = (handle_f.value or "").strip().lstrip("@").lower()
        if handle and not re.fullmatch(r"[a-z0-9_]{3,32}", handle):
            msg.value = t("bad_handle")
            page.update()
            return
        if vis["public"] and not handle:
            msg.value = t("handle_needed")
            page.update()
            return
        try:
            await api.update_chat_profile(chat_id,
                                          title=(title_f.value or "").strip(),
                                          bio=(bio_f.value or "").strip(),
                                          is_public=vis["public"],
                                          handle=handle)
        except Exception as ex:
            msg.value = (t("handle_taken") if "409" in str(ex)
                         else _err_text(ex))
            page.update()
            return
        page.pop_dialog()
        _toast(page, t("saved"), ok=True)
        await chat_page(page, chat_id)

    async def _photo(e=None):
        name, data = await _pick(page, IMG_EXT, t("group_photo"))
        if not data:
            return
        got = await _edit_photo(page, name, data, square=True)
        if got is None:
            return
        name, data = got
        try:
            up = await api.upload_media_bytes(name, data)
            await api.update_chat_profile(chat_id, avatar_url=up["url"])
        except Exception as ex:
            msg.value = _err_text(ex)
            page.update()
            return
        page.pop_dialog()
        _toast(page, t("saved"), ok=True)
        await chat_page(page, chat_id)

    async def _add(e=None):
        u = (add_f.value or "").strip().lstrip("@").lower()
        if not u:
            return
        try:
            await api.add_member(chat_id, u)
        except Exception as ex:
            msg.value = _err_text(ex)
            page.update()
            return
        page.pop_dialog()
        await chat_page(page, chat_id)

    async def _drop(u):
        try:
            await api.remove_member(chat_id, u)
        except Exception as ex:
            msg.value = _err_text(ex)
            page.update()
            return
        page.pop_dialog()
        _toast(page, t("kicked"), ok=True)
        await chat_page(page, chat_id)

    async def _role(u, role):
        try:
            await api.set_role(chat_id, u, role)
        except Exception as ex:
            msg.value = _err_text(ex)
            page.update()
            return
        page.pop_dialog()
        _toast(page, t("role_changed"), ok=True)
        await chat_page(page, chat_id)

    async def _ban(u, on):
        try:
            await api.set_banned(chat_id, u, on)
        except Exception as ex:
            msg.value = _err_text(ex)
            page.update()
            return
        page.pop_dialog()
        _toast(page, t("banned_done") if on else t("unbanned_done"), ok=True)
        await chat_page(page, chat_id)

    async def _leave(e=None):
        try:
            await api.leave_chat(chat_id)
        except Exception as ex:
            msg.value = (t("owner_cant_leave") if "400" in str(ex)
                         else _err_text(ex))
            page.update()
            return
        page.pop_dialog()
        _toast(page, t("left_group"), ok=True)
        await chat_list_page(page)

    def _member_menu(m):
        un = m.get("username") or ""
        role = m.get("role") or "member"
        banned = bool(m.get("banned"))
        items = [_mi(page, "user", t("view_profile"),
                     lambda: page.run_task(_show_user_profile, page, un))]
        if role != "admin" and role != "owner":
            items.append(_mi(page, "shield", t("make_admin"),
                             lambda: page.run_task(_role, un, "admin")))
        if role == "admin":
            items.append(_mi(page, "user", t("make_member"),
                             lambda: page.run_task(_role, un, "member")))
        if is_owner and role != "owner":
            items.append(_mi(page, "crown", t("transfer_owner"),
                             lambda: _confirm(page, t("transfer_owner"),
                                              t("transfer_owner_confirm",
                                                name=m.get("display_name") or un),
                                              lambda: page.run_task(_role, un,
                                                                    "owner"))))
        if role != "owner":
            items.append(_mi(page, "ban",
                             t("unban") if banned else t("ban"),
                             lambda: page.run_task(_ban, un, not banned),
                             danger=not banned))
            items.append(_mi(page, "trash", t("remove"),
                             lambda: page.run_task(_drop, un), danger=True))
        _menu_dialog(page, *items)

    rows = []
    for m in prof.get("members") or []:
        un = m.get("username") or ""
        role = m.get("role") or "member"
        tag = (ui.vic("crown", 14, P["acc"]) if role == "owner" else
               ui.vic("shield", 14, P["acc"]) if role == "admin" else
               ui.vic("ban", 14, P["acc2"]) if m.get("banned") else None)
        rows.append(ui.row_item(
            None, m.get("display_name") or un, f"@{un}",
            trailing=ft.Row([x for x in (tag, ui.vic("dots", 16, P["sub"]))
                             if x is not None], spacing=6, tight=True),
            on_click=(None if role == "owner" and not is_owner
                      else (lambda e, mm=m: _member_menu(mm)))))

    add_f.on_submit = lambda e: page.run_task(_add)
    ui.dialog(
        page, t("edit_group"),
        ft.Container(
            ft.Column(
                [ft.Row([ui.avatar(prof.get("title") or "", 64,
                                   url=_media(prof.get("avatar_url"))),
                         ft.Container(ui.vic("camera", 16, "#ffffff"), width=30,
                                      height=30, bgcolor=P["acc"],
                                      border_radius=BR(15, 15, 15, 15),
                                      alignment=AL.CENTER, ink=True,
                                      tooltip=t("group_photo"),
                                      on_click=lambda e: page.run_task(_photo))],
                        spacing=6, alignment=ft.MainAxisAlignment.CENTER,
                        vertical_alignment=ft.CrossAxisAlignment.END),
                 ui.spacer(h=12), title_f, ui.spacer(h=10), bio_f,
                 ui.spacer(h=10), handle_f, ui.spacer(h=8), ui.group(pub_row),
                 ui.spacer(h=10), ft.Row([add_f,
                                          ui.icon_btn("plus", color=P["acc"],
                                                      on_click=lambda e: page.run_task(_add))],
                                         spacing=6),
                 ui.section(t("members")), ui.group(*rows) if rows else ui.spacer(),
                 ui.spacer(h=10),
                 ui.group(ui.row_item("logout", t("leave_group"),
                                      icon_color=P["acc2"],
                                      on_click=lambda e: _confirm(
                                          page, t("leave_group"),
                                          t("leave_confirm"),
                                          lambda: page.run_task(_leave),
                                          yes_label=t("leave_group")))),
                 msg],
                spacing=0, tight=True, scroll=ft.ScrollMode.AUTO),
            height=min(560, 400 + 60 * len(rows))),
        actions=[ui.ghost_btn(t("cancel"), on_click=lambda e: page.pop_dialog()),
                 ui.primary_btn(t("save"), width=110, height=40,
                                on_click=lambda e: page.run_task(_save))],
        width=400)

# ═══════════════════════════════════════════════════
#  تنظیمات
# ═══════════════════════════════════════════════════
async def settings_page(page):
    _detach_ws()
    _player.clear()
    gen = _fresh()
    # ⚡ آفلاین‑اول: این صفحه *هیچ* درخواستِ شبکه‌ای را پیش از کشیدنِ خودش
    # منتظر نمی‌ماند. پیش‌تر `await _load_me()` و `await api.get_privacy()`
    # این‌جا بودند و روی نتِ کند صفحه چند ثانیه سفید می‌مانْد (و با هر بار
    # عوض‌کردنِ پوسته هم تکرار می‌شد، چون `_set_theme` باز همین را صدا می‌زند).
    # حالا از حافظه/انبار رنگ می‌شود و شبکه پشتِ صحنه به‌روزش می‌کند.
    await _seed_me()
    priv = await store.load_blob("priv") or {"show_last_seen": True}

    name_f = ui.field(t("display_name"), value=_me.get("display_name") or "",
                      prefix=ui.vic("user", 18))
    user_f = ui.field(t("username"), value=_me.get("username") or "",
                      prefix=ui.vic("dot", 18))
    bio_f = ui.field(t("bio"), value=_me.get("bio") or "",
                     prefix=ui.vic("info", 18), multiline=True)

    async def _save_profile(e=None):
        try:
            await api.update_profile(
                display_name=(name_f.value or "").strip() or None,
                bio=(bio_f.value or ""))
            un = (user_f.value or "").strip().lstrip("@").lower()
            if un and un != (_me.get("username") or ""):
                await api.change_username(un)
        except Exception as ex:
            _toast(page, _err_text(ex))
            return
        # حافظه‌ی محلی را *خودمان* به‌روز می‌کنیم: صفحه دوباره ساخته می‌شود و
        # دیگر منتظرِ `/profile/me` نمی‌ماند، پس اگر این‌جا ننویسیم خانه‌ها
        # مقدارِ پیش از ذخیره را نشان می‌دهند.
        _me["display_name"] = (name_f.value or "").strip() or None
        _me["bio"] = (bio_f.value or "")
        if (user_f.value or "").strip():
            _me["username"] = (user_f.value or "").strip().lstrip("@").lower()
        page.run_task(store.save_blob, "me", dict(_me))
        _toast(page, t("saved"), ok=True)
        await settings_page(page)

    def _pw_dialog(e=None):
        old_f = ui.field(t("old_password"), password=True,
                         prefix=ui.vic("lock", 18))
        new_f = ui.field(t("new_password"), password=True,
                         prefix=ui.vic("key", 18))
        m = ft.Text("", size=fs(12), color=P["acc2"])

        async def _ok(_=None):
            try:
                await api.change_password(old_f.value or "", new_f.value or "")
            except Exception as ex:
                m.value = _err_text(ex)
                page.update()
                return
            page.pop_dialog()
            _toast(page, t("saved"), ok=True)

        ui.dialog(page, t("change_password"),
                  ft.Column([old_f, ui.spacer(h=10), new_f, ui.spacer(h=6), m],
                            spacing=0, tight=True),
                  actions=[ui.ghost_btn(t("cancel"),
                                        on_click=lambda _: page.pop_dialog()),
                           ui.primary_btn(t("save"), width=100, height=40,
                                          on_click=lambda _: page.run_task(_ok))])

    # ── ظاهر ────────────────────────────────────────
    async def _set_theme(name):
        if name == ui.theme_name() and name != ui.CUSTOM:
            return
        _use_theme(name)
        await prefs.put("theme", name)
        if not ui.is_light():
            await prefs.put("dark_theme", name)
        await settings_page(page)

    def _theme_card(name, label, c1, c2):
        on = ui.theme_name() == name
        return ft.Container(
            ft.Column([ft.Container(
                width=76, height=48, border_radius=BR(12, 12, 12, 12),
                gradient=ft.LinearGradient(begin=AL.TOP_LEFT,
                                           end=AL.BOTTOM_RIGHT,
                                           colors=[c1, c2])),
                ui.spacer(h=6),
                ft.Text(label, size=fs(11),
                        color=P["txt"] if on else P["sub"])],
                spacing=0, tight=True,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER),
            padding=PAD(10, 10, 10, 8), border_radius=BR(16, 16, 16, 16),
            bgcolor=op(0.10) if on else op(0.03),
            border=ft.Border(*[ft.BorderSide(1.5, P["acc"] if on else op(0.06))] * 4),
            on_click=lambda e: page.run_task(_set_theme, name), ink=True,
            animate=anim(FAST, EASE), animate_scale=anim(FAST, EASE), scale=1.0)

    def _chip(label, on, cb):
        c = ft.Container(
            ft.Text(label, size=fs(12), font_family=ui.FONT_MED,
                    color="#ffffff" if on else P["sub"]),
            padding=PAD(14, 8, 14, 8), border_radius=BR(12, 12, 12, 12),
            bgcolor=P["acc"] if on else op(0.06), on_click=cb, ink=True,
            animate=anim(FAST, EASE), alignment=AL.CENTER)
        return c

    async def _set_scale(v):
        ui.set_font_scale(v)
        await prefs.put("font_scale", str(v))
        await settings_page(page)

    async def _set_glass(v):
        """شدتِ شیشه‌ای‌بودنِ منوها و نوارها (لیکویید گلس)."""
        ui.set_glass(v)
        await prefs.put("glass", str(v))
        await settings_page(page)

    # ── سازنده‌ی پوسته v2: مدل‌های آماده + رنگ + کاغذدیواری ──
    def _theme_builder(e=None):
        pick = {
            "base": prefs.get("c_base") or "crimson",
            "preset": prefs.get("c_preset") or "",
            "img": prefs.get("chat_bg") or "",
            "dim": prefs.get("chat_bg_dim") or "0.35",
            "blur": prefs.get("chat_bg_blur") or "0",
            "busy": False,
        }
        acc_f = ui.field(t("color_accent"), value=prefs.get("c_acc") or "",
                         prefix=ui.vic("palette", 18))
        sent_f = ui.field(t("color_bubble"), value=prefs.get("c_sent") or "",
                          prefix=ui.vic("smile", 18))
        bg_f = ui.field(t("color_bg"), value=prefs.get("c_bg") or "",
                        prefix=ui.vic("image", 18))
        m = ft.Text(t("color_hint"), size=fs(11), color=P["dim"])
        holder = ft.Column([], spacing=0, tight=True)

        def _valid(v):
            v = (v or "").strip()
            return (not v) or bool(re.fullmatch(r"#?[0-9a-fA-F]{6}", v))

        def _norm(v):
            v = (v or "").strip()
            if not v:
                return ""
            return v if v.startswith("#") else "#" + v

        def _f(key, default=0.0):
            try:
                return float(pick.get(key) or default)
            except (TypeError, ValueError):
                return float(default)

        def _pal():
            """پالتِ پیش‌نمایش از روی انتخاب‌های فعلی (بدونِ اعمالِ سراسری)."""
            return ui.build_custom(pick["base"], _norm(acc_f.value),
                                   _norm(sent_f.value), _norm(bg_f.value))

        def _up():
            """بازسازیِ بدنه‌ی دیالوگ — هیچ کنترلی درجا دست‌کاری نمی‌شه."""
            holder.controls = _body()
            try:
                page.update()
            except Exception:
                pass

        # ── دست‌گیره‌ها ─────────────────────────────
        def _use_preset(p):
            pick["preset"] = p["key"]
            pick["base"] = p["base"]
            acc_f.value = p["acc"]
            sent_f.value = p["sent"]
            bg_f.value = p["bg"]
            m.value = t("color_hint")
            m.color = P["dim"]
            _up()

        def _set_base(b):
            pick["base"] = b
            pick["preset"] = ""
            _up()

        def _set_acc(hexv):
            acc_f.value = hexv
            pick["preset"] = ""
            _up()

        def _typed(_=None):
            pick["preset"] = ""
            _up()

        for f in (acc_f, sent_f, bg_f):
            f.on_change = _typed

        def _set_dim(v):
            pick["dim"] = f"{float(v):.2f}"
            _up()

        def _set_blur(v):
            pick["blur"] = f"{float(v):.0f}"
            _up()

        async def _pick_bg(_=None):
            if pick["busy"]:
                return
            name, data = await _pick(page, IMG_EXT, t("upload_bg"))
            if not data:
                return
            pick["busy"] = True
            m.value = t("uploading")
            m.color = P["sub"]
            _up()
            try:
                res = await api.upload_media_bytes(name, data)
            except Exception as ex:
                pick["busy"] = False
                m.value = _err_text(ex)
                m.color = P["acc2"]
                _up()
                return
            pick["busy"] = False
            pick["img"] = res.get("url") or ""
            m.value = t("bg_set")
            m.color = P["ok"]
            _up()

        def _drop_bg(_=None):
            pick["img"] = ""
            m.value = t("bg_removed")
            m.color = P["sub"]
            _up()

        # ── تکه‌های بدنه ────────────────────────────
        def _gallery():
            cards = [ui.preset_card(p, t(p["key"]),
                                    on=(pick["preset"] == p["key"]),
                                    on_click=lambda e, pp=p: _use_preset(pp))
                     for p in ui.PRESETS]
            return ft.Row(cards, spacing=8, wrap=True, run_spacing=8,
                          alignment=ft.MainAxisAlignment.CENTER)

        def _base_row():
            return ft.Row(
                [_chip(t("theme_crimson"), pick["base"] == "crimson",
                       lambda e: _set_base("crimson")),
                 _chip(t("theme_telegram"), pick["base"] == "telegram",
                       lambda e: _set_base("telegram")),
                 _chip(t("theme_light"), pick["base"] == "light",
                       lambda e: _set_base("light"))],
                spacing=6, tight=True, wrap=True, run_spacing=6)

        QUICK_ACC = ["#e53935", "#3390ec", "#9c5cf6", "#5c6bc0", "#f0a020",
                     "#f0568c", "#22b8cf", "#8c9cb0", "#ff7043", "#c8702a"]

        def _quick_row():
            cur = _norm(acc_f.value).lower()
            return ft.Row([ui.swatch_dot(c, on=(cur == c),
                                         on_click=lambda e, cc=c: _set_acc(cc))
                           for c in QUICK_ACC],
                          spacing=7, wrap=True, run_spacing=7,
                          alignment=ft.MainAxisAlignment.CENTER)

        def _preview():
            pal = _pal()
            bubble_in = ft.Container(
                ft.Text(t("preview_in"), size=fs(11.5), color=pal["txt"]),
                bgcolor=ft.Colors.with_opacity(0.94, pal["recv"]),
                padding=PAD(10, 7, 10, 7), border_radius=BR(14, 14, 4, 14),
                animate=anim(MED, EASE))
            bubble_out = ft.Container(
                ft.Text(t("preview_out"), size=fs(11.5), color=pal["txt"]),
                bgcolor=ft.Colors.with_opacity(0.94, pal["sent"]),
                padding=PAD(10, 7, 10, 7), border_radius=BR(14, 14, 14, 4),
                animate=anim(MED, EASE))
            chips = ft.Row(
                [ft.Container(width=30, height=8, bgcolor=pal["acc"],
                              border_radius=BR(5, 5, 5, 5)),
                 ft.Container(width=18, height=8, bgcolor=pal["acc2"],
                              border_radius=BR(5, 5, 5, 5)),
                 ft.Container(width=12, height=8, bgcolor=pal["ok"],
                              border_radius=BR(5, 5, 5, 5))],
                spacing=5, tight=True)
            body = ft.Container(
                ft.Column([ft.Row([bubble_in],
                                  alignment=ft.MainAxisAlignment.START),
                           ft.Row([bubble_out],
                                  alignment=ft.MainAxisAlignment.END),
                           chips],
                          spacing=7, tight=True),
                padding=PAD(10, 10, 10, 10))
            layers = [ft.Container(expand=True, bgcolor=pal["bg0"])]
            if pick["img"]:
                layers += ui.photo_bg(_media(pick["img"]),
                                      _f("dim", 0.35), _f("blur", 0.0))
            layers.append(body)
            return ft.Container(
                ft.Stack(layers), height=126, border_radius=BR(16, 16, 16, 16),
                border=ft.Border(*[ft.BorderSide(1, op(0.08))] * 4),
                animate=anim(MED, EASE), animate_opacity=anim(MED, EASE),
                clip_behavior=ft.ClipBehavior.ANTI_ALIAS)

        def _bg_row():
            thumb = ft.Container(
                (ui.photo(_media(pick["img"]), width=52, height=52,
                          fit=ft.BoxFit.COVER, radius=12)
                 if pick["img"] else ui.vic("image", 20, op(0.35))),
                width=52, height=52, alignment=AL.CENTER,
                bgcolor=op(0.05), border_radius=BR(12, 12, 12, 12),
                animate=anim(FAST, EASE))
            btns = [ui.primary_btn(t("change_bg") if pick["img"]
                                   else t("upload_bg"), icon="image",
                                   height=38,
                                   on_click=lambda e: page.run_task(_pick_bg))]
            if pick["img"]:
                btns.append(ui.ghost_btn(t("remove_bg"), on_click=_drop_bg))
            rows = [ft.Row([thumb,
                            ft.Column([ft.Text(t("chat_bg"), size=fs(12.5),
                                               font_family=ui.FONT_MED,
                                               color=P["txt"]),
                                       ft.Text(t("chat_bg_sub") if pick["img"]
                                               else t("no_bg"), size=fs(10.5),
                                               color=P["dim"], max_lines=2)],
                                      spacing=2, tight=True, expand=True)],
                           spacing=10,
                           vertical_alignment=ft.CrossAxisAlignment.CENTER),
                    ui.spacer(h=8),
                    ft.Row(btns, spacing=8, wrap=True, run_spacing=6)]
            if pick["img"]:
                rows += [
                    ui.spacer(h=6),
                    ft.Text(t("bg_dim"), size=fs(10.5), color=P["sub"]),
                    ft.Slider(min=0, max=0.85, divisions=17,
                              value=_f("dim", 0.35), active_color=P["acc"],
                              label="{value}",
                              on_change=lambda e: _set_dim(e.control.value)),
                    ft.Text(t("bg_blur"), size=fs(10.5), color=P["sub"]),
                    ft.Slider(min=0, max=18, divisions=9,
                              value=_f("blur", 0.0), active_color=P["acc"],
                              label="{value}",
                              on_change=lambda e: _set_blur(e.control.value))]
            return ft.Column(rows, spacing=0, tight=True)

        def _lbl(txt, sub=None):
            return ft.Column(
                [ft.Text(txt, size=fs(12.5), font_family=ui.FONT_MED,
                         color=P["txt"]),
                 *([ft.Text(sub, size=fs(10.5), color=P["dim"])] if sub else [])],
                spacing=1, tight=True)

        def _sep():
            return ft.Container(height=1, bgcolor=op(0.07),
                                margin=MG(0, 12, 0, 12))

        def _body():
            return [_lbl(t("theme_models"), t("theme_models_sub")),
                    ui.spacer(h=10), _gallery(),
                    _sep(),
                    _lbl(t("live_preview")), ui.spacer(h=8), _preview(),
                    ui.spacer(h=12), _base_row(),
                    ui.spacer(h=12), _lbl(t("quick_colors")),
                    ui.spacer(h=8), _quick_row(),
                    ui.spacer(h=12), acc_f, ui.spacer(h=10), sent_f,
                    ui.spacer(h=10), bg_f,
                    _sep(),
                    _bg_row(),
                    ui.spacer(h=10), m]

        async def _apply(_=None):
            for f in (acc_f, sent_f, bg_f):
                if not _valid(f.value):
                    m.value = t("bad_color")
                    m.color = P["acc2"]
                    _up()
                    return
            await prefs.put("c_base", pick["base"])
            await prefs.put("c_preset", pick["preset"])
            await prefs.put("c_acc", _norm(acc_f.value))
            await prefs.put("c_sent", _norm(sent_f.value))
            await prefs.put("c_bg", _norm(bg_f.value))
            await prefs.put("chat_bg", pick["img"])
            await prefs.put("chat_bg_dim", pick["dim"])
            await prefs.put("chat_bg_blur", pick["blur"])
            _use_theme(ui.CUSTOM)
            await prefs.put("theme", ui.CUSTOM)
            if not ui.is_light():
                await prefs.put("dark_theme", ui.CUSTOM)
            page.pop_dialog()
            _toast(page, t("saved"), ok=True)
            await settings_page(page)

        holder.controls = _body()
        ui.dialog(page, t("theme_builder"),
                  ft.Container(ft.Column([holder], spacing=0, tight=True,
                                         scroll=ft.ScrollMode.AUTO),
                               height=430),
                  actions=[ui.ghost_btn(t("cancel"),
                                        on_click=lambda e: page.pop_dialog()),
                           ui.primary_btn(t("apply"), width=110, height=40,
                                          on_click=lambda e: page.run_task(_apply))],
                  width=396)

    sc = ui.font_scale()
    gl = ui.glass_level()
    appearance = ui.group(
        ft.Container(ft.Row([_theme_card("crimson", t("theme_crimson"),
                                         "#e53935", "#3d0c0c"),
                             _theme_card("telegram", t("theme_telegram"),
                                         "#3390ec", "#17212b"),
                             _theme_card("light", t("theme_light"),
                                         "#3390ec", "#e9eef5"),
                             _theme_card(ui.CUSTOM, t("theme_custom"),
                                         prefs.get("c_acc") or "#9c27b0",
                                         prefs.get("c_sent") or "#4a148c")],
                            spacing=10, wrap=True, run_spacing=10,
                            alignment=ft.MainAxisAlignment.CENTER),
                     padding=PAD(14, 12, 14, 8)),
        ui.row_item("palette", t("theme_builder"), subtitle=t("theme_builder_sub"),
                    trailing=ui.vic("chevron_left" if i18n.rtl()
                                    else "chevron_right", 16),
                    on_click=_theme_builder),
        ui.row_item("sun" if ui.is_light() else "moon", t("day_night"),
                    subtitle=t("day_night_sub"),
                    trailing=_chip(t("day_mode") if ui.is_light()
                                   else t("night_mode"), True,
                                   lambda e: page.run_task(_toggle_day_night,
                                                           page, settings_page))),
        ui.toggle_row("pattern", t("chat_pattern"), prefs.flag("pattern"),
                      lambda e: page.run_task(_set_flag_reload, "pattern",
                                              e.control.value),
                      subtitle=t("chat_pattern_sub")),
        ui.row_item("globe", t("language"), trailing=ft.Row(
            [_chip(t("persian"), i18n.lang() == "fa",
                   lambda e: page.run_task(_flip_lang, page, "fa", settings_page)),
             _chip(t("english"), i18n.lang() == "en",
                   lambda e: page.run_task(_flip_lang, page, "en", settings_page))],
            spacing=6, tight=True)),
        ui.row_item("text_size", t("font_size"), trailing=ft.Row(
            [_chip(t("font_small"), abs(sc - 0.9) < 0.02,
                   lambda e: page.run_task(_set_scale, 0.9)),
             _chip(t("font_normal"), abs(sc - 1.0) < 0.02,
                   lambda e: page.run_task(_set_scale, 1.0)),
             _chip(t("font_large"), abs(sc - 1.15) < 0.02,
                   lambda e: page.run_task(_set_scale, 1.15))],
            spacing=6, tight=True)),
        # ── میزانِ «لیکویید گلس» بودنِ منوها و نوارها ──
        ui.row_item("eye", t("glass_level"), subtitle=t("glass_level_sub"),
                    trailing=ft.Row(
            [_chip(t("glass_flat"), gl < 0.05,
                   lambda e: page.run_task(_set_glass, 0.0)),
             _chip(t("glass_normal"), abs(gl - 1.0) < 0.06,
                   lambda e: page.run_task(_set_glass, 1.0)),
             _chip(t("glass_high"), gl > 1.45,
                   lambda e: page.run_task(_set_glass, 1.6))],
            spacing=6, tight=True)),
        ft.Container(ft.Slider(min=0, max=1.6, divisions=16, value=gl,
                               active_color=P["acc"], inactive_color=op(0.16),
                               thumb_color=P["acc"], label="{value}",
                               on_change_end=lambda e: page.run_task(
                                   _set_glass, round(float(e.control.value), 2))),
                     padding=PAD(8, 0, 8, 4)))

    # ── حریم خصوصی و امنیت ──────────────────────────
    async def _sessions(e=None):
        try:
            rows = await api.list_sessions()
        except Exception as ex:
            _toast(page, _err_text(ex))
            return

        async def _kill(sid):
            try:
                await api.revoke_session(sid)
            except Exception as ex:
                _toast(page, _err_text(ex))
                return
            page.pop_dialog()
            await _sessions()

        async def _kill_rest(_=None):
            try:
                await api.revoke_other_sessions()
            except Exception as ex:
                _toast(page, _err_text(ex))
                return
            page.pop_dialog()
            _toast(page, t("saved"), ok=True)
            await _sessions()

        items = []
        for s in rows:
            cur = bool(s.get("current"))
            trail = (ft.Text(t("this_device"), size=fs(10), color=P["ok"])
                     if cur else
                     ui.icon_btn("close", box=34, size=16, color=P["acc2"],
                                 tip=t("revoke"),
                                 on_click=lambda e, i=s["session_id"]:
                                     page.run_task(_kill, i)))
            items.append(ui.row_item("monitor", s.get("device") or "?",
                                     _when(s.get("last_active_at")),
                                     trailing=trail))
        body = (ft.Column([ui.group(*items), ui.spacer(h=10),
                           ui.row_item("logout", t("revoke_others"),
                                       on_click=lambda e: page.run_task(_kill_rest),
                                       icon_color=P["acc2"])],
                          spacing=0, tight=True, scroll=ft.ScrollMode.AUTO)
                if items else
                ft.Text(t("no_sessions"), size=fs(13), color=P["sub"]))
        ui.dialog(page, t("active_sessions"),
                  ft.Container(body, height=min(460, 120 + 66 * len(items))),
                  actions=[ui.ghost_btn(t("close"),
                                        on_click=lambda e: page.pop_dialog())],
                  width=400)

    async def _blocked(e=None):
        try:
            rows = await api.list_blocks()
        except Exception as ex:
            _toast(page, _err_text(ex))
            return

        async def _un(u):
            try:
                await api.unblock_user(u)
            except Exception as ex:
                _toast(page, _err_text(ex))
                return
            page.pop_dialog()
            _toast(page, t("unblocked_done"), ok=True)
            await _blocked()

        items = [ui.row_item(
            None, b.get("display_name") or "", f"@{b.get('username')}",
            trailing=ft.TextButton(
                t("unblock"),
                on_click=lambda e, u=b.get("username"): page.run_task(_un, u),
                style=ft.ButtonStyle(color=P["acc"],
                                     shape=ft.RoundedRectangleBorder(radius=10))))
            for b in rows]
        body = (ft.Column([ui.group(*items)], spacing=0, tight=True,
                          scroll=ft.ScrollMode.AUTO) if items else
                ft.Text(t("no_blocked"), size=fs(13), color=P["sub"]))
        ui.dialog(page, t("blocked_users"),
                  ft.Container(body, height=min(420, 90 + 62 * len(items))),
                  actions=[ui.ghost_btn(t("close"),
                                        on_click=lambda e: page.pop_dialog())],
                  width=380)

    async def _set_seen(v):
        try:
            await api.set_privacy(bool(v))
        except Exception as ex:
            _toast(page, _err_text(ex))
            return
        # انبارِ محلی هم هم‌زمان، وگرنه بارِ بعد که تنظیمات باز شود سوییچ
        # لحظه‌ای مقدارِ کهنه را نشان می‌دهد و بعد می‌پرد.
        page.run_task(store.save_blob, "priv", {"show_last_seen": bool(v)})
        _toast(page, t("saved"), ok=True)

    async def _set_flag(key, v):
        await prefs.put(key, "1" if v else "0")

    async def _set_notify(v):
        """کلیدِ اعلان — روشن‌کردنش همان‌جا اجازه‌ی سیستم را هم می‌گیرد.

        سافاری و آیفون اجازه‌ی اعلان را *فقط* در پاسخِ ژستِ کاربر می‌دهند، پس
        پرسشِ زمانِ راه‌افتادن آن‌جا بی‌اثر است؛ همین کلیک، لحظه‌ی درستش است.
        """
        await prefs.put("notify", "1" if v else "0")
        if not v:
            return
        try:
            notify.ask(page)
            await _ask_perm(page, "NOTIFICATION")
        except Exception:
            pass

    async def _set_flag_reload(key, v):
        await prefs.put(key, "1" if v else "0")
        await settings_page(page)

    async def _push_test(_=None):
        """اعلانِ آزمایشی: از سرور می‌خواهیم همین حساب را خبر کند.

        همان مسیرِ پیامِ واقعی را می‌رود، پس اگر این اعلان آمد یعنی همه‌ی
        زنجیره سالم است و اگر نیامد ایراد در سرور یا در اجازه‌ی دستگاه است
        — نه در دانستنِ ما.
        """
        try:
            await api.push_test()
        except Exception as ex:
            _toast(page, _err_text(ex))
            return
        _toast(page, t("push_test_sent"), ok=True)

    # ── رمزنگاری ────────────────────────────────────
    def _enc_dialog(e=None):
        key_f = ui.field(t("enc_key"), password=True, prefix=ui.vic("key", 18))
        m = ft.Text(t("enc_key_hint"), size=fs(11), color=P["dim"])

        async def _ok(_=None):
            pw = key_f.value or ""
            if len(pw.strip()) < 6:
                m.value = t("enc_key_short")
                m.color = P["acc2"]
                page.update()
                return
            if not crypto.set_key(pw):
                m.value = t("error")
                m.color = P["acc2"]
                page.update()
                return
            await prefs.put("enc_key", pw)
            page.pop_dialog()
            _toast(page, t("enc_key_set", fp=crypto.fingerprint()), ok=True)
            await settings_page(page)

        async def _clear(_=None):
            crypto.clear_key()
            await prefs.put("enc_key", "")
            await prefs.put("enc", "0")
            page.pop_dialog()
            _toast(page, t("enc_key_cleared"), ok=True)
            await settings_page(page)

        acts = [ui.ghost_btn(t("cancel"), on_click=lambda e: page.pop_dialog())]
        if crypto.has_key():
            acts.insert(0, ui.ghost_btn(t("enc_key_clear"),
                                        on_click=lambda e: page.run_task(_clear)))
        acts.append(ui.primary_btn(t("save"), width=100, height=40,
                                   on_click=lambda e: page.run_task(_ok)))
        key_f.on_submit = lambda e: page.run_task(_ok)
        ui.dialog(page, t("encryption"),
                  ft.Column([key_f, ui.spacer(h=8), m], spacing=0, tight=True),
                  actions=acts)

    async def _toggle_enc(v):
        if v and not crypto.has_key():
            _toast(page, t("enc_need_key"))
            _enc_dialog()
            return
        await prefs.put("enc", "1" if v else "0")
        await settings_page(page)

    fp = crypto.fingerprint() if crypto.has_key() else None
    security = ui.group(
        ui.toggle_row("lock", t("encryption"), prefs.flag("enc"),
                      lambda e: page.run_task(_toggle_enc, e.control.value),
                      subtitle=t("encryption_sub")),
        ui.row_item("key", t("enc_key"),
                    subtitle=(t("enc_fp", fp=fp) if fp else t("enc_no_key")),
                    trailing=ui.vic("chevron_left" if i18n.rtl()
                                    else "chevron_right", 16),
                    on_click=_enc_dialog))

    # ── بایگانی ─────────────────────────────────────
    arch_n = 0
    try:
        arch_n = int(await api.archived_count())
    except Exception:
        pass
    archive = ui.group(
        ui.row_item("archive", t("archived_chats"),
                    subtitle=(t("archived_n", n=i18n.num(arch_n)) if arch_n
                              else t("no_archived")),
                    trailing=(ui.badge(arch_n, muted=True) if arch_n else
                              ui.vic("chevron_left" if i18n.rtl()
                                     else "chevron_right", 16)),
                    on_click=lambda e: page.run_task(chat_list_page, page, True)))

    _seen_sw = []
    privacy = ui.group(
        ui.row_item("monitor", t("active_sessions"),
                    trailing=ui.vic("chevron_left" if i18n.rtl()
                                    else "chevron_right", 16),
                    on_click=lambda e: page.run_task(_sessions)),
        ui.row_item("ban", t("blocked_users"),
                    trailing=ui.vic("chevron_left" if i18n.rtl()
                                    else "chevron_right", 16),
                    on_click=lambda e: page.run_task(_blocked)),
        ui.toggle_row("eye", t("last_seen_privacy"),
                      priv.get("show_last_seen", True),
                      lambda e: page.run_task(_set_seen, e.control.value),
                      subtitle=t("last_seen_privacy_desc"), out=_seen_sw))

    # ── اعلان ───────────────────────────────────────
    # ردیفِ نخست *صادق* است: روی نسخه‌ای که سرویسِ بومی دارد می‌گوید اعلان با
    # برنامه‌ی بسته هم می‌آید، و روی وب/ویندوز/APKِ قدیمی همان‌جا می‌گوید فقط
    # وقتی برنامه باز است. `push.available()` وجودِ `native.json` را می‌بیند —
    # مُهری که خودِ اکتیویتیِ کاتلین سرِ هر باز شدن می‌گذارد.
    _bg_ok = False
    try:
        _bg_ok = push.available()
    except Exception:
        pass
    notifs = ui.group(
        ui.row_item("shield" if _bg_ok else "info",
                    t("push_bg_on") if _bg_ok else t("push_bg_off"),
                    subtitle=(t("push_bg_on_desc") if _bg_ok
                              else t("push_bg_off_desc")),
                    icon_color=P["acc"] if _bg_ok else P["sub"]),
        ui.toggle_row("bell", t("notify"), prefs.flag("notify"),
                      lambda e: page.run_task(_set_notify, e.control.value),
                      subtitle=t("notify_desc")),
        ui.toggle_row("bell", t("sound"), prefs.flag("sound"),
                      lambda e: page.run_task(_set_flag, "sound",
                                              e.control.value)),
        ui.toggle_row("phone", t("vibrate"), prefs.flag("vibrate"),
                      lambda e: page.run_task(_set_flag, "vibrate",
                                              e.control.value),
                      subtitle=t("vibrate_desc")),
        ui.toggle_row("dot", t("unread_badges"), prefs.flag("badges"),
                      lambda e: page.run_task(_set_flag, "badges",
                                              e.control.value)),
        ui.row_item("send", t("push_test"), subtitle=t("push_test_desc"),
                    trailing=ui.vic("chevron_left" if i18n.rtl()
                                    else "chevron_right", 16),
                    on_click=lambda e: page.run_task(_push_test)))

    # ── سرور ────────────────────────────────────────
    server = ui.group(
        ui.row_item("globe", t("server"),
                    subtitle=config.api_base(),
                    trailing=ui.vic("chevron_left" if i18n.rtl()
                                    else "chevron_right", 16),
                    on_click=lambda e: server_dialog(
                        page, after=lambda p: settings_page(p))))

    def _logout_dialog(e=None):
        ui.dialog(page, t("logout"),
                  ft.Text(t("logout_q"), size=fs(13), color=P["sub"]),
                  actions=[ui.ghost_btn(t("cancel"),
                                        on_click=lambda _: page.pop_dialog()),
                           ui.primary_btn(t("yes_logout"), width=130, height=40,
                                          on_click=lambda _: (
                                              page.pop_dialog(),
                                              page.run_task(_logout, page)))])

    cam = ft.Container(
        ui.vic("camera", 16, "#ffffff"), width=32, height=32,
        bgcolor=P["acc"], border_radius=BR(16, 16, 16, 16), alignment=AL.CENTER,
        ink=True, tooltip=t("pick_photo"),
        on_click=lambda e: page.run_task(_pick_avatar, page, settings_page))
    card = ui.glass(
        ft.Column(
            [ft.Stack([ui.avatar(_me.get("display_name") or "?", 92,
                                 url=_media(_me.get("avatar_url")), ring=True,
                                 on_click=lambda e: page.run_task(
                                     _show_my_profile, page)),
                       ft.Container(cam, right=0, bottom=0)],
                      width=92, height=92),
             ui.spacer(h=10),
             ft.Text(_me.get("display_name") or "", size=fs(18), weight=W.W_600,
                     font_family=ui.FONT_BOLD, color=P["txt"]),
             ft.Text(f"@{_me.get('username') or ''}", size=fs(12),
                     color=P["acc"]),
             ui.spacer(h=8),
             ui.dots(len(_me.get("photos") or []), 0, width=120)],
            spacing=0, tight=True,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER),
        radius=22, pad=PAD(18, 18, 18, 14), tint=0.06)

    account = ui.group(
        ft.Container(ft.Column([name_f, ui.spacer(h=10), user_f,
                                ui.spacer(h=10), bio_f, ui.spacer(h=12),
                                ui.primary_btn(t("save"), icon="check",
                                               height=42,
                                               on_click=lambda e: page.run_task(_save_profile))],
                               spacing=0, tight=True),
                     padding=PAD(14, 14, 14, 14)),
        ui.row_item("image", t("my_photos"),
                    subtitle=t("photos_n",
                               n=i18n.num(len(_me.get("photos") or [])),
                               max=i18n.num(5)),
                    trailing=ui.vic("chevron_left" if i18n.rtl()
                                    else "chevron_right", 16),
                    on_click=lambda e: page.run_task(_show_my_profile, page)),
        ui.row_item("key", t("change_password"), on_click=_pw_dialog,
                    trailing=ui.vic("chevron_left" if i18n.rtl()
                                    else "chevron_right", 16)))

    async def _contact_us(e=None):
        """«ارتباط با ما» → دیپ‌لینکِ `/start support` در تلگرام.

        لینک را از خودِ سرور می‌پرسیم (نامِ ربات آنجا نگه داشته می‌شود، نه
        اینجا) و فقط سرِ فشردن، تا بازکردنِ تنظیمات یک درخواستِ اضافه نداشته
        باشد. بعدش هر چه بنویسی به‌شکلِ تیکت به مدیر می‌رسد و پاسخش در همان
        گفت‌وگوی ربات برمی‌گردد.
        """
        link = ""
        try:
            link = (await api.referral_info() or {}).get("support_link") or ""
        except Exception:
            pass
        if not link:
            _toast(page, t("contact_off"))
            return
        _toast(page, t("contact_hint"))
        await _open_url(page, link)          # خودش سرِ شکست نشانی را نشان می‌دهد

    # ── دانلودِ مستقیمِ برنامه — فقط در نسخه‌ی وب ─────────────────────
    # خواسته‌ی نسخه‌ی ۱.۶: «توی نسخه وب باید قابلیت دانلود مستقیم برنامه
    # برای گوشی و پیسی هم باشه». روی برنامه‌ی نصب‌شده این دو ردیف بی‌معنی
    # است — کاربر *همان* فایل را اجرا کرده و راهِ تازه‌شدنش «بررسیِ
    # به‌روزرسانی» است، نه دانلودِ دستی.
    def _dl_row(icon, key, url):
        return ui.row_item(
            icon, t(key), t(f"{key}_sub"),
            on_click=lambda e: _open_url_bg(page, url, save=True),
            trailing=ui.vic("download", 16))

    dl_rows = []
    if _web["on"] and _DL_BASE:
        dl_rows = [_dl_row("phone", "dl_apk", DL_APK),
                   _dl_row("monitor", "dl_exe", DL_EXE)]
        # روی گوشی اول اندروید، روی رایانه اول ویندوز. در نسخه‌ی وب
        # `page.platform` را فلاتر از خودِ مرورگر می‌گیرد، پس درست است.
        if str(getattr(getattr(page, "platform", None), "name", "")).upper() \
                not in ("ANDROID", "ANDROID_TV", "IOS"):
            dl_rows.reverse()

    about = ui.group(
        ui.row_item("info", t("version"), f"{i18n.num(APP_VERSION)}"),
        *dl_rows,
        ui.row_item("download", t("update_check"),
                    on_click=lambda e: page.run_task(_update_check, page, True),
                    trailing=ui.vic("chevron_left" if i18n.rtl()
                                    else "chevron_right", 16)),
        ui.row_item("send", t("contact_us"), t("contact_hint"),
                    on_click=lambda e: page.run_task(_contact_us),
                    trailing=ui.vic("chevron_left" if i18n.rtl()
                                    else "chevron_right", 16)),
        ui.row_item("mic", t("voice"), t("voice_format")),
        ui.row_item("logout", t("logout"), on_click=_logout_dialog,
                    icon_color=P["acc2"]))

    head = ui.glass_bar(
        ft.Row([_back(page, chat_list_page),
                ft.Text(t("settings"), size=fs(17), weight=W.W_700,
                        font_family=ui.FONT_BOLD, color=P["txt"], expand=True),
                _daynight_btn(page, settings_page)],
               spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        top=True)

    blocks = [card,
              ui.section(t("profile_account")), account,
              ui.section(t("appearance")), appearance,
              ui.section(t("chats")), archive,
              ui.section(t("privacy_security")), privacy, security,
              ui.section(t("notifications")), notifs,
              ui.section(t("server")), server,
              ui.section(t("about")), about,
              ui.spacer(h=NAV_H + 20)]
    for b in blocks:
        ui.prep_in(b, dy=0.06, ms=MED)
    ui.prep_in(head, dy=-0.25, ms=MED)
    nav = _nav(page, "settings")
    ui.prep_in(nav, dy=0.6, ms=MED)

    body = ft.Column(
        [head,
         ft.Container(
             ft.Stack([ft.Column(blocks, spacing=0, tight=True,
                                 scroll=ft.ScrollMode.AUTO, expand=True),
                       ft.Container(nav, left=0, right=0, bottom=0)],
                      expand=True),
             padding=PAD(14, 10, 14, 0), expand=True)],
        spacing=0, expand=True)
    _go(page, body)
    page.run_task(ui.enter, page, head, *blocks, stagger=0.03)
    page.run_task(ui.enter, page, nav, stagger=0.0, first_delay=0.05)
    page.run_task(_nav_sync, page, nav, "settings", gen)

    async def _refresh():
        """تازه‌کردنِ پشتِ صحنه — *بعد* از آن‌که صفحه رنگ شده.

        قاعده‌ی جانشین‌نکردنِ نوشته‌ی کاربر: هر خانه فقط وقتی به‌روز می‌شود که
        دست‌نخورده مانده باشد (مقدارش همان است که خودمان گذاشتیم)؛ وگرنه
        پاسخِ دیرهنگامِ شبکه چیزی را که کاربر تایپ کرده پاک می‌کرد.
        """
        seed = {"n": name_f.value, "u": user_f.value, "b": bio_f.value}
        ok = await _load_me()
        if not _alive(gen):
            return
        if ok:
            _net_mark(True)
            pairs = ((name_f, "n", "display_name"), (user_f, "u", "username"),
                     (bio_f, "b", "bio"))
            hit = [f for f, k, key in pairs
                   if f.value == seed[k]
                   and (f.value or "") != (_me.get(key) or "")]
            for f, k, key in pairs:
                if f.value == seed[k]:
                    f.value = _me.get(key) or ""
            if hit:
                _safe_update(*hit)
        try:
            got = await asyncio.wait_for(api.get_privacy(), timeout=6.0)
        except Exception:
            return
        if not (_alive(gen) and isinstance(got, dict)):
            return
        page.run_task(store.save_blob, "priv", dict(got))
        want = bool(got.get("show_last_seen", True))
        if _seen_sw and bool(_seen_sw[0].value) != want:
            _seen_sw[0].value = want
            _safe_update(_seen_sw[0])

    page.run_task(_refresh)


# ═══════════════════════════════════════════════════
#  راه‌اندازی
# ═══════════════════════════════════════════════════
async def main(page: ft.Page):
    global _picker, _recorder, _clip, _geo, _perm, _url

    _pg["p"] = page          # کارهای پشتِ صحنه از همین‌جا صفحه را می‌گیرند

    # ردِ پاهای اجرای قبل — *قبل* از ساختنِ هر چیزی. اگر برنامه دفعه‌ی پیش
    # وسطِ تماسِ نیتیو مرده باشد (ضبطِ صدا، رمزگشای عکس) این‌جا می‌فهمیم و
    # همان مسیر را این‌بار نمی‌رویم. → `_CRASHED`، `_rec_ban`
    try:
        _marks_boot()
    except Exception:
        pass

    # مرورگر یا برنامه؟ همین اول لازم است (ضبطِ صدا، فایلِ موقت).
    try:
        _web["on"] = bool(getattr(page, "web", False))
    except Exception:
        _web["on"] = False
    prefs.attach(ft.SharedPreferences())
    try:
        await prefs.load()
    except Exception:
        pass
    i18n.set_lang(prefs.get("lang", "fa"))
    # آدرسِ سروری که کاربر ذخیره کرده (روی گوشی حتماً لازمه)
    if prefs.get("server"):
        try:
            config.set_base(prefs.get("server"))
            api.rebase()
        except Exception:
            pass
    elif _web["on"]:
        # در مرورگر، سرور همان دامنه‌ای است که خودِ صفحه از آن آمده (ورسل که
        # درخواست‌ها را به سرور پیش می‌دهد). آدرسِ ثابتِ ریلوی در ایران فیلتر
        # است و از داخلِ صفحه‌ی وب هم CORS می‌خورد ⇒ برنامه بالا نمی‌آمد.
        try:
            origin = config.page_origin(getattr(page, "url", "") or "")
            if origin:
                config.set_base(origin)
                api.rebase()
        except Exception:
            pass
    # ── دروازه‌ی سرویسِ اعلانِ بومی ─────────────────
    # `push.json` تنها راهی‌ست که سرویسِ کاتلین (اعلان با برنامه‌ی بسته) توکن
    # و نشانیِ سرور را می‌بیند. `prefs.put` هر بار که یکی از این‌ها عوض شود
    # خودش از نو می‌نویسدش، ولی سرِ *اولین* اجرا هیچ‌کدام عوض نمی‌شوند و
    # فایل هرگز ساخته نمی‌شد ⇒ سرویس بی‌کار می‌ماند. این‌جا، پس از بازگردانیِ
    # نشانیِ سرور (که در فایل می‌رود)، یک‌بار صریح می‌نویسیمش.
    try:
        push.sync_from_prefs()
    except Exception:
        pass
    _use_theme(prefs.get("theme", "crimson"))
    ui.set_font_scale(prefs.scale())
    ui.set_glass(prefs.glass())
    if prefs.get("enc_key"):
        try:
            crypto.set_key(prefs.get("enc_key"))
        except Exception:
            pass

    page.title = t("app_name")
    page.fonts = FONTS
    page.rtl = i18n.rtl()
    page.theme_mode = (ft.ThemeMode.LIGHT if ui.is_light()
                       else ft.ThemeMode.DARK)
    page.theme = ui.flet_theme()
    page.bgcolor = P["bg0"]
    page.padding = 0
    # ── گوشی یا رومیزی؟ ─────────────────────────────
    phone = ui.set_device(page)
    _window_setup(page, phone)

    def _on_resize(e=None):
        """چرخشِ گوشی / تغییرِ اندازه‌ی پنجره: عرضِ مرجع را تازه کن."""
        ui.set_device(page)

    try:
        page.on_resized = _on_resize
    except Exception:
        pass

    # ── حضور: آنلاین فقط وقتی برنامه «جلو» است ───────
    def _life(e=None):
        """چرخه‌ی عمرِ برنامه ⇒ خبرِ حضور به سرور.

        RESUME/SHOW یعنی برنامه جلو آمد؛ HIDE/PAUSE/INACTIVE/DETACH یعنی رفت
        پس‌زمینه یا بسته شد. سرور با همین بی‌درنگ آفلاین‌مان می‌کند، پس
        دیگر برنامه‌ی بسته «آنلاین» نشان داده نمی‌شود.
        """
        s = str(getattr(e, "state", "") or "").upper()
        fg = ("RESUME" in s) or ("SHOW" in s)
        if not fg:
            # ضبطِ در جریان را همین‌جا رها کن (توضیحش سرِ `_rec_abort`).
            fn = _rec_abort.get("fn")
            if fn:
                try:
                    fn()
                except Exception:
                    pass
        notify.set_foreground(fg)
        if fg:
            # سوکت در پس‌زمینه مرده و نگهبان شاید وسطِ خوابِ پس‌رفت باشد.
            ws_client.wake()
            # ضربه روی اعلانِ سرویسِ بومی برنامه را از پس‌زمینه بالا می‌آورد
            # و هیچ رویدادی به پایتون نمی‌رسد؛ تنها نشانه همین برگشت است.
            _push_tap(page)
        page.run_task(ws_client.send_presence, fg)

    try:
        page.on_app_lifecycle_state_change = _life
    except Exception:
        pass

    # سرویس‌ها: با ساخته‌شدن خودشون رو ثبت می‌کنن و باید ارجاعِ قویشون
    # نگه داشته بشه (متغیرِ ماژول)؛ هرگز به درختِ کنترل‌ها اضافه نمی‌شن.
    _picker = ft.FilePicker()
    try:
        # بازکنندهٔ پیوند. همین‌جا می‌سازیمش تا ثبتش قطعی باشد؛ `_url_svc`
        # سرِ ناچاری خودش هم می‌سازد، ولی آن‌جا `context.page` تضمینی نیست.
        _url = ft.UrlLauncher()
    except Exception:
        _url = None
    try:
        _clip = ft.Clipboard()
    except Exception:
        _clip = None
    if fgl is not None:
        # موقعیت‌یاب: روی دسکتاپ/وب ممکن است نباشد؛ نبودش نباید هیچ‌چیز را
        # بشکند، فقط گزینه‌ی «موقعیت» پیامِ «پشتیبانی نمی‌شود» می‌دهد.
        try:
            _geo = fgl.Geolocator()
        except Exception:
            _geo = None
    if fph is not None:
        # مجوزهای زمانِ اجرا. این خط پیش‌تر *نبود*: `_perm` همیشه None می‌ماند
        # و `_ask_perm` بی‌صدا True می‌داد، پس RECORD_AUDIO هرگز خواسته
        # نمی‌شد و ضبط در لایه‌ی جاوا با SecurityException پروسه را می‌کشت.
        try:
            _perm = fph.PermissionHandler()
        except Exception:
            _perm = None
    if far is not None:
        try:
            # تنظیماتِ *پیش‌فرضِ* سرویس هم باید قانونی باشد: اگر جایی
            # `start_recording` بی `configuration` صدا خورد، این می‌نشیند.
            got = _rec_config("OPUS" if _web["on"] else "AACLC")
            _recorder = (far.AudioRecorder(configuration=got[0]) if got
                         else far.AudioRecorder())
        except Exception:
            _recorder = None
        # چیدنِ نقشه *بی‌انتظار*: تا هفت رفت‌وبرگشت با لایه‌ی Flutter است و
        # در مرورگرِ گوشی همین‌جا چند ثانیه صفحه را سفید نگه می‌داشت. اگر
        # کاربر پیش از پایانش میکروفون بزند، `_start_rec` خودش منتظر می‌شود.
        page.run_task(_pick_encoder)
    notify.attach(page)

    # نشستِ ذخیره‌شده؟ صفحه‌ی ورود رد می‌شود (تا «خروج از حساب»)
    try:
        if await _resume_session(page):
            return
    except Exception:
        pass
    await auth_page(page)


if __name__ == "__main__":
    # پوشه‌ی دارایی‌ها را صریح بده؛ وگرنه اجرا از سورس آیکون‌های SVG را پیدا نمی‌کند
    # (در نسخه‌ی بسته‌بندی‌شده هم همین مسیر داخلِ بسته درست درمی‌آید).
    ft.run(main, assets_dir=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "assets"))
