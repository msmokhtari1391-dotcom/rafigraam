"""
به‌روزرسانیِ درون‌برنامه‌ای — سرور می‌گوید تازه‌ترین نسخه چیست و فایلش کجاست.

منبعِ اطلاعات به‌ترتیبِ اولویت:
  ۱) فایلِ `updates.json` در `DATA_DIR` — با ویرایشِ همین فایل (بی دیپلویِ
     دوباره) می‌توانی نسخه‌ی تازه اعلام کنی.
  ۲) متغیرهای محیطی: `UPDATE_VERSION`, `UPDATE_NOTES`, `UPDATE_MIN_VERSION`,
     `UPDATE_APK_URL`, `UPDATE_EXE_URL`, `UPDATE_WEB_URL`, `GITHUB_REPO`.
  ۳) خودِ فایل‌های آپلودشده در `DATA_DIR/releases/` (Rafigram.apk / .exe) —
     اگر آنجا باشند، همین سرور با `/updates/file/{plat}` تحویلشان می‌دهد.

هیچ‌کدام لازم نیست؛ اگر چیزی تنظیم نشده باشد پاسخ `{"version": null}` است و
کلاینت بی‌صدا رد می‌شود.
"""
import hashlib
import json
import logging
import os
import re
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .config import settings

log = logging.getLogger("rafigram.updates")
router = APIRouter(prefix="/updates", tags=["updates"])

# فایلِ باینریِ هر سکو، اگر خودِ سرور میزبانش باشد
FILES = {"android": "Rafigram.apk", "windows": "Rafigram.exe"}
APK_MIME = "application/vnd.android.package-archive"
MIME = {"android": APK_MIME, "windows": "application/vnd.microsoft.portable-executable"}
_HASH_CACHE: dict[str, tuple[float, int, str]] = {}   # path → (mtime, size, sha1)


class Asset(BaseModel):
    url: str
    size: int = 0
    sha1: str = ""


class UpdateOut(BaseModel):
    version: Optional[str] = None
    notes: str = ""
    min_version: str = ""        # پایین‌تر از این ⇒ ارتقا اجباری
    mandatory: bool = False      # کلاینت خودش با مقایسه‌ی نسخه پر می‌کند
    android: Optional[Asset] = None
    windows: Optional[Asset] = None
    web_url: str = ""


def _releases_dir() -> Path:
    d = settings.data_dir / "releases"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return d


def _sha1_of(p: Path) -> str:
    """SHA1 با حافظه‌ی کوچک — فایلِ ۷۰مگابایتی هر بار دوباره هش نمی‌شود."""
    try:
        stt = p.stat()
    except OSError:
        return ""
    key = str(p)
    hit = _HASH_CACHE.get(key)
    if hit and hit[0] == stt.st_mtime and hit[1] == stt.st_size:
        return hit[2]
    h = hashlib.sha1()
    try:
        with open(p, "rb") as f:
            for blk in iter(lambda: f.read(1 << 20), b""):
                h.update(blk)
    except OSError:
        return ""
    dig = h.hexdigest()
    _HASH_CACHE[key] = (stt.st_mtime, stt.st_size, dig)
    return dig


def _local_asset(plat: str, base: str) -> Optional[Asset]:
    p = _releases_dir() / FILES[plat]
    if not p.is_file():
        return None
    return Asset(url=f"{base}/updates/file/{plat}", size=p.stat().st_size,
                 sha1=_sha1_of(p))


def _gh_url(repo: str, tag: str, name: str) -> str:
    return f"https://github.com/{repo}/releases/download/{tag}/{name}"


def _from_json() -> dict:
    p = settings.data_dir / "updates.json"
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as ex:                       # فایلِ خراب برنامه را نمی‌خواباند
        log.warning("updates.json خوانده نشد: %s", ex)
        return {}


def _env_ver() -> str:
    v = (os.getenv("UPDATE_VERSION", "") or "").strip().lstrip("vV")
    return v


def _clean(v) -> str:
    """نسخه را به شکلِ «۱.۲.۳» پاک می‌کند؛ چیزِ بی‌ربط ⇒ رشته‌ی خالی."""
    s = str(v or "").strip().lstrip("vV")
    return s if re.fullmatch(r"\d+(\.\d+)*", s) else ""


def _base_url(req: Request) -> str:
    """آدرسِ عمومیِ سرور — با احترام به پروکسیِ Railway (x-forwarded-*)."""
    env = (os.getenv("PUBLIC_BASE_URL", "") or "").strip().rstrip("/")
    if env:
        return env
    host = (req.headers.get("x-forwarded-host")
            or req.headers.get("host") or "").strip()
    if not host:
        return str(req.base_url).rstrip("/")
    proto = (req.headers.get("x-forwarded-proto") or req.url.scheme
             or "https").split(",")[0].strip()
    return f"{proto}://{host}"


def build_manifest(base: str) -> UpdateOut:
    """آگاهی‌نامه‌ی نسخه — از updates.json، env، و فایل‌های محلی."""
    j = _from_json()
    ver = _clean(j.get("version")) or _clean(_env_ver())
    notes = str(j.get("notes") or os.getenv("UPDATE_NOTES", "") or "")
    minv = _clean(j.get("min_version")) or _clean(os.getenv("UPDATE_MIN_VERSION", ""))
    web = str(j.get("web_url") or os.getenv("UPDATE_WEB_URL", "") or "").strip()

    def pick(plat: str, env_key: str) -> Optional[Asset]:
        raw = j.get(plat)
        if isinstance(raw, dict) and raw.get("url"):
            return Asset(url=str(raw["url"]), size=int(raw.get("size") or 0),
                         sha1=str(raw.get("sha1") or ""))
        if isinstance(raw, str) and raw:
            return Asset(url=raw)
        url = (os.getenv(env_key, "") or "").strip()
        if url:
            return Asset(url=url)
        repo = (os.getenv("GITHUB_REPO", "") or "").strip().strip("/")
        if repo and ver:
            return Asset(url=_gh_url(repo, f"v{ver}", FILES[plat]))
        return _local_asset(plat, base)

    apk = pick("android", "UPDATE_APK_URL")
    exe = pick("windows", "UPDATE_EXE_URL")
    if not ver:
        # نسخه اعلام نشده؟ اگر فایلِ محلی هست، بی‌نسخه چیزی نمی‌گوییم.
        return UpdateOut()
    return UpdateOut(version=ver, notes=notes, min_version=minv,
                     android=apk, windows=exe, web_url=web)


@router.get("/latest", response_model=UpdateOut)
async def latest(request: Request):
    """تازه‌ترین نسخه — بی نیاز به ورود، تا صفحه‌ی ورود هم بتواند بپرسد."""
    return build_manifest(_base_url(request))


@router.get("/file/{plat}")
async def file(plat: str):
    """تحویلِ باینریِ میزبانی‌شده روی همین سرور (اگر گذاشته باشی)."""
    if plat not in FILES:
        raise HTTPException(404, "سکوی ناشناخته")
    p = _releases_dir() / FILES[plat]
    if not p.is_file():
        raise HTTPException(404, "فایلی برای این سکو گذاشته نشده")
    return FileResponse(str(p), media_type=MIME[plat], filename=FILES[plat],
                        headers={"Cache-Control": "no-cache"})
