"""ویرایشگرِ سبکِ عکس — برش، چرخش، قلمِ نقاشی و قلمِ سانسور.

خواسته‌ی نسخه‌ی ۱.۵: «پیش از فرستادنِ عکس (یا گذاشتنِ آواتار/استوری) یک
ویرایشگرِ سبک با برش و چرخش، قلمِ نقاشی/حاشیه‌نویسی و قلمِ محو/سانسور باشد؛
و برای عکسِ پروفایل یک کادرِ برشِ مربعیِ ۱:۱ که بشود جابه‌جا و بزرگ/کوچکش کرد.»

⚠️ گرهِ اصلی و راهِ حلش. «پخت» (بازنویسیِ واقعیِ پیکسل‌ها) کُدک می‌خواهد و ما
روی اندروید کُدکی نداریم: Pillow روی `pypi.flet.dev` فقط چرخِ موبایل دارد و
به نسخه‌ی پایتونِ ساخت گره می‌خورد، و سپردنش به سرور یعنی ویرایش آفلاین از کار
بیفتد. راهِ حل خودِ فلاتر است: `ft.Screenshot.capture()` هر زیردرختی را به PNG
تبدیل می‌کند (`RepaintBoundary.toImage`). پس ترکیب را در *درختِ ویجت* می‌سازیم
و همان را عکس می‌گیریم — بی هیچ کُدکی، یکسان روی ویندوز/اندروید/وب، و کامل
آفلاین.

سنجیده شده (`.tmp/shot_probe.py`):
  • `capture(pixel_ratio=r)` تصویری به اندازه‌ی «منطقی × r» می‌دهد.
  • سطحی بزرگ‌تر از پنجره هم کامل گرفته می‌شود (۷۰۰ منطقی در پنجره‌ی ۴۶۰
    ⇒ PNGِ ۱۴۰۰×۱۴۰۰)، پس اندازه‌ی خروجی به اندازه‌ی صفحه بند نیست.
  • ایده‌ی برش — Stackِ کلیپ‌دار + فرزندِ جای‌دار با آفستِ منفی — در خروجی
    پیکسل‌به‌پیکسل درست درمی‌آید.

هندسه: *همه‌چیز* در «فضای نرمالِ عکسِ چرخیده» نگه داشته می‌شود (۰..۱). پس
پیش‌نمایشِ کوچک و سطحِ پختِ بزرگ یک توصیفِ مشترک دارند و خروجی مو‌به‌مو همان
است که کاربر دید.

سانسور بی کُدک: همان عکس را با رمزگشاییِ خیلی درشت‌دانه (`cache_width=BLOCKS`)
و فیلترِ `NONE` دوباره می‌کشیم ⇒ موزاییکِ واقعی. هر «داب»ِ قلم یک پنجره‌ی گردِ
کلیپ‌شده روی همان موزاییک است، پس شبکه‌ی موزاییک در کلِ عکس هم‌تراز می‌ماند و
فلاتر هم آن تصویرِ ریز را *یک‌بار* رمزگشایی می‌کند (کشِ تصویر کلیدش
`(src, cache_w, cache_h)` است) — صد داب هم ارزان است.

  edit_photo(page, src, name=…, square=False, toast=…) → dict | None
      None                          ⇒ کاربر لغو کرد
      {"changed": False}            ⇒ چیزی عوض نشد؛ همان فایلِ اصلی را بفرست
      {"changed": True, "name", "data", "path"}
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
import struct
import sys
import tempfile
import uuid

import flet as ft
import flet.canvas as cv

import ui
from i18n import t
from ui import AL, BR, EASE, FAST, P, PAD, anim, fs, op

log = logging.getLogger("rafigram.editor")

# رنگ‌های قلم. سبز نداریم (قاعده‌ی پوسته: سبز فقط برای نقطه‌ی «آنلاین»).
PEN_COLORS = ["#ffffff", "#16161c", "#ff3b30", "#ff9f0a", "#0a84ff",
              "#af52de", "#ff2d92"]
PEN_MIN, PEN_MAX = 0.004, 0.045   # پهنای قلم، نسبت به پهنای عکس
CEN_MIN, CEN_MAX = 0.05, 0.24     # پهنای قلمِ سانسور
BLOCKS = 34                       # درشتیِ موزاییکِ سانسور (بلوک در پهنا)
MIN_CROP = 0.10                   # کوچک‌ترین کادرِ برش
GRAB = 30.0                       # شعاعِ گرفتنِ دستگیره (پیکسلِ منطقی)
MAX_OUT = 1600                    # بزرگ‌ترین ضلعِ خروجی
MAX_OUT_SQ = 1024                 # ... برای آواتار (۱:۱ — جایی نمایش داده
                                  #     نمی‌شود که بیش از این بخواهد)
MAX_PTS = 260                     # سقفِ نقطه در یک خط (ضدِ سنگینی)
MAX_DABS = 140                    # سقفِ کاشیِ سانسور
BAKE_MAX = 2800                   # بزرگ‌ترین ضلعِ *سطحِ پخت*. برشِ ریزِ یک
                                  # عکسِ بزرگ وگرنه سطحی به اندازه‌ی خودِ عکس
                                  # می‌خواهد و رَمِ گوشی را می‌بلعد.


# ── فیلترهای آماده (مثل تلگرام) ─────────────────────────────────
# هر فیلتر یک «دستورِ ساختِ لایه» است: تابعی که با وضعیتِ اسلایدرها
# فهرستِ لایه‌های blend را برمی‌گرداند. چون اعمال روی GPU انجام می‌شود،
# این‌ها هم پیش‌نمایشِ آنی دارند و هم روی گوشیِ ضعیف و وب بدون‌هزینه‌اند.
#
# چرا فیلترها همه به غیرِ «original» رنگ می‌زنند و «original» خنثی است:
# چون فیلتر در تلگرام **همیشه** رنگ می‌زند. فیلترِ خنثی کاربر را گمران می‌کند
# که گزینه خراب است. (در نسخهٔ اول، «original» و «none» هر دو بی‌اثر بودند.)
def _f_none(st):
    return []


def _f_original(st):
    # «اورجینال» در تلگرام یعنی *برگرداندنِ* رنگِ خام — پس باید خنثی‌سازِ
    # هر فیلترِ رنگی باشد، نه یک لایه که خودش رنگ اضافه می‌کند.
    # ⚠️ رنگِ خاکستریِ خالص روی SOFT_LIGHT اشباع را صفر می‌کند ولی پوست را
    # سبز-خاکستری می‌کند؛ به همین دلیل COLOR با خاکستریِ خنثی بهتر جواب
    # می‌دهد (اشباع می‌ماند، رنگِ غالب به سمتِ خاکستری می‌رود).
    # ⚠️ به لغزنده‌ها دست نمی‌زنیم: خودشان لایه می‌سازند و دست زدنِ ما یعنی
    # دوبار اعمال‌شدن (آزمونِ `.tmp/filter_probe.py` گروه ۹ همین را می‌گیرد).
    return [("#808080", "COLOR", 0.35)]


def _f_vivid(st):
    # اشباعِ بالا. کنتراست را فیلتر تعیین نمی‌کند چون لغزندهٔ کنتراست
    # خودش یک لایه می‌سازد — اگر اینجا هم بسازیم، دو بار اعمال می‌شود.
    return [("#808080", "SATURATION", 0.55)]


def _f_warm(st):
    # گرمای دستی روی فیلترِ گرم **جمع** می‌شد و عکس را نارنجیِ سوخته
    # می‌کرد؛ فیلتر پایه ثابت است و لغزندهٔ گرما جداگانه روی آن می‌نشیند.
    return [("#ffb066", "SOFT_LIGHT", 0.45),
            ("#808080", "SATURATION", 0.25)]


def _f_cool(st):
    return [("#66aaff", "SOFT_LIGHT", 0.42),
            ("#808080", "SATURATION", 0.2)]


def _f_bw(st):
    return [("#808080", "SATURATION", 1.0)]


def _f_fade(st):
    # کم‌اشباع + کم‌کنتراست = حسِ نوستالژی.
    # ⚠️ «کم‌کردنِ اشباع» در BlendMode با opacity مثبت انجام می‌شود: لایه‌ی
    # خاکستریِ نرم با SOFT_LIGHT اشباع را پایین می‌آورد. عددِ منفی برای
    # opacity illegal است و `Colors.with_opacity` استثنا می‌داد.
    return [("#3a4a5a", "SOFT_LIGHT", 0.42),
            ("#6b6b6b", "SOFT_LIGHT", 0.30)]


def _f_dark(st):
    return [("#0a1020", "MULTIPLY", 0.30),
            ("#7a7a7a", "SOFT_LIGHT", 0.35)]


def _f_bright(st):
    return [("#ffffff", "SCREEN", 0.22),
            ("#808080", "SATURATION", 0.15)]


# نامِ فیلتر → (برچسبِ آیکن، تابع). ترتیبِ نمایش همین است.
FILTERS = [
    ("none",     "none",     _f_none),
    ("original", "auto",     _f_original),
    ("vivid",    "palette",  _f_vivid),
    ("warm",     "wb_sunny", _f_warm),
    ("cool",     "ac_unit",  _f_cool),
    ("bw",       "contrast", _f_bw),
    ("fade",     "looks_one", _f_fade),
    ("dark",     "dark_mode", _f_dark),
    ("bright",   "light_mode", _f_bright),
]

# نگاشتِ «BlendMode» به رشته، برای پرهیز از تایپِ مستقیم در جدول بالا
# (جدول‌ها نمی‌توانند ft.BlendMode را در سطح ماژول صدا بزنند چون هنوز
# ایمپورت نشده — پس نام‌ها را نگه می‌داریم و اینجا به واقعی تبدیل می‌کنیم.)
_BM = {
    "SCREEN": "SCREEN", "MULTIPLY": "MULTIPLY", "OVERLAY": "OVERLAY",
    "SOFT_LIGHT": "SOFT_LIGHT", "SATURATION": "SATURATION", "COLOR": "COLOR",
}


def _bind_filters():
    """رنگ‌های جدول را به کلاس‌هایِ واقعیِ فلات گره می‌زند (یک‌بار، هنگامِ
    ایمپورتِ ماژول)."""
    global FILTER_RECIPES
    import flet as _ft
    made = {}
    for key, _label, fn in FILTERS:
        def _wrap(f=fn, _bm=_BM):
            def _inner(st):
                out = []
                for item in f(st):
                    if not item:
                        continue
                    color, mode, op = item
                    bm = getattr(_ft.BlendMode, _bm.get(str(mode), str(mode)),
                                 None)
                    if bm is None:
                        continue
                    out.append((color, bm, op))
                return out
            return _inner
        made[key] = _wrap()
    FILTER_RECIPES = made


FILTER_RECIPES = {}
try:
    _bind_filters()
except Exception:                                   # pragma: no cover
    FILTER_RECIPES = {"none": _f_none}


def _flat_tint(st):
    """فیلتر، اما «تخت» و قابلِ نشاندن روی خودِ عکس.

    برای سانسور لازم است: هر پنجره یک تکه از عکسِ بزرگ است و اگر فیلتر
    بیرون از آن بنشیند، رنگِ داخلِ پنجره‌ها با بیرون فرق می‌کند و کاربر
    می‌بیند فیلتر روی سانسور کار نمی‌کند. پس چند blendِ ساده را **همه با
    اشباعِ صفر** می‌کنیم تا رنگِ اصلیِ هر فیلتر (مثلاً نارنجیِ warm) گم
    نشود ولی تفاوت‌های ریزِ روشنایی و کنتراست بماند.
    """
    key = st.get("fil")
    if key in (None, "none"):
        return []
    try:
        import flet as _ft
    except Exception:                                # pragma: no cover
        return []
    out = []
    for color, _mode, o in FILTER_RECIPES.get(key, _f_none)(st):
        try:
            h = color.lstrip("#")
            if len(h) == 3:
                h = "".join(ch * 2 for ch in h)
            r, g, b = (int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
        except (ValueError, IndexError):
            continue
        lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
        v = int(max(0.0, min(1.0, lum)) * 255)
        grey = "#%02x%02x%02x" % (v, v, v)
        try:
            out.append((grey, _ft.BlendMode.SATURATION,
                        max(0.0, min(1.0, o))))
        except Exception:                            # pragma: no cover
            continue
    return out


# ── اندازه‌ی عکس، فقط از سرصفحه‌اش ───────────────────
def _head(src, n=1 << 19):
    """چند بایتِ نخستِ فایل (یا خودِ داده)."""
    if isinstance(src, (bytes, bytearray)):
        return bytes(src[:n])
    try:
        with open(src, "rb") as f:
            return f.read(n)
    except OSError:
        return b""


def _webp(raw):
    """اندازه‌ی WebP — سه گونه‌اش سه جای متفاوت دارند."""
    tag = raw[12:16]
    if tag == b"VP8X" and len(raw) >= 30:
        return (int.from_bytes(raw[24:27], "little") + 1,
                int.from_bytes(raw[27:30], "little") + 1)
    if tag == b"VP8L" and len(raw) >= 25:
        b = int.from_bytes(raw[21:25], "little")
        return ((b & 0x3FFF) + 1, ((b >> 14) & 0x3FFF) + 1)
    if tag == b"VP8 " and len(raw) >= 30 and raw[23:26] == b"\x9d\x01\x2a":
        return (struct.unpack("<H", raw[26:28])[0] & 0x3FFF,
                struct.unpack("<H", raw[28:30])[0] & 0x3FFF)
    return None


def probe_size(src) -> tuple[int, int] | None:
    """اندازه‌ی پیکسلیِ عکس، فقط از سرصفحه — بی هیچ کُدکی.

    چرا لازم است: تا نسبتِ ابعاد را بدانیم. قابِ پیش‌نمایش را دقیقاً به همان
    نسبت می‌سازیم، پس نگاشتِ «انگشت ⇒ پیکسلِ عکس» بی خطاست و کادرِ برش همان
    جایی می‌افتد که کاربر می‌بیند. JPEG/PNG/GIF/WebP/BMP همه‌ی چیزی است که
    یک گوشی یا نماگرفتِ رومیزی می‌سازد.
    """
    raw = _head(src)
    if len(raw) < 26:
        return None
    try:
        if raw[:2] == b"\xff\xd8":                       # JPEG
            i, n = 2, len(raw)
            while i + 9 < n:
                if raw[i] != 0xFF:
                    i += 1
                    continue
                m = raw[i + 1]
                if m == 0xFF:
                    i += 1
                    continue
                if m in (0xD8, 0x01) or 0xD0 <= m <= 0xD7:
                    i += 2
                    continue
                seg = struct.unpack(">H", raw[i + 2:i + 4])[0]
                # SOF0..SOF15 جز DHT/JPG/DAC — همان‌جا ارتفاع و پهنا هست
                if 0xC0 <= m <= 0xCF and m not in (0xC4, 0xC8, 0xCC):
                    h, w = struct.unpack(">HH", raw[i + 5:i + 9])
                    return (w, h) if w and h else None
                if seg < 2:
                    return None
                i += 2 + seg
            return None
        if raw[:8] == b"\x89PNG\r\n\x1a\n" and raw[12:16] == b"IHDR":
            w, h = struct.unpack(">II", raw[16:24])
            return (w, h) if w and h else None
        if raw[:6] in (b"GIF87a", b"GIF89a"):
            w, h = struct.unpack("<HH", raw[6:10])
            return (w, h) if w and h else None
        if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
            return _webp(raw)
        if raw[:2] == b"BM":
            w, h = struct.unpack("<ii", raw[18:26])
            return (abs(w), abs(h)) if w and h else None
    except Exception as ex:
        log.info("سرصفحه‌ی عکس خوانده نشد: %s", ex)
    return None


# ── هندسه‌ی نرمال ───────────────────────────────────
def _clamp(v, lo=0.0, hi=1.0):
    return lo if v < lo else (hi if v > hi else v)


def _xy(pos):
    """(x, y) از رویدادِ اشاره — هر شکلی که فلت داد."""
    if pos is None:
        return 0.0, 0.0
    x = getattr(pos, "x", None)
    if x is None:
        if isinstance(pos, (tuple, list)) and len(pos) >= 2:
            return float(pos[0]), float(pos[1])
        return 0.0, 0.0
    return float(x), float(getattr(pos, "y", 0.0) or 0.0)


def _rot_pt(p):
    """نقطه‌ی نرمال، ۹۰ درجه ساعتگرد: (x, y) → (1-y, x)."""
    return (1.0 - p[1], p[0])


def _rot_rect(r):
    """کادرِ نرمال (x, y, w, h)، ۹۰ درجه ساعتگرد."""
    x, y, w, h = r
    return [1.0 - y - h, x, h, w]


def _jpeg(raw, quality=88):
    """PNGِ خام → JPEG، *اگر* Pillow دستِ‌مان باشد.

    چرا مهم است: خروجیِ `capture()` همیشه PNG است و PNGِ یک عکسِ ۱۶۰۰ پیکسلی
    چند مگابایت می‌شود — روی دیتای موبایل حیف است. روی ویندوز (نسخه‌ی exe)
    Pillow از دلِ venv می‌آید و همان تصویر به چند صد کیلوبایت می‌رسد. روی
    اندروید و وب Pillow نیست، پس PNG می‌رود: بزرگ‌تر، ولی درست و بی‌افت.
    هیچ‌جا خطا نمی‌دهیم — نبودنِ Pillow یک حالتِ عادی است، نه خرابی.
    """
    try:
        import io

        from PIL import Image
        im = Image.open(io.BytesIO(raw))
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=quality, optimize=True,
                progressive=True)
        out = buf.getvalue()
        return (out, "jpg") if out and len(out) < len(raw) else (raw, "png")
    except Exception:
        return raw, "png"


async def edit_photo(page, src, *, name="photo.jpg", square=False, toast=None):
    """ویرایشگرِ تمام‌صفحه. تا کاربر «تأیید» یا «لغو» نزند برنمی‌گردد."""
    def _say(msg, ok=False):
        if callable(toast):
            try:
                toast(msg, ok)
            except Exception:
                pass

    size = probe_size(src)
    if not size:
        # نسبتِ ابعاد را نمی‌دانیم ⇒ نگاشتِ انگشت به پیکسل حدسی می‌شود و
        # برش غلط درمی‌آید. دروغ نمی‌گوییم: همان عکسِ اصلی می‌رود.
        _say(t("edit_unsupported"))
        return {"changed": False}
    sw, sh = size

    st = {
        "rot": 0,                    # ۰/۹۰/۱۸۰/۲۷۰
        "tool": "crop",              # crop | pen | censor | filter
        "pen_w": 0.014, "cen_w": 0.11, "color": PEN_COLORS[0],
        "pen": [], "cen": [],        # خط‌های نقاشی و سانسور
        "seq": [],                   # ترتیبِ کارها، برای «واگرد»
        "crop": [0.0, 0.0, 1.0, 1.0],
        "grab": None, "p0": (0.0, 0.0), "c0": None,
        "busy": False,
        # ── تنظیماتِ رنگ (ادیت کامل مثلِ تلگرام) ──────────────────────
        "fil": "none",               # نامِ فیلترِ آماده
        "bright": 0.0,               # ‎-1..+1
        "contrast": 0.0,             # ‎-1..+1
        "satur": 0.0,                # ‎-1..+1  (اشباع)
        "warm": 0.0,                 # ‎-1..+1  (گرمی/سردی)
    }

    def _rsize():
        """اندازه‌ی پیکسلیِ عکس *پس از* چرخش."""
        return (sh, sw) if st["rot"] % 180 else (sw, sh)

    # قابِ پیش‌نمایش: هرچه از صفحه پس از نوارِ بالا و جعبه‌ابزار می‌ماند
    box_w = max(180.0, ui.vw() - 28.0)
    box_h = max(200.0, ui.vh() - 250.0)

    def _disp():
        """اندازه‌ی نمایشِ عکسِ چرخیده در قاب (CONTAIN)."""
        rw, rh = _rsize()
        k = min(box_w / rw, box_h / rh)
        return rw * k, rh * k

    def _square_crop():
        """بزرگ‌ترین مربعِ وسط — کادرِ آغازینِ آواتار."""
        rw, rh = _rsize()
        s = float(min(rw, rh))
        w, h = s / rw, s / rh
        return [(1.0 - w) / 2.0, (1.0 - h) / 2.0, w, h]

    if square:
        st["crop"] = _square_crop()

    # ── لایه‌های ترکیب (مشترکِ پیش‌نمایش و پخت) ──────
    def _img_layer(dw, dh, *, blocks=0, left=None, top=None, exact=False,
                   mono=False):
        """عکسِ چرخیده، دقیقاً به اندازه‌ی قابِ dw×dh.

        چرخش در فلاتر *فقط رنگ‌آمیزی* است و چیدمان را عوض نمی‌کند؛ پس عکس را
        به اندازه‌ی «نچرخیده» می‌سازیم (برای ۹۰ درجه، جابه‌جا) و دور مرکز
        می‌چرخانیمش. چون در Stackِ dw×dh وسط‌چین است، بی هیچ حسابِ آفستی
        درست جا می‌افتد.

        `blocks` ⇒ نسخه‌ی موزاییکیِ سانسور. `exact` ⇒ سقفِ رمزگشایی برابرِ
        همان اندازه (سطحِ پخت؛ بیشترش فقط حافظه می‌خورد). `mono` ⇒ فیلتر
        را روی خودِ عکس می‌نشاند تا وقتی عکس *کلیپ* شده (سانسور) هم رنگی
        بماند — لایه‌های blendِ بیرونِ کلیپ دیده نمی‌شوند.
        """
        rot = st["rot"]
        iw, ih = (dw, dh) if rot % 180 == 0 else (dh, dw)
        if blocks:
            cw = max(4, int(blocks))
            ch = max(4, int(round(blocks * ih / max(1.0, iw))))
            im = ft.Image(src=src, width=iw, height=ih, fit=ft.BoxFit.FILL,
                          cache_width=cw, cache_height=ch,
                          filter_quality=ft.FilterQuality.NONE,
                          gapless_playback=True)
        else:
            m = 1.0 if exact else 2.0
            im = ui.photo(src, width=iw, height=ih, fit=ft.BoxFit.FILL,
                          cap_w=int(round(iw * m)), cap_h=int(round(ih * m)),
                          quality=ft.FilterQuality.HIGH)
        if rot % 360:
            im.rotate = ft.Rotate(math.radians(rot))
        inner = [im]
        if mono:
            for color, mode, o in _flat_tint(st):
                c = ft.Container(width=iw, height=ih)
                try:
                    c.blend_mode = mode
                except Exception:
                    continue
                c.bgcolor = ft.Colors.with_opacity(max(0.0, min(1.0, o)), color)
                inner.append(c)
        stk = ft.Stack(inner, width=dw, height=dh, alignment=AL.CENTER)
        if left is not None:
            stk.left, stk.top = left, top
        return stk

    def _pen_shapes(dw, dh):
        """خط‌های قلم → شکل‌های بوم."""
        out = []
        for s in st["pen"]:
            pts, pw = s["pts"], max(1.0, s["w"] * dw)
            fill = ft.Paint(color=s["c"], style=ft.PaintingStyle.FILL,
                            anti_alias=True)
            if len(pts) < 2:
                out.append(cv.Circle(pts[0][0] * dw, pts[0][1] * dh,
                                     pw / 2.0, paint=fill))
                continue
            paint = ft.Paint(color=s["c"], stroke_width=pw, anti_alias=True,
                             style=ft.PaintingStyle.STROKE,
                             stroke_cap=ft.StrokeCap.ROUND,
                             stroke_join=ft.StrokeJoin.ROUND)
            el = [cv.Path.MoveTo(pts[0][0] * dw, pts[0][1] * dh)]
            # نرم‌کردنِ خط: هر نقطه نقطه‌ی کنترل است و وسطِ دو نقطه، مقصد.
            for i in range(1, len(pts) - 1):
                el.append(cv.Path.QuadraticTo(
                    pts[i][0] * dw, pts[i][1] * dh,
                    (pts[i][0] + pts[i + 1][0]) / 2.0 * dw,
                    (pts[i][1] + pts[i + 1][1]) / 2.0 * dh))
            el.append(cv.Path.LineTo(pts[-1][0] * dw, pts[-1][1] * dh))
            out.append(cv.Path(el, paint=paint))
        return out

    def _cen_tiles(dw, dh):
        """قلمِ سانسور: پنجره‌های گردِ کلیپ‌شده روی نسخه‌ی موزاییکیِ همان عکس.

        چون همه‌ی پنجره‌ها *یک* تصویرِ ریز را نشان می‌دهند و هرکدام به اندازه‌ی
        جای خودش جابه‌جا شده، شبکه‌ی موزاییک در کلِ عکس هم‌تراز می‌ماند —
        درست مثلِ سانسورِ تلگرام، و بی هیچ کُدکی.
        """
        tiles, budget = [], MAX_DABS
        for s in st["cen"]:
            r = max(5.0, s["w"] * dw / 2.0)
            for (nx, ny) in s["pts"]:
                if budget <= 0:
                    return tiles
                budget -= 1
                tx, ty = nx * dw - r, ny * dh - r
                tiles.append(ft.Container(
                    ft.Stack([_img_layer(dw, dh, blocks=BLOCKS,
                                         left=-tx, top=-ty, mono=True)],
                             width=2 * r, height=2 * r),
                    left=tx, top=ty, width=2 * r, height=2 * r,
                    bgcolor=op(0.0), border_radius=BR(r, r, r, r),
                    clip_behavior=ft.ClipBehavior.ANTI_ALIAS))
        return tiles

    # ── رنگ: فیلترهای آماده + لغزنده‌های دستی ───────────────────────
    # چرا لایه‌ی blend و نه پردازشِ پیکسل در پایتون: فلاتر این‌ها را روی GPU
    # رندر می‌کند، پس (۱) روی گوشیِ ضعیف و روی وب هم فوری است و (۲) خروجی
    # دقیقاً همان چیزی است که کاربر در پیش‌نمایش می‌بیند — چون «پخت» یعنی
    # اسکرین‌شات گرفتن از همین درخت. اگر با PIL فیلتر می‌زدیم، آن‌وقت باید
    # دو بار پیکسل پیمایش می‌شد و پیش‌نمایش با خروجی فرق می‌کرد.
    def _layer(color, mode, opacity=1.0, dw=None, dh=None):
        dw = dw if dw is not None else box_w
        dh = dh if dh is not None else box_h
        c = ft.Container(width=dw, height=dh)
        try:
            c.blend_mode = mode
        except Exception:
            return None
        c.bgcolor = ft.Colors.with_opacity(max(0.0, min(1.0, opacity)), color)
        return c

    def _adjust_layers(dw, dh):
        out = []
        # فیلترِ آماده (مثل تلگرام): هرکدام یک ترکیبِ blend
        f = st["fil"]
        if f in FILTER_RECIPES:
            for color, mode, op in FILTER_RECIPES[f](st):
                L = _layer(color, mode, op, dw, dh)
                if L is not None:
                    out.append(L)
        # لغزنده‌های دستی
        b, c, s, w = (st["bright"], st["contrast"], st["satur"], st["warm"])
        if b > 0.02:                       # روشن‌تر
            L = _layer("#ffffff", ft.BlendMode.SCREEN, b * 0.55, dw, dh)
            if L is not None:
                out.append(L)
        elif b < -0.02:                    # تاریک‌تر
            L = _layer("#000000", ft.BlendMode.MULTIPLY, -b * 0.6, dw, dh)
            if L is not None:
                out.append(L)
        if c > 0.02:                       # کنتراستِ بالا
            L = _layer("#808080", ft.BlendMode.OVERLAY, c * 0.5, dw, dh)
            if L is not None:
                out.append(L)
        elif c < -0.02:
            L = _layer("#808080", ft.BlendMode.SOFT_LIGHT, -c * 0.6, dw, dh)
            if L is not None:
                out.append(L)
        if abs(s) > 0.02:                  # اشباع
            mode = ft.BlendMode.SATURATION if s > 0 else ft.BlendMode.COLOR
            L = _layer("#808080", mode, abs(s) * 0.75, dw, dh)
            if L is not None:
                out.append(L)
        if abs(w) > 0.02:                  # گرمی/سردی
            col = "#ff9d4d" if w > 0 else "#4da3ff"
            L = _layer(col, ft.BlendMode.SOFT_LIGHT, abs(w) * 0.5, dw, dh)
            if L is not None:
                out.append(L)
        return out

    def _layers(dw, dh, *, exact=False):
        """عکس + سانسور + قلم — همان چیزی که پخته می‌شود."""
        base = _img_layer(dw, dh, exact=exact)
        return [base, *_adjust_layers(dw, dh),
                *_cen_tiles(dw, dh),
                cv.Canvas(_pen_shapes(dw, dh), width=dw, height=dh)]

    # ── کادرِ برش ────────────────────────────────────
    def _corners(dw, dh):
        x, y, w, h = st["crop"]
        return [(x * dw, y * dh), ((x + w) * dw, y * dh),
                (x * dw, (y + h) * dh), ((x + w) * dw, (y + h) * dh)]

    def _handle(cx, cy, i):
        """دستگیره‌ی گوشه — دو ضلعِ سفیدِ ضخیم، مثلِ برشِ تلگرام."""
        s, th = 22.0, 3.0
        side = ft.BorderSide(th, "#ffffff")
        kw = {"top" if i < 2 else "bottom": side,
              "left" if i % 2 == 0 else "right": side}
        return ft.Container(
            left=cx - (0 if i % 2 == 0 else s), top=cy - (0 if i < 2 else s),
            width=s, height=s, border=ft.Border(**kw))

    def _crop_overlay(dw, dh):
        """بیرونِ کادر تاریک، خودِ کادر با خطِ سفید و شبکه‌ی یک‌سومی."""
        x, y, w, h = st["crop"]
        L, T, Wd, Hd = x * dw, y * dh, w * dw, h * dh
        dim = op(0.58, "#000000")
        thin = op(0.34, "#ffffff")
        out = [
            ft.Container(left=0, top=0, width=dw, height=T, bgcolor=dim),
            ft.Container(left=0, top=T + Hd, width=dw,
                         height=max(0.0, dh - T - Hd), bgcolor=dim),
            ft.Container(left=0, top=T, width=L, height=Hd, bgcolor=dim),
            ft.Container(left=L + Wd, top=T, width=max(0.0, dw - L - Wd),
                         height=Hd, bgcolor=dim),
            ft.Container(left=L, top=T, width=Wd, height=Hd,
                         border=ft.Border(*[ft.BorderSide(1.4, "#ffffff")] * 4),
                         border_radius=BR(3, 3, 3, 3)),
        ]
        for i in (1, 2):                       # شبکه‌ی یک‌سومی
            out.append(ft.Container(left=L + Wd * i / 3.0, top=T, width=1.0,
                                    height=Hd, bgcolor=thin))
            out.append(ft.Container(left=L, top=T + Hd * i / 3.0, width=Wd,
                                    height=1.0, bgcolor=thin))
        if square:
            # آواتار گرد نشان داده می‌شود؛ راهنمای دایره می‌گوید چه دیده می‌شود
            out.append(ft.Container(
                left=L, top=T, width=Wd, height=Hd,
                border=ft.Border(*[ft.BorderSide(1.2, op(0.5, "#ffffff"))] * 4),
                border_radius=BR(Wd / 2, Wd / 2, Wd / 2, Wd / 2)))
        for i, (cx, cy) in enumerate(_corners(dw, dh)):
            out.append(_handle(cx, cy, i))
        return out

    # ── ظرف‌های زنده (برای تازه‌کردنِ نقطه‌ای، بی بازسازیِ همه) ──
    holder = {"cen": None, "pen": None, "crop": None, "dw": 1.0, "dh": 1.0}
    frame = ft.Container(width=box_w, height=box_h, alignment=AL.CENTER)

    def _touch(k):
        c = holder.get(k)
        if c is None:
            return
        try:
            c.update()
        except Exception:
            pass

    def _refresh_cen():
        c = holder["cen"]
        if c is not None:
            c.controls = _cen_tiles(holder["dw"], holder["dh"])
            _touch("cen")

    def _refresh_pen():
        c = holder["pen"]
        if c is not None:
            c.shapes = _pen_shapes(holder["dw"], holder["dh"])
            _touch("pen")

    def _refresh_crop():
        c = holder["crop"]
        if c is not None:
            c.controls = (_crop_overlay(holder["dw"], holder["dh"])
                          if st["tool"] == "crop" else [])
            _touch("crop")

    # ── انگشت ────────────────────────────────────────
    # چرا یک `GestureDetector` برای هر سه ابزار: فلاتر رویدادِ کشیدن را به
    # *یک* بازشناس می‌دهد؛ چند آشکارسازِ روی‌هم‌افتاده سرِ همان کشیدن دعوا
    # می‌کنند و نتیجه‌اش پرش و از‌دست‌رفتنِ نقطه‌هاست. پس یکی می‌گذاریم و
    # خودمان بر اساسِ ابزارِ فعال تقسیم می‌کنیم.
    ANCH = {0: (-1, -1), 1: (1, -1), 2: (-1, 1), 3: (1, 1)}

    def _norm(e):
        x, y = _xy(getattr(e, "local_position", None))
        dw, dh = holder["dw"], holder["dh"]
        return x, y, _clamp(x / dw), _clamp(y / dh)

    def _pick_grab(x, y):
        """نزدیک‌ترین دستگیره، وگرنه «جابه‌جایی» اگر داخلِ کادر باشیم."""
        dw, dh = holder["dw"], holder["dh"]
        best, bd = None, GRAB * GRAB
        for i, (cx, cy) in enumerate(_corners(dw, dh)):
            d = (x - cx) ** 2 + (y - cy) ** 2
            if d < bd:
                best, bd = i, d
        if best is not None:
            return best
        cx0, cy0, cw, ch = st["crop"]
        if cx0 * dw <= x <= (cx0 + cw) * dw \
                and cy0 * dh <= y <= (cy0 + ch) * dh:
            return "move"
        return None

    def _drag_crop(x, y):
        """کادر را جابه‌جا یا بزرگ/کوچک می‌کند (۱:۱ اگر آواتار باشد)."""
        g = st["grab"]
        if g is None or st["c0"] is None:
            return
        dw, dh = holder["dw"], holder["dh"]
        x0, y0, w0, h0 = st["c0"]
        dx = (x - st["p0"][0]) / dw
        dy = (y - st["p0"][1]) / dh
        if g == "move":
            st["crop"] = [_clamp(x0 + dx, 0.0, 1.0 - w0),
                          _clamp(y0 + dy, 0.0, 1.0 - h0), w0, h0]
            _refresh_crop()
            return
        sx, sy = ANCH[g]
        ax = (x0 + w0) if sx < 0 else x0          # گوشه‌ی *ثابت*
        ay = (y0 + h0) if sy < 0 else y0
        lim_w = (1.0 - ax) if sx > 0 else ax
        lim_h = (1.0 - ay) if sy > 0 else ay
        w = w0 + (dx if sx > 0 else -dx)
        if square:
            # ۱:۱ یعنی برابری در *پیکسل*، نه در فضای نرمال
            q = dw / dh
            w = max(MIN_CROP, min(w, lim_w, lim_h / q))
            h = w * q
        else:
            h = h0 + (dy if sy > 0 else -dy)
            w = max(MIN_CROP, min(w, lim_w))
            h = max(MIN_CROP, min(h, lim_h))
        st["crop"] = [ax if sx > 0 else ax - w, ay if sy > 0 else ay - h, w, h]
        _refresh_crop()

    def _add_pt(nx, ny, first=False):
        """نقطه‌ی تازه‌ی خط — با رقیق‌سازی، تا خط سبک بماند."""
        arr = st["pen"] if st["tool"] == "pen" else st["cen"]
        if first:
            arr.append({"c": st["color"],
                        "w": st["pen_w"] if st["tool"] == "pen"
                        else st["cen_w"],
                        "pts": [(nx, ny)]})
            st["seq"].append(st["tool"])
            return True
        if not arr:
            return False
        s = arr[-1]
        if len(s["pts"]) >= MAX_PTS:
            return False
        dw, dh = holder["dw"], holder["dh"]
        lx, ly = s["pts"][-1]
        far = ((nx - lx) * dw) ** 2 + ((ny - ly) * dh) ** 2
        step = 3.0 if st["tool"] == "pen" else max(6.0, s["w"] * dw * 0.40)
        if far < step * step:
            return False
        s["pts"].append((nx, ny))
        return True

    def _pan_start(e):
        x, y, nx, ny = _norm(e)
        if st["tool"] == "crop":
            st["grab"] = _pick_grab(x, y)
            st["p0"], st["c0"] = (x, y), list(st["crop"])
            return
        _add_pt(nx, ny, first=True)
        (_refresh_pen if st["tool"] == "pen" else _refresh_cen)()

    def _pan_update(e):
        x, y, nx, ny = _norm(e)
        if st["tool"] == "crop":
            _drag_crop(x, y)
        elif _add_pt(nx, ny):
            (_refresh_pen if st["tool"] == "pen" else _refresh_cen)()

    def _pan_end(e=None):
        st["grab"], st["c0"] = None, None

    def _undo(e=None):
        if not st["seq"]:
            return
        k = st["seq"].pop()
        arr = st["pen"] if k == "pen" else st["cen"]
        if arr:
            arr.pop()
        (_refresh_pen if k == "pen" else _refresh_cen)()

    def _refresh_frame():
        """پیش‌نمایش را تازه می‌کند (پس از تغییرِ فیلتر/اسلایدر).

        کلِ قاب دوباره ساخته می‌شود، چون لایه‌های blend در `Stack` بالایِ عکس
        می‌نشینند و باید دوباره در چیدمان بنشینند. این ارزان است: تعدادشان
        کم است و هیچ پردازشِ پیکسلی در پایتون انجام نمی‌شود.
        """
        _build()
        try:
            frame.update()
        except Exception:
            pass

    def _rotate(e=None):
        """۹۰ درجه ساعتگرد — کادر و خط‌ها هم با عکس می‌چرخند."""
        st["rot"] = (st["rot"] + 90) % 360
        st["crop"] = _rot_rect(st["crop"])
        for arr in (st["pen"], st["cen"]):
            for s in arr:
                s["pts"] = [_rot_pt(p) for p in s["pts"]]
        _build()
        try:
            frame.update()
        except Exception:
            pass

    def _build():
        """قابِ پیش‌نمایش را از نو می‌سازد (پس از چرخش یا در آغاز)."""
        dw, dh = _disp()
        holder["dw"], holder["dh"] = dw, dh
        cen = ft.Stack(_cen_tiles(dw, dh), width=dw, height=dh)
        pen = cv.Canvas(_pen_shapes(dw, dh), width=dw, height=dh)
        crp = ft.Stack(_crop_overlay(dw, dh) if st["tool"] == "crop" else [],
                       width=dw, height=dh)
        holder["cen"], holder["pen"], holder["crop"] = cen, pen, crp
        gd = ft.GestureDetector(
            content=ft.Container(width=dw, height=dh, bgcolor=op(0.0)),
            width=dw, height=dh, drag_interval=16,
            on_pan_start=_pan_start, on_pan_update=_pan_update,
            on_pan_end=_pan_end)
        frame.content = ft.Stack(
            [_img_layer(dw, dh), *_adjust_layers(dw, dh), cen, pen, crp, gd],
            width=dw, height=dh,
            clip_behavior=ft.ClipBehavior.HARD_EDGE)

    # ── نوارِ ابزار ─────────────────────────────────
    bar = ft.Container(animate=anim(FAST, EASE))

    def _chip(key, icon, label, on_click, action=False):
        """دکمه‌ی ابزار: آیکون + برچسبِ ریز، با تهِ‌رنگ وقتی فعال است."""
        on = (not action) and st["tool"] == key
        col = "#ffffff" if on else op(0.62, "#ffffff")
        return ft.Container(
            ft.Column([ui.vic(icon, 20, col),
                       ft.Text(label, size=fs(10), color=col,
                               font_family=ui.FONT_MED if on else ui.FONT)],
                      spacing=3, tight=True,
                      alignment=ft.MainAxisAlignment.CENTER,
                      horizontal_alignment=ft.CrossAxisAlignment.CENTER),
            width=62, height=54, alignment=AL.CENTER, ink=True,
            border_radius=BR(15, 15, 15, 15), on_click=on_click,
            bgcolor=op(0.14, "#ffffff") if on else op(0.0),
            animate=anim(FAST, EASE))

    def _set_tool(k):
        def _h(e=None):
            if st["tool"] == k:
                return
            st["tool"] = k
            _bar()
            # کادرِ برش فقط در حالتِ «برش» معنا دارد؛ با عوض‌شدنِ ابزار باید
            # در پیش‌نمایش هم برداشته شود وگرنه دستگیره‌ها روی عکس می‌مانند
            # و فیلتر را جابه‌جا نمی‌کنند.
            _refresh_frame()
            try:
                bar.update()
            except Exception:
                pass
        return _h

    def _width_row():
        """پهنای قلم — روی خطِ *بعدی* اثر می‌گذارد، نه خط‌های کشیده‌شده."""
        pen = st["tool"] == "pen"
        lo, hi = (PEN_MIN, PEN_MAX) if pen else (CEN_MIN, CEN_MAX)
        cur = st["pen_w"] if pen else st["cen_w"]

        def _ch(e):
            try:
                v = lo + (hi - lo) * _clamp(float(e.control.value))
            except (TypeError, ValueError):
                return
            st["pen_w" if pen else "cen_w"] = v

        return ft.Container(
            ft.Row([ui.vic("pencil" if pen else "censor", 15,
                           op(0.45, "#ffffff")),
                    ft.Container(ft.Slider(
                        value=(cur - lo) / (hi - lo), min=0.0, max=1.0,
                        on_change=_ch, thumb_color="#ffffff",
                        active_color=st["color"] if pen else op(0.8, "#ffffff"),
                        inactive_color=op(0.15, "#ffffff")), expand=True)],
                   spacing=4, tight=True),
            padding=PAD(16, 0, 16, 0), height=32)

    def _set_color(c):
        def _h(e=None):
            st["color"] = c
            _bar()
            try:
                bar.update()
            except Exception:
                pass
        return _h

    def _color_row():
        """رنگِ قلم. سبز نداریم — سبز در این پوسته فقط نشانِ «آنلاین» است."""
        out = []
        for c in PEN_COLORS:
            on = c == st["color"]
            dot = ft.Container(
                width=22, height=22, bgcolor=c,
                border_radius=BR(11, 11, 11, 11),
                border=ft.Border(*[ft.BorderSide(
                    2.0, op(0.92 if on else 0.2, "#ffffff"))] * 4))
            out.append(ft.Container(
                dot, width=30, height=30, alignment=AL.CENTER, ink=True,
                border_radius=BR(15, 15, 15, 15), on_click=_set_color(c),
                scale=1.14 if on else 1.0, animate_scale=anim(FAST, EASE)))
        return ft.Container(
            ft.Row(out, spacing=1, tight=True,
                   alignment=ft.MainAxisAlignment.CENTER),
            padding=PAD(8, 2, 8, 0))

    def _filter_row():
        """ردیفِ فیلترهای آماده + لغزنده‌های روشنایی/کنتراست/اشباع/گرمی."""
        thumbs = []
        for key, icon, _fn in FILTERS:
            on = st["fil"] == key

            def _h(e=None, k=key):
                st["fil"] = k
                st["seq"].append(("fil", k))
                _refresh_frame()
                _bar()
                try:
                    bar.update()
                except Exception:
                    pass
            thumbs.append(ft.Container(
                ui.vic(icon, 19, "#ffffff" if on else op(0.6, "#ffffff")),
                width=40, height=40, alignment=AL.CENTER, ink=True,
                border_radius=BR(20, 20, 20, 20),
                border=ft.Border.all(2, P["acc"]) if on else None,
                bgcolor=op(0.16, "#ffffff") if on else op(0.06, "#ffffff"),
                animate=anim(FAST, EASE), tooltip=t("filters"),
                on_click=_h))
        rows = [ft.Container(
            ft.Row(thumbs, spacing=4, tight=True, scroll=ft.ScrollMode.AUTO,
                   alignment=ft.MainAxisAlignment.CENTER),
            padding=PAD(6, 4, 6, 0))]

        def _slider(key, icon, label):
            def _ch(e):
                try:
                    st[key] = (_clamp(float(e.control.value)) * 2.0) - 1.0
                except (TypeError, ValueError, KeyError):
                    return
                _refresh_frame()
            return ft.Container(
                ft.Row([
                    ui.vic(icon, 15, op(0.7, "#ffffff")),
                    ft.Slider(min=0.0, max=1.0,
                              value=(_clamp((st[key] + 1.0) / 2.0)) * 1.0,
                              expand=True, active_color=P["acc"],
                              inactive_color=op(0.14, "#ffffff"),
                              on_change=_ch),
                    ft.Text(label, size=fs(9.5), color=op(0.6, "#ffffff"),
                            font_family=ui.FONT_MED, width=44,
                            text_align=ft.TextAlign.END),
                ], spacing=6, tight=True, vertical_alignment=
                ft.CrossAxisAlignment.CENTER),
                padding=PAD(2, 10, 2, 0))

        rows.append(_slider("bright", "light_mode", t("brightness")))
        rows.append(_slider("contrast", "contrast", t("contrast")))
        rows.append(_slider("satur", "palette", t("saturation")))
        rows.append(_slider("warm", "thermostat", t("warmth")))
        return ft.Column(rows, spacing=1, tight=True)

    def _bar():
        """نوارِ پایین را می‌چیند: ردیفِ رنگ/پهنا (اگر لازم بود) + ابزارها."""
        rows = []
        if st["tool"] == "filter":
            rows.append(_filter_row())
        if st["tool"] == "pen":
            rows.append(_color_row())
        if st["tool"] in ("pen", "cen"):
            rows.append(_width_row())
        rows.append(ft.Row(
            [_chip("crop", "crop", t("crop"), _set_tool("crop")),
             _chip("rot", "rotate", t("rotate"), _rotate, action=True),
             _chip("pen", "pencil", t("draw"), _set_tool("pen")),
             _chip("cen", "censor", t("censor"), _set_tool("cen")),
             _chip("filter", "palette", t("filters"),
                   _set_tool("filter"))],
            spacing=2, tight=True, alignment=ft.MainAxisAlignment.CENTER))
        bar.content = ft.Column(
            rows, spacing=0, tight=True,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER)

    # ── نوارِ بالا ───────────────────────────────────
    top = ft.Container(
        ft.Row([ui.glass_circle("close", lambda e: _finish(None), box=40,
                                size=18, tip=t("cancel")),
                ft.Container(ft.Text(t("edit_photo"), size=fs(15),
                                     color="#ffffff",
                                     font_family=ui.FONT_MED),
                             expand=True, alignment=AL.CENTER),
                ui.glass_circle("undo", _undo, box=40, size=18,
                                tip=t("undo")),
                ui.spacer(w=8),
                ui.glass_circle("check", lambda e: page.run_task(_apply),
                                box=40, size=18, color=P["acc"],
                                tip=t("edit_done"))],
               spacing=0, tight=True,
               vertical_alignment=ft.CrossAxisAlignment.CENTER),
        padding=PAD(10, 8, 10, 4))

    def _dirty():
        """چیزی عوض شده؟ (برای آواتار همیشه می‌پخیم، چون ۱:۱ لازم است)"""
        if square or st["rot"] or st["pen"] or st["cen"]:
            return True
        # فیلترها هم «عوض‌کردنی» حساب می‌شوند، وگرنه با انتخابِ یک فیلتر و
        # زدنِ «تأیید» بی‌صدا دور انداخته می‌شدند.
        if st["fil"] not in (None, "none"):
            return True
        if any(abs(st[k]) > 0.005 for k in ("bright", "contrast", "satur",
                                            "warm")):
            return True
        return [round(v, 4) for v in st["crop"]] != [0.0, 0.0, 1.0, 1.0]

    async def _apply(e=None):
        if st["busy"]:
            return
        st["busy"] = True
        try:
            if not _dirty():
                _finish({"changed": False})
                return
            res = await _bake()
            _finish(res if res else {"changed": False})
        except Exception:                                   # pragma: no cover
            log.exception("bake failed")
            _say(t("edit_failed"))
            _finish({"changed": False})
        finally:
            st["busy"] = False

    # ── پخت ─────────────────────────────────────────
    # خودِ فلاتر همان درختی را که می‌بینید رَستر می‌کند، پس نه کُدکِ پایتونی
    # لازم است و نه رفت‌وبرگشت با سرور. سطحِ پخت به اندازه‌ی *پیکسلِ خروجی*
    # ساخته می‌شود و لایه‌ها با آفستِ منفی جای برش می‌افتند. ساختارش مو‌به‌مو
    # همانِ آزمونِ `.tmp/shot_probe.py` است (ظرفِ بی‌اندازه‌ی left/top).
    stage = ft.Container(left=0, top=0)

    async def _bake():
        rw, rh = _rsize()
        cx, cy, cw, ch = st["crop"]
        cap = MAX_OUT_SQ if square else MAX_OUT
        ow, oh = cw * rw, ch * rh
        k = min(1.0, cap / max(1.0, max(ow, oh)),
                BAKE_MAX / max(1.0, max(rw, rh)))
        ow = max(16, int(round(ow * k)))
        oh = ow if square else max(16, int(round(oh * k)))
        # fw = ow/cw = rw*k ⇒ سطحِ پخت هرگز از خودِ عکس بزرگ‌تر نمی‌شود
        fw, fh = ow / max(1e-6, cw), oh / max(1e-6, ch)
        shot = ft.Screenshot(content=ft.Stack(
            [ft.Stack(_layers(fw, fh, exact=True), width=fw, height=fh,
                      left=-cx * fw, top=-cy * fh)],
            width=float(ow), height=float(oh),
            clip_behavior=ft.ClipBehavior.HARD_EDGE))
        stage.content = shot
        raw = None
        try:
            page.update()
            await asyncio.sleep(0.12)
            raw = await shot.capture(pixel_ratio=1.0, delay=280)
        finally:
            stage.content = None
            try:
                page.update()
            except Exception:
                pass
        if not raw:
            _say(t("edit_failed"))
            return None
        data, ext = _jpeg(raw)
        base = os.path.splitext(os.path.basename(str(name) or "photo"))[0]
        fn = f"{base or 'photo'}_edit.{ext}"
        log.info("baked %dx%d → %d bytes (%s)", ow, oh, len(data), ext)
        return {"changed": True, "name": fn, "data": data,
                "path": _spill(data, fn)}

    def _spill(data, fn):
        """کپیِ موقت روی دیسک، فقط برای پیش‌نمایشِ ارزانِ فراخواننده.

        روی وب (Pyodide) پرونده‌سیستم مجازی است و فلاتر آن را نمی‌بیند، پس
        `None` می‌دهیم و فراخواننده خودِ بایت‌ها را نشان می‌دهد.
        """
        if sys.platform == "emscripten":
            return None
        try:
            d = os.path.join(tempfile.gettempdir(), "rafigram_edit")
            os.makedirs(d, exist_ok=True)
            p = os.path.join(d, f"{uuid.uuid4().hex[:8]}_{fn}")
            with open(p, "wb") as f:
                f.write(data)
            return p
        except Exception:
            log.debug("spill failed", exc_info=True)
            return None

    def _finish(res):
        try:
            if root in page.overlay:
                page.overlay.remove(root)
            page.update()
        except Exception:                                   # pragma: no cover
            log.debug("unmount failed", exc_info=True)
        if not fut.done():
            fut.set_result(res)

    # ── سوارکردن ─────────────────────────────────────
    # `stage` نخستین فرزند است تا *رنگ‌آمیزی بشود* ولی زیرِ پرده‌ی پُر بماند.
    # با `opacity=0` فلاتر از رنگ‌آمیزی‌اش می‌گذرد و عکسِ خالی درمی‌آید.
    root = ft.Stack(
        [stage,
         ft.Container(bgcolor="#07070a", left=0, top=0, right=0, bottom=0),
         ft.Container(ui.safe(ft.Column(
             [top, ft.Container(frame, expand=True, alignment=AL.CENTER), bar],
             spacing=0, expand=True,
             horizontal_alignment=ft.CrossAxisAlignment.CENTER)),
             left=0, top=0, right=0, bottom=0)],
        left=0, top=0, right=0, bottom=0,
        clip_behavior=ft.ClipBehavior.HARD_EDGE)

    fut = asyncio.get_running_loop().create_future()
    _bar()
    _build()
    page.overlay.append(root)
    page.update()
    return await fut
