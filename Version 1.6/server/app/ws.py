"""
WebSocket — چت ریل‌تایم.

proto_message:
  ارسال از کلاینت:  {"chat_id": "...", "content": "سلام"}
  دریافت در کلاینت: {"type":"message", "chat_id":"...", "sender_id":"...", ...}
"""
import asyncio
import json
from datetime import datetime, timezone
from typing import Dict, Set

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from .auth import ws_authenticate
from .db import AsyncSessionLocal
from .models import (
    KIND_CHANNEL, ROLE_ADMIN, ROLE_MEMBER, ROLE_RANK, Chat, ChatMember, Message,
)


def _iso(dt) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()

router = APIRouter(tags=["ws"])


class ConnectionManager:
    def __init__(self):
        self._connections: Dict[str, Set[WebSocket]] = {}
        # سوکت‌هایی که برنامه‌شان «جلو» است. حضور (Online) فقط با همین‌ها
        # سنجیده می‌شود: کاربری که برنامه را کوچک کرده سوکتش باز است ولی
        # آنلاین نیست.
        self._fg: Dict[str, Set[WebSocket]] = {}
        self._lock = asyncio.Lock()

    async def connect(self, user_id: str, ws: WebSocket):
        async with self._lock:
            self._connections.setdefault(user_id, set()).add(ws)
            # اتصالِ تازه یعنی برنامه همین حالا جلو است
            self._fg.setdefault(user_id, set()).add(ws)

    async def disconnect(self, user_id: str, ws: WebSocket):
        async with self._lock:
            if user_id in self._connections:
                self._connections[user_id].discard(ws)
                if not self._connections[user_id]:
                    del self._connections[user_id]
            if user_id in self._fg:
                self._fg[user_id].discard(ws)
                if not self._fg[user_id]:
                    del self._fg[user_id]

    async def set_foreground(self, user_id: str, ws: WebSocket, fg: bool):
        async with self._lock:
            if fg:
                self._fg.setdefault(user_id, set()).add(ws)
            elif user_id in self._fg:
                self._fg[user_id].discard(ws)
                if not self._fg[user_id]:
                    del self._fg[user_id]

    def is_online(self, user_id: str) -> bool:
        """آنلاین = دستِ‌کم یک سوکتِ بازِ «جلو». بی این، حضور از روی
        `last_seen_at` حدس زده می‌شد و برنامه‌ی بسته تا ۷۰ ثانیه آنلاین
        می‌ماند."""
        return bool(self._fg.get(user_id))

    def online_ids(self) -> Set[str]:
        return {uid for uid, socks in self._fg.items() if socks}

    async def send_to_user(self, user_id: str, data: dict):
        async with self._lock:
            conns = self._connections.get(user_id, set()).copy()
        for ws in conns:
            try:
                await ws.send_text(json.dumps(data, ensure_ascii=False))
            except Exception:
                pass

    async def broadcast_to_chat(self, chat_id: str, sender_id: str, data: dict):
        """پیام رو به همه‌ی اعضای چت (به‌جز فرستنده و اخراج‌شده‌ها) بفرست."""
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(ChatMember.user_id).where(
                    ChatMember.chat_id == chat_id,
                    ChatMember.banned == False,      # noqa: E712
                )
            )
            member_ids = [r[0] for r in result.all()]
        away: list[str] = []
        for uid in member_ids:
            if uid != sender_id:
                await self.send_to_user(uid, data)
                if not self.is_online(uid):
                    away.append(uid)
        # ── اعلانِ بیرونِ برنامه ──────────────────────
        # هر کس سوکتِ «جلو» ندارد (برنامه بسته، گوشی در جیب، برگه پشتِ
        # صحنه) از این‌جا هم خبردار می‌شود: صفِ long-poll برای سرویسِ بومیِ
        # اندروید و Web Push برای مرورگر. کاربرِ جلوی چشم را رد می‌کنیم تا
        # اعلانِ تکراری نگیرد — خودِ برنامه نشانش می‌دهد.
        if away and data.get("type") == "message":
            try:
                from . import push
                await push.dispatch(chat_id, sender_id, data, away)
            except Exception:
                pass

    async def notify_presence(self, user_id: str, online: bool,
                              last_seen: str | None = None):
        """تغییرِ حضور را به همه‌ی هم‌چتی‌ها می‌گوید تا نقطه‌ی آنلاین بی‌درنگ
        عوض شود (نه با پرسشِ دوره‌ایِ ۳۰ ثانیه‌ای)."""
        payload = {"type": "presence", "user_id": user_id,
                   "online": bool(online), "last_seen_at": last_seen}
        async with AsyncSessionLocal() as db:
            mine = (await db.execute(
                select(ChatMember.chat_id).where(ChatMember.user_id == user_id)
            )).all()
            chat_ids = [r[0] for r in mine]
            peers: Set[str] = set()
            if chat_ids:
                rows = (await db.execute(
                    select(ChatMember.user_id).where(
                        ChatMember.chat_id.in_(chat_ids),
                        ChatMember.banned == False,      # noqa: E712
                    )
                )).all()
                peers = {r[0] for r in rows if r[0] != user_id}
        for uid in peers:
            await self.send_to_user(uid, payload)


manager = ConnectionManager()


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket, token: str = ""):
    await ws.accept()

    if not token:
        await ws.send_text(json.dumps({"error": "توکن نیست"}))
        await ws.close(code=4001)
        return

    async with AsyncSessionLocal() as db:
        try:
            user = await ws_authenticate(token, db)
        except Exception:
            await ws.send_text(json.dumps({"error": "توکن نامعتبر"}))
            await ws.close(code=4001)
            return

    await manager.connect(user.id, ws)
    await _mark_seen(user.id)
    await manager.notify_presence(user.id, True)

    await ws.send_text(
        json.dumps(
            {"type": "connected", "user_id": user.id, "username": user.username},
            ensure_ascii=False,
        )
    )

    try:
        while True:
            raw = await ws.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await ws.send_text(json.dumps({"error": "JSON نامعتبر"}))
                continue

            chat_id = data.get("chat_id", "")
            content = data.get("content", "").strip()
            msg_type = data.get("msg_type", "text")
            reply_to = data.get("reply_to")
            enc = bool(data.get("enc"))
            caption = (data.get("caption") or "").strip() or None

            # نبضِ کلاینت — هر ۳۰ ثانیه می‌آید. جوابش لازم است، وگرنه
            # واچ‌داگِ کلاینت سوکت را «مرده» می‌بیند و می‌بندد. ضمناً همین
            # رفت‌وبرگشت سوکت را از دستِ مهلتِ بی‌کاریِ پروکسی (کلادفلر) در
            # می‌آورد. هیچ چیزی ذخیره نمی‌شود.
            if data.get("type") == "ping":
                await ws.send_text(json.dumps({"type": "pong"}))
                continue

            # رویدادِ حضور — کلاینت وقتی برنامه به پس‌زمینه می‌رود/برمی‌گردد
            # می‌فرستد. هیچ چیزی ذخیره نمی‌شود جز `last_seen_at` سرِ رفتن.
            if data.get("type") == "presence":
                fg = str(data.get("state") or "").lower() != "background"
                was = manager.is_online(user.id)
                await manager.set_foreground(user.id, ws, fg)
                now = manager.is_online(user.id)
                if was != now:
                    seen = None if now else await _mark_seen(user.id)
                    await manager.notify_presence(user.id, now, seen)
                continue

            # رویدادِ «داره تایپ می‌کنه» — ذخیره نمی‌شه، فقط پخش
            if data.get("type") == "typing" and chat_id:
                await manager.broadcast_to_chat(chat_id, user.id, {
                    "type": "typing", "chat_id": chat_id,
                    "user_id": user.id, "name": user.display_name,
                })
                continue

            if not chat_id or not content:
                await ws.send_text(json.dumps({"error": "chat_id و content لازمه"}))
                continue

            # ذخیره پیام در دیتابیس
            async with AsyncSessionLocal() as db:
                member = (
                    await db.execute(
                        select(ChatMember).where(
                            ChatMember.chat_id == chat_id,
                            ChatMember.user_id == user.id,
                        )
                    )
                ).scalar_one_or_none()

                if not member or member.banned:
                    await ws.send_text(json.dumps(
                        {"error": "عضو این چت نیستی"}, ensure_ascii=False))
                    continue

                chat = (
                    await db.execute(select(Chat).where(Chat.id == chat_id))
                ).scalar_one_or_none()

                # کانال: فقط مالک و مدیرها اجازه‌ی نوشتن دارن
                if chat is not None and (chat.kind or "") == KIND_CHANNEL:
                    if ROLE_RANK.get(member.role or ROLE_MEMBER, 1) < ROLE_RANK[ROLE_ADMIN]:
                        await ws.send_text(json.dumps(
                            {"error": "در این کانال فقط مدیرها می‌تونن پیام بذارن"},
                            ensure_ascii=False))
                        continue

                # چت خصوصی + بلاک ⇒ پیام ارسال نمی‌شه
                if chat is not None and not chat.is_group:
                    om = (
                        await db.execute(
                            select(ChatMember).where(
                                ChatMember.chat_id == chat_id,
                                ChatMember.user_id != user.id,
                            ).limit(1)
                        )
                    ).scalar_one_or_none()
                    if om is not None:
                        from .privacy import blocked_between
                        if await blocked_between(db, user.id, om.user_id):
                            await ws.send_text(json.dumps(
                                {"error": "امکان ارسال پیام به این کاربر نیست"},
                                ensure_ascii=False))
                            continue

                msg = Message(
                    chat_id=chat_id,
                    sender_id=user.id,
                    content=content,
                    msg_type=msg_type,
                    reply_to=reply_to,
                    enc=enc,
                    caption=caption,
                )
                db.add(msg)
                await db.commit()
                await db.refresh(msg)

                from .chat import _media_size

                reply_text = None
                if reply_to:
                    rm = (await db.execute(select(Message).where(Message.id == reply_to))).scalar_one_or_none()
                    if rm:
                        reply_text = "" if rm.deleted else rm.content[:80]

                outgoing = {
                    "type": "message",
                    "chat_id": chat_id,
                    "message_id": msg.id,
                    "sender_id": user.id,
                    "sender_name": user.display_name,
                    "content": content,
                    "msg_type": msg_type,
                    "reply_to": reply_to,
                    "reply_to_text": reply_text,
                    "created_at": _iso(msg.created_at),
                    "enc": enc,
                    "size": _media_size(content),
                    "caption": caption,
                }

            # ارسال به فرستنده (تأیید)
            await ws.send_text(json.dumps(outgoing, ensure_ascii=False))

            # ارسال به بقیه اعضای چت
            await manager.broadcast_to_chat(chat_id, user.id, outgoing)

    except WebSocketDisconnect:
        await _drop(user.id, ws)
    except Exception:
        await _drop(user.id, ws)


async def _mark_seen(user_id: str) -> str | None:
    """`last_seen_at` را همین حالا می‌نویسد و ISO آن را برمی‌گردانَد."""
    from .models import User
    now = datetime.now(timezone.utc)
    try:
        async with AsyncSessionLocal() as db:
            u = (await db.execute(
                select(User).where(User.id == user_id))).scalar_one_or_none()
            if u is None:
                return None
            u.last_seen_at = now
            db.add(u)
            await db.commit()
    except Exception:
        return None
    return now.isoformat()


async def _drop(user_id: str, ws: WebSocket) -> None:
    """قطعِ اتصال ⇒ اگر آخرین سوکتِ جلو بود، بی‌درنگ آفلاین."""
    was = manager.is_online(user_id)
    await manager.disconnect(user_id, ws)
    if was and not manager.is_online(user_id):
        seen = await _mark_seen(user_id)
        await manager.notify_presence(user_id, False, seen)
