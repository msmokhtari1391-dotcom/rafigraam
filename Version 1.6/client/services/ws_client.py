"""
WebSocket client — اتصال ریل‌تایم به سرور، با اتصالِ مجددِ خودکار.

هر پیامِ سرور به همه‌ی «شنونده‌های سراسری» پاس داده می‌شه. علاوه بر
پیام‌های سرور، دو رویدادِ محلی هم تولید می‌شه:
  {"type": "ws_state", "online": bool}   وضعیتِ اتصال عوض شد
  {"type": "resync"}                     بعد از وصل‌شدنِ مجدد، لیست‌ها رو تازه کن

سه چیزی که این فایل را از یک «connect ساده» جدا می‌کند:

  • چند نشانی — نشانیِ HTTP (ورسل) ارتقای سوکت را با ۴۰۴ رد می‌کند، پس
    `config.ws_candidates()` به ترتیب امتحان می‌شود و نشانیِ برنده نگه
    داشته می‌شود تا اتصالِ بعدی از همان‌جا آغاز شود.
  • نبض — هر ۳۰ ثانیه `{"type":"ping"}` وقتی برنامه جلوست. بی این، هم
    کلادفلر سوکتِ بی‌کار را می‌بندد و هم قطعیِ خاموش (سوکتی که به‌ظاهر باز
    است ولی هیچ‌چیز از آن نمی‌آید) دقیقه‌ها دیده نمی‌شود.
  • مرورگر — پایتونِ وب (Pyodide) سوکتِ TCPِ خام ندارد، پس کلاینتِ asyncioی
    کتابخانه‌ی `websockets` در وب هرگز وصل نمی‌شود؛ `_JSWebSocket` همان
    قراردادِ کوچکِ لازم را روی `js.WebSocket` می‌سازد.
"""
import asyncio
import json
import logging
import sys
import time
from typing import Callable, Optional

import websockets
from websockets.exceptions import ConnectionClosed
from websockets.protocol import State

import config

log = logging.getLogger("rafigram.ws")

HB_EVERY = 30.0        # نبض وقتی برنامه «جلو» است
HB_IDLE = 120.0        # نبضِ کم‌جانِ پس‌زمینه — فقط تا سوکت بسته نشود
HB_DEAD = 80.0         # بیش از این خبری نیامد ⇒ سوکت مرده؛ از نو


def _in_browser() -> bool:
    """پایتون داخلِ مرورگر (Pyodide) اجرا می‌شود؟"""
    return sys.platform == "emscripten" or "pyodide" in sys.modules


class _JSWebSocket:
    """سوکتِ مرورگر با ظاهرِ `websockets`: `async for`, `send`, `close`, `state`.

    چرا لازم است: کلاینتِ asyncioی `websockets` سوکتِ TCP می‌خواهد و مرورگر
    چنین چیزی به Pyodide نمی‌دهد؛ پس در نسخه‌ی وب هر تلاشِ اتصال بی‌صدا
    می‌شکست و ریل‌تایم خاموش می‌ماند. اینجا فقط همان چند عضوی ساخته می‌شود
    که `_supervise` / `_listen` / `_open` مصرف می‌کنند.

    ارجاعِ کالبک‌ها را نگه می‌داریم: `create_proxy` بی‌ارجاعِ پایتونی آزاد
    می‌شود و رویدادها بی‌صدا قطع می‌شوند.
    """

    def __init__(self, url: str):
        import js
        from pyodide.ffi import create_proxy
        self.state = State.CONNECTING
        self._q: asyncio.Queue = asyncio.Queue()
        self._ready = asyncio.get_running_loop().create_future()
        self._proxies: list = []
        self._ws = js.WebSocket.new(url)

        def bind(name: str, fn) -> None:
            p = create_proxy(fn)
            self._proxies.append(p)
            setattr(self._ws, name, p)

        bind("onopen", self._on_open)
        bind("onmessage", self._on_msg)
        bind("onclose", self._on_close)
        bind("onerror", self._on_close)

    # ── رویدادهای مرورگر ────────────────────────────
    def _on_open(self, _e=None) -> None:
        self.state = State.OPEN
        if not self._ready.done():
            self._ready.set_result(True)

    def _on_msg(self, e) -> None:
        try:
            self._q.put_nowait(str(e.data))
        except Exception:
            pass

    def _on_close(self, _e=None) -> None:
        self.state = State.CLOSED
        if not self._ready.done():
            self._ready.set_exception(OSError("ws-closed"))
        try:
            self._q.put_nowait(None)          # پایانِ `async for`
        except Exception:
            pass


    # ── قراردادِ `websockets` ───────────────────────
    async def wait_open(self, timeout: float) -> None:
        try:
            await asyncio.wait_for(self._ready, timeout)
        except BaseException:
            await self.close()
            raise

    async def send(self, text: str) -> None:
        if self.state != State.OPEN:
            raise OSError("ws-not-open")
        self._ws.send(text)

    async def close(self, *_a, **_kw) -> None:
        self.state = State.CLOSED
        if not self._ready.done():
            # لغو، نه set_exception: وگرنه استثنایی می‌ماند که کسی
            # برنمی‌داردش و asyncio هشدارِ بی‌ربط چاپ می‌کند.
            self._ready.cancel()
        try:
            self._ws.close()
        except Exception:
            pass
        for p in self._proxies:
            try:
                p.destroy()
            except Exception:
                pass
        self._proxies.clear()
        try:
            self._q.put_nowait(None)
        except Exception:
            pass

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        raw = await self._q.get()
        if raw is None:
            raise StopAsyncIteration
        return raw


async def _dial(url: str, timeout: float = 10.0):
    """اتصال، با حاملِ درستِ همین سکو (مرورگر یا بومی)."""
    if _in_browser():
        sock = _JSWebSocket(url)
        await sock.wait_open(timeout)
        return sock
    return await websockets.connect(url, open_timeout=timeout)


class WSClient:
    def __init__(self):
        self._ws = None
        self._token: str | None = None
        self._on_message: Optional[Callable] = None
        self._on_connect: Optional[Callable] = None
        self._task: Optional[asyncio.Task] = None
        self._global_listeners: list[Callable] = []
        self._online = False
        self._stop = False
        self._had_session = False  # یک‌بار وصل شده بود؟ (برای resync)
        self._fg = True            # برنامه «جلو» است؟ (حضور)
        self._base: str | None = None   # نشانیِ برنده‌ی بارِ پیش
        self._last_rx = 0.0        # آخرین فریمِ رسیده (برای واچ‌داگ)
        self._nudge = None         # «همین حالا دوباره تلاش کن» (رویدادِ بیداری)

    # ── ثبت کالبک‌ها ────────────────────────────────
    def on_message(self, callback: Callable):
        self._on_message = callback

    def add_global_listener(self, callback: Callable):
        if callback not in self._global_listeners:
            self._global_listeners.append(callback)

    def remove_global_listener(self, callback: Callable):
        if callback in self._global_listeners:
            self._global_listeners.remove(callback)

    def on_connect(self, callback: Callable):
        self._on_connect = callback

    @property
    def online(self) -> bool:
        return self._online

    def wake(self) -> None:
        """«همین حالا دوباره تلاش کن» — خوابِ پس‌رفتِ نگهبان را می‌شکند.

        چرا لازم است: پس‌رفت تا ۱۵ ثانیه بالا می‌رود و این برای شبکه‌ی
        قطع‌ووصل درست است. ولی وقتی کاربر برنامه را از پس‌زمینه برمی‌گرداند،
        سوکت مرده و نگهبان شاید همان وسطِ خوابِ ۱۵ ثانیه باشد؛ آن‌وقت پیامِ
        تازه تا پانزده ثانیه بعد نمی‌رسید. این‌جا بیدارش می‌کنیم و پس‌رفت هم
        از صفر شروع می‌شود.
        """
        ev = self._nudge
        if ev is None:
            return
        try:
            ev.set()
        except Exception:
            pass

    # ── اتصال ───────────────────────────────────────
    async def connect(self, token: str):
        """اتصال به سرور؛ اگه قطع شد، خودش دوباره وصل می‌شه."""
        self._token = token
        self._stop = False
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._supervise())

    def _bases(self) -> list[str]:
        """نشانی‌های سوکت، با «برنده‌ی بارِ پیش» در صدر."""
        out = [b for b in config.ws_candidates() if b]
        if self._base in out:
            out.remove(self._base)
            out.insert(0, self._base)
        else:
            self._base = None      # آدرسِ سرور عوض شده ⇒ برنده‌ی کهنه بی‌ربط
        return out or [config.ws_base()]

    async def _supervise(self):
        """حلقه‌ی نگهبان: وصل شو، گوش بده، قطع شد صبر کن و باز وصل شو."""
        delay = 1.0
        # رویدادِ بیداری همین‌جا ساخته می‌شود، نه در `__init__`: آن‌جا هنوز
        # حلقه‌ی رویداد وجود ندارد و روی پایتونِ قدیمی‌تر خطا می‌داد.
        self._nudge = asyncio.Event()
        while not self._stop:
            for base in self._bases():
                if self._stop:
                    break
                try:
                    ws = await _dial(f"{base}/ws?token={self._token}", 10.0)
                except asyncio.CancelledError:
                    raise
                except Exception as ex:
                    log.info("سوکت وصل نشد: %s → %s", base, type(ex).__name__)
                    continue                      # نشانیِ بعدی
                self._base, self._ws = base, ws
                self._last_rx = time.monotonic()
                hb = asyncio.create_task(self._heartbeat(ws))
                try:
                    await self._set_online(True)
                    delay = 1.0
                    # اتصالِ تازه پیش‌فرض «جلو» حساب می‌شود؛ اگر برنامه در
                    # پس‌زمینه دوباره وصل شده، همان‌جا خبر می‌دهیم.
                    if not self._fg:
                        await self._say({"type": "presence",
                                         "state": "background"})
                    await self._listen(ws)
                except asyncio.CancelledError:
                    hb.cancel()
                    raise
                except Exception:
                    pass          # سرور خوابه یا شبکه قطعه — پایین باز تلاش
                hb.cancel()
                self._ws = None
                try:
                    await ws.close()
                except Exception:
                    pass
                await self._set_online(False)
                break             # وصل شده بودیم ⇒ چرخشِ نشانی لازم نیست
            if self._stop:
                break
            self._nudge.clear()
            try:                       # خواب — ولی `wake()` می‌تواند بشکندش
                await asyncio.wait_for(self._nudge.wait(), delay)
                delay = 1.0            # بیدارمان کردند ⇒ پس‌رفت از نو
            except asyncio.TimeoutError:
                delay = min(delay * 1.7, 15.0)

    async def _say(self, payload: dict) -> bool:
        """فرستادنِ بی‌سروصدا روی سوکتِ جاری."""
        ws = self._open()
        if ws is None:
            return False
        try:
            await ws.send(json.dumps(payload, ensure_ascii=False))
        except Exception:
            return False
        return True

    async def _heartbeat(self, ws):
        """نبضِ برنامه‌ای + واچ‌داگِ سوکتِ مرده.

        نبض «برنامه‌ای» است نه فریمِ Pingِ پروتکل: در مرورگر به فریمِ خام
        دسترسی نداریم، و سرور هم باید بداند این سوکت زنده و «جلو» است (حضور
        را از همین می‌سنجد). سرور با `{"type":"pong"}` جواب می‌دهد؛ سرورِ
        به‌روزنشده یک `{"error": …}`ِ بی‌آزار می‌دهد که همان هم نشانِ
        زنده‌بودنِ راه است.
        """
        while True:
            await asyncio.sleep(HB_EVERY if self._fg else HB_IDLE)
            try:
                await ws.send(json.dumps({"type": "ping"}))
            except Exception:
                return                # سوکت رفته؛ نگهبان خودش می‌بیندش
            # واچ‌داگ فقط وقتی معنی دارد که انتظارِ ترافیک داشته باشیم.
            if self._fg and (time.monotonic() - self._last_rx) > HB_DEAD:
                log.info("سوکت خاموش بود ⇒ بستن و اتصالِ تازه")
                try:
                    await ws.close()
                except Exception:
                    pass
                return

    async def _set_online(self, value: bool):
        if value == self._online:
            return
        self._online = value
        await self._emit({"type": "ws_state", "online": value})
        if value and self._had_session:
            # بعد از قطعیِ موقت، همه چیز باید از سرور تازه بشه
            await self._emit({"type": "resync"})
        if value:
            self._had_session = True

    async def _emit(self, data: dict):
        for listener in self._global_listeners[:]:
            try:
                await listener(data)
            except Exception:
                pass

    async def _listen(self, ws):
        try:
            async for raw in ws:
                self._last_rx = time.monotonic()
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                # پاسخِ نبض فقط نشانِ زنده‌بودن است؛ به رابط نمی‌رود. (سرورِ
                # به‌روزنشده به‌جای pong یک `{"error": …}` می‌دهد؛ آن هم بی‌اثر
                # است چون هیچ شنونده‌ای `error` را نمی‌خواند — و همین رسیدنش
                # `_last_rx` را تازه کرد، که کارِ اصلی است.)
                if data.get("type") == "pong":
                    continue

                if data.get("type") == "connected":
                    if self._on_connect:
                        try:
                            await self._on_connect(data)
                        except Exception:
                            pass
                    continue

                if self._on_message:
                    try:
                        await self._on_message(data)
                    except Exception:
                        pass
                await self._emit(data)
        except ConnectionClosed:
            pass

    # ── ارسال ───────────────────────────────────────
    def _open(self):
        """سوکتِ آماده‌ی ارسال، یا None. (شیمِ مرورگر هم `state` دارد.)"""
        ws = self._ws
        return ws if (ws is not None and ws.state == State.OPEN) else None

    async def send(self, chat_id: str, content: str, msg_type: str = "text",
                   reply_to: str | None = None, enc: bool = False,
                   caption: str | None = None) -> bool:
        ws = self._open()
        if ws is None:
            return False
        payload = {"chat_id": chat_id, "content": content, "msg_type": msg_type}
        if reply_to:
            payload["reply_to"] = reply_to
        if enc:
            payload["enc"] = True
        if caption:
            payload["caption"] = caption
        try:
            await ws.send(json.dumps(payload, ensure_ascii=False))
        except Exception:
            return False
        return True

    async def send_typing(self, chat_id: str) -> bool:
        """«داره می‌نویسه…» — ذخیره نمی‌شه، فقط برای بقیه‌ی اعضا پخش می‌شه."""
        ws = self._open()
        if ws is None or not chat_id:
            return False
        try:
            await ws.send(json.dumps({"type": "typing", "chat_id": chat_id}))
        except Exception:
            return False
        return True

    async def send_presence(self, foreground: bool) -> bool:
        """حضور: «جلو» یا «پس‌زمینه».

        سرور آنلاین‌بودن را فقط با همین می‌سنجد، پس با کوچک‌شدنِ برنامه فوراً
        آفلاین می‌شویم و با برگشتن، آنلاین — بی‌آنکه منتظرِ مهلتِ ۷۰ ثانیه‌ای
        `last_seen_at` بمانیم.
        """
        self._fg = bool(foreground)
        ws = self._open()
        if ws is None:
            return False
        try:
            await ws.send(json.dumps(
                {"type": "presence",
                 "state": "foreground" if foreground else "background"}))
        except Exception:
            return False
        return True

    async def disconnect(self):
        """بستنِ کاملِ اتصال (خروج از حساب)."""
        self._stop = True
        self._had_session = False
        task, ws = self._task, self._ws
        self._task, self._ws = None, None
        if task:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        if ws:
            try:
                await ws.close()
            except Exception:
                pass
        self._online = False


# singleton
ws_client = WSClient()
