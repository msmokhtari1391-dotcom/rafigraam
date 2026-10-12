"""
کلیدِ امضای انتشارِ APK — ساختش، نگه‌داشتنش، و آگاه‌کردنِ ساز از جایش.

    python tools/keystore.py            # بساز اگر نیست
    python tools/keystore.py --show     # فقط اثرِ انگشت را نشان بده
    python tools/keystore.py --force    # ⚠️ کلیدِ کهنه را دور بریز

⚠️ این کلید *بیرونِ* مخزن می‌نشیند (`D:/coding/tools/rafigram-keys/`) و هرگز
نباید کامیت شود: پوشه‌ی `.tmp/` در گیت پیگیری می‌شود، پس کلید آنجا یعنی
کلیدِ خصوصیِ ما روی گیت‌هابِ عمومی.

⚠️ اگر این فایل گم شود، دیگر نمی‌توان روی نسخه‌های نصب‌شده به‌روزرسانی
گذاشت — اندروید APKِ با کلیدِ دیگر را «برنامه‌ی دیگری» می‌بیند و نصب را رد
می‌کند («App not installed»). یک نسخه‌ی پشتیبان جای امن بگذار.

نکته‌ی صادقانه: خودـامضایی هشدارِ «سازنده‌ی ناشناس»/Play Protect را برنمی‌دارد
— آن هشدار برای هر APKِ بیرونِ گوگل‌پلی هست. کاری که می‌کند این است که امضا
بین نسخه‌ها *ثابت* می‌ماند (کلیدِ debug هر بار می‌تواند عوض شود و مسیرِ
به‌روزرسانی را می‌شکند) و عمرِ کلید ۲۷ سال است نه یک سال.
"""
from __future__ import annotations

import argparse
import os
import re
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_SH = ROOT / ".tmp" / "android_env.sh"

# بیرونِ مخزن، کنارِ JDK و SDK — همان‌جایی که بقیه‌ی ابزارها هستند.
KEYS_DIR = Path(os.getenv("RAFIGRAM_KEYSTORE_DIR")
                or "D:/coding/tools/rafigram-keys")
JKS = KEYS_DIR / "rafigram-release.jks"
ENV_FILE = KEYS_DIR / "keystore.env"

ALIAS = "rafigram"
DNAME = "CN=Rafigram, OU=Rafigram, O=Rafigram, L=Tehran, C=IR"
VALID_DAYS = 10000                 # ~۲۷ سال؛ کلیدِ debug فقط ۱ سال است


def log(msg: str) -> None:
    print(f"[keystore] {msg}", flush=True)


def java_home() -> str:
    """JAVA_HOME — از env یا از همان `.tmp/android_env.sh` که ساز می‌خواند."""
    jh = os.getenv("JAVA_HOME") or ""
    if not jh and ENV_SH.is_file():
        m = re.search(r'^export JAVA_HOME="([^"]+)"',
                      ENV_SH.read_text("utf-8"), re.M)
        if m:
            jh = m.group(1)
    return jh


def keytool() -> str:
    """مسیرِ `keytool`؛ نبودش خطای روشن می‌دهد، نه ردِّ پای گنگ."""
    jh = java_home()
    if jh:
        for name in ("keytool.exe", "keytool"):
            p = Path(jh) / "bin" / name
            if p.is_file():
                return str(p)
    found = shutil.which("keytool")
    if found:
        return found
    raise SystemExit("keytool پیدا نشد — JAVA_HOME را بده یا "
                     ".tmp/android_env.sh را بررسی کن")


def write_env(password: str) -> None:
    """پرچم‌های محیطی‌ای که `flet build apk` می‌خواند.

    از env می‌دهیم نه از خطِ فرمان، تا گذرواژه در لیستِ پروسه‌ها و در خروجیِ
    پرگویِ (`-v`) ساز نیفتد.
    """
    ENV_FILE.write_text(
        "# ساختِ tools/keystore.py — گذرواژه‌ی کلیدِ امضا. کامیتش نکن.\n"
        f'export FLET_ANDROID_SIGNING_KEY_STORE="{JKS.as_posix()}"\n'
        f'export FLET_ANDROID_SIGNING_KEY_STORE_PASSWORD="{password}"\n'
        f'export FLET_ANDROID_SIGNING_KEY_PASSWORD="{password}"\n'
        f'export FLET_ANDROID_SIGNING_KEY_ALIAS="{ALIAS}"\n',
        encoding="utf-8", newline="\n")
    try:
        os.chmod(ENV_FILE, 0o600)
    except OSError:
        pass
    log(f"گذرواژه در {ENV_FILE} نوشته شد (کامیتش نکن)")


def read_password() -> str:
    if not ENV_FILE.is_file():
        return ""
    m = re.search(r'FLET_ANDROID_SIGNING_KEY_STORE_PASSWORD="([^"]*)"',
                  ENV_FILE.read_text("utf-8"))
    return m.group(1) if m else ""


def show() -> bool:
    """اثرِ انگشتِ کلید — همان چیزی که اندروید برای «همان برنامه» می‌سنجد."""
    if not JKS.is_file():
        log(f"کلیدی در {JKS} نیست")
        return False
    pw = read_password()
    if not pw:
        log(f"گذرواژه در {ENV_FILE} نیست؛ اثرِ انگشت خوانده نشد")
        return False
    r = subprocess.run([keytool(), "-list", "-v", "-keystore", str(JKS),
                        "-alias", ALIAS, "-storepass", pw],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    if r.returncode != 0:
        log("خواندنِ کلید نشد: " + (r.stderr or "")[:300])
        return False
    for line in (r.stdout or "").splitlines():
        s = line.strip()
        if s.startswith(("SHA1:", "SHA256:", "Valid from:", "Alias name:")):
            log(s)
    return True


def create(force: bool, password: str = "") -> None:
    if JKS.is_file() and not force:
        log(f"کلید از قبل هست: {JKS}")
        show()
        return
    if JKS.is_file() and force:
        # کلیدِ کهنه را دور نمی‌ریزیم، کنار می‌گذاریم: با دور ریختنش مسیرِ
        # به‌روزرسانیِ هر گوشیِ نصب‌شده تا همیشه می‌شکند.
        bak = JKS.with_suffix(".jks.bak")
        JKS.replace(bak)
        log(f"⚠️ کلیدِ پیشین به {bak.name} منتقل شد — پاکش نکن")
    KEYS_DIR.mkdir(parents=True, exist_ok=True)
    pw = password or secrets.token_urlsafe(24)
    cmd = [keytool(), "-genkeypair", "-v",
           "-keystore", str(JKS), "-alias", ALIAS,
           "-keyalg", "RSA", "-keysize", "4096",
           "-validity", str(VALID_DAYS),
           "-storetype", "PKCS12",
           "-dname", DNAME,
           "-storepass", pw, "-keypass", pw]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise SystemExit("ساختِ کلید نشد:\n" + (r.stderr or r.stdout or "")[:800])
    log(f"کلید ساخته شد: {JKS}")
    write_env(pw)
    show()


def main() -> None:
    ap = argparse.ArgumentParser(description="کلیدِ امضای انتشارِ رفیگرام")
    ap.add_argument("--show", action="store_true", help="فقط نشان بده")
    ap.add_argument("--force", action="store_true",
                    help="⚠️ کلیدِ تازه بساز (مسیرِ به‌روزرسانی می‌شکند)")
    ap.add_argument("--password", default="", help="گذرواژه‌ی دستی")
    a = ap.parse_args()
    if a.show:
        sys.exit(0 if show() else 1)
    create(a.force, a.password)
    log("برای امضاشدنِ APK کافی است دوباره .tmp/build_apk.sh را اجرا کنی")


if __name__ == "__main__":
    main()
