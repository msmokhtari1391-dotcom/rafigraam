"""
API HTTP client — ارتباط با سرور FastAPI.
همه متد‌ها async هستن تا UI بلاک نشه.

از نسخه‌ی ۱.۵ یک لایه‌ی «کشِ خواندنِ عبوری» هم دارد: هر پاسخِ موفقِ خواندنی
روی دیسک می‌نشیند و اگر بارِ بعد شبکه نبود (یا کند بود و خطا داد) همان نسخه‌ی
دیسک برمی‌گردد. خواسته‌ی کاربر: «هیچ وابستگیِ رابط به شبکه» — لیستِ چت،
پیام‌ها و پروفایل باید آفلاین هم بیایند، بی چرخنده‌ی بی‌پایان.
"""
import asyncio
import hashlib
import json
import logging
import os
import platform
import unicodedata
import uuid

import httpx

import config

log = logging.getLogger("rafigram.api")

_CHUNK = 64 * 1024


# ── کشِ آفلاینِ پاسخ‌های خواندنی ────────────────────────
# جای فایل‌ها همان پوشه‌ی داده‌ی پایدارِ برنامه است (نه پوشه‌ی موقت، که سیستم
# جارویش می‌کند). محتوای چت روی دیسکِ *خصوصیِ برنامه* می‌نشیند — همان کاری که
# خودِ تلگرام هم می‌کند؛ روی اندروید برنامه‌ی دیگری نمی‌تواند بخواندش.
_CACHE_OK = True


def _cache_dir() -> str:
    android = os.getenv("FLET_APP_STORAGE_DATA")
    if android:
        base = os.path.join(android, "rafigram")
    else:
        root = (os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA")
                or os.path.join(os.path.expanduser("~"), ".local", "share"))
        base = os.path.join(root, "Rafigram")
    d = os.path.join(base, "apicache")
    os.makedirs(d, exist_ok=True)
    return d


def _cache_file(key: str) -> str:
    # کلید ممکن است شناسه‌ی چت و پارامتر داشته باشد ⇒ هش می‌کنیم تا نامِ فایل
    # همیشه قانونی بماند (روی ویندوز `:` و `/` مجاز نیستند).
    h = hashlib.sha1(key.encode("utf-8")).hexdigest()[:20]
    return os.path.join(_cache_dir(), f"{h}.json")


def cache_put(key: str, data) -> None:
    global _CACHE_OK
    if not _CACHE_OK:
        return
    try:
        p = _cache_file(key)
        with open(p + ".tmp", "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(p + ".tmp", p)            # نوشتنِ اتمی
    except Exception as ex:
        # دیسکِ پر یا فقط-خواندنی: کش اختیاری است، ولی دیگر تلاش نمی‌کنیم
        _CACHE_OK = False
        log.info("کشِ آفلاین خاموش شد: %s", ex)


def cache_get(key: str):
    try:
        with open(_cache_file(key), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def cache_clear() -> None:
    """خروج از حساب ⇒ کشِ آفلاین هم برود (داده‌ی کاربرِ قبلی نماند)."""
    try:
        d = _cache_dir()
        for n in os.listdir(d):
            if n.endswith(".json"):
                try:
                    os.remove(os.path.join(d, n))
                except OSError:
                    pass
    except Exception:
        pass


_MIME = {
    ".png": "image/png", ".webp": "image/webp", ".gif": "image/gif",
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".bmp": "image/bmp",
    ".mp4": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime",
    ".mp3": "audio/mpeg", ".ogg": "audio/ogg", ".wav": "audio/wav",
    ".m4a": "audio/mp4", ".pdf": "application/pdf", ".txt": "text/plain",
    ".zip": "application/zip",
}


def mime_for(filename: str) -> str:
    """نوعِ فایل از پسوند؛ ناشناس ⇒ octet-stream (هر فایلی مجازه)."""
    ext = ("." + filename.rsplit(".", 1)[-1]).lower() if "." in (filename or "") else ""
    return _MIME.get(ext, "application/octet-stream")


def _device_name() -> str:
    """اسمِ دستگاه برای «دستگاه‌های فعال».

    هدرِ HTTP فقط ASCII قبول می‌کنه (latin-1)، پس هر چیزِ غیرِ لاتین حذف می‌شه.
    """
    try:
        raw = f"Rafigram Desktop ({platform.system()} {platform.release()})"
    except Exception:
        raw = "Rafigram Desktop"
    clean = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode()
    return (clean.strip() or "Rafigram Desktop")[:200]


DEVICE = _device_name()


class APIClient:
    def __init__(self):
        self._token: str | None = None
        self._user_id: str | None = None
        self._username: str | None = None
        self._display_name: str | None = None
        self._stale = False          # آخرین پاسخِ خواندنی از دیسک آمد؟
        self.ban_info: dict | None = None   # بدنه‌ی ۴۰۳ِ «بن» (صفحه‌ی شمارش)
        self._client = httpx.AsyncClient(
            base_url=config.api_base(), timeout=15
        )

    def rebase(self) -> str:
        """آدرسِ سرور عوض شده ⇒ کلاینتِ HTTP را با پایه‌ی تازه از نو بساز.

        `httpx.AsyncClient.base_url` بعد از ساخت قابلِ اتکا نیست، پس نمونه‌ی
        تازه می‌سازیم و قبلی را (اگر حلقه‌ی رویداد باشد) پشتِ صحنه می‌بندیم.
        """
        old, self._client = self._client, httpx.AsyncClient(
            base_url=config.api_base(), timeout=15)
        try:
            asyncio.get_running_loop().create_task(old.aclose())
        except Exception:
            pass
        return config.api_base()

    async def health(self, base: str | None = None) -> bool:
        """آدرسِ سرور را می‌آزماید؛ کلاینتِ اصلی دست نمی‌خورد."""
        url = config.normalize(base or config.api_base()) + "/health"
        async with httpx.AsyncClient(timeout=8, follow_redirects=True) as c:
            r = await c.get(url)
        return r.status_code == 200

    @property
    def token(self) -> str | None:
        return self._token

    @property
    def user_id(self) -> str | None:
        return self._user_id

    @property
    def display_name(self) -> str | None:
        return self._display_name

    @property
    def username(self) -> str | None:
        return self._username

    @property
    def is_logged_in(self) -> bool:
        return self._token is not None

    def _headers(self) -> dict:
        h = {"X-Device": DEVICE}
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        return h

    # ── Auth ────────────────────────────────────────
    async def register(
        self, username: str, display_name: str, password: str, reg_code: str = ""
    ) -> dict:
        body = {
            "username": username,
            "display_name": display_name,
            "password": password,
        }
        if reg_code:
            # سرورِ تازه `referral_code` می‌خواهد (کدِ یک‌بارمصرفِ ربات یا کدِ
            # مدیر) و سرورِ قدیمی `registration_code`؛ هر دو را می‌فرستیم تا
            # با هر نسخه‌ای کار کند.
            body["registration_code"] = reg_code
            body["referral_code"] = reg_code
        r = await self._client.post("/auth/register", json=body,
                                    headers={"X-Device": DEVICE})
        r.raise_for_status()
        data = r.json()
        self._set_auth(data)
        return data

    async def referral_info(self) -> dict:
        """آیا ثبت‌نام کدِ دعوت لازم دارد و ربات کجاست؟ (بی‌نیاز به ورود)"""
        try:
            r = await self._client.get("/referral/info")
            r.raise_for_status()
            return r.json() or {}
        except Exception:
            return {}

    async def referral_request(self) -> dict:
        """«دریافت کد»ِ دستی: سرور کدِ تازه می‌سازد و به تلگرامِ *مدیر* می‌فرستد.

        خودِ کد در پاسخ نمی‌آید (وگرنه دعوتی‌بودن بی‌معنی می‌شد)؛ فقط
        می‌گوید فرستاده شد یا چرا نشد. مدیر بعد دستی به رفیقش می‌رساندش.
        """
        r = await self._client.post("/referral/request", json={})
        r.raise_for_status()
        return r.json() or {}

    async def referral_otp(self, tg_username: str) -> dict:
        """«کدِ خودکار»: کد یک‌راست به تلگرامِ *خودِ* کاربر می‌رود.

        شرطش این است که کاربر پیش‌تر به ربات `/start` داده باشد — تلگرام
        اجازه نمی‌دهد ربات به غریبه پیام بدهد. اگر نشناسدش، سرور ۴۰۴ با
        متنِ راهنما می‌دهد (کلاینت همان را نشان می‌دهد).
        """
        r = await self._client.post("/referral/otp",
                                    json={"username": tg_username})
        r.raise_for_status()
        return r.json() or {}

    async def referral_check(self, code: str) -> bool:
        """درستیِ کد را بی‌مصرف‌کردنش می‌پرسد (بازخوردِ فوریِ فرم)."""
        try:
            r = await self._client.post("/referral/check", json={"code": code})
            r.raise_for_status()
            return bool((r.json() or {}).get("ok"))
        except Exception:
            return False

    async def login(self, username: str, password: str) -> dict:
        r = await self._client.post(
            "/auth/login", json={"username": username, "password": password},
            headers={"X-Device": DEVICE},
        )
        r.raise_for_status()
        data = r.json()
        self._set_auth(data)
        return data

    def _set_auth(self, data: dict):
        self._token = data["token"]
        self._user_id = data["user_id"]
        self._username = data["username"]
        self._display_name = data["display_name"]

    def adopt(self, session: dict) -> bool:
        """نشستِ ذخیره‌شده را سوار می‌کند (بی هیچ رفت‌وآمدِ شبکه‌ای).

        برای ورودِ خودکار در شروعِ برنامه‌ست: توکن روی کلاینت می‌نشیند و
        بعد با `verify()` می‌سنجیم که سرور هنوز قبولش دارد یا نه.
        """
        token = (session or {}).get("token") or ""
        if not token:
            return False
        self._token = token
        self._user_id = (session.get("user_id") or "") or None
        self._username = (session.get("username") or "") or None
        self._display_name = (session.get("display_name") or "") or None
        return True

    async def verify(self) -> dict | None:
        """توکنِ جاری هنوز معتبر است؟ پروفایل را برمی‌گرداند، وگرنه None.

        هر پاسخِ ۴۰۱/۴۰۳ یعنی نشست باطل شده (رمز عوض شده یا دستگاه
        باطل شده) ⇒ باید پاک شود. خطای شبکه اما نشست را باطل نمی‌کند.
        ۴۰۳ِ «بن» بدنه‌اش را در `self.ban_info` می‌گذارد تا رابط بتواند
        صفحه‌ی «بن شدی» با شمارش معکوس بسازد.
        """
        self.ban_info = None
        if not self._token:
            return None
        try:
            me = await self.get_my_profile()
        except httpx.HTTPStatusError as ex:
            if ex.response.status_code in (401, 403):
                try:
                    det = ex.response.json().get("detail")
                except Exception:
                    det = None
                if (ex.response.status_code == 403
                        and isinstance(det, dict) and det.get("code") == "banned"):
                    self.ban_info = det
                self.logout()
            return None
        except Exception:
            return None
        self._user_id = me.get("user_id") or self._user_id
        self._username = me.get("username") or self._username
        self._display_name = me.get("display_name") or self._display_name
        return me

    def logout(self):
        self._token = None
        self._user_id = None
        self._username = None
        self._display_name = None
        cache_clear()          # داده‌ی حسابِ قبلی روی دیسک نماند

    # ── کشِ عبوری ───────────────────────────────────
    # `stale` می‌گوید پاسخِ آخر از دیسک آمد یا از شبکه؛ لایه‌ی رابط می‌تواند
    # نشانِ «آفلاین» را از همین بخواند بی اینکه خودش شبکه را بسنجد.
    @property
    def stale(self) -> bool:
        return bool(self._stale)

    def _ckey(self, name: str, *parts) -> str:
        """کلیدِ کش — همیشه شاملِ کاربر، تا حسابِ دیگری داده‌ی این را نبیند."""
        who = self._user_id or self._username or "anon"
        return "|".join([str(who), name, *[str(p) for p in parts]])

    async def _cached(self, key: str, call, on_empty=None):
        """شبکه اول؛ نشد، دیسک. هیچ‌کدام نبود، خطا را بالا می‌دهیم.

        هدف این است که رابط هرگز به‌خاطرِ شبکه خالی یا قفل نماند. پاسخِ موفق
        همیشه روی دیسک تازه می‌شود، پس «آخرین حالتِ خوب» همیشه در دسترس است.
        فقط خطای شبکه/تایم‌اوت به دیسک پناه می‌برد؛ ۴۰۱ و ۴۰۳ نه — آن‌ها یعنی
        نشست باطل شده و نشان‌دادنِ داده‌ی کهنه گمراه‌کننده است.
        """
        try:
            data = await call()
        except httpx.HTTPStatusError as ex:
            if ex.response.status_code in (401, 403):
                raise
            got = cache_get(key)
            if got is not None:
                self._stale = True
                return got
            raise
        except Exception:
            got = cache_get(key)
            if got is not None:
                self._stale = True
                return got
            if on_empty is not None:
                self._stale = True
                return on_empty
            raise
        self._stale = False
        cache_put(key, data)
        return data

    # ── Chat ────────────────────────────────────────
    async def list_chats(self, archived: bool = False) -> list[dict]:
        async def go():
            r = await self._client.get(
                "/chat/list",
                params={"archived": str(bool(archived)).lower()},
                headers=self._headers())
            r.raise_for_status()
            return r.json()

        return await self._cached(self._ckey("chats", archived), go)

    async def archived_count(self) -> int:
        async def go():
            r = await self._client.get("/chat/archived_count",
                                       headers=self._headers())
            r.raise_for_status()
            return int(r.json().get("count") or 0)

        # صفر یک پیش‌فرضِ بی‌خطر است: پوشه‌ی بایگانی پنهان می‌ماند، نه اینکه
        # با عددِ اشتباه ظاهر شود.
        return int(await self._cached(self._ckey("arcnt"), go, on_empty=0) or 0)

    async def set_archived(self, chat_id: str, archived: bool = True) -> dict:
        r = await self._client.post(f"/chat/{chat_id}/archive",
                                    json={"archived": bool(archived)},
                                    headers=self._headers())
        r.raise_for_status()
        return r.json()

    async def search_all(self, q: str) -> list[dict]:
        """جست‌وجوی سراسری: کاربر، گروه/کانالِ عمومی، یا @شناسه."""
        r = await self._client.get("/chat/search", params={"q": q},
                                   headers=self._headers())
        r.raise_for_status()
        return r.json()

    async def join_chat(self, handle: str | None = None,
                        chat_id: str | None = None) -> dict:
        payload = {}
        if handle:
            payload["handle"] = handle
        if chat_id:
            payload["chat_id"] = chat_id
        r = await self._client.post("/chat/join", json=payload,
                                    headers=self._headers())
        r.raise_for_status()
        return r.json()

    async def leave_chat(self, chat_id: str) -> dict:
        r = await self._client.post(f"/chat/{chat_id}/leave", headers=self._headers())
        r.raise_for_status()
        return r.json()

    async def clear_chat_history(self, chat_id: str) -> dict:
        r = await self._client.post(f"/chat/{chat_id}/clear", headers=self._headers())
        r.raise_for_status()
        return r.json()

    async def report_content(self, target_type: str, target_id: str, reason: str, details: str = "") -> dict:
        payload = {"target_type": target_type, "target_id": target_id, "reason": reason, "details": details}
        r = await self._client.post("/chat/report", json=payload, headers=self._headers())
        r.raise_for_status()
        return r.json()

    async def create_chat(self, username: str) -> dict:
        r = await self._client.post(
            "/chat/create", json={"username": username}, headers=self._headers()
        )
        r.raise_for_status()
        return r.json()

    async def create_group(self, title: str, members: list[str], bio: str = "",
                           avatar_url: str = "", kind: str = "group",
                           is_public: bool = False, handle: str = "") -> dict:
        payload = {"title": title, "members": members,
                   "kind": kind, "is_public": bool(is_public)}
        if bio: payload["bio"] = bio
        if avatar_url: payload["avatar_url"] = avatar_url
        if handle: payload["handle"] = handle.strip().lstrip("@")
        r = await self._client.post(
            "/chat/create_group", json=payload, headers=self._headers(),
        )
        r.raise_for_status()
        return r.json()

    async def chat_profile(self, chat_id: str) -> dict:
        r = await self._client.get(f"/chat/{chat_id}/profile", headers=self._headers())
        r.raise_for_status()
        return r.json()

    async def update_chat_profile(self, chat_id: str, title: str = None,
                                  bio: str = None, avatar_url: str = None,
                                  is_public: bool | None = None,
                                  handle: str | None = None) -> dict:
        payload = {}
        if title is not None: payload["title"] = title
        if bio is not None: payload["bio"] = bio
        if avatar_url is not None: payload["avatar_url"] = avatar_url
        if is_public is not None: payload["is_public"] = bool(is_public)
        if handle is not None: payload["handle"] = handle.strip().lstrip("@")
        r = await self._client.put(f"/chat/{chat_id}/profile", json=payload, headers=self._headers())
        r.raise_for_status()
        return r.json()

    async def set_role(self, chat_id: str, username: str, role: str = "admin") -> dict:
        """ارتقا/تنزیل/واگذاریِ مالکیت — role: owner | admin | member."""
        r = await self._client.post(f"/chat/{chat_id}/role",
                                    json={"username": username, "role": role},
                                    headers=self._headers())
        r.raise_for_status()
        return r.json()

    async def set_banned(self, chat_id: str, username: str, banned: bool = True) -> dict:
        r = await self._client.post(f"/chat/{chat_id}/ban",
                                    json={"username": username,
                                          "banned": bool(banned)},
                                    headers=self._headers())
        r.raise_for_status()
        return r.json()

    async def add_member(self, chat_id: str, username: str) -> dict:
        r = await self._client.post(f"/chat/{chat_id}/add_member?username={username}", headers=self._headers())
        r.raise_for_status()
        return r.json()

    async def remove_member(self, chat_id: str, username: str) -> dict:
        r = await self._client.delete(f"/chat/{chat_id}/remove_member?username={username}", headers=self._headers())
        r.raise_for_status()
        return r.json()

    async def get_messages(
        self, chat_id: str, limit: int = 50, before: str | None = None
    ) -> list[dict]:
        async def go():
            params = {"limit": limit}
            if before:
                params["before"] = before
            r = await self._client.get(
                f"/chat/{chat_id}/messages", params=params,
                headers=self._headers()
            )
            r.raise_for_status()
            return r.json()

        if before:
            # صفحه‌های عقب‌تر کش نمی‌شوند: کلیدشان به `before` بند است و
            # هیچ‌وقت دوباره با همان کلید خواسته نمی‌شوند ⇒ فقط دیسک را
            # پر می‌کردند. صفحه‌ی اول همان چیزی است که کاربر آفلاین می‌بیند.
            return await go()
        return await self._cached(self._ckey("msgs", chat_id, limit), go)

    async def send_message(
        self, chat_id: str, content: str, msg_type: str = "text",
        reply_to: str | None = None, enc: bool = False,
        caption: str | None = None, file_name: str | None = None
    ) -> dict:
        payload = {"content": content, "msg_type": msg_type, "enc": bool(enc)}
        if reply_to:
            payload["reply_to"] = reply_to
        if caption:
            payload["caption"] = caption
        if file_name:
            payload["file_name"] = file_name
        r = await self._client.post(
            f"/chat/{chat_id}/send",
            json=payload,
            headers=self._headers(),
        )
        r.raise_for_status()
        return r.json()

    # ── گیف‌های ذخیره‌شده ─────────────────────────────
    async def list_gifs(self) -> list[str]:
        async def go():
            r = await self._client.get("/gifs", headers=self._headers())
            r.raise_for_status()
            return (r.json() or {}).get("urls") or []

        return await self._cached(self._ckey("gifs"), go, on_empty=[])

    async def add_gif(self, url: str) -> dict:
        """{"urls": [...], "added": bool} — `added=False` یعنی از قبل بود."""
        r = await self._client.post("/gifs/add", json={"url": url},
                                    headers=self._headers())
        r.raise_for_status()
        d = r.json() or {}
        return {"urls": d.get("urls") or [], "added": bool(d.get("added"))}

    async def remove_gif(self, url: str) -> list[str]:
        r = await self._client.delete("/gifs", params={"url": url},
                                      headers=self._headers())
        r.raise_for_status()
        return (r.json() or {}).get("urls") or []

    # ── عملیاتِ پیام ─────────────────────────────────
    async def edit_message(self, chat_id: str, mid: str, content: str,
                           enc: bool = False) -> dict:
        r = await self._client.put(f"/chat/{chat_id}/message/{mid}",
                                   json={"content": content, "enc": bool(enc)},
                                   headers=self._headers())
        r.raise_for_status()
        return r.json()

    async def delete_message(self, chat_id: str, mid: str,
                             for_everyone: bool = False) -> dict:
        r = await self._client.delete(
            f"/chat/{chat_id}/message/{mid}",
            params={"for_everyone": "true" if for_everyone else "false"},
            headers=self._headers())
        r.raise_for_status()
        return r.json()

    async def pin_message(self, chat_id: str, mid: str, pinned: bool = True) -> dict:
        r = await self._client.post(f"/chat/{chat_id}/message/{mid}/pin",
                                    json={"pinned": bool(pinned)},
                                    headers=self._headers())
        r.raise_for_status()
        return r.json()

    async def get_pinned(self, chat_id: str) -> dict | None:
        r = await self._client.get(f"/chat/{chat_id}/pinned", headers=self._headers())
        r.raise_for_status()
        return r.json()

    async def react(self, chat_id: str, mid: str, emoji: str) -> dict:
        """واکنش رو اضافه/برداشته می‌کنه (toggle)."""
        r = await self._client.post(f"/chat/{chat_id}/message/{mid}/react",
                                    json={"emoji": emoji},
                                    headers=self._headers())
        r.raise_for_status()
        return r.json()

    async def forward_message(self, chat_id: str, mid: str, to_chat_id: str) -> dict:
        r = await self._client.post(f"/chat/{chat_id}/message/{mid}/forward",
                                    json={"to_chat_id": to_chat_id},
                                    headers=self._headers())
        r.raise_for_status()
        return r.json()

    # ── پیام‌های ذخیره‌شده ────────────────────────────
    async def saved_chat(self) -> dict:
        """شناسه‌ی «پیام‌های ذخیره‌شده»ی من — سرور نبودش را می‌سازد."""
        r = await self._client.get("/chat/saved", headers=self._headers())
        r.raise_for_status()
        return r.json()

    async def save_message(self, chat_id: str, mid: str) -> dict:
        """یک نسخه‌ی مستقل از این پیام در دفترچه‌ی یادداشتِ خودم.

        `{"chat_id": ..., "message": {...}}` برمی‌گردد؛ `chat_id` مقصد است
        نه مبدأ، پس می‌شود بی درخواستِ تازه «برو ببین» را پیشنهاد داد.
        """
        r = await self._client.post(f"/chat/{chat_id}/message/{mid}/save",
                                    headers=self._headers())
        r.raise_for_status()
        return r.json()

    async def mark_read(self, chat_id: str) -> dict:
        """این چت خونده شد — شمارنده‌ی نخونده‌ها صفر می‌شه."""
        r = await self._client.post(f"/chat/{chat_id}/read", headers=self._headers())
        r.raise_for_status()
        return r.json()

    # ── Profile ──────────────────────────────────────
    async def get_my_profile(self) -> dict:
        async def go():
            r = await self._client.get("/profile/me", headers=self._headers())
            r.raise_for_status()
            return r.json()

        # کش‌شدنِ این یکی معنیِ بزرگی دارد: `verify()` روی همین سوار است، پس
        # با نبودِ شبکه برنامه *بیرونت نمی‌اندازد* و آفلاین باز می‌شود. ۴۰۱ و
        # ۴۰۳ از کش رد نمی‌شوند، پس نشستِ باطل هم واقعاً باطل می‌ماند.
        return await self._cached(self._ckey("me"), go)

    async def get_profile(self, username: str) -> dict:
        r = await self._client.get(f"/profile/{username}", headers=self._headers())
        r.raise_for_status()
        return r.json()

    async def update_profile(self, display_name: str | None = None, bio: str | None = None, avatar_url: str | None = None, birthday: str | None = None) -> dict:
        payload = {}
        if display_name is not None:
            payload["display_name"] = display_name
        if bio is not None:
            payload["bio"] = bio
        if avatar_url is not None:
            payload["avatar_url"] = avatar_url
        if birthday is not None:
            payload["birthday"] = birthday
        r = await self._client.put("/profile/me", json=payload, headers=self._headers())
        r.raise_for_status()
        if display_name is not None:
            self._display_name = display_name
        return r.json()

    async def upload_avatar(self, file_path: str) -> dict:
        """آپلود عکس پروفایل از روی مسیرِ فایل.

        خواندنِ فایل در تردِ جدا انجام می‌شود تا حلقه‌ی رویدادِ UI قفل نشود
        (با `open()` مستقیم، httpx بدنه را همان‌جا و همگام می‌خواند).
        """
        def _read():
            with open(file_path, "rb") as f:
                return f.read()

        data = await asyncio.to_thread(_read)
        name = file_path.replace("\\", "/").rsplit("/", 1)[-1]
        return await self.upload_avatar_bytes(name, data)

    async def upload_avatar_bytes(self, filename: str, data: bytes) -> dict:
        """آپلود عکس پروفایل از بایت‌های خام (خروجی FilePicker)."""
        ext = (filename.rsplit(".", 1)[-1] or "jpg").lower()
        mime = {
            "png": "image/png",
            "webp": "image/webp",
            "gif": "image/gif",
        }.get(ext, "image/jpeg")
        r = await self._client.post(
            "/profile/me/avatar",
            files={"file": (filename, data, mime)},
            headers=self._headers(),
        )
        r.raise_for_status()
        return r.json()

    # ── گالریِ عکسِ پروفایل (حداکثر ۵) ────────────────
    async def my_photos(self) -> dict:
        """{"photos":[{id,url}], "urls":[...], "max":5}"""
        r = await self._client.get("/profile/me/photos", headers=self._headers())
        r.raise_for_status()
        return r.json()

    async def add_photo_bytes(self, filename: str, data: bytes) -> dict:
        """عکسِ تازه به گالری اضافه می‌کنه و همون عکسِ اصلی می‌شه."""
        ext = (filename.rsplit(".", 1)[-1] or "jpg").lower()
        mime = {"png": "image/png", "webp": "image/webp",
                "gif": "image/gif"}.get(ext, "image/jpeg")
        r = await self._client.post(
            "/profile/me/photos",
            files={"file": (filename, data, mime)},
            headers=self._headers(), timeout=120,
        )
        r.raise_for_status()
        return r.json()

    async def delete_photo(self, url: str) -> dict:
        r = await self._client.delete("/profile/me/photos", params={"url": url},
                                      headers=self._headers())
        r.raise_for_status()
        return r.json()

    async def set_main_photo(self, url: str) -> dict:
        r = await self._client.put("/profile/me/photos/main", json={"url": url},
                                   headers=self._headers())
        r.raise_for_status()
        return r.json()

    async def change_password(self, old_password: str, new_password: str) -> dict:
        r = await self._client.put(
            "/profile/me/password",
            json={"old_password": old_password, "new_password": new_password},
            headers=self._headers(),
        )
        r.raise_for_status()
        return r.json()

    async def change_username(self, username: str) -> dict:
        r = await self._client.put(
            "/profile/me/username", json={"username": username},
            headers=self._headers(),
        )
        r.raise_for_status()
        data = r.json()
        self._username = data.get("username") or self._username
        return data

    async def upload_media_bytes(self, filename: str, data: bytes,
                                 on_progress=None) -> dict:
        """آپلود فایل عمومی (عکس گروه، پیوست پیام) → {"url", "name", "size"}.

        با دادنِ `on_progress(sent, total)` بدنه تکه‌تکه فرستاده می‌شه تا
        نوارِ پیشرفت پر شه.
        """
        mime = mime_for(filename)
        if on_progress is None:
            r = await self._client.post(
                "/chat/upload",
                files={"file": (filename, data, mime)},
                headers=self._headers(),
                timeout=300,
            )
            r.raise_for_status()
            return r.json()

        boundary = "----rafigram" + uuid.uuid4().hex
        safe = (filename or "file").replace('"', "_").replace("\r", "").replace("\n", "")
        pre = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{safe}"\r\n'
            f"Content-Type: {mime}\r\n\r\n"
        ).encode("utf-8")
        post = f"\r\n--{boundary}--\r\n".encode("utf-8")
        total = len(data)

        async def body():
            yield pre
            for i in range(0, total, _CHUNK):
                part = data[i:i + _CHUNK]
                yield part
                try:
                    on_progress(min(i + len(part), total), total)
                except Exception:
                    pass
                await asyncio.sleep(0)     # نفس‌کشیدنِ حلقه‌ی UI
            yield post

        h = self._headers()
        h["Content-Type"] = f"multipart/form-data; boundary={boundary}"
        h["Content-Length"] = str(len(pre) + total + len(post))
        r = await self._client.post("/chat/upload", content=body(),
                                    headers=h, timeout=300)
        r.raise_for_status()
        return r.json()

    async def download_to(self, url: str, dest_path: str, on_progress=None) -> str:
        """فایل رو تکه‌تکه روی دیسک می‌ریزه و مسیرش رو برمی‌گردونه."""
        got = 0
        async with self._client.stream("GET", url, headers=self._headers(),
                                       timeout=300) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length") or 0)
            with open(dest_path, "wb") as f:
                async for chunk in r.aiter_bytes(_CHUNK):
                    await asyncio.to_thread(f.write, chunk)
                    got += len(chunk)
                    if on_progress:
                        try:
                            on_progress(got, total)
                        except Exception:
                            pass
        return dest_path

    # ── به‌روزرسانی ──────────────────────────────────
    async def latest_version(self) -> dict:
        """آگاهی‌نامه‌ی نسخه — بی توکن هم جواب می‌دهد."""
        r = await self._client.get("/updates/latest", timeout=12)
        r.raise_for_status()
        return r.json()

    async def download_url(self, url: str, dest_path: str, on_progress=None) -> str:
        """دانلودِ آدرسِ بیرونی — بی هیچ سرصفحه‌ی احرازِ هویت.

        فایلِ به‌روزرسانی ممکن است روی GitHub باشد؛ توکنِ ما نباید به
        میزبانِ غریبه فرستاده شود، پس کلاینتِ تازه‌ی بی‌سرصفحه می‌سازیم.
        """
        got = 0
        async with httpx.AsyncClient(timeout=None,
                                     follow_redirects=True) as c:
            async with c.stream("GET", url) as r:
                r.raise_for_status()
                total = int(r.headers.get("content-length") or 0)
                with open(dest_path, "wb") as f:
                    async for chunk in r.aiter_bytes(_CHUNK):
                        # نوشتنِ دیسک در نخِ جدا ⇒ رابط کاربری نمی‌لرزد
                        await asyncio.to_thread(f.write, chunk)
                        got += len(chunk)
                        if on_progress:
                            try:
                                on_progress(got, total)
                            except Exception:
                                pass
        return dest_path

    # ── حریم خصوصی و امنیت ───────────────────────────
    async def list_sessions(self) -> list[dict]:
        """دستگاه‌های فعال (هر ورود یک ردیف)."""
        r = await self._client.get("/privacy/sessions", headers=self._headers())
        r.raise_for_status()
        return r.json()

    async def revoke_session(self, session_id: str) -> dict:
        r = await self._client.delete(f"/privacy/sessions/{session_id}",
                                     headers=self._headers())
        r.raise_for_status()
        return r.json()

    async def revoke_other_sessions(self) -> dict:
        r = await self._client.post("/privacy/sessions/revoke_others",
                                    headers=self._headers())
        r.raise_for_status()
        return r.json()

    async def list_blocks(self) -> list[dict]:
        r = await self._client.get("/privacy/blocks", headers=self._headers())
        r.raise_for_status()
        return r.json()

    async def block_user(self, username: str) -> dict:
        r = await self._client.post("/privacy/blocks", json={"username": username},
                                    headers=self._headers())
        r.raise_for_status()
        return r.json()

    async def unblock_user(self, username: str) -> dict:
        r = await self._client.delete(f"/privacy/blocks/{username}",
                                      headers=self._headers())
        r.raise_for_status()
        return r.json()

    async def get_privacy(self) -> dict:
        r = await self._client.get("/privacy/settings", headers=self._headers())
        r.raise_for_status()
        return r.json()

    async def set_privacy(self, show_last_seen: bool) -> dict:
        r = await self._client.put("/privacy/settings",
                                   json={"show_last_seen": bool(show_last_seen)},
                                   headers=self._headers())
        r.raise_for_status()
        return r.json()

    # ── استوری (۲۴ساعته) ─────────────────────────────
    async def story_feed(self) -> list[dict]:
        """حلقه‌های استوریِ نوارِ بالای صفحه‌ی چت."""
        async def go():
            r = await self._client.get("/stories/feed",
                                       headers=self._headers())
            r.raise_for_status()
            return r.json()

        return await self._cached(self._ckey("sfeed"), go, on_empty=[])

    async def stories_of(self, owner_kind: str, owner_id: str) -> list[dict]:
        r = await self._client.get(f"/stories/of/{owner_kind}/{owner_id}",
                                   headers=self._headers())
        r.raise_for_status()
        return r.json()

    async def my_stories(self) -> list[dict]:
        r = await self._client.get("/stories/mine", headers=self._headers())
        r.raise_for_status()
        return r.json()

    async def add_story(self, media_url: str, media_type: str = "image",
                        caption: str = "", owner_kind: str = "user",
                        owner_id: str | None = None) -> dict:
        body = {"media_url": media_url, "media_type": media_type,
                "owner_kind": owner_kind}
        if caption:
            body["caption"] = caption
        if owner_id:
            body["owner_id"] = owner_id
        r = await self._client.post("/stories", json=body,
                                    headers=self._headers())
        r.raise_for_status()
        return r.json()

    async def story_seen(self, story_id: str) -> dict:
        r = await self._client.post(f"/stories/{story_id}/seen",
                                    headers=self._headers())
        r.raise_for_status()
        return r.json()

    async def story_viewers(self, story_id: str) -> list[dict]:
        r = await self._client.get(f"/stories/{story_id}/viewers",
                                   headers=self._headers())
        r.raise_for_status()
        return r.json()

    async def delete_story(self, story_id: str) -> dict:
        r = await self._client.delete(f"/stories/{story_id}",
                                      headers=self._headers())
        r.raise_for_status()
        return r.json()

    async def like_story(self, story_id: str) -> dict:
        r = await self._client.post(f"/stories/{story_id}/like",
                                    headers=self._headers())
        r.raise_for_status()
        return r.json()

    # ── پست‌های پروفایل ───────────────────────────────
    async def my_posts(self, archived: bool = False) -> list[dict]:
        r = await self._client.get("/profile/me/posts", params={"archived": archived}, headers=self._headers())
        r.raise_for_status()
        return r.json()

    async def add_post(self, media_url: str, media_type: str = "image", caption: str = "") -> dict:
        payload = {"media_url": media_url, "media_type": media_type, "caption": caption}
        r = await self._client.post("/profile/me/posts", json=payload, headers=self._headers())
        r.raise_for_status()
        return r.json()

    async def toggle_archive_post(self, post_id: str) -> dict:
        r = await self._client.post(f"/profile/me/posts/{post_id}/archive", headers=self._headers())
        r.raise_for_status()
        return r.json()

    async def delete_post(self, post_id: str) -> dict:
        r = await self._client.delete(f"/profile/me/posts/{post_id}", headers=self._headers())
        r.raise_for_status()
        return r.json()

    # ── اعلان ────────────────────────────────────────
    async def push_test(self) -> dict:
        """سرور یک اعلانِ آزمایشی برای همین کاربر می‌فرستد.

        دکمه‌ی «تستِ اعلان» در تنظیمات همین را صدا می‌زند: هم مسیرِ سرور
        سنجیده می‌شود، هم سرویسِ بومیِ گوشی، بی اینکه لازم باشد کسی
        واقعاً به ما پیام بدهد.
        """
        r = await self._client.post("/push/test", headers=self._headers())
        r.raise_for_status()
        return r.json()


# singleton
api = APIClient()
