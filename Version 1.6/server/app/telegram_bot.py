"""
رباتِ تلگرام — درون همین پروسه‌ی FastAPI، با «long polling».

دو کار دارد:
  ۱) کدِ دعوت می‌دهد تا رفقا بتوانند در رفیگرام حساب بسازند،
  ۲) پلِ «ارتباط با ما» است: پیامِ کاربر را به مدیر می‌رساند و پاسخِ مدیر
     را به کاربر برمی‌گرداند.
نه وب‌هوکی لازم است نه دامنه‌ای؛ فقط `TELEGRAM_BOT_TOKEN` در env. بی توکن،
ربات ساکت خاموش می‌ماند.

فرمان‌ها (همه در منوی همبرگریِ پایینِ تلگرام هم می‌آیند):
  /start                 → خوش‌آمد + کدِ تازه
  /start support         → حالتِ پشتیبانی (لینکِ «ارتباط با ما» در برنامه)
  /code | «کد»           → کدِ تازه (یا کدِ استفاده‌نشده‌ی قبلی)
  /mycodes               → فهرستِ کدهای استفاده‌نشده + سهمیه‌ی مانده
  /support               → پیام به مدیر
  /admin                 → کدِ همیشگیِ مدیر (بی‌نهایت‌بار، فقط برای صاحبِ ربات)
  /help                  → راهنما

فرمان‌های مدیر (در منو نیستند):
  /tickets               → تیکت‌های بی‌پاسخ
  /r <شماره> <متن>       → پاسخ به یک تیکت (یا فقط ریپلای روی خودِ تیکت)
  /reset                 → ریستِ دیتابیس (دو گامی)

«صاحبِ ربات» کسی است که `TELEGRAM_OWNER_ID` می‌گوید، وگرنه نخستین کسی که
به ربات پیام داد. در `DATA_DIR/bot_owner.json` می‌ماند تا ری‌استارت‌ها
فراموشش نکنند. دکمه‌ی «دریافت کد» در فرمِ ثبت‌نام کد را به همین چت می‌فرستد.

سقفِ حساب (۱.۵): هر کاربرِ عادی روی هم برای ۴ حساب کد می‌گیرد
(`REFERRAL_ACCOUNT_CAP`). حساب در `referrals.quota_state` می‌شود؛ آن‌جا
توضیحش هست که چرا سقفِ قبلی («کدهای استفاده‌نشده») هیچ حدی نمی‌گذاشت.
"""
import asyncio
import json
import logging
import re
import time

import httpx

from .config import settings

log = logging.getLogger("rafigram.bot")

API = "https://api.telegram.org/bot{token}/{method}"
_task: asyncio.Task | None = None
_owner: str | None = None          # کشِ درون‌حافظه‌ایِ شناسه‌ی صاحب

# ── حالتِ پشتیبانی ──────────────────────────────────
# `tg_id → مهلت`. کاربر که «ارتباط با ما» را می‌زند این‌جا می‌نشیند و پیامِ
# بعدی‌اش تیکت می‌شود. عمداً در حافظه است نه دیتابیس: حالتِ چنددقیقه‌ایِ یک
# گفت‌وگو ارزشِ نوشتن روی دیسک را ندارد و اگر سرور ری‌استارت شد، کاربر
# دوباره لینک را می‌زند. خودِ *تیکت* البته در دیتابیس می‌ماند.
_await_msg: dict[str, float] = {}
SUPPORT_TTL = 30 * 60.0
# `message_id`ِ تیکتِ فرستاده‌شده به مدیر → شماره‌ی تیکت، تا مدیر بتواند
# همان‌طور که آدم جواب می‌دهد ریپلای کند و لازم نباشد شماره تایپ کند.
_fwd: dict[str, int] = {}

COMMANDS = [
    {"command": "code", "description": "کدِ دعوتِ تازه (یک‌بارمصرف)"},
    {"command": "mycodes", "description": "کدهای من و سهمیه‌ی مانده"},
    {"command": "support", "description": "ارتباط با ما (پیام به مدیر)"},
    {"command": "admin", "description": "کدِ همیشگیِ مدیر (بی‌نهایت)"},
    {"command": "help", "description": "راهنما"},
]

WELCOME = (
    "سلام! 👋\n"
    "این ربات کدِ دعوتِ «رفیگرام» را می‌دهد.\n\n"
    "کدِ تو:\n<code>{code}</code>\n\n"
    "در برنامه، هنگامِ ساختِ حساب همین را در فیلدِ «کدِ دعوت» بگذار.\n"
    "⚠️ این کد یک‌بارمصرف است؛ بعدِ ثبت‌نام از کار می‌افتد.\n"
    "{quota}\n"
    "منوی پایین (☰) را بزن: /code /mycodes /support"
)

PUSHED = (
    "🔑 کدِ تازه (از دکمه‌ی «دریافت کد» در برنامه):\n"
    "<code>{code}</code>\n\n"
    "یک‌بارمصرف است؛ بعدِ ثبت‌نام باطل می‌شود."
)

# کدی که کاربر خودش از فرمِ ثبت‌نام و با نامِ تلگرامش خواسته
PUSHED_ME = (
    "🔑 کدِ دعوتِ تو:\n<code>{code}</code>\n\n"
    "همین را در فیلدِ «کدِ دعوت» بگذار و حسابت را بساز.\n"
    "یک‌بارمصرف است؛ بعدِ ثبت‌نام باطل می‌شود."
)

CAPPED = (
    "سهمیه‌ات پر شده: <b>{used}</b> حساب از <b>{cap}</b>.\n\n"
    "اگر حسابِ دیگری لازم داری، از /support (یا «ارتباط با ما» در تنظیماتِ "
    "برنامه) بنویس تا دستی برایت باز کنم."
)

ADMIN_MSG = (
    "🛡 کدِ همیشگیِ مدیر:\n<code>{code}</code>\n\n"
    "بی‌نهایت‌بار کار می‌کند و هیچ‌وقت منقضی نمی‌شود.\n"
    "⚠️ به کسی نده؛ هر کسی داشته باشد می‌تواند حساب بسازد."
)

HELP = (
    "دستورها:\n"
    "/code — کدِ دعوتِ تازه (یک‌بارمصرف)\n"
    "/mycodes — کدهای من و سهمیه‌ی مانده\n"
    "/support — ارتباط با ما؛ پیامت به مدیر می‌رسد\n"
    "/admin — کدِ همیشگیِ مدیر (بی‌نهایت‌بار)\n"
    "/help — همین راهنما"
)

HELP_OWNER = HELP + (
    "\n\nمدیر:\n"
    "/tickets — تیکت‌های بی‌پاسخ\n"
    "/r &lt;شماره&gt; &lt;متن&gt; — پاسخ (یا فقط روی تیکت ریپلای کن)\n"
    "/reset — ریستِ دیتابیس (دو گامی)"
)

SUPPORT_ASK = (
    "📮 <b>ارتباط با ما</b>\n\n"
    "پیامت را همین‌جا بنویس و بفرست؛ می‌رسانمش به مدیر و پاسخ را همین‌جا "
    "برایت می‌آورم.\n"
    "<i>(یک پیام؛ اگر بیشتر لازم بود باز /support بزن.)</i>"
)

SUPPORT_GOT = (
    "✅ پیامت رسید (تیکتِ #<b>{num}</b>).\n"
    "پاسخ که آمد، همین‌جا خبرت می‌کنم."
)

TICKET_TO_OWNER = (
    "🎫 <b>تیکتِ #{num}</b>\n"
    "از: {who}\n"
    "──────────\n"
    "{text}\n"
    "──────────\n"
    "پاسخ: روی همین پیام ریپلای کن، یا <code>/r {num} متن</code>"
)

ANSWER_TO_USER = (
    "📬 <b>پاسخِ مدیر</b> (تیکتِ #{num}):\n\n{text}\n\n"
    "<i>اگر باز حرفی داشتی /support بزن.</i>"
)


# ⚠️ عمداً در منوی همبرگری نیست: دستوری که همه‌ی داده‌ها را می‌برد نباید یک
# ضربه‌ی دستِ‌خطا فاصله داشته باشد.
RESET_ARM = (
    "⚠️ <b>ریستِ کاملِ دیتابیس</b>\n\n"
    "این‌ها پاک می‌شوند:\n{rows}\n"
    "کدِ مدیر از <code>SECRET_KEY</code> می‌آید و در دیتابیس نیست، پس بعدِ "
    "ریست هم همان است و می‌توانی دوباره حساب بسازی.\n\n"
    "اگر مطمئنی، تا ۲ دقیقه این را بفرست:\n"
    "<code>/reset {token}</code>"
)



def enabled() -> bool:
    return bool(settings.telegram_bot_token)


# ── صاحبِ ربات ──────────────────────────────────────
def owner_id() -> str | None:
    """شناسه‌ی چتِ صاحب: env ← فایل ← کشِ حافظه."""
    global _owner
    if settings.telegram_owner_id:
        return settings.telegram_owner_id
    if _owner:
        return _owner
    try:
        data = json.loads(settings.owner_file.read_text("utf-8"))
        _owner = str(data.get("id") or "") or None
    except Exception:
        _owner = None
    return _owner


def _claim_owner(tg_id, tg_username) -> bool:
    """نخستین کاربر صاحب می‌شود. `True` = همین حالا صاحب شد."""
    global _owner
    if tg_id is None or owner_id():
        return False
    _owner = str(tg_id)
    try:
        settings.owner_file.write_text(
            json.dumps({"id": _owner, "username": tg_username or ""},
                       ensure_ascii=False), "utf-8")
    except Exception as ex:                     # دیسکِ فقط-خواندنی؟ مهم نیست
        log.warning("owner file write failed: %s", ex)
    log.info("bot owner bound: %s (@%s)", _owner, tg_username or "")
    return True


def is_owner(tg_id) -> bool:
    own = owner_id()
    return bool(own) and str(tg_id) == str(own)



async def _call(client: httpx.AsyncClient, method: str, **params):
    """یک فراخوانیِ Bot API؛ خطا را بالا نمی‌دهد، فقط لاگ می‌کند.

    برای «دریافت کد» باید علتِ شکست در لاگِ Railway معلوم باشد؛ بی این
    فقط پیامِ ۵۰۲ به کاربر می‌رسد و معلوم نیست چرا نشد.
    """
    url = API.format(token=settings.telegram_bot_token, method=method)
    try:
        r = await client.post(url, json=params)
    except httpx.TimeoutException as ex:
        log.error("telegram %s timeout after %ss: %s",
                  method, client.timeout.read, ex)
        return None
    except Exception as ex:                     # شبکه/SSL/…
        log.error("telegram %s failed: %s: %s",
                  method, type(ex).__name__, ex)
        return None
    try:
        data = r.json()
    except Exception:
        log.error("telegram %s: پاسخِ غیر-JSON (HTTP %s): %.200s",
                  method, r.status_code, r.text or "")
        return None
    if r.status_code != 200:
        log.error("telegram %s: HTTP %s: %s", method, r.status_code,
                  (data.get("description") if isinstance(data, dict)
                   else r.text) or "")
    if not isinstance(data, dict) or not data.get("ok"):
        return None
    return data.get("result")


async def _say(client, chat_id, text: str, **kw):
    return await _call(client, "sendMessage", chat_id=chat_id, text=text,
                       parse_mode="HTML", disable_web_page_preview=True, **kw)


def esc(s) -> str:
    """متنِ کاربر را برای `parse_mode=HTML` بی‌خطر می‌کند.

    بی این، یک `<` در پیامِ کاربر کلِ پیام را برای تلگرام بی‌اعتبار می‌کند و
    تیکت هرگز به مدیر نمی‌رسد.
    """
    return (str(s or "").replace("&", "&amp;")
            .replace("<", "&lt;").replace(">", "&gt;"))


# ── نگاشتِ نامِ کاربری → شناسه ───────────────────────
async def _remember(tg_id, username, name) -> None:
    """هر کسی که پیام داد این‌جا ثبت/به‌روز می‌شود.

    تنها منبعِ نگاشتِ `username → tg_id` است؛ توضیحِ کاملش سرِ مدلِ
    `TgUser` است. شکستش هیچ‌جا را نمی‌شکند: فقط «کدِ خودکار» برای این نفر
    کار نمی‌کند و پیامِ صادقانه‌ی «اول ربات را شروع کن» می‌گیرد.
    """
    if tg_id is None:
        return
    from sqlalchemy import select

    from .db import AsyncSessionLocal
    from .models import TgUser, utcnow
    uname = (username or "").strip().lstrip("@").lower() or None
    try:
        async with AsyncSessionLocal() as db:
            row = (await db.execute(
                select(TgUser).where(TgUser.tg_id == str(tg_id))
            )).scalar_one_or_none()
            if row is None:
                db.add(TgUser(tg_id=str(tg_id), username=uname,
                              name=(name or None)))
            else:
                row.username, row.name = uname, (name or row.name)
                row.last_seen_at = utcnow()
                db.add(row)
            await db.commit()
    except Exception as ex:
        log.warning("tg_users upsert failed: %s", ex)


async def resolve_username(db, uname: str) -> str | None:
    """`ali_92` → شناسه‌ی تلگرام، یا None اگر هرگز به ربات پیام نداده."""
    from sqlalchemy import select

    from .models import TgUser
    u = (uname or "").strip().lstrip("@").lower()
    if not u:
        return None
    row = (await db.execute(
        select(TgUser).where(TgUser.username == u)
        .order_by(TgUser.last_seen_at.desc())
    )).scalars().first()
    return row.tg_id if row else None


async def _fresh_code(tg_id, tg_username) -> tuple[str | None, dict]:
    """کدِ کاربر + وضعیتِ سهمیه‌اش: `(کد یا None، وضعیت)`.

    `None` یعنی سقفِ حساب پر است (`referrals.issue_for` تصمیم می‌گیرد).
    مدیر مستثناست.
    """
    from . import referrals
    from .db import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        st = await referrals.quota_state(db, tg_id)
        code, _why = await referrals.issue_for(
            db, tg_id, tg_username=tg_username, exempt=is_owner(tg_id))
    return code, st


async def _my_codes(tg_id) -> tuple[list[str], dict]:
    from . import referrals
    from .db import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        st = await referrals.quota_state(db, tg_id)
    return list(st.get("codes") or []), st


def _quota_line(tg_id, st: dict) -> str:
    """یک خط دربارهٔ سهمیه، برای چسبیدن به تهِ پیامِ خوش‌آمد."""
    if is_owner(tg_id):
        return "\n(تو مدیری؛ سهمیه‌ی حساب نداری.)\n"
    left = max(0, int(st.get("cap", 0)) - int(st.get("used", 0)))
    return (f"\nسهمیه: <b>{left}</b> حساب از <b>{st.get('cap')}</b> مانده.\n")


# ── تیکتِ پشتیبانی ──────────────────────────────────
async def _ticket_new(tg_id, username, name, text: str) -> int | None:
    from .db import AsyncSessionLocal
    from .models import SupportTicket

    try:
        async with AsyncSessionLocal() as db:
            row = SupportTicket(tg_id=str(tg_id),
                                username=(username or None),
                                name=(name or None), text=text[:4000])
            db.add(row)
            await db.commit()
            await db.refresh(row)
            return int(row.id)
    except Exception as ex:
        log.error("ticket create failed: %s", ex)
        return None


async def _ticket_open() -> list:
    from sqlalchemy import select

    from .db import AsyncSessionLocal
    from .models import SupportTicket

    async with AsyncSessionLocal() as db:
        return list((await db.execute(
            select(SupportTicket)
            .where(SupportTicket.answered == False)     # noqa: E712
            .order_by(SupportTicket.id)
        )).scalars().all())


async def _ticket_answer(num: int, text: str):
    """تیکت را پاسخ‌خورده علامت می‌زند و ردیفش را برمی‌گرداند (یا None)."""
    from sqlalchemy import select

    from .db import AsyncSessionLocal
    from .models import SupportTicket, utcnow

    async with AsyncSessionLocal() as db:
        row = (await db.execute(
            select(SupportTicket).where(SupportTicket.id == int(num))
        )).scalar_one_or_none()
        if row is None:
            return None
        row.answered, row.answer = True, text[:4000]
        row.answered_at = utcnow()
        db.add(row)
        await db.commit()
        await db.refresh(row)
        return row


def _who(tg_id, username, name) -> str:
    bits = [esc(name)] if name else []
    if username:
        bits.append(f"@{esc(username)}")
    bits.append(f"<code>{esc(tg_id)}</code>")
    return " · ".join(bits)


async def _support_open(client, chat_id, tg_id) -> None:
    """کاربر را در حالتِ پشتیبانی می‌گذارد؛ پیامِ بعدی‌اش تیکت می‌شود."""
    _await_msg[str(tg_id)] = time.monotonic() + SUPPORT_TTL
    await _say(client, chat_id, SUPPORT_ASK)


async def _support_take(client, chat_id, tg_id, username, name,
                        text: str) -> None:
    """پیامِ کاربر را تیکت می‌کند و به مدیر می‌رساند."""
    _await_msg.pop(str(tg_id), None)
    num = await _ticket_new(tg_id, username, name, text)
    if num is None:
        await _say(client, chat_id,
                   "الان نشد پیامت را ثبت کنم؛ چند لحظه بعد دوباره /support بزن.")
        return
    own = owner_id()
    if own:
        res = await _say(client, own, TICKET_TO_OWNER.format(
            num=num, who=_who(tg_id, username, name), text=esc(text)))
        if isinstance(res, dict) and res.get("message_id"):
            _fwd[str(res["message_id"])] = num
    else:
        log.warning("ticket #%s has no owner to notify", num)
    await _say(client, chat_id, SUPPORT_GOT.format(num=num))


async def _support_reply(client, chat_id, num: int, text: str) -> None:
    """پاسخِ مدیر را به صاحبِ تیکت می‌رساند."""
    row = await _ticket_answer(num, text)
    if row is None:
        await _say(client, chat_id, f"تیکتِ #{num} پیدا نشد.")
        return
    ok = await _say(client, row.tg_id,
                    ANSWER_TO_USER.format(num=num, text=esc(text)))
    if ok is None:
        await _say(client, chat_id,
                   f"تیکتِ #{num} بسته شد، ولی پیام به کاربر نرسید "
                   "(شاید ربات را بلاک کرده).")
        return
    await _say(client, chat_id, f"✅ پاسخِ تیکتِ #{num} فرستاده شد.")



async def _reset(client, chat_id, tg_id, arg: str) -> None:
    """ریستِ دیتابیس — دو گامی و فقط برای صاحبِ ربات."""
    from . import maintenance
    from .db import AsyncSessionLocal

    if not arg:
        async with AsyncSessionLocal() as db:
            cnt = await maintenance.counts(db)
        rows = "\n".join(f"• {k}: {v}" for k, v in cnt.items()) \
            or "• (دیتابیس همین حالا خالی است)"
        await _say(client, chat_id, RESET_ARM.format(
            rows=rows, token=maintenance.arm(tg_id)))
        return
    if not maintenance.check(tg_id, arg):
        await _say(client, chat_id,
                   "نشانه‌ی تأیید درست نبود یا منقضی شد. دوباره /reset بزن.")
        return
    async with AsyncSessionLocal() as db:
        gone = await maintenance.wipe(db)
    body = "\n".join(f"• {k}: {v}" for k, v in gone.items()) or "• چیزی نبود"
    await _say(client, chat_id, f"🧹 دیتابیس ریست شد:\n{body}\n\n"
                                f"کدِ مدیر همان قبلی است (/admin).")


def _ticket_of_text(s: str) -> int | None:
    """شماره‌ی تیکت را از متنِ پیامِ ریپلای‌شده درمی‌آورد.

    `_fwd` در حافظه است و ری‌استارتِ Railway پاکش می‌کند؛ ولی خودِ پیامِ
    تیکت در تلگرام مانده و تیترش «تیکتِ #12» است. پس ریپلای بعدِ ری‌استارت
    هم کار می‌کند. شرطِ «تیکت» در متن هست تا ریپلای روی یک پیامِ عادی که
    تصادفاً `#۵` دارد، بی‌خود تیکتی را نبندد.
    """
    if "تیکت" not in (s or ""):
        return None
    m = re.search(r"#(\d{1,9})", s)
    return int(m.group(1)) if m else None


async def _handle(client, msg: dict):
    chat = (msg.get("chat") or {})
    chat_id = chat.get("id")
    if chat_id is None:
        return
    frm = msg.get("from") or {}
    tg_id = frm.get("id")
    tg_username = frm.get("username")
    tg_name = " ".join(x for x in (frm.get("first_name"),
                                   frm.get("last_name")) if x).strip()
    text = (msg.get("text") or "").strip()
    low = text.lower().split("@")[0]

    # نخستین رفیقی که پیام داد صاحبِ ربات می‌شود؛ «دریافت کد»ِ برنامه از آن
    # به بعد به همین چت می‌رسد.
    if _claim_owner(tg_id, tg_username):
        await _say(client, chat_id,
                   "✅ این چت به‌عنوانِ چتِ مدیرِ رفیگرام ثبت شد.\n"
                   "از این پس دکمه‌ی «دریافت کد» در برنامه، کد را همین‌جا "
                   "می‌فرستد.")
    # نامِ کاربری را همیشه به‌روز نگه می‌داریم؛ «کدِ خودکار» از همین می‌خواند.
    await _remember(tg_id, tg_username, tg_name)

    if low.startswith("/help"):
        await _say(client, chat_id,
                   HELP_OWNER if is_owner(tg_id) else HELP)
        return
    if low.startswith("/admin"):
        if not is_owner(tg_id):
            await _say(client, chat_id, "این دستور فقط برای مدیرِ ربات است.")
            return
        await _say(client, chat_id,
                   ADMIN_MSG.format(code=settings.admin_referral_code))
        return
    if low.startswith("/reset"):
        if not is_owner(tg_id):
            await _say(client, chat_id, "این دستور فقط برای مدیرِ ربات است.")
            return
        await _reset(client, chat_id, tg_id, text.split(maxsplit=1)[1]
                     if len(text.split(maxsplit=1)) > 1 else "")
        return

    # ── پنلِ پشتیبانیِ مدیر ─────────────────────────
    if low.startswith("/tickets"):
        if not is_owner(tg_id):
            await _say(client, chat_id, "این دستور فقط برای مدیرِ ربات است.")
            return
        rows = await _ticket_open()
        if not rows:
            await _say(client, chat_id, "تیکتِ بی‌پاسخی نیست. 🎉")
            return
        body = "\n\n".join(
            f"🎫 <b>#{r.id}</b> — {_who(r.tg_id, r.username, r.name)}\n"
            f"{esc((r.text or '')[:300])}" for r in rows[:20])
        await _say(client, chat_id,
                   f"تیکت‌های بی‌پاسخ ({len(rows)}):\n\n{body}\n\n"
                   "پاسخ: <code>/r شماره متن</code>")
        return
    if low.startswith("/r ") or low == "/r":
        if not is_owner(tg_id):
            await _say(client, chat_id, "این دستور فقط برای مدیرِ ربات است.")
            return
        parts = text.split(maxsplit=2)
        if len(parts) < 3 or not parts[1].lstrip("#").isdigit():
            await _say(client, chat_id,
                       "شکلِ درست: <code>/r 12 سلام، درست شد</code>")
            return
        await _support_reply(client, chat_id,
                             int(parts[1].lstrip("#")), parts[2])
        return
    # مدیر روی خودِ تیکت ریپلای کرد؟ همان‌قدر خوب است و شماره لازم نیست.
    if is_owner(tg_id) and text and not text.startswith("/"):
        rep = msg.get("reply_to_message") or {}
        num = _fwd.get(str(rep.get("message_id") or ""))
        if num is None:
            num = _ticket_of_text(rep.get("text") or "")
        if num is not None:
            await _support_reply(client, chat_id, num, text)
            return

    # ── پشتیبانی از سمتِ کاربر ──────────────────────
    # لینکِ «ارتباط با ما» در برنامه `/start support` است.
    if low.startswith("/support") or low in ("/start support", "/start=support"):
        await _support_open(client, chat_id, tg_id)
        return
    exp = _await_msg.get(str(tg_id), 0.0)
    if exp and text and not text.startswith("/"):
        if time.monotonic() < exp:
            await _support_take(client, chat_id, tg_id, tg_username,
                                tg_name, text)
            return
        _await_msg.pop(str(tg_id), None)        # مهلت گذشته بود

    if low.startswith("/mycodes"):
        codes, st = await _my_codes(tg_id)
        head = ("کدِ استفاده‌نشده‌ای نداری." if not codes
                else "کدهای استفاده‌نشده‌ی تو:\n"
                     + "\n".join(f"<code>{c}</code>" for c in codes))
        await _say(client, chat_id, head + "\n" + _quota_line(tg_id, st)
                   + ("\n/code بزن." if not codes else ""))
        return

    # هر چیزِ دیگری (شاملِ /start و /code) = «کد می‌خواهم»
    code, st = await _fresh_code(tg_id, tg_username)
    if not code:
        # سقف پر است — این تنها جایی است که ربات «نه» می‌گوید، و همان‌جا
        # راهِ درست (پشتیبانی) را نشان می‌دهد.
        await _say(client, chat_id, CAPPED.format(used=st.get("used"),
                                                  cap=st.get("cap")))
        return
    await _say(client, chat_id, WELCOME.format(
        code=code, quota=_quota_line(tg_id, st)))


# ── فرستادنِ کد از سمتِ برنامه ──────────────────────
async def send_code(chat_id, code: str, personal: bool = False) -> bool:
    """یک کدِ آماده را به چتِ داده‌شده می‌فرستد. `False` = نشد.

    `personal=True` یعنی گیرنده خودِ متقاضی است (مسیرِ «کدِ خودکار» در
    `/referral/otp`)، نه مدیری که باید کد را دستی برساند
    (`/referral/request`)؛ فقط متنِ پیام فرق می‌کند.
    """
    if not enabled():
        return False
    tpl = PUSHED_ME if personal else PUSHED
    timeout = httpx.Timeout(20.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        res = await _call(client, "sendMessage", chat_id=chat_id,
                          text=tpl.format(code=code), parse_mode="HTML",
                          disable_web_page_preview=True)
    return res is not None


async def _setup_menu(client) -> None:
    """فرمان‌ها را ثبت می‌کند تا منوی همبرگریِ پایینِ تلگرام پرشود."""
    await _call(client, "setMyCommands", commands=COMMANDS)
    # دکمه‌ی کنارِ فیلدِ نوشتن را روی «فهرستِ فرمان‌ها» می‌گذارد (همان ☰)
    await _call(client, "setChatMenuButton",
                menu_button={"type": "commands"})



async def _poll():
    """حلقه‌ی long polling. تا خودِ برنامه زنده است، این هم زنده است."""
    offset = 0
    backoff = 1.0
    timeout = httpx.Timeout(40.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        me = await _call(client, "getMe")
        if me:
            uname = me.get("username") or ""
            if uname and not settings.telegram_bot_username:
                settings.telegram_bot_username = uname
            log.info("telegram bot online: @%s", uname)
        await _setup_menu(client)
        while True:
            try:
                ups = await _call(client, "getUpdates", offset=offset,
                                  timeout=30,
                                  allowed_updates=["message"])
                if ups is None:
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 1.7, 30.0)
                    continue
                backoff = 1.0
                for up in ups:
                    offset = max(offset, int(up.get("update_id", 0)) + 1)
                    msg = up.get("message")
                    if msg:
                        try:
                            await _handle(client, msg)
                        except Exception as ex:
                            log.warning("bot handler failed: %s", ex)
            except asyncio.CancelledError:
                raise
            except Exception as ex:
                log.warning("bot poll failed: %s", ex)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 1.7, 30.0)


async def start() -> None:
    """از `lifespan` صدا زده می‌شود؛ بی‌توکن هیچ کاری نمی‌کند."""
    global _task
    if not enabled():
        log.info("telegram bot disabled (no TELEGRAM_BOT_TOKEN)")
        return
    if _task and not _task.done():
        return
    _task = asyncio.create_task(_poll(), name="telegram-bot")


async def stop() -> None:
    global _task
    task, _task = _task, None
    if task:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
