"""
موقعیتِ جغرافیایی — یک قرارداد، سه مشتری.

پیامِ موقعیت در رفیگرام *متنِ ساده* است، نه یک `msg_type`ِ تازه:

    📍 موقعیت: 35.72841, 51.38912
    https://www.google.com/maps?q=35.728410,51.389120

چرا متن؟ چون سرور و انبارِ محلی و جست‌وجو و فوروارد و نقلِ‌قول همه از قبل
متن را می‌شناسند. نوعِ تازه یعنی مهاجرتِ پایگاه‌داده و اینکه نسخه‌های قدیمیِ
برنامه پیامِ ناشناس ببینند. این‌طور، نسخه‌ی قدیمی همان دو خط را می‌بیند و
روی خطِ دوم می‌زند و نقشه باز می‌شود؛ نسخه‌ی تازه به‌جایش کارتِ نقشه می‌کشد.

`parse()` سخت‌گیر است: تنها وقتی مختصات می‌دهد که *همه‌ی* پیام یک موقعیت
باشد. وگرنه هر جمله‌ای که تویش لینکِ نقشه بود، متنش ناپدید می‌شد.
"""
import math
import re

# قالبِ خواسته‌ی کاربر: `www.google.com/maps?q=` — روی گوشی خودِ برنامه‌ی
# گوگل‌مپ را باز می‌کند و روی رومیزی صفحه‌ی وب را.
LINK = "https://www.google.com/maps?q={lat:.6f},{lon:.6f}"
ZOOM = 15                      # ≈ یک محله در هر کاشی
TILE = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"

_LAT_MAX = 85.05112878          # سقفِ تصویرِ مرکاتور

# `?q=`، `?ll=`، `?daddr=`، `?center=` — و کاماىِ کدشده هم پذیرفته است
_P_Q = re.compile(
    r"[?&](?:q|ll|daddr|saddr|center|destination)="
    r"(-?\d{1,3}(?:\.\d+)?)(?:,|%2C|%2c)\s*(-?\d{1,3}(?:\.\d+)?)")
# `/maps/@35.7,51.3,15z` — شکلی که از نوارِ نشانیِ گوگل‌مپ کپی می‌شود
_P_AT = re.compile(r"/@(-?\d{1,3}(?:\.\d+)?),(-?\d{1,3}(?:\.\d+)?)")
# `geo:35.7,51.3` — قراردادِ اندروید
_P_GEO = re.compile(r"^geo:(-?\d{1,3}(?:\.\d+)?),(-?\d{1,3}(?:\.\d+)?)")
# `openstreetmap.org/?mlat=35.7&mlon=51.3`
_P_OSM = re.compile(r"[?&]mlat=(-?\d{1,3}(?:\.\d+)?)"
                    r".*?[?&]mlon=(-?\d{1,3}(?:\.\d+)?)")
# فقط «۳۵٫۷، ۵۱٫۳» — پیامی که کاربر دستی مختصات نوشته
_P_BARE = re.compile(r"^(-?\d{1,3}\.\d+)\s*,\s*(-?\d{1,3}\.\d+)$")

_URL_HEAD = ("http://", "https://", "geo:")


def link(lat: float, lon: float) -> str:
    """نشانیِ گوگل‌مپِ این نقطه."""
    return LINK.format(lat=float(lat), lon=float(lon))


def nav(lat: float, lon: float) -> str:
    """نشانیِ `geo:` — همان نقطه، ولی برای *برنامه‌ی نقشه‌ی خودِ گوشی*.

    روی اندروید این طرحِ نشانی را سیستم به هر برنامه‌ی نقشه‌ای که نصب است
    می‌دهد (گوگل‌مپ، نشان، بلد) و مرورگر از میان برداشته می‌شود؛ پس هم سریع‌تر
    است هم به دسترسیِ گوگل نیاز ندارد. `?q=` را هم می‌گذاریم تا سنجاق با
    برچسب بیفتد، وگرنه بعضی برنامه‌ها فقط دوربین را جابه‌جا می‌کنند.

    بیرونِ اندروید معنایی ندارد ⇒ صداکننده باید `link()` را جانشین بگذارد.
    """
    p = f"{float(lat):.6f},{float(lon):.6f}"
    return f"geo:{p}?q={p}"


def compose(lat: float, lon: float, label: str = "") -> str:
    """متنِ پیامِ موقعیت. `label` نامِ ترجمه‌شده‌ی «موقعیت» است."""
    head = f"📍 {label}: " if label else "📍 "
    return (f"{head}{float(lat):.5f}, {float(lon):.5f}\n"
            f"{link(lat, lon)}")


def _ok(lat, lon):
    try:
        la, lo = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    if not (-90.0 <= la <= 90.0) or not (-180.0 <= lo <= 180.0):
        return None
    if la == 0.0 and lo == 0.0:
        return None                 # «جزیره‌ی خالی» — یعنی چیزی اشتباه خوانده‌ایم
    return la, lo


def from_url(u: str):
    """(عرض، طول) از یک نشانیِ نقشه — یا None."""
    s = (u or "").strip()
    if not s:
        return None
    for pat in (_P_GEO, _P_OSM, _P_Q, _P_AT, _P_BARE):
        mt = pat.search(s)
        if mt:
            got = _ok(mt.group(1), mt.group(2))
            if got:
                return got
    return None


def parse(text: str):
    """(عرض، طول) اگر *همه‌ی* این پیام یک موقعیت باشد — وگرنه None.

    پذیرفته: یک خطِ تنهای نشانی، یا سرخطِ «📍 …» به‌علاوه‌ی نشانی. سرخط باید
    با 📍 شروع شود چون نامِ «موقعیت» به زبانِ *فرستنده* است و ما نمی‌دانیم
    او فارسی نوشته یا انگلیسی؛ آن ایموجی بی‌زبان است.
    """
    s = (text or "").strip()
    if not s or len(s) > 400:
        return None
    lines = [ln.strip() for ln in s.splitlines() if ln.strip()]
    if not lines or len(lines) > 2:
        return None
    if len(lines) == 2 and not lines[0].startswith("📍"):
        return None
    tail = lines[-1]
    if len(lines) == 1 and not tail.startswith(_URL_HEAD):
        # تک‌خطیِ بی‌نشانی: تنها «۳۵٫۷، ۵۱٫۳»ِ خالص پذیرفته است
        mt = _P_BARE.match(tail)
        return _ok(mt.group(1), mt.group(2)) if mt else None
    return from_url(tail)


def tile(lat: float, lon: float, zoom: int = ZOOM):
    """(نشانیِ کاشی، کسرِ افقی، کسرِ عمودیِ نقطه در آن کاشی).

    ریاضیِ «نقشه‌ی لغزان»: کاشیِ ۲۵۶ پیکسلیِ z/x/y و اینکه نقطه کجای آن
    کاشی افتاده. سنجاق را با همین دو کسر سرِ جای درستش می‌گذاریم — وگرنه
    وسطِ کاشی می‌نشیند و تا نیم‌کیلومتر دروغ می‌گوید.
    """
    la = max(-_LAT_MAX, min(_LAT_MAX, float(lat)))
    lo = max(-180.0, min(180.0, float(lon)))
    n = 2 ** int(zoom)
    x = (lo + 180.0) / 360.0 * n
    y = (1.0 - math.asinh(math.tan(math.radians(la))) / math.pi) / 2.0 * n
    xt = min(n - 1, max(0, int(x)))
    yt = min(n - 1, max(0, int(y)))
    fx = min(1.0, max(0.0, x - xt))
    fy = min(1.0, max(0.0, y - yt))
    return TILE.format(z=int(zoom), x=xt, y=yt), fx, fy


def pretty(lat: float, lon: float) -> str:
    """«35.72841, 51.38912» — همان که زیرِ کارتِ نقشه می‌آید."""
    return f"{float(lat):.5f}, {float(lon):.5f}"
