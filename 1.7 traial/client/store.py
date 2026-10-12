"""انبارِ محلی — لیستِ چت‌ها و آخرین پیام‌ها روی دستگاه می‌مانند.

چرا: تلگرام آفلاین هم چیزی نشان می‌دهد. پیش از این، بی‌اینترنت لیستِ چت
خالی بود و صفحه‌ی چت هم هیچ پیامی نداشت (فقط فایلِ رسانه کش می‌شد،
`_cached_file`). این‌جا یک انبارِ کوچکِ ماندگار می‌سازیم که:

  • در باز شدنِ برنامه، لیستِ چت *فوراً* از انبار رنگ می‌شود و بعد
    درخواستِ شبکه رویش می‌نشیند (حسِ «سریع باز شد»، حتی روی نتِ کند)،
  • آفلاین، آخرین پیام‌های هر گفت‌وگو خوانده می‌شوند.

بستر همان `ft.SharedPreferences` است (روی رومیزی فایلِ JSON، روی اندروید
SharedPreferences، در مرورگر localStorage) — پس یک راه برای هر سه.
`localStorage` حدودِ ۵ مگابایت جا دارد، این است که سخت‌گیرانه سهمیه
می‌گذاریم: تنها کلیدهای لازمِ هر چت، ۴۰ پیامِ آخر، و حداکثر ۲۵ گفت‌وگو.
"""
import json

import prefs

CHATS_KEY = "cache.chats"
MSGS_KEY = "cache.msgs."
PROF_KEY = "cache.prof."   # پروفایلِ هر گفت‌وگو (سرصفحه + نقشِ من)
BLOB_KEY = "cache.b."     # تکه‌های کوچکِ JSON (پروفایلِ خودم، حریمِ خصوصی، …)
BLOBS = ("me", "priv")    # نامِ همه‌ی تکه‌ها — برای پاک‌کردنِ سرِ خروج
MAX_CHATS = 60          # ردیف‌های لیست (سبک‌اند)
MAX_MSG_CHATS = 25      # گفت‌وگوهایی که پیام‌شان نگه داشته می‌شود
MAX_MSGS = 40           # پیامِ آخر در هر گفت‌وگو
MAX_MEMBERS = 50        # اعضایی که از یک گروه ذخیره می‌شوند

# فقط همین کلیدها ذخیره می‌شوند؛ بقیه‌ی چیزها آفلاین بی‌مصرف‌اند و جا می‌خورند.
CHAT_KEYS = ("chat_id", "title", "avatar_url", "is_group", "kind", "handle",
             "archived", "unread", "muted", "pinned", "other_user",
             "last_message", "last_message_at", "last_message_type",
             "last_sender_id", "members_count")
MSG_KEYS = ("message_id", "chat_id", "sender_id", "sender_name", "content",
            "msg_type", "created_at", "reply_to", "reply_preview", "caption",
            "enc", "edited", "seen_by", "deleted", "reactions",
            "my_reactions")
# پروفایلِ گفت‌وگو: هرچه سرصفحه و دسترسی‌ها از آن ساخته می‌شوند. بی این،
# باز شدنِ گفت‌وگو ناچار بود منتظرِ `chat/profile` بماند و «نقشِ من» را
# نمی‌دانست (کانالِ خودم «نمی‌توانی بنویسی» نشان می‌داد).
PROF_KEYS = ("chat_id", "title", "avatar_url", "bio", "is_group", "kind",
             "handle", "my_role", "can_post", "members_count",
             "members_hidden", "is_public")
MEM_KEYS = ("user_id", "display_name", "username", "avatar_url",
            "last_seen_at", "show_last_seen", "role")

# ⚠️ حضور ذخیره نمی‌شود. `online` یک حقیقتِ *لحظه‌ای* است و ذخیره‌اش یعنی
# دفعه‌ی بعد که برنامه باز شد — شاید ساعت‌ها بعد، شاید بی‌اینترنت — نقطه‌ی
# سبزِ «آنلاین» از روی داده‌ی کهنه کشیده شود. `last_seen_at` می‌ماند، چون
# خودش تاریخ دارد و پنجره‌ی ۷۰ ثانیه‌ایِ `_is_online` طبیعتاً منقضی‌اش
# می‌کند؛ پس آفلاین «آخرین بازدید ۱۰:۴۵» دیده می‌شود، نه ادعای دروغ.
VOLATILE = ("online",)


def _trim(row, keys):
    out = {k: row.get(k) for k in keys if row.get(k) is not None}
    ou = out.get("other_user")
    if isinstance(ou, dict):
        out["other_user"] = {k: v for k, v in ou.items() if k not in VOLATILE}
    return out


def _mine(key: str, uid=None) -> str:
    """کلیدها به کاربر بسته‌اند؛ با عوض شدنِ حساب، انبارِ نفرِ قبلی دیده نشود.

    `uid` تنها برای پاک‌کردنِ *پس از* خروج لازم می‌شود: آن‌جا `api.user_id`
    دیگر خالی است و بی این پارامتر، انبارِ واقعیِ کاربر روی دستگاه می‌ماند.
    """
    if uid is None:
        from services.api import api
        uid = api.user_id
    return f"{key}|{uid or '-'}"


async def save_chats(chats) -> None:
    try:
        rows = [_trim(c, CHAT_KEYS) for c in (chats or [])[:MAX_CHATS]]
        await prefs.raw_put(_mine(CHATS_KEY), json.dumps(rows,
                                                         ensure_ascii=False))
    except Exception:
        pass


async def load_chats() -> list:
    try:
        raw = await prefs.raw_get(_mine(CHATS_KEY))
        rows = json.loads(raw) if raw else []
        return rows if isinstance(rows, list) else []
    except Exception:
        return []


async def save_msgs(chat_id, msgs) -> None:
    """آخرین پیام‌های یک گفت‌وگو + به‌روزکردنِ فهرستِ سهمیه."""
    if not chat_id:
        return
    try:
        rows = [_trim(m, MSG_KEYS) for m in (msgs or [])[-MAX_MSGS:]]
        await prefs.raw_put(_mine(MSGS_KEY + str(chat_id)),
                            json.dumps(rows, ensure_ascii=False))
        await _touch(str(chat_id))
    except Exception:
        pass


async def load_msgs(chat_id) -> list:
    if not chat_id:
        return []
    try:
        raw = await prefs.raw_get(_mine(MSGS_KEY + str(chat_id)))
        rows = json.loads(raw) if raw else []
        return rows if isinstance(rows, list) else []
    except Exception:
        return []


async def save_prof(chat_id, prof) -> None:
    """پروفایلِ یک گفت‌وگو را نگه می‌دارد — تا بارِ بعد *فوری* باز شود.

    `offline=True` یعنی خودش از همین انبار آمده؛ دوباره‌نویسی‌اش بی‌فایده است
    و بدتر، نسخه‌ی کاملِ سرور را با نسخه‌ی ناقص جانشین می‌کند.
    """
    if not chat_id or not isinstance(prof, dict) or prof.get("offline"):
        return
    try:
        row = {k: prof.get(k) for k in PROF_KEYS if prof.get(k) is not None}
        row["members"] = [
            {k: v for k, v in (m or {}).items()
             if k in MEM_KEYS and v is not None}
            for m in (prof.get("members") or [])[:MAX_MEMBERS]]
        await prefs.raw_put(_mine(PROF_KEY + str(chat_id)),
                            json.dumps(row, ensure_ascii=False))
        await _touch(str(chat_id))
    except Exception:
        pass


async def load_prof(chat_id) -> dict:
    if not chat_id:
        return {}
    try:
        raw = await prefs.raw_get(_mine(PROF_KEY + str(chat_id)))
        got = json.loads(raw) if raw else None
        if not isinstance(got, dict) or not got:
            return {}
        got["offline"] = True     # ⚠️ «این از انبار است، نه از سرور»
        return got
    except Exception:
        return {}


async def save_blob(name: str, obj) -> None:
    """یک تکه‌ی کوچکِ JSON را نگه می‌دارد — پروفایلِ خودم، حریمِ خصوصی، …

    چرا لازم شد: صفحه‌ی «تنظیمات» و «پروفایل» پیش از کشیدنِ اولین پیکسل
    منتظرِ `/profile/me` و `/privacy` می‌ماندند. روی نتِ فیلترشده این یعنی
    چند ثانیه صفحه‌ی خالی — همان «وقتی میزنم رو ستینگ طول میکشه». اکنون از
    همین انبار *فوری* رنگ می‌شوند و شبکه پشتِ صحنه به‌روزشان می‌کند.
    """
    try:
        await prefs.raw_put(_mine(BLOB_KEY + str(name)),
                            json.dumps(obj, ensure_ascii=False))
    except Exception:
        pass


async def load_blob(name: str, default=None):
    try:
        raw = await prefs.raw_get(_mine(BLOB_KEY + str(name)))
        got = json.loads(raw) if raw else None
        return got if isinstance(got, (dict, list)) else default
    except Exception:
        return default


async def _touch(chat_id: str) -> None:
    """گفت‌وگوی تازه سرِ فهرست؛ از سهمیه که گذشت، کهنه‌ترین پاک می‌شود.

    بی این، انبارِ مرورگر با هر گفت‌وگویی که باز می‌شود بزرگ‌تر می‌شد تا
    `localStorage` پر شود و *نوشتنِ* بعدی خطا بدهد.
    """
    raw = await prefs.raw_get(_mine("cache.order"))
    try:
        order = json.loads(raw) if raw else []
    except Exception:
        order = []
    order = [c for c in order if c != chat_id][:MAX_MSG_CHATS * 2]
    order.insert(0, chat_id)
    for old in order[MAX_MSG_CHATS:]:
        await prefs.raw_put(_mine(MSGS_KEY + old), "")
        await prefs.raw_put(_mine(PROF_KEY + old), "")
    order = order[:MAX_MSG_CHATS]
    await prefs.raw_put(_mine("cache.order"),
                        json.dumps(order, ensure_ascii=False))


async def clear(uid=None) -> None:
    """خروج از حساب: هر چه از این کاربر مانده پاک شود.

    `uid` را وقتی بده که `api.user_id` را از قبل از دست داده‌ای (مثلاً
    `verify()` نشستِ باطل را دور ریخته)؛ وگرنه هیچ‌چیز پاک نمی‌شود.
    """
    try:
        raw = await prefs.raw_get(_mine("cache.order", uid))
        order = json.loads(raw) if raw else []
    except Exception:
        order = []
    for cid in order:
        await prefs.raw_put(_mine(MSGS_KEY + str(cid), uid), "")
        await prefs.raw_put(_mine(PROF_KEY + str(cid), uid), "")
    await prefs.raw_put(_mine("cache.order", uid), "")
    await prefs.raw_put(_mine(CHATS_KEY, uid), "")
    for name in BLOBS:
        await prefs.raw_put(_mine(BLOB_KEY + name, uid), "")
