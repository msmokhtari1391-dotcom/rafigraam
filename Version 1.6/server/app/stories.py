"""
استوریِ ۲۴ساعته — کاربر، گروه، و کانال.

هر استوری سرِ ساخت `expires_at` می‌گیرد (۲۴ ساعت بعد)، پس «فعال بودن» یک
مقایسه‌ی ساده است و به ساعتِ خواندن وابسته نیست. یک وظیفه‌ی پس‌زمینه
ردیف‌های منقضی و فایلِ بی‌صاحبشان را جمع می‌کند.

دسترسی:
  • استوریِ کاربر  → فقط خودش می‌گذارد؛ کسانی می‌بینند که با او گفت‌وگوی
    خصوصی دارند و بلاک هم نیستند.
  • استوریِ گروه   → هر عضوِ اخراج‌نشده می‌گذارد؛ همه‌ی اعضا می‌بینند.
  • استوریِ کانال  → فقط صاحب و مدیرها می‌گذارند؛ همه‌ی اعضا می‌بینند.
"""
import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete as sa_delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import get_current_user
from .chat import _is_manager, _iso, _member_of, _require_chat, notify_users
from .config import settings
from .db import AsyncSessionLocal, get_session
from .models import (
    KIND_CHANNEL, OWNER_CHAT, OWNER_USER, STORY_TTL_HOURS,
    Block, Chat, ChatMember, Message, SavedGif, Story, StoryView, User,
    UserPhoto,
)
from .privacy import blocked_between

log = logging.getLogger("rafigram.stories")
router = APIRouter(prefix="/stories", tags=["stories"])

# سقفِ استوریِ فعالِ هر صاحب (کاربر یا گفت‌وگو) — جلوِ سیل‌کردن را می‌گیرد
MAX_ACTIVE_PER_OWNER = 30
# فاصله‌ی جاروی ردیف‌های منقضی
REAP_EVERY_SECONDS = 15 * 60


def _now() -> datetime:
    return datetime.now(timezone.utc)


class StoryCreate(BaseModel):
    media_url: str
    media_type: str = "image"
    caption: Optional[str] = None
    owner_kind: str = OWNER_USER
    owner_id: Optional[str] = None      # خالی = خودِ کاربر


class StoryOut(BaseModel):
    id: str
    owner_kind: str
    owner_id: str
    author_id: str
    author_name: str = ""
    author_username: str = ""
    media_url: str
    media_type: str = "image"
    caption: Optional[str] = None
    created_at: Optional[str] = None
    expires_at: Optional[str] = None
    seen: bool = False
    views: int = 0
    mine: bool = False


class RingOut(BaseModel):
    """یک حلقه‌ی استوری در نوارِ بالای صفحه‌ی چت."""
    owner_kind: str
    owner_id: str
    title: str = ""
    avatar_url: Optional[str] = None
    username: Optional[str] = None
    count: int = 0
    has_unseen: bool = False
    last_at: Optional[str] = None
    is_self: bool = False
    preview: Optional[str] = None


# ── ابزارها ────────────────────────────────────────
def _norm_media(raw: Optional[str]) -> str:
    """فقط آدرسِ مدیای همین سرور یا یک URL کاملِ ساده پذیرفته می‌شود."""
    u = (raw or "").strip()
    if not u:
        raise HTTPException(400, "آدرسِ رسانه لازمه")
    if len(u) > 500:
        raise HTTPException(400, "آدرسِ رسانه خیلی بلنده")
    if not (u.startswith("/media/") or u.startswith("http://")
            or u.startswith("https://")):
        raise HTTPException(400, "آدرسِ رسانه معتبر نیست")
    return u


def _norm_owner(user: User, owner_kind, owner_id) -> tuple[str, str]:
    kind = (owner_kind or OWNER_USER).strip().lower()
    if kind not in (OWNER_USER, OWNER_CHAT):
        raise HTTPException(400, "نوعِ صاحبِ استوری معتبر نیست")
    oid = (owner_id or "").strip() or (user.id if kind == OWNER_USER else "")
    if not oid:
        raise HTTPException(400, "شناسه‌ی گفت‌وگو لازمه")
    return kind, oid


async def _can_post(db: AsyncSession, user: User, kind: str, oid: str) -> bool:
    """کاربر برای خودش؛ در گروه هر عضو؛ در کانال فقط صاحب و مدیرها."""
    if kind == OWNER_USER:
        return oid == user.id
    chat = await _require_chat(db, oid)
    mem = await _member_of(db, oid, user.id)
    if not mem or mem.banned:
        return False
    if (chat.kind or "") == KIND_CHANNEL:
        return _is_manager(mem)
    return True


async def _shares_private(db: AsyncSession, a_id: str, b_id: str) -> bool:
    """آیا این دو نفر گفت‌وگوی خصوصیِ مشترکی دارند؟ (یعنی «مخاطب»اند)"""
    mine = select(ChatMember.chat_id).where(ChatMember.user_id == a_id)
    row = (await db.execute(
        select(ChatMember.chat_id)
        .join(Chat, Chat.id == ChatMember.chat_id)
        .where(ChatMember.user_id == b_id, ChatMember.chat_id.in_(mine),
               Chat.is_group == False)          # noqa: E712
        .limit(1))).first()
    return row is not None


async def _can_view(db: AsyncSession, user: User, kind: str, oid: str) -> bool:
    if kind == OWNER_USER:
        if oid == user.id:
            return True
        if await blocked_between(db, user.id, oid):
            return False
        return await _shares_private(db, user.id, oid)
    mem = await _member_of(db, oid, user.id)
    return bool(mem and not mem.banned)


async def _require_view(db: AsyncSession, user: User, kind: str, oid: str):
    if not await _can_view(db, user, kind, oid):
        raise HTTPException(403, "به استوریِ این حساب دسترسی نداری")


async def _visible_owners(db: AsyncSession, user: User) -> tuple[set, set]:
    """(کاربرهایی که استوری‌شان را می‌بینم، گفت‌وگوهایی که عضوشانم)

    یک‌جا حساب می‌شود تا خوراکِ حلقه‌ها به‌جای پرسشِ به‌ازای هر صاحب، چند
    پرسشِ ثابت بزند.
    """
    mems = (await db.execute(
        select(ChatMember).where(ChatMember.user_id == user.id)
    )).scalars().all()
    chat_ids = {m.chat_id for m in mems if not m.banned}
    peers: set = {user.id}
    if chat_ids:
        priv = {r[0] for r in (await db.execute(
            select(Chat.id).where(Chat.id.in_(chat_ids),
                                  Chat.is_group == False)   # noqa: E712
        )).all()}
        if priv:
            peers |= {r[0] for r in (await db.execute(
                select(ChatMember.user_id)
                .where(ChatMember.chat_id.in_(priv),
                       ChatMember.user_id != user.id)
            )).all()}
    # بلاک (هر دو سو) حلقه را برمی‌دارد
    rows = (await db.execute(
        select(Block).where((Block.user_id == user.id)
                            | (Block.blocked_id == user.id))
    )).scalars().all()
    for b in rows:
        peers.discard(b.blocked_id if b.user_id == user.id else b.user_id)
    peers.add(user.id)
    return peers, chat_ids


async def _seen_ids(db: AsyncSession, user_id: str, story_ids) -> set:
    ids = [s for s in story_ids if s]
    if not ids:
        return set()
    rows = (await db.execute(
        select(StoryView.story_id).where(StoryView.user_id == user_id,
                                         StoryView.story_id.in_(ids))
    )).all()
    return {r[0] for r in rows}


async def _view_counts(db: AsyncSession, story_ids) -> dict:
    ids = [s for s in story_ids if s]
    if not ids:
        return {}
    rows = (await db.execute(
        select(StoryView.story_id).where(StoryView.story_id.in_(ids))
    )).all()
    out: dict = {}
    for (sid,) in rows:
        out[sid] = out.get(sid, 0) + 1
    return out


async def _active(db: AsyncSession, kind: str, oid: str) -> list:
    return list((await db.execute(
        select(Story).where(Story.owner_kind == kind, Story.owner_id == oid,
                            Story.expires_at > _now())
        .order_by(Story.created_at.asc())
    )).scalars().all())


async def _authors(db: AsyncSession, ids) -> dict:
    ids = list({i for i in ids if i})
    if not ids:
        return {}
    rows = (await db.execute(select(User).where(User.id.in_(ids)))).scalars().all()
    return {u.id: u for u in rows}


# ── گذاشتنِ استوری ─────────────────────────────────
@router.post("", response_model=StoryOut)
async def create_story(
    body: StoryCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    kind, oid = _norm_owner(user, body.owner_kind, body.owner_id)
    if not await _can_post(db, user, kind, oid):
        raise HTTPException(403, "اجازه‌ی گذاشتنِ استوری اینجا را نداری")
    url = _norm_media(body.media_url)
    mtype = (body.media_type or "image").strip().lower()
    if mtype not in ("image", "video"):
        mtype = "image"
    cap = (body.caption or "").strip() or None
    if cap and len(cap) > 1024:
        cap = cap[:1024]

    mine = await _active(db, kind, oid)
    if len(mine) >= MAX_ACTIVE_PER_OWNER:
        raise HTTPException(
            429, f"سقفِ {MAX_ACTIVE_PER_OWNER} استوریِ فعال پر شده")

    now = _now()
    st = Story(owner_kind=kind, owner_id=oid, author_id=user.id, media_url=url,
               media_type=mtype, caption=cap, created_at=now,
               expires_at=now + timedelta(hours=STORY_TTL_HOURS))
    db.add(st)
    await db.commit()
    await _notify_new(db, user, kind, oid, st)
    return StoryOut(
        id=st.id, owner_kind=kind, owner_id=oid, author_id=user.id,
        author_name=user.display_name or user.username,
        author_username=user.username, media_url=url, media_type=mtype,
        caption=cap, created_at=_iso(st.created_at),
        expires_at=_iso(st.expires_at), seen=True, views=0, mine=True)


async def _notify_new(db: AsyncSession, user: User, kind: str, oid: str,
                      st: Story) -> None:
    """به بیننده‌های ممکن خبر می‌دهد تا حلقه‌ها بی‌رفرشِ دستی روشن شوند."""
    try:
        if kind == OWNER_CHAT:
            rows = (await db.execute(
                select(ChatMember).where(ChatMember.chat_id == oid)
            )).scalars().all()
            targets = [m.user_id for m in rows
                       if not m.banned and m.user_id != user.id]
        else:
            priv = select(Chat.id).where(
                Chat.id.in_(select(ChatMember.chat_id)
                            .where(ChatMember.user_id == user.id)),
                Chat.is_group == False)             # noqa: E712
            rows = (await db.execute(
                select(ChatMember.user_id)
                .where(ChatMember.chat_id.in_(priv),
                       ChatMember.user_id != user.id)
            )).all()
            targets = list({r[0] for r in rows})
            blocked = (await db.execute(
                select(Block).where((Block.user_id == user.id)
                                    | (Block.blocked_id == user.id))
            )).scalars().all()
            drop = {(b.blocked_id if b.user_id == user.id else b.user_id)
                    for b in blocked}
            targets = [t for t in targets if t not in drop]
        if targets:
            await notify_users(targets, {
                "type": "story", "story_id": st.id, "owner_kind": kind,
                "owner_id": oid, "author_id": user.id})
    except Exception as exc:      # خبررسانی هرگز نباید گذاشتنِ استوری را بشکند
        log.warning("story notify failed: %s", exc)


# ── خوراکِ حلقه‌ها (نوارِ بالای صفحه‌ی چت) ───────────
@router.get("/feed", response_model=list[RingOut])
async def feed(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """یک حلقه به‌ازای هر صاحبِ استوری. حلقه‌ی خودم همیشه اول است."""
    rows = list((await db.execute(
        select(Story).where(Story.expires_at > _now())
        .order_by(Story.created_at.asc())
    )).scalars().all())
    if not rows:
        return []
    peers, chat_ids = await _visible_owners(db, user)
    mine_ids = []
    groups: dict = {}
    for s in rows:
        if s.owner_kind == OWNER_USER:
            if s.owner_id not in peers:
                continue
        elif s.owner_id not in chat_ids:
            continue
        groups.setdefault((s.owner_kind, s.owner_id), []).append(s)
        mine_ids.append(s.id)
    if not groups:
        return []

    seen = await _seen_ids(db, user.id, mine_ids)
    users = await _authors(db, [k[1] for k in groups if k[0] == OWNER_USER])
    chat_keys = [k[1] for k in groups if k[0] == OWNER_CHAT]
    chats = {}
    if chat_keys:
        chats = {c.id: c for c in (await db.execute(
            select(Chat).where(Chat.id.in_(chat_keys)))).scalars().all()}

    out: list[RingOut] = []
    for (kind, oid), items in groups.items():
        last = items[-1]
        if kind == OWNER_USER:
            u = users.get(oid)
            title = (u.display_name or u.username) if u else ""
            avatar, uname = (u.avatar_url if u else None), (u.username if u else None)
        else:
            c = chats.get(oid)
            title = (c.title or "") if c else ""
            avatar, uname = (c.avatar_url if c else None), (c.handle if c else None)
        out.append(RingOut(
            owner_kind=kind, owner_id=oid, title=title, avatar_url=avatar,
            username=uname, count=len(items),
            has_unseen=any(s.id not in seen for s in items),
            last_at=_iso(last.created_at),
            is_self=(kind == OWNER_USER and oid == user.id),
            preview=last.media_url))
    # تازه‌ترین بالاتر؛ بعد «دیده‌نشده»ها؛ و حلقه‌ی خودم همیشه اول.
    # (مرتب‌سازیِ پایدارِ پایتون اجازه می‌دهد این سه را پشتِ‌هم بزنیم.)
    out.sort(key=lambda r: (r.last_at or ""), reverse=True)
    out.sort(key=lambda r: (not r.is_self, not r.has_unseen))
    return out


# ── استوری‌های یک صاحب (نمایشگر) ───────────────────
@router.get("/of/{owner_kind}/{owner_id}", response_model=list[StoryOut])
async def stories_of(
    owner_kind: str,
    owner_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    kind, oid = _norm_owner(user, owner_kind, owner_id)
    await _require_view(db, user, kind, oid)
    items = await _active(db, kind, oid)
    if not items:
        return []
    ids = [s.id for s in items]
    seen = await _seen_ids(db, user.id, ids)
    counts = await _view_counts(db, ids)
    authors = await _authors(db, [s.author_id for s in items])
    out = []
    for s in items:
        a = authors.get(s.author_id)
        out.append(StoryOut(
            id=s.id, owner_kind=s.owner_kind, owner_id=s.owner_id,
            author_id=s.author_id,
            author_name=((a.display_name or a.username) if a else ""),
            author_username=(a.username if a else ""),
            media_url=s.media_url, media_type=s.media_type or "image",
            caption=s.caption, created_at=_iso(s.created_at),
            expires_at=_iso(s.expires_at), seen=(s.id in seen),
            views=counts.get(s.id, 0), mine=(s.author_id == user.id)))
    return out


@router.get("/mine", response_model=list[StoryOut])
async def my_stories(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """استوری‌های فعالی که خودم گذاشته‌ام (هرجا: پروفایل، گروه، کانال)."""
    items = list((await db.execute(
        select(Story).where(Story.author_id == user.id,
                            Story.expires_at > _now())
        .order_by(Story.created_at.desc())
    )).scalars().all())
    counts = await _view_counts(db, [s.id for s in items])
    return [StoryOut(
        id=s.id, owner_kind=s.owner_kind, owner_id=s.owner_id,
        author_id=s.author_id,
        author_name=(user.display_name or user.username),
        author_username=user.username, media_url=s.media_url,
        media_type=s.media_type or "image", caption=s.caption,
        created_at=_iso(s.created_at), expires_at=_iso(s.expires_at),
        seen=True, views=counts.get(s.id, 0), mine=True) for s in items]


async def _get_story(db: AsyncSession, story_id: str) -> Story:
    st = (await db.execute(
        select(Story).where(Story.id == story_id)
    )).scalar_one_or_none()
    if st is None:
        raise HTTPException(404, "استوری پیدا نشد")
    return st


@router.post("/{story_id}/seen")
async def mark_seen(
    story_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    st = await _get_story(db, story_id)
    await _require_view(db, user, st.owner_kind, st.owner_id)
    row = (await db.execute(
        select(StoryView).where(StoryView.story_id == st.id,
                                StoryView.user_id == user.id)
    )).scalar_one_or_none()
    if row is None:
        db.add(StoryView(story_id=st.id, user_id=user.id))
        await db.commit()
    counts = await _view_counts(db, [st.id])
    return {"ok": True, "views": counts.get(st.id, 0)}


class ViewerOut(BaseModel):
    id: str
    username: str = ""
    display_name: str = ""
    avatar_url: Optional[str] = None
    at: Optional[str] = None


@router.get("/{story_id}/viewers", response_model=list[ViewerOut])
async def viewers(
    story_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """فهرستِ بازدیدکننده‌ها — فقط برای گذارنده (و مدیرِ گروه/کانال)."""
    st = await _get_story(db, story_id)
    ok = st.author_id == user.id
    if not ok and st.owner_kind == OWNER_CHAT:
        ok = _is_manager(await _member_of(db, st.owner_id, user.id))
    if not ok:
        raise HTTPException(403, "فهرستِ بازدیدکننده‌ها فقط برای گذارنده است")
    rows = list((await db.execute(
        select(StoryView).where(StoryView.story_id == st.id)
        .order_by(StoryView.created_at.desc())
    )).scalars().all())
    users = await _authors(db, [r.user_id for r in rows])
    out = []
    for r in rows:
        u = users.get(r.user_id)
        out.append(ViewerOut(
            id=r.user_id, username=(u.username if u else ""),
            display_name=((u.display_name or u.username) if u else ""),
            avatar_url=(u.avatar_url if u else None), at=_iso(r.created_at)))
    return out


@router.delete("/{story_id}")
async def delete_story(
    story_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """گذارنده همیشه؛ در گروه/کانال مدیرها هم می‌توانند پاک کنند."""
    st = await _get_story(db, story_id)
    ok = st.author_id == user.id
    if not ok and st.owner_kind == OWNER_CHAT:
        ok = _is_manager(await _member_of(db, st.owner_id, user.id))
    if not ok:
        raise HTTPException(403, "اجازه‌ی پاک‌کردنِ این استوری را نداری")
    url = st.media_url
    await db.execute(sa_delete(StoryView).where(StoryView.story_id == st.id))
    await db.delete(st)
    await db.commit()
    await _drop_media(db, url)
    return {"ok": True}


# ── جاروی استوری‌های منقضی ─────────────────────────
async def _drop_media(db: AsyncSession, url: Optional[str]) -> None:
    """فایل را فقط وقتی پاک می‌کند که هیچ‌جای دیگری به آن اشاره نکند."""
    u = url or ""
    if "/media/" not in u:
        return
    name = os.path.basename(u.split("?", 1)[0].split("#", 1)[0])
    if not name:
        return
    like = f"%{name}%"
    refs = (
        select(Story.id).where(Story.media_url.like(like)),
        select(Message.id).where(Message.file_url.like(like)),
        select(Message.id).where(Message.content.like(like)),
        select(SavedGif.id).where(SavedGif.url.like(like)),
        select(UserPhoto.id).where(UserPhoto.url.like(like)),
        select(User.id).where(User.avatar_url.like(like)),
        select(Chat.id).where(Chat.avatar_url.like(like)),
    )
    for stmt in refs:
        if (await db.execute(stmt.limit(1))).first():
            return
    try:
        (settings.media_dir / name).unlink()
    except OSError:
        pass


async def reap_once() -> int:
    """ردیف‌های منقضی و فایلِ بی‌صاحبشان را پاک می‌کند؛ شمارِ پاک‌شده‌ها."""
    async with AsyncSessionLocal() as db:
        rows = list((await db.execute(
            select(Story).where(Story.expires_at <= _now())
        )).scalars().all())
        if not rows:
            return 0
        urls = [s.media_url for s in rows]
        for s in rows:
            await db.execute(
                sa_delete(StoryView).where(StoryView.story_id == s.id))
            await db.delete(s)
        await db.commit()
        # حالا که ردیف‌ها رفته‌اند، «بی‌صاحب بودنِ» فایل درست سنجیده می‌شود
        for u in urls:
            await _drop_media(db, u)
    return len(rows)


_task: Optional[asyncio.Task] = None


async def _reaper() -> None:
    while True:
        try:
            n = await reap_once()
            if n:
                log.info("stories reaped: %s", n)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("story reaper failed: %s", exc)
        await asyncio.sleep(REAP_EVERY_SECONDS)


async def start_reaper() -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_reaper())


async def stop_reaper() -> None:
    global _task
    if _task is not None:
        _task.cancel()
        try:
            await _task
        except (asyncio.CancelledError, Exception):
            pass
        _task = None





