"""
پیکربندی کلاینت — آدرس سرور.

سه راه برای تعیینِ آدرس، به همین ترتیبِ اولویت:
  ۱) متغیرِ محیطی `RAFIGRAM_API` (برای توسعه؛ بر همه‌چیز مقدم است)
  ۲) آدرسی که کاربر داخلِ برنامه وارد کرده (در تنظیمات ذخیره می‌شود)
  ۳) `DEFAULT_API_BASE` پایین — دامنه‌ی ورسل (که خودش به سرور می‌رساند)

روی گوشی (APK) آدرسِ `127.0.0.1` بی‌معنی‌ست، پس آدرس باید در زمانِ اجرا
قابلِ عوض‌کردن باشد؛ `set_base()` همین کار را می‌کند و همه‌ی مصرف‌کننده‌ها
از `api_base()` / `ws_base()` می‌خوانند (نه از یک مقدارِ ثابتِ لحظه‌ی import).
"""
import os

# سرورِ زنده — برنامه بی هیچ تنظیمی همین را می‌گیرد (هم روی گوشی، هم ویندوز).
# این آدرسِ ورسل است، نه ریلوی: ریلوی در ایران فیلتر است و برنامه با آن بالا
# نمی‌آمد. ورسل خودش درخواست‌ها را (با کارگرِ کلادفلر) به ریلوی پیش می‌دهد،
# و صفحه‌ی وب هم از همین دامنه سرو می‌شود ⇒ همه‌ی نسخه‌ها یک آدرس دارند.
DEFAULT_API_BASE = "https://rafigram.vercel.app"

# میزبانِ فایل‌های به‌روزرسانی و آگاهی‌نامه‌ی نسخه (`latest.json`). همان
# دامنه‌ی وب است؛ جدا نگهش می‌داریم تا اگر کاربر سرورِ دیگری وارد کرد،
# دانلودِ نسخه‌ی تازه هنوز از جای درست بیاید.
DOWNLOAD_BASE = "https://rafigram.vercel.app"

# ── سوکتِ ریل‌تایم: جدا از نشانیِ HTTP ─────────────────────────────
# ⚠️ ورسل سوکت را پیش نمی‌دهد. لایه‌ی بازنویسیِ `vercel.json` فقط HTTP را
# پیش می‌برد و درخواستِ ارتقا به WebSocket را با ۴۰۴ رد می‌کند؛ سنجیده شد:
#
#     wss://rafigram.vercel.app/ws                 → HTTP 404
#     wss://winter-star-8da6…workers.dev/ws        → 101 (و پاسخِ سرور)
#
# یعنی سوکتِ برنامه *هرگز* وصل نمی‌شد — نه روی گوشی، نه ویندوز، نه وب. همین
# یک چیز هر دو نشانه را توضیح می‌دهد: «پیامِ تازه تا از گفت‌وگو بیرون نروم
# نمی‌آید» و «حضور همیشه ساعت نشان می‌دهد نه آنلاین». کارگرِ کلادفلر همان
# ارتقا را درست به ریلوی می‌رساند، پس سوکت مستقیم به کارگر می‌رود و بارِ
# HTTP همان‌طور از ورسل می‌گذرد (که در ایران بی‌فیلتر است).
WS_PROXY_BASE = "wss://winter-star-8da6.nova-ea150d.workers.dev"

# سرورِ روی همین کامپیوتر — دکمه‌ی «سرور محلی» در پنجره‌ی آدرس، و
# پایه‌ی مجموعه‌های آزمون که نباید به سرورِ واقعی دست بزنند.
LOCAL_API_BASE = "http://127.0.0.1:8000"

ENV_BASE = (os.getenv("RAFIGRAM_API") or "").strip()


def normalize(url: str) -> str:
    """آدرسِ واردشده‌ی کاربر را تمیز می‌کند.

    «rafigram.up.railway.app» ⇒ «https://rafigram.up.railway.app»
    (Railway همیشه https می‌دهد، پس پیش‌فرضِ بی‌پیشوند https است.)
    """
    url = (url or "").strip().replace("‌", " ").strip()
    url = url.strip("/ \t\r\n")
    if not url:
        return ""
    low = url.lower()
    if not low.startswith(("http://", "https://")):
        # localhost و آی‌پیِ محلی https ندارند
        host = url.split("/")[0].split(":")[0].lower()
        local = host in ("localhost", "127.0.0.1", "0.0.0.0") or host.startswith(
            ("192.168.", "10.", "172.16.", "172.17.", "172.18.", "172.19."))
        url = ("http://" if local else "https://") + url
    return url.rstrip("/")


def ws_of(url: str) -> str:
    """آدرسِ WebSocket از روی آدرسِ HTTP (http→ws و https→wss)."""
    if url.startswith("https://"):
        return "wss://" + url[len("https://"):]
    if url.startswith("http://"):
        return "ws://" + url[len("http://"):]
    return "ws://" + url


API_BASE_URL = normalize(ENV_BASE or DEFAULT_API_BASE)
WS_BASE_URL = ws_of(API_BASE_URL)


def api_base() -> str:
    """آدرسِ جاریِ HTTP سرور (همیشه بی‌اسلشِ آخر)."""
    return API_BASE_URL


def ws_base() -> str:
    """آدرسِ جاریِ WebSocket سرور."""
    return WS_BASE_URL


def ws_candidates() -> list[str]:
    """نشانی‌های سوکت، به ترتیبِ اولویت؛ اولی که وصل شود برنده است.

    فهرست است نه یک نشانیِ تنها، چون هیچ‌کدام نباید تکْ‌نقطه‌ی شکست باشد:
    نشانیِ HTTP (ورسل) ارتقای سوکت را رد می‌کند، و نشانیِ کارگر روزی ممکن
    است عوض شود. سرورِ دست‌سازِ کاربر هم باید بتواند خودش سوکت بدهد.
    """
    out: list[str] = []

    def add(u: str) -> None:
        u = (u or "").rstrip("/")
        if u and u not in out:
            out.append(u)

    own = ws_of(API_BASE_URL)
    if is_default():
        add(WS_PROXY_BASE)       # تنها راهِ سنجیده‌شده‌ی سوکت
        add(own)                 # اگر روزی ورسل سوکت را هم پیش داد
    else:
        add(own)                 # سرورِ خودِ کاربر/محلی: مستقیم است
        add(WS_PROXY_BASE)
    return out


def is_default() -> bool:
    """آدرس هنوز همان سرورِ محلیِ پیش‌فرض است؟"""
    return API_BASE_URL == normalize(DEFAULT_API_BASE)


def http_candidates() -> list[str]:
    """نشانی‌های HTTP، به ترتیبِ اولویت — برای سرویسِ اعلانِ بومیِ اندروید.

    همان منطقِ `ws_candidates()` ولی برای HTTP، و با یک تفاوتِ مهم: وقتی
    نشانی همان پیش‌فرض است، *کارگرِ کلادفلر جلو می‌افتد* نه ورسل. چون سرویسِ
    اعلان یک درخواستِ ۲۵ ثانیه‌ای باز نگه می‌دارد (long-poll) و پروکسیِ ورسل
    برای پاسخِ دیرهنگام مهلتِ سخت‌گیرانه‌تری دارد؛ کارگر همان درخواست را
    بی‌مشکل نگه می‌دارد و در ایران هم باز است (سوکتِ برنامه از همان می‌رود).
    ورسل به‌عنوان راهِ دومّ می‌ماند، پس اگر روزی کارگر عوض شد اعلان قطع
    نمی‌شود.
    """
    out: list[str] = []

    def add(u: str) -> None:
        u = (u or "").rstrip("/")
        if u and u not in out:
            out.append(u)

    proxy = WS_PROXY_BASE
    if proxy.startswith("wss://"):
        proxy = "https://" + proxy[len("wss://"):]
    elif proxy.startswith("ws://"):
        proxy = "http://" + proxy[len("ws://"):]
    if is_default():
        add(proxy)
        add(API_BASE_URL)
    else:
        add(API_BASE_URL)        # سرورِ خودِ کاربر/محلی: مستقیم است
        add(proxy)
    return out


def page_origin(url: str = "") -> str:
    """اگر داخلِ مرورگر اجرا می‌شویم، خودِ دامنه‌ی صفحه (وگرنه رشته‌ی خالی).

    نسخه‌ی وب از همان دامنه‌ای سرو می‌شود که API را هم پیش می‌دهد (ورسل ⇢
    سرور). پس بهترین آدرسِ سرور برای مرورگر «همین دامنه» است، نه آدرسِ
    ثابتِ ریلوی: نه فیلتر می‌شود، نه CORS دارد، و اگر روزی دامنه عوض شود
    باز هم کار می‌کند.

    اول `page.url` (فلت خودش آدرسِ صفحه را به پایتون می‌دهد) و اگر نبود،
    `js.location` — پایتونِ وب داخلِ Web Worker است، پس `window` ممکن است
    نباشد ولی `location` هست.
    """
    src = (url or "").strip()
    if not src:
        try:
            import js                              # فقط در مرورگر
        except Exception:
            js = None
        for get in (lambda: js.window.location.href,
                    lambda: js.location.href):
            if js is None:
                break
            try:
                src = str(get())
                break
            except Exception:
                continue
    if not src.lower().startswith(("http://", "https://")):
        return ""
    scheme, _, rest = src.partition("://")
    host = rest.split("/")[0]
    return f"{scheme.lower()}://{host}".rstrip("/") if host else ""


def set_base(url: str) -> str:
    """آدرسِ سرور را در زمانِ اجرا عوض می‌کند و آدرسِ نهایی را برمی‌گرداند.

    اگر `RAFIGRAM_API` ست شده باشد، همان برنده است (تا اجرای توسعه‌ای
    با آدرسِ ذخیره‌شده‌ی کاربر قاطی نشود).
    """
    global API_BASE_URL, WS_BASE_URL
    if ENV_BASE:
        return API_BASE_URL
    url = normalize(url) or normalize(DEFAULT_API_BASE)
    API_BASE_URL, WS_BASE_URL = url, ws_of(url)
    return API_BASE_URL
