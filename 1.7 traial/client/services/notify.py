"""
اعلانِ سیستمی — «پیامِ تازه» را بیرونِ برنامه هم نشان بده.

سه سکو، سه راه، یک قرارداد:

  • مرورگر (و PWAِ نصب‌شده روی اندروید و آیفون) — `Notification`ِ خودِ
    مرورگر. اجازه یک‌بار پرسیده می‌شود و از آن پس، تا وقتی برگه زنده است
    (حتی پشتِ برگه‌های دیگر یا پنجره‌ی کوچک‌شده) اعلانِ واقعیِ سیستم می‌آید.
  • ویندوز — توستِ واقعیِ ویندوز، با یک PowerShellِ بی‌پنجره که XMLِ WinRT را
    به `ToastNotificationManager` می‌دهد. هیچ بسته‌ای اضافه نمی‌شود:
    `winotify`/`plyer` در وابستگی‌ها نیستند و آوردنشان هم حجمِ exe را بالا
    می‌برد هم یک وابستگیِ تازه‌ی بی‌ضرورت است.
  • برنامه‌ی اندروید — Flet 0.86 افزونه‌ی اعلانِ بومی ندارد، پس نوارِ
    درون‌برنامه‌ای (هدزآپ، بالای صفحه، مثلِ تلگرام) می‌نشیند و با ضربه روی آن
    همان چت باز می‌شود. اعلانِ *پس‌زمینه* روی APK بی افزونه‌ی فلاتر شدنی
    نیست؛ نسخه‌ی وبِ نصب‌شده روی گوشی همان را دارد.

قاعده‌ی خفه‌کردن (مثلِ تلگرام): پیامِ خودم هرگز، و چتی که همین حالا جلوی چشم
است هم نه. بقیه بله — با نامِ فرستنده، آواتارش (اگر باشد) و تکه‌ای از متن.
"""
import asyncio
import os
import subprocess
import sys
import time

import flet as ft

import config
import geo
import prefs
import ui
from i18n import t
from services.ws_client import ws_client
from ui import BR, OFF, P, fs, op

_st = {
    "page": None,
    "fg": True,          # برنامه جلوی چشم است؟
    "chat": None,        # چتِ بازِ همین حالا (اعلانش خفه است)
    "me": "",            # شناسه‌ی خودم (پیامِ خودم اعلان ندارد)
    "web": False,
    "granted": False,    # مرورگر اجازه داده؟
    "asked": False,
    "open": None,        # کالبکِ «این چت را باز کن»
    "last": 0.0,         # زمانِ آخرین اعلان — گلوگاهِ ضدِ سیل
    "wrap": None,        # جعبه‌ی نوارِ درون‌برنامه‌ای روی صفحه
    "hide": 0.0,         # نوار تا این لحظه بماند
    "rows": {},          # chat_id → (عنوان, آواتار, گروه؟)
    "snd": None,         # سرویسِ صدا — زنگِ اعلان
    "hap": None,         # سرویسِ لرزش (اندروید/آی‌اواس)
    "back": None,        # کالبکِ «برنامه از پس‌زمینه برگشت»
}

CHIME = "sounds/ding.wav"    # ساخته‌شده با `.tmp/mk_chime.py` — ۰٫۴۶ ثانیه

# متنِ کوتاهِ اعلان برای پیامِ غیرِمتنی
_KIND_KEY = {"image": "photo", "video": "video", "voice": "voice_message",
             "file": "file", "gif": "gif", "sticker": "sticker",
             "location": "att_location"}
_GAP = 1.2               # کمینه‌ی فاصله‌ی دو اعلان (ثانیه)


# ── قرارداد با بقیه‌ی برنامه ─────────────────────────
def attach(page) -> None:
    """یک‌بار در `main()`. از این پس هر پیامِ سوکت از این‌جا می‌گذرد."""
    _st["page"] = page
    try:
        _st["web"] = bool(getattr(page, "web", False))
    except Exception:
        _st["web"] = False
    _alerts()
    ws_client.add_global_listener(_on_ws)
    if _st["web"]:
        page.run_task(_web_ask)


def _alerts() -> None:
    """زنگ و لرزش — دو سرویسِ Flet که با *ساخته‌شدن* خودشان را ثبت می‌کنند.

    همین‌جا و یک‌بار ساخته می‌شوند، نه سرِ هر اعلان: ثبتِ سرویس یک
    رفت‌وبرگشت با لایه‌ی فلاتر است و اگر هر بار انجام شود، زنگ با تأخیرِ
    نامرتب — یا وسطِ سیلِ پیام هرگز — شنیده می‌شود. ارجاعشان در `_st` می‌ماند
    (متغیرِ ماژول) وگرنه زباله‌روب می‌بردشان و سرویس با خودش می‌رفت.

    نبودشان هیچ‌چیز را نمی‌شکند: روی رومیزی لرزشی نیست و اگر افزونه‌ی صدا
    نصب نباشد، اعلان بی‌صدا می‌آید.
    """
    try:
        import flet_audio as fa
        _st["snd"] = fa.Audio(src=CHIME, volume=1.0,
                              release_mode=fa.ReleaseMode.STOP)
    except Exception:
        _st["snd"] = None
    try:
        _st["hap"] = ft.HapticFeedback()
    except Exception:
        _st["hap"] = None


async def _alert() -> None:
    """زنگ + لرزشِ اعلان. هر دو دلبخواهی‌اند و بی‌صدا شکست می‌خورند.

    چرا این‌جا و نه سرِ هر سه راهِ اعلان؟ چون توستِ ویندوز و اعلانِ مرورگر
    صدای *خودشان* را دارند و زنگِ ما می‌شد صدای دوم. تنها نوارِ
    درون‌برنامه‌ای است که ساکت می‌آمد — همان که کاربر می‌گفت «هیچ نوتیفی
    نمی‌فرسته»، چون روی گوشی جز همین نوار چیزی نیست.
    """
    if prefs.flag("sound"):
        snd = _st["snd"]
        if snd is not None:
            try:
                await snd.play(0)
            except Exception:
                pass
    if prefs.flag("vibrate"):
        hap = _st["hap"]
        if hap is not None:
            try:
                await hap.medium_impact()
            except Exception:
                pass


def set_foreground(fg: bool) -> None:
    """برنامه جلو آمد یا رفت — و اگر برگشت، جاافتاده‌ها را بگیر.

    `fg=False` سه معنا دارد: کوچک شدنِ پنجره، رفتن به پس‌زمینه‌ی اندروید، و
    (روی رومیزی) حتی از دست دادنِ تمرکز؛ چون توستِ ویندوز *تنها* در این حالت
    فرستاده می‌شود و پیش از این هرگز فرستاده نمی‌شد.
    """
    was = _st["fg"]
    _st["fg"] = bool(fg)
    if fg and not was:
        fn, page = _st["back"], _st["page"]
        if fn and page is not None:
            try:
                page.run_task(fn)
            except Exception:
                pass


def on_resume(fn) -> None:
    """کالبکِ «برنامه از پس‌زمینه برگشت» — برای گرفتنِ پیام‌های جاافتاده."""
    _st["back"] = fn


def set_active_chat(chat_id) -> None:
    """چتِ بازِ جلوی چشم — اعلانِ همین یکی نمی‌آید."""
    _st["chat"] = chat_id or None


def set_me(user_id: str) -> None:
    _st["me"] = str(user_id or "")


def on_open(fn) -> None:
    """کالبکِ ضربه روی نوار: `fn(chat_id)`."""
    _st["open"] = fn


def set_chats(chats) -> None:
    """کشِ عنوان و آواتارِ چت‌ها — اعلان از همین‌جا آواتار برمی‌دارد.

    سوکت در فریمِ پیام آواتار نمی‌فرستد و خواندنِ کشِ دیسکی برای هر پیام یک
    رفت‌وبرگشتِ اضافه است، پس لیستِ چت‌ها که *همین حالا* در حافظه‌ی صفحه‌ی
    فهرست است را همان‌جا به ما می‌دهد.
    """
    try:
        _st["rows"] = {
            c.get("chat_id"): (c.get("title") or "",
                               c.get("avatar_url") or "",
                               bool(c.get("is_group"))) for c in (chats or [])}
    except Exception:
        pass

def _snippet(data: dict) -> str:
    """یک خطِ کوتاه از پیام — همان که در اعلان دیده می‌شود."""
    if data.get("enc"):
        return t("enc_locked")
    kind = (data.get("msg_type") or "text").lower()
    if kind in _KIND_KEY:
        cap = " ".join((data.get("caption") or "").split())
        head = t(_KIND_KEY[kind])
        return f"{head} — {cap}" if cap else head
    body = " ".join((data.get("content") or "").split())
    if body and geo.parse(data.get("content") or ""):
        return f"📍 {t('att_location')}"     # نه لینکِ بلندِ گوگل‌مپ
    return body[:140] if body else t("new_message")


def _icon_url(raw: str):
    if not raw:
        return None
    if raw.startswith(("http://", "https://")):
        return raw
    return f"{config.api_base()}/{raw.lstrip('/')}"


async def _on_ws(data: dict) -> None:
    """شنونده‌ی سراسریِ سوکت — تنها دروازه‌ی اعلان."""
    if not isinstance(data, dict) or data.get("type") != "message":
        return
    if not prefs.flag("notify"):
        return
    sender = str(data.get("sender_id") or "")
    chat_id = data.get("chat_id")
    if not chat_id or (sender and sender == _st["me"]):
        return                              # پیامِ خودم اعلان ندارد
    if _st["fg"] and chat_id == _st["chat"]:
        return                              # همین چت جلوی چشم است
    now = time.monotonic()
    if now - _st["last"] < _GAP:
        return                              # سیلِ پیام ⇒ فقط اولی
    _st["last"] = now
    title, avatar, is_group = _st["rows"].get(chat_id, ("", "", False))
    who = (data.get("sender_name") or "").strip()
    body = _snippet(data)
    if is_group and title:
        head, body = title, (f"{who}: {body}" if who else body)
    else:
        head = who or title or t("app_name")
    icon = _icon_url(avatar)
    if _st["web"]:
        if await _web_fire(head, body, icon, str(chat_id)):
            return
    elif sys.platform == "win32":
        # روی ویندوز اگر برنامه در پس‌زمینه است، پنجره کوچک شده، یا کاربر در
        # صفحه/چتِ دیگری‌ست، توست سیستم ویندوز هم فرستاده می‌شود.
        if not _st["fg"] or _st["chat"] != chat_id:
            _win_toast(head, body)
    _banner(head, body, icon, chat_id)

# ── مرورگر ──────────────────────────────────────────
def _web_hidden() -> bool:
    """برگه پشتِ صحنه است؟ چرخه‌ی عمرِ Flet در وب همیشه دقیق نیست، پس خودِ
    `document.hidden` را می‌پرسیم و فقط اگر نشد به حدسِ خودمان برمی‌گردیم."""
    try:
        import js
        return bool(js.document.hidden)
    except Exception:
        return not _st["fg"]


async def _web_ask() -> None:
    """اجازه‌ی اعلانِ مرورگر — یک‌بار."""
    if _st["asked"]:
        return
    _st["asked"] = True
    try:
        import js
        api = getattr(js.window, "Notification", None)
        if api is None:
            return                       # مرورگرِ بی‌اعلان
        now = str(api.permission)
        if now == "granted":
            _st["granted"] = True
            return
        if now == "denied":
            return
        _st["granted"] = (str(await api.requestPermission()) == "granted")
    except Exception:
        _st["granted"] = False


def ask(page=None) -> None:
    """دوباره بپرس — از دلِ یک کلیکِ واقعیِ کاربر.

    سافاری (و آیفون) اجازه‌ی اعلان را فقط در پاسخِ ژستِ کاربر می‌دهد، پس
    پرسشِ زمانِ راه‌افتادن آن‌جا بی‌اثر است؛ کلیدِ «اعلان» در تنظیمات این را
    صدا می‌زند و همان‌جا پنجره‌ی اجازه می‌آید.
    """
    _st["asked"] = False
    p = page or _st["page"]
    if _st["web"] and p is not None:
        try:
            p.run_task(_web_ask)
        except Exception:
            pass

async def _web_fire(title, body, icon, tag) -> bool:
    """اعلانِ واقعیِ مرورگر. `False` = نشد ⇒ نوارِ درون‌برنامه‌ای.

    دو راه، به همین ترتیب: نخست `showNotification`ِ سرویس‌ورکر — کرومِ
    اندروید سازنده‌ی `new Notification` را ممنوع کرده و استثنا می‌دهد، و
    خروجیِ `flet build web` همیشه سرویس‌ورکر دارد. اگر نبود، سازنده‌ی
    کلاسیک که در فایرفاکس و سافاریِ رومیزی کار می‌کند.
    """
    if not _st["granted"] or not _web_hidden():
        return False                     # برگه جلوی چشم ⇒ نوارِ خودمان
    try:
        import js
        from pyodide.ffi import to_js
        opt = {"body": body, "tag": tag, "renotify": True}
        if icon:
            opt["icon"], opt["badge"] = icon, icon
        arg = to_js(opt, dict_converter=js.Object.fromEntries)
    except Exception:
        return False
    try:
        reg = await js.navigator.serviceWorker.ready
        await reg.showNotification(title, arg)
        return True
    except Exception:
        pass
    try:
        js.Notification.new(title, arg)
        return True
    except Exception:
        return False


# ── ویندوز ──────────────────────────────────────────
# متن از راهِ env می‌رود، نه داخلِ خودِ اسکریپت: پیامِ کاربر می‌تواند کوتیشن و
# `$` و خطِ تازه داشته باشد و چسباندنش در متنِ اسکریپت یعنی تزریقِ فرمان.
_PS = (
    "$ErrorActionPreference='Stop';"
    "[Windows.UI.Notifications.ToastNotificationManager,"
    "Windows.UI.Notifications,ContentType=WindowsRuntime]>$null;"
    "$tpl=[Windows.UI.Notifications.ToastNotificationManager]::"
    "GetTemplateContent("
    "[Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
    "$tx=$tpl.GetElementsByTagName('text');"
    "$tx.Item(0).AppendChild($tpl.CreateTextNode($env:RG_TITLE))>$null;"
    "$tx.Item(1).AppendChild($tpl.CreateTextNode($env:RG_BODY))>$null;"
    "$n=[Windows.UI.Notifications.ToastNotification]::new($tpl);"
    "[Windows.UI.Notifications.ToastNotificationManager]::"
    "CreateToastNotifier($env:RG_AID).Show($n)"
)
# AUMIDِ خودِ پاورشل. ویندوز از فرستنده‌ی ثبت‌نشده توست نمی‌پذیرد و ما در
# رجیستری برنامه‌ای ثبت نکرده‌ایم؛ این شناسه روی ویندوز ۱۰/۱۱ همیشه هست.
_AUMID = ("{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}"
          "\\WindowsPowerShell\\v1.0\\powershell.exe")

def _win_toast(title: str, body: str) -> bool:
    """توستِ ویندوز. `False` = نشد ⇒ نوارِ درون‌برنامه‌ای."""
    try:
        env = dict(os.environ)
        env.update({"RG_TITLE": (title or " ")[:120],
                    "RG_BODY": (body or " ")[:220], "RG_AID": _AUMID})
        flags = 0x00000008 | 0x08000000     # DETACHED_PROCESS | NO_WINDOW
        subprocess.Popen(
            ["powershell", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-Command", _PS],
            env=env, creationflags=flags, close_fds=True,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


# ── نوارِ درون‌برنامه‌ای (هدزآپ) ────────────────────────
def _dismiss(e=None) -> None:
    page, wrap = _st["page"], _st["wrap"]
    _st["wrap"] = None
    if page is None or wrap is None:
        return
    try:
        page.overlay.remove(wrap)
        page.update()
    except Exception:
        pass


async def _auto_hide() -> None:
    """خودش می‌رود — ولی اگر وسطش نوارِ تازه‌تری آمده باشد، دست نمی‌زند."""
    while True:
        left = _st["hide"] - time.monotonic()
        if left <= 0:
            break
        await asyncio.sleep(min(left, 0.5))
    _dismiss()

def _banner(title, body, icon, chat_id) -> None:
    """نوارِ بالای صفحه — همان‌جا که تلگرام هدزآپ را نشان می‌دهد.

    با مختصاتِ صریح (`top/left/right`) در Overlay می‌نشیند تا *فقط* جای خودش
    را بگیرد؛ جعبه‌ی تمام‌صفحه ضربه‌های زیرِ خودش را می‌خورد و رابط قفل به نظر
    می‌رسد.
    """
    page = _st["page"]
    if page is None:
        return
    _dismiss()

    def _go(e=None):
        _dismiss()
        fn = _st["open"]
        if fn and chat_id:
            try:
                fn(chat_id)
            except Exception:
                pass

    card = ui.glass(
        ft.Row([
            ui.avatar(title or "?", size=40, url=icon),
            ft.Column([
                ft.Text(title or t("app_name"), size=fs(13), color=P["txt"],
                        font_family=ui.FONT_BOLD, max_lines=1,
                        overflow=ft.TextOverflow.ELLIPSIS),
                ft.Text(body, size=fs(11), color=P["sub"], max_lines=2,
                        overflow=ft.TextOverflow.ELLIPSIS)],
                spacing=1, expand=True),
            ui.vic("bell", 15, op(0.45))], spacing=10),
        radius=20, pad=10, sigma=28)
    wrap = ft.Container(
        card, top=10, left=12, right=12, ink=True, on_click=_go,
        border_radius=BR(20, 20, 20, 20),
        shadow=ft.BoxShadow(blur_radius=26, spread_radius=-6,
                            color=op(0.45, "#000000"), offset=OFF(0, 8)))
    _st["wrap"] = wrap
    _st["hide"] = time.monotonic() + 4.5
    try:
        page.overlay.append(wrap)
        page.update()
        page.run_task(_auto_hide)
        page.run_task(_alert)      # زنگ و لرزش — نوار تنها اعلانِ ساکت بود
    except Exception:
        _st["wrap"] = None
