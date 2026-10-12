"""
دیپلویِ رفیگرام — ساخت، هش، کامیت، پوش، و ریلیزِ گیت‌هاب.

  python tools/deploy.py --version 1.1 --exe --apk --commit --push --release

هر گام جداست؛ چیزی که نخواسته‌ای اجرا نمی‌شود. بی `--push`/`--release` هیچ
چیزی بیرون از این کامپیوتر نمی‌رود.

ترتیبِ ساخت مهم است: نخست EXE و بعد APK، چون `flet pack` پوشه‌ی `build/` و
هر فایلِ غیرِ exe در `dist/` را پاک می‌کند.

توکنِ گیت‌هاب فقط از env خوانده می‌شود (`GITHUB_TOKEN`) و هیچ‌جا چاپ نمی‌شود.
مخزن از `GITHUB_REPO` یا از `git remote get-url origin` درمی‌آید.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
VENV = ROOT / ".venv" / "Scripts"
FLET = VENV / "flet.exe"
PY = VENV / "python.exe"
APK_SRC = ROOT / "build" / "apk"
EXE_NAME = "Rafigram.exe"
APK_NAME = "Rafigram.apk"
MAX_APK = 200 * 1024 * 1024        # سقفِ توافقیِ حجمِ APK

# دامنه‌ای که فایل‌های دانلود و `latest.json` از آن سرو می‌شوند. همان دامنه‌ی
# وب است (ورسل) و باید با `client/config.py:DOWNLOAD_BASE` یکی باشد، وگرنه
# برنامه آگاهی‌نامه را از جایی می‌خواند که ما در آن چیزی نگذاشته‌ایم.
DOWNLOAD_BASE = "https://rafigram.vercel.app"


def log(msg: str) -> None:
    print(f"[deploy] {msg}", flush=True)


def run(cmd, cwd=ROOT, check=True, quiet=False):
    """اجرای فرمان با خروجیِ زنده؛ خطا ⇒ توقف (مگر check=False)."""
    if not quiet:
        log("$ " + " ".join(str(c) for c in cmd))
    r = subprocess.run([str(c) for c in cmd], cwd=str(cwd))
    if check and r.returncode != 0:
        raise SystemExit(f"فرمان شکست خورد ({r.returncode})")
    return r.returncode


def sha1_file(p: Path) -> str:
    h = hashlib.sha1()
    with open(p, "rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()
def cur_version() -> str:
    """`APP_VERSION` از client/main.py."""
    txt = (ROOT / "client" / "main.py").read_text("utf-8")
    m = re.search(r'^APP_VERSION\s*=\s*"([^"]+)"', txt, re.M)
    return m.group(1) if m else "0.0"


def cur_build() -> int | None:
    """شماره‌ی بیلد از `client/main.py` (APP_BUILD) — تای‌بریکِ به‌روزرسانی.

    نسخه که عوض نمی‌شود (مثلاً ۱.۷ → ۱.۷)، تنها بیلد است که آپدیتِ جدید را
    از قدیم جدا می‌کند: کاربر «منتشر کردم ولی می‌گه آپدیت نیومده» دقیقاً
    برای همین بود که latest.json فقط `version` داشت و هر دو یکسان بودند.
    """
    txt = (ROOT / "client" / "main.py").read_text("utf-8")
    m = re.search(r'^APP_BUILD\s*=\s*(\d+)', txt, re.M)
    return int(m.group(1)) if m else None


def bump(ver: str, build_no: int | None = None) -> None:
    """نسخه را در کلاینت و اسکریپتِ APK هم‌زمان می‌نویسد.

    ⚠️ `APP_BUILD` در `client/main.py` هم همین‌جا ست می‌شود — همان عددی که
    `updater.check(api, APP_VERSION, APP_BUILD)` با `build` داخلِ
    `latest.json` می‌سنجد. پیش از این فقط `build_apk.sh` به‌روز می‌شد و
    `APP_BUILD` روی ۹ مانده بود؛ نتیجه: برنامه‌ی بیلدِ ۱۱ خودش را «بیلدِ ۹»
    می‌دید و سرِ هر بررسی می‌گفت «آپدیت آمده» — همان باگِ گزارش‌شده.
    """
    mp = ROOT / "client" / "main.py"
    txt = mp.read_text("utf-8")
    new = re.sub(r'^APP_VERSION\s*=\s*"[^"]+"',
                 f'APP_VERSION = "{ver}"', txt, count=1, flags=re.M)
    if build_no is not None:
        new = re.sub(r'^APP_BUILD\s*=\s*\d+',
                     f'APP_BUILD = {build_no}', new, count=1, flags=re.M)
    if new != txt:
        mp.write_text(new, "utf-8")
        log(f"APP_VERSION → {ver}"
            + (f" (APP_BUILD → {build_no})" if build_no else ""))
    sh = ROOT / ".tmp" / "build_apk.sh"
    if not sh.is_file():
        return
    s = sh.read_text("utf-8")
    trio = ver if ver.count(".") == 2 else (ver + ".0" * (2 - ver.count(".")))
    s2 = re.sub(r"--build-version \S+", f"--build-version {trio}", s)
    if build_no is not None:
        s2 = re.sub(r"--build-number \S+", f"--build-number {build_no}", s2)
    if s2 != s:
        sh.write_text(s2, "utf-8")
        log(f"build_apk.sh → {trio}" + (f" ({build_no})" if build_no else ""))


# فایل‌هایی که باید از جاروی `flet pack` جان سالم ببرند. مسیرها نسبت به
# `dist/web` است. → `_keep_web()` / `_restore_web()`
WEB_KEEP = ("vercel.json", ".vercel/project.json")


def _keep_web() -> dict:
    """محتوای فایل‌های ماندنیِ `dist/web` را در حافظه برمی‌دارد."""
    got = {}
    for rel in WEB_KEEP:
        p = DIST / "web" / rel
        if p.is_file():
            got[rel] = p.read_bytes()
    return got


def _restore_web(saved: dict) -> None:
    """همان‌ها را سرِ جایشان برمی‌گرداند (پوشه‌ها را هم می‌سازد)."""
    for rel, data in (saved or {}).items():
        p = DIST / "web" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    if saved:
        log("پیوندِ ورسل و vercel.json پس از pack برگشتند")


def scrub_web(silent: bool = False) -> None:
    """`dist/web/.env.local` را پاک می‌کند.

    `vercel link`/`vercel env pull` این فایل را می‌سازد و درونش توکنِ OIDC
    است. هر چه در `dist/web` باشد به‌شکلِ فایلِ ایستا سرو می‌شود ⇒ اگر بماند،
    توکن روی اینترنت عمومی می‌نشیند. پس پیش از هر انتشار می‌رود.
    """
    p = DIST / "web" / ".env.local"
    if p.is_file():
        p.unlink()
        if not silent:
            log("⚠️ dist/web/.env.local پاک شد (توکنِ OIDC، نباید منتشر شود)")


def build_exe() -> Path:
    """EXE با flet pack — نخستین گام، چون dist را جارو می‌کند."""
    if not FLET.is_file():
        raise SystemExit("flet.exe در .venv پیدا نشد")
    # `flet pack` هر فایلِ غیرِ exe در `dist/` را می‌برد — از جمله پیوندِ
    # پروژه‌ی ورسل. `build_web()` بعداً همین را نگه می‌دارد، ولی تا آن‌وقت
    # دیگر چیزی نمانده تا نگه دارد؛ پس اینجا خودمان برش می‌داریم و برمی‌گردانیم.
    # بی این کار `vercel deploy` بارِ بعد می‌پرسد کدام پروژه و پروژه‌ی
    # اشتباهیِ «web» ساخته می‌شود.
    saved = _keep_web()
    run([FLET, "pack", "client/main.py", "-n", "Rafigram",
         "-i", "client/assets/icon.ico",
         "--add-data", "client/assets:assets",
         "--hidden-import", "flet_video",
         "--hidden-import", "flet_audio",
         "--hidden-import", "flet_audio_recorder", "-y"])
    _restore_web(saved)
    exe = DIST / EXE_NAME
    if not exe.is_file():
        raise SystemExit("EXE ساخته نشد")
    log(f"EXE {exe.stat().st_size / 1e6:.1f} MB")
    return exe
def build_apk() -> Path:
    """APK با اسکریپتِ آماده (قالبِ وصله‌خورده + arm64)."""
    sh = ROOT / ".tmp" / "build_apk.sh"
    if not sh.is_file():
        raise SystemExit(".tmp/build_apk.sh نیست")
    run(["bash", str(sh)])
    cands = sorted(APK_SRC.rglob("*.apk"), key=lambda p: p.stat().st_size,
                   reverse=True)
    if not cands:
        raise SystemExit("APK ساخته نشد")
    src = cands[0]
    DIST.mkdir(parents=True, exist_ok=True)
    dst = DIST / APK_NAME
    dst.write_bytes(src.read_bytes())
    size = dst.stat().st_size
    log(f"APK {size / 1e6:.1f} MB ← {src.name}")
    if size > MAX_APK:
        raise SystemExit(f"APK از سقفِ {MAX_APK / 1e6:.0f} مگابایت گذشت")
    (DIST / (APK_NAME + ".sha1")).write_text(sha1_file(dst) + "\n", "ascii")
    return dst


def build_web() -> Path:
    """نسخه‌ی وب (PWA) — خروجی در dist/web."""
    out = ROOT / "build" / "web"
    # مثلِ APK از اسکریپتِ محیط‌دار رد می‌شویم: بی `FLUTTER_ROOT`، فلت خودش
    # فلاتر را دانلود می‌کند و پشتِ فیلتر zipِ نصفه می‌گیرد (BadZipFile).
    sh = ROOT / ".tmp" / "build_web.sh"
    if sh.is_file():
        run(["bash", str(sh), "build/web"])
    else:
        run([FLET, "build", "web", "client", "-o", str(out),
             "--project", "rafigram", "--product", "Rafigram",
             "--org", "ir.rafigram", "--company", "Rafigram",
             "--copyright", "Rafigram",
             "--pwa-background-color", "#0b0b10",
             "--pwa-theme-color", "#0b0b10",
             "--route-url-strategy", "path",
             # هیچ‌چیز از CDN: پایتونِ مرورگر (Pyodide ~۱۵ مگ)، CanvasKit و
             # فونت‌ها همه از دامنه‌ی خودمان. با CDN، در ایران jsdelivr/gstatic
             # بگیر‌نگیر است و برنامه‌ی وب روی گوشی تا ابد بالا نمی‌آمد (و در
             # سافاریِ آیفون صفحه سفید می‌ماند).
             "--no-cdn",
             "--no-rich-output", "--yes"])
    idx = out / "index.html"
    if not idx.is_file():
        raise SystemExit("خروجیِ وب ساخته نشد")
    # وصله‌های PWA (آیکون‌ها، متاهای iOS، مانیفستِ فارسی)
    run([PY, str(ROOT / "tools" / "pwa.py"), str(out)])
    web_dist = DIST / "web"
    # پیکربندیِ میزبانی (پیش‌دادنِ درخواست‌ها به پراکسی + حافظه‌ی مرورگر) داخلِ
    # `dist/web` می‌نشیند، ولی همین پوشه پایین‌تر تا ته پاک می‌شود ⇒ اگر فقط
    # آنجا باشد، با هر ساخت از دست می‌رفت. سرچشمه‌اش `web/vercel.json` است؛
    # و اگر آن نبود، نسخه‌ی دست‌نویسِ خودِ کاربر را برمی‌گردانیم.
    host_src = ROOT / "web" / "vercel.json"
    host_old = (web_dist / "vercel.json").read_bytes() \
        if (web_dist / "vercel.json").is_file() else None
    # پیوندِ پروژه‌ی ورسل (`.vercel/project.json`) هم همین‌جاست و با پاک‌شدنِ
    # پوشه می‌رفت ⇒ `vercel deploy` بارِ بعد می‌پرسید کدام پروژه و می‌شد
    # اشتباهی پروژه‌ی تازه ساخت. شناسه است، نه رمز؛ نگه‌داشتنش بی‌خطر است.
    link_old = (web_dist / ".vercel" / "project.json").read_bytes() \
        if (web_dist / ".vercel" / "project.json").is_file() else None
    if web_dist.exists():
        for p in sorted(web_dist.rglob("*"), reverse=True):
            p.unlink() if p.is_file() else p.rmdir()
    web_dist.mkdir(parents=True, exist_ok=True)
    for p in out.rglob("*"):
        rel = p.relative_to(out)
        tgt = web_dist / rel
        if p.is_dir():
            tgt.mkdir(parents=True, exist_ok=True)
        else:
            tgt.parent.mkdir(parents=True, exist_ok=True)
            tgt.write_bytes(p.read_bytes())
    if host_src.is_file():
        (web_dist / "vercel.json").write_bytes(host_src.read_bytes())
        log("vercel.json از web/ کپی شد")
    elif host_old is not None:
        (web_dist / "vercel.json").write_bytes(host_old)
        log("vercel.json (نسخه‌ی قبلی) نگه داشته شد")
    if link_old is not None:
        (web_dist / ".vercel").mkdir(exist_ok=True)
        (web_dist / ".vercel" / "project.json").write_bytes(link_old)
        log(".vercel/project.json نگه داشته شد")
    log(f"WEB → {web_dist}")
    scrub_web(silent=True)      # توکنِ OIDC نه در پوشه بماند، نه در zip
    # یک zip هم می‌سازیم تا بشود همان را به ریلیز چسباند یا روی هر میزبانِ
    # فایلِ ساده باز کرد (همان چیزی که workflow گیت‌هاب هم می‌سازد).
    import shutil as _sh
    _sh.make_archive(str(DIST / "Rafigram-web"), "zip", str(web_dist))
    log(f"ZIP {(DIST / 'Rafigram-web.zip').stat().st_size / 1e6:.1f} MB")
    return web_dist
def manifest(ver: str, repo: str, notes: str,
             base: str = DOWNLOAD_BASE, build: int | None = None) -> dict:
    """`dist/latest.json` — هم شکلِ سرور، هم شکلِ تختِ فایلِ ایستا.

    کلاینت کلیدهای تودرتوی `android`/`windows` را می‌خواند (با حجم و هش، تا
    دانلودِ نصفه را بگیرد) ولی `apk_url`/`exe_url`/`release_notes` هم نوشته
    می‌شود تا فایل با چشمِ آدم هم خوانا باشد و هر ابزارِ دیگری هم بتواند
    بخواندش. `client/updater.py` هر دو شکل را می‌فهمد.

    `build` شماره‌ی بیلدِ تکمیلی است: وقتی نسخه چندان عوض نمی‌شود، همین
    عدد است که به‌روزرسانیِ تازه را از کهنه جدا می‌کند.

    آدرس‌ها روی دامنه‌ی ورسل‌اند، نه گیت‌هاب: گیت‌هاب در ایران بگیرنگیر است و
    فایلِ به‌روزرسانی باید از همان دامنه‌ای بیاید که خودِ برنامه با آن حرف
    می‌زند.
    """
    base = (base or "").rstrip("/")
    man = {"version": ver, "notes": notes, "release_notes": notes,
           "min_version": "", "web_url": base}
    build = build if build is not None else cur_build()
    if build is not None:
        man["build"] = build
    flat = {"android": "apk", "windows": "exe"}
    for key, name in (("android", APK_NAME), ("windows", EXE_NAME)):
        p = DIST / name
        if not p.is_file():
            continue
        url = f"{base}/dist/{name}" if base else (
            f"https://github.com/{repo}/releases/download/v{ver}/{name}"
            if repo else "")
        size, sha = p.stat().st_size, sha1_file(p)
        man[key] = {"url": url, "size": size, "sha1": sha}
        man[f"{flat[key]}_url"] = url
        man[f"{flat[key]}_size"] = size
        man[f"{flat[key]}_sha1"] = sha
    (DIST / "latest.json").write_text(
        json.dumps(man, ensure_ascii=False, indent=2), "utf-8")
    log("dist/latest.json نوشته شد")
    return man


def publish_downloads(man: dict) -> None:
    """باینری‌ها و `latest.json` را داخلِ خروجیِ وب می‌گذارد.

    آدرس‌های آگاهی‌نامه `…/dist/Rafigram.exe` است، پس همان فایل‌ها باید کنارِ
    خودِ سایت روی ورسل باشند تا «به‌روزرسانی از داخلِ برنامه» بی مرورگر و بی
    گیت‌هاب کار کند. `latest.json` هم در ریشه می‌نشیند تا اگر سرور خواب بود،
    کلاینت همین فایل را بخواند.
    """
    web = DIST / "web"
    if not (web / "index.html").is_file():
        log("خروجیِ وب نیست؛ فایل‌های دانلود کپی نشد")
        return
    d = web / "dist"
    d.mkdir(parents=True, exist_ok=True)
    n = 0
    for name in (EXE_NAME, APK_NAME):
        p = DIST / name
        if p.is_file():
            (d / name).write_bytes(p.read_bytes())
            n += 1
    (web / "latest.json").write_text(
        json.dumps(man, ensure_ascii=False, indent=2), "utf-8")
    size = sum(f.stat().st_size for f in d.iterdir() if f.is_file())
    log(f"dist/web/dist: {n} فایل ({size / 1e6:.0f} مگابایت) + latest.json")


def git(*args, check=True, capture=False):
    cmd = ["git", *[str(a) for a in args]]
    if capture:
        r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
        if check and r.returncode != 0:
            raise SystemExit(r.stderr.strip() or "git شکست خورد")
        return (r.stdout or "").strip()
    return run(cmd, check=check)


def repo_slug() -> str:
    """`owner/name` از env یا از ریموتِ origin."""
    env = (os.getenv("GITHUB_REPO", "") or "").strip().strip("/")
    if env:
        return env
    url = git("remote", "get-url", "origin", check=False, capture=True)
    m = re.search(r"github\.com[:/]+([^/]+)/([^/.]+)", url or "")
    return f"{m.group(1)}/{m.group(2)}" if m else ""


def commit(ver: str, msg: str, binaries: bool) -> bool:
    """سورس‌ها (و اگر خواستی باینری‌ها) را کامیت می‌کند؛ True = کامیتِ تازه."""
    git("add", "-A", "client", "server", "tools", "README.md",
        "RAILWAY-fa.md", ".github", check=False)
    if binaries:
        for name in (EXE_NAME, APK_NAME, APK_NAME + ".sha1", "latest.json"):
            p = DIST / name
            if p.is_file():
                git("add", "-f", f"dist/{name}", check=False)
    if not git("status", "--porcelain", capture=True):
        log("چیزی برای کامیت نیست")
        return False
    git("commit", "-m", msg or f"Rafigram v{ver}")
    return True
def push(ver: str, tag: bool = True) -> None:
    """پوش با توکنِ env (اگر باشد)؛ توکن در هیچ خروجی‌ای دیده نمی‌شود."""
    tok = (os.getenv("GITHUB_TOKEN", "") or "").strip()
    branch = git("rev-parse", "--abbrev-ref", "HEAD", capture=True) or "main"
    if tag:
        git("tag", "-f", f"v{ver}", check=False)
    if tok:
        slug = repo_slug()
        if not slug:
            raise SystemExit("مخزن پیدا نشد (GITHUB_REPO یا ریموتِ origin)")
        url = f"https://x-access-token:{tok}@github.com/{slug}.git"
        log(f"push → github.com/{slug} ({branch})")
        run(["git", "push", url, f"HEAD:{branch}"], quiet=True)
        if tag:
            run(["git", "push", "-f", url, f"v{ver}"], quiet=True)
        return
    log(f"push → origin ({branch})")
    git("push", "origin", f"HEAD:{branch}")
    if tag:
        git("push", "-f", "origin", f"v{ver}")


def release(ver: str, notes: str, files: list[Path]) -> None:
    """ریلیزِ گیت‌هاب می‌سازد و باینری‌ها را به‌عنوان asset بالا می‌فرستد."""
    import httpx
    tok = (os.getenv("GITHUB_TOKEN", "") or "").strip()
    if not tok:
        raise SystemExit("GITHUB_TOKEN تنظیم نیست")
    slug = repo_slug()
    if not slug:
        raise SystemExit("مخزن پیدا نشد")
    H = {"Authorization": f"Bearer {tok}",
         "Accept": "application/vnd.github+json",
         "X-GitHub-Api-Version": "2022-11-28"}
    api = f"https://api.github.com/repos/{slug}"
    tag = f"v{ver}"
    with httpx.Client(timeout=120, follow_redirects=True) as c:
        r = c.get(f"{api}/releases/tags/{tag}", headers=H)
        if r.status_code == 200:
            rel = r.json()
            log(f"ریلیزِ {tag} از قبل بود")
        else:
            r = c.post(f"{api}/releases", headers=H, json={
                "tag_name": tag, "name": f"Rafigram {ver}",
                "body": notes or f"Rafigram {ver}", "draft": False,
                "prerelease": False})
            if r.status_code >= 300:
                raise SystemExit(f"ساختِ ریلیز نشد: {r.status_code} {r.text[:200]}")
            rel = r.json()
            log(f"ریلیزِ {tag} ساخته شد")
        up = rel["upload_url"].split("{")[0]
        have = {a["name"]: a["id"] for a in rel.get("assets") or []}
        for p in files:
            if not p.is_file():
                continue
            if p.name in have:      # جایگزینی: قبلی را پاک کن
                c.delete(f"{api}/releases/assets/{have[p.name]}", headers=H)
            log(f"upload {p.name} ({p.stat().st_size / 1e6:.1f} MB)")
            with open(p, "rb") as f:
                r = c.post(f"{up}?name={p.name}", headers={
                    **H, "Content-Type": "application/octet-stream"},
                    content=f.read())
            if r.status_code >= 300:
                raise SystemExit(f"آپلودِ {p.name} نشد: {r.status_code}")
    log("ریلیز کامل شد")
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="ساخت و انتشارِ رفیگرام")
    ap.add_argument("--version", help="نسخه‌ی تازه (مثلاً 1.1)")
    ap.add_argument("--build-number", type=int, help="شمارهٔ بیلدِ اندروید")
    ap.add_argument("--exe", action="store_true", help="ساختِ EXE")
    ap.add_argument("--apk", action="store_true", help="ساختِ APK")
    ap.add_argument("--web", action="store_true", help="ساختِ نسخه‌ی وب")
    ap.add_argument("--all", action="store_true", help="EXE + APK + WEB")
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--binaries", action="store_true",
                    help="باینری‌ها هم کامیت شوند (dist/)")
    ap.add_argument("--push", action="store_true")
    ap.add_argument("--release", action="store_true",
                    help="ریلیزِ گیت‌هاب + آپلودِ باینری‌ها")
    ap.add_argument("--notes", default="", help="توضیحِ نسخه")
    ap.add_argument("--message", default="", help="پیامِ کامیت")
    ap.add_argument("--download-base", default=DOWNLOAD_BASE,
                    help="دامنه‌ی دانلود در latest.json (پیش‌فرض: ورسل)")
    ap.add_argument("--no-publish", action="store_true",
                    help="باینری‌ها داخلِ dist/web کپی نشوند")
    a = ap.parse_args(argv)

    ver = (a.version or cur_version()).lstrip("vV")
    if not re.fullmatch(r"\d+(\.\d+)*", ver):
        raise SystemExit(f"نسخه‌ی نامعتبر: {ver}")
    if a.version:
        bump(ver, a.build_number)
    log(f"نسخه {ver}")
    t0 = time.time()

    # ترتیب مهم است: EXE، بعد APK (وگرنه pack خروجیِ APK را جارو می‌کند)
    if a.exe or a.all:
        build_exe()
    if a.apk or a.all:
        build_apk()
    if a.web or a.all:
        build_web()

    slug = repo_slug()
    man = manifest(ver, slug, a.notes, a.download_base, a.build_number)
    if not a.no_publish:
        publish_downloads(man)
    scrub_web()          # آخرین دروازه پیش از `vercel deploy` دستیِ کاربر
    if a.commit and commit(ver, a.message, a.binaries) and a.push:
        push(ver)
    elif a.push:
        push(ver)
    if a.release:
        release(ver, a.notes, [DIST / EXE_NAME, DIST / APK_NAME,
                               DIST / "latest.json"])
    log(f"تمام در {time.time() - t0:.0f} ثانیه")
    print(json.dumps(man, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
