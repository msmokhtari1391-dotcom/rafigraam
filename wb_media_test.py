"""
آزمونِ موجِ «مدیا و گیف» — شش خواسته‌ی تازه.

۱) زدن روی مدیا بازش می‌کند (دانلود نمی‌کند)
۲) «ذخیره در دانلود» در منویِ نگه‌داشتن
۳) کپشن زیرِ فایل هنگامِ فرستادن
۴) بخشِ گیف در چت
۵) پرسشِ «به شکلِ گیف بفرستم؟» برای ویدیو
۶) «افزودن به گیف‌ها» روی گیف

اجرا:  .venv/Scripts/python.exe wb_media_test.py     (سرور باید بالا باشه)
"""
import asyncio
import os
import struct
import sys
import time
import uuid
import zlib

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
        print(f"  FAIL  {name}  {str(extra)[:240]}")


def sec(title):
    print(f"\n[{title}]")


# ── نمونه‌فایل‌های واقعیِ کوچک ───────────────────────
def png_bytes(w=8, h=8):
    """یک PNG معتبرِ ۸×۸ — بی‌وابستگیِ بیرونی."""
    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))
    raw = b"".join(b"\x00" + b"\xff\x00\x88" * w for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))


GIF_BYTES = (b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff"
             b"!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01"
             b"\x00\x00\x02\x02D\x01\x00;")
MP4_BYTES = (b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"
             + b"\x00\x00\x00\x08free" + b"\x00" * 64)
MP3_BYTES = b"ID3\x03\x00\x00\x00\x00\x00\x00" + b"\xff\xfb\x90\x00" + b"\x00" * 64


# ── صفحه‌ی جعلی ────────────────────────────────────
class FakeWindow:
    width = height = min_width = min_height = 0
    title_bar_hidden = False
    bgcolor = None
    prevent_close = False

    def center(self):
        pass


class FakePage:
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
        self.snacks = []      # SnackBar در فلت روی هم تَه‌نشین نمی‌شه

    def clean(self):
        self.controls = []

    def add(self, *ctrls):
        self.controls.extend(ctrls)

    def update(self, *a):
        self.updates += 1

    def show_dialog(self, dlg):
        if isinstance(dlg, ft.SnackBar):
            self.snacks.append(dlg)
            return
        self.dialogs.append(dlg)

    def toasts(self):
        return [x for s in self.snacks for x in texts(s)]

    def pop_dialog(self, *a):
        if self.dialogs:
            self.dialogs.pop()

    def set_clipboard(self, text):
        self.clipboard = text

    def run_task(self, fn, *a, **kw):
        task = asyncio.create_task(fn(*a, **kw))
        self.tasks.append(task)
        return task

    async def drain(self, timeout=10.0):
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
            for attr in ("value", "text", "label", "hint_text", "tooltip",
                         "content"):
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


def clickable(root, label):
    """ردیفِ منو / دکمه‌ای که متنش دقیقاً `label` است و on_click دارد."""
    for c in find_all(root, lambda n: getattr(n, "on_click", None) is not None):
        if label in texts(c):
            return c
    return None


class Ev:
    """رویدادِ جعلیِ Flet — فقط `control` و `data` لازم است."""

    def __init__(self, control=None, data=None):
        self.control = control
        self.data = data


async def main():
    import crypto            # noqa: F401
    import i18n
    import prefs
    import ui
    import main as app
    from services.api import api, APIClient
    from services.ws_client import ws_client
    from i18n import t

    tag = uuid.uuid4().hex[:6]
    u1, u2 = f"md{tag}a", f"md{tag}b"
    pw = "test1234"
    page = FakePage()

    # فایل هرگز واقعاً با برنامه‌ی سیستم باز نمی‌شود — فقط ثبت می‌کنیم
    opened = []
    app._open_local = lambda p: (opened.append(p), True)[1]

    sec("0) bootstrap")
    await prefs.load()
    i18n.set_lang("fa")
    app._use_theme("telegram")
    ui.set_font_scale(prefs.scale())
    b = APIClient()
    await api.register(u1, "مدیا یک", pw)
    await b.register(u2, "مدیا دو", pw)
    ok("both registered", bool(api.token) and bool(b.token))
    await app._load_me()
    ch = await api.create_chat(u2)
    chat_id = ch.get("chat_id")
    ok("chat created", bool(chat_id), ch)
    ch2 = await api.create_group(f"گروهِ مدیا {tag}", [u2])
    gid = ch2.get("chat_id")
    ok("group created", bool(gid), ch2)

    # ── ۱) کپشن روی سرور ─────────────────────────
    sec("1) caption round-trip (HTTP)")
    up_png = await api.upload_media_bytes("shot.png", png_bytes())
    up_gif = await api.upload_media_bytes("loop.gif", GIF_BYTES)
    up_mp4 = await api.upload_media_bytes("clip.mp4", MP4_BYTES)
    up_mp3 = await api.upload_media_bytes("song.mp3", MP3_BYTES)
    up_bin = await api.upload_media_bytes("notes.pdf", b"%PDF-1.4\n%%EOF\n")
    ok("uploads returned urls",
       all(u.get("url") for u in (up_png, up_gif, up_mp4, up_mp3, up_bin)))

    CAP = "کپشنِ آزمایشی ۱۲۳"
    r_img = await api.send_message(chat_id, up_png["url"], "image", caption=CAP)
    ok("send_message returns caption", r_img.get("caption") == CAP, r_img)
    ok("send_message keeps msg_type", r_img.get("msg_type") == "image", r_img)

    msgs = await api.get_messages(chat_id)
    last = msgs[-1] if msgs else {}
    ok("get_messages returns caption", last.get("caption") == CAP, last)

    fw = await api.forward_message(chat_id, r_img["message_id"], gid)
    ok("forward keeps caption", fw.get("caption") == CAP, fw)

    r_none = await api.send_message(chat_id, "سلامِ متنی", "text")
    ok("text message has no caption", r_none.get("caption") in (None, ""),
       r_none)
    r_blank = await api.send_message(chat_id, up_bin["url"], "file",
                                     caption="   ")
    ok("blank caption stored as null", r_blank.get("caption") in (None, ""),
       r_blank)

    await api.pin_message(chat_id, r_img["message_id"], True)
    pin = await api.get_pinned(chat_id)
    ok("pinned message keeps caption", (pin or {}).get("caption") == CAP, pin)
    await api.pin_message(chat_id, r_img["message_id"], False)

    # ── ۲) کپشن روی وب‌سوکت ──────────────────────
    sec("2) caption round-trip (WebSocket)")
    got = []

    async def _spy(d):
        got.append(d)

    ws_b = ws_client.__class__()
    ws_b.add_global_listener(_spy)
    await ws_b.connect(b.token)
    for _ in range(40):
        if ws_b.online:
            break
        await asyncio.sleep(0.15)
    ok("peer ws online", ws_b.online)

    await ws_client.connect(api.token)
    for _ in range(40):
        if ws_client.online:
            break
        await asyncio.sleep(0.15)
    ok("sender ws online", ws_client.online)

    WCAP = "کپشنِ وب‌سوکتی"
    sent = await ws_client.send(chat_id, up_gif["url"], "gif", caption=WCAP)
    ok("ws send accepted", sent)
    for _ in range(50):
        if any(d.get("type") == "message" and d.get("caption") == WCAP
               for d in got):
            break
        await asyncio.sleep(0.12)
    hit = next((d for d in got if d.get("type") == "message"
                and d.get("caption") == WCAP), None)
    ok("peer received caption over ws", hit is not None,
       [d.get("type") for d in got])
    ok("peer received msg_type gif", (hit or {}).get("msg_type") == "gif", hit)
    ok("peer received size", isinstance((hit or {}).get("size"), int), hit)
    gif_mid = (hit or {}).get("message_id")

    stored = await api.get_messages(chat_id)
    sg = next((m for m in stored if m.get("message_id") == gif_mid), None)
    ok("ws caption persisted in db", (sg or {}).get("caption") == WCAP, sg)

    # ── ۳) API گیف‌های ذخیره‌شده ──────────────────
    sec("3) saved-GIF API")
    ok("fresh account has no gifs", await api.list_gifs() == [])
    a1 = await api.add_gif(up_gif["url"])
    ok("add gif → added True", a1["added"] and a1["urls"] == [up_gif["url"]], a1)
    a2 = await api.add_gif(up_gif["url"])
    ok("add same gif → added False", not a2["added"] and len(a2["urls"]) == 1, a2)
    up_gif2 = await api.upload_media_bytes("loop2.gif", GIF_BYTES)
    a3 = await api.add_gif(up_gif2["url"])
    ok("second gif added", a3["added"] and len(a3["urls"]) == 2, a3)
    ok("newest gif first", a3["urls"][0] == up_gif2["url"], a3)
    ok("list matches add result", await api.list_gifs() == a3["urls"])
    ok("peer account is isolated", await b.list_gifs() == [])
    d1 = await api.remove_gif(up_gif2["url"])
    ok("remove works", d1 == [up_gif["url"]], d1)
    d2 = await api.remove_gif(up_gif2["url"])
    ok("remove is idempotent", d2 == [up_gif["url"]], d2)
    bad = None
    try:
        await api.add_gif("javascript:alert(1)")
    except Exception as ex:
        bad = ex
    ok("bad gif url rejected", bad is not None, bad)
    await api.remove_gif(up_gif["url"])
    ok("gif list emptied for ui test", await api.list_gifs() == [])

    # ── ۴) کمک‌تابع‌های مسیر و نوع ────────────────
    sec("4) path & kind helpers")
    ok("_kind_for gif", app._kind_for("a.GIF") == "gif")
    ok("_kind_for image", app._kind_for("a.jpeg") == "image")
    ok("_kind_for video", app._kind_for("a.mkv") == "video")
    ok("_kind_for audio", app._kind_for("a.m4a") == "audio")
    ok("_kind_for file", app._kind_for("a.pdf") == "file")
    ok("_kind_for no ext", app._kind_for("README") == "file")
    ok("_ext_of upper", app._ext_of("X.MP4") == "mp4")
    raw = app._fname_raw("/media/" + "ab" * 16 + ".mp4")
    ok("_fname_raw hex → readable", raw.startswith("rafigram-")
       and raw.endswith(".mp4"), raw)
    ok("_fname_raw keeps real name",
       app._fname_raw("/media/holiday.jpg") == "holiday.jpg")
    ok("_fname_raw fallback", app._fname_raw("") == "file.bin")
    dl = app._downloads_dir()
    ok("_downloads_dir exists", os.path.isdir(dl), dl)
    cd = app._cache_dir()
    ok("_cache_dir exists", os.path.isdir(cd), cd)
    ok("cache is not downloads", os.path.normcase(cd) !=
       os.path.normcase(dl) and "Rafigram" in cd, cd)
    ok("MEDIA_KINDS complete",
       set(app.MEDIA_KINDS) == {"image", "gif", "video", "audio",
                                "voice", "file"}, app.MEDIA_KINDS)

    # ── ۵) باز کردن ≠ ذخیره کردن ─────────────────
    sec("5) open vs save")
    app._dl_cache.clear()
    m_png = {"content": up_png["url"], "msg_type": "image", "caption": CAP}
    want = app._fname_raw(up_png["url"])
    dl_before = os.path.join(dl, want)
    if os.path.exists(dl_before):
        os.remove(dl_before)
    opened.clear()
    page.snacks.clear()
    await app._open_media(page, m_png)
    cached = app._dl_cache.get(up_png["url"])
    ok("open cached the file", bool(cached) and os.path.isfile(cached), cached)
    ok("opening toast shown", t("opening") in page.toasts(), page.toasts())
    ok("cache path is inside app cache",
       os.path.normcase(os.path.dirname(cached or "")) ==
       os.path.normcase(cd), cached)
    ok("open handed path to the OS", opened and opened[-1] == cached, opened)
    with open(cached, "rb") as fh:
        ok("cached bytes match upload", fh.read() == png_bytes())
    ok("open did NOT write into Downloads", not os.path.exists(dl_before))

    opened.clear()
    await app._open_media(page, m_png)
    ok("second open reuses cache (no re-download)",
       opened and opened[-1] == cached, opened)

    await app._save_media(page, m_png)
    ok("save wrote into Downloads", os.path.isfile(dl_before), dl_before)
    ok("saved toast shown", t("saved_to_downloads") in page.toasts(),
       page.toasts())
    with open(dl_before, "rb") as fh:
        ok("saved bytes match upload", fh.read() == png_bytes())
    await app._save_media(page, m_png)
    stem, ext = os.path.splitext(dl_before)
    dup = f"{stem} (1){ext}"
    ok("second save de-duplicates name", os.path.isfile(dup), dup)
    for p in (dl_before, dup):
        try:
            os.remove(p)
        except OSError:
            pass
    ok("Downloads cleaned up after test",
       not os.path.exists(dl_before) and not os.path.exists(dup))

    bad_m = {"content": "/media/does-not-exist-" + tag + ".png"}
    opened.clear()
    await app._open_media(page, bad_m)
    ok("missing file → no OS call, no crash", not opened)

    # ── ۶) رندرِ حباب‌های مدیا ────────────────────
    sec("6) media bubbles render")
    VCAP = "کپشنِ ویدیو"
    r_vid = await api.send_message(chat_id, up_mp4["url"], "video",
                                  caption=VCAP)
    await api.send_message(chat_id, up_mp3["url"], "audio")
    await api.send_message(chat_id, up_bin["url"], "file", caption="سندِ مهم")
    await asyncio.sleep(0.3)

    await app.chat_page(page, chat_id)
    await page.drain()
    tree = page.controls
    tx, ic = texts(tree), icons(tree)
    ok("image caption rendered", CAP in tx, [x for x in tx if "کپشن" in x])
    ok("ws gif caption rendered", WCAP in tx)
    ok("video caption rendered", VCAP in tx)
    ok("file caption rendered", "سندِ مهم" in tx)
    ok("GIF corner tag rendered", "GIF" in tx, [x for x in tx if len(x) < 6])
    ok("video ext tag rendered", "MP4" in tx)
    ok("play badge rendered", "play.svg" in ic)
    # حبابِ ویدیو حالا قابِ اولِ فیلم رو نشون می‌ده؛ آیکونِ film فقط
    # جانشینه (وقتی پلاگینِ ویدیو نباشه)
    ok("video bubble shows a poster (or the film fallback)",
       find(page.controls, lambda n: type(n).__name__ == "Video") is not None
       or "film.svg" in ic, ic[:8])
    # موسیقی حالا در جای خودش پخش می‌شه (مثلِ ویس): موج + سرعت + ساعت،
    # بی هیچ پنجره‌ی تمام‌صفحه‌ای. پس آیکونِ music دیگه لازم نیست.
    ok("inline music player has a speed control", "speed.svg" in ic, ic[:12])
    ok("inline music player has minus/plus speed steps",
       "minus.svg" in ic and "plus.svg" in ic)
    ok("inline music player shows 0:00 / 0:00 clock",
       any("/" in x and ":" in x for x in tx),
       [x for x in tx if ":" in x][:6])
    ok("inline music player shows the rate label",
       any(x.endswith("×") for x in tx), [x for x in tx if "×" in x][:4])
    ok("open icon on file card", "open.svg" in ic)
    ok("tap-to-open hint shown", t("tap_to_open") in tx)
    ok("gif button in compose bar", "gif.svg" in ic)
    ok("gif button tooltip", t("gifs") in tx)
    ok("no legacy 'saved' label", t("saved") not in tx)

    imgs = [n for n in find_all(tree, lambda n: isinstance(n, ft.Image))
            if isinstance(getattr(n, "src", None), str)
            and "/media/" in n.src]
    ok("media images point at the server", len(imgs) >= 2, len(imgs))

    # زدن روی عکس → نمایشگرِ درون‌برنامه‌ای (نه دانلود)
    holder = find(tree, lambda n: isinstance(n, ft.Container)
                  and getattr(n, "on_click", None)
                  and getattr(n, "tooltip", None) == t("tap_to_open")
                  and find(n, lambda k: isinstance(k, ft.Image)
                           and isinstance(getattr(k, "src", None), str)
                           and up_png["url"] in k.src))
    ok("image bubble is tappable", holder is not None)
    before_dialogs = len(page.dialogs)
    if holder is not None:
        holder.on_click(Ev(holder))
    await page.drain()
    ok("tap opened the in-app viewer", len(page.dialogs) > before_dialogs)
    dlg = page.dialogs[-1] if page.dialogs else None
    dtx = texts(dlg)
    ok("viewer offers save-to-downloads", t("save_to_downloads") in dtx, dtx)
    ok("viewer offers open-with-system", t("open_file") in dtx, dtx)
    ok("viewer shows the caption", CAP in dtx)
    page.pop_dialog()

    # ── ۷) منوی نگه‌داشتن ────────────────────────
    sec("7) long-press menu")
    dets = find_all(tree, lambda n: isinstance(n, ft.GestureDetector)
                    and getattr(n, "on_long_press", None) is not None)
    ok("bubbles have long-press handlers", len(dets) >= 5, len(dets))
    menus = []
    for d in dets:
        n0 = len(page.dialogs)
        try:
            d.on_long_press(Ev(d))
        except Exception as ex:
            ok("long-press raised", False, ex)
            continue
        if len(page.dialogs) > n0:
            menus.append(texts(page.dialogs[-1]))
            page.pop_dialog()
    ok("menus opened", len(menus) >= 5, len(menus))
    with_open = [m for m in menus if t("open_file") in m
                 and t("save_to_downloads") in m]
    ok("media menus have open + save",
       len(with_open) >= 5, f"{len(with_open)}/{len(menus)}")
    with_gif = [m for m in menus if t("add_to_gifs") in m]
    ok("only the gif menu has add-to-gifs", len(with_gif) == 1,
       len(with_gif))
    txt_menus = [m for m in menus if t("open_file") not in m]
    ok("text menu has no media items", len(txt_menus) >= 1, len(txt_menus))
    ok("text menu still has reply/copy",
       all(t("reply") in m and t("copy") in m for m in txt_menus))
    ok("media menu keeps the reactions row",
       all(all(em in m for em in app.QUICK) for m in with_open),
       app.QUICK)

    # «افزودن به گیف‌ها» را واقعاً بزن
    gif_det = None
    for d in dets:
        n0 = len(page.dialogs)
        d.on_long_press(Ev(d))
        if len(page.dialogs) > n0:
            if t("add_to_gifs") in texts(page.dialogs[-1]):
                gif_det = page.dialogs[-1]
                break
            page.pop_dialog()
    ok("found the gif message menu", gif_det is not None)
    item = clickable(gif_det, t("add_to_gifs")) if gif_det else None
    ok("add-to-gifs item is clickable", item is not None)
    if item is not None:
        page.snacks.clear()
        item.on_click(Ev(item))
    await page.drain()
    saved = await api.list_gifs()
    ok("add-to-gifs saved it on the account", saved == [up_gif["url"]], saved)
    ok("gif_added toast shown", t("gif_added") in page.toasts(), page.toasts())
    a_dup = await api.add_gif(up_gif["url"])
    ok("re-adding reports duplicate", not a_dup["added"], a_dup)

    # ── ۸) دیالوگِ فرستادنِ فایل: کپشن + «به شکلِ گیف؟» ──
    sec("8) compose dialog (caption + send-as-gif)")

    def fake_pick(name, data, path=None):
        async def _p(pg, exts, title):
            app._last_pick["path"] = path
            return name, data
        app._pick = _p

    attach = find(page.controls, lambda n: isinstance(n, ft.Container)
                  and getattr(n, "tooltip", None) == t("attach")
                  and getattr(n, "on_click", None))
    ok("attach button found", attach is not None)

    async def open_compose(name, data, path=None):
        fake_pick(name, data, path)
        n0 = len(page.dialogs)
        attach.on_click(Ev(attach))
        await page.drain()
        return page.dialogs[-1] if len(page.dialogs) > n0 else None

    # ویدیو ⇒ سوییچِ «به شکلِ گیف بفرستم؟» باید باشه
    dv = await open_compose("clip2.mp4", MP4_BYTES)
    ok("compose dialog opened for video", dv is not None)
    vtx = texts(dv)
    ok("dialog title is send_file", t("send_file") in vtx, vtx)
    ok("caption field present", t("caption") in vtx, vtx)
    ok("caption hint present", t("caption_hint") in vtx, vtx)
    ok("video asks send-as-gif", t("send_as_gif") in vtx, vtx)
    ok("send-as-gif has a hint", t("send_as_gif_sub") in vtx, vtx)
    ok("video preview uses film icon", "film.svg" in icons(dv), icons(dv))
    sw = find(dv, lambda n: isinstance(n, ft.Switch))
    ok("switch rendered", sw is not None)
    ok("switch starts off", sw is not None and sw.value is False)

    cap_f = find(dv, lambda n: isinstance(n, ft.TextField))
    ok("caption text field rendered", cap_f is not None)
    VC2 = "ویدیوی خنده‌دار"
    cap_f.value = VC2
    sw.value = True
    sw.on_change(Ev(sw))
    send_btn = clickable(dv, t("send"))
    ok("send button in dialog", send_btn is not None)
    send_btn.on_click(Ev(send_btn))
    await page.drain()
    await asyncio.sleep(0.4)
    msgs2 = await api.get_messages(chat_id)
    lm = msgs2[-1] if msgs2 else {}
    ok("video sent AS GIF", lm.get("msg_type") == "gif", lm)
    ok("gif carries the caption", lm.get("caption") == VC2, lm)

    # همان ویدیو بی‌سوییچ ⇒ باید «video» بمونه
    dv2 = await open_compose("clip3.mp4", MP4_BYTES)
    ok("second compose opened", dv2 is not None)
    find(dv2, lambda n: isinstance(n, ft.TextField)).value = "بی‌گیف"
    clickable(dv2, t("send")).on_click(Ev(None))
    await page.drain()
    await asyncio.sleep(0.4)
    lm2 = (await api.get_messages(chat_id))[-1]
    ok("switch off → stays video", lm2.get("msg_type") == "video", lm2)
    ok("video caption stored", lm2.get("caption") == "بی‌گیف", lm2)

    # عکس ⇒ سوییچِ گیف نباید باشه
    di = await open_compose("photo9.png", png_bytes())
    ok("compose dialog opened for image", di is not None)
    itx = texts(di)
    ok("image has caption field", t("caption") in itx)
    ok("image does NOT ask send-as-gif", t("send_as_gif") not in itx, itx)
    find(di, lambda n: isinstance(n, ft.TextField)).value = "عکسِ کپشن‌دار"
    clickable(di, t("send")).on_click(Ev(None))
    await page.drain()
    await asyncio.sleep(0.4)
    lm3 = (await api.get_messages(chat_id))[-1]
    ok("image sent as image", lm3.get("msg_type") == "image", lm3)
    ok("image caption stored", lm3.get("caption") == "عکسِ کپشن‌دار", lm3)

    # فایلِ معمولی ⇒ نوعِ file، بی‌سوییچ، با کپشن
    df = await open_compose("report.pdf", b"%PDF-1.4\n%%EOF\n")
    ftx = texts(df)
    ok("file compose has no gif switch", t("send_as_gif") not in ftx)
    ok("file compose shows the file name", any("report.pdf" in x for x in ftx),
       ftx)
    find(df, lambda n: isinstance(n, ft.TextField)).value = "گزارشِ ماه"
    clickable(df, t("send")).on_click(Ev(None))
    await page.drain()
    await asyncio.sleep(0.4)
    lm4 = (await api.get_messages(chat_id))[-1]
    ok("pdf sent as file", lm4.get("msg_type") == "file", lm4)
    ok("file caption stored", lm4.get("caption") == "گزارشِ ماه", lm4)

    # صوت ⇒ نوعِ audio
    da = await open_compose("track.mp3", MP3_BYTES)
    ok("audio compose uses music icon", "music.svg" in icons(da), icons(da))
    clickable(da, t("send")).on_click(Ev(None))
    await page.drain()
    await asyncio.sleep(0.4)
    lm5 = (await api.get_messages(chat_id))[-1]
    ok("mp3 sent as audio", lm5.get("msg_type") == "audio", lm5)
    ok("empty caption stays null", lm5.get("caption") in (None, ""), lm5)

    # لغو ⇒ هیچ چیزی فرستاده نمی‌شه
    n_before = len(await api.get_messages(chat_id))
    dc = await open_compose("skip.png", png_bytes())
    clickable(dc, t("cancel")).on_click(Ev(None))
    await page.drain()
    await asyncio.sleep(0.3)
    ok("cancel sends nothing",
       len(await api.get_messages(chat_id)) == n_before)

    # ── ۹) پنلِ گیف‌های من ───────────────────────
    sec("9) my-GIFs panel")
    while page.dialogs:
        page.pop_dialog()
    gbtn = find(page.controls, lambda n: isinstance(n, ft.Container)
                and getattr(n, "tooltip", None) == t("gifs")
                and getattr(n, "on_click", None))
    ok("gif button found in compose bar", gbtn is not None)

    async def open_panel():
        n0 = len(page.dialogs)
        gbtn.on_click(Ev(gbtn))
        await page.drain()
        return page.dialogs[-1] if len(page.dialogs) > n0 else None

    await api.remove_gif(up_gif["url"])
    ok("gif list cleared", await api.list_gifs() == [])
    pe = await open_panel()
    ok("panel opened", pe is not None)
    ptx = texts(pe)
    ok("panel title", t("my_gifs") in ptx, ptx)
    ok("empty state shown", t("no_gifs") in ptx and t("no_gifs_sub") in ptx,
       ptx)
    ok("upload button offered", t("upload_gif") in ptx, ptx)
    ok("no tiles when empty",
       not find_all(pe, lambda n: isinstance(n, ft.Image)
                    and isinstance(getattr(n, "src", None), str)
                    and "/media/" in n.src))
    page.pop_dialog()

    # آپلودِ گیفِ تازه از خودِ پنل
    fake_pick("fresh.gif", GIF_BYTES)
    p2 = await open_panel()
    ok("panel reopened", p2 is not None)
    up_item = clickable(p2, t("upload_gif"))
    ok("upload button clickable", up_item is not None)
    up_item.on_click(Ev(up_item))
    await page.drain()
    await asyncio.sleep(0.4)
    mine = await api.list_gifs()
    ok("panel upload saved a gif", len(mine) == 1, mine)
    tiles = find_all(p2, lambda n: isinstance(n, ft.Image)
                     and isinstance(getattr(n, "src", None), str)
                     and mine and mine[0] in n.src)
    ok("tile appeared after upload", len(tiles) == 1, len(tiles))
    ok("tile carries the GIF badge", "GIF" in texts(p2), texts(p2))

    # زدن روی کاشی ⇒ فرستادنِ گیف
    cell = find(p2, lambda n: isinstance(n, ft.Container)
                and getattr(n, "on_click", None)
                and find(n, lambda k: isinstance(k, ft.Image)
                         and isinstance(getattr(k, "src", None), str)
                         and mine[0] in k.src))
    ok("tile is clickable", cell is not None)
    depth = len(page.dialogs)
    page.snacks.clear()
    cell.on_click(Ev(cell))
    await page.drain()
    await asyncio.sleep(0.5)
    lg = (await api.get_messages(chat_id))[-1]
    ok("tile sent the gif", lg.get("msg_type") == "gif"
       and lg.get("content") == mine[0], lg)
    ok("panel closed after sending", len(page.dialogs) == depth - 1,
       f"{len(page.dialogs)} vs {depth}")

    # نگه‌داشتن روی کاشی ⇒ «حذف از گیف‌ها»
    p3 = await open_panel()
    gd = find(p3, lambda n: isinstance(n, ft.GestureDetector)
              and getattr(n, "on_long_press", None) is not None)
    ok("tile has long-press", gd is not None)
    gd.on_long_press(Ev(gd))
    await page.drain()
    rm_menu = page.dialogs[-1]
    ok("remove item offered", t("remove_from_gifs") in texts(rm_menu),
       texts(rm_menu))
    rm = clickable(rm_menu, t("remove_from_gifs"))
    page.snacks.clear()
    rm.on_click(Ev(rm))
    await page.drain()
    await asyncio.sleep(0.3)
    ok("gif removed from the account", await api.list_gifs() == [])
    ok("gif_removed toast shown", t("gif_removed") in page.toasts(),
       page.toasts())
    ok("menu closed, panel is on top again",
       page.dialogs and page.dialogs[-1] is p3, len(page.dialogs))
    ok("panel fell back to the empty state",
       t("no_gifs") in texts(p3) and not find_all(
           p3, lambda n: isinstance(n, ft.Image)
           and isinstance(getattr(n, "src", None), str)
           and "/media/" in n.src))
    while page.dialogs:
        page.pop_dialog()

    # ── ۱۰) بازگشتِ فرستنده و سازگاری ─────────────
    sec("10) sender round-trip & i18n")
    await app.chat_page(page, chat_id)
    await page.drain()
    tx2 = texts(page.controls)
    ok("sent-as-gif caption visible after reload", VC2 in tx2)
    ok("image caption visible after reload", "عکسِ کپشن‌دار" in tx2)
    ok("file caption visible after reload", "گزارشِ ماه" in tx2)
    ok("gif tag still rendered", "GIF" in tx2)

    i18n.set_lang("en")
    keys = ("open_file", "opening", "open_failed", "save_to_downloads",
            "saved_to_downloads", "video", "audio", "gif", "caption",
            "caption_hint", "send_file", "send_as_gif", "send_as_gif_sub",
            "tap_to_open", "gifs", "my_gifs", "no_gifs", "no_gifs_sub",
            "add_to_gifs", "gif_added", "gif_exists", "remove_from_gifs",
            "gif_removed", "upload_gif", "delete", "delete_for_me",
            "delete_for_all", "delete_which", "delete_hint", "deleted")
    miss_en = [k for k in keys if t(k) == k]
    ok("all new keys exist in EN", not miss_en, miss_en)
    i18n.set_lang("fa")
    miss_fa = [k for k in keys if t(k) == k]
    ok("all new keys exist in FA", not miss_fa, miss_fa)

    await app.chat_page(page, chat_id)
    await page.drain()
    ok("chat re-renders in fa without error", bool(page.controls))

    # ── ۱۱) گیفِ ویدیویی (باگی که کاربر دید) ────────
    sec("11) mp4-backed GIF renders as a looping video")
    ok("flet_video plugin is available", ui.HAS_VIDEO)
    ok("is_video_src spots an mp4", ui.is_video_src("/media/a.mp4"))
    ok("is_video_src spots an mp4 with a query",
       ui.is_video_src("http://x/media/a.MP4?v=2"))
    ok("is_video_src leaves real gifs alone",
       not ui.is_video_src("/media/a.gif")
       and not ui.is_video_src("/media/a.webp")
       and not ui.is_video_src(None))

    vid_ctl = ui.loop_video("http://127.0.0.1:8000/media/x.mp4",
                            width=96, height=96, radius=12)
    ok("loop_video builds a control", vid_ctl is not None)
    inner_v = find(vid_ctl, lambda n: type(n).__name__ == "Video")
    ok("loop_video wraps a real Video control", inner_v is not None,
       type(vid_ctl).__name__)
    ok("gif video is muted", inner_v is not None and inner_v.muted is True)
    ok("gif video autoplays", inner_v is not None and inner_v.autoplay is True)
    ok("gif video loops",
       inner_v is not None and str(inner_v.playlist_mode).endswith("LOOP"),
       getattr(inner_v, "playlist_mode", None))
    ok("gif video has no player chrome",
       inner_v is not None and inner_v.controls is None)

    # کاشیِ «گیف‌های من» با یک mp4 باید ویدیو بسازه، نه Image شکسته
    tile_v = ui.media_tile("/media/clip.mp4", size=96, badge="GIF")
    ok("gif tile with an mp4 renders a video, not an Image",
       find(tile_v, lambda n: type(n).__name__ == "Video") is not None
       and find(tile_v, lambda n: isinstance(n, ft.Image)) is None)
    tile_i = ui.media_tile("/media/real.gif", size=96, badge="GIF")
    ok("gif tile with a real .gif still uses Image",
       find(tile_i, lambda n: isinstance(n, ft.Image)) is not None)

    # حبابِ چت: گیفی که پشتش mp4 است باید پخش بشه
    g_as_mp4 = await api.send_message(chat_id, up_mp4["url"], "gif",
                                      caption="گیفِ ویدیویی")
    ok("mp4 stored as a gif message", g_as_mp4.get("msg_type") == "gif",
       g_as_mp4)
    await app.chat_page(page, chat_id)
    await page.drain()
    vids = find_all(page.controls, lambda n: type(n).__name__ == "Video")
    ok("chat bubble plays the mp4-backed gif", len(vids) >= 1, len(vids))
    broken = find_all(page.controls, lambda n: isinstance(n, ft.Image)
                      and isinstance(getattr(n, "src", None), str)
                      and n.src.endswith(".mp4"))
    ok("no Image control is pointed at an mp4 any more", not broken,
       [n.src for n in broken])
    ok("GIF badge kept on the video bubble", "GIF" in texts(page.controls))

    # ── ۱۲) حذف: فقط برای من / برای همه ────────────
    sec("12) delete for me vs for everyone")
    d_msg = await api.send_message(chat_id, "این پیام پاک می‌شه", "text")
    d_mid = d_msg["message_id"]
    res_me = await api.delete_message(chat_id, d_mid, for_everyone=False)
    ok("delete-for-me reported", res_me.get("for_everyone") is False, res_me)
    mine_after = await api.get_messages(chat_id)
    ok("gone from my own history",
       not any(m["message_id"] == d_mid for m in mine_after))
    peer_after = await b.get_messages(chat_id)
    peer_row = next((m for m in peer_after if m["message_id"] == d_mid), None)
    ok("peer never notified — message intact for them",
       peer_row is not None and peer_row["content"] == "این پیام پاک می‌شه"
       and not peer_row["deleted"], peer_row)

    d2 = await api.send_message(chat_id, "این برای همه پاک می‌شه", "text")
    d2_mid = d2["message_id"]
    res_all = await api.delete_message(chat_id, d2_mid, for_everyone=True)
    ok("delete-for-everyone reported", res_all.get("for_everyone") is True,
       res_all)
    peer2 = await b.get_messages(chat_id)
    ok("no tombstone left for the peer (Telegram style)",
       not any(m["message_id"] == d2_mid for m in peer2)
       and not any(m["msg_type"] == "deleted" for m in peer2), len(peer2))
    await app.chat_page(page, chat_id)
    await page.drain()
    ok("no «پیام حذف شد» text in the chat view",
       t("msg_deleted") not in texts(page.controls))

    # ── پایان ─────────────────────────────────────
    try:
        await ws_b.disconnect()
    except Exception:
        pass
    try:
        await ws_client.disconnect()
    except Exception:
        pass
    for cl in (api, b):
        try:
            await cl._client.aclose()
        except Exception:
            pass
    print(f"\nPASS {PASS} FAIL {FAIL}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
