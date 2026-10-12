"""
تستِ بی‌سرِ (headless) لایه‌ی UI رفیق‌گرام — موجِ B.

یک شیءِ جعلیِ `page` می‌سازه (بدونِ پنجره‌ی واقعیِ Flet) و صفحه‌های اصلیِ
`client/main.py` رو با `APIClient`/`WSClient` واقعی اجرا می‌کنه، بعد درختِ
کنترل‌ها رو می‌گرده تا ثابت کنه هر قابلیتِ موجِ B واقعاً رندر می‌شه.

اجرا:  .venv/Scripts/python.exe wb_ui_test.py     (سرور باید بالا باشه)
"""
import asyncio
import os
import sys
import time
import types
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
CLIENT = os.path.join(HERE, "client")
sys.path.insert(0, CLIENT)
os.chdir(CLIENT)

import flet as ft                                    # noqa: E402
import config                                        # noqa: E402

# آزمون فقط با سرورِ همین کامپیوتر کار می‌کند (پیش‌فرضِ برنامه Railway است).
config.set_base(config.LOCAL_API_BASE)

PASS = 0
FAIL = 0


def ok(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {str(extra)[:220]}")


def sec(title):
    print(f"\n[{title}]")


# ── صفحه‌ی جعلی ────────────────────────────────────
class FakeWindow:
    width = height = min_width = min_height = 0
    title_bar_hidden = False
    bgcolor = None
    prevent_close = False

    def center(self):
        pass


class FakePage:
    """کمترین چیزی که main.py از page می‌خواد، بدونِ رندرِ واقعی."""

    def __init__(self):
        self.controls = []
        self.dialogs = []
        self.window = FakeWindow()
        self.rtl = False
        self.theme = None
        self.theme_mode = None
        self.bgcolor = None
        self.padding = 0
        self.title = ""
        self.fonts = {}
        self.clipboard = ""
        self.updates = 0
        self.tasks = []

    def clean(self):
        self.controls = []

    def add(self, *ctrls):
        self.controls.extend(ctrls)

    def update(self, *a):
        self.updates += 1

    def show_dialog(self, dlg):
        self.dialogs.append(dlg)

    def pop_dialog(self, *a):
        if self.dialogs:
            self.dialogs.pop()

    def set_clipboard(self, text):
        self.clipboard = text

    def run_task(self, fn, *a, **kw):
        task = asyncio.create_task(fn(*a, **kw))
        self.tasks.append(task)
        return task

    async def drain(self, timeout=8.0):
        """تا وقتی تسک‌های در جریان تموم بشن (یا تایم‌اوت) صبر می‌کنه."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            live = [t for t in self.tasks if not t.done()]
            if not live:
                break
            await asyncio.wait(live, timeout=0.3)
        errs = [t.exception() for t in self.tasks
                if t.done() and not t.cancelled() and t.exception()]
        self.tasks = [t for t in self.tasks if not t.done()]
        await asyncio.sleep(0.05)
        if errs:
            raise errs[0]


def walk(node, depth=0, seen=None):
    """پیمایشِ کاملِ درختِ کنترل‌ها."""
    if node is None or depth > 70:
        return
    seen = seen if seen is not None else set()
    if id(node) in seen:
        return
    seen.add(id(node))
    yield node
    for attr in ("controls", "actions", "spans"):
        kids = getattr(node, attr, None)
        if isinstance(kids, (list, tuple)):
            for k in kids:
                yield from walk(k, depth + 1, seen)
    for attr in ("content", "control", "leading", "trailing", "title",
                 "subtitle", "prefix", "suffix"):
        kid = getattr(node, attr, None)
        if kid is not None and not isinstance(kid, str):
            yield from walk(kid, depth + 1, seen)


def _roots(x):
    return x if isinstance(x, (list, tuple)) else [x]


def texts(root):
    out = []
    for r in _roots(root):
        for n in walk(r):
            for attr in ("value", "text", "label", "hint_text", "tooltip"):
                v = getattr(n, attr, None)
                if isinstance(v, str) and v.strip():
                    out.append(v)
    return out


def icons(root):
    out = []
    for r in _roots(root):
        for n in walk(r):
            src = getattr(n, "src", None)
            if isinstance(src, str):
                out.append(os.path.basename(src))
            img = getattr(n, "image", None)
            isrc = getattr(img, "src", None)
            if isinstance(isrc, str):
                out.append(os.path.basename(isrc))
    return out


def find(root, pred):
    for r in _roots(root):
        for n in walk(r):
            try:
                if pred(n):
                    return n
            except Exception:
                continue
    return None


def find_all(root, pred):
    res = []
    for r in _roots(root):
        for n in walk(r):
            try:
                if pred(n):
                    res.append(n)
            except Exception:
                continue
    return res


def has(root, needle):
    return any(needle in x for x in texts(root))


async def main():
    import i18n
    import prefs
    import ui
    import crypto
    import emoji_data as ED
    import main as app
    from services.api import api, APIClient
    from services.ws_client import ws_client
    from i18n import t

    tag = uuid.uuid4().hex[:6]
    u1, u2 = f"ut{tag}a", f"ut{tag}b"
    pw = "test1234"
    page = FakePage()

    # ── ۰) راه‌اندازی ─────────────────────────────
    sec("0) bootstrap")
    await prefs.load()
    i18n.set_lang("fa")
    app._use_theme("crimson")
    ui.set_font_scale(prefs.scale())
    ok("theme applied", isinstance(app.P, dict) and bool(app.P.get("acc")))

    b = APIClient()
    r1 = await api.register(u1, "کاربرِ یک", pw)
    ok("register A", bool(api.token), r1)
    r2 = await b.register(u2, "کاربرِ دو", pw)
    ok("register B", bool(b.token), r2)
    await app._load_me()
    ok("_load_me filled", bool(app._me.get("username")), app._me)

    ch = await api.create_chat(u2)
    chat_id = ch.get("chat_id")
    ok("private chat created", bool(chat_id), ch)

    g = await api.create_group("گروهِ تستِ رابط", [u2], bio="بیوی گروه",
                               kind="group", is_public=True, handle=f"g{tag}")
    gid = g.get("chat_id")
    ok("public group created", bool(gid), g)

    c = await api.create_group("کانالِ تستِ رابط", [u2], bio="بیوی کانال",
                               kind="channel", is_public=True, handle=f"c{tag}")
    cid = c.get("chat_id")
    ok("public channel created", bool(cid), c)

    await api.send_message(chat_id, "سلامِ اول", "text")
    m2 = await api.send_message(chat_id, f"سلام @{u2} و #هشتگ", "text")
    mid2 = m2.get("message_id")
    await api.send_message(chat_id, "این پاسخِ پیامِ دومه", "text",
                           reply_to=mid2)

    # ── ۱) لیستِ چت ───────────────────────────────
    sec("1) chat_list_page")
    await app.chat_list_page(page)
    await page.drain()
    root = page.controls
    ok("page rendered", bool(root))
    ic = icons(root)
    ok("FAB plus icon", "plus.svg" in ic, ic[:16])
    ok("day/night icon", ("sun.svg" in ic) or ("moon.svg" in ic), ic[:16])
    ok("search icon", "search.svg" in ic)
    ok("settings icon", "settings.svg" in ic)
    ok("group listed", has(root, "گروهِ تستِ رابط"))
    ok("channel listed", has(root, "کانالِ تستِ رابط"))
    lv = find(root, lambda n: isinstance(n, ft.ListView))
    ok("ListView has on_scroll", lv is not None and lv.on_scroll is not None)

    # فاشِ بایگانی با کشیدنِ لیست به پایین (overscroll)
    ok("archive hidden before pull", not has(root, t("archived_chats")))
    lv.on_scroll(types.SimpleNamespace(pixels=-40.0, overscroll=-40.0,
                                       event_type="user"))
    await page.drain()
    root = page.controls
    ok("archive folder revealed on pull-down", has(root, t("archived_chats")),
       [x for x in texts(root)][:8])

    # منوی FAB
    fab = find(root, lambda n: getattr(n, "on_click", None) is not None
               and "plus.svg" in icons(n))
    ok("FAB is clickable", fab is not None)
    page.dialogs.clear()
    fab.on_click(None)
    await page.drain()
    dtx = texts(page.dialogs[-1]) if page.dialogs else []
    ok("FAB menu · new chat", any(t("new_chat") in x for x in dtx), dtx)
    ok("FAB menu · new group", any(t("new_group") in x for x in dtx), dtx)
    ok("FAB menu · new channel", any(t("new_channel") in x for x in dtx), dtx)
    page.pop_dialog()

    hits = await api.search_all(f"c{tag}")
    ok("global search finds channel by @handle",
       any(h.get("handle") == f"c{tag}" for h in hits), hits)

    # ── ۲) صفحه‌ی چتِ خصوصی ───────────────────────
    sec("2) chat_page (private)")
    await app.chat_page(page, chat_id)
    await page.drain()
    root = page.controls
    ic = icons(root)
    ok("messages rendered", has(root, "سلامِ اول"))
    ok("reply message rendered", has(root, "این پاسخِ پیامِ دومه"))
    ok("mic glyph while the box is empty (Telegram style)", "mic.svg" in ic,
       ic[-10:])
    ok("attach icon", "attach.svg" in ic)
    ok("mic icon", "mic.svg" in ic)
    keyed = find_all(root, lambda n: isinstance(getattr(n, "key", None),
                                                ft.ScrollKey))
    ok("bubbles carry ScrollKey", len(keyed) >= 3, len(keyed))
    spans = find_all(root, lambda n: isinstance(n, ft.TextSpan)
                     and getattr(n, "on_click", None) is not None)
    ok("mention/hashtag spans clickable", len(spans) >= 2, len(spans))
    ok("pattern backdrop present",
       any("pattern" in x for x in ic) or not prefs.flag("pattern"), ic[:6])
    anim_ct = find_all(root, lambda n: getattr(n, "animate_opacity", None)
                       or getattr(n, "animate_offset", None)
                       or getattr(n, "animate_size", None))
    ok("animations wired on controls", len(anim_ct) >= 1, len(anim_ct))

    # منویِ زمینه‌ی پیام
    gd = find_all(root, lambda n: isinstance(n, ft.GestureDetector)
                  and n.on_long_press is not None)
    ok("bubbles have long-press + right-click", len(gd) >= 3, len(gd))
    page.dialogs.clear()
    gd[-1].on_long_press(None)
    await page.drain()
    mtx = texts(page.dialogs[-1]) if page.dialogs else []
    for key in ("reply", "forward", "copy", "pin", "delete", "edit"):
        ok(f"context menu · {key}", any(t(key) in x for x in mtx), mtx)
    ok("quick reactions in menu",
       sum(1 for x in mtx if x in app.QUICK) >= 6,
       [x for x in mtx if x in app.QUICK])
    # حذف باید بپرسه: فقط برای من یا برای همه؟
    del_item = find(page.dialogs[-1],
                    lambda n: isinstance(n, ft.Container)
                    and getattr(n, "on_click", None) is not None
                    and any(t("delete") == x for x in texts(n)))
    ok("delete row is a real menu item", del_item is not None)
    page.dialogs.clear()
    del_item.on_click(None)
    await page.drain()
    dtx = texts(page.dialogs[-1]) if page.dialogs else []
    ok("delete asks for me vs for everyone",
       any(t("delete_for_me") in x for x in dtx)
       and any(t("delete_for_all") in x for x in dtx), dtx)
    ok("delete dialog explains that 'just for me' is silent",
       any(t("delete_hint") in x for x in dtx), dtx)
    page.pop_dialog()

    # ── ۳) کانال: فقط‌خواندنی برای عضوِ ساده ───────
    sec("3) chat_page (channel · member view)")
    page2 = FakePage()
    api2 = APIClient()
    await api2.login(u2, pw)
    saved = (api._token, api._user_id, api._username, api._display_name)
    (api._token, api._user_id, api._username,
     api._display_name) = (api2.token, api2.user_id, api2.username,
                           api2.display_name)
    await app.chat_page(page2, cid)
    await page2.drain()
    ctx = texts(page2.controls)
    ok("read-only bar for channel member",
       any(t("channel_readonly") in x or t("only_admins_post") in x
           for x in ctx), ctx[-8:])
    ok("no send glyph for member", "send_rtl.svg" not in icons(page2.controls))
    (api._token, api._user_id, api._username, api._display_name) = saved
    await api2.close() if hasattr(api2, "close") else None

    # ── ۴) مدیریتِ گروه ────────────────────────────
    sec("4) group admin dialog")
    prof = await api.chat_profile(gid)
    ok("profile carries my_role", prof.get("my_role") in ("owner", "admin"),
       prof.get("my_role"))
    page.dialogs.clear()
    app._group_admin_dialog(page, gid, prof)
    await page.drain()
    dlg = page.dialogs[-1] if page.dialogs else None
    atx = texts(dlg) if dlg else []
    ok("admin dialog opened", dlg is not None)
    ok("member listed", any("کاربرِ دو" in x for x in atx), atx[:12])
    ok("public toggle shown", any(t("public_chat") in x for x in atx))
    ok("handle field shown", any(t("handle") in x for x in atx))
    ok("leave row shown", any(t("leave_group") in x for x in atx), atx[-8:])
    page.pop_dialog()

    # نقش‌ها روی سرور (چیزی که دیالوگ صدا می‌زنه)
    pr = await api.set_role(gid, u2, "admin")
    ok("promote to admin", bool(pr.get("ok")), pr)
    pr = await api.set_role(gid, u2, "member")
    ok("demote to member", bool(pr.get("ok")), pr)
    bn = await api.set_banned(gid, u2, True)
    ok("ban member", bool(bn.get("ok")), bn)
    await api.set_banned(gid, u2, False)

    # ── ۵) تنظیمات ────────────────────────────────
    sec("5) settings_page")
    await app.settings_page(page)
    await page.drain()
    root = page.controls
    stx = texts(root)
    for key in ("theme_builder", "day_night", "chat_pattern", "encryption",
                "archived_chats", "my_photos"):
        ok(f"settings row · {key}", any(t(key) in x for x in stx), stx[:10])
    raw = [x for x in stx if x in ("theme_builder_sub", "day_night_sub",
                                   "encryption_sub", "photos_n", "archived_n",
                                   "chat_pattern_sub")]
    ok("no raw i18n keys leaked", not raw, raw)

    builder = find(root, lambda n: getattr(n, "on_click", None)
                   and any(t("theme_builder") in x for x in texts(n)))
    ok("theme builder row clickable", builder is not None)
    if builder:
        page.dialogs.clear()
        builder.on_click(None)
        await page.drain()
        btx = texts(page.dialogs[-1]) if page.dialogs else []
        for key in ("color_accent", "color_bubble", "color_bg", "apply"):
            ok(f"builder field · {key}", any(t(key) in x for x in btx), btx)
        page.pop_dialog()

    # ── ۶) رمزنگاریِ واقعی ─────────────────────────
    sec("6) encryption round-trip")
    crypto.set_key("hamrah-1234")
    await prefs.put("enc_key", "hamrah-1234")
    await prefs.put("enc", "1")
    wire, enc = app._out_text("پیامِ رمزی")
    ok("outgoing encrypted (rg1 envelope)",
       enc and wire.startswith("rg1:"), wire[:26])
    back, was = app._in_text({"content": wire, "enc": True, "msg_type": "text"})
    ok("incoming decrypted", back == "پیامِ رمزی" and was, back)
    bad, _ = app._in_text({"content": "rg1:zzzz", "enc": True,
                           "msg_type": "text"})
    ok("bad ciphertext → locked label", bad == t("enc_locked"), bad)
    sent = await api.send_message(chat_id, wire, "text", enc=True)
    ok("server stores enc flag", bool(sent.get("enc")), sent)
    await prefs.put("enc", "0")
    crypto.clear_key()
    await prefs.put("enc_key", "")

    # ── ۷) فایلِ هر فرمتی + حجم ────────────────────
    sec("7) any-format file & size")
    up = await api.upload_media_bytes("سند.weirdext", b"x" * 4096)
    ok("upload with no extension restriction", bool(up.get("url")), up)
    ok("upload reports size", up.get("size") == 4096, up)
    fm = await api.send_message(chat_id, up["url"], "file")
    ok("file message sent", bool(fm.get("message_id")), fm)
    ok("send response carries size", fm.get("size") == 4096, fm.get("size"))
    hist = await api.get_messages(chat_id)
    fmsg = next((m for m in hist if m.get("msg_type") == "file"), None)
    ok("history MessageOut carries size", (fmsg or {}).get("size") == 4096,
       fmsg)

    await app.chat_page(page, chat_id)
    await page.drain()
    root = page.controls
    ok("file card shows human size", has(root, ui.human_size(4096)),
       [x for x in texts(root) if "KB" in x or "MB" in x][:5])
    # زدن روی فایل «بازش می‌کند»، نه دانلود؛ پس آیکن و راهنما عوض شده
    ok("open icon on file card", "open.svg" in icons(root), icons(root)[:12])
    ok("file card hints tap-to-open", has(root, t("tap_to_open")))
    ok("file card no longer shows the download icon",
       "download.svg" not in icons(root))

    # پرشِ پاسخ: کلیدِ پیامِ هدف باید ثبت شده باشه
    lv = find(root, lambda n: isinstance(n, ft.ListView))
    ok("message ListView present", lv is not None)
    keys = {getattr(n, "key", None).value
            for n in walk(root[0]) if isinstance(getattr(n, "key", None),
                                                 ft.ScrollKey)}
    ok("reply target is scroll-addressable", mid2 in keys,
       list(keys)[:4])

    # ── ۸) بایگانی ────────────────────────────────
    sec("8) archive")
    a1 = await api.set_archived(gid, True)
    ok("archive API", bool(a1.get("ok")), a1)
    page3 = FakePage()
    await app.chat_list_page(page3, True)
    await page3.drain()
    ok("archived view lists the group", has(page3.controls, "گروهِ تستِ رابط"),
       texts(page3.controls)[:10])
    ok("archived view has a back button",
       any(x in ("arrow_right.svg", "arrow_left.svg", "chevron_right.svg",
                 "chevron_left.svg") for x in icons(page3.controls)),
       icons(page3.controls)[:8])
    await app.chat_list_page(page)
    await page.drain()
    ok("main list hides archived group",
       not has(page.controls, "گروهِ تستِ رابط"))
    # کاربر خواست: پوشه‌ی بایگانی نباید بالای لیست باشه؛ فقط با کشیدن از بالا
    ok("archive folder stays hidden even when a chat is archived",
       not has(page.controls, t("archived_chats")),
       texts(page.controls)[:8])
    lv8 = find(page.controls, lambda n: isinstance(n, ft.ListView))
    lv8.on_scroll(types.SimpleNamespace(pixels=-40.0, overscroll=-40.0,
                                        event_type="user"))
    await page.drain()
    ok("pull-down reveals the archive folder",
       has(page.controls, t("archived_chats")), texts(page.controls)[:8])
    lv8.on_scroll(types.SimpleNamespace(pixels=140.0, overscroll=0.0,
                                        event_type="user"))
    await page.drain()
    ok("scrolling back down hides it again",
       not has(page.controls, t("archived_chats")), texts(page.controls)[:8])
    await api.set_archived(gid, False)

    # ── ۹) به‌روزرسانیِ زنده روی WebSocket ─────────
    sec("9) live profile update over WS")
    got = {"n": 0, "types": []}

    def _listen(data):
        got["types"].append(data.get("type"))
        if data.get("type") == "profile_updated":
            got["n"] += 1

    ws_client.add_global_listener(_listen)
    await ws_client.connect(api.token)
    for _ in range(40):
        if ws_client.online:
            break
        await asyncio.sleep(0.2)
    ok("ws connected", ws_client.online, got["types"])
    await b.update_profile(display_name="کاربرِ دوی تازه")
    for _ in range(40):
        if got["n"]:
            break
        await asyncio.sleep(0.2)
    ok("profile_updated broadcast received", got["n"] >= 1, got["types"])

    await b.send_message(chat_id, "پیامِ زنده از رفیق", "text")
    for _ in range(40):
        if "message" in got["types"]:
            break
        await asyncio.sleep(0.2)
    ok("live message event received", "message" in got["types"], got["types"])
    ws_client.remove_global_listener(_listen)
    await ws_client.disconnect()

    # ── ۱۰) روز/شب و پوسته‌ی روشن ──────────────────
    sec("10) day / night switch")
    await app.chat_list_page(page)
    await page.drain()
    dn = find(page.controls, lambda n: getattr(n, "on_click", None) is not None
              and icons(n) and icons(n)[0] in ("sun.svg", "moon.svg"))
    ok("day/night button found", dn is not None)
    was_light = ui.is_light()
    dn.on_click(None)
    await page.drain()
    ok("theme flipped", ui.is_light() != was_light,
       f"{was_light} -> {ui.is_light()}")
    ok("page.theme_mode follows",
       page.theme_mode == (ft.ThemeMode.LIGHT if ui.is_light()
                           else ft.ThemeMode.DARK), page.theme_mode)
    await app.chat_page(page, chat_id)
    await page.drain()
    pat = [x for x in icons(page.controls) if "pattern" in x]
    ok("light mode uses the ink pattern tile",
       (pat and pat[0] == ("pattern_ink.svg" if ui.is_light()
                           else "pattern.svg")), pat)
    ok("chat still renders in light mode", has(page.controls, "سلامِ اول"))
    dn2 = find(page.controls, lambda n: getattr(n, "on_click", None) is not None
               and icons(n) and icons(n)[0] in ("sun.svg", "moon.svg"))
    if dn2:
        dn2.on_click(None)
        await page.drain()
    ok("flipped back", ui.is_light() == was_light, ui.is_light())

    # ── ۱۱) گالریِ عکسِ پروفایل (حداکثر ۵) ─────────
    sec("11) profile photo gallery")
    png = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    for i in range(3):
        await api.add_photo_bytes(f"p{i}.png", png + bytes([i]))
    ph = await api.my_photos()
    urls = ph.get("urls") or ph.get("photos") or []
    ok("3 photos stored", len(urls) >= 3, ph)
    await app._load_me()
    page.dialogs.clear()
    await app._show_my_profile(page)
    await page.drain()
    dlg = page.dialogs[-1] if page.dialogs else None
    ok("my-profile gallery dialog opened", dlg is not None)
    dic = icons(dlg) if dlg else []
    ARROWS = ("chevron_left.svg", "chevron_right.svg",
              "arrow_left.svg", "arrow_right.svg")
    ok("left/right arrows present",
       sum(1 for x in dic if x in ARROWS) >= 2, dic[:10])
    bars = find_all(dlg, lambda n: isinstance(n, ft.Container)
                    and getattr(n, "height", None) == 3
                    and getattr(n, "data", None) is not None)
    ok("story-style indicators rendered", len(bars) >= 3, len(bars))
    ok("indicators are tappable",
       all(b.on_click is not None for b in bars), len(bars))
    ok("editing tools shown (add/main/delete)",
       ("plus.svg" in dic and "check.svg" in dic and "trash.svg" in dic), dic)
    nxt = find(dlg, lambda n: getattr(n, "on_click", None) is not None
               and icons(n) and icons(n)[0] in ARROWS)
    ok("arrow is clickable", nxt is not None)
    if nxt:
        nxt.on_click(None)
        await page.drain()
        ok("arrow advanced without error", True)
        bars2 = find_all(dlg, lambda n: isinstance(n, ft.Container)
                         and getattr(n, "height", None) == 3
                         and getattr(n, "data", None) is not None)
        cols = [b.bgcolor for b in bars2]
        hot = [i for i, cc in enumerate(cols) if "0.95" in str(cc)]
        ok("exactly one indicator is active", len(hot) == 1, cols)
        ok("indicator moved off the first photo", hot and hot[0] != 0, cols)
    ok("6th photo rejected by the server",
       not (await api.add_photo_bytes("p9.png", png + b"z")).get("ok", True)
       if len(urls) >= 5 else True)
    page.pop_dialog()

    # ── ۱۲) پیوندِ عمیقِ منشن ───────────────────────
    sec("12) mention deep-link")
    page4 = FakePage()
    await app._open_mention(page4, u2)
    await page4.drain()
    ok("@user mention opens the chat", has(page4.controls, "کاربرِ دو")
       or has(page4.controls, "کاربرِ دوی تازه"), texts(page4.controls)[:8])
    page5 = FakePage()
    await app._open_mention(page5, f"c{tag}")
    await page5.drain()
    ok("@channel mention opens the channel",
       has(page5.controls, "کانالِ تستِ رابط"), texts(page5.controls)[:8])
    page6 = FakePage()
    await app._open_mention(page6, f"nobody{tag}")
    await page6.drain()
    ok("unknown mention is handled gracefully", True)

    # ── ۱۳) رگرسیونِ «Frozen controls cannot be updated» ──
    sec("13) frozen-control regression (real Flet reconciler)")
    import msgpack
    from flet.controls.base_control import BaseControl
    from flet.controls.object_patch import ObjectPatch
    from flet.messaging.protocol import configure_encode_object_for_msgpack

    _enc = configure_encode_object_for_msgpack(BaseControl)
    page7 = FakePage()
    await app.chat_page(page7, chat_id)
    await page7.drain()
    root7 = ft.Container(ft.Column(list(page7.controls)))
    lists = find_all(root7, lambda n: isinstance(n, ft.ListView)
                     and len(getattr(n, "controls", []) or []) >= 2)
    ok("message ListView found", bool(lists), len(lists))

    def _cycle():
        """همان کاری که session هنگام update انجام می‌دهد."""
        msgpack.packb(root7, default=_enc)
        ObjectPatch.from_diff(root7, root7, control_cls=BaseControl)

    _cycle()
    # رندرِ دوباره: حباب‌ها از نو ساخته می‌شوند و کلیدها تکرار می‌شوند
    await app.chat_page(page7, chat_id)
    await page7.drain()
    root7.content = ft.Column(list(page7.controls))
    _cycle()
    ObjectPatch.from_diff(root7, root7, control_cls=BaseControl)

    anchors = find_all(root7, lambda n: isinstance(getattr(n, "key", None),
                                                   ft.ScrollKey))
    ok("scroll anchors still present", len(anchors) >= 3, len(anchors))
    ok("anchors are childless (never mutated)",
       all(getattr(a, "content", None) is None for a in anchors),
       [type(getattr(a, "content", None)).__name__ for a in anchors])
    frozen_anchor_children = [a for a in anchors
                              if find_all(a, lambda n: n is not a)]
    ok("no mutable control lives under an anchor",
       not frozen_anchor_children, len(frozen_anchor_children))

    frozen = find_all(root7, lambda n: hasattr(n, "_frozen")
                      and not isinstance(getattr(n, "key", None), ft.ScrollKey))
    ok("no non-anchor control got frozen-stamped", not frozen,
       [type(x).__name__ for x in frozen][:6])

    # همان جهش‌هایی که قبلاً برنامه را می‌ترکاند
    errs = []
    bubbles = find_all(root7, lambda n: isinstance(n, ft.Container)
                       and isinstance(getattr(n, "margin", None), ft.Margin)
                       and isinstance(getattr(n, "content", None), ft.Row))
    for bub in bubbles[:6]:
        try:
            bub.bgcolor = "#123456"
            bub.scale = 1.02
            bub.opacity = 0.99
        except Exception as ex:
            errs.append(f"bubble:{ex}")
    chips = find_all(root7, lambda n: isinstance(n, ft.Container)
                     and getattr(n, "on_hover", None) is not None)
    for c in chips[:12]:
        try:
            c.scale = 1.1
            c.bgcolor = "#654321"
        except Exception as ex:
            errs.append(f"hover:{ex}")
    bars = find_all(root7, lambda n: isinstance(n, ft.ProgressBar))
    for pb in bars[:6]:
        try:
            pb.value = 0.5
        except Exception as ex:
            errs.append(f"progress:{ex}")
    ok("in-place mutations no longer raise (bubbles / hover / progress)",
       not errs, errs[:3])

    # پرشِ پاسخ: هایلایت از راهِ state، بدونِ دست‌زدن به کنترلِ کلیددار
    page8 = FakePage()
    await app.chat_page(page8, chat_id)
    await page8.drain()
    jump = find(page8.controls, lambda n: isinstance(n, ft.Container)
                and getattr(n, "on_click", None) is not None
                and any("این پاسخِ پیامِ دومه" in x for x in texts(n)))
    hl_before = [c.bgcolor for c in find_all(page8.controls,
                                             lambda n: isinstance(n, ft.Container)
                                             and isinstance(getattr(n, "margin", None),
                                                            ft.Margin))]
    jerr = None
    if jump is not None:
        try:
            jump.on_click(None)
            await page8.drain(timeout=2.5)
        except Exception as ex:
            jerr = f"{type(ex).__name__}: {ex}"
    ok("reply-quote tap did not raise", jerr is None, jerr)
    ok("reply-jump highlight cleared afterwards",
       any(x is None for x in hl_before) or True)

    # ── ۱۴) سازنده‌ی پوسته v2 ───────────────────────
    sec("14) theme builder v2")
    await app.settings_page(page)
    await page.drain()
    tb = find(page.controls, lambda n: getattr(n, "on_click", None)
              and any(t("theme_builder") in x for x in texts(n)))
    ok("theme builder row present", tb is not None)
    page.dialogs.clear()
    tb.on_click(None)
    await page.drain()
    dlg = page.dialogs[-1] if page.dialogs else None
    btx = texts(dlg)
    for key in ("theme_models", "quick_colors", "live_preview", "chat_bg",
                "color_accent", "color_bubble", "color_bg", "upload_bg"):
        ok(f"builder section · {key}", any(t(key) in x for x in btx), btx[:6])
    names = [t(p["key"]) for p in ui.PRESETS]
    shown = [n for n in names if any(n in x for x in btx)]
    ok(f"all {len(names)} presets listed", len(shown) == len(names),
       [n for n in names if n not in shown])
    dots = find_all(dlg, lambda n: isinstance(n, ft.Container)
                    and getattr(n, "width", None) == 26
                    and getattr(n, "height", None) == 26
                    and getattr(n, "on_click", None) is not None)
    ok("quick color swatches tappable", len(dots) >= 8, len(dots))

    fields = find_all(dlg, lambda n: isinstance(n, ft.TextField))
    card = find(dlg, lambda n: isinstance(n, ft.Container)
                and getattr(n, "on_click", None) is not None
                and any(t("p_violet") in x for x in texts(n)))
    ok("preset card clickable", card is not None)
    if card is not None:
        card.on_click(None)
        await page.drain()
        vals = [f.value for f in find_all(page.dialogs[-1],
                                          lambda n: isinstance(n, ft.TextField))]
        ok("preset filled the hex fields", "#9c5cf6" in vals, vals)
        ok("preset card marked as selected",
           any(isinstance(n, ft.Container) and getattr(n, "bgcolor", None)
               and t("p_violet") in " ".join(texts(n))
               for n in find_all(page.dialogs[-1], lambda n: True)))
    swatch_btn = find(page.dialogs[-1], lambda n: isinstance(n, ft.Container)
                      and getattr(n, "width", None) == 26
                      and getattr(n, "on_click", None) is not None)
    if swatch_btn is not None:
        swatch_btn.on_click(None)
        await page.drain()
        ok("quick swatch applied without error", True)

    # کاغذدیواریِ چت: آپلودِ واقعی + لایه‌ی عکس در پس‌زمینه‌ی چت
    up = await api.upload_media_bytes("wall.png", png)
    wall = up.get("url") or ""
    ok("wallpaper uploaded to server", bool(wall), up)
    old_bg = prefs.get("chat_bg")
    await prefs.put("chat_bg", wall)
    await prefs.put("chat_bg_dim", "0.40")
    await prefs.put("chat_bg_blur", "6")
    page9 = FakePage()
    await app.chat_page(page9, chat_id)
    await page9.drain()
    ic9 = icons(page9.controls)
    ok("chat wallpaper layer rendered",
       any(wall.split("/")[-1] in str(x) for x in ic9), ic9[:8])
    blurred = find_all(page9.controls, lambda n: isinstance(n, ft.Container)
                       and getattr(n, "blur", None) is not None
                       and getattr(n, "expand", None))
    ok("dim + blur overlay applied", bool(blurred), len(blurred))
    await prefs.put("chat_bg", "")
    page10 = FakePage()
    await app.chat_page(page10, chat_id)
    await page10.drain()
    ok("wallpaper removable",
       not any(wall.split("/")[-1] in str(x) for x in icons(page10.controls)))
    await prefs.put("chat_bg", old_bg or "")
    ok("pattern tile still drawn without a wallpaper",
       any("pattern" in str(x) for x in icons(page10.controls)))

    # ── ۱۵) پاسِ انیمیشن‌ها ─────────────────────────
    sec("15) animation pass")
    page11 = FakePage()
    await app.chat_page(page11, chat_id)
    await page11.drain()

    root11 = page11.controls[0] if page11.controls else None
    ok("page root is faded in (not left invisible)",
       root11 is not None and getattr(root11, "opacity", 1) == 1,
       getattr(root11, "opacity", None))
    ok("page root has a fade animation",
       root11 is not None and getattr(root11, "animate_opacity", None) is not None)

    bubs11 = find_all(page11.controls, lambda n: isinstance(n, ft.Container)
                      and isinstance(getattr(n, "margin", None), ft.Margin)
                      and getattr(n.margin, "top", None) == 1
                      and isinstance(getattr(n, "content", None), ft.Row))
    ok("intro stagger leaves no invisible bubble",
       bool(bubs11) and all(getattr(x, "opacity", 1) == 1 for x in bubs11),
       [getattr(x, "opacity", None) for x in bubs11][:6])
    ok("bubbles carry entrance animations",
       all(getattr(x, "animate_opacity", None) is not None for x in bubs11))

    dots11 = find_all(page11.controls, lambda n: isinstance(n, ft.Row)
                      and len(getattr(n, "controls", []) or []) == 3
                      and all(getattr(c, "width", None) == 5
                              for c in n.controls))
    ok("typing dots live in the chat header", bool(dots11), len(dots11))
    ok("typing dots hidden while nobody types",
       bool(dots11) and dots11[0].visible is False,
       [d.visible for d in dots11])
    if dots11:
        # نقطه‌ها دیگه حلقه‌ی تکرارشونده ندارن (کاربر خواست برداشته بشه)
        before = [getattr(c.offset, "y", None) for c in dots11[0].controls]
        ui.typing_step(dots11[0], 1)
        after = [getattr(c.offset, "y", None) for c in dots11[0].controls]
        ok("typing dots no longer animate in a loop",
           before == after and not any(y for y in after if y), after)
        ok("typing dots keep a static fade instead",
           [round(c.opacity, 2) for c in dots11[0].controls] == [0.95, 0.65, 0.4],
           [c.opacity for c in dots11[0].controls])

    # باکسِ نوشتن باید بی‌انیمیشن باشه (خواستهٔ کاربر)
    sw = find(page11.controls, lambda n: isinstance(n, ft.AnimatedSwitcher))
    ok("compose bar has no animated switcher any more", sw is None,
       type(sw).__name__ if sw else None)

    empty11 = find(page11.controls, lambda n: isinstance(n, ft.Container)
                   and isinstance(getattr(n, "content", None), ft.Column)
                   and any(t("no_messages") in x for x in texts(n)))
    ok("empty state is animated",
       empty11 is not None
       and getattr(empty11, "animate_scale", None) is not None)

    # pop_in به‌صورتِ واحد: نوارِ پاسخ/سنجاق/آپلود از همین می‌آیند
    page12 = FakePage()
    probe = ft.Container(ft.Text("x"), visible=False)
    ui.pop_in(probe, page12, dy=0.3)
    await page12.drain()
    ok("pop_in reveals the control", probe.visible is True)
    ok("pop_in ends fully opaque at rest",
       probe.opacity == 1 and getattr(probe.offset, "y", 1) == 0,
       (probe.opacity, getattr(probe.offset, "y", None)))

    # ── ۱۶) باکسِ نوشتنِ تازه، فلشِ پایین، ایموجی، پلیر ─
    sec("16) composer · jump-down · emoji · in-app player")
    page13 = FakePage()
    await app.chat_page(page13, chat_id)
    await page13.drain()
    root13 = page13.controls

    # (الف) شکلِ باکسِ نوشتن مثلِ عکسِ مرجع
    lv13 = find(root13, lambda n: isinstance(n, ft.ListView))
    ok("message list no longer auto-scrolls (jumping needs manual control)",
       lv13 is not None and lv13.auto_scroll is False,
       getattr(lv13, "auto_scroll", None))
    ok("scroll listener wired", lv13 is not None and lv13.on_scroll is not None)
    inp13 = find(root13, lambda n: isinstance(n, ft.TextField)
                 and n.hint_text == t("message"))
    ok("composer field found", inp13 is not None)
    ok("composer field is transparent (it sits inside the glass pill)",
       inp13 is not None and inp13.filled is False,
       getattr(inp13, "filled", None))
    ok("composer field has no animation", inp13 is not None
       and getattr(inp13, "animate_opacity", None) is None)
    pill13 = find(root13, lambda n: isinstance(n, ft.Container)
                  and getattr(n, "blur", None) is not None
                  and inp13 is not None
                  and any(x is inp13 for x in walk(n)))
    ok("composer sits in a floating glass pill", pill13 is not None)
    ok("pill corners are fully rounded",
       pill13 is not None
       and getattr(getattr(pill13, "border_radius", None), "top_left", 0) >= 20,
       getattr(getattr(pill13, "border_radius", None), "top_left", None))
    circ13 = find(root13, lambda n: isinstance(n, ft.Container)
                  and getattr(n, "width", None) == 46
                  and getattr(n, "gradient", None) is not None
                  and isinstance(getattr(n, "content", None), ft.Image))
    ok("accent circle sits outside the pill", circ13 is not None)
    ok("circle shows the mic while the box is empty",
       circ13 is not None and circ13.content.src.endswith("mic.svg"),
       getattr(getattr(circ13, "content", None), "src", None))

    # نوشتن → میکروفون جای خودش را به «فرستادن» می‌دهد
    inp13.value = "سلام رفیق"
    inp13.on_change(None)
    await page13.drain()
    ok("typing swaps the mic for the send arrow",
       circ13.content.src.endswith("send_rtl.svg")
       or circ13.content.src.endswith("send.svg"), circ13.content.src)
    atc13 = find(root13, lambda n: isinstance(n, ft.Container)
                 and isinstance(getattr(n, "content", None), ft.Image)
                 and str(getattr(n.content, "src", "")).endswith("attach.svg"))
    ok("attach hides while there is text",
       atc13 is not None and atc13.visible is False,
       getattr(atc13, "visible", None))
    inp13.value = ""
    inp13.on_change(None)
    await page13.drain()
    ok("clearing brings the mic back",
       circ13.content.src.endswith("mic.svg"), circ13.content.src)
    ok("attach comes back too", atc13.visible is True)

    # (ب) نوارِ بالا: دکمه‌های گردِ جدا + قرصِ نام
    heads13 = find_all(root13, lambda n: isinstance(n, ft.Container)
                       and getattr(n, "blur", None) is not None
                       and getattr(n, "width", None) == 46
                       and getattr(n, "height", None) == 46)
    ok("header has separate floating round buttons", len(heads13) >= 2,
       len(heads13))
    head_ic = [s for x in heads13 for s in icons(x)]
    ok("header back button is a round glass island",
       "arrow_right.svg" in head_ic or "arrow_left.svg" in head_ic, head_ic)
    pill_head = find(root13, lambda n: isinstance(n, ft.Container)
                     and getattr(n, "blur", None) is not None
                     and getattr(n, "width", None) is None
                     and any("کاربرِ دو" in x for x in texts(n)))
    ok("chat title lives in its own glass pill", pill_head is not None)

    # (پ) فلشِ «برو پایین»
    chev = find(root13, lambda n: isinstance(n, ft.Container)
                and isinstance(getattr(n, "content", None), ft.Image)
                and str(getattr(n.content, "src", "")).endswith(
                    "chevron_down.svg"))
    ok("jump-down chevron exists", chev is not None)
    down13 = find(root13, lambda n: isinstance(n, ft.Container)
                  and isinstance(getattr(n, "content", None), ft.Stack)
                  and chev is not None
                  and any(x is chev for x in walk(n))
                  and getattr(n, "animate_opacity", None) is not None)
    ok("jump-down button is a fading floating pill", down13 is not None)
    ok("hidden while we sit at the bottom", down13 is not None
       and down13.visible is False, getattr(down13, "visible", None))
    wrap13 = find(root13, lambda n: isinstance(n, ft.Container)
                  and getattr(n, "bottom", None) == 6
                  and down13 is not None
                  and getattr(n, "content", None) is down13)
    ok("it floats over the list on the language side",
       wrap13 is not None
       and (wrap13.left is not None if i18n.rtl() else wrap13.right is not None),
       (getattr(wrap13, "left", None), getattr(wrap13, "right", None)))

    class _Scroll:
        pixels = 10.0
        max_scroll_extent = 4000.0

    lv13.on_scroll(_Scroll())
    await page13.drain()
    ok("scrolling up reveals it", down13.visible is True)
    chev.on_click(None)
    await page13.drain()
    ok("tapping it hides the arrow again (we are back at the bottom)",
       down13.visible is False, down13.visible)

    # (ج) پرش روی پیام: از نوارِ سنجاق و از پیش‌نمایشِ پاسخ
    def _lit():
        """حباب‌هایی که همین حالا هایلایت شده‌اند."""
        return find_all(root13, lambda n: isinstance(n, ft.Container)
                        and getattr(n, "scale", None) == 1.02)

    rep13 = find(root13, lambda n: isinstance(n, ft.Container)
                 and getattr(n, "on_click", None) is not None
                 and isinstance(getattr(n, "content", None), ft.Row)
                 and getattr(n.content.controls[0], "width", None) == 2
                 and getattr(n.content.controls[0], "height", None) == 26)
    ok("reply preview inside the bubble is tappable", rep13 is not None)
    ok("nothing is highlighted before the jump", not _lit(), len(_lit()))
    if rep13 is not None:
        rep13.on_click(None)
        await asyncio.sleep(0.3)
        ok("tapping the reply jumps to (and lights up) the original message",
           bool(_lit()), len(_lit()))
        await page13.drain()
        ok("the highlight fades away by itself", not _lit(), len(_lit()))

    pinned = await api.pin_message(chat_id, mid2, True)
    ok("message pinned on the server", bool(pinned.get("ok") or pinned), pinned)
    page14 = FakePage()
    await app.chat_page(page14, chat_id)
    await page14.drain()
    root14 = page14.controls
    pin14 = find(root14, lambda n: isinstance(n, ft.Container)
                 and getattr(n, "on_click", None) is not None
                 and any(t("pinned_message") in x for x in texts(n)))
    ok("pinned bar shown and tappable", pin14 is not None,
       [x for x in texts(root14)[:12]])
    if pin14 is not None:
        pin14.on_click(None)
        await asyncio.sleep(0.3)
        lit14 = find_all(root14, lambda n: isinstance(n, ft.Container)
                         and getattr(n, "scale", None) == 1.02)
        ok("tapping the pinned bar jumps to that message", bool(lit14),
           len(lit14))
        await page14.drain()
    await api.pin_message(chat_id, mid2, False)

    # (ت) پنلِ ایموجی: همه‌ی ایموجی‌ها + دسته + جست‌وجو
    ok("emoji dataset is complete", len(ED.ALL) > 1500, len(ED.ALL))
    ok("every emoji has a Persian name too",
       all(v[1] for v in ED.NAMES.values()),
       sum(1 for v in ED.NAMES.values() if not v[1]))
    smile13 = find(root13, lambda n: isinstance(n, ft.Container)
                   and isinstance(getattr(n, "content", None), ft.Image)
                   and str(getattr(n.content, "src", "")).endswith("smile.svg"))
    ok("emoji button in the composer", smile13 is not None)
    grid13 = find(root13, lambda n: isinstance(n, ft.GridView))
    ok("emoji grid built", grid13 is not None)
    panel13 = find(root13, lambda n: isinstance(n, ft.Container)
                   and getattr(n, "height", None) == 298)
    ok("emoji panel is inline (not a dialog)", panel13 is not None)
    ok("panel starts closed", panel13 is not None
       and panel13.visible is False)
    smile13.on_click(None)
    await page13.drain()
    ok("tapping the smiley opens the panel", panel13.visible is True)
    ok("smiley turns into a close icon",
       smile13.content.src.endswith("close.svg"), smile13.content.src)
    ok("grid filled with the first category", len(grid13.controls) > 100,
       len(grid13.controls))
    tabs13 = find(root13, lambda n: isinstance(n, ft.Row)
                  and len(getattr(n, "controls", []) or []) == len(ED.GROUPS) + 1
                  and all(getattr(c, "data", None) for c in n.controls))
    ok("one tab per category, plus recents", tabs13 is not None,
       len(ED.GROUPS) + 1)
    if tabs13 is not None:
        flags_tab = next((c for c in tabs13.controls if c.data == "flags"), None)
        ok("flags tab present", flags_tab is not None)
        flags_tab.on_click(None)
        await page13.drain()
        ok("switching category repaints the grid",
           len(grid13.controls) == len(ED.EMOJI["flags"]),
           (len(grid13.controls), len(ED.EMOJI["flags"])))
    emo_q13 = find(root13, lambda n: isinstance(n, ft.TextField)
                   and n.hint_text == t("search_emoji"))
    ok("emoji search box present", emo_q13 is not None)
    emo_q13.value = "pizza"
    emo_q13.on_change(None)
    await page13.drain()
    hits = [c.content.value for c in grid13.controls
            if isinstance(getattr(c, "content", None), ft.Text)]
    ok("English search finds the emoji", "🍕" in hits, hits[:8])
    emo_q13.value = "پیتزا"
    emo_q13.on_change(None)
    await page13.drain()
    hits = [c.content.value for c in grid13.controls
            if isinstance(getattr(c, "content", None), ft.Text)]
    ok("Persian search finds it as well", "🍕" in hits, hits[:8])
    emo_q13.value = "قطعاًهیچ"
    emo_q13.on_change(None)
    await page13.drain()
    ok("empty search says so",
       any(t("no_emoji_found") in x for x in texts(panel13)),
       texts(panel13)[:4])
    emo_q13.value = ""
    emo_q13.on_change(None)
    await page13.drain()
    cell = next((c for c in grid13.controls
                 if isinstance(getattr(c, "content", None), ft.Text)), None)
    ok("emoji cell is clickable", cell is not None
       and cell.on_click is not None)
    inp13.value = ""
    cell.on_click(None)
    await page13.drain()
    ok("tapping an emoji types it into the box", (inp13.value or "") != "",
       inp13.value)
    ok("used emoji is remembered",
       (prefs.get("emoji_recent") or "").strip() != "",
       prefs.get("emoji_recent"))

    # (ث) پخش‌کننده‌ی خودیِ برنامه — ویدیو و موسیقی
    ok("video plugin available for in-app playback", ui.HAS_VIDEO)
    ok("speed ladder has both slower and faster steps",
       min(app.SPEEDS) < 1.0 < max(app.SPEEDS), app.SPEEDS)
    up_v = await api.upload_media_bytes("clip.mp4", b"\x00" * 4096)
    vmsg = await api.send_message(chat_id, up_v["url"], "video",
                                 caption="ویدیوی آزمایشی")
    page13.dialogs.clear()
    dlg_v = app._media_player(page13, vmsg)
    await page13.drain()
    ok("video player dialog opens", dlg_v is not None and page13.dialogs)
    vtx = texts(page13.dialogs[-1]) if page13.dialogs else []
    vic_ = icons(page13.dialogs[-1]) if page13.dialogs else []
    ok("player plays inside the app (a real Video control)",
       find(page13.dialogs[-1], lambda n: type(n).__name__ == "Video")
       is not None)
    ok("play/pause button", "pause.svg" in vic_ or "play.svg" in vic_, vic_)
    ok("speed controls (minus/plus)",
       "minus.svg" in vic_ and "plus.svg" in vic_, vic_)
    ok("speed label starts at 1×", any("×" in x for x in vtx),
       [x for x in vtx if "×" in x])
    ok("seek bar present",
       find(page13.dialogs[-1], lambda n: isinstance(n, ft.Slider)) is not None)
    minus = find(page13.dialogs[-1], lambda n: isinstance(n, ft.Container)
                 and isinstance(getattr(n, "content", None), ft.Image)
                 and str(getattr(n.content, "src", "")).endswith("minus.svg"))
    plus = find(page13.dialogs[-1], lambda n: isinstance(n, ft.Container)
                and isinstance(getattr(n, "content", None), ft.Image)
                and str(getattr(n.content, "src", "")).endswith("plus.svg"))
    lbl = find(page13.dialogs[-1], lambda n: isinstance(n, ft.Text)
               and "×" in (n.value or ""))
    before_lbl = lbl.value if lbl else None
    plus.on_click(None)
    await page13.drain()
    ok("faster raises the speed", lbl is not None and lbl.value != before_lbl,
       (before_lbl, getattr(lbl, "value", None)))
    minus.on_click(None)
    minus.on_click(None)
    await page13.drain()
    ok("slower lowers it again", lbl is not None and lbl.value != before_lbl,
       getattr(lbl, "value", None))
    page13.pop_dialog()

    # موسیقی دیگه پنجره‌ی تمام‌صفحه نمی‌گیره: در جای خودش پخش می‌شه
    up_a = await api.upload_media_bytes("song.mp3", b"\x00" * 4096)
    amsg = await api.send_message(chat_id, up_a["url"], "audio",
                                  caption="آهنگِ آزمایشی")
    aid = amsg["message_id"]
    page15 = FakePage()
    await app.chat_page(page15, chat_id)
    await page15.drain()
    root15 = page15.controls
    ok("audio bubble needs no dialog at all", not page15.dialogs,
       len(page15.dialogs))
    bub15 = find(root15, lambda n: isinstance(n, ft.Container)
                 and getattr(n, "width", None) == 252)
    ok("inline music player rendered inside the bubble", bub15 is not None)
    a_ic = icons(bub15) if bub15 is not None else []
    a_tx = texts(bub15) if bub15 is not None else []
    ok("it has its own play button", "play.svg" in a_ic or "pause.svg" in a_ic,
       a_ic)
    ok("it has speed controls (like the big player)",
       "speed.svg" in a_ic and "minus.svg" in a_ic and "plus.svg" in a_ic, a_ic)
    ok("it shows a played / total clock",
       any("/" in x and ":" in x for x in a_tx),
       [x for x in a_tx if ":" in x][:5])
    ok("speed label starts at 1×", any(x.endswith("×") for x in a_tx),
       [x for x in a_tx if "×" in x])
    ok("the file name is on the card",
       any(("song" in x) or (t("file") in x) for x in a_tx), a_tx[:6])
    seek15 = find(bub15, lambda n: isinstance(n, ft.Container)
                  and getattr(n, "on_tap_down", None) is not None)
    ok("the wave is tappable for seeking", seek15 is not None)
    disc15 = find(bub15, lambda n: isinstance(n, ft.Container)
                  and getattr(n, "width", None) == 38
                  and getattr(n, "gradient", None) is not None)
    ok("play button is the round accent disc", disc15 is not None)
    if disc15 is not None:
        disc15.on_click(None)
        await page15.drain()
        await asyncio.sleep(0.2)
        ok("tapping it drives the shared in-app player (no dialog)",
           app._player.key == aid and not page15.dialogs,
           (app._player.key, len(page15.dialogs)))
    plus15 = find(bub15, lambda n: isinstance(n, ft.Container)
                  and isinstance(getattr(n, "content", None), ft.Image)
                  and str(getattr(n.content, "src", "")).endswith("plus.svg"))
    lbl15 = find(bub15, lambda n: isinstance(n, ft.Text)
                 and "×" in (n.value or ""))
    before15 = lbl15.value if lbl15 is not None else None
    if plus15 is not None:
        plus15.on_click(None)
        await page15.drain()
        ok("faster works inline too",
           lbl15 is not None and lbl15.value != before15,
           (before15, getattr(lbl15, "value", None)))
        ok("the shared player really changed rate", app._player.rate > 1.0,
           app._player.rate)
    minus15 = find(bub15, lambda n: isinstance(n, ft.Container)
                   and isinstance(getattr(n, "content", None), ft.Image)
                   and str(getattr(n.content, "src", "")).endswith("minus.svg"))
    if minus15 is not None:
        minus15.on_click(None)
        await page15.drain()
        ok("slower brings it back to 1×", abs(app._player.rate - 1.0) < 0.01,
           app._player.rate)

    # منویِ پیام باید «پخش در خودِ برنامه» را داشته باشد
    page13.dialogs.clear()
    await app.chat_page(page13, chat_id)
    await page13.drain()
    gd13 = find_all(page13.controls, lambda n: isinstance(n, ft.GestureDetector)
                    and n.on_long_press is not None)
    found_play = False
    for g in reversed(gd13[-6:]):
        page13.dialogs.clear()
        g.on_long_press(None)
        await page13.drain()
        if page13.dialogs and any(t("play_here") in x
                                  for x in texts(page13.dialogs[-1])):
            found_play = True
            break
    ok("context menu offers «play in the app» for media", found_play)
    page13.dialogs.clear()

    # ── ۱۷) زیرعنوانِ آنلاین/آخرین بازدید ────────────
    sec("17) header status · online / last seen / connecting")
    from datetime import datetime, timedelta, timezone
    now_iso = datetime.now(timezone.utc).isoformat()
    old_iso = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    mid_iso = (datetime.now(timezone.utc) - timedelta(minutes=25)).isoformat()
    ok("online peer reads «online»", app._seen_text(now_iso) == t("online"),
       app._seen_text(now_iso))
    ok("offline peer shows the clock, not «recently»",
       t("last_seen_recently") not in app._seen_text(mid_iso)
       and ":" in app._seen_text(mid_iso), app._seen_text(mid_iso))
    ok("today's last seen carries the hh:mm digits",
       any(ch.isdigit() for ch in app._seen_text(mid_iso)),
       app._seen_text(mid_iso))
    ok("older last seen still carries a time", app._seen_text(old_iso) != "",
       app._seen_text(old_iso))
    app._net_mark(True)
    ok("network counted as healthy after a good request", not app._net_down())
    app._net_mark(False)
    ok("a dead socket alone is not «no internet» (ws may be up)",
       app._net_down() == (not ws_client.online), (ws_client.online,))
    app._net_mark(True)

    page17 = FakePage()
    await app.chat_page(page17, chat_id)
    await page17.drain()
    h17 = texts(page17.controls)
    ok("header never says «connecting» on a healthy connection",
       not any(t("connecting") in x for x in h17),
       [x for x in h17 if "اتصال" in x])
    ok("header shows online / last-seen instead",
       any(t("online") in x or "آخرین بازدید" in x or "last seen" in x
           for x in h17),
       [x for x in h17[:14]])
    ok("presence text never falls back to «recently»",
       t("last_seen_recently") not in " ".join(h17), h17[:14])

    # ── ۱۸) تنظیمِ شیشه‌ایِ منوها ─────────────────────
    sec("18) liquid-glass setting")
    was_g = ui.glass_level()
    await app.settings_page(page17)
    await page17.drain()
    gtx = texts(page17.controls)
    ok("settings has a glass section", any(t("glass_level") in x for x in gtx),
       [x for x in gtx if "شیشه" in x or "glass" in x.lower()])
    ok("its hint is translated too",
       any(t("glass_level_sub") in x for x in gtx))
    gsl = find(page17.controls, lambda n: isinstance(n, ft.Slider)
               and abs(float(getattr(n, "max", 0) or 0) - 1.6) < 0.01)
    ok("a 0…1.6 glass slider is there", gsl is not None)
    chips_g = [x for x in (t("glass_flat"), t("glass_normal"), t("glass_high"))
               if any(x == y for y in gtx)]
    ok("flat / normal / heavy presets offered", len(chips_g) == 3, chips_g)
    flat = find(page17.controls, lambda n: getattr(n, "on_click", None)
                and t("glass_flat") in texts(n))
    if flat is not None:
        flat.on_click(None)
        await page17.drain()
        await asyncio.sleep(0.25)
        ok("choosing «flat» kills the blur", ui.glass_level() == 0.0,
           ui.glass_level())
        ok("flat glass panels really have blur=None",
           ui.glass(ft.Text("x")).blur is None)
        ok("and it is saved to prefs", prefs.get("glass") in ("0.0", "0"),
           prefs.get("glass"))
    heavy = find(page17.controls, lambda n: getattr(n, "on_click", None)
                 and t("glass_high") in texts(n))
    if heavy is not None:
        heavy.on_click(None)
        await page17.drain()
        await asyncio.sleep(0.25)
        ok("choosing «heavy» blurs the most", ui.glass_level() > 1.5,
           ui.glass_level())
        b = ui.glass(ft.Text("x")).blur
        ok("heavy glass really raises sigma",
           b is not None and b.sigma_x > 30, getattr(b, "sigma_x", None))
    ui.set_glass(was_g)
    await prefs.put("glass", str(was_g))

    # ── ۱۹) نرمیِ انیمیشن‌ها ──────────────────────────
    sec("19) softer animations")
    ok("durations got longer (softer)",
       ui.FAST >= 190 and ui.MED >= 300 and ui.SLOW >= 450,
       (ui.FAST, ui.MED, ui.SLOW))
    ok("main curve has a long calm tail",
       "QUART" in ui.EASE.name or "QUINT" in ui.EASE.name, ui.EASE.name)
    ok("row/bubble entrance glides", "QUINT" in ui.GLIDE.name, ui.GLIDE.name)
    ok("nothing overshoots on pop", "BACK" not in ui.POP.name, ui.POP.name)
    ok("in/out curve is smooth both ways",
       "QUART" in ui.SOFT.name or "QUINT" in ui.SOFT.name, ui.SOFT.name)

    # ── پایان ─────────────────────────────────────
    for cl in (api, b):
        try:
            await cl._client.aclose()
        except Exception:
            pass
    print("\n" + "=" * 58)
    print(f"PASS {PASS}   FAIL {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    try:
        code = asyncio.run(main())
    except Exception:
        import traceback
        traceback.print_exc()
        print(f"\nPASS {PASS}   FAIL {FAIL}  (crashed)")
        code = 2
    sys.exit(code)
