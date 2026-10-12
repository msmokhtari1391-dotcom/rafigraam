"""مدل‌های دیتابیس"""
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return uuid.uuid4().hex


class User(SQLModel, table=True):
    __tablename__ = "users"
    id: str = Field(default_factory=new_id, primary_key=True)
    username: str = Field(index=True, unique=True, min_length=2, max_length=32)
    display_name: str = Field(max_length=64)
    password_hash: str = Field()
    avatar_url: Optional[str] = Field(default=None)
    bio: Optional[str] = Field(default=None, max_length=200)
    created_at: datetime = Field(default_factory=utcnow)
    # آخرین بازدید + این‌که برای بقیه دیده بشه یا نه
    last_seen_at: Optional[datetime] = Field(default=None)
    show_last_seen: bool = Field(default=True)


class UserPhoto(SQLModel, table=True):
    """عکس‌های پروفایل (حداکثر ۵ تا؛ اولی همون avatar_url فعلیه)."""
    __tablename__ = "user_photos"
    id: str = Field(default_factory=new_id, primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    url: str = Field()
    created_at: datetime = Field(default_factory=utcnow)


# نقش‌های عضویت
ROLE_OWNER, ROLE_ADMIN, ROLE_MEMBER = "owner", "admin", "member"
ROLE_RANK = {ROLE_OWNER: 3, ROLE_ADMIN: 2, ROLE_MEMBER: 1}

# نوع گفت‌وگو
KIND_PRIVATE, KIND_GROUP, KIND_CHANNEL = "private", "group", "channel"
# «پیام‌های ذخیره‌شده» — گفت‌وگوی تک‌نفره‌ی خودِ کاربر با خودش. دفترچه‌ی
# یادداشتِ تلگرام. یک عضو دارد (صاحبش)، در جست‌وجوی سراسری نمی‌آید، و
# هرچه در آن نشست *نسخه‌ی مستقلِ خودش* است: فرستنده‌ی اصلی پیام و فایلش را
# پاک کند، این یکی سرِ جایش می‌ماند.
KIND_SAVED = "saved"


class Chat(SQLModel, table=True):
    __tablename__ = "chats"
    id: str = Field(default_factory=new_id, primary_key=True)
    title: Optional[str] = Field(default=None, max_length=128)
    is_group: bool = Field(default=False)
    bio: Optional[str] = Field(default=None, max_length=200)
    avatar_url: Optional[str] = Field(default=None)
    creator_id: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow)
    # private | group | channel
    kind: str = Field(default=KIND_PRIVATE, index=True)
    # عمومی = در جست‌وجو پیدا می‌شه و هرکسی می‌تونه عضو شه
    is_public: bool = Field(default=False)
    # شناسه‌ی عمومی (@handle) — یکتا، فقط برای گروه/کانال
    handle: Optional[str] = Field(default=None, index=True)


class ChatMember(SQLModel, table=True):
    __tablename__ = "chat_members"
    id: str = Field(default_factory=new_id, primary_key=True)
    chat_id: str = Field(foreign_key="chats.id", index=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    joined_at: datetime = Field(default_factory=utcnow)
    # آخرین لحظه‌ای که این عضو چت رو خونده (برای شمارنده‌ی نخونده‌ها)
    last_read_at: Optional[datetime] = Field(default=None)
    # owner | admin | member
    role: str = Field(default=ROLE_MEMBER)
    # این عضو چت رو بایگانی کرده (فقط برای خودش)
    archived: bool = Field(default=False)
    # اخراج‌شده‌ی همیشگی — نمی‌تونه دوباره عضو شه
    banned: bool = Field(default=False)


class Message(SQLModel, table=True):
    __tablename__ = "messages"
    id: str = Field(default_factory=new_id, primary_key=True)
    chat_id: str = Field(foreign_key="chats.id", index=True)
    sender_id: str = Field(foreign_key="users.id")
    content: str = Field()
    msg_type: str = Field(default="text")
    file_url: Optional[str] = Field(default=None)
    reply_to: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow)
    # ویرایش / حذف برای همه / سنجاق
    edited_at: Optional[datetime] = Field(default=None)
    deleted: bool = Field(default=False)
    pinned: bool = Field(default=False)
    # نامِ فرستنده‌ی اصلی وقتی پیام فوروارد شده
    forward_from: Optional[str] = Field(default=None)
    # پیام رمزنگاری‌شده فرستاده شده؟ (قلابِ رمزنگاری سمت کلاینت)
    enc: bool = Field(default=False)
    # نوشته‌ی زیرِ مدیا (عکس/ویدیو/گیف/فایل)
    caption: Optional[str] = Field(default=None, max_length=1024)


class SavedGif(SQLModel, table=True):
    """گیف‌های ذخیره‌شده‌ی هر کاربر — «گیف‌های من» در پنلِ چت."""
    __tablename__ = "saved_gifs"
    id: str = Field(default_factory=new_id, primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    url: str = Field()
    created_at: datetime = Field(default_factory=utcnow)


class MessageHide(SQLModel, table=True):
    """پیامی که فقط برای یک کاربر پاک شده («حذف برای خودم»)."""
    __tablename__ = "message_hides"
    id: str = Field(default_factory=new_id, primary_key=True)
    message_id: str = Field(foreign_key="messages.id", index=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    created_at: datetime = Field(default_factory=utcnow)


class MessageReaction(SQLModel, table=True):
    """واکنشِ یک کاربر به یک پیام (هر کاربر برای هر ایموجی یک ردیف)."""
    __tablename__ = "message_reactions"
    id: str = Field(default_factory=new_id, primary_key=True)
    message_id: str = Field(foreign_key="messages.id", index=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    emoji: str = Field(max_length=16)
    created_at: datetime = Field(default_factory=utcnow)


class Block(SQLModel, table=True):
    """کاربر A کاربر B رو بلاک کرده."""
    __tablename__ = "blocks"
    id: str = Field(default_factory=new_id, primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True)     # بلاک‌کننده
    blocked_id: str = Field(foreign_key="users.id", index=True)   # بلاک‌شده
    created_at: datetime = Field(default_factory=utcnow)


class AuthSession(SQLModel, table=True):
    """نشست فعال (هر ورود یک ردیف) — برای «دستگاه‌های فعال» و لغو دسترسی."""
    __tablename__ = "auth_sessions"
    id: str = Field(default_factory=new_id, primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    device: str = Field(default="", max_length=200)
    revoked: bool = Field(default=False)
    created_at: datetime = Field(default_factory=utcnow)
    last_active_at: datetime = Field(default_factory=utcnow)


class ReferralCode(SQLModel, table=True):
    """کدِ دعوتِ یک‌بارمصرف.

    رباتِ تلگرام برای هر درخواست یک کد می‌سازد و همان‌جا به کاربر می‌دهد؛
    سرِ ثبت‌نام کد «سوزانده» می‌شود (`used=True`) و دیگر به کار نمی‌آید.
    کدِ مدیر (بی‌نهایت‌بار) در env است و ردیفی در این جدول ندارد.
    """
    __tablename__ = "referral_codes"
    id: str = Field(default_factory=new_id, primary_key=True)
    code: str = Field(index=True, unique=True, max_length=32)
    # سازنده در تلگرام (برای پیگیری و سهمیه)
    tg_user_id: Optional[str] = Field(default=None, index=True)
    tg_username: Optional[str] = Field(default=None, max_length=64)
    used: bool = Field(default=False)
    used_by: Optional[str] = Field(default=None)          # users.id
    used_at: Optional[datetime] = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow)


class TgUser(SQLModel, table=True):
    """هر کاربرِ تلگرامی که دستِ‌کم یک‌بار به ربات پیام داده.

    چرا لازم است: Bot API اجازه نمی‌دهد به کسی که *خودش* ربات را شروع
    نکرده پیام بدهیم — نه با شناسه و نه با نامِ کاربری. پس برای «کدِ
    خودکار» (کاربر نامِ تلگرامش را در فرمِ ثبت‌نام می‌نویسد و کد به تلگرامِ
    *خودش* می‌رسد) باید نگاشتِ `username → tg_id` را از پیش داشته باشیم؛
    و تنها راهِ پرکردنش همین است که هنگامِ پیام‌دادن ثبتش کنیم.
    """
    __tablename__ = "tg_users"
    id: str = Field(default_factory=new_id, primary_key=True)
    tg_id: str = Field(index=True, unique=True, max_length=32)
    # بی @ و همیشه با حروفِ کوچک ذخیره می‌شود (تلگرام بی‌حساسیت به بزرگی است)
    username: Optional[str] = Field(default=None, index=True, max_length=64)
    name: Optional[str] = Field(default=None, max_length=128)
    created_at: datetime = Field(default_factory=utcnow)
    last_seen_at: datetime = Field(default_factory=utcnow)


class SupportTicket(SQLModel, table=True):
    """تیکتِ «ارتباط با ما» — پیامی که کاربر از راهِ ربات فرستاده.

    شناسه‌اش عدد است (نه uuid) چون مدیر باید بتواند در تلگرام تایپش کند:
    `/r 12 پاسخ`. بقیه‌ی جدول‌ها uuid دارند، این یکی برای دستِ آدم است.
    """
    __tablename__ = "support_tickets"
    id: Optional[int] = Field(default=None, primary_key=True)
    tg_id: str = Field(index=True, max_length=32)
    username: Optional[str] = Field(default=None, max_length=64)
    name: Optional[str] = Field(default=None, max_length=128)
    text: str = Field(max_length=4000)
    created_at: datetime = Field(default_factory=utcnow, index=True)
    answered: bool = Field(default=False, index=True)
    answer: Optional[str] = Field(default=None, max_length=4000)
    answered_at: Optional[datetime] = Field(default=None)


# ── استوری ─────────────────────────────────────────
# صاحبِ استوری یا یک کاربر است یا یک گفت‌وگو (گروه/کانال)
OWNER_USER, OWNER_CHAT = "user", "chat"
STORY_TTL_HOURS = 24


class Story(SQLModel, table=True):
    """استوریِ ۲۴ساعته — کاربر، گروه، یا کانال.

    `owner_kind`/`owner_id` می‌گوید استوری زیرِ نامِ کی نشسته (کاربر یا
    گفت‌وگو) و `author_id` می‌گوید کدام کاربر گذاشته‌اش (در گروه/کانال با
    هم فرق دارند). `expires_at` سرِ ساخت حساب می‌شود، پس فیلترِ «فعال» یک
    مقایسه‌ی ساده است و به ساعتِ خواندن وابسته نیست.
    """
    __tablename__ = "stories"
    id: str = Field(default_factory=new_id, primary_key=True)
    owner_kind: str = Field(default=OWNER_USER, index=True)   # user | chat
    owner_id: str = Field(index=True)                          # users.id | chats.id
    author_id: str = Field(foreign_key="users.id", index=True)
    media_url: str = Field()
    media_type: str = Field(default="image")                   # image | video
    caption: Optional[str] = Field(default=None, max_length=1024)
    created_at: datetime = Field(default_factory=utcnow, index=True)
    expires_at: datetime = Field(default_factory=utcnow, index=True)


class StoryView(SQLModel, table=True):
    """چه کسی چه استوری‌ای را دیده (برای حلقه‌ی روشن و فهرستِ بازدیدکننده‌ها)."""
    __tablename__ = "story_views"
    id: str = Field(default_factory=new_id, primary_key=True)
    story_id: str = Field(foreign_key="stories.id", index=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    created_at: datetime = Field(default_factory=utcnow)


class PushSub(SQLModel, table=True):
    """اشتراکِ اعلانِ بیرونِ برنامه — یک دستگاه، یک راهِ رساندن.

    `kind="web"` تنها گونه‌ی ماندگار است: اشتراکِ Web Push مرورگر
    (آدرسِ push + دو کلیدِ رمزنگاری). کانالِ بومیِ اندروید ردیف نمی‌خواهد،
    چون خودِ long-poll با توکنِ کاربر احراز می‌شود.

    `endpoint` یکتاست: یک مرورگر ثبتِ دوباره کند، همان ردیف تازه می‌شود نه
    ردیفِ دوم — وگرنه هر بار باز شدنِ سایت یک اعلانِ تکراری اضافه می‌کرد.
    """
    __tablename__ = "push_subs"
    id: str = Field(default_factory=new_id, primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    kind: str = Field(default="web", max_length=8)
    endpoint: str = Field(default="", index=True)
    p256dh: str = Field(default="")
    auth: str = Field(default="")
    device: str = Field(default="", max_length=200)
    created_at: datetime = Field(default_factory=utcnow)
    last_ok_at: Optional[datetime] = Field(default=None)
    fails: int = Field(default=0)
