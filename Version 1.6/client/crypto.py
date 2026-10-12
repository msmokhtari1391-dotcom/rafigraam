"""
رمزنگاریِ محلیِ متنِ پیام‌ها (قلابِ سمتِ کلاینت).

سرور فقط متنِ رمزشده رو می‌بینه و ذخیره می‌کنه (با پرچمِ `enc=True`).
باز کردنش تنها با همون گذرواژه‌ای ممکنه که کاربر در تنظیمات وارد کرده و
هیچ‌وقت به سرور فرستاده نمی‌شه.

ساختِ رمز فقط با کتابخانه‌ی استاندارد پایتون:
  کلیدِ اصلی  = PBKDF2-HMAC-SHA256(گذرواژه، نمکِ برنامه، ۲۰۰٬۰۰۰ دور)
  هر پیام     = نانسِ تصادفیِ ۱۶ بایتی
  k_enc/k_mac = HMAC(کلیدِ اصلی، b"e"/b"m" + نانس)   ← کلیدهای جدا
  رمزگذاری    = XOR با کلیدجریانِ HMAC-SHA256(k_enc, نانس+شمارنده)  (حالتِ CTR)
  اصالت       = encrypt-then-MAC با HMAC-SHA256(k_mac, نانس+رمز)[:16]

قالبِ روی سیم:  ``rg1:<base64(نانس ۱۶ + برچسب ۱۶ + متنِ رمزشده)>``
"""
import base64
import hashlib
import hmac
import os
import struct

MARK = "rg1:"
_APP_SALT = b"rafigram/msg/v1"
_ROUNDS = 200_000
_NONCE = 16
_TAG = 16

_master: bytes | None = None
_key_src: str = ""


# ── کلید ───────────────────────────────────────────
def set_key(passphrase: str) -> bool:
    """گذرواژه رو به کلیدِ اصلی تبدیل و کش می‌کنه. خالی ⇒ خاموش."""
    global _master, _key_src
    pw = (passphrase or "").strip()
    if not pw:
        _master, _key_src = None, ""
        return False
    if pw == _key_src and _master is not None:
        return True
    _master = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), _APP_SALT, _ROUNDS)
    _key_src = pw
    return True


def has_key() -> bool:
    return _master is not None


def clear_key() -> None:
    global _master, _key_src
    _master, _key_src = None, ""


def fingerprint() -> str:
    """اثرِ انگشتِ کوتاهِ کلید — برای این‌که دو طرف مطمئن شن کلیدشون یکیه."""
    if _master is None:
        return ""
    return hashlib.sha256(_master + b"/fp").hexdigest()[:8]


# ── درونی ──────────────────────────────────────────
def _subkeys(nonce: bytes):
    e = hmac.new(_master, b"e" + nonce, hashlib.sha256).digest()
    m = hmac.new(_master, b"m" + nonce, hashlib.sha256).digest()
    return e, m


def _stream(k: bytes, nonce: bytes, n: int) -> bytes:
    """کلیدجریانِ به‌طولِ n بایت (HMAC در حالتِ شمارنده)."""
    out = bytearray()
    ctr = 0
    while len(out) < n:
        out += hmac.new(k, nonce + struct.pack(">I", ctr), hashlib.sha256).digest()
        ctr += 1
    return bytes(out[:n])


def _xor(data: bytes, ks: bytes) -> bytes:
    return bytes(a ^ b for a, b in zip(data, ks))


# ── بیرونی ─────────────────────────────────────────
def is_encrypted(text: str) -> bool:
    return isinstance(text, str) and text.startswith(MARK)


def encrypt(text: str) -> str:
    """متن → ``rg1:...``؛ بدون کلید، خودِ متن برگشت داده می‌شه."""
    if _master is None or not text:
        return text
    raw = text.encode("utf-8")
    nonce = os.urandom(_NONCE)
    k_enc, k_mac = _subkeys(nonce)
    ct = _xor(raw, _stream(k_enc, nonce, len(raw)))
    tag = hmac.new(k_mac, nonce + ct, hashlib.sha256).digest()[:_TAG]
    return MARK + base64.b64encode(nonce + tag + ct).decode("ascii")


def decrypt(text: str) -> tuple[str, bool]:
    """(متنِ باز‌شده، درست‌بود؟). اگه کلید نباشه یا خراب باشه ok=False."""
    if not is_encrypted(text):
        return text, True
    if _master is None:
        return text, False
    try:
        blob = base64.b64decode(text[len(MARK):].encode("ascii"), validate=True)
        if len(blob) < _NONCE + _TAG:
            return text, False
        nonce, tag, ct = blob[:_NONCE], blob[_NONCE:_NONCE + _TAG], blob[_NONCE + _TAG:]
        k_enc, k_mac = _subkeys(nonce)
        want = hmac.new(k_mac, nonce + ct, hashlib.sha256).digest()[:_TAG]
        if not hmac.compare_digest(tag, want):
            return text, False      # کلیدِ اشتباه یا دست‌کاری‌شده
        return _xor(ct, _stream(k_enc, nonce, len(ct))).decode("utf-8"), True
    except Exception:
        return text, False


def show(text: str, placeholder: str = "🔒 پیامِ رمزشده") -> tuple[str, bool]:
    """متنِ نمایشی برای حباب: باز‌شده، یا جای‌گیرِ قفل."""
    if not is_encrypted(text):
        return text, False
    plain, ok = decrypt(text)
    return (plain, True) if ok else (placeholder, True)
