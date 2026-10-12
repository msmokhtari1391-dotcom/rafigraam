"""
هابِ اعلان — پیام را به کاربری که *در برنامه نیست* هم می‌رساند.

دو کانال، یک صف:

  • «poll» — سرویسِ بومیِ اندروید (Kotlin، بیرونِ فلاتر) روی
    `GET /push/poll` می‌نشیند و ۲۵ ثانیه منتظر می‌ماند. برنامه بسته باشد
    هم آن سرویس زنده است، پس اعلان مثلِ تلگرام می‌آید. نه فایربیس، نه
    گوگل: مستقیم از سرورِ خودمان.
  • «web» — Web Push استاندارد (RFC 8291 + VAPID) برای نسخه‌ی سایت/PWA.
    مرورگر بسته باشد هم سرویس‌ورکر با پوشِ گوگل/اپل بیدار می‌شود.

هر پیامی که از `broadcast_to_chat` می‌گذرد، برای اعضایی که سوکتِ *جلو*
ندارند از این‌جا هم می‌رود. متنِ اعلان همین‌جا ساخته می‌شود تا Kotlin و
سرویس‌ورکر هیچ منطقی نداشته باشند.

کلیدهای VAPID: اگر `VAPID_PRIVATE_KEY` در env نبود، از `SECRET_KEY`
مشتق می‌شوند — پس بی هیچ تنظیمی کار می‌کند و ری‌استارتِ Railway
اشتراک‌های مرورگر را نمی‌سوزاند (کلید عوض شود همه باطل می‌شوند).
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from .auth import check_session, decode_payload
from .config import settings
from .db import AsyncSessionLocal, get_session
from .models import PushSub, User

router = APIRouter(prefix="/push", tags=["push"])

# ── صفِ درون‌حافظه‌ای ────────────────────────────────
# فقط برای کانالِ long-poll. ماندگار نیست و لازم هم نیست: پیامِ جامانده را
# خودِ برنامه سرِ باز شدن از دیتابیس می‌گیرد؛ این صف کارش «الان خبر بده» است.
_KEEP = 24                # حداکثر رویدادِ نگه‌داشته‌شده برای هر کاربر
_TTL = 300.0              # رویدادِ کهنه‌تر از این دور ریخته می‌شود (ثانیه)
_CATCHUP = 120.0          # سرویسِ تازه‌راه‌افتاده تا این عقب را می‌گیرد

_q: dict[str, list[dict]] = {}          # user_id → [رویداد, ...]
_n: dict[str, int] = {}                 # user_id → شماره‌ی آخرین رویداد
_ev: dict[str, asyncio.Event] = {}      # user_id → پرچمِ «چیزِ تازه آمد»


def _waiter(uid: str) -> asyncio.Event:
    ev = _ev.get(uid)
    if ev is None:
        ev = _ev[uid] = asyncio.Event()
    return ev


def _wake(uid: str) -> None:
    """همه‌ی long-pollهای این کاربر را بیدار کن.

    رویداد را `pop` می‌کنیم نه `clear`: منتظرها ارجاعِ خودشان را دارند و
    دورِ بعد یک پرچمِ تازه ساخته می‌شود. با `clear` یک مسابقه‌ی کلاسیک
    داشتیم — رویدادِ بعدی می‌توانست پیشِ پای بیدار شدنِ منتظرِ قبلی پاک شود.
    """
    ev = _ev.pop(uid, None)
    if ev is not None:
        ev.set()


def _push(uid: str, ev: dict) -> None:
    """رویداد را در صفِ کاربر بگذار و منتظرها را بیدار کن."""
    n = _n.get(uid, 0) + 1
    _n[uid] = n
    row = dict(ev)
    row["seq"] = n
    row["at"] = time.time()
    box = _q.setdefault(uid, [])
    box.append(row)
    cut = time.time() - _TTL
    if len(box) > _KEEP or (box and box[0]["at"] < cut):
        box = [r for r in box if r["at"] >= cut][-_KEEP:]
        _q[uid] = box
    _wake(uid)


# ── متنِ اعلان ──────────────────────────────────────
# همین‌جا ساخته می‌شود، نه در Kotlin و نه در سرویس‌ورکر: آن دو نه i18n
# دارند نه باید منطقِ پیام را تکرار کنند. فارسی است چون برنامه فارسی است.
_KIND = {"image": "عکس", "video": "ویدیو", "voice": "پیامِ صوتی",
         "file": "فایل", "gif": "گیف", "sticker": "استیکر",
         "location": "موقعیت مکانی"}


def _snippet(frame: dict) -> str:
    """یک خطِ کوتاه از پیام — همان که در اعلان دیده می‌شود."""
    if frame.get("enc"):
        return "پیامِ رمزی"                 # سرور کلید ندارد، پس متن هم ندارد
    kind = str(frame.get("msg_type") or "text").lower()
    if kind in _KIND:
        cap = " ".join(str(frame.get("caption") or "").split())
        head = _KIND[kind]
        return f"{head} — {cap}" if cap else head
    body = " ".join(str(frame.get("content") or "").split())
    low = body.lower()
    if "google.com/maps" in low or low.startswith("geo:"):
        return "📍 موقعیت مکانی"            # نه لینکِ بلندِ گوگل‌مپ
    return body[:140] if body else "پیامِ تازه"


async def _head(chat_id: str, sender_id: str) -> tuple[str, str, bool, bool, str]:
    """(عنوان, آواتار, گروه؟, کانال؟, نامِ فرستنده) — یک رفت‌وبرگشتِ دیتابیس.

    در چتِ خصوصی سرِ اعلان نامِ *فرستنده* می‌نشیند و آواتارِ خودش؛ در گروه
    نامِ گروه و آواتارِ گروه، و نامِ فرستنده به متن می‌چسبد. در کانال هم
    ریپلای غیرفعال است چون اعضا نمی‌توانند در کانال پیام بگذارند.
    """
    from .models import Chat
    title, avatar, group, is_channel, who = "", "", False, False, ""
    try:
        async with AsyncSessionLocal() as db:
            chat = (await db.execute(
                select(Chat).where(Chat.id == chat_id))).scalar_one_or_none()
            if chat is not None:
                is_channel = (chat.kind or "") == "channel"
                group = bool(chat.is_group) or is_channel or (chat.kind or "") == "group"
                title = chat.title or ""
                avatar = chat.avatar_url or ""
            snd = (await db.execute(
                select(User).where(User.id == sender_id))).scalar_one_or_none()
            if snd is not None:
                who = snd.display_name or snd.username or ""
                if not group:
                    title, avatar = who, (snd.avatar_url or "")
    except Exception:
        pass
    return title, avatar, group, is_channel, who


async def dispatch(chat_id: str, sender_id: str, frame: dict,
                   targets: list[str]) -> None:
    """تنها دروازه‌ی اعلانِ بیرونِ برنامه. `ws.broadcast_to_chat` صدایش می‌زند.

    `targets` = اعضایی که سوکتِ *جلو* ندارند (برنامه بسته یا در پس‌زمینه).
    آدرسِ آواتار نسبی می‌ماند (`/media/...`): هر کانال خودش پایه‌ی خودش را
    می‌چسباند — Kotlin آدرسِ سرورش را دارد و سرویس‌ورکر `location.origin` را.
    """
    if not targets:
        return
    title, avatar, group, is_channel, who = await _head(chat_id, sender_id)
    body = _snippet(frame)
    if group and title and not is_channel:
        body = f"{who}: {body}" if who else body
    media_url = ""
    media_type = ""
    m_type = str(frame.get("msg_type") or "text").lower()
    if m_type in ("image", "photo"):
        media_url = str(frame.get("file_url") or frame.get("content") or "")
        media_type = "image/jpeg"
    ev = {"chat_id": str(chat_id), "title": title or "رفیگرام", "body": body,
          "icon": avatar or "", "sender_id": str(sender_id or ""),
          "sender_name": str(who or (title if not group else "کاربر")),
          "message_id": str(frame.get("message_id") or ""),
          "group": bool(group),
          "channel": bool(is_channel),
          "can_reply": not is_channel,
          "media_url": media_url,
          "media_type": media_type}
    for uid in targets:
        _push(uid, ev)
    try:                       # پوشِ مرورگر کند است؛ فرستنده منتظرش نماند
        asyncio.create_task(_web_fanout(list(targets), ev))
    except RuntimeError:
        pass


# ── کلیدهای VAPID ───────────────────────────────────
def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _ub64(s: str) -> bytes:
    s = (s or "").strip().replace("-", "+").replace("_", "/")
    return base64.b64decode(s + "=" * (-len(s) % 4))


# مرتبه‌ی منحنیِ P-256 — برای مشتق‌کردنِ کلیدِ خصوصی از `SECRET_KEY`
_N = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
_SUB = os.getenv("VAPID_SUBJECT", "").strip() or "mailto:admin@rafigram.app"
_keys: dict = {}


def keys() -> tuple:
    """(کلیدِ خصوصیِ EC، کلیدِ عمومیِ b64url). یک‌بار ساخته و کش می‌شود.

    بی `cryptography` هر دو خالی برمی‌گردند و کانالِ وب ساکت خاموش می‌ماند
    (کانالِ اندروید به آن کاری ندارد) — نه استثنا، نه ۵۰۰.
    """
    if _keys:
        return _keys["priv"], _keys["pub"]
    priv, pub = None, ""
    try:
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.serialization import (
            Encoding, PublicFormat)
        env = os.getenv("VAPID_PRIVATE_KEY", "").strip()
        if env:
            d = int.from_bytes(_ub64(env), "big") % _N or 1
        else:
            dig = hmac.new(settings.secret_key.encode("utf-8"),
                           b"rafigram-vapid", hashlib.sha256).digest()
            d = int.from_bytes(dig, "big") % (_N - 1) + 1
        priv = ec.derive_private_key(d, ec.SECP256R1())
        pub = _b64u(priv.public_key().public_bytes(
            Encoding.X962, PublicFormat.UncompressedPoint))
    except Exception:
        priv, pub = None, ""
    _keys.update(priv=priv, pub=pub)
    return priv, pub


# ── رمزنگاریِ Web Push (RFC 8291، aes128gcm) ─────────
# بی `pywebpush` و بی `http_ece`: تنها `cryptography`. سه HKDF و یک
# AES-GCM است و کدش از رفت‌وآمدِ نسخه‌های آن دو بسته کم‌دردسرتر.
def _hkdf(salt: bytes, ikm: bytes, info: bytes, size: int) -> bytes:
    """HKDF-SHA256 — یک بلوک بس است (خروجی ما همیشه ≤ ۳۲ بایت)."""
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    return hmac.new(prk, info + b"\x01", hashlib.sha256).digest()[:size]


def _seal(payload: bytes, p256dh: str, auth: str) -> bytes:
    """بدنه‌ی رمزشده‌ی `Content-Encoding: aes128gcm`."""
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.serialization import (
        Encoding, PublicFormat)
    ua_raw = _ub64(p256dh)
    ua = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_raw)
    es = ec.generate_private_key(ec.SECP256R1())
    as_raw = es.public_key().public_bytes(
        Encoding.X962, PublicFormat.UncompressedPoint)
    shared = es.exchange(ec.ECDH(), ua)
    salt = os.urandom(16)
    ikm = _hkdf(_ub64(auth), shared,
                b"WebPush: info\x00" + ua_raw + as_raw, 32)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    body = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)
    return (salt + (4096).to_bytes(4, "big") + bytes([len(as_raw)])
            + as_raw + body)


def _vapid(endpoint: str) -> dict:
    """هدرِ `Authorization: vapid ...` برای این سرویسِ پوش."""
    priv, pub = keys()
    if priv is None:
        return {}
    try:
        import jwt
        from urllib.parse import urlsplit
        u = urlsplit(endpoint)
        tok = jwt.encode({"aud": f"{u.scheme}://{u.netloc}",
                          "exp": int(time.time()) + 12 * 3600,
                          "sub": _SUB}, priv, algorithm="ES256")
        if isinstance(tok, bytes):
            tok = tok.decode("ascii")
        return {"Authorization": f"vapid t={tok}, k={pub}"}
    except Exception:
        return {}


async def _web_fanout(uids: list[str], ev: dict) -> None:
    """اعلانِ مرورگر برای همه‌ی اشتراک‌های این کاربران.

    اشتراکِ باطل (۴۰۴/۴۱۰ از گوگل/اپل) همان‌جا از دیتابیس پاک می‌شود؛
    وگرنه صفِ سوخته‌ها بزرگ می‌شد و هر پیام چند درخواستِ بی‌فایده داشت.
    """
    priv, _pub = keys()
    if priv is None or not uids:
        return
    try:
        async with AsyncSessionLocal() as db:
            subs = (await db.execute(select(PushSub).where(
                PushSub.user_id.in_(uids),
                PushSub.kind == "web"))).scalars().all()
            if not subs:
                return
            payload = json.dumps(ev, ensure_ascii=False).encode("utf-8")
            dead: list[PushSub] = []
            async with httpx.AsyncClient(timeout=12.0) as cx:
                for s in subs:
                    code = await _send_one(cx, s, payload)
                    if code in (404, 410):
                        dead.append(s)
                    elif code and code < 300:
                        s.last_ok_at = datetime.now(timezone.utc)
                        s.fails = 0
                        db.add(s)
                    else:
                        s.fails = int(s.fails or 0) + 1
                        db.add(s)
                        if s.fails > 12:
                            dead.append(s)
            for s in dead:
                await db.delete(s)
            await db.commit()
    except Exception:
        pass


async def _send_one(cx: httpx.AsyncClient, s: PushSub,
                    payload: bytes) -> int | None:
    """یک درخواستِ پوش. کدِ HTTP یا `None` اگر حتی نرفت."""
    try:
        body = _seal(payload, s.p256dh, s.auth)
        head = {"Content-Encoding": "aes128gcm",
                "Content-Type": "application/octet-stream",
                "TTL": "3600", "Urgency": "high"}
        head.update(_vapid(s.endpoint))
        r = await cx.post(s.endpoint, content=body, headers=head)
        return r.status_code
    except Exception:
        return None


# ── درگاه‌های HTTP ──────────────────────────────────
async def _quiet(request: Request) -> str:
    """کاربرِ این درخواست — بی دست‌زدن به «آخرین بازدید».

    `get_current_user` هر بار `last_seen_at` را تازه می‌کند و سرویسِ
    پس‌زمینه هر ۲۵ ثانیه یک‌بار می‌پرسد؛ با آن، گوشیِ خاموشِ کاربر همیشه
    «همین حالا آنلاین بود» نشان می‌داد و «آخرین بازدید» دروغ می‌شد.
    """
    head = request.headers.get("authorization") or ""
    if not head.lower().startswith("bearer "):
        raise HTTPException(401, "لاگین نکردی")
    payload = decode_payload(head[7:].strip())
    async with AsyncSessionLocal() as db:
        await check_session(db, payload.get("sid"))
    uid = str(payload.get("sub") or "")
    if not uid:
        raise HTTPException(401, "توکن نامعتبره")
    return uid


def _clean(rows: list[dict]) -> list[dict]:
    """رویدادها بی فیلدهای درونی (`at`) — همان که کلاینت لازم دارد."""
    return [{k: v for k, v in r.items() if k != "at"} for r in rows]


@router.get("/key")
async def push_key():
    """کلیدِ عمومیِ VAPID — عمومی است و احراز هویت نمی‌خواهد.

    سرویس‌ورکر همین را می‌گیرد و به `pushManager.subscribe` می‌دهد؛ پس
    کلید در فایلِ سایت هاردکد نمی‌شود و عوض شدنش نیاز به بیلدِ تازه ندارد.
    """
    _priv, pub = keys()
    return {"key": pub, "subject": _SUB, "enabled": bool(pub)}


class SubIn(BaseModel):
    endpoint: str
    p256dh: str = ""
    auth: str = ""
    kind: str = "web"
    device: str = ""


@router.post("/register")
async def push_register(body: SubIn, request: Request,
                        db: AsyncSession = Depends(get_session)):
    """اشتراکِ Web Push مرورگر را ثبت (یا تازه) می‌کند.

    کلیدِ یکتا `endpoint` است نه کاربر: یک مرورگر هر بار باز شدنِ سایت
    ثبت را تکرار می‌کند و بی این، هر بار یک ردیفِ تازه و یک اعلانِ تکراری
    اضافه می‌شد. اگر همان مرورگر با حسابِ دیگری وارد شود، ردیف به او
    منتقل می‌شود — یک مرورگر، یک حساب.
    """
    uid = await _quiet(request)
    ep = (body.endpoint or "").strip()
    if not ep.startswith("https://") or len(ep) > 900:
        raise HTTPException(400, "آدرسِ اشتراک درست نیست")
    if not (body.p256dh or "").strip() or not (body.auth or "").strip():
        raise HTTPException(400, "کلیدهای اشتراک نیامده")
    row = (await db.execute(select(PushSub).where(
        PushSub.endpoint == ep))).scalar_one_or_none()
    if row is None:
        row = PushSub(user_id=uid, endpoint=ep)
    row.user_id, row.kind = uid, "web"
    row.p256dh, row.auth = body.p256dh.strip(), body.auth.strip()
    row.device, row.fails = (body.device or "")[:200], 0
    db.add(row)
    await db.commit()
    return {"ok": True}


@router.post("/unregister")
async def push_unregister(body: SubIn, request: Request,
                          db: AsyncSession = Depends(get_session)):
    """سرِ «خروج از حساب» — تا اعلانِ حسابِ قبلی روی این مرورگر نیاید."""
    uid = await _quiet(request)
    ep = (body.endpoint or "").strip()
    row = (await db.execute(select(PushSub).where(
        PushSub.endpoint == ep))).scalar_one_or_none()
    if row is not None and row.user_id == uid:
        await db.delete(row)
        await db.commit()
    return {"ok": True}


@router.get("/poll")
async def push_poll(request: Request, since: int = Query(-1),
                    timeout: float = Query(25.0)):
    """long-poll: تا ۲۵ ثانیه منتظر می‌ماند، سپس خالی برمی‌گردد.

    قرارداد با سرویسِ بومیِ اندروید:
      • `since=-1` (نخستین پرسش) ⇒ فقط دو دقیقه‌ی عقب. وگرنه سرویسِ
        تازه‌راه‌افتاده ۲۴ اعلانِ کهنه را یک‌جا بالا می‌آورد.
      • `since=<seq آخرینی که دیدم>` ⇒ هر چه تازه‌تر است.
      • پاسخ همیشه `{"seq": n, "events": [...]}`؛ `seq` را نگه دار و
        دفعه‌ی بعد همان را بفرست. بی این، فاصله‌ی بینِ دو پرسش پیام می‌خورد.
    سرور ری‌استارت شود شماره‌ها از صفر شروع می‌شوند؛ `since > seq` را همان
    «از نو» می‌فهمیم، وگرنه سرویس تا ابد در انتظار می‌ماند.
    """
    uid = await _quiet(request)
    wait = max(1.0, min(50.0, float(timeout or 25.0)))
    now = _n.get(uid, 0)
    if since > now:
        since = 0
    if since < 0:
        cut = time.time() - _CATCHUP
        return {"seq": now,
                "events": _clean([r for r in _q.get(uid, [])
                                  if r["at"] >= cut])}
    if since < now:
        return {"seq": now,
                "events": _clean([r for r in _q.get(uid, [])
                                  if r["seq"] > since])}
    ev = _waiter(uid)
    try:
        await asyncio.wait_for(ev.wait(), timeout=wait)
    except (asyncio.TimeoutError, TimeoutError):
        return {"seq": _n.get(uid, now), "events": []}
    return {"seq": _n.get(uid, now),
            "events": _clean([r for r in _q.get(uid, [])
                              if r["seq"] > since])}


@router.post("/test")
async def push_test(request: Request):
    """اعلانِ آزمایشی برای خودِ کاربر — کلیدِ «تستِ اعلان» در تنظیمات.

    از همان مسیرِ پیامِ واقعی می‌رود (صف + پوشِ مرورگر)، پس اگر این آمد
    یعنی زنجیره‌ی اعلان سرتاسر سالم است.
    """
    uid = await _quiet(request)
    ev = {"chat_id": "", "title": "رفیگرام",
          "body": "اعلانِ آزمایشی — همه‌چیز درست کار می‌کند ✅",
          "icon": "", "sender_id": "", "sender_name": "پشتیبانی رفیگرام",
          "message_id": "", "group": False}
    _push(uid, ev)
    try:
        asyncio.create_task(_web_fanout([uid], ev))
    except RuntimeError:
        pass
    _priv, pub = keys()
    async with AsyncSessionLocal() as db:
        subs = (await db.execute(select(PushSub).where(
            PushSub.user_id == uid))).scalars().all()
    return {"ok": True, "web_subs": len(subs), "vapid": bool(pub)}
