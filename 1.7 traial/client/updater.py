"""
به‌روزرسانیِ درون‌برنامه‌ای — گرفتنِ نسخه‌ی تازه و نصبش، بی رفتن به مرورگر.

  • `check()`      → آگاهی‌نامه‌ی سرور، یا None اگر چیزی تازه نیست.
  • `download()`   → فایل را تکه‌تکه می‌گیرد (نوشتن روی دیسک در نخِ جدا،
                     تا حلقه‌ی رویدادِ رابط کاربری تکان نخورد).
  • `install()`    → ویندوز: اسکریپتِ جابه‌جایی + راه‌اندازیِ دوباره.
                     اندروید: تحویلِ APK به نصب‌کننده‌ی سیستم.

هرجا نصبِ خودکار نشد، `install()` دروغ نمی‌گوید و False برمی‌گرداند تا
لایه‌ی رابط بتواند مسیرِ فایل را به کاربر نشان دهد.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile

log = logging.getLogger("rafigram.updater")

_CHUNK = 1 << 18          # ۲۵۶ کیلوبایت
ANDROID = ("ANDROID_ROOT" in os.environ) or ("ANDROID_DATA" in os.environ)
WINDOWS = sys.platform.startswith("win")


def plat() -> str:
    """نامِ سکو در آگاهی‌نامه‌ی سرور."""
    if ANDROID:
        return "android"
    return "windows" if WINDOWS else ""


def parse(v) -> tuple[int, ...]:
    """«۱.۲.۳» → (1, 2, 3)؛ چیزِ بی‌ربط → (0,)."""
    s = re.sub(r"[^0-9.]", "", str(v or ""))
    parts = [p for p in s.split(".") if p != ""]
    return tuple(int(p) for p in parts) or (0,)


def newer(remote, local) -> bool:
    """آیا نسخه‌ی سرور از نسخه‌ی نصب‌شده بالاتر است؟"""
    a, b = parse(remote), parse(local)
    n = max(len(a), len(b))
    a += (0,) * (n - len(a))
    b += (0,) * (n - len(b))
    return a > b


def frozen() -> bool:
    """داخلِ exe اجرا می‌شویم؟ (فقط آن‌وقت جایگزینیِ فایل معنی دارد)"""
    return bool(getattr(sys, "frozen", False))


# ── پوشه‌ی اختصاصیِ رفیگرام ───────────────────────────────
#
# خواسته‌ی کاربر: «یک پوشه‌ی `/Downloads/Rafigram/` روی دستگاه ساخته شود تا
# مدیای دانلودی و فایلِ نصبِ به‌روزرسانی همه یک‌جا باشند.»
#
# ⚠️ صداقتِ لازم: از اندروید ۱۰ (API 29) به بعد یک برنامه‌ی معمولی *نمی‌تواند*
# در پوشه‌ی عمومیِ Download بنویسد، مگر مجوزِ «دسترسی به همه‌ی فایل‌ها»
# (`MANAGE_EXTERNAL_STORAGE`) را از صفحه‌ی تنظیمات گرفته باشد — و آن مجوز در
# manifestِ ما نیست. پس این تابع *امتحان می‌کند* و اگر نشد بی‌صدا می‌رود سرِ
# حافظه‌ی خصوصیِ خودِ برنامه، که همیشه نوشتنی است و FileProvider هم می‌شناسدش.
#
# آزمونِ نوشتنی‌بودن هم واقعی است نه `os.access`: روی اندروید `os.access` گاهی
# برای پوشه‌ای «بله» می‌دهد که ساختنِ فایل در آن رد می‌شود. پس یک فایلِ ریز
# می‌سازیم و پاکش می‌کنیم — تنها پاسخِ قابلِ اعتماد.
APP_FOLDER = "Rafigram"
_dl_cache: dict[str, str] = {}


def _usable(d: str) -> bool:
    """واقعاً می‌شود در این پوشه فایل ساخت؟"""
    try:
        os.makedirs(d, exist_ok=True)
        probe = os.path.join(d, ".rafi_write_test")
        with open(probe, "wb") as f:
            f.write(b"1")
        os.remove(probe)
        return True
    except Exception:
        return False


def _dl_roots() -> list[str]:
    """جاهایی که پوشه‌ی رفیگرام می‌تواند بنشیند — به ترتیبِ دلخواهی.

    ترتیبِ اندروید حاصلِ یک نکته‌ی مهم است: `provider_paths.xml`ِ قالب یک ریشه‌ی
    `external_files` با `path="."` دارد، یعنی *کلِ* `/storage/emulated/0`. پس
    هر مسیری زیرِ آن، هم برای کاربر در فایل‌منیجر دیدنی است و هم FileProvider
    می‌تواند به نصب‌کننده‌ی سیستم تحویلش دهد.

    و مهم‌تر: `…/Android/data/<بسته>/files` پوشه‌ی خودِ برنامه در حافظه‌ی
    بیرونی است — روی *همه‌ی* نسخه‌های اندروید بی هیچ مجوزی نوشتنی است، ولی
    همچنان زیرِ `/storage/emulated/0` است. یعنی وقتی دانلودِ عمومی نشد (که از
    اندروید ۱۰ بی مجوزِ «دسترسی به همه‌ی فایل‌ها» نمی‌شود)، این جایگزین هم
    نوشتنی است هم نصب‌شدنی. پناهِ آخر (`cache`/`data`ِ داخلی) هم کار می‌کند
    ولی کاربر با فایل‌منیجر نمی‌بیندش.
    """
    if plat() == "android":
        pkg = package_name()
        return ["/storage/emulated/0/Download", "/sdcard/Download",
                "/storage/self/primary/Download",
                f"/storage/emulated/0/Android/data/{pkg}/files",
                f"/sdcard/Android/data/{pkg}/files",
                os.getenv("FLET_APP_STORAGE_TEMP") or "",
                os.getenv("FLET_APP_STORAGE_DATA") or "",
                tempfile.gettempdir()]
    home = os.path.expanduser("~")
    return [os.path.join(home, "Downloads"), os.path.join(home, "Download"),
            home, tempfile.gettempdir()]


def downloads_dir() -> str:
    """`…/Download/Rafigram` اگر شد، وگرنه نزدیک‌ترین جای نوشتنی.

    نتیجه کش می‌شود: این تابع در هر ذخیره‌ی مدیا صدا زده می‌شود و آزمونِ
    نوشتن چند تماسِ دیسک است.
    """
    got = _dl_cache.get("dir")
    if got and os.path.isdir(got):
        return got
    for root in _dl_roots():
        if not root:
            continue
        d = os.path.join(root, APP_FOLDER)
        if _usable(d):
            _dl_cache["dir"] = d
            return d
    d = tempfile.gettempdir()          # آخرین پناه؛ همیشه هست
    _dl_cache["dir"] = d
    return d


def public_downloads() -> bool:
    """پوشه‌ی رفیگرام در دانلودِ *عمومی* است یا در حافظه‌ی خصوصیِ برنامه؟

    لایه‌ی رابط از این می‌پرسد تا پیامِ درست بدهد: «در پوشه‌ی دانلود ذخیره شد»
    یا «در حافظه‌ی برنامه ذخیره شد، برای دیدنش در پوشه‌ی دانلود مجوزِ دسترسی
    به همه‌ی فایل‌ها لازم است». دروغ نمی‌گوییم.
    """
    d = downloads_dir().replace("\\", "/").lower()
    return "/download" in d


def apk_name(version: str = "") -> str:
    """`Rafigram_v1.5.apk` — نامِ نسخه‌دار، خواسته‌ی کاربر.

    چرا مهم است: با نامِ ثابتِ `Rafigram.apk` هر دانلود روی قبلی می‌نوشت و
    اگر نصبِ خودکار نمی‌گرفت، کاربر نمی‌دانست فایلِ کنارِ دستش کدام نسخه است.

    نسخه از سرور می‌آید، پس فقط رقم و نقطه‌اش را نگه می‌داریم — نه برای زیبایی،
    که تا رشته‌ی بی‌ربطی (`../`، فاصله، حرفِ عجیب) به نامِ فایل راه پیدا نکند.
    """
    v = re.sub(r"[^0-9.]", "", str(version or ""))
    v = re.sub(r"\.+", ".", v).strip(".")          # «۱..۵.» → «۱.۵»
    return f"Rafigram_v{v}.apk" if v else "Rafigram.apk"


def normalize(info: dict | None) -> dict:
    """هر دو شکلِ آگاهی‌نامه را به یک شکل درمی‌آورد.

    شکلِ سرور (`/updates/latest`) کلیدهای تودرتوی `android`/`windows` دارد و
    شکلِ فایلِ ایستا (`latest.json` روی ورسل) کلیدهای تختِ `apk_url`/`exe_url`.
    هر کدام بیاید باید کار کند: اگر روزی یکی را دستی نوشتیم و آن یکی را
    فراموش کردیم، به‌روزرسانی نباید بی‌صدا بخوابد.
    """
    d = dict(info or {})
    if not d.get("notes"):
        d["notes"] = str(d.get("release_notes") or "")
    for plt, flat, size, sha in (("android", "apk_url", "apk_size", "apk_sha1"),
                                 ("windows", "exe_url", "exe_size", "exe_sha1")):
        cur = d.get(plt)
        if isinstance(cur, str) and cur:              # `"android": "http…"`
            cur = {"url": cur}
        if not (isinstance(cur, dict) and cur.get("url")) and d.get(flat):
            cur = {"url": str(d[flat]), "size": int(d.get(size) or 0),
                   "sha1": str(d.get(sha) or "")}
        d[plt] = cur if isinstance(cur, dict) else {}
    return d


async def _static_manifest() -> dict:
    """آگاهی‌نامه‌ی ایستا از دامنه‌ی وب — پشتوانه‌ی وقتی سرور نیست.

    سرورِ ریلوی ممکن است خواب یا نارس باشد، ولی فایلِ `latest.json` کنارِ
    خودِ سایت روی ورسل همیشه هست؛ پس به‌روزرسانی به تنها یک راه بند نیست.
    """
    import config as _cfg                             # هم‌پوشه‌ی خودِ کلاینت
    base = (getattr(_cfg, "DOWNLOAD_BASE", "") or "").rstrip("/")
    if not base:
        return {}
    try:
        import httpx
        async with httpx.AsyncClient(timeout=12, follow_redirects=True) as c:
            r = await c.get(f"{base}/latest.json")
            r.raise_for_status()
            data = r.json()
            return data if isinstance(data, dict) else {}
    except Exception as ex:
        log.info("latest.json گرفته نشد: %s", ex)
        return {}


async def check(api, local_version: str) -> dict | None:
    """آگاهی‌نامه را می‌گیرد و اگر تازه‌تر بود برمی‌گرداند، وگرنه None."""
    try:
        info = await api.latest_version()
    except Exception as ex:
        log.info("بررسیِ نسخه نشد: %s", ex)
        info = None
    if not (info or {}).get("version"):
        info = await _static_manifest()
    info = normalize(info)
    ver = info.get("version")
    if not ver or not newer(ver, local_version):
        return None
    p = plat()
    asset = (info.get(p) or {}) if p else {}
    if not asset.get("url"):
        return None
    info["asset"] = asset
    info["platform"] = p
    info["mandatory"] = bool(
        info.get("min_version") and newer(info["min_version"], local_version))
    return info


def dest_for(platform: str, version: str = "") -> str:
    """جایی که فایلِ دانلودی می‌نشیند.

    اندروید: پوشه‌ی اختصاصیِ `Rafigram` (`downloads_dir()`) با نامِ نسخه‌دار،
    تا اگر نصبِ خودکار نگرفت کاربر بتواند دستی همان‌جا پیدا و نصبش کند و
    بداند کدام نسخه است. اگر آن پوشه در حافظه‌ی خصوصیِ برنامه درآمده باشد،
    FileProvider هم می‌شناسدش (بخشِ `_content_uris`) پس نصبِ خودکار همچنان
    کار می‌کند.

    ویندوز: کنارِ خودِ exe (تا جایگزینی ساده باشد)، وگرنه پوشه‌ی موقت.
    """
    if platform == "android":
        return os.path.join(downloads_dir(), apk_name(version))
    name = "Rafigram-new.exe"
    if frozen():
        near = os.path.dirname(os.path.abspath(sys.executable))
        if os.access(near, os.W_OK):
            return os.path.join(near, name)
    return os.path.join(tempfile.gettempdir(), name)


async def download(api, url: str, dest: str, on_progress=None,
                   sha1: str = "") -> str:
    """فایل را می‌گیرد و درستیِ هشش را (اگر داده شده باشد) می‌سنجد."""
    tmp = dest + ".part"
    try:
        if os.path.exists(tmp):
            os.remove(tmp)
    except OSError:
        pass
    await api.download_url(url, tmp, on_progress=on_progress)
    if sha1:
        got = await asyncio.to_thread(_sha1, tmp)
        if got.lower() != sha1.lower():
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise ValueError("hash-mismatch")
    await asyncio.to_thread(_replace, tmp, dest)
    return dest


def _sha1(path: str) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def _replace(src: str, dst: str) -> None:
    try:
        os.replace(src, dst)
    except OSError:
        shutil.move(src, dst)


# ── نصب ────────────────────────────────────────────
_BAT = """@echo off
rem جایگزینیِ خودکارِ Rafigram.exe — تا پروسه‌ی قدیمی نبسته، صبر می‌کند.
setlocal
set OLD=%~1
set NEW=%~2
set /a n=0
:wait
timeout /t 1 /nobreak >nul 2>&1
move /y "%NEW%" "%OLD%" >nul 2>&1
if exist "%NEW%" (
  set /a n+=1
  if %n% LSS 30 goto wait
)
start "" "%OLD%"
del "%~f0" >nul 2>&1
"""


def _install_windows(new_exe: str) -> bool:
    """اسکریپتِ کوچکی می‌سازد که پس از بستهٔ‌شدنِ برنامه فایل را عوض کند."""
    if not frozen():
        # اجرای توسعه‌ای (python main.py) — چیزی برای جایگزینی نیست
        return False
    old = os.path.abspath(sys.executable)
    bat = os.path.join(tempfile.gettempdir(), "rafigram_update.bat")
    try:
        with open(bat, "w", encoding="ascii", newline="\r\n") as f:
            f.write(_BAT)
        flags = 0x00000008 | 0x08000000        # DETACHED_PROCESS | NO_WINDOW
        subprocess.Popen(["cmd", "/c", bat, old, os.path.abspath(new_exe)],
                         creationflags=flags, close_fds=True)
        return True
    except Exception as ex:
        log.warning("اسکریپتِ به‌روزرسانی اجرا نشد: %s", ex)
        return False


_PKG_FALLBACK = "ir.rafigram.rafigram"
_APK_MIME = "application/vnd.android.package-archive"
# FLAG_GRANT_READ_URI_PERMISSION (0x1) | FLAG_ACTIVITY_NEW_TASK (0x10000000)
_INTENT_FLAGS = "0x10000001"


def package_name() -> str:
    """`applicationId` برنامه — از نامِ پروسه‌ی خودمان.

    اندروید نامِ پروسه را همان نامِ بسته می‌گذارد و در `/proc/self/cmdline`
    می‌نویسد (پروسه‌های جانبی «بسته:نام» هستند، پس تا «:» را می‌گیریم). این
    را لازم داریم تا مرجعِ FileProviderِ خودمان را بسازیم؛ حدس‌زدنش خطاست،
    چون با هر تغییرِ `--org`/`--project` عوض می‌شود.
    """
    try:
        with open("/proc/self/cmdline", "rb") as f:
            raw = f.read().split(b"\0")[0].decode("utf-8", "replace")
        name = raw.strip().split(":")[0].strip()
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+", name):
            return name
    except OSError:
        pass
    return _PKG_FALLBACK


def _content_uris(apk: str) -> list[str]:
    """نشانی‌های `content://` معادلِ مسیرِ APK.

    قالبِ اندرویدِ فلت یک FileProvider با مرجعِ `<بسته>.provider` دارد و
    `res/xml/provider_paths.xml` سه ریشه تعریف می‌کند: `external_files`
    (کلِ حافظه‌ی بیرونی)، `files` (پوشه‌ی خصوصیِ برنامه) و `cache`.

    از پایتون به `FileProvider.getUriForFile()` دسترسی نداریم (نه JNI داریم
    نه کانالِ پلتفرم)، ولی لازم هم نیست: آن تابع کارش فقط همین نگاشتِ
    «مسیر ⇒ content://<مرجع>/<نامِ‌ریشه>/<مسیرِ‌نسبی>» است و ما همان را
    دستی می‌سازیم. اجازه‌ی خواندن را `--grant-read-uri-permission` می‌دهد.

    این لازم است چون از اندروید ۷ (API 24) نصب‌کننده‌ی سیستم نشانیِ `file://`
    را نمی‌پذیرد؛ نتیجه‌اش همان «فایل دانلود شد ولی نصب شروع نشد» بود.

    ⚠️ ترتیب اتفاقی نیست: اول حافظه‌ی بیرونی سنجیده می‌شود و اگر مسیر زیرِ آن
    بود، *همان‌جا تمام*. وگرنه مسیرِ `…/Android/data/<بسته>/files/x` هم به
    نشانه‌ی `/files/` می‌خورد و نگاشتی می‌سازد که به `getFilesDir()`ِ *داخلی*
    اشاره می‌کند — فایلی که آن‌جا نیست. آن نشانی «باز» می‌شود ولی نصب‌کننده
    فایل را نمی‌یابد، و ما اشتباهاً موفقیت گزارش می‌کردیم.
    """
    auth = package_name() + ".provider"
    p = apk.replace("\\", "/")
    out: list[str] = []

    def add(root: str, rel: str) -> None:
        rel = rel.strip("/")
        if rel:
            out.append(f"content://{auth}/{root}/{rel}")

    # حافظه‌ی بیرونی (پوشه‌ی دانلود، و پوشه‌ی خودِ برنامه در حافظه‌ی بیرونی)
    for base in ("/storage/emulated/0/", "/sdcard/", "/storage/self/primary/"):
        if p.startswith(base):
            add("external_files", p[len(base):])
            return out
    # پوشه‌ی خصوصی و کشِ برنامه: مسیرشان جایی «…/files/…» یا «…/cache/…» دارد
    for marker, root in (("/files/", "files"), ("/cache/", "cache")):
        i = p.find(marker)
        if i != -1:
            add(root, p[i + len(marker):])
    return out


def _install_android(apk: str) -> bool:
    """APK را به نصب‌کننده‌ی سیستم می‌سپارد (بی مرورگر، بی پوشه‌ی دانلود).

    از پایتون تنها راهِ فرستادنِ Intent همین `am start` است. به ترتیب امتحان
    می‌شود: `content://` (راهِ درست از اندروید ۷ به بعد) و بعد `file://`
    (برای اندرویدهای قدیمی‌تر). اگر هیچ‌کدام نگرفت، False برمی‌گردد و رابط
    مسیرِ فایل را به کاربر نشان می‌دهد — دروغ نمی‌گوییم.

    ⚠️ بی مجوزِ `REQUEST_INSTALL_PACKAGES` در AndroidManifest، سیستم از
    API 26 به بعد این Intent را رد می‌کند. مجوز از `.tmp/build_apk.sh`
    تزریق می‌شود.
    """
    apk = os.path.abspath(apk)
    tried = []
    for uri in _content_uris(apk) + ["file://" + apk]:
        for act in ("android.intent.action.VIEW",
                    "android.intent.action.INSTALL_PACKAGE"):
            cmd = ["am", "start", "-a", act, "-d", uri, "-t", _APK_MIME,
                   "-f", _INTENT_FLAGS, "--grant-read-uri-permission"]
            try:
                r = subprocess.run(cmd, capture_output=True, timeout=20)
            except Exception as ex:
                log.info("am اجرا نشد: %s", ex)
                return False            # خودِ `am` نیست؛ ادامه بی‌فایده است
            out = ((r.stdout or b"") + (r.stderr or b"")).decode(
                "utf-8", "replace").strip()
            low = out.lower()
            # «Warning: Activity not started, its current task has been
            # brought to the front» موفقیت است؛ تنها «Error:» شکست است.
            if r.returncode == 0 and "error:" not in low \
                    and "exception" not in low:
                log.info("نصب‌کننده باز شد: %s", uri.split("/")[2])
                return True
            tried.append(f"{act.rsplit('.', 1)[-1]}@{uri[:60]} → {out[:120]}")
    for line in tried:
        log.info("نصبِ خودکار نشد: %s", line)
    return False


def install(path: str) -> bool:
    """نصب/جایگزینی؛ True یعنی کار به سیستم سپرده شد."""
    p = plat()
    if p == "android":
        return _install_android(path)
    if p == "windows":
        return _install_windows(path)
    return False
