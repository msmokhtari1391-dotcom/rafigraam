"""
اندپوینت‌های پروفایل — ویرایش اطلاعات، آپلود عکس، دریافت پروفایل.
"""
import os
import uuid
from datetime import timezone
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import get_current_user
from .config import settings
from .db import get_session
from .models import ChatMember, User, UserPhoto, UserPost

router = APIRouter(prefix="/profile", tags=["profile"])

MAX_PHOTOS = 5


class UpdateProfileRequest(BaseModel):
    display_name: Optional[str] = None
    bio: Optional[str] = None
    avatar_url: Optional[str] = None
    birthday: Optional[str] = None


class ProfileOut(BaseModel):
    user_id: str
    username: str
    display_name: str
    bio: Optional[str] = None
    avatar_url: Optional[str] = None
    birthday: Optional[str] = None
    last_seen_at: Optional[str] = None
    online: bool = False
    photos: list[str] = []


def _iso(dt) -> Optional[str]:
    """تاریخ دیتابیس (UTC بدون منطقه‌ی زمانی) → ISO با tz."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _online(user_id: str) -> bool:
    """حضورِ زنده از رجیستریِ وب‌سوکت (importِ تنبل، برای پرهیز از حلقه)."""
    try:
        from .ws import manager
        return manager.is_online(user_id)
    except Exception:
        return False


# ── عکس‌های پروفایل ───────────────────────────────
async def _photos_of(db: AsyncSession, user_id: str) -> list[UserPhoto]:
    return list((await db.execute(
        select(UserPhoto).where(UserPhoto.user_id == user_id)
        .order_by(UserPhoto.created_at)
    )).scalars().all())


async def _photo_urls(db: AsyncSession, u: User) -> list[str]:
    """آدرسِ همه‌ی عکس‌ها؛ عکسِ فعلی همیشه اولِ لیسته."""
    urls = [p.url for p in await _photos_of(db, u.id)]
    cur = u.avatar_url
    if cur:
        urls = [cur] + [x for x in urls if x != cur]
    return urls


# ── پخشِ زنده‌ی تغییرِ پروفایل ──────────────────────
async def broadcast_profile(db: AsyncSession, u: User) -> None:
    """به همه‌ی کسانی که با این کاربر چتِ مشترک دارن خبر می‌ده."""
    try:
        from .ws import manager
    except Exception:
        return
    try:
        my_chats = list((await db.execute(
            select(ChatMember.chat_id).where(ChatMember.user_id == u.id)
        )).scalars().all())
        peers: set[str] = {u.id}
        if my_chats:
            peers |= set((await db.execute(
                select(ChatMember.user_id).where(ChatMember.chat_id.in_(my_chats))
            )).scalars().all())
        payload = {
            "type": "profile_updated",
            "user_id": u.id,
            "username": u.username,
            "display_name": u.display_name,
            "bio": u.bio or "",
            "avatar_url": u.avatar_url or "",
            "photos": await _photo_urls(db, u),
        }
        for uid in peers:
            await manager.send_to_user(uid, payload)
    except Exception:
        pass  # خرابیِ پخشِ زنده نباید خودِ ویرایش رو بشکنه


@router.get("/me")
async def get_my_profile(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    return ProfileOut(
        user_id=user.id, username=user.username,
        display_name=user.display_name, bio=user.bio,
        avatar_url=user.avatar_url,
        birthday=user.birthday,
        last_seen_at=_iso(user.last_seen_at),
        photos=await _photo_urls(db, user),
    )


@router.get("/me/photos")
async def my_photos(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    rows = await _photos_of(db, user.id)
    return {
        "photos": [{"id": p.id, "url": p.url} for p in rows],
        "urls": await _photo_urls(db, user),
        "max": MAX_PHOTOS,
    }


@router.post("/me/photos")
async def add_photo(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """عکسِ تازه اضافه می‌کنه (حداکثر ۵) و همون رو عکسِ اصلی می‌کنه."""
    rows = await _photos_of(db, user.id)
    if len(rows) >= MAX_PHOTOS:
        raise HTTPException(400, f"حداکثر {MAX_PHOTOS} عکس می‌تونی داشته باشی")
    ext = os.path.splitext(file.filename or "photo.jpg")[1] or ".jpg"
    ext = "".join(c for c in ext if c.isalnum() or c == ".")[:12].lower() or ".jpg"
    filename = f"{uuid.uuid4().hex}{ext}"
    content = await file.read()
    with open(settings.media_dir / filename, "wb") as f:
        f.write(content)
    url = f"/media/{filename}"
    db.add(UserPhoto(user_id=user.id, url=url))
    user.avatar_url = url
    db.add(user)
    await db.commit()
    await db.refresh(user)
    await broadcast_profile(db, user)
    return {"url": url, "photos": await _photo_urls(db, user)}


@router.delete("/me/photos")
async def delete_photo(
    url: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """عکس رو از گالری برمی‌داره؛ اگه عکسِ اصلی بود، بعدی جاش می‌شینه."""
    rows = await _photos_of(db, user.id)
    target = next((p for p in rows if p.url == url), None)
    if target is None:
        # عکسِ قدیمیِ بدون ردیفِ گالری (فقط avatar_url)
        if user.avatar_url != url:
            raise HTTPException(404, "عکس پیدا نشد")
    else:
        await db.delete(target)
    left = [p.url for p in rows if p.url != url]
    if user.avatar_url == url:
        user.avatar_url = left[-1] if left else None
        db.add(user)
    await db.commit()
    await db.refresh(user)
    # فایلِ فیزیکی هم پاک شه (اگه مالِ خودِ ما باشه)
    try:
        name = os.path.basename(url or "")
        if url.startswith("/media/") and name:
            p = settings.media_dir / name
            if p.exists():
                p.unlink()
    except Exception:
        pass
    await broadcast_profile(db, user)
    return {"ok": True, "photos": await _photo_urls(db, user)}


class SetMainPhotoRequest(BaseModel):
    url: str


@router.put("/me/photos/main")
async def set_main_photo(
    body: SetMainPhotoRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """یکی از عکس‌های گالری رو عکسِ اصلی می‌کنه."""
    rows = await _photos_of(db, user.id)
    if not any(p.url == body.url for p in rows):
        raise HTTPException(404, "عکس پیدا نشد")
    user.avatar_url = body.url
    db.add(user)
    await db.commit()
    await db.refresh(user)
    await broadcast_profile(db, user)
    return {"avatar_url": user.avatar_url, "photos": await _photo_urls(db, user)}


@router.get("/{username}")
async def get_user_profile(
    username: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    uname = (username or "").strip().lstrip("@").lower()
    u = (await db.execute(select(User).where(User.username == uname))).scalar_one_or_none()
    if not u:
        raise HTTPException(404, "کاربر پیدا نشد")
    return ProfileOut(
        user_id=u.id, username=u.username,
        display_name=u.display_name, bio=u.bio,
        avatar_url=u.avatar_url,
        birthday=u.birthday,
        last_seen_at=(_iso(u.last_seen_at) if u.show_last_seen else None),
        online=(_online(u.id) if u.show_last_seen else False),
        photos=await _photo_urls(db, u),
    )


@router.put("/me")
async def update_my_profile(
    body: UpdateProfileRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    if body.display_name is not None:
        user.display_name = body.display_name
    if body.bio is not None:
        user.bio = body.bio
    if body.avatar_url is not None:
        user.avatar_url = body.avatar_url
    if body.birthday is not None:
        user.birthday = body.birthday
    db.add(user)
    await db.commit()
    await db.refresh(user)
    await broadcast_profile(db, user)
    return {"ok": True}


@router.post("/me/avatar")
async def upload_avatar(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    ext = os.path.splitext(file.filename or "avatar.jpg")[1] or ".jpg"
    ext = "".join(c for c in ext if c.isalnum() or c == ".")[:12].lower() or ".jpg"
    filename = f"{uuid.uuid4().hex}{ext}"
    filepath = settings.media_dir / filename
    content = await file.read()
    with open(filepath, "wb") as f:
        f.write(content)
    user.avatar_url = f"/media/{filename}"
    db.add(user)
    # آواتارِ تازه در گالری هم ثبت می‌شه (تا سقفِ مجاز)
    rows = await _photos_of(db, user.id)
    if len(rows) < MAX_PHOTOS:
        db.add(UserPhoto(user_id=user.id, url=user.avatar_url))
    await db.commit()
    await db.refresh(user)
    await broadcast_profile(db, user)
    return {"avatar_url": user.avatar_url, "photos": await _photo_urls(db, user)}


# ── تغییر گذرواژه ─────────────────────────────────
class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


@router.put("/me/password")
async def change_password(
    body: ChangePasswordRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    from .auth import check_password, hash_password

    if not check_password(body.old_password, user.password_hash):
        raise HTTPException(400, "گذرواژه‌ی فعلی درست نیست")
    if len(body.new_password) < 4:
        raise HTTPException(400, "گذرواژه‌ی جدید خیلی کوتاهه")
    user.password_hash = hash_password(body.new_password)
    db.add(user)
    await db.commit()
    return {"ok": True}


# ── تغییر نام کاربری ──────────────────────────────
class ChangeUsernameRequest(BaseModel):
    username: str


@router.put("/me/username")
async def change_username(
    body: ChangeUsernameRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    new = (body.username or "").strip().lstrip("@").lower()
    if len(new) < 3:
        raise HTTPException(400, "نام کاربری حداقل ۳ حرف")
    if not all(c.isalnum() or c == "_" for c in new):
        raise HTTPException(400, "فقط حرف و رقم و _ مجازه")
    if new != user.username:
        taken = (await db.execute(
            select(User).where(User.username == new)
        )).scalar_one_or_none()
        if taken:
            raise HTTPException(409, "این نام کاربری گرفته شده")
        user.username = new
        db.add(user)
        await db.commit()
        await db.refresh(user)
        await broadcast_profile(db, user)
    return {"username": user.username}


# ── پست‌های پروفایل تلگرام (پست و آرشیو) ─────────────
class PostCreateRequest(BaseModel):
    media_url: str
    media_type: str = "image"
    caption: Optional[str] = None


class PostOut(BaseModel):
    id: str
    user_id: str
    media_url: str
    media_type: str = "image"
    caption: Optional[str] = None
    archived: bool = False
    created_at: Optional[str] = None


@router.get("/me/posts", response_model=list[PostOut])
async def get_my_posts(
    archived: bool = False,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """لیست پست‌های خود کاربر (عادی یا آرشیو شده)."""
    rows = (await db.execute(
        select(UserPost).where(UserPost.user_id == user.id, UserPost.archived == archived)
        .order_by(UserPost.created_at.desc())
    )).scalars().all()
    return [
        PostOut(
            id=p.id, user_id=p.user_id, media_url=p.media_url,
            media_type=p.media_type or "image", caption=p.caption,
            archived=bool(p.archived), created_at=_iso(p.created_at)
        )
        for p in rows
    ]


@router.post("/me/posts", response_model=PostOut)
async def create_post(
    body: PostCreateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """ایجاد پست جدید در پروفایل کاربر."""
    u = (body.media_url or "").strip()
    if not u:
        raise HTTPException(400, "آدرس رسانه الزامی است")
    post = UserPost(
        user_id=user.id,
        media_url=u,
        media_type=body.media_type or "image",
        caption=body.caption,
        archived=False
    )
    db.add(post)
    await db.commit()
    await db.refresh(post)
    return PostOut(
        id=post.id, user_id=post.user_id, media_url=post.media_url,
        media_type=post.media_type or "image", caption=post.caption,
        archived=bool(post.archived), created_at=_iso(post.created_at)
    )


@router.post("/me/posts/{post_id}/archive")
async def toggle_archive_post(
    post_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """آرشیو کردن یا بازگردانی پست از آرشیو."""
    post = (await db.execute(
        select(UserPost).where(UserPost.id == post_id, UserPost.user_id == user.id)
    )).scalar_one_or_none()
    if not post:
        raise HTTPException(404, "پست پیدا نشد")
    post.archived = not post.archived
    db.add(post)
    await db.commit()
    return {"ok": True, "archived": post.archived}


@router.delete("/me/posts/{post_id}")
async def delete_post(
    post_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """حذف پست از پروفایل."""
    post = (await db.execute(
        select(UserPost).where(UserPost.id == post_id, UserPost.user_id == user.id)
    )).scalar_one_or_none()
    if not post:
        raise HTTPException(404, "پست پیدا نشد")
    await db.delete(post)
    await db.commit()
    return {"ok": True}

