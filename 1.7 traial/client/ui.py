"""
Rafigram — سیستم طراحی «شیشه‌ی مایع» تلگرامی.

اینجا فقط توکن‌های ظاهری و ویجت‌های پایه‌ست؛ منطق برنامه توی main.py می‌مونه.
"""
import asyncio
import hashlib

import flet as ft

import i18n

# پخش‌کننده‌ی ویدیو (برای گیف‌های تلگرامی = ویدیوی بی‌صدا و تکرارشو).
# اگر بسته نبود، همه‌جا به کارتِ ساکن برمی‌گردیم و هیچ خطایی بالا نمی‌آد.
try:
    import flet_video as _fv
    HAS_VIDEO = True
except Exception:                                   # pragma: no cover
    _fv, HAS_VIDEO = None, False

# ── میان‌بُرها ──────────────────────────────────────
PAD, BR, AL, MG = ft.Padding, ft.BorderRadius, ft.Alignment, ft.Margin
OFF, SCL = ft.Offset, ft.Scale
ANIM, CURVE, W = ft.Animation, ft.AnimationCurve, ft.FontWeight

# ── قلم‌ها (فایل‌های ttf در main.py ثبت می‌شن) ──────
FONT = "Vazir"
FONT_MED = "VazirMedium"
FONT_BOLD = "VazirBold"

# ── اندازه‌ی متن (قابل تنظیم از بخش ظاهر) ──────────
_scale = 1.0


def set_font_scale(value) -> None:
    global _scale
    try:
        _scale = max(0.85, min(1.35, float(value)))
    except (TypeError, ValueError):
        _scale = 1.0


def font_scale() -> float:
    return _scale


def is_rtl() -> bool:
    """جهت راست‌به‌چپ (فارسی)."""
    return bool(i18n.rtl())


# ── شدتِ «شیشه‌ای» بودنِ منوها و نوارها (بخشِ ظاهرِ تنظیمات) ──
# ۰ = تختِ کامل (بی‌تاری، پس‌زمینه‌ی پُر)، ۱ = شیشه‌ی پیش‌فرض،
# ۱٫۶ = شیشه‌ی خیلی مات‌وشفاف.
_glass = 1.0


def set_glass(value) -> None:
    global _glass
    try:
        _glass = max(0.0, min(1.6, float(value)))
    except (TypeError, ValueError):
        _glass = 1.0


def glass_level() -> float:
    return _glass


# ── بارِ گرافیکیِ «تاری» روی گوشی ────────────────────
# هر `ft.Blur` یک BackdropFilter است: کلِ چیزی که پشتش است دوباره رندر و
# تار می‌شود. روی رومیزی ارزان است، ولی روی GPUی گوشی هر کدام هزینه دارد و
# چند دهها تای همزمان (یکی به‌ازای هر حبابِ پیام) اسکرول را لنگ می‌کند.
# پس روی گوشی دو قاعده:
#   • تاریِ «تکرارشونده» (چیزی که به‌ازای هر ردیفِ لیست ساخته می‌شود) حذف
#     می‌شود — این کنترل‌ها پس‌زمینه‌ی تقریباً کدر دارند، پس تفاوتِ دیداری
#     تقریباً صفر است ولی جهش (jank) از بین می‌رود،
#   • بقیه‌ی تاری‌ها سقف می‌خورند تا شعاعِ تاری از توانِ گوشی بیرون نزند.
PHONE_BLUR_CAP = 12.0

# تاریِ زنده داخلِ لیست‌های اسکرول‌شو همیشه صاف (flat) می‌شود — روی هر
# دستگاهی. هر ردیفِ تارِ یک لیست یعنی یک BackdropFilter که در هر فریمِ
# اسکرول کلِ پشتِ خودش را از نو رندر می‌کند؛ هزینه‌اش خطی با تعدادِ ردیفِ
# دیده‌شده بالا می‌رود و همان لنگیِ اندروید را می‌سازد.
FLAT_LISTS = True


def blur_of(sigma, heavy=False, tile=ft.BlurTileMode.MIRROR):
    """`ft.Blur` با شدتِ تنظیماتِ کاربر — یا None وقتی نباید تار شود.

    `heavy=True` یعنی این تاری روی گوشی گران است: یا به‌ازای هر ردیفِ یک
    لیستِ اسکرول‌شو ساخته می‌شود (حبابِ پیام، کپسولِ تاریخ)، یا به‌اندازه‌ی
    تمامِ صفحه است (لکه‌های نورِ پس‌زمینه). روی گوشی همین‌ها حذف می‌شوند و
    با `FLAT_LISTS` روی رومیزی هم.
    """
    try:
        s = float(sigma) * _glass
    except (TypeError, ValueError):
        return None
    if s <= 0.5:
        return None
    if heavy and FLAT_LISTS:
        return None
    if _PHONE:
        if heavy:
            return None
        s = min(s, PHONE_BLUR_CAP)
    return ft.Blur(s, s, tile)


def fs(size) -> int:
    """اندازه‌ی متن با ضریبِ تنظیماتِ کاربر."""
    return max(8, int(round(size * _scale)))


# ── فاصله‌گذاریِ تلگرام دسکتاپ ──────────────────────
# اندازه‌ها از خودِ تلگرامِ اندروید گرفته شده‌اند: آواتارِ ۵۴ در ردیفِ ۷۶.
# پیش‌تر ۴۸/۶۸ بود که ریزتر از تلگرام می‌نمود — خواسته‌ی «آواتارها بزرگ‌تر و
# باکیفیت‌تر». تیزی‌اش هم از `photo()` می‌آید که آواتار را با سقفِ ۳ برابر
# رمزگشایی می‌کند.
AV = 54            # قطر آواتار در لیست چت‌ها
AV_STORY = 56      # قطر آواتار در نوارِ استوری‌های بالای صفحه
ROW_H = 76         # ارتفاع ردیفِ چت
ROW_PAD = 12       # پدینگ افقیِ درونِ ردیف
ROW_VPAD = 7       # پدینگ عمودیِ ردیف (۷۶ − ۶۲ جای حلقه ⇒ ۷ هر طرف)
LIST_PAD = 8       # حاشیه‌ی افقیِ لیست

# ── جای حلقه‌ی استوری ───────────────────────────────
# ⚠️ باگی که کاربر دید: «با گذاشتن یا دیدنِ استوری، آواتار کوچک/کج می‌شود».
# علتش این بود که `story_ring(name, AV)` عددِ `AV` را اندازه‌ی *کلِ حلقه*
# می‌گرفت و آواتار را `AV − 8` می‌کشید؛ پس ردیفی که استوری داشت آواتارِ ۴۰
# نشان می‌داد و ردیفِ بی‌استوری ۴۸ — یعنی همان جهشِ ۱۷ درصدی.
#
# درستش کارِ خودِ تلگرام است: حلقه *بیرونِ* آواتار کشیده می‌شود و آواتار هیچ
# وقت کوچک نمی‌شود. پس از نسخه‌ی ۱.۵ عددِ `size` در `story_ring` = قطرِ خودِ
# آواتار، و جای کلی `size + 2*RING_PAD` است. برای اینکه ردیفِ با/بی استوری
# هم‌قد بمانند، لیست *همیشه* جای کاملِ حلقه را می‌گیرد (`slot()`), چه حلقه‌ای
# باشد چه نه.
RING_PAD = 4       # ۲ ضخامتِ حلقه + ۲ فاصله تا آواتار


def slot(size=None) -> int:
    """جای کلی‌ای که آواتار + حلقه‌ی استوری می‌گیرد."""
    return int((AV if size is None else size) + 2 * RING_PAD)

# ── دستگاه: گوشی یا رومیزی ──────────────────────────
# روی گوشی نباید هیچ چیزی زیرِ نوارِ وضعیت/نوارِ حرکتی بیفتد و پنل‌های
# پهنا-ثابت (کارتِ ورود، دیالوگ‌ها) نباید از عرضِ صفحه بزنند بیرون.
_PHONE = False
_VW, _VH = 460.0, 860.0     # اندازه‌ی صفحه‌ی نمایش/پنجره


def set_device(page=None, phone=None, w=None, h=None) -> bool:
    """پلتفرم و اندازه‌ی صفحه را از `page` می‌خواند (یا دستی ست می‌شود).

    روی وب، `page.platform` همیشه WEB است — حتی وقتی صفحه در جیبِ کاربر
    است و همان GPUی ضعیفِ گوشی را دارد. پس عرضِ صفحه هم قضاوت می‌شود:
    وبِ زیرِ ~۵۶۰ پیکسل = گوشی ⇒ سقفِ تاری و حذفِ BackdropFilterهای
    گران مثلِ APK اجرا می‌شود. این همان بارِ «لنگیِ وبِ موبایل» بود.
    """
    global _PHONE, _VW, _VH
    if phone is not None:
        _PHONE = bool(phone)
    elif page is not None:
        try:
            _PHONE = page.platform in (ft.PagePlatform.ANDROID,
                                       ft.PagePlatform.IOS)
        except Exception:
            _PHONE = False
        if not _PHONE:
            try:
                _PHONE = (
                    page.platform == ft.PagePlatform.WEB_BROWSER
                    and isinstance(page.width, (int, float))
                    and page.width > 0 and page.width < 560)
            except Exception:
                pass
    # عرض و ارتفاع جدا سنجیده می‌شوند: `page.width` روی چند فریمِ اولِ اجرا
    # None (یا صفر) است و نباید اندازه‌ی درستِ قبلی را خراب کند. پیش‌تر این‌جا
    # `globals()[name] = …` بود که همان کار را می‌کرد ولی ابزارهای بازبینیِ کد
    # آن را «مقداردهی» نمی‌دیدند و `global` را بی‌مصرف گزارش می‌کردند.
    size = []
    for cur, val, lo, attr in ((_VW, w, 240.0, "width"),
                               (_VH, h, 320.0, "height")):
        if val is None and page is not None:
            val = getattr(page, attr, None)
        size.append(float(val) if isinstance(val, (int, float)) and val >= lo
                    else cur)
    _VW, _VH = size
    return _PHONE


def is_phone() -> bool:
    return _PHONE


def vw() -> float:
    """پهنای صفحه‌ی نمایش (روی رومیزی: پهنای پنجره)."""
    return _VW


def vh() -> float:
    return _VH


def panel_w(want, gap=28.0):
    """پهنای پنلِ ثابت، چفت‌شده به عرضِ صفحه تا هرگز سرریز نشود."""
    return max(220.0, min(float(want), _VW - gap))


def body_w(want):
    """پهنای محتوایِ داخلِ دیالوگ (پدینگِ شیشه + حاشیه‌ی دیالوگ کم شده)."""
    return panel_w(want, 96.0)


def safe(child, top=True, bottom=True, left=True, right=True, **kw):
    """بدنه را از نوارِ وضعیت، ناچ و نوارِ حرکتیِ گوشی دور نگه می‌دارد.

    روی رومیزی این حاشیه‌ها صفرند، پس چیدمان عوض نمی‌شود.
    """
    kw.setdefault("expand", True)
    return ft.SafeArea(child,
                       avoid_intrusions_left=left, avoid_intrusions_top=top,
                       avoid_intrusions_right=right,
                       avoid_intrusions_bottom=bottom,
                       maintain_bottom_view_padding=False,
                       minimum_padding=PAD(0, 0, 0, 0), **kw)

# ── زمان‌بندی (منحنی‌های تلگرام: تند شروع، نرم تموم) ──
# زمان‌بندیِ پویا و فنری تلگرام: تند، نرم و پر از فیدبک زنده
FAST, MED, SLOW = 160, 260, 360
_ANIM_LEVEL = "full"

def set_anim_level(level: str) -> None:
    """تنظیم سرعت انیمیشن‌ها (کامل، کاهش‌یافته، خاموش) مطابق تنظیمات تلگرام."""
    global _ANIM_LEVEL, FAST, MED, SLOW
    lvl = str(level or "full").lower()
    _ANIM_LEVEL = lvl
    if lvl == "off":
        FAST, MED, SLOW = 0, 0, 0
    elif lvl == "reduced":
        FAST, MED, SLOW = 80, 140, 200
    else:
        FAST, MED, SLOW = 160, 260, 360

def anim_level() -> str:
    return _ANIM_LEVEL

EASE = CURVE.EASE_OUT_CUBIC       # منحنی حرکت طبیعی تلگرام
SOFT = CURVE.EASE_IN_OUT_CUBIC    # رفت‌وبرگشتِ نرم (نوارها و پرده‌ها)
GLIDE = CURVE.EASE_OUT_CUBIC      # ورودِ روان ردیف‌ها و حباب‌ها
POP = CURVE.EASE_OUT_BACK         # پرش و فیدبک فنریِ زنده در دکمه‌ها و تب‌ها
BOUNCE = CURVE.EASE_OUT_BACK      # انیمیشن فنری پاپ‌آپ‌ها و دکمه‌ها


def anim(ms=MED, curve=EASE):
    return ANIM(ms, curve)


def op(alpha, color=None):
    """شفافیت روی رنگ؛ بدون رنگ، «پرده»ی خودِ پوسته (روشن/تاریک) رو می‌گیره."""
    return ft.Colors.with_opacity(alpha, color or P.get("veil", "#ffffff"))


# ── پالت‌ها ────────────────────────────────────────
# veil = رنگِ لایه‌های شیشه‌ای (در پوسته‌ی روشن تیره‌ست، در تاریک سفید)
THEMES = {
    "telegram": {
        "name": "تلگرام مدرن",
        "bg0": "#0e0e10", "bg1": "#18181a", "bg2": "#1c1c1e",
        "acc": "#24A1DE", "acc2": "#2AABEE",
        "nav_active": "#24A1DE", "nav_badge": "#E53935",
        "sent": "#24A1DE", "recv": "#1c1c1e",
        "txt": "#ffffff", "sub": "#8e8e93", "dim": "#636366",
        "line": "#2c2c2e", "glow1": "#24A1DE", "glow2": "#1c3a5e",
        "ok": "#5cc85c", "veil": "#ffffff", "light": False,
    },
    "crimson": {
        "name": "مشکی–قرمز",
        "bg0": "#0e0e10", "bg1": "#18181a", "bg2": "#1c1c1e",
        "acc": "#e53935", "acc2": "#ff5252",
        "nav_active": "#e53935", "nav_badge": "#E53935",
        "sent": "#7d1f22", "recv": "#1c1c26",
        "txt": "#f7f7fa", "sub": "#8e8e93", "dim": "#636366",
        "line": "#2c2c2e", "glow1": "#e53935", "glow2": "#3d0c0c",
        "ok": "#5cc85c", "veil": "#ffffff", "light": False,
    },
    "light": {
        "name": "روشنِ آبی",
        "bg0": "#eff4f9", "bg1": "#ffffff", "bg2": "#e7eef6",
        "acc": "#2f8ce8", "acc2": "#1a7ad9",
        "nav_active": "#2f8ce8", "nav_badge": "#E53935",
        "sent": "#d5ebff", "recv": "#ffffff",
        "txt": "#0f1b26", "sub": "#587082", "dim": "#7d92a3",
        "line": "#dbe4ed", "glow1": "#bcdcfb", "glow2": "#dfeaf6",
        "ok": "#2f9e4f", "veil": "#0d2233", "light": True,
    },
}

# پوسته‌ی ساختنی (رنگ‌های دلخواهِ کاربر) — پایه‌اش تاریکه و روی
# همون سه‌تا رنگِ اصلی (لهجه، حبابِ پیام، ته‌رنگِ پس‌زمینه) بازنویسی می‌شه.
CUSTOM = "custom"
CUSTOM_KEYS = ("c_base", "c_acc", "c_sent", "c_bg")

# ── مدل‌های آماده‌ی پوسته (گالریِ سازنده‌ی تم) ─────────
# هر مدل = پایه + سه رنگِ اصلی. سبز عمداً بین لهجه‌ها نیست
# (سبز فقط برای نقطه‌ی «آنلاین» و پیامِ موفقیت می‌مونه).
PRESETS = [
    {"key": "p_telegram", "base": "telegram",
     "acc": "#3390ec", "sent": "#2b5278", "bg": "#17212b"},
    {"key": "p_crimson", "base": "crimson",
     "acc": "#e53935", "sent": "#7d1f22", "bg": "#14141c"},
    {"key": "p_violet", "base": "crimson",
     "acc": "#9c5cf6", "sent": "#4a2a7a", "bg": "#191325"},
    {"key": "p_indigo", "base": "telegram",
     "acc": "#5c6bc0", "sent": "#33407e", "bg": "#151a2e"},
    {"key": "p_amber", "base": "crimson",
     "acc": "#f0a020", "sent": "#7a5316", "bg": "#1c1710"},
    {"key": "p_rose", "base": "crimson",
     "acc": "#f0568c", "sent": "#7a2a48", "bg": "#1e1218"},
    {"key": "p_cyan", "base": "telegram",
     "acc": "#22b8cf", "sent": "#155c66", "bg": "#0f1c22"},
    {"key": "p_slate", "base": "crimson",
     "acc": "#8c9cb0", "sent": "#37424f", "bg": "#15181c"},
    {"key": "p_day", "base": "light",
     "acc": "#2f8ce8", "sent": "#d5ebff", "bg": "#e6edf5"},
    {"key": "p_paper", "base": "light",
     "acc": "#c06a26", "sent": "#f6e2cd", "bg": "#f1eae0"},
]


def preset(key):
    """مدلِ آماده با این کلید (یا None)."""
    for p in PRESETS:
        if p["key"] == key:
            return p
    return None


def _hex(v, fallback):
    """`#rrggbb` معتبر یا مقدار پیش‌فرض."""
    s = str(v or "").strip()
    if not s.startswith("#"):
        s = "#" + s
    if len(s) == 4:  # #abc → #aabbcc
        s = "#" + "".join(ch * 2 for ch in s[1:])
    if len(s) != 7:
        return fallback
    try:
        int(s[1:], 16)
    except ValueError:
        return fallback
    return s.lower()


def _mix(c1, c2, t):
    """آمیختنِ دو رنگِ hex با نسبتِ t."""
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{int(a[i] + (b[i] - a[i]) * t):02x}" for i in range(3))


def build_custom(base="crimson", acc=None, sent=None, bg=None):
    """پالتِ دلخواه رو از یک پوسته‌ی پایه می‌سازه و در THEMES می‌ذاره."""
    base = base if base in ("telegram", "crimson", "light") else "crimson"
    pal = dict(THEMES[base])
    pal["name"] = "دلخواه"
    a = _hex(acc, pal["acc"])
    pal["acc"] = a
    pal["nav_active"] = a
    pal["acc2"] = _mix(a, "#ffffff" if not pal["light"] else "#000000", 0.22)
    pal["glow1"] = a
    if sent:
        pal["sent"] = _hex(sent, pal["sent"])
    if bg:
        b = _hex(bg, pal["bg0"])
        if pal["light"]:
            pal["bg0"] = _mix(b, "#ffffff", 0.35)
            pal["bg1"] = _mix(b, "#ffffff", 0.72)
            pal["bg2"] = _mix(b, "#ffffff", 0.55)
            pal["recv"] = _mix(b, "#ffffff", 0.85)
        else:
            pal["bg0"] = _mix(b, "#000000", 0.35)
            pal["bg1"] = _mix(b, "#000000", 0.15)
            pal["bg2"] = b
            pal["recv"] = _mix(b, "#000000", 0.05)
        pal["glow2"] = b
    THEMES[CUSTOM] = pal
    return pal


_name = "telegram"
P = dict(THEMES[_name])


def use_theme(name):
    """پالت فعال رو عوض می‌کنه (in-place تا همه‌ی ارجاع‌ها به P تازه بمونن)."""
    global _name
    if name not in THEMES:
        name = "telegram"
    _name = name
    P.clear()
    P.update(THEMES[name])


def theme_name():
    return _name


def is_light() -> bool:
    return bool(P.get("light"))


def dark_twin(name=None) -> str:
    """جفتِ تاریکِ پوسته‌ی فعلی (برای کلیدِ خورشید/ماه)."""
    n = name or _name
    return "telegram" if n in ("light", CUSTOM) else n


def overlay_style():
    """نوارِ وضعیت و نوارِ حرکتیِ گوشی را کاملاً شفاف می‌کند.

    نتیجه: برنامه لبه‌تا‌لبه (full screen) پخش می‌شود و پس‌زمینه‌ی درخشان
    از زیرِ ساعت/باتری و نوارِ پایین هم دیده می‌شود. رنگِ آیکون‌های سیستم
    برعکسِ روشناییِ پوسته انتخاب می‌شود تا همیشه خوانا باشند، و
    `enforce_*_contrast=False` جلوی سایه‌ی خاکستریِ خودکارِ اندروید را
    می‌گیرد (وگرنه یک نوارِ نیمه‌تاریک بالای صفحه می‌ماند و برنامه
    تمام‌صفحه به نظر نمی‌رسد).
    """
    light = is_light()
    return ft.SystemOverlayStyle(
        status_bar_color=ft.Colors.TRANSPARENT,
        system_navigation_bar_color=ft.Colors.TRANSPARENT,
        system_navigation_bar_divider_color=ft.Colors.TRANSPARENT,
        enforce_system_status_bar_contrast=False,
        enforce_system_navigation_bar_contrast=False,
        status_bar_brightness=ft.Brightness.LIGHT if light else ft.Brightness.DARK,
        status_bar_icon_brightness=(ft.Brightness.DARK if light
                                    else ft.Brightness.LIGHT),
        system_navigation_bar_icon_brightness=(ft.Brightness.DARK if light
                                               else ft.Brightness.LIGHT),
    )


def flet_theme():
    """تم متریال هم‌رنگ پالت، تا ویجت‌های آماده (دیالوگ، رادیو، ...) جا بیفتن."""
    return ft.Theme(
        font_family=FONT,
        color_scheme=ft.ColorScheme(
            primary=P["acc"], on_primary="#ffffff",
            secondary=P["acc2"], surface=P["bg1"], on_surface=P["txt"],
            error=P["acc2"], outline=P["line"],
        ),
        system_overlay_style=overlay_style(),
    )


# ── آیکون‌های برداری (SVG داخل assets/icons) ────────
def vic(name, size=22, color=None, opacity=None):
    """آیکونِ SVG با رنگِ دلخواه. `name` اسم فایل بدون پسوند."""
    img = ft.Image(
        src=f"icons/{name}.svg", width=size, height=size,
        color=color or P["sub"], color_blend_mode=ft.BlendMode.SRC_IN,
        fit=ft.BoxFit.CONTAIN,
    )
    if opacity is not None:
        img.opacity = opacity
    return img


def _icon(icon, size, color):
    """هم اسمِ SVG رو قبول می‌کنه، هم آیکونِ متریال."""
    if isinstance(icon, str) and not icon.startswith("#"):
        return vic(icon, size=size, color=color)
    return ft.Icon(icon, size=size, color=color)


# ── تصویر با سقفِ رمزگشایی — بنیادِ روانیِ رابط و ضدِ کرشِ حافظه ─────────
#
# ⚠️ مهم‌ترین درسِ نسخه‌ی ۱.۵. `ft.Image` بی `cache_width`/`cache_height` عکس را
# در حافظه به *اندازه‌ی اصلیِ خودش* باز می‌کند، نه به اندازه‌ای که نشان می‌دهد.
# یک عکسِ ۱۲ مگاپیکسلیِ دوربین چهار بایت برای هر پیکسل می‌خواهد ⇒ حدودِ ۵۰
# مگابایت رَم برای کاشیِ ۹۴ پیکسلی. پانزده کاشی در برگه‌ی سنجاق ⇒ نزدیکِ یک
# گیگابایت، و اندروید بی هیچ استثنایی پروسه را می‌کشد. این دقیقاً همان
# «چهار عکس نشان داد و برنامه کامل بست» بود؛ استثنایی هم به پایتون نمی‌رسد
# که بگیریمش، چون مرگ در لایه‌ی نیتیو است.
#
# با سقف، فلاتر همان‌جا در رمزگشا (`ResizeImage`) کوچکش می‌کند: رَم به اندازه‌ی
# نمایش می‌ماند، رمزگشایی سریع‌تر است و پرش/لگِ اسکرول هم می‌رود.
#
# ضریب `DPR` برای تیزی است: روی صفحه‌ی چگالِ گوشی یک پیکسلِ منطقی چند پیکسلِ
# فیزیکی است، پس دو برابرِ اندازه‌ی نمایش رمزگشایی می‌کنیم — تیز، ولی همچنان
# صدها برابر کوچک‌تر از اندازه‌ی اصلی.
DPR = 2.0
CAP_MAX = 2048          # سقفِ مطلق؛ بزرگ‌تر از این روی هیچ صفحه‌ای دیده نمی‌شود
CAP_FULL = 1600         # سقفِ عکسِ تمام‌صفحه (اندازه‌ی نمایش را نمی‌دانیم)


def _cap(v, mult=DPR):
    """اندازه‌ی نمایش → سقفِ رمزگشایی. None اگر اندازه‌ای نداریم."""
    try:
        v = float(v or 0)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    return max(16, min(CAP_MAX, int(round(v * mult))))


def photo(src, width=None, height=None, fit=None, radius=0, *,
          mult=DPR, cap_w=None, cap_h=None, quality=None, error=None,
          full=False, **kw):
    """`ft.Image` با سقفِ رمزگشایی — تنها راهِ ساختِ تصویر در این برنامه.

    هر `ft.Image` تازه‌ای باید از این‌جا رد شود، وگرنه همان کرشِ حافظه برمی‌گردد.
    `full=True` برای نمایشگرِ تمام‌صفحه است که اندازه‌ی نمایشش را از پیش
    نمی‌دانیم: سقفِ ثابتِ `CAP_FULL` می‌گیرد.
    """
    cw = cap_w if cap_w is not None else _cap(width, mult)
    ch = cap_h if cap_h is not None else _cap(height, mult)
    if full and cw is None and ch is None:
        cw = CAP_FULL
    if cw is None and ch is None:       # نه اندازه، نه سقف ⇒ دستِ‌کم یک سقف
        cw = CAP_FULL
    if quality is None:
        # وقتی خیلی کوچک می‌کنیم، فیلترِ پیش‌فرض (LOW) پله‌پله می‌شود.
        quality = ft.FilterQuality.MEDIUM
    img = ft.Image(
        src=src, width=width, height=height, fit=fit or ft.BoxFit.COVER,
        cache_width=cw, cache_height=ch, filter_quality=quality,
        # جابه‌جاییِ `src` (مثلاً عوض‌شدنِ آواتار) بی پرشِ سفید
        gapless_playback=True, anti_alias=bool(radius),
        **kw)
    if radius:
        img.border_radius = BR(radius, radius, radius, radius)
    if error is not None:
        img.error_content = error
    return img


# ── پس‌زمینه‌ی درخشان (لازمه‌ی واقعی‌بودن افکت شیشه) ──
def _blob(color, size, left=None, top=None, right=None, bottom=None, alpha=0.30):
    """لکه‌ی نوریِ پس‌زمینه.

    روی رومیزی یک تاریِ ۷۰ لبه‌اش را آب می‌کند. روی گوشی این تاری حذف
    می‌شود: سه BackdropFilter به بزرگیِ صفحه پشتِ هر صفحه، تنها منبعِ
    لنگیِ اسکرول بود — و چون خودِ گرادیانِ شعاعی از مرکز به شفافِ کامل
    محو می‌شود، تفاوتِ دیداری‌اش به چشم نمی‌آید.
    """
    return ft.Container(
        width=size, height=size, left=left, top=top, right=right, bottom=bottom,
        border_radius=BR(size, size, size, size),
        gradient=ft.RadialGradient(colors=[op(alpha, color), op(0.0, color)]),
        blur=(None if _PHONE else ft.Blur(70, 70, ft.BlurTileMode.DECAL)),
        ignore_interactions=True,
    )


def backdrop():
    """چند لکه‌ی نوریِ محو که شیشه‌ها روش شکل می‌گیرن."""
    k = 0.5 if is_light() else 1.0        # در پوسته‌ی روشن ملایم‌تر
    return [
        _blob(P["glow1"], 520, left=-160, top=-180, alpha=0.34 * k),
        _blob(P["acc"], 360, right=-120, top=180, alpha=0.16 * k),
        _blob(P["glow2"], 620, left=-80, bottom=-260, alpha=0.40 * k),
    ]


def screen(page, body):
    """صفحه = پس‌زمینه‌ی درخشان + بدنه، همه داخل یک Stack.

    پس‌زمینه لبه‌تا‌لبه می‌ماند (مثلِ تلگرام) ولی بدنه داخلِ ناحیه‌ی امنِ
    گوشی قرار می‌گیرد تا سرصفحه زیرِ نوارِ وضعیت و نوار پایین زیرِ نوارِ
    حرکتی نرود.
    """
    page.bgcolor = P["bg0"]
    return ft.Stack([*backdrop(), safe(body)], expand=True)


# ── شیشه ───────────────────────────────────────────
def glass(child, radius=22, pad=18, tint=0.055, sigma=24, edge=0.10,
          shadow=True, heavy=False, in_list=False, **kw):
    """پنل شیشه‌ای: تار کردن پشت + لایه‌ی نور + لبه‌ی روشن + سایه‌ی عمق.

    در پوسته‌ی روشن شیشه «شیریِ سفید» می‌شه (نه خاکستریِ کدر) و سایه‌اش
    آبیِ ملایمه؛ در تاریک همان شیشه‌ی دودیِ قبلی.

    `heavy=True` برای پنل‌هایی‌ست که داخلِ یک لیستِ اسکرول‌شو تکرار می‌شن؛
    روی گوشی تاریشان حذف می‌شه (رنگِ پُرکننده جایش را می‌گیرد).

    `in_list=True` صریح‌تر است: این پنل داخلِ یک لیستِ اسکرول‌شو است، پس
    وقتی `FLAT_LISTS` روشن باشد تاری‌اش قطعاً حذف می‌شود — چه گوشی، چه
    رومیزی. اسکرولِ روان از شیشه‌ی زنده مهم‌تر است.
    """
    if isinstance(radius, (int, float)):
        radius = BR(radius, radius, radius, radius)
    if isinstance(pad, (int, float)):
        pad = PAD(pad, pad, pad, pad)
    heavy = heavy or (in_list and FLAT_LISTS)
    lite = is_light()
    # شدتِ شیشه از تنظیمات، دو سرِ لغزنده دو حالتِ کاملاً متفاوت‌اند:
    #   g = 0    ⇒ solid = 1  ⇒ پنلِ کاملاً کدر (بی‌تاری، بی‌درخشش)
    #   g = 1    ⇒ همان شیشه‌ی متعادلِ همیشگی
    #   g = 1.6  ⇒ clear = 1  ⇒ پُرکننده حذف می‌شه؛ فقط تاری + لبه‌ی نور
    g = _glass
    bl = blur_of(sigma, heavy=heavy)
    solid = max(0.0, 1.0 - min(1.0, g))    # چقدر باید «کدر» بشه
    if bl is None and g > 0.02:
        # روی گوشی تاری حذف شد؛ اگر پُرکننده هم شفاف بماند متن ناخوانا
        # می‌شود. پس همان‌قدر که تاری کم شد، رنگِ پُرکننده کدرتر می‌شود —
        # ظاهرِ «شیشه‌ی مات» می‌ماند، بارِ GPU نمی‌ماند.
        solid = max(solid, 0.65)
    clear = max(0.0, min(1.0, (g - 1.0) / 0.6))   # چقدر باید «شفاف» بشه
    sheen = (1.0 - 0.85 * clear) * (1.0 - 0.8 * solid)   # درخششِ سطح
    if lite:
        bg = kw.pop("bgcolor", ft.Colors.with_opacity(
            min(1.0, (0.62 + tint * 2) * (1.0 - clear) + solid * 0.38),
            "#ffffff"))
        grad = ft.LinearGradient(
            begin=AL.TOP_LEFT, end=AL.BOTTOM_RIGHT,
            colors=[ft.Colors.with_opacity(0.55 * sheen, "#ffffff"),
                    ft.Colors.with_opacity(0.30 * sheen, "#ffffff"),
                    ft.Colors.with_opacity(0.48 * sheen, "#ffffff")],
            stops=[0.0, 0.55, 1.0])
        edge_c = op(0.07 * (1.0 + 1.4 * clear) * (1.0 - 0.45 * solid))
        shade = ft.BoxShadow(blur_radius=26 * (0.55 + 0.45 * g),
                             spread_radius=-8, color=op(0.13), offset=OFF(0, 10))
    else:
        bg = kw.pop("bgcolor",
                    op(min(1.0, tint + solid * 0.95), P["bg1"])
                    if solid > 0.02 else op(tint * (1.0 - clear)))
        grad = ft.LinearGradient(
            begin=AL.TOP_LEFT, end=AL.BOTTOM_RIGHT,
            colors=[op(0.09 * sheen), op(0.02 * sheen), op(0.06 * sheen)],
            stops=[0.0, 0.55, 1.0])
        edge_c = op(edge * (1.0 + 1.4 * clear) * (1.0 - 0.45 * solid))
        shade = ft.BoxShadow(blur_radius=34 * (0.55 + 0.45 * g),
                             spread_radius=-6, color=op(0.55, "#000000"),
                             offset=OFF(0, 12))
    return ft.Container(
        content=child,
        padding=pad,
        border_radius=radius,
        blur=bl,
        bgcolor=bg,
        gradient=grad,
        border=ft.Border(*[ft.BorderSide(1, edge_c)] * 4),
        shadow=(shade if shadow else None),
        clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
        **kw,
    )


def glass_bar(child, top=False, **kw):
    """نوار شیشه‌ای بالا/پایین صفحه — گوشه‌ی سمتِ صفحه تیز می‌مونه."""
    r = BR(0, 0, 20, 20) if top else BR(20, 20, 0, 0)
    return glass(child, radius=r, pad=PAD(10, 8, 10, 8), tint=0.05, sigma=28,
                 shadow=False, **kw)


def glass_pill(child, radius=26, pad=None, tint=0.07, **kw):
    """جزیره‌ی شیشه‌ایِ قرص‌شکل و شناور (نوارِ بالا و باکسِ نوشتن)."""
    return glass(child, radius=BR(radius, radius, radius, radius),
                 pad=pad if pad is not None else PAD(6, 6, 6, 6),
                 tint=tint, sigma=26, shadow=True, **kw)


def glass_circle(icon, on_click=None, box=44, size=21, color=None, tip=None,
                 **kw):
    """دکمه‌ی گردِ شناورِ شیشه‌ای — جدا از نوار، مثل تلگرامِ عکسِ مرجع."""
    ic = _icon(icon, size, color or P["txt"])
    c = glass(ft.Container(ic, width=box, height=box, alignment=AL.CENTER),
              radius=BR(box, box, box, box), pad=PAD(0, 0, 0, 0), tint=0.07,
              sigma=26, shadow=True, width=box, height=box,
              alignment=AL.CENTER, on_click=on_click, ink=True, tooltip=tip,
              animate_scale=anim(FAST, EASE), scale=1.0, **kw)

    def _h(e):
        c.scale = 1.07 if e.data else 1.0
        ic.color = (color or P["acc"]) if e.data else (color or P["txt"])
        try:
            c.update()
        except Exception:
            pass
    c.on_hover = _h
    return c


# ── آواتار (گرادیان ثابت بر اساس اسم، مثل تلگرام) ──
AV_GRADS = [
    ("#e17076", "#f5a9a9"), ("#65aadd", "#a5cdf0"), ("#a695e7", "#c9bdf3"),
    ("#ee7aae", "#f7b0cd"), ("#e5ca77", "#f2e2ab"), ("#6ec9cb", "#a8e2e3"),
    ("#d97b5c", "#f0ae95"), ("#8d7ae0", "#b6a9ef"),
]


def _grad_for(name):
    h = hashlib.md5((name or "?").encode("utf-8")).digest()[0]
    return AV_GRADS[h % len(AV_GRADS)]


def avatar(name, size=AV, url=None, on_click=None, ring=False, badge=None,
           tooltip=None, icon=None, shadow=True):
    """دایره‌ی آواتار: عکس اگه باشه، وگرنه حرف اول روی گرادیان اختصاصی.

    قفلِ ۱:۱ — سه لایه، تا آواتار هرگز بیضی نشود:
      • `aspect_ratio=1.0` روی خودِ عکس و روی جعبه،
      • `fit=COVER` تا عکسِ غیرمربع بُرش بخورد (نه اینکه کشیده شود)،
      • یک پوششِ بیرونی با `Row(tight=True)` که قیدِ تنگِ پدر را می‌شکند.
    لایه‌ی سوم مهم است: هر پدری که پهنا یا بلندیِ تنگ بدهد (ردیفِ لیست با
    ارتفاعِ ثابت، ستونِ کشیده) وگرنه جعبه‌ی ۴۸×۴۸ را می‌فشارد و دایره پهن
    یا لاغر می‌شود. با این پوشش، فشار به پوششِ بیرونی می‌خورد و هسته‌ی
    آواتار قیدِ شُل (loose) می‌گیرد، پس همیشه مربع می‌ماند.

    `icon` جای حرفِ اول می‌نشیند — برای چت‌هایی که اسمشان ترجمه می‌شود و
    حرفِ اولشان با زبان عوض می‌شود («پیام‌های ذخیره‌شده» → «پ»، «S»). گرادیان
    از خودِ `name` می‌آید، پس با عوض شدنِ زبان رنگش نمی‌پرد اگر `name` ثابت
    بماند؛ به همین خاطر صداکننده یک کلیدِ ثابت می‌دهد نه متنِ ترجمه‌شده.

    `shadow=False` سایه‌ی زیرِ آواتار را برمی‌دارد. تنها صداکننده‌اش
    `story_ring` است: سایه‌ی ۱۰ پیکسلی روی حلقه‌ی ۴ پیکسلی پخش می‌شد و
    حلقه را کدر و ناهموار می‌کرد.
    """
    r = BR(size, size, size, size)
    if url:
        # سقفِ رمزگشایی: آواتار ۴۸ پیکسلی نباید عکسِ ۱۲ مگاپیکسلی را کامل باز
        # کند. ضریبِ ۳ (نه ۲) می‌دهیم چون آواتار کوچک است و تیزیِ لبه‌ی دایره
        # بیش از هر جای دیگری به چشم می‌آید — خواسته‌ی «آواتارِ باکیفیت».
        inner = photo(url, width=size, height=size, mult=3.0,
                      aspect_ratio=1.0, fit=ft.BoxFit.COVER)
        inner.border_radius = r
    else:
        c1, c2 = _grad_for(name)
        face = (vic(icon, max(13, int(size * 0.46)), "#ffffff") if icon
                else ft.Text((name or "?").strip()[:1].upper(),
                             size=max(11, int(size * 0.42)), weight=W.W_600,
                             font_family=FONT_BOLD, color="#ffffff"))
        inner = ft.Container(
            face,
            width=size, height=size, border_radius=r, alignment=AL.CENTER,
            aspect_ratio=1.0,
            gradient=ft.LinearGradient(begin=AL.TOP_LEFT, end=AL.BOTTOM_RIGHT,
                                       colors=[c1, c2]),
        )
    box = ft.Container(
        content=inner, width=size, height=size, border_radius=r,
        aspect_ratio=1.0,
        clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
        border=(ft.Border(*[ft.BorderSide(2, op(0.55, P["acc"]))] * 4)
                if ring else None),
        shadow=(ft.BoxShadow(blur_radius=10,
                             color=op(0.12 if is_light() else 0.35, "#000000"),
                             offset=OFF(0, 3)) if shadow else None),
        tooltip=None,
    )
    if badge is not None:
        # نقطه‌ی وضعیت (آنلاین) گوشه‌ی پایینِ آواتار
        d = max(10, int(size * 0.26))
        dot = ft.Container(
            width=d, height=d, border_radius=BR(d, d, d, d), bgcolor=badge,
            border=ft.Border(*[ft.BorderSide(2, P["bg1"])] * 4),
            right=0, bottom=0,
        )
        box = ft.Stack([box, dot], width=size, height=size, aspect_ratio=1.0)
    # پوششِ بیرونی: قیدِ پدر را جذب می‌کند و به هسته قیدِ شُل می‌دهد.
    # `on_click` روی همین پوشش می‌نشیند تا جاهایی که فقط `.content` را
    # عوض می‌کنند (به‌روزرسانیِ زنده‌ی عکسِ پروفایل) کلیک را از دست ندهند.
    shell = ft.Container(
        ft.Row([box], spacing=0, tight=True,
               alignment=ft.MainAxisAlignment.CENTER,
               vertical_alignment=ft.CrossAxisAlignment.CENTER),
        width=size, height=size, alignment=AL.CENTER,
        animate_scale=anim(FAST, EASE),
        on_click=on_click, ink=bool(on_click),
        border_radius=r, tooltip=tooltip,
    )
    if on_click:
        shell.scale = 1.0

        def _h(e):
            shell.scale = 1.06 if e.data else 1.0
            shell.update()
        shell.on_hover = _h
    return shell


# ── حلقه‌ی استوری ──────────────────────────────────
# رنگ‌های حلقه‌ی «دیده‌نشده» — گرمِ تلگرامی، بی سبز (سبز فقط نقطه‌ی آنلاین).
STORY_RING = ("#F5C64A", "#F2814B", "#E9518F", "#7C5CFF", "#37B6FF")


def story_ring(name, size=60, url=None, unseen=True, plus=False,
               on_click=None, badge=None, tooltip=None):
    """آواتار با حلقه‌ی استوری؛ دیده‌نشده = حلقه‌ی رنگی و درخشان.

    ⚠️ `size` = قطرِ *خودِ آواتار*، نه قطرِ حلقه. حلقه بیرونش می‌نشیند و جای
    کلی `slot(size)` است. تا نسخه‌ی ۱.۴ برعکس بود (`inner = size - 8`) و
    همان باگِ «آواتار با استوری کوچک می‌شود» را می‌ساخت؛ تلگرام هم حلقه را
    بیرون می‌کشد، نه به هزینه‌ی آواتار.

    ★ قاعده‌ی طلایی (۱.۶): **عکسِ پروفایل با آمدنِ استوری هیچ تغییری نمی‌کند.**
    پس حلقه یک لایه‌ی مستقل در `Stack` است که *پشتِ* آواتار کشیده می‌شود و
    آواتار با همان `av_slot()`ای پیچیده می‌شود که مسیرِ بی‌استوری هم دارد —
    یعنی دقیقاً همان پیکسل‌ها، همان‌جا. پیش از این آواتار داخلِ یک
    `Container`ِ گرادیان‌دار با `padding` می‌نشست و سه چیز خرابش می‌کرد:
      • `padding` + `aspect_ratio` روی پدر، آواتار را چند پیکسل جابه‌جا می‌کرد
        (در عکسِ آزمایشی ~۷ پیکسل به چپ) و ضخامتِ حلقه ناهموار می‌شد،
      • سایه‌ی خودِ آواتار روی حلقه‌ی ۴ پیکسلی پخش می‌شد و کدرش می‌کرد
        (اکنون `shadow=False`)،
      • یک `BoxShadow`ِ بنفشِ ۱۶ پیکسلی هاله می‌انداخت و کلِ ردیف را
        آلوده می‌کرد (اکنون حذف شده).

    `badge` = نقطه‌ی وضعیت (آنلاین). روی *خودِ آواتار* می‌نشیند — نه لایه‌ی
    بیرونی — تا جایش هم با مسیرِ بی‌استوری یکی باشد؛ حلقه پشتِ آواتار است و
    دیگر چیزی را نمی‌بُرد. `unseen=False` حلقه‌ی خاکستریِ محو می‌دهد؛ سبز
    نمی‌شود، چون سبز در رفیگرام فقط برای همین نقطه‌ی آنلاین است.
    """
    inner = max(24, int(size))
    outer = slot(inner)
    # لایه‌ی ۱ — حلقه: یک دایره‌ی توپُر که آواتارِ مات وسطش را می‌پوشاند، پس
    # آن‌چه دیده می‌شود همان حلقه‌ی `RING_PAD` پیکسلی است.
    ring = ft.Container(
        width=outer, height=outer,
        border_radius=BR(outer, outer, outer, outer),
        gradient=(ft.SweepGradient(colors=list(STORY_RING) + [STORY_RING[0]],
                                   center=AL.CENTER) if unseen else None),
        bgcolor=(None if unseen else op(0.14)))
    # لایه‌ی ۲ — آواتار، با *همان* پوششِ مسیرِ بی‌استوری. بی سایه، تا حلقه
    # تیز بماند.
    face = av_slot(avatar(name, inner, url=url, badge=badge, shadow=False),
                   inner)
    core = ft.Stack([ring, face], width=outer, height=outer)
    if plus:
        d = max(16, int(outer * 0.30))
        core = ft.Stack(
            [core,
             ft.Container(_icon("plus", int(d * 0.62), "#ffffff"),
                          width=d, height=d, alignment=AL.CENTER,
                          bgcolor=P["acc"], border_radius=BR(d, d, d, d),
                          border=ft.Border(*[ft.BorderSide(2, P["bg1"])] * 4),
                          right=0, bottom=0)],
            width=outer, height=outer)
    # پوششِ بیرونی `aspect_ratio` ندارد — و این عمدی است. `AspectRatio` وقتی
    # پدر قیدِ *تنگ* بدهد (بچه‌ی یک `Column` که در محورِ افقی کشیده می‌شود)
    # بر `width`/`height` غلبه می‌کند و حلقه را به پهنایِ کاملِ نوار بزرگ
    # می‌کند؛ بعد از ارتفاعِ ثابتِ نوار سرریز و بریده می‌شود و *هیچ* رنگ
    # نمی‌شود. قفلِ ۱:۱ همان `width=height` + `Row(tight=True)` است که قید را
    # جذب می‌کند و به هسته قیدِ شُل می‌دهد (مثلِ `avatar()`).
    shell = ft.Container(
        ft.Row([core], spacing=0, tight=True,
               alignment=ft.MainAxisAlignment.CENTER,
               vertical_alignment=ft.CrossAxisAlignment.CENTER),
        width=outer, height=outer, alignment=AL.CENTER,
        border_radius=BR(outer, outer, outer, outer), tooltip=tooltip,
        animate_scale=anim(FAST, EASE), on_click=on_click, ink=bool(on_click))
    if on_click:
        shell.scale = 1.0

        def _h(e):
            shell.scale = 1.06 if e.data else 1.0
            shell.update()
        shell.on_hover = _h
    return shell


def av_slot(ctrl, size=None):
    """آواتارِ بی‌حلقه را در جای *هم‌اندازه‌ی* حلقه می‌نشاند.

    چرا لازم است: در لیستِ چت بعضی ردیف‌ها حلقه‌ی استوری دارند و بعضی نه. اگر
    ردیفِ بی‌حلقه فقط به‌اندازه‌ی آواتار جا بگیرد، ستونِ متن‌ها میانِ ردیف‌ها
    چند پیکسل جابه‌جا می‌شود و با هر بار آمدن/رفتنِ استوری کلِ لیست تکان
    می‌خورد. با این پوشش، جای آواتار همیشه `slot()` است — چه حلقه باشد چه نه.
    """
    d = slot(size)
    return ft.Container(
        ft.Row([ctrl], spacing=0, tight=True,
               alignment=ft.MainAxisAlignment.CENTER,
               vertical_alignment=ft.CrossAxisAlignment.CENTER),
        width=d, height=d, alignment=AL.CENTER)


def row_h(lines=2) -> float:
    """ارتفاعِ ردیفِ لیستِ چت — با ضریبِ اندازه‌ی متنِ کاربر بزرگ می‌شود.

    `ROW_H` ثابت (۶۸) درست به‌اندازه‌ی آواتار + پدینگ بود، یعنی صفر جای
    اضافی؛ کاربری که متن را «بزرگ» کرده بود دو سطرِ عنوان/پیشنمایش را
    نمی‌توانست جا بدهد و ردیف محتوا را می‌فشرد.

    از نسخه‌ی ۱.۵ جای *حلقه‌ی استوری* را هم حساب می‌کند (`slot(AV)`)، نه فقط
    آواتار را: ردیف باید با و بی استوری یک قد بماند، وگرنه با آمدنِ استوری
    لیست تکان می‌خورد.
    """
    pad = 2 * ROW_VPAD
    text_need = fs(15) * 1.45 * max(1, lines) + 6      # سطرها + فاصله
    return float(max(ROW_H, slot(AV) + pad, text_need + pad))


def bubble_max() -> float:
    """بیشترین پهنایِ حبابِ پیام — حدودِ ۷۵٪ عرضِ صفحه.

    بی این سقف، `ft.Text` داخلِ `Row` قیدِ پهنای بی‌نهایت می‌گیرد، یک سطرِ
    بی‌پایان می‌چیند و از حباب بیرون بریده می‌شود. روی رومیزیِ عریض هم سقفِ
    مطلق می‌گذاریم تا خطِ متن برای خواندن زیادی بلند نشود.
    """
    return max(190.0, min(_VW * 0.75, 560.0))


# ── انیمیشن ورود ───────────────────────────────────
def prep_in(ctrl, dx=0.0, dy=0.10, scale=None, ms=SLOW, curve=GLIDE):
    """کنترل رو در حالت «قبل از ورود» می‌ذاره. بعداً play_in صداش کن."""
    ctrl.opacity = 0
    ctrl.offset = OFF(dx, dy)
    ctrl.animate_opacity = anim(ms, curve)
    ctrl.animate_offset = anim(ms, curve)
    if scale is not None:
        ctrl.scale = scale
        ctrl.animate_scale = anim(ms, curve)
    return ctrl


def play_in(ctrl):
    ctrl.opacity = 1
    ctrl.offset = OFF(0, 0)
    if ctrl.scale is not None:
        ctrl.scale = 1.0


async def enter(page, *ctrls, stagger=0.035, first_delay=0.03):
    """ورودِ پله‌ای — همون حسِ باز شدن لیست چت تلگرام."""
    await asyncio.sleep(first_delay)
    for c in ctrls:
        play_in(c)
        try:
            page.update()
        except Exception:
            return
        if stagger:
            await asyncio.sleep(stagger)


def hover_tint(ctrl, base=None, hi=None, ms=FAST):
    """هایلایت نرمِ ردیف‌ها وقتی موس روش می‌ره."""
    base = base if base is not None else op(0.0)
    hi = hi if hi is not None else op(0.05 if is_light() else 0.06)
    ctrl.bgcolor = base
    ctrl.animate = anim(MED, EASE)

    def _h(e):
        ctrl.bgcolor = hi if e.data else base
        try:
            ctrl.update()
        except Exception:
            pass
    ctrl.on_hover = _h
    return ctrl


def pop_in(ctrl, page, dy=-0.18, dx=0.0, scale=None, ms=MED):
    """کنترل را سرِ جای خودش با یک لغزشِ نرم نشان می‌دهد (نوارِ پاسخ، سنجاق…)."""
    prep_in(ctrl, dx=dx, dy=dy, scale=scale, ms=ms)
    ctrl.visible = True
    try:
        page.run_task(enter, page, ctrl, stagger=0.0, first_delay=0.01)
    except Exception:
        play_in(ctrl)
    return ctrl


def pulse(ctrl, up=1.03, ms=FAST):
    """بازخوردِ فشارِ نرم روی هر کنترلِ کلیک‌شدنی (بدونِ نیاز به هاور)."""
    ctrl.animate_scale = anim(ms, EASE)
    if ctrl.scale is None:
        ctrl.scale = 1.0

    def _h(e):
        ctrl.scale = up if e.data else 1.0
        try:
            ctrl.update()
        except Exception:
            pass
    ctrl.on_hover = _h
    return ctrl


def typing_dots(color=None, size=5, gap=3):
    """سه نقطه‌ی «داره می‌نویسه» — ساکن و آرام (بی‌حلقه‌ی تکرارشونده)."""
    col = color or P["acc"]
    items = [ft.Container(width=size, height=size, bgcolor=col,
                         opacity=o, border_radius=BR(size, size, size, size))
             for o in (0.95, 0.65, 0.40)]
    return ft.Row(items, spacing=gap, tight=True, visible=False,
                  vertical_alignment=ft.CrossAxisAlignment.CENTER)


def typing_step(row, step):
    """(بازنشسته) موجِ نقطه‌ها برداشته شد؛ برای سازگاری بی‌اثر مانده."""
    return None


# ── دکمه‌ها و فیلدها ───────────────────────────────
def icon_btn(icon, on_click=None, color=None, size=22, tip=None, box=40):
    """دکمه‌ی آیکونیِ گرد با هاور شیشه‌ای (آیکون SVG یا متریال)."""
    ic = _icon(icon, size, color or P["sub"])
    c = ft.Container(
        ic, width=box, height=box, border_radius=BR(box, box, box, box),
        alignment=AL.CENTER, on_click=on_click, ink=True, tooltip=tip,
        bgcolor=op(0.0), animate=anim(FAST, EASE), animate_scale=anim(FAST, EASE),
        scale=1.0,
    )

    def _h(e):
        c.bgcolor = op(0.10) if e.data else op(0.0)
        c.scale = 1.05 if e.data else 1.0
        ic.color = (color or P["txt"]) if e.data else (color or P["sub"])
        try:
            c.update()
        except Exception:
            pass
    c.on_hover = _h
    return c


def field(label=None, hint=None, value="", password=False, width=None,
          multiline=False, on_submit=None, on_change=None, prefix=None):
    """فیلد متنیِ تلگرامی: بدون خط، پس‌زمینه‌ی نیمه‌شفاف، گوشه‌ی گرد."""
    return ft.TextField(
        label=label, hint_text=hint, value=value,
        password=password, can_reveal_password=password,
        width=width, expand=(width is None),
        multiline=multiline, min_lines=1, max_lines=5 if multiline else 1,
        bgcolor=op(0.06), color=P["txt"],
        border=ft.InputBorder.NONE, filled=True, border_radius=14,
        cursor_color=P["acc"], selection_color=op(0.30, P["acc"]),
        label_style=ft.TextStyle(color=P["sub"], size=fs(13)),
        hint_style=ft.TextStyle(color=P["dim"], size=fs(14)),
        text_size=fs(14), content_padding=PAD(16, 14, 16, 14),
        prefix_icon=prefix, on_submit=on_submit, on_change=on_change,
    )


def composer_field(hint=None):
    """فیلدِ نوشتنِ پیام — بی‌پس‌زمینه، چون داخلِ قرصِ شیشه‌ای می‌نشیند."""
    return ft.TextField(
        hint_text=hint, expand=True, multiline=True, min_lines=1, max_lines=6,
        bgcolor=op(0.0), color=P["txt"], filled=False,
        border=ft.InputBorder.NONE, border_radius=0,
        cursor_color=P["acc"], selection_color=op(0.30, P["acc"]),
        hint_style=ft.TextStyle(color=P["dim"], size=fs(14.5)),
        text_size=fs(14.5), content_padding=PAD(8, 12, 8, 12),
        text_vertical_align=ft.VerticalAlignment.CENTER,
    )


def primary_btn(text, on_click=None, icon=None, width=None, height=46):
    """دکمه‌ی اصلی: گرادیانِ رنگ لهجه + سایه‌ی رنگی + فشارِ نرم."""
    row = [ft.Text(text, size=fs(15), weight=W.W_600, font_family=FONT_BOLD,
                   color="#ffffff")]
    if icon:
        row.insert(0, _icon(icon, 18, "#ffffff"))
    c = ft.Container(
        ft.Row(row, alignment=ft.MainAxisAlignment.CENTER, spacing=8),
        width=width, height=height, border_radius=BR(14, 14, 14, 14),
        alignment=AL.CENTER, on_click=on_click, ink=True,
        gradient=ft.LinearGradient(begin=AL.CENTER_LEFT, end=AL.CENTER_RIGHT,
                                   colors=[P["acc2"], P["acc"]]),
        shadow=ft.BoxShadow(blur_radius=18, spread_radius=-4,
                            color=op(0.45, P["acc"]), offset=OFF(0, 6)),
        animate_scale=anim(FAST, EASE), scale=1.0,
    )

    def _h(e):
        c.scale = 1.02 if e.data else 1.0
        c.update()
    c.on_hover = _h
    return c


def chip_btn(label, on, on_click=None):
    """دکمه‌ی کپسولیِ شیک با فیدبک فنری."""
    c = ft.Container(
        ft.Text(label, size=fs(12), font_family=FONT_MED,
                color="#ffffff" if on else P["sub"]),
        padding=PAD(14, 7, 14, 7), border_radius=BR(12, 12, 12, 12),
        bgcolor=P["acc"] if on else op(0.06), on_click=on_click, ink=True,
        animate=anim(FAST, EASE), animate_scale=anim(FAST, POP), scale=1.0, alignment=AL.CENTER)

    def _h(e):
        c.scale = 1.05 if e.data else 1.0
        try:
            c.update()
        except Exception:
            pass
    c.on_hover = _h
    return c


# ── قطعات تلگرامی ──────────────────────────────────
def section(title):
    """تیتر بخش، مثل تیترهای کوچکِ تنظیمات تلگرام."""
    return ft.Container(
        ft.Text(title, size=fs(13), weight=W.W_600, font_family=FONT_BOLD,
                color=P["acc"]),
        padding=PAD(6, 14, 6, 8),
    )


def inset_group(*rows, radius=20, title=None):
    """کارتِ اینستِ تلگرام (Inset Container) با حاشیه‌ی ۱۸–۲۴dp و پس‌زمینه‌ی تیره."""
    items = []
    if title:
        items.append(
            ft.Container(
                ft.Text(title, size=fs(13.5), weight=W.W_600,
                        color=P.get("nav_active", P["acc"]), font_family=FONT_BOLD),
                padding=PAD(16, 12, 16, 4)
            )
        )
    for i, r in enumerate(rows):
        items.append(r)
        if i != len(rows) - 1:
            items.append(ft.Container(height=1, bgcolor=P.get("line", "#2c2c2e"),
                                      margin=MG(16, 0, 16, 0)))
    return ft.Container(
        content=ft.Column(items, spacing=0, tight=True),
        bgcolor=P["bg1"],
        border_radius=BR(radius, radius, radius, radius),
        border=ft.Border(*[ft.BorderSide(0.8, P.get("line", "#2c2c2e"))] * 4),
        padding=PAD(0, 4, 0, 4),
        margin=MG(0, 4, 0, 8)
    )


def settings_tile(icon, title, subtitle=None, trailing=None, on_click=None,
                  icon_bg=None, icon_color="#ffffff", badge_count=None):
    """ردیفِ مدرنِ تنظیمات تلگرام با نشانِ دایره‌ای رنگی ۳۲x۳۲ آیکون در سمت چپ."""
    bg_col = icon_bg or P["acc"]
    ic = _icon(icon, 17, icon_color)
    circle = ft.Container(
        content=ic, width=32, height=32, border_radius=BR(16, 16, 16, 16),
        bgcolor=bg_col, alignment=AL.CENTER
    )
    mid = [ft.Text(title, size=fs(14.5), weight=W.W_500, color=P["txt"],
                   font_family=FONT_MED, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)]
    if subtitle:
        mid.append(ft.Text(subtitle, size=fs(12), color=P["sub"],
                           max_lines=1, overflow=ft.TextOverflow.ELLIPSIS))
    mid_col = ft.Column(mid, spacing=2, expand=True, alignment=ft.MainAxisAlignment.CENTER)

    right_items = []
    if badge_count:
        right_items.append(badge(badge_count, bg="#E53935"))
    if trailing is not None:
        right_items.append(trailing)
    elif on_click is not None:
        right_items.append(vic("chevron_left" if is_rtl() else "chevron_right", 16, P["dim"]))

    row_content = [circle, mid_col]
    if right_items:
        row_content.append(ft.Row(right_items, spacing=6, tight=True, alignment=ft.MainAxisAlignment.END))

    c = ft.Container(
        ft.Row(row_content, spacing=14, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        padding=PAD(14, 11, 14, 11), ink=bool(on_click), on_click=on_click,
        animate_opacity=FAST, animate_scale=anim(FAST, POP), scale=1.0
    )
    hover_tint(c)
    if on_click:
        def _tile_h(e):
            c.scale = 1.015 if e.data else 1.0
            try:
                c.update()
            except Exception:
                pass
        c.on_hover = _tile_h
    return c


def action_tile(icon, label, on_click=None, width=76, height=64, color=None):
    """کاشیِ اکشن بالای پروفایل تلگرام (Message, Mute, Share, Stop)."""
    col = color or P["txt"]
    ic = _icon(icon, 22, col)
    c = ft.Container(
        content=ft.Column([
            ic,
            ft.Text(label, size=fs(11.5), weight=W.W_500, color=col,
                    font_family=FONT_MED, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)
        ], spacing=4, alignment=ft.MainAxisAlignment.CENTER,
           horizontal_alignment=ft.CrossAxisAlignment.CENTER, tight=True),
        width=width, height=height, border_radius=BR(14, 14, 14, 14),
        bgcolor=P["bg2"], alignment=AL.CENTER, ink=True, on_click=on_click,
        border=ft.Border(*[ft.BorderSide(0.8, P.get("line", "#2c2c2e"))] * 4),
        animate_scale=anim(FAST, POP), scale=1.0
    )
    def _h(e):
        c.scale = 1.05 if e.data else 1.0
        try:
            c.update()
        except Exception:
            pass
    c.on_hover = _h
    return c


def sub_tab_item(label, active=False, on_click=None):
    """زبانه افقی دسته‌بندی مدیا در پروفایل (Media, Files, Links, Voice, GIFs)."""
    col = P.get("nav_active", P["acc"]) if active else P["sub"]
    line = ft.Container(
        height=3, width=32 if active else 0, border_radius=BR(3, 3, 3, 3),
        bgcolor=col if active else op(0.0), animate=anim(FAST, EASE)
    )
    return ft.Container(
        content=ft.Column([
            ft.Text(label, size=fs(13.5), weight=W.W_600 if active else W.W_500,
                    color=col, font_family=FONT_MED),
            line
        ], spacing=4, alignment=ft.MainAxisAlignment.CENTER,
           horizontal_alignment=ft.CrossAxisAlignment.CENTER, tight=True),
        padding=PAD(12, 6, 12, 6), ink=True, on_click=on_click,
        border_radius=BR(8, 8, 8, 8)
    )


def link_preview_card(url, title, desc=None, image_url=None, on_click=None):
    """کارت پیش‌نمایش لینک سبک تلگرام درون حباب چت."""
    side_border = ft.BorderSide(3, P.get("acc", "#24A1DE"))
    border_kw = {"right": side_border} if is_rtl() else {"left": side_border}

    col_items = [
        ft.Text(title or url, size=fs(13), weight=W.W_600, color=P["acc"],
                font_family=FONT_MED, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)
    ]
    if desc:
        col_items.append(
            ft.Text(desc, size=fs(12), color=P["txt"], max_lines=3,
                    overflow=ft.TextOverflow.ELLIPSIS)
        )
    if image_url:
        col_items.append(
            ft.Container(
                photo(image_url, height=140, fit=ft.BoxFit.COVER, aspect_ratio=16/9),
                border_radius=BR(8, 8, 8, 8), clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
                margin=MG(0, 4, 0, 2)
            )
        )
    col_items.append(
        ft.Text(url, size=fs(11), color=P["sub"], max_lines=1,
                overflow=ft.TextOverflow.ELLIPSIS)
    )

    return ft.Container(
        content=ft.Column(col_items, spacing=3, tight=True),
        padding=PAD(10, 8, 10, 8),
        bgcolor=op(0.20, P["bg0"]),
        border=ft.Border(**border_kw),
        border_radius=BR(6, 6, 6, 6),
        ink=bool(on_click), on_click=on_click,
        margin=MG(0, 4, 0, 4)
    )


def group(*rows, radius=18):
    """کارتِ گروهیِ ردیف‌ها با جداکننده‌های نازک — الگوی تنظیمات تلگرام."""
    items = []
    for i, r in enumerate(rows):
        items.append(r)
        if i != len(rows) - 1:
            items.append(ft.Container(height=1, bgcolor=op(0.06),
                                      margin=MG(14, 0, 14, 0)))
    return glass(ft.Column(items, spacing=0, tight=True),
                 radius=radius, pad=PAD(0, 6, 0, 6), tint=0.05, sigma=18,
                 in_list=True)


def row_item(icon, title, subtitle=None, trailing=None, on_click=None,
             icon_color=None, stack=None):
    """یک ردیفِ لیست: آیکون + عنوان (+زیرعنوان) + المان سمت مقابل.

    اگر المانِ سمتِ مقابل چند تکه باشد (مثلِ سه کپسولِ «کوچک/معمولی/بزرگ»)،
    روی صفحه‌ی باریک جای عنوان آن‌قدر تنگ می‌شد که متنِ فارسی حرف‌به‌حرف
    زیرِ هم می‌شکست. در آن حالت المان‌ها می‌روند زیرِ عنوان (`stack`) و
    عنوان تمامِ عرض را می‌گیرد. `stack=True/False` این تصمیم را دستی می‌کند.
    """
    left = [_icon(icon, 20, icon_color or P["sub"])] if icon else []
    mid = [ft.Text(title, size=fs(14), color=P["txt"],
                   max_lines=2, overflow=ft.TextOverflow.ELLIPSIS)]
    if subtitle:
        mid.append(ft.Text(subtitle, size=fs(12), color=P["sub"],
                           max_lines=2, overflow=ft.TextOverflow.ELLIPSIS))
    label = ft.Column(mid, spacing=2, expand=True,
                      alignment=ft.MainAxisAlignment.CENTER)
    if stack is None:
        stack = (isinstance(trailing, ft.Row)
                 and len(trailing.controls or []) > 1
                 and vw() < 520)
    if stack and trailing is not None:
        # کپسول‌ها می‌روند سطرِ بعد و خودشان هم اگر جا نشدند می‌شکنند
        trailing.wrap = True
        trailing.run_spacing = 8
        trailing.alignment = ft.MainAxisAlignment.START
        body = ft.Column(
            [ft.Row([*left, label], spacing=14,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER),
             ft.Container(trailing, padding=PAD(0, 10, 0, 0))],
            spacing=0, tight=True)
    else:
        body = ft.Row(
            [*left, label,
             *([trailing] if trailing is not None else [])],
            spacing=14, vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
    c = ft.Container(body, padding=PAD(16, 12, 16, 12), on_click=on_click,
                     ink=bool(on_click), border_radius=BR(12, 12, 12, 12))
    if on_click:
        hover_tint(c)
    return c


def date_chip(text):
    """کپسولِ تاریخ بین پیام‌ها."""
    lite = is_light()
    return ft.Container(
        ft.Container(ft.Text(text, size=fs(11), color=op(0.85, P["txt"])),
                     padding=PAD(12, 5, 12, 5),
                     border_radius=BR(12, 12, 12, 12),
                     bgcolor=(op(0.78, "#ffffff") if lite
                              else op(0.13 if not is_phone() else 0.55,
                                      "#000000")),
                     border=(ft.Border(*[ft.BorderSide(1, op(0.07))] * 4)
                             if lite else None),
                     # داخلِ لیستِ پیام‌هاست ⇒ روی گوشی بی‌تاری (heavy)
                     blur=blur_of(12, heavy=True)),

        alignment=AL.CENTER, margin=MG(0, 10, 0, 6),
        animate_opacity=anim(SLOW, EASE), animate_offset=anim(SLOW, EASE),
        animate_scale=anim(MED, POP),
    )


def dialog(page, title, body, actions=None, width=340):
    """دیالوگ شیشه‌ای هم‌سبکِ بقیه‌ی برنامه."""
    panel = glass(
        ft.Column(
            [ft.Text(title, size=fs(17), weight=W.W_600,
                     font_family=FONT_BOLD, color=P["txt"]),
             ft.Container(height=10),
             body,
             *([ft.Container(height=14),
                ft.Row(actions, alignment=ft.MainAxisAlignment.END,
                       spacing=8)] if actions else []),
             ],
            spacing=0, tight=True,
        ),
        radius=24, pad=PAD(20, 18, 20, 16), tint=0.10, sigma=30,
    )
    prep_in(panel, dy=0.05, scale=0.96, ms=MED)
    d = ft.AlertDialog(
        bgcolor=op(0.0),
        shadow_color=op(0.0),
        elevation=0,
        barrier_color=op(0.30 if is_light() else 0.55, "#000000"),
        content_padding=PAD(0, 0, 0, 0),
        inset_padding=PAD(20, 40, 20, 40),
        # روی گوشیِ باریک، پهنای ثابت باعثِ سرریز و جابه‌جاییِ چیدمان می‌شه
        # `rtl`: دیالوگ‌ها در `page._dialogs` می‌نشینند، بیرونِ درختِ اصلی، و
        # `page.rtl` را به ارث نمی‌برند؛ بی این، عنوان و دکمه‌ها در فارسی
        # وارونه (چپ‌چین) می‌افتند.
        content=ft.Container(panel, width=panel_w(width, 56), rtl=i18n.rtl()),
    )
    page.show_dialog(d)
    try:
        page.run_task(enter, page, panel, stagger=0.0, first_delay=0.02)
    except Exception:
        play_in(panel)
    return d


def ghost_btn(text, on_click=None, color=None):
    """دکمه‌ی خنثی (لغو/بستن)."""
    return ft.TextButton(
        text, on_click=on_click,
        style=ft.ButtonStyle(color=color or P["sub"],
                             shape=ft.RoundedRectangleBorder(radius=12)),
    )


def spacer(h=0, w=0):
    return ft.Container(height=h or None, width=w or None)


# ── نشانگرِ نخونده (مثل تلگرام) ──────────────────────
def badge(count, muted=False, bg=None):
    """کپسولِ شمارشِ پیام‌های نخونده — رقم‌ها با زبانِ برنامه هم‌خوان‌اند."""
    txt = (i18n.num(count) if count < 1000
           else f"{i18n.num(count // 1000)}k")
    base_bg = bg or (op(0.35, "#ffffff") if muted else P.get("nav_badge", "#E53935"))
    return ft.Container(
        ft.Text(txt, size=fs(10.5), weight=W.W_700, font_family=FONT_BOLD,
                color="#ffffff"),
        padding=PAD(6, 1.5, 6, 2.5),
        border_radius=BR(11, 11, 11, 11),
        bgcolor=base_bg,
        alignment=AL.CENTER,
        shadow=(None if muted else
                ft.BoxShadow(blur_radius=10, spread_radius=-3,
                             color=op(0.55, base_bg), offset=OFF(0, 2))),
        animate_scale=anim(FAST, POP), scale=1.0,
    )


# ── ردیفِ کلید روشن/خاموش ───────────────────────────
def toggle_row(icon, title, value, on_change, subtitle=None, out=None):
    """ردیفِ تنظیمات با سوییچ (روشن/خاموش).

    `out` یک لیستِ خالی است و اگر بدهی، خودِ `Switch` در آن گذاشته می‌شود؛
    صداکننده این‌طور می‌تواند بعدها مقدارش را عوض کند — مثلاً وقتی پاسخِ
    شبکه *بعد* از رنگ‌شدنِ صفحه می‌رسد (آفلاین‑اول).
    """
    sw = ft.Switch(value=bool(value), on_change=on_change,
                   active_color=P["acc"], inactive_track_color=op(0.12))
    if out is not None:
        out.append(sw)
    return row_item(icon, title, subtitle=subtitle, trailing=sw)


# ── لوگو ────────────────────────────────────────────
def logo(size=76, glow=True):
    """نشانِ رفیگرام با درخششِ رنگی."""
    return ft.Container(
        ft.Image(src="icons/logo.svg", width=size, height=size,
                 fit=ft.BoxFit.CONTAIN),
        width=size, height=size,
        border_radius=BR(size * 0.28, size * 0.28, size * 0.28, size * 0.28),
        shadow=(ft.BoxShadow(blur_radius=30, spread_radius=-6,
                             color=op(0.55, P["acc"]), offset=OFF(0, 10))
                if glow else None),
    )


# ── موجِ صوتی (میله‌های پیام صوتی) ───────────────────
def wave_bars(seed, count=34, height=22, played=0.0, color=None, dim=None):
    """میله‌های موجِ صوتی؛ نسبتِ `played` پررنگ می‌شه."""
    color = color or "#ffffff"
    dim = dim or op(0.34, "#ffffff")
    h = hashlib.md5((seed or "v").encode("utf-8")).digest()
    bars = []
    done = int(count * max(0.0, min(1.0, played)))
    for i in range(count):
        # ارتفاعِ شبه‌تصادفیِ ثابت برای هر فایل (تا هر بار عوض نشه)
        v = h[i % len(h)] / 255.0
        bh = max(3, int(height * (0.28 + 0.72 * v)))
        bars.append(ft.Container(
            width=2.5, height=bh, border_radius=BR(2, 2, 2, 2),
            bgcolor=color if i < done else dim,
            animate=anim(FAST, EASE),
        ))
    return ft.Row(bars, spacing=2, height=height,
                  vertical_alignment=ft.CrossAxisAlignment.CENTER)


# ── رنگِ متن روی حبابِ پیام ─────────────────────────
# ── هندسه‌ی حبابِ پیام ───────────────────────────────
# تلگرام پیام‌های پشت‌سرهمِ یک فرستنده را یک «دسته» می‌کند: فقط حبابِ
# آخرِ دسته گوشه‌ی تیز (دُم) دارد و بقیه گوشه‌ی نیم‌گردِ چسبیده می‌گیرند،
# و فاصله‌ی درونِ دسته از فاصله‌ی بینِ دسته‌ها کمتر است.
BUB_R = 18          # گوشه‌های آزاد
BUB_TAIL = 4        # گوشه‌ی دُم — فقط آخرینِ دسته
BUB_JOIN = 9        # گوشه‌ی سمتِ دُم در حباب‌های میانیِ دسته
GAP_IN, GAP_OUT = 1, 4      # حاشیه‌ی عمودی: درونِ دسته / بینِ دسته‌ها
                            # (حاشیه‌ها جمع می‌شوند: ۲ و ۸ پیکسل)


def bubble_radius(me, last=True):
    """گوشه‌های حباب؛ گوشه‌ی دُم فقط روی آخرین حبابِ دسته تیز می‌شود."""
    k = BUB_TAIL if last else BUB_JOIN
    return (BR(BUB_R, BUB_R, BUB_R, k) if me
            else BR(BUB_R, BUB_R, k, BUB_R))


def on_bubble(sent=False):
    """در پوسته‌ی روشن، متنِ حباب تیره‌ست؛ در تاریک سفید."""
    return P["txt"] if is_light() else "#ffffff"


def sub_bubble(sent=False):
    """رنگِ متنِ کم‌رنگ (ساعت، ریپلای) روی حباب."""
    # در روشن، ۰.۶۵ روی حبابِ آبیِ کم‌رنگ خیلی محو می‌شد
    return op(0.72, P["txt"]) if is_light() else op(0.72, "#ffffff")


def _lumin(hexc) -> float:
    """روشناییِ نسبیِ sRGB (با تصحیحِ گاما) — ۰ سیاه، ۱ سفید."""
    try:
        ch = [int(hexc[i:i + 2], 16) / 255.0 for i in (1, 3, 5)]
    except Exception:
        return 0.5
    lin = [(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
           for c in ch]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _ratio(c1, c2) -> float:
    """نسبتِ کنتراستِ دو رنگ (استانداردِ WCAG): ۱ = یکسان، ۲۱ = سیاه/سفید."""
    a, b = _lumin(c1) + 0.05, _lumin(c2) + 0.05
    return (a / b) if a > b else (b / a)


_ACC_CACHE = {}
MIN_ACC_RATIO = 2.5     # کمتر از این، لهجه روی حباب گِل‌آلود می‌شود


def bub_acc(sent=False):
    """لهجه‌ای که روی *همین* حباب خوانا باشد.

    رنگِ لهجه برای پس‌زمینه‌ی صفحه انتخاب شده، نه برای حباب. در پوسته‌هایی
    که لهجه و حبابِ فرستاده هم‌خانواده‌اند (سرمه‌ای روی سرمه‌ای، آبی روی
    آبی) تیکِ «خوانده شد» و سرنامِ فرستنده تویِ حباب گم می‌شوند. پس فقط
    وقتی کنتراست از آستانه کمتر باشد، آن‌قدر که لازم است روشن/تیره‌ترش
    می‌کنیم — نه بیشتر، تا رنگِ امضایِ پوسته حفظ شود.
    """
    acc, bg = P["acc"], (P["sent"] if sent else P["recv"])

    def _ok(c):
        return isinstance(c, str) and c.startswith("#") and len(c) == 7
    if not (_ok(acc) and _ok(bg)):
        return acc
    key = (acc, bg)
    if key in _ACC_CACHE:
        return _ACC_CACHE[key]
    out = acc
    if _ratio(acc, bg) < MIN_ACC_RATIO:
        far = "#000000" if _lumin(bg) > 0.18 else "#ffffff"
        for step in (0.15, 0.30, 0.45, 0.60, 0.75):
            out = _mix(acc, far, step)
            if _ratio(out, bg) >= MIN_ACC_RATIO:
                break
    _ACC_CACHE[key] = out
    return out


# ── دکمه‌ی شناورِ گرد (FAB تلگرام موبایل) ─────────────
# ── نوارِ پایینِ تلگرامِ اندروید ─────────────────────
def nav_tab(icon, label, active=False, count=0, on_click=None, avatar_url=None):
    """یک زبانه‌ی نوارِ پایینِ تلگرام: قرصِ هایلایتِ متحرک + آیکون/آواتار با فنریت زنده."""
    active_col = P.get("nav_active", P["acc"])
    col = active_col if active else P["sub"]
    if avatar_url:
        ic = avatar(label or "?", 24, url=avatar_url, shadow=False)
    else:
        ic = _icon(icon, 22, col)

    # قرصِ هایلایت پشت آیکون (دقیقاً مثل نسخه جدید تلگرام)
    pill_bg = op(0.18, active_col) if active else op(0.0)
    icon_box = ft.Container(
        content=ic,
        padding=PAD(14, 3, 14, 3) if active else PAD(8, 3, 8, 3),
        border_radius=BR(16, 16, 16, 16),
        bgcolor=pill_bg,
        alignment=AL.CENTER,
        animate=anim(FAST, POP),
        animate_scale=anim(FAST, POP),
        scale=1.08 if active else 1.0
    )

    stack = [icon_box]
    if count:
        stack.append(ft.Container(badge(count, bg="#E53935"), right=-6, top=-6))

    body = ft.Column(
        [ft.Stack(stack, alignment=AL.CENTER),
         ft.Text(label, size=fs(10.5), color=col,
                 font_family=FONT_BOLD if active else FONT,
                 weight=W.W_600 if active else W.W_400,
                 max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)],
        spacing=3, tight=True,
        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        alignment=ft.MainAxisAlignment.CENTER)

    c = ft.Container(
        body, expand=True, padding=PAD(2, 4, 2, 4), ink=True,
        border_radius=BR(18, 18, 18, 18), on_click=on_click, tooltip=label,
        animate_scale=anim(FAST, POP), scale=1.0)

    def _h(e):
        c.scale = 1.06 if e.data else 1.0
        try:
            c.update()
        except Exception:
            pass
    c.on_hover = _h
    return c


def nav_bar(*tabs):
    """نوارِ شناورِ پایین — مطابق داک مدرن تلگرام با گوشه‌های گرد و تم تیره."""
    return ft.Container(
        ft.Container(
            ft.Row(list(tabs), spacing=4,
                   alignment=ft.MainAxisAlignment.SPACE_EVENLY,
                   vertical_alignment=ft.CrossAxisAlignment.CENTER),
            bgcolor=P["bg1"],
            border_radius=BR(24, 24, 24, 24),
            border=ft.Border(*[ft.BorderSide(1, P.get("line", "#2c2c2e"))] * 4),
            padding=PAD(6, 6, 6, 6),
            shadow=ft.BoxShadow(blur_radius=25, spread_radius=-4,
                                color=op(0.65, "#000000"), offset=OFF(0, 8)),
        ),
        margin=MG(12, 0, 12, 10), animate_offset=anim(FAST, GLIDE),
        animate_opacity=anim(FAST, EASE))


def fab(icon, on_click=None, size=56, tip=None, icon_size=24):
    """دکمه‌ی گردِ شناور با گرادیانِ لهجه و سایه‌ی رنگی."""
    ic = _icon(icon, icon_size, "#ffffff")
    c = ft.Container(
        ic, width=size, height=size, border_radius=BR(size, size, size, size),
        alignment=AL.CENTER, on_click=on_click, ink=True, tooltip=tip,
        gradient=ft.LinearGradient(begin=AL.TOP_LEFT, end=AL.BOTTOM_RIGHT,
                                   colors=[P["acc2"], P["acc"]]),
        # سایه‌ی جمع‌تر و نزدیک‌تر (بلندیِ ۶ به‌جای ۸) تا با سایه‌ی حباب و
        # نوارِ پایین یک‌خانواده باشد؛ در پوسته‌ی روشن یک حلقه‌ی نازکِ سفید
        # لبه‌اش را از پس‌زمینه‌ی کم‌رنگ جدا می‌کند.
        border=(ft.Border(*[ft.BorderSide(1.4, op(0.55, "#ffffff"))] * 4)
                if is_light() else None),
        shadow=ft.BoxShadow(blur_radius=18, spread_radius=-2,
                            color=op(0.45, P["acc"]), offset=OFF(0, 6)),
        animate_scale=anim(FAST, POP), animate_opacity=FAST,
        animate_rotation=anim(MED, EASE),
        scale=1.0, rotate=ft.Rotate(0),
    )

    def _h(e):
        c.scale = 1.08 if e.data else 1.0
        c.update()
    c.on_hover = _h
    return c


# ── پس‌زمینه‌ی نقش‌دارِ چت (کاشیِ برداری) ──────────────
def pattern_bg(opacity=0.06, tile=210):
    """کاشیِ SVG که کلِ صفحه‌ی چت رو مثل تلگرام پُر می‌کنه."""
    # در پوسته‌ی روشن، نقشِ تیره روی زمینه‌ی روشن زود شلوغ می‌شه؛ کم‌رنگ‌ترش می‌کنیم
    op_v = opacity * 0.55 if is_light() else opacity
    return ft.Container(
        expand=True, ignore_interactions=True, opacity=op_v,
        image=ft.DecorationImage(
            src="icons/pattern_ink.svg" if is_light() else "icons/pattern.svg",
            repeat=ft.ImageRepeat.REPEAT, fit=ft.BoxFit.NONE,
        ),
    )


def photo_bg(src, dim=0.35, blur=0.0):
    """کاغذدیواریِ دلخواهِ کاربر برای صفحه‌ی چت (عکسِ آپلودشده)."""
    layers = [ft.Container(
        expand=True, ignore_interactions=True,
        image=ft.DecorationImage(src=src, fit=ft.BoxFit.COVER,
                                 repeat=ft.ImageRepeat.NO_REPEAT))]
    try:
        d = max(0.0, min(0.85, float(dim)))
    except (TypeError, ValueError):
        d = 0.35
    try:
        b = max(0.0, min(24.0, float(blur)))
    except (TypeError, ValueError):
        b = 0.0
    if d > 0 or b > 0:
        layers.append(ft.Container(
            expand=True, ignore_interactions=True,
            bgcolor=ft.Colors.with_opacity(
                d, "#000000" if not is_light() else "#ffffff"),
            blur=(ft.Blur(b, b, ft.BlurTileMode.MIRROR) if b > 0 else None)))
    return layers


def chat_backdrop(opacity=0.07, bg_src=None, dim=0.35, blur=0.0, pattern=True):
    """لایه‌های پس‌زمینه‌ی صفحه‌ی چت: درخشش + کاغذدیواری + کاشیِ نقش‌دار."""
    layers = [*backdrop()]
    if bg_src:
        layers += photo_bg(bg_src, dim, blur)
    if pattern:
        layers.append(pattern_bg(opacity))
    return layers


def preset_card(p, label, on=False, on_click=None, width=104):
    """کارتِ یک مدلِ آماده‌ی پوسته در گالریِ سازنده‌ی تم."""
    strip = ft.Container(
        ft.Row([ft.Container(width=26, height=10, bgcolor=p["sent"],
                             border_radius=BR(6, 6, 6, 6)),
                ft.Container(width=16, height=10, bgcolor=p["acc"],
                             border_radius=BR(6, 6, 6, 6))],
               spacing=4, tight=True,
               alignment=ft.MainAxisAlignment.CENTER),
        alignment=AL.CENTER)
    return ft.Container(
        ft.Column([ft.Container(strip, bgcolor=p["bg"], height=42,
                                border_radius=BR(10, 10, 10, 10),
                                alignment=AL.CENTER),
                   ft.Text(label, size=fs(10.5), color=P["sub"] if not on
                           else P["txt"], font_family=FONT_MED,
                           max_lines=1, no_wrap=True,
                           text_align=ft.TextAlign.CENTER)],
                  spacing=6, tight=True,
                  horizontal_alignment=ft.CrossAxisAlignment.CENTER),
        width=width, padding=PAD(6, 6, 6, 6), on_click=on_click, ink=True,
        border_radius=BR(14, 14, 14, 14),
        bgcolor=op(0.10) if on else op(0.03),
        border=ft.Border(*[ft.BorderSide(1.4 if on else 1,
                                         p["acc"] if on else op(0.07))] * 4),
        animate=anim(FAST, EASE), animate_scale=anim(FAST, POP),
        scale=1.0)


def swatch_dot(color, on=False, on_click=None, size=26):
    """نقطه‌ی رنگِ آماده برای انتخابِ سریعِ لهجه."""
    return ft.Container(
        (vic("check", 14, "#ffffff") if on else None),
        width=size, height=size, bgcolor=color, alignment=AL.CENTER,
        border_radius=BR(size, size, size, size), on_click=on_click, ink=True,
        border=ft.Border(*[ft.BorderSide(2, op(0.85) if on else op(0.10))] * 4),
        animate=anim(FAST, EASE), animate_scale=anim(FAST, POP), scale=1.0)


# ── نشانگرِ عکس‌ها (استوری‌وار) ───────────────────────
def dots(count, index=0, on_pick=None, width=None):
    """میله‌های باریکِ بالای عکسِ پروفایل — مثل استوریِ تلگرام."""
    if count <= 1:
        return ft.Container(height=0)
    items = []
    for i in range(count):
        bar = ft.Container(
            height=3, expand=True, border_radius=BR(3, 3, 3, 3),
            bgcolor=op(0.95, "#ffffff") if i == index else op(0.30, "#ffffff"),
            animate=anim(FAST, EASE), data=i,
            on_click=(lambda e: on_pick(int(e.control.data))) if on_pick else None,
        )
        items.append(bar)
    return ft.Row(items, spacing=4, width=width)


# ── ویدیوی تکرارشو (گیفِ تلگرامی = mp4ِ بی‌صدا) ─────
VID_SRC_EXT = (".mp4", ".mov", ".webm", ".mkv", ".m4v", ".avi", ".3gp")


def is_video_src(src):
    """آیا این نشانی یک ویدیوست؟ (گیف‌های تلگرامی mp4 ذخیره می‌شن)"""
    if not src:
        return False
    s = str(src).split("?")[0].split("#")[0].lower()
    return s.endswith(VID_SRC_EXT)


def loop_video(src, width=None, height=None, fit=None, radius=0,
               muted=True, autoplay=True, on_error=None, loop=True,
               on_position=None, on_duration=None, on_complete=None):
    """ویدیو برای گیف (بی‌صدا و تکرارشو) یا پخش‌کننده؛ بی‌پلاگین: None."""
    if not (HAS_VIDEO and src):
        return None
    try:
        vid = _fv.Video(
            playlist=[_fv.VideoMedia(resource=str(src))],
            playlist_mode=(_fv.PlaylistMode.LOOP if loop
                           else _fv.PlaylistMode.NONE),
            autoplay=autoplay, muted=muted, controls=None,
            fill_color="#00000000", wakelock=False,
            fit=fit or ft.BoxFit.COVER,
            width=width, height=height,
            on_error=on_error, on_position_change=on_position,
            on_duration_change=on_duration, on_complete=on_complete,
        )
    except Exception:                                   # pragma: no cover
        return None
    if not radius:
        return vid
    return ft.Container(vid, width=width, height=height,
                        border_radius=BR(radius, radius, radius, radius),
                        clip_behavior=ft.ClipBehavior.ANTI_ALIAS)


def inner_video(ctrl):
    """کنترلِ Videoِ درونِ چیزی که loop_video ساخته (شاید در Container باشد)."""
    if ctrl is None or not HAS_VIDEO:
        return None
    if isinstance(ctrl, _fv.Video):
        return ctrl
    inner = getattr(ctrl, "content", None)
    return inner if isinstance(inner, _fv.Video) else None


def gif_media(src, width=None, height=None, fit=None, radius=0):
    """گیف را درست نشان می‌دهد: mp4 با پخش‌کننده، تصویر با Image."""
    if is_video_src(src):
        v = loop_video(src, width=width, height=height, fit=fit, radius=radius)
        if v is not None:
            return v
    img = photo(src, width=width, height=height, fit=fit or ft.BoxFit.COVER,
                error=ft.Container(
                    _icon("gif", 22, P["sub"]), width=width, height=height,
                    alignment=AL.CENTER))
    if not radius:
        return img
    img.border_radius = BR(radius, radius, radius, radius)
    img.anti_alias = True
    return img


# ── چیپِ واکنش (ایموجی + شمارش) ──────────────────────
def media_tile(src, size=94, on_click=None, on_long_press=None, radius=12,
               badge=None, video=False):
    """کاشیِ مربعیِ مدیا (شبکه‌ی گیف‌ها) با هاورِ نرم و نشانِ گوشه."""
    img = None
    if video or is_video_src(src):
        img = loop_video(src, width=size, height=size,
                         fit=ft.BoxFit.COVER, radius=radius)
    if img is None:
        # ⚠️ اینجا محلِ کرشِ «برگه‌ی سنجاق» بود: پانزده عکسِ دوربین با
        # اندازه‌ی اصلی رمزگشایی می‌شد. `photo()` سقف می‌گذارد.
        img = photo(src, width=size, height=size, fit=ft.BoxFit.COVER,
                    radius=radius,
                    error=ft.Container(
                        _icon("gif", 22, P["sub"]), width=size, height=size,
                        alignment=AL.CENTER))
    layers = [img]
    if badge:
        layers.append(ft.Container(
            ft.Text(badge, size=fs(9), color="#ffffff",
                    font_family=FONT_MED),
            bottom=5, right=5, padding=PAD(5, 1, 5, 2),
            bgcolor=op(0.55, "#000000"),
            border_radius=BR(6, 6, 6, 6)))
    cell = ft.Container(
        ft.Stack(layers, width=size, height=size),
        width=size, height=size, border_radius=BR(radius, radius, radius, radius),
        animate_scale=anim(FAST, POP), scale=1.0, ink=bool(on_click),
        on_click=on_click,
    )

    def _h(e):
        cell.scale = 1.06 if e.data else 1.0
        try:
            cell.update()
        except Exception:
            pass
    if on_click:
        cell.on_hover = _h
    if on_long_press is None:
        return cell
    return ft.GestureDetector(cell, mouse_cursor=ft.MouseCursor.CLICK,
                              on_long_press=on_long_press,
                              on_secondary_tap=on_long_press)


def play_badge(size=54, icon="play"):
    """دایره‌ی «پخش» روی بندانگشتیِ ویدیو."""
    return ft.Container(
        _icon(icon, int(size * 0.42), "#ffffff"),
        width=size, height=size, alignment=AL.CENTER,
        border_radius=BR(size, size, size, size),
        bgcolor=op(0.42, "#000000"),
        border=ft.Border(*[ft.BorderSide(1.5, op(0.55, "#ffffff"))] * 4),
        animate_scale=anim(FAST, POP), scale=1.0,
    )


def tag_chip(text, icon=None, color=None):
    """برچسبِ کوچکِ گوشه‌ی مدیا (GIF / MP4 / …)."""
    col = color or "#ffffff"
    cells = []
    if icon:
        cells.append(_icon(icon, 11, col))
    cells.append(ft.Text(text, size=fs(9.5), color=col, font_family=FONT_MED))
    return ft.Container(
        ft.Row(cells, spacing=3, tight=True,
               vertical_alignment=ft.CrossAxisAlignment.CENTER),
        padding=PAD(6, 1, 6, 2), bgcolor=op(0.52, "#000000"),
        border_radius=BR(7, 7, 7, 7))


def reaction_chip(emoji, count, mine=False, on_click=None):
    """کپسولِ واکنش زیر پیام؛ واکنشِ خودم لبه‌ی رنگی داره."""
    c = ft.Container(
        ft.Row([ft.Text(emoji, size=fs(13)),
                ft.Text(str(count), size=fs(11), color=P["txt"],
                        font_family=FONT_MED)],
               spacing=4, tight=True,
               vertical_alignment=ft.CrossAxisAlignment.CENTER),
        padding=PAD(8, 3, 8, 4), border_radius=BR(12, 12, 12, 12),
        bgcolor=op(0.22, P["acc"]) if mine else op(0.10),
        border=(ft.Border(*[ft.BorderSide(1, op(0.55, P["acc"]))] * 4)
                if mine else None),
        on_click=on_click, ink=bool(on_click),
        animate_scale=anim(FAST, POP), scale=1.0, animate=anim(FAST, EASE),
    )

    def _h(e):
        c.scale = 1.10 if e.data else 1.0
        c.update()
    if on_click:
        c.on_hover = _h
    return c


# ── منوی شیشه‌ایِ شناور ──────────────────────────────
def menu_item(icon, text, on_click=None, danger=False):
    """یک ردیفِ منوی بافت‌شیشه‌ای."""
    col = P["acc2"] if danger else P["txt"]
    c = ft.Container(
        ft.Row([_icon(icon, 18, col), ft.Text(text, size=fs(13.5), color=col)],
               spacing=12, tight=True,
               vertical_alignment=ft.CrossAxisAlignment.CENTER),
        padding=PAD(14, 9, 18, 9), border_radius=BR(10, 10, 10, 10),
        on_click=on_click, ink=True, animate=anim(FAST, EASE),
    )
    hover_tint(c, hi=op(0.10))
    return c


def float_menu(*items, width=None):
    """قابِ منوی شناور (Reply/Forward/...)"""
    return glass(
        ft.Column(list(items), spacing=2, tight=True),
        radius=16, pad=PAD(6, 6, 6, 6), tint=0.14, sigma=34, edge=0.16,
        width=width,
    )


# ── فایل: آیکون، حجم، نوارِ پیشرفت ────────────────────
_FILE_ICONS = (
    ("gif", ("gif",)),
    ("image", ("png", "jpg", "jpeg", "webp", "bmp", "svg", "heic")),
    ("film", ("mp4", "mkv", "mov", "avi", "webm", "m4v")),
    ("music", ("mp3", "m4a", "wav", "ogg", "opus", "flac", "aac")),
    ("archive", ("zip", "rar", "7z", "tar", "gz", "xz", "iso")),
)


def file_icon_for(name):
    ext = (name or "").rsplit(".", 1)[-1].lower() if "." in (name or "") else ""
    for icon, exts in _FILE_ICONS:
        if ext in exts:
            return icon
    return "file"


def human_size(n):
    """حجمِ خوانا (بایت → KB/MB/GB)."""
    try:
        n = float(n or 0)
    except (TypeError, ValueError):
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" or n >= 10 else f"{n:.1f} {unit}"
        n /= 1024.0
    return ""


def progress_bar(value=None, color=None, height=3, width=None):
    """نوارِ پیشرفتِ نازکِ تلگرامی (value=None ⇒ نامعین)."""
    return ft.ProgressBar(
        value=value, height=height, width=width,
        color=color or P["acc"], bgcolor=op(0.18),
        border_radius=BR(3, 3, 3, 3),
    )


def file_card(name, size_text, icon=None, on_click=None, sub=None,
              progress=None, color=None, width=250, trailing=None):
    """کارتِ فایل: آیکونِ گرد + نام + حجم (+ نوارِ پیشرفت)."""
    col = color or P["txt"]
    rows = [ft.Text(name, size=fs(13.5), color=col, max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS, font_family=FONT_MED)]
    meta = " · ".join([x for x in (size_text, sub) if x])
    if meta:
        rows.append(ft.Text(meta, size=fs(11), color=op(0.70, col)))
    if progress is not None:
        rows.append(ft.Container(progress_bar(progress, color=col),
                                 margin=MG(0, 4, 0, 0)))
    disc = ft.Container(
        _icon(icon or file_icon_for(name), 20, "#ffffff"),
        width=40, height=40, border_radius=BR(40, 40, 40, 40),
        alignment=AL.CENTER, bgcolor=op(0.22, P["acc"]),
    )
    cells = [disc, ft.Column(rows, spacing=2, tight=True, expand=True)]
    if trailing is not None:
        cells.append(trailing)
    return ft.Container(
        ft.Row(cells, spacing=10,
               vertical_alignment=ft.CrossAxisAlignment.CENTER),
        width=width, on_click=on_click, ink=bool(on_click),
        border_radius=BR(12, 12, 12, 12), padding=PAD(4, 4, 8, 4),
    )
