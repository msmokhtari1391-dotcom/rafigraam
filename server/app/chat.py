"""
اندپوینت‌های چت — لیست چت‌ها، ساخت چت خصوصی، ارسال/دریافت پیام.
"""
import os
import re
import shutil
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import get_current_user
from .config import settings
from .db import get_session
from .models import (
    KIND_CHANNEL, KIND_GROUP, KIND_PRIVATE, KIND_SAVED, ROLE_ADMIN,
    ROLE_MEMBER, ROLE_OWNER, ROLE_RANK, Chat, ChatMember, Message, MessageHide,
    MessageReaction, User,
)


async def hidden_ids(db: AsyncSession, user_id: str) -> set:
    """شناسه‌ی پیام‌هایی که این کاربر «فقط برای خودم» پاکشون کرده."""
    rows = (await db.execute(
        select(MessageHide.message_id).where(MessageHide.user_id == user_id)
    )).all()
    return {r[0] for r in rows}


def _iso(dt) -> Optional[str]:
    """تاریخِ دیتابیس رو با منطقه‌ی زمانی UTC به ISO تبدیل می‌کنه."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def is_online(user_id: str) -> bool:
    """حضورِ زنده از رجیستریِ وب‌سوکت.

    وارد کردنِ تنبل است تا حلقه‌ی import پیش نیاید (`ws` خودش از `chat`
    استفاده می‌کند). اگر ماژول در دسترس نبود، «آفلاین» جوابِ محافظه‌کارانه
    است — بهتر از آنلاینِ دروغین.
    """
    try:
        from .ws import manager
        return manager.is_online(user_id)
    except Exception:
        return False


router = APIRouter(prefix="/chat", tags=["chat"])

# هر فرمتی مجازه. فایل‌ها با نامِ تصادفی ذخیره و از مسیر /media به‌صورت
# فایلِ ساکن سرو می‌شن (هیچ‌وقت اجرا نمی‌شن)، پس پسوند محدود نمی‌کنیم.
MAX_UPLOAD_BYTES = 100 * 1024 * 1024  # ۱۰۰ مگابایت


def _safe_ext(filename: str) -> str:
    """پسوندِ بی‌خطر: فقط حرف/رقم، حداکثر ۱۲ نویسه."""
    ext = os.path.splitext(os.path.basename(filename or ""))[1]
    ext = "".join(c for c in ext if c.isalnum() or c == ".")[:12].lower()
    return ext if ext.startswith(".") and len(ext) > 1 else ".bin"


def _media_size(*candidates) -> Optional[int]:
    """حجمِ فایلِ رسانه‌ای روی دیسک (بایت) از روی آدرسِ /media/... ."""
    for raw in candidates:
        if not raw or not isinstance(raw, str):
            continue
        path = raw.split("?", 1)[0].split("#", 1)[0]
        idx = path.find("/media/")
        if idx < 0:
            continue
        name = os.path.basename(path[idx + len("/media/"):])
        if not name:
            continue
        try:
            return (settings.media_dir / name).stat().st_size
        except OSError:
            continue
    return None


def _media_name(raw) -> Optional[str]:
    """نامِ فایل از دلِ یک آدرسِ `/media/...` — یا None اگر آدرسِ مدیا نبود."""
    if not raw or not isinstance(raw, str):
        return None
    path = raw.split("?", 1)[0].split("#", 1)[0]
    idx = path.find("/media/")
    if idx < 0:
        return None
    return os.path.basename(path[idx + len("/media/"):]) or None


def _dup_media(raw) -> Optional[str]:
    """کپیِ *فیزیکیِ* فایلِ رسانه با نامِ تازه. آدرسِ تازه را برمی‌گرداند.

    قلبِ «استقلالِ انبار» در پیام‌های ذخیره‌شده. اگر فقط رشته‌ی آدرس را
    کپی می‌کردیم، دو پیام به یک فایل اشاره می‌کردند و پاک‌کردنِ اصل، ذخیره‌شده
    را هم تو خالی می‌کرد. (امروز `maintenance.wipe()` تنها جایی است که فایل
    را از دیسک برمی‌دارد، پس آن اشاره‌ی مشترک *فعلاً* تصادفاً کار می‌کند —
    و یک زباله‌جمع‌کنِ فایلِ بی‌مالک، همان روز خرابش می‌کرد.)

    None یعنی «چیزی برای کپی نبود» و صداکننده باید همان آدرسِ اصلی را
    نگه دارد: پیامِ متنی، آدرسِ بیرونی، یا فایلی که دیگر روی دیسک نیست.
    """
    name = _media_name(raw)
    if not name:
        return None
    src = settings.media_dir / name
    try:
        if not src.is_file():
            return None
        ext = os.path.splitext(name)[1]
        ext = "".join(c for c in ext if c.isalnum() or c == ".")[:12].lower()
        if not ext.startswith(".") or len(ext) < 2:
            ext = ".bin"
        dst_name = f"{uuid.uuid4().hex}{ext}"
        shutil.copyfile(src, settings.media_dir / dst_name)
    except OSError:
        return None
    return f"/media/{dst_name}"


@router.post("/upload")
async def upload_media(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
):
    """آپلود فایل با هر فرمتی (عکس گروه، پیوستِ پیام). فقط کاربرِ لاگین‌کرده."""
    ext = _safe_ext(file.filename or "file.bin")
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "فایل بزرگ‌تر از ۱۰۰ مگابایته")
    filename = f"{uuid.uuid4().hex}{ext}"
    with open(settings.media_dir / filename, "wb") as f:
        f.write(content)
    return {"url": f"/media/{filename}", "name": file.filename or filename,
            "size": len(content)}


# ── ابزارهای نقش و دسترسی ──────────────────────────
async def _member_of(db: AsyncSession, chat_id: str, user_id: str):
    return (await db.execute(
        select(ChatMember).where(ChatMember.chat_id == chat_id,
                                 ChatMember.user_id == user_id)
    )).scalar_one_or_none()


async def _require_member(db: AsyncSession, chat_id: str, user_id: str) -> ChatMember:
    mem = await _member_of(db, chat_id, user_id)
    if not mem or mem.banned:
        raise HTTPException(403, "عضو این چت نیستی")
    return mem


async def _require_chat(db: AsyncSession, chat_id: str) -> Chat:
    chat = (await db.execute(
        select(Chat).where(Chat.id == chat_id)
    )).scalar_one_or_none()
    if not chat:
        raise HTTPException(404, "چت پیدا نشد")
    return chat


def _rank(mem: Optional[ChatMember]) -> int:
    return ROLE_RANK.get((mem.role if mem else ROLE_MEMBER) or ROLE_MEMBER, 1)


# ── پیام‌های ذخیره‌شده ──────────────────────────────
# عنوانِ جانشین. کلاینتِ تازه از روی `kind == "saved"` خودش به زبانِ کاربر
# می‌نویسدش؛ این رشته برای نسخه‌های قدیمیِ برنامه است که این نوع را نمی‌شناسند.
SAVED_TITLE = "پیام‌های ذخیره‌شده"


async def saved_chat(db: AsyncSession, user_id: str) -> Chat:
    """گفت‌وگوی «پیام‌های ذخیره‌شده»ی این کاربر — نبود، ساخته می‌شود.

    یک عضو دارد و همان صاحبش است، پس همه‌ی سنجه‌های موجود (`_require_member`،
    `can_post`، `hidden_ids`) بی هیچ استثنایی رویش کار می‌کنند.

    اگر مسابقه‌ی دو درخواستِ هم‌زمان دو تا ساخته باشد، همیشه *قدیمی‌ترین* را
    برمی‌گردانیم تا همه‌ی دستگاه‌ها یک‌جا بروند؛ `list_chats` هم دومی را
    نشان نمی‌دهد.
    """
    rows = (await db.execute(
        select(Chat).join(ChatMember, ChatMember.chat_id == Chat.id)
        .where(Chat.kind == KIND_SAVED, ChatMember.user_id == user_id)
        .order_by(Chat.created_at)
    )).scalars().all()
    if rows:
        return rows[0]
    chat = Chat(title=SAVED_TITLE, is_group=False, kind=KIND_SAVED,
                creator_id=user_id)
    db.add(chat)
    await db.flush()
    db.add(ChatMember(chat_id=chat.id, user_id=user_id, role=ROLE_OWNER))
    await db.commit()
    await db.refresh(chat)
    return chat


def _detach(src: Message) -> tuple[str, Optional[str]]:
    """`(content, file_url)`ِ یک نسخه‌ی *مستقل* از این پیام.

    مدیا در این برنامه دو جا می‌تواند بنشیند: `send` آدرس را در `content`
    می‌گذارد (`file_url` را دست نمی‌زند) و ردیف‌های قدیمی‌تر `file_url` دارند.
    پس هر دو را جدا می‌کنیم.

    هرکدام که فایلِ رسانه نبود یا فایلش دیگر روی دیسک نیست، دست‌نخورده
    می‌ماند: پیامِ متنی متن است، و اشاره به فایلِ گم‌شده هم بهتر از خالی‌کردنِ
    پیام است.
    """
    content = _dup_media(src.content) or src.content
    file_url = _dup_media(src.file_url) or src.file_url
    return content, file_url


def _is_saved(chat: Optional[Chat]) -> bool:
    return bool(chat is not None and (chat.kind or "") == KIND_SAVED)


def _is_manager(mem: Optional[ChatMember]) -> bool:
    """صاحب یا مدیر؟"""
    return _rank(mem) >= ROLE_RANK[ROLE_ADMIN]


def _norm_handle(raw: Optional[str]) -> Optional[str]:
    h = (raw or "").strip().lstrip("@").lower()
    if not h:
        return None
    if len(h) < 3 or len(h) > 32:
        raise HTTPException(400, "شناسه باید بین ۳ تا ۳۲ نویسه باشه")
    if not all(c.isalnum() or c == "_" for c in h):
        raise HTTPException(400, "شناسه فقط حرف و رقم و _ می‌پذیره")
    return h


async def _handle_taken(db: AsyncSession, handle: str, skip_chat: str = "") -> bool:
    row = (await db.execute(
        select(Chat).where(Chat.handle == handle)
    )).scalars().first()
    return bool(row and row.id != skip_chat)


async def _reactions_for(db: AsyncSession, message_ids: list[str], me: str):
    """{message_id: {emoji: count}} و {message_id: [ایموجی‌های خودم]}"""
    counts: dict[str, dict[str, int]] = {}
    mine: dict[str, list[str]] = {}
    if not message_ids:
        return counts, mine
    rows = (await db.execute(
        select(MessageReaction).where(MessageReaction.message_id.in_(message_ids))
    )).scalars().all()
    for r in rows:
        counts.setdefault(r.message_id, {})
        counts[r.message_id][r.emoji] = counts[r.message_id].get(r.emoji, 0) + 1
        if r.user_id == me:
            mine.setdefault(r.message_id, []).append(r.emoji)
    return counts, mine


# ── اسکیماها ───────────────────────────────────────
class CreateChatRequest(BaseModel):
    username: str  # یوزرنیم طرف مقابل


class CreateGroupRequest(BaseModel):
    title: str
    members: list[str] = []
    bio: Optional[str] = None
    avatar_url: Optional[str] = None
    kind: str = KIND_GROUP           # group | channel
    is_public: bool = False
    handle: Optional[str] = None


class SendMessageRequest(BaseModel):
    content: str
    msg_type: str = "text"
    reply_to: Optional[str] = None
    enc: bool = False
    caption: Optional[str] = None
    file_name: Optional[str] = None


class ReportRequest(BaseModel):
    target_type: str  # "user", "message", "story", "channel"
    target_id: str
    reason: str  # "spam", "violence", "pornography", "illegal", "other"
    details: Optional[str] = None


class UserOut(BaseModel):
    user_id: str
    username: str
    display_name: str
    avatar_url: Optional[str] = None
    last_seen_at: Optional[str] = None
    # حضورِ واقعی — از سوکت‌های بازِ «جلو»، نه از حدسِ روی last_seen_at
    online: bool = False
    role: str = ROLE_MEMBER
    banned: bool = False


class ChatOut(BaseModel):
    chat_id: str
    title: str
    is_group: bool = False
    avatar_url: Optional[str] = None
    other_user: Optional[UserOut] = None
    last_message: Optional[str] = None
    last_message_type: Optional[str] = None
    last_sender_id: Optional[str] = None
    last_message_at: Optional[str] = None
    unread: int = 0
    kind: str = KIND_PRIVATE
    is_public: bool = False
    handle: Optional[str] = None
    archived: bool = False
    my_role: str = ROLE_MEMBER


class MessageOut(BaseModel):
    message_id: str
    sender_id: str
    sender_name: str
    content: str
    msg_type: str
    file_url: Optional[str] = None
    reply_to: Optional[str] = None
    reply_to_text: Optional[str] = None
    created_at: str
    edited: bool = False
    deleted: bool = False
    pinned: bool = False
    forward_from: Optional[str] = None
    enc: bool = False
    reactions: dict[str, int] = {}
    my_reactions: list[str] = []
    size: Optional[int] = None
    caption: Optional[str] = None
    file_name: Optional[str] = None


class SearchHitOut(BaseModel):
    """یک نتیجه‌ی جست‌وجوی سراسری."""
    kind: str                 # user | group | channel
    title: str
    handle: Optional[str] = None
    avatar_url: Optional[str] = None
    bio: Optional[str] = None
    chat_id: Optional[str] = None
    username: Optional[str] = None
    members: int = 0
    joined: bool = False


# ── لیست چت‌های کاربر ─────────────────────────────
@router.get("/list", response_model=list[ChatOut])
async def list_chats(
    archived: bool = False,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    # «پیام‌های ذخیره‌شده» باید در فهرستِ *هر* کاربر باشد، بی اینکه خودش
    # کاری کرده باشد. این‌جا می‌سازیمش چون تنها نقطه‌ای است که هر کلاینتی —
    # وب، اندروید، ویندوز، تازه یا قدیمی — سرِ هر بار باز شدن می‌زندش. بارِ
    # اضافه‌اش یک SELECTِ نمایه‌دار است، آن هم فقط در فهرستِ اصلی.
    if not archived:
        try:
            await saved_chat(db, user.id)
        except Exception:
            pass          # ساختنش نشد؟ بقیه‌ی فهرست نباید قربانی شود

    # همه‌ی عضویت‌های کاربر (شامل last_read_at برای شمارشِ نخونده‌ها)
    my_members = (
        await db.execute(
            select(ChatMember).where(ChatMember.user_id == user.id)
        )
    ).scalars().all()
    if not my_members:
        return []

    hid = await hidden_ids(db, user.id)
    chats_out = []
    saw_saved = False
    for mem in my_members:
        if bool(mem.banned):
            continue
        if bool(mem.archived) != bool(archived):
            continue
        cid = mem.chat_id
        chat = (
            await db.execute(select(Chat).where(Chat.id == cid))
        ).scalar_one_or_none()
        if chat is None:
            continue
        if (chat.kind or "") == KIND_SAVED:
            if saw_saved:
                continue      # مسابقه‌ی ساخت دو تا ساخته؛ یکی بس است
            saw_saved = True

        other_user = None
        title = chat.title or ""
        avatar_url = chat.avatar_url

        if not chat.is_group:
            other_member = (
                await db.execute(
                    select(ChatMember)
                    .where(ChatMember.chat_id == cid, ChatMember.user_id != user.id)
                    .limit(1)
                )
            ).scalar_one_or_none()
            if other_member:
                ou = (
                    await db.execute(
                        select(User).where(User.id == other_member.user_id)
                    )
                ).scalar_one_or_none()
                if ou:
                    other_user = UserOut(
                        user_id=ou.id, username=ou.username,
                        display_name=ou.display_name, avatar_url=ou.avatar_url,
                        last_seen_at=(_iso(ou.last_seen_at)
                                      if ou.show_last_seen else None),
                        online=(is_online(ou.id) if ou.show_last_seen
                                else False),
                    )
                    # اسم و عکسِ چتِ خصوصی همیشه از خودِ طرف مقابل میاد،
                    # تا تغییر نام/عکسِ او فوراً در لیست دیده بشه.
                    title = ou.display_name
                    avatar_url = ou.avatar_url

        # آخرین پیام (پاک‌شده‌ها — چه برای همه چه فقط برای خودم — شمرده نمی‌شن)
        lq = (select(Message)
              .where(Message.chat_id == cid,
                     Message.deleted == False)      # noqa: E712
              .order_by(desc(Message.created_at)))
        if hid:
            lq = lq.where(Message.id.notin_(hid))
        last_msg = (await db.execute(lq.limit(1))).scalar_one_or_none()

        # تعداد پیام‌های نخونده (پیام‌های دیگران بعد از آخرین بازدید)
        unread = 0
        if last_msg is not None:
            cnt_q = select(func.count(Message.id)).where(
                Message.chat_id == cid, Message.sender_id != user.id,
                Message.deleted == False,            # noqa: E712
            )
            if hid:
                cnt_q = cnt_q.where(Message.id.notin_(hid))
            if mem.last_read_at is not None:
                cnt_q = cnt_q.where(Message.created_at > mem.last_read_at)
            unread = int((await db.execute(cnt_q)).scalar() or 0)

        # اگه چت هنوز پیامی نداره، با زمان ساختش مرتب می‌شه
        # (نه ته لیست) تا چتِ تازه‌ساخته‌شده بالا بیاد.
        stamp = _iso(last_msg.created_at) if last_msg else _iso(chat.created_at)

        chats_out.append(
            ChatOut(
                chat_id=cid,
                title=title,
                is_group=bool(chat.is_group),
                avatar_url=avatar_url,
                other_user=other_user,
                last_message=(None if (last_msg and last_msg.deleted)
                              else (last_msg.content if last_msg else None)),
                last_message_type=(
                    "deleted" if (last_msg and last_msg.deleted)
                    else (last_msg.msg_type if last_msg else None)),
                last_sender_id=last_msg.sender_id if last_msg else None,
                last_message_at=stamp,
                unread=unread,
                kind=chat.kind or (KIND_GROUP if chat.is_group else KIND_PRIVATE),
                is_public=bool(chat.is_public),
                handle=chat.handle,
                archived=bool(mem.archived),
                my_role=mem.role or ROLE_MEMBER,
            )
        )

    # مرتب‌سازی بر اساس آخرین پیام
    chats_out.sort(
        key=lambda c: c.last_message_at or "0000", reverse=True
    )
    # ...و یک استثنا: «پیام‌های ذخیره‌شده» همیشه بالای فهرست می‌نشیند (مثلِ
    # تلگرام)، وگرنه دفترچه‌ی یادداشتی که یک هفته بازش نکرده‌ای ته فهرست گم
    # می‌شود. جداسازی است نه مرتب‌سازیِ دوباره، پس ترتیبِ بقیه دست‌نخورده
    # می‌ماند (`sort` پایتون پایدار است، ولی این‌جا حتی به آن هم تکیه نمی‌کنیم).
    top = [c for c in chats_out if (c.kind or "") == KIND_SAVED]
    return top + [c for c in chats_out if (c.kind or "") != KIND_SAVED]


# ── شمارشِ چت‌های بایگانی‌شده (برای پوشه‌ی «بایگانی») ──
@router.get("/archived_count")
async def archived_count(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    n = int((await db.execute(
        select(func.count(ChatMember.id)).where(
            ChatMember.user_id == user.id,
            ChatMember.archived == True,      # noqa: E712
            ChatMember.banned == False,       # noqa: E712
        )
    )).scalar() or 0)
    return {"count": n}


# ── شناسه‌ی «پیام‌های ذخیره‌شده» ─────────────────────
# پیش از هر مسیرِ `/{chat_id}` می‌نشیند تا «saved» شناسه‌ی چت خوانده نشود.
@router.get("/saved")
async def my_saved_chat(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """گفت‌وگوی «پیام‌های ذخیره‌شده»ی من — نبود، همین‌جا ساخته می‌شود.

    کلاینت وقتی این را صدا می‌زند که کاربر «ذخیره» را زده و می‌خواهد همان‌جا
    برود؛ منتظرِ رفرشِ فهرست ماندن یعنی یک کلیکِ بی‌جواب.
    """
    chat = await saved_chat(db, user.id)
    return {"chat_id": chat.id, "kind": KIND_SAVED, "title": chat.title or ""}


class ArchiveRequest(BaseModel):
    archived: bool = True


@router.post("/{chat_id}/archive")
async def set_archived(
    chat_id: str,
    body: ArchiveRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """بایگانی/برگرداندنِ یک چت — فقط برای خودِ کاربر."""
    mem = await _require_member(db, chat_id, user.id)
    mem.archived = bool(body.archived)
    db.add(mem)
    await db.commit()
    return {"ok": True, "archived": mem.archived}


# ── علامت‌زدن چت به‌عنوان خوانده‌شده ────────────────
@router.post("/{chat_id}/read")
async def mark_read(
    chat_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    mem = (
        await db.execute(
            select(ChatMember).where(
                ChatMember.chat_id == chat_id, ChatMember.user_id == user.id
            )
        )
    ).scalar_one_or_none()
    if not mem:
        raise HTTPException(403, "عضو این چت نیستی")
    mem.last_read_at = datetime.now(timezone.utc)
    db.add(mem)
    await db.commit()
    # به بقیه بگو تا تیکِ پیام‌هاشون دوبل بشه (بی‌نیاز به تازه‌کردنِ صفحه)
    await notify_chat(chat_id, {
        "type": "read", "chat_id": chat_id, "user_id": user.id,
        "at": _iso(mem.last_read_at),
    }, skip=user.id)
    return {"ok": True}


# ── ساخت چت خصوصی ─────────────────────────────────
@router.post("/create")
async def create_chat(
    body: CreateChatRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    # پیدا کردن کاربر مقابل
    other = (
        await db.execute(select(User).where(User.username == body.username))
    ).scalar_one_or_none()
    if not other:
        raise HTTPException(404, "کاربر پیدا نشد")
    if other.id == user.id:
        raise HTTPException(400, "نمی‌تونی با خودت چت بزنی")
    from .privacy import blocked_between
    if await blocked_between(db, user.id, other.id):
        raise HTTPException(403, "امکان چت با این کاربر نیست")

    # چک کن قبلاً چت خصوصی داریم یا نه
    my_memberships = (
        await db.execute(
            select(ChatMember).where(ChatMember.user_id == user.id)
        )
    ).scalars().all()
    for m in my_memberships:
        chat = (
            await db.execute(select(Chat).where(Chat.id == m.chat_id))
        ).scalar_one()
        if not chat.is_group:
            other_m = (
                await db.execute(
                    select(ChatMember)
                    .where(
                        ChatMember.chat_id == chat.id,
                        ChatMember.user_id != user.id,
                    )
                )
            ).scalar_one_or_none()
            if other_m and other_m.user_id == other.id:
                return {"chat_id": chat.id, "existing": True}

    # ساخت چت جدید
    chat = Chat(title=None, is_group=False, kind=KIND_PRIVATE)
    db.add(chat)
    await db.flush()
    db.add(ChatMember(chat_id=chat.id, user_id=user.id, role=ROLE_MEMBER))
    db.add(ChatMember(chat_id=chat.id, user_id=other.id, role=ROLE_MEMBER))
    await db.commit()
    # به طرف مقابل خبر بده تا لیست چتش بی‌درنگ تازه بشه
    await notify_chat_created(chat.id, [other.id])
    return {"chat_id": chat.id, "existing": False}


async def notify_chat_created(chat_id: str, user_ids: list[str]) -> None:
    """رویداد «چت تازه ساخته شد» رو برای اعضا از طریق WebSocket می‌فرسته."""
    try:
        from .ws import manager
        for uid in user_ids:
            await manager.send_to_user(
                uid, {"type": "chat_created", "chat_id": chat_id}
            )
    except Exception:
        pass  # نبودِ اتصالِ ریل‌تایم نباید ساختِ چت رو خراب کنه


async def notify_chat(chat_id: str, data: dict, skip: str = "") -> None:
    """رویدادِ دلخواه رو برای همه‌ی اعضای چت می‌فرسته (به‌جز `skip`)."""
    try:
        from .ws import manager
        await manager.broadcast_to_chat(chat_id, skip, data)
    except Exception:
        pass


async def notify_users(user_ids: list[str], data: dict) -> None:
    try:
        from .ws import manager
        for uid in user_ids:
            await manager.send_to_user(uid, data)
    except Exception:
        pass


# ── ساخت گروه یا کانال ────────────────────────────
@router.post("/create_group")
async def create_group(
    body: CreateGroupRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    if not body.title.strip():
        raise HTTPException(400, "عنوان گروه لازمه")
    kind = body.kind if body.kind in (KIND_GROUP, KIND_CHANNEL) else KIND_GROUP
    handle = _norm_handle(body.handle)
    if body.is_public and not handle:
        raise HTTPException(400, "گروه/کانالِ عمومی باید شناسه (@) داشته باشه")
    if handle and await _handle_taken(db, handle):
        raise HTTPException(409, "این شناسه گرفته شده")

    # پیدا کردن اعضا (کانالِ عمومی می‌تونه بدون عضوِ اولیه ساخته شه)
    member_users = []
    for uname in body.members:
        uname = (uname or "").strip().lstrip("@").lower()
        if not uname:
            continue
        u = (
            await db.execute(select(User).where(User.username == uname))
        ).scalar_one_or_none()
        if not u:
            raise HTTPException(404, f"کاربر '{uname}' پیدا نشد")
        member_users.append(u)
    if not body.is_public and not member_users:
        raise HTTPException(400, "حداقل یک عضو اضافه کن")

    chat = Chat(title=body.title.strip(), is_group=True, bio=body.bio,
                avatar_url=body.avatar_url, creator_id=user.id,
                kind=kind, is_public=bool(body.is_public), handle=handle)
    db.add(chat)
    await db.flush()

    # سازنده = صاحب؛ بقیه = عضو (بدون تکرار)
    db.add(ChatMember(chat_id=chat.id, user_id=user.id, role=ROLE_OWNER))
    for u in member_users:
        if u.id != user.id:
            db.add(ChatMember(chat_id=chat.id, user_id=u.id, role=ROLE_MEMBER))

    await db.commit()
    await notify_chat_created(
        chat.id, [u.id for u in member_users if u.id != user.id]
    )
    return {"chat_id": chat.id, "kind": kind, "handle": handle,
            "is_public": bool(body.is_public)}


# ── جست‌وجوی سراسری: کاربر، گروه/کانالِ عمومی، @شناسه ──
@router.get("/search", response_model=list[SearchHitOut])
async def search_all(
    q: str = "",
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """نامِ کاربر/گروه یا `@شناسه`.

    گروه و کانالِ عمومی با نام یا شناسه پیدا می‌شن؛ خصوصی‌ها فقط با
    شناسه‌ی دقیق (یعنی باید `@handle` کامل رو بدونی).
    """
    raw = (q or "").strip()
    if not raw.lstrip("@"):
        return []
    exact = raw.startswith("@")
    term = raw.lstrip("@").lower()
    like = f"%{term}%"
    hits: list[SearchHitOut] = []

    # عضویت‌های من (برای علامتِ «عضو هستم»)
    mine = {m.chat_id: m for m in (await db.execute(
        select(ChatMember).where(ChatMember.user_id == user.id)
    )).scalars().all()}

    # ۱) کاربران
    users = (await db.execute(
        select(User).where(
            (func.lower(User.username) == term) if exact
            else (func.lower(User.username).like(like)
                  | func.lower(User.display_name).like(like))
        ).limit(25)
    )).scalars().all()
    for u in users:
        if u.id == user.id:
            continue
        hits.append(SearchHitOut(
            kind="user", title=u.display_name, handle=u.username,
            username=u.username, avatar_url=u.avatar_url, bio=u.bio))

    # ۲) گروه و کانالِ عمومی یا دارای شناسه
    if exact:
        cond = (func.lower(Chat.handle) == term)
    else:
        cond = (func.lower(Chat.handle).like(like) | func.lower(Chat.title).like(like))
    cond = cond & ((Chat.is_public == True) | (Chat.id.in_(mine.keys())) | (Chat.handle != None))

    chats = (await db.execute(
        select(Chat).where(Chat.is_group == True, cond).limit(25)
    )).scalars().all()
    for c in chats:
        if not c.is_public and (c.handle or "").lower() != term:
            continue
        n = int((await db.execute(
            select(func.count(ChatMember.id)).where(
                ChatMember.chat_id == c.id,
                ChatMember.banned == False)  # noqa: E712
        )).scalar() or 0)
        hits.append(SearchHitOut(
            kind=c.kind or KIND_GROUP, title=c.title or "", handle=c.handle,
            avatar_url=c.avatar_url, bio=c.bio, chat_id=c.id, members=n,
            joined=(c.id in mine and not mine[c.id].banned)))
    return hits


class JoinRequest(BaseModel):
    handle: Optional[str] = None
    chat_id: Optional[str] = None


@router.post("/join")
async def join_chat(
    body: JoinRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """عضو شدن در گروه/کانال — با `@شناسه` یا شناسه‌ی چت (از نتایجِ جست‌وجو)."""
    chat = None
    if body.chat_id:
        chat = (await db.execute(
            select(Chat).where(Chat.id == body.chat_id)
        )).scalar_one_or_none()
    elif body.handle:
        h = _norm_handle(body.handle)
        chat = (await db.execute(
            select(Chat).where(func.lower(Chat.handle) == h)
        )).scalars().first()
    if not chat or not chat.is_group:
        raise HTTPException(404, "گروه یا کانال پیدا نشد")
    # خصوصی: فقط با شناسه‌ی دقیق می‌شه عضو شد
    if not chat.is_public and not body.handle:
        raise HTTPException(403, "این گروه خصوصیه — با شناسه‌ی دقیق عضو شو")

    mem = await _member_of(db, chat.id, user.id)
    if mem is not None:
        if mem.banned:
            raise HTTPException(403, "از این گروه اخراج شدی")
        return {"chat_id": chat.id, "existing": True}
    db.add(ChatMember(chat_id=chat.id, user_id=user.id, role=ROLE_MEMBER))
    await db.commit()
    await notify_chat(chat.id, {"type": "chat_updated", "chat_id": chat.id},
                      skip=user.id)
    return {"chat_id": chat.id, "existing": False}


@router.post("/{chat_id}/leave")
async def leave_chat(
    chat_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """ترکِ گروه/کانال. صاحب باید اول مالکیت رو واگذار کنه."""
    chat = await _require_chat(db, chat_id)
    mem = await _require_member(db, chat_id, user.id)
    if not chat.is_group:
        raise HTTPException(400, "چتِ خصوصی رو نمی‌شه ترک کرد")
    if (mem.role or ROLE_MEMBER) == ROLE_OWNER:
        raise HTTPException(400, "اول مالکیت رو به یکی دیگه بده")
    await db.delete(mem)
    await db.commit()
    await notify_chat(chat_id, {"type": "chat_updated", "chat_id": chat_id},
                      skip=user.id)
    return {"ok": True}


# ── ارسال پیام ─────────────────────────────────────
async def can_post(db: AsyncSession, chat: Chat, mem: ChatMember) -> bool:
    """در کانال فقط صاحب و مدیرها می‌نویسن؛ بقیه‌جا هر عضوِ اخراج‌نشده."""
    if mem is None or mem.banned:
        return False
    if (chat.kind or "") == KIND_CHANNEL:
        return _is_manager(mem)
    return True


@router.post("/{chat_id}/send", response_model=MessageOut)
async def send_message(
    chat_id: str,
    body: SendMessageRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    # چک عضویت
    member = await _require_member(db, chat_id, user.id)

    chat = (
        await db.execute(select(Chat).where(Chat.id == chat_id))
    ).scalar_one_or_none()
    if chat is None:
        raise HTTPException(404, "چت پیدا نشد")
    if not await can_post(db, chat, member):
        raise HTTPException(403, "در این کانال فقط مدیرها می‌تونن پیام بذارن")

    # در چت خصوصی، اگه بلاک باشه پیام رد می‌شه
    if not chat.is_group:
        om = (
            await db.execute(
                select(ChatMember).where(
                    ChatMember.chat_id == chat_id, ChatMember.user_id != user.id
                ).limit(1)
            )
        ).scalar_one_or_none()
        if om is not None:
            from .privacy import blocked_between
            if await blocked_between(db, user.id, om.user_id):
                raise HTTPException(403, "امکان ارسال پیام به این کاربر نیست")

    msg = Message(
        chat_id=chat_id,
        sender_id=user.id,
        content=body.content,
        msg_type=body.msg_type,
        reply_to=body.reply_to,
        enc=bool(body.enc),
        caption=((body.caption or "").strip() or None),
        file_name=((body.file_name or "").strip() or None),
    )
    db.add(msg)
    await db.commit()
    await db.refresh(msg)

    # متن پیام ریپلای شده
    reply_text = None
    if msg.reply_to:
        orig = (await db.execute(select(Message).where(Message.id == msg.reply_to))).scalar_one_or_none()
        if orig:
            reply_text = orig.content[:80]

    out = MessageOut(
        message_id=msg.id,
        sender_id=msg.sender_id,
        sender_name=user.display_name,
        content=msg.content,
        msg_type=msg.msg_type,
        file_url=msg.file_url,
        reply_to=msg.reply_to,
        reply_to_text=reply_text,
        created_at=_iso(msg.created_at) or "",
        enc=bool(msg.enc),
        size=_media_size(msg.file_url, msg.content),
        caption=msg.caption,
        file_name=msg.file_name,
    )

    # مسیر HTTP هم باید بقیه‌ی اعضا رو ریل‌تایم خبر کنه
    # (وگرنه لیست چتِ گیرنده تا ریفرش دستی به‌روز نمی‌شه).
    frame = {"type": "message", "chat_id": chat_id, **out.model_dump()}
    if _is_saved(chat):
        # دفترچه‌ی یادداشت تنها یک عضو دارد: خودم. پس «به‌جز خودم» یعنی هیچ‌کس
        # و یادداشت روی گوشی، روی ویندوز هم باید بنشیند.
        await notify_users([user.id], frame)
    else:
        try:
            from .ws import manager
            await manager.broadcast_to_chat(chat_id, user.id, frame)
        except Exception:
            pass

    return out


# ── دریافت پیام‌های یک چت ─────────────────────────
@router.get("/{chat_id}/messages", response_model=list[MessageOut])
async def get_messages(
    chat_id: str,
    limit: int = 50,
    before: Optional[str] = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    # چک عضویت
    await _require_member(db, chat_id, user.id)

    q = select(Message).where(Message.chat_id == chat_id)

    # پیجینیشن: فقط پیام‌های قبل از یک پیام خاص
    if before:
        before_msg = (
            await db.execute(
                select(Message).where(Message.id == before)
            )
        ).scalar_one_or_none()
        if before_msg:
            q = q.where(Message.created_at < before_msg.created_at)

    q = q.order_by(desc(Message.created_at)).limit(limit)
    result = await db.execute(q)
    messages = result.scalars().all()

    # پیام‌هایی که خودم «فقط برای خودم» پاک کرده‌ام دیده نمی‌شن،
    # و ردیف‌های حذف‌شده‌ی قدیمی هم دیگه سنگِ قبر نشون نمی‌دن (مثل تلگرام).
    hid = await hidden_ids(db, user.id)
    messages = [m for m in messages if not m.deleted and m.id not in hid]

    # برگردون از قدیم به جدید
    messages.reverse()

    # اسم فرستنده‌ها + متن پیام‌های ریپلای‌شده
    sender_ids = {m.sender_id for m in messages}
    reply_ids = {m.reply_to for m in messages if m.reply_to}
    senders = {}
    reply_texts = {}
    for sid in sender_ids:
        u = (await db.execute(select(User).where(User.id == sid))).scalar_one_or_none()
        if u:
            senders[sid] = u.display_name
    for rid in reply_ids:
        rm = (await db.execute(select(Message).where(Message.id == rid))).scalar_one_or_none()
        if rm:
            reply_texts[rid] = "" if rm.deleted else rm.content[:80]

    counts, mine = await _reactions_for(db, [m.id for m in messages], user.id)

    return [
        MessageOut(
            message_id=m.id,
            sender_id=m.sender_id,
            sender_name=senders.get(m.sender_id, "ناشناس"),
            content=("" if m.deleted else m.content),
            msg_type=("deleted" if m.deleted else m.msg_type),
            file_url=(None if m.deleted else m.file_url),
            reply_to=m.reply_to,
            reply_to_text=reply_texts.get(m.reply_to) if m.reply_to else None,
            created_at=_iso(m.created_at) or "",
            edited=bool(m.edited_at),
            deleted=bool(m.deleted),
            pinned=bool(m.pinned),
            forward_from=m.forward_from,
            enc=bool(m.enc),
            reactions=counts.get(m.id, {}),
            my_reactions=mine.get(m.id, []),
            size=(None if m.deleted else _media_size(m.file_url, m.content)),
            caption=(None if m.deleted else m.caption),
        )
        for m in messages
    ]


# ── عملیاتِ روی پیام ───────────────────────────────
async def _own_message(db: AsyncSession, chat_id: str, mid: str) -> Message:
    msg = (await db.execute(
        select(Message).where(Message.id == mid, Message.chat_id == chat_id)
    )).scalar_one_or_none()
    if msg is None:
        raise HTTPException(404, "پیام پیدا نشد")
    return msg


class EditMessageRequest(BaseModel):
    content: str
    enc: bool = False


@router.put("/{chat_id}/message/{mid}", response_model=MessageOut)
async def edit_message(
    chat_id: str,
    mid: str,
    body: EditMessageRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """ویرایشِ متنِ پیام — فقط فرستنده‌ی خودش."""
    await _require_member(db, chat_id, user.id)
    msg = await _own_message(db, chat_id, mid)
    if msg.sender_id != user.id:
        raise HTTPException(403, "فقط پیام خودت رو می‌تونی ویرایش کنی")
    if msg.deleted:
        raise HTTPException(400, "این پیام حذف شده")
    if msg.msg_type not in ("text",):
        raise HTTPException(400, "فقط پیام متنی قابلِ ویرایشه")
    text_new = (body.content or "").strip()
    if not text_new:
        raise HTTPException(400, "متن خالیه")
    msg.content = text_new
    msg.enc = bool(body.enc)
    msg.edited_at = datetime.now(timezone.utc)
    db.add(msg)
    await db.commit()
    await db.refresh(msg)
    out = MessageOut(
        message_id=msg.id, sender_id=msg.sender_id, sender_name=user.display_name,
        content=msg.content, msg_type=msg.msg_type, file_url=msg.file_url,
        reply_to=msg.reply_to, created_at=_iso(msg.created_at) or "",
        edited=True, enc=bool(msg.enc),
        size=_media_size(msg.file_url, msg.content),
        caption=msg.caption,
        file_name=msg.file_name,
    )
    await notify_chat(chat_id, {
        "type": "message_edited", "chat_id": chat_id, **out.model_dump()
    }, skip=user.id)
    return out


@router.post("/{chat_id}/clear")
async def clear_chat_history(
    chat_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """پاک کردن تاریخچه‌ی گفت‌وگو برای کاربر جاری (Clear History)."""
    await _require_member(db, chat_id, user.id)
    msgs = (await db.execute(select(Message.id).where(Message.chat_id == chat_id))).scalars().all()
    for mid in msgs:
        old = (await db.execute(
            select(MessageHide).where(MessageHide.message_id == mid, MessageHide.user_id == user.id)
        )).scalar_one_or_none()
        if old is None:
            db.add(MessageHide(message_id=mid, user_id=user.id))
    await db.commit()
    return {"ok": True}


@router.delete("/{chat_id}/message/{mid}")
async def delete_message(
    chat_id: str,
    mid: str,
    for_everyone: bool = False,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """حذف پیام — یا فقط برای خودم (پنهان‌کردن) یا برای همه (پاکِ کامل)."""
    mem = await _require_member(db, chat_id, user.id)
    msg = await _own_message(db, chat_id, mid)

    if not for_everyone:
        # «فقط برای خودم» — یک ردیفِ پنهان‌سازی؛ طرف مقابل هیچ خبری نمی‌شه
        old = (await db.execute(
            select(MessageHide).where(MessageHide.message_id == mid,
                                     MessageHide.user_id == user.id)
        )).scalar_one_or_none()
        if old is None:
            db.add(MessageHide(message_id=mid, user_id=user.id))
            await db.commit()
        return {"ok": True, "for_everyone": False}

    if msg.sender_id != user.id and not _is_manager(mem):
        raise HTTPException(403, "اجازه‌ی حذفِ این پیام رو نداری")

    # «برای همه» — مثل تلگرام کاملاً پاک می‌شه، هیچ ردی نمی‌مونه
    for row in (await db.execute(
        select(MessageReaction).where(MessageReaction.message_id == mid)
    )).scalars().all():
        await db.delete(row)
    for row in (await db.execute(
        select(MessageHide).where(MessageHide.message_id == mid)
    )).scalars().all():
        await db.delete(row)
    for row in (await db.execute(
        select(Message).where(Message.reply_to == mid)
    )).scalars().all():
        row.reply_to = None
        db.add(row)
    await db.delete(msg)
    await db.commit()
    await notify_chat(chat_id, {
        "type": "message_deleted", "chat_id": chat_id, "message_id": mid,
    }, skip=user.id)
    return {"ok": True, "for_everyone": True}


class PinRequest(BaseModel):
    pinned: bool = True


@router.post("/{chat_id}/message/{mid}/pin")
async def pin_message(
    chat_id: str,
    mid: str,
    body: PinRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """سنجاق‌کردن — در گروه/کانال فقط مدیرها، در چت خصوصی هر دو طرف."""
    mem = await _require_member(db, chat_id, user.id)
    chat = await _require_chat(db, chat_id)
    if chat.is_group and not _is_manager(mem):
        raise HTTPException(403, "فقط مدیرها می‌تونن پیام سنجاق کنن")
    msg = await _own_message(db, chat_id, mid)
    if msg.deleted:
        raise HTTPException(400, "این پیام حذف شده")
    if body.pinned:
        # هر چت فقط یک پیامِ سنجاق‌شده داره (مثل تلگرام: آخرین سنجاق بالاست)
        others = (await db.execute(
            select(Message).where(
                Message.chat_id == chat_id, Message.pinned == True,  # noqa: E712
            )
        )).scalars().all()
        for o in others:
            o.pinned = False
            db.add(o)
    msg.pinned = bool(body.pinned)
    db.add(msg)
    await db.commit()
    await notify_chat(chat_id, {
        "type": "message_pinned", "chat_id": chat_id,
        "message_id": mid, "pinned": bool(msg.pinned),
    }, skip=user.id)
    return {"ok": True, "pinned": bool(msg.pinned)}


@router.get("/{chat_id}/pinned", response_model=Optional[MessageOut])
async def pinned_message(
    chat_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """پیامِ سنجاق‌شده‌ی این چت (اگه باشه)."""
    await _require_member(db, chat_id, user.id)
    hid = await hidden_ids(db, user.id)
    pq = select(Message).where(
        Message.chat_id == chat_id,
        Message.pinned == True,          # noqa: E712
        Message.deleted == False,        # noqa: E712
    )
    if hid:
        pq = pq.where(Message.id.notin_(hid))
    msg = (await db.execute(
        pq.order_by(desc(Message.created_at)).limit(1)
    )).scalar_one_or_none()
    if msg is None:
        return None
    u = (await db.execute(select(User).where(User.id == msg.sender_id))).scalar_one_or_none()
    return MessageOut(
        message_id=msg.id, sender_id=msg.sender_id,
        sender_name=(u.display_name if u else "ناشناس"),
        content=msg.content, msg_type=msg.msg_type, file_url=msg.file_url,
        reply_to=msg.reply_to, created_at=_iso(msg.created_at) or "",
        edited=bool(msg.edited_at), pinned=True, enc=bool(msg.enc),
        forward_from=msg.forward_from,
        size=_media_size(msg.file_url, msg.content),
        caption=msg.caption,
        file_name=msg.file_name,
    )


class ReactRequest(BaseModel):
    emoji: str


@router.post("/{chat_id}/message/{mid}/react")
async def react_message(
    chat_id: str,
    mid: str,
    body: ReactRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """واکنش رو اضافه/برداشته می‌کنه (toggle) و نتیجه‌ی تازه رو برمی‌گردونه."""
    await _require_member(db, chat_id, user.id)
    msg = await _own_message(db, chat_id, mid)
    if msg.deleted:
        raise HTTPException(400, "این پیام حذف شده")
    emoji = (body.emoji or "").strip()[:16]
    if not emoji:
        raise HTTPException(400, "ایموجی خالیه")
    row = (await db.execute(
        select(MessageReaction).where(
            MessageReaction.message_id == mid,
            MessageReaction.user_id == user.id,
            MessageReaction.emoji == emoji,
        )
    )).scalar_one_or_none()
    if row is not None:
        await db.delete(row)
    else:
        db.add(MessageReaction(message_id=mid, user_id=user.id, emoji=emoji))
    await db.commit()
    counts, mine = await _reactions_for(db, [mid], user.id)
    payload = {
        "type": "reaction", "chat_id": chat_id, "message_id": mid,
        "reactions": counts.get(mid, {}),
    }
    await notify_chat(chat_id, payload, skip=user.id)
    return {
        "ok": True, "reactions": counts.get(mid, {}),
        "my_reactions": mine.get(mid, []),
    }


class ForwardRequest(BaseModel):
    to_chat_id: str


@router.post("/{chat_id}/message/{mid}/forward", response_model=MessageOut)
async def forward_message(
    chat_id: str,
    mid: str,
    body: ForwardRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """پیام رو به چتِ دیگه‌ای می‌فرسته و نامِ فرستنده‌ی اصلی رو نگه می‌داره."""
    await _require_member(db, chat_id, user.id)
    src = await _own_message(db, chat_id, mid)
    if src.deleted:
        raise HTTPException(400, "این پیام حذف شده")
    dest_mem = await _require_member(db, body.to_chat_id, user.id)
    dest = await _require_chat(db, body.to_chat_id)
    if not await can_post(db, dest, dest_mem):
        raise HTTPException(403, "در این کانال فقط مدیرها می‌تونن پیام بذارن")

    orig_sender = (await db.execute(
        select(User).where(User.id == src.sender_id)
    )).scalar_one_or_none()
    origin = src.forward_from or (
        orig_sender.display_name if orig_sender else "ناشناس"
    )
    # فوروارد به «پیام‌های ذخیره‌شده» = یک نسخه‌ی مستقل. جای دیگر همان فایل
    # به اشتراک می‌ماند (رفتارِ همیشگیِ فوروارد)، ولی دفترچه‌ی یادداشت قرار
    # است حتی بعدِ پاک‌شدنِ اصل سرِ جایش بماند.
    if _is_saved(dest):
        content, file_url = _detach(src)
    else:
        content, file_url = src.content, src.file_url
    msg = Message(
        chat_id=body.to_chat_id,
        sender_id=user.id,
        content=content,
        msg_type=src.msg_type,
        file_url=file_url,
        forward_from=origin,
        enc=bool(src.enc),
        caption=src.caption,
        file_name=src.file_name,
    )
    db.add(msg)
    await db.commit()
    await db.refresh(msg)
    out = MessageOut(
        message_id=msg.id, sender_id=msg.sender_id, sender_name=user.display_name,
        content=msg.content, msg_type=msg.msg_type, file_url=msg.file_url,
        created_at=_iso(msg.created_at) or "", forward_from=origin,
        enc=bool(msg.enc),
        size=_media_size(msg.file_url, msg.content),
        caption=msg.caption,
        file_name=msg.file_name,
    )
    frame = {"type": "message", "chat_id": body.to_chat_id, **out.model_dump()}
    if _is_saved(dest):
        # تنها عضوِ این چت خودمم، پس `skip=user.id` یعنی هیچ‌کس. برای
        # دستگاه‌های *دیگرِ* همین کاربر می‌فرستیم تا آن‌ها هم پیام را ببینند؛
        # دستگاهِ فرستنده دوباره‌اش نمی‌کند (کلاینت با `message_id` غربال
        # می‌کند) و اعلانی هم نمی‌آید (فرستنده خودم است).
        await notify_users([user.id], frame)
    else:
        await notify_chat(body.to_chat_id, frame, skip=user.id)
    return out


# ── ذخیره‌ی یک پیام در «پیام‌های ذخیره‌شده» ────────────
@router.post("/{chat_id}/message/{mid}/save")
async def save_message(
    chat_id: str,
    mid: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """یک نسخه‌ی *مستقل* از این پیام در دفترچه‌ی یادداشتِ خودم.

    فرقش با فوروارد این است که مقصد را نمی‌پرسد (همیشه چتِ ذخیره‌شده‌ی خودِ
    کاربر است، و اگر نبود همان‌جا ساخته می‌شود) و فایلِ رسانه را همیشه
    فیزیکی کپی می‌کند: از این پس فرستنده‌ی اصلی پیام و فایلش را پاک کند،
    این یکی سرِ جایش می‌ماند.

    خروجی `{"chat_id", "message"}` است نه خودِ `MessageOut`: کلاینت شناسه‌ی
    چتِ مقصد را لازم دارد تا «رفتن به پیام‌های ذخیره‌شده» را بی یک درخواستِ
    اضافه پیشنهاد بدهد.
    """
    await _require_member(db, chat_id, user.id)
    src = await _own_message(db, chat_id, mid)
    if src.deleted:
        raise HTTPException(400, "این پیام حذف شده")
    dest = await saved_chat(db, user.id)

    who = (await db.execute(
        select(User).where(User.id == src.sender_id)
    )).scalar_one_or_none()
    # نامِ فرستنده‌ی اصلی می‌ماند — مگر پیامِ خودم باشد، که «فوروارد از خودم»
    # سرِ بی‌خود شلوغ می‌کند.
    origin = src.forward_from or (
        None if src.sender_id == user.id
        else (who.display_name if who else "ناشناس")
    )
    content, file_url = _detach(src)
    msg = Message(
        chat_id=dest.id,
        sender_id=user.id,
        content=content,
        msg_type=src.msg_type,
        file_url=file_url,
        forward_from=origin,
        enc=bool(src.enc),
        caption=src.caption,
        file_name=src.file_name,
    )
    db.add(msg)
    await db.commit()
    await db.refresh(msg)
    out = MessageOut(
        message_id=msg.id, sender_id=msg.sender_id,
        sender_name=user.display_name, content=msg.content,
        msg_type=msg.msg_type, file_url=msg.file_url,
        created_at=_iso(msg.created_at) or "", forward_from=origin,
        enc=bool(msg.enc), size=_media_size(msg.file_url, msg.content),
        caption=msg.caption,
        file_name=msg.file_name,
    )
    await notify_users([user.id], {
        "type": "message", "chat_id": dest.id, **out.model_dump()
    })
    return {"chat_id": dest.id, "message": out.model_dump()}


# ── پروفایل چت/گروه ──────────────────────────────
class ChatProfileOut(BaseModel):
    chat_id: str
    title: str
    is_group: bool
    bio: Optional[str] = None
    avatar_url: Optional[str] = None
    creator_id: Optional[str] = None
    members: list[UserOut] = []
    is_admin: bool = False
    kind: str = KIND_PRIVATE
    is_public: bool = False
    handle: Optional[str] = None
    my_role: str = ROLE_MEMBER
    can_post: bool = True
    members_count: int = 0
    members_hidden: bool = False
    peer_read_at: Optional[str] = None


@router.get("/{chat_id}/profile", response_model=ChatProfileOut)
async def chat_profile(
    chat_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    chat = await _require_chat(db, chat_id)
    member = await _require_member(db, chat_id, user.id)

    members_out = []
    all_members = (await db.execute(
        select(ChatMember).where(ChatMember.chat_id == chat_id)
    )).scalars().all()
    # مالک اول، بعد مدیرها، بعد اعضا
    all_members = sorted(all_members, key=lambda m: -_rank(m))
    # در کانال، فهرستِ مشترک‌ها خصوصی است: فقط مدیرها می‌بیننش و بقیه
    # تنها شمارِ کلِ مشترک‌ها رو می‌گیرن (مثلِ تلگرام).
    hide = ((chat.kind or "") == KIND_CHANNEL and not _is_manager(member))
    # آخرین خواندهٔ طرف‌های دیگر → تیکِ دوبل برای فرستنده
    reads = [m.last_read_at for m in all_members
             if m.user_id != user.id and m.last_read_at is not None]
    peer_read = max(reads) if reads else None
    for m in (all_members if not hide else []):
        u = (await db.execute(select(User).where(User.id == m.user_id))).scalar_one_or_none()
        if u:
            members_out.append(UserOut(
                user_id=u.id, username=u.username, display_name=u.display_name,
                avatar_url=u.avatar_url,
                last_seen_at=(_iso(u.last_seen_at) if u.show_last_seen else None),
                online=(is_online(u.id) if u.show_last_seen else False),
                role=(m.role or ROLE_MEMBER), banned=bool(m.banned),
            ))

    return ChatProfileOut(
        chat_id=chat.id, title=chat.title or "", is_group=chat.is_group,
        bio=chat.bio, avatar_url=chat.avatar_url, creator_id=chat.creator_id,
        members=members_out, is_admin=_is_manager(member),
        kind=(chat.kind or KIND_PRIVATE), is_public=bool(chat.is_public),
        handle=chat.handle, my_role=(member.role or ROLE_MEMBER),
        can_post=await can_post(db, chat, member),
        members_count=len(all_members), members_hidden=hide,
        peer_read_at=_iso(peer_read),
    )


class UpdateChatRequest(BaseModel):
    title: Optional[str] = None
    bio: Optional[str] = None
    avatar_url: Optional[str] = None
    is_public: Optional[bool] = None
    handle: Optional[str] = None


@router.put("/{chat_id}/profile")
async def update_chat_profile(
    chat_id: str,
    body: UpdateChatRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    chat = await _require_chat(db, chat_id)
    mem = await _require_member(db, chat_id, user.id)
    if not _is_manager(mem):
        raise HTTPException(403, "فقط مدیرها می‌تونن ویرایش کنن")
    if body.title is not None:
        chat.title = body.title
    if body.bio is not None:
        chat.bio = body.bio
    if body.avatar_url is not None:
        chat.avatar_url = body.avatar_url
    if body.handle is not None:
        h = _norm_handle(body.handle)
        if body.handle.strip() and h is None:
            raise HTTPException(400, "شناسه باید ۳ تا ۳۲ نویسه و فقط حرف/رقم/زیرخط باشه")
        if h and await _handle_taken(db, h, skip_chat=chat_id):
            raise HTTPException(409, "این شناسه قبلاً گرفته شده")
        chat.handle = h
    if body.is_public is not None:
        if body.is_public and not (chat.handle or _norm_handle(body.handle or "")):
            raise HTTPException(400, "برای عمومی‌کردن باید شناسه (@) داشته باشه")
        chat.is_public = bool(body.is_public)
    db.add(chat)
    await db.commit()
    await notify_chat(chat_id, {"type": "chat_updated", "chat_id": chat_id})
    return {"ok": True, "handle": chat.handle, "is_public": bool(chat.is_public)}


@router.post("/{chat_id}/add_member")
async def add_member(
    chat_id: str,
    username: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    chat = await _require_chat(db, chat_id)
    if not chat.is_group:
        raise HTTPException(404, "گروه پیدا نشد")
    mem = await _require_member(db, chat_id, user.id)
    if not _is_manager(mem):
        raise HTTPException(403, "فقط مدیرها می‌تونن عضو اضافه کنن")
    uname = (username or "").strip().lstrip("@").lower()
    target = (await db.execute(select(User).where(User.username == uname))).scalar_one_or_none()
    if not target:
        raise HTTPException(404, "کاربر پیدا نشد")
    exists = await _member_of(db, chat_id, target.id)
    if exists is not None:
        if exists.banned:
            raise HTTPException(403, "این کاربر از گروه اخراج شده — اول رفعِ اخراج کن")
        raise HTTPException(409, "عضو هست")
    db.add(ChatMember(chat_id=chat_id, user_id=target.id, role=ROLE_MEMBER))
    await db.commit()
    await notify_chat_created(chat_id, [target.id])
    await notify_chat(chat_id, {"type": "chat_updated", "chat_id": chat_id}, skip=user.id)
    return {"ok": True}


@router.delete("/{chat_id}/remove_member")
async def remove_member(
    chat_id: str,
    username: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    chat = await _require_chat(db, chat_id)
    if not chat.is_group:
        raise HTTPException(404, "گروه پیدا نشد")
    mem = await _require_member(db, chat_id, user.id)
    if not _is_manager(mem):
        raise HTTPException(403, "فقط مدیرها می‌تونن عضو حذف کنن")
    uname = (username or "").strip().lstrip("@").lower()
    target = (await db.execute(select(User).where(User.username == uname))).scalar_one_or_none()
    if not target:
        raise HTTPException(404, "کاربر پیدا نشد")
    victim = await _member_of(db, chat_id, target.id)
    if victim is None:
        raise HTTPException(404, "عضو نیست")
    if _rank(victim) >= _rank(mem):
        raise HTTPException(403, "به عضوی هم‌رده یا بالاتر از خودت دست نمی‌رسه")
    await db.delete(victim)
    await db.commit()
    await notify_users([target.id], {"type": "chat_removed", "chat_id": chat_id})
    await notify_chat(chat_id, {"type": "chat_updated", "chat_id": chat_id}, skip=user.id)
    return {"ok": True}


# ── مدیریتِ نقش‌ها و اخراج ─────────────────────────
class RoleRequest(BaseModel):
    username: str
    role: str = ROLE_ADMIN     # admin | member | owner


@router.post("/{chat_id}/role")
async def set_role(
    chat_id: str,
    body: RoleRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """ارتقا/تنزیلِ عضو. انتقالِ مالکیت فقط از خودِ مالک."""
    chat = await _require_chat(db, chat_id)
    if not chat.is_group:
        raise HTTPException(400, "این چت گروه/کانال نیست")
    me_mem = await _require_member(db, chat_id, user.id)
    role = (body.role or "").strip().lower()
    if role not in ROLE_RANK:
        raise HTTPException(400, "نقش نامعتبره")
    uname = (body.username or "").strip().lstrip("@").lower()
    target = (await db.execute(select(User).where(User.username == uname))).scalar_one_or_none()
    if not target:
        raise HTTPException(404, "کاربر پیدا نشد")
    if target.id == user.id:
        raise HTTPException(400, "نقشِ خودت رو نمی‌تونی عوض کنی")
    victim = await _member_of(db, chat_id, target.id)
    if victim is None:
        raise HTTPException(404, "عضو نیست")

    if role == ROLE_OWNER:
        if (me_mem.role or "") != ROLE_OWNER:
            raise HTTPException(403, "فقط مالک می‌تونه مالکیت رو واگذار کنه")
        victim.role = ROLE_OWNER
        me_mem.role = ROLE_ADMIN
        chat.creator_id = target.id
        db.add_all([victim, me_mem, chat])
    else:
        if not _is_manager(me_mem):
            raise HTTPException(403, "فقط مدیرها می‌تونن نقش بدن")
        if _rank(victim) >= _rank(me_mem):
            raise HTTPException(403, "به عضوی هم‌رده یا بالاتر از خودت دست نمی‌رسه")
        if ROLE_RANK[role] >= _rank(me_mem):
            raise HTTPException(403, "نقشی بالاتر یا هم‌رده‌ی خودت نمی‌تونی بدی")
        victim.role = role
        db.add(victim)
    await db.commit()
    await notify_chat(chat_id, {"type": "chat_updated", "chat_id": chat_id})
    return {"ok": True, "role": victim.role}


class BanRequest(BaseModel):
    username: str
    banned: bool = True


@router.post("/{chat_id}/ban")
async def set_banned(
    chat_id: str,
    body: BanRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """اخراجِ همیشگی (یا رفعِ آن). عضوِ اخراج‌شده نمی‌تونه دوباره عضو شه."""
    chat = await _require_chat(db, chat_id)
    if not chat.is_group:
        raise HTTPException(400, "این چت گروه/کانال نیست")
    me_mem = await _require_member(db, chat_id, user.id)
    if not _is_manager(me_mem):
        raise HTTPException(403, "فقط مدیرها می‌تونن اخراج کنن")
    uname = (body.username or "").strip().lstrip("@").lower()
    target = (await db.execute(select(User).where(User.username == uname))).scalar_one_or_none()
    if not target:
        raise HTTPException(404, "کاربر پیدا نشد")
    if target.id == user.id:
        raise HTTPException(400, "خودت رو نمی‌تونی اخراج کنی")
    victim = (await db.execute(
        select(ChatMember).where(
            ChatMember.chat_id == chat_id, ChatMember.user_id == target.id
        )
    )).scalar_one_or_none()
    if victim is None:
        if not body.banned:
            raise HTTPException(404, "عضو نیست")
        # اخراجِ پیشگیرانه: ردیفی با banned=True می‌سازیم
        victim = ChatMember(
            chat_id=chat_id, user_id=target.id, role=ROLE_MEMBER, banned=True
        )
    else:
        if _rank(victim) >= _rank(me_mem) and victim.role != ROLE_MEMBER:
            raise HTTPException(403, "به عضوی هم‌رده یا بالاتر از خودت دست نمی‌رسه")
        if (victim.role or "") == ROLE_OWNER:
            raise HTTPException(403, "مالک رو نمی‌شه اخراج کرد")
        victim.banned = bool(body.banned)
        if victim.banned:
            victim.role = ROLE_MEMBER
    db.add(victim)
    await db.commit()
    if victim.banned:
        await notify_users([target.id], {"type": "chat_removed", "chat_id": chat_id})
    else:
        await notify_users([target.id], {"type": "chat_created", "chat_id": chat_id})
    await notify_chat(chat_id, {"type": "chat_updated", "chat_id": chat_id}, skip=user.id)
    return {"ok": True, "banned": bool(victim.banned)}


@router.post("/report")
async def report_content(
    body: ReportRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """ثبت گزارش تخلف پیام، کاربر، استوری یا محتوا (Telegram Report).

خواسته‌ی کاربر: علاوه بر متنِ گزارش، خودِ پیام/مدیا/استوری هم برای مدیر
فرستاده شود تا «قضاوت کند». پس این‌جا `offender` (نامِ متخلف) و
`media_url`/`media_kind` هم استخراج می‌شود.
    """
    if not body.target_id:
        raise HTTPException(400, "شناسه‌ی مورد نظر مشخص نشده است")
    target_uname = body.target_id
    offender = ""
    media_url, media_kind = "", ""
    snippet = body.details or ""
    try:
        if body.target_type == "message":
            msg = (await db.execute(select(Message).where(Message.id == body.target_id))).scalar_one_or_none()
            if msg:
                kind = (msg.msg_type or "text").lower()
                content = (msg.content or "").strip()
                # مدیا = content یک مسیرِ فایل است، نه متن. راهِ تشخیص دوگانه:
                # نوعِ پیام، یا پسوندِ فایل (وقتی kind متن است ولی content
                # فایل است — همان که در گزارشِ واقعی دیدیم).
                low = content.lower().split("?")[0].rstrip("/")
                looks_file = bool(re.search(
                    r"\.(jpg|jpeg|png|webp|gif|bmp|mp4|mov|m4v|mkv|webm|3gp|"
                    r"mp3|m4a|ogg|wav|opus|pdf|zip|rar|apk)$", low))
                if kind in ("image", "video", "gif", "voice", "audio",
                            "file", "sticker") or (content and looks_file):
                    media_url = content
                    if kind in ("image", "video"):
                        media_kind = kind
                    elif low.endswith((".mp4", ".mov", ".m4v", ".mkv",
                                        ".webm", ".3gp")):
                        media_kind = "video"
                    else:
                        media_kind = "image"
                    cap = (msg.caption or "")[:140]
                    msg_txt = f"[{media_kind}]{' — ' + cap if cap else ''}"
                else:
                    msg_txt = (msg.content or "")[:140]
                snippet = f"متن پیام: {msg_txt} | توضیحات: {snippet}" if snippet else f"متن پیام: {msg_txt}"
                u_sender = (await db.execute(select(User).where(User.id == msg.sender_id))).scalar_one_or_none()
                if u_sender:
                    offender = u_sender.username or ""
                    target_uname = u_sender.username or u_sender.display_name or u_sender.id
        elif body.target_type == "chat":
            chat_obj = (await db.execute(select(Chat).where(Chat.id == body.target_id))).scalar_one_or_none()
            if chat_obj:
                if chat_obj.handle:
                    target_uname = chat_obj.handle
                elif not chat_obj.is_group:
                    om = (await db.execute(select(ChatMember).where(ChatMember.chat_id == chat_obj.id, ChatMember.user_id != user.id))).scalar_one_or_none()
                    if om:
                        u_other = (await db.execute(select(User).where(User.id == om.user_id))).scalar_one_or_none()
                        if u_other:
                            offender = u_other.username or ""
                            target_uname = u_other.username or u_other.display_name or u_other.id
                else:
                    target_uname = chat_obj.title or chat_obj.id
        elif body.target_type == "story":
            from .models import Story
            st_obj = (await db.execute(select(Story).where(Story.id == body.target_id))).scalar_one_or_none()
            if st_obj:
                media_url, media_kind = (st_obj.media_url or ""), (st_obj.media_type or "image")
                cap = (st_obj.caption or "")[:140]
                snippet = (f"استوری: [{media_kind}]{' — ' + cap if cap else ''}"
                           + (f" | توضیحات: {snippet}" if snippet else ""))
                u_author = (await db.execute(select(User).where(User.id == st_obj.author_id))).scalar_one_or_none()
                if u_author:
                    offender = u_author.username or ""
                    target_uname = u_author.username or u_author.display_name or u_author.id
        elif body.target_type == "user":
            offender = (body.target_id or "").lstrip("@")
    except Exception:
        pass

    try:
        from . import telegram_bot
        await telegram_bot.send_report_to_owner(
            reporter_username=user.username or user.display_name or user.id,
            target_type=body.target_type,
            target_id=target_uname,
            reason=body.reason,
            details=snippet,
            media_url=media_url, media_kind=media_kind, offender=offender,
        )
    except Exception:
        pass
    return {"ok": True, "message": "Report submitted successfully"}
