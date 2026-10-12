"""
وصله‌ی PWA روی خروجیِ `flet build web`.

فلاتر یک `index.html` و `manifest.json` کم‌جان می‌سازد؛ این ابزار آن را به
یک PWA کاملِ فارسی/راست‌به‌چپ تبدیل می‌کند:

  • آیکون‌های ۱۹۲/۵۱۲ (معمولی و maskable) و `apple-touch-icon` ۱۸۰
  • متاهای iOS: `apple-mobile-web-app-capable`, `...-status-bar-style`,
    `...-title` ⇒ «افزودن به صفحه‌ی خانه» در سافاری تمام‌صفحه باز می‌شود
  • `viewport-fit=cover` + `theme-color` تاریک ⇒ بی نوارِ سفید و بی جهش
  • `manifest.json` با نام و جهتِ فارسی، `display: standalone`, `dir: rtl`،
    و `id`/`start_url`/`scope` **مطلق** (`/`) — نسبی‌بودنشان (`.`) همان چیزی
    بود که نشانکِ روی صفحه‌ی خانه‌ی آیفون را به نشانیِ خراب می‌بست
  • سرویس‌ورکر: نه ثبت می‌شود و نه ناوبری می‌کند. ورکرِ خودکُشِ فلاتر بعد از
    `unregister` روی هر پنجره `client.navigate()` می‌زد و در حالتِ standalone
    آیفون پنجره را می‌پراند («Safari can't open the page» با نشانیِ `data:`)
  • `offline.html`

  python tools/pwa.py build/web
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC_ICON = ROOT / "client" / "assets" / "icon.png"
BG = "#0b0b10"
NAME = "رفیگرام"
NAME_EN = "Rafigram"
DESC = "مسنجرِ خصوصیِ رفقا"

SIZES = {"icons/Icon-192.png": 192, "icons/Icon-512.png": 512,
         "icons/Icon-maskable-192.png": 192,
         "icons/Icon-maskable-512.png": 512,
         "icons/apple-touch-icon.png": 180}

VIEWPORT = ("width=device-width, initial-scale=1.0, maximum-scale=1.0, "
            "user-scalable=no, viewport-fit=cover")

# ⚠️ نشانی‌ها همه با «/» شروع می‌شوند، نه نسبی. سافاریِ آیفون هنگامِ «افزودن
# به صفحه‌ی خانه» نشانیِ شروع را همان‌جا در نشانکِ روی صفحه *منجمد* می‌کند؛ اگر
# نسبی باشد و صفحه از مسیرِ دیگری باز شده باشد، چیزی که ذخیره می‌شود ممکن است
# نشانیِ درستِ سایت نباشد و باز کردنش خطای «نمی‌توانم به سرور وصل شوم» بدهد.
# با «/» هیچ ابهامی نمی‌ماند: همیشه ریشه‌ی دامنه.
HEAD = """  <!-- PWA (iOS «افزودن به صفحه‌ی خانه») -->
  <meta name="viewport" content="{vp}">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
  <meta name="apple-mobile-web-app-title" content="{name}">
  <meta name="application-name" content="{name}">
  <meta name="theme-color" content="{bg}">
  <meta name="color-scheme" content="dark">
  <meta name="description" content="{desc}">
  <link rel="apple-touch-icon" href="/icons/apple-touch-icon.png">
  <link rel="apple-touch-icon" sizes="180x180" href="/icons/apple-touch-icon.png">
  <link rel="icon" type="image/png" sizes="192x192" href="/icons/Icon-192.png">
  <link rel="icon" type="image/png" sizes="512x512" href="/icons/Icon-512.png">
  <link rel="manifest" href="/manifest.json">
"""
MANIFEST = {
    "name": f"{NAME} — {DESC}",
    "short_name": NAME,
    # `id` هویتِ پایدارِ برنامه است (کروم/سافاریِ تازه). بی آن، هر تغییرِ
    # `start_url` مثلِ «یک برنامه‌ی تازه» دیده می‌شود.
    "id": "/",
    "start_url": "/",
    "scope": "/",
    "display": "standalone",
    "display_override": ["standalone", "minimal-ui"],
    "orientation": "portrait",
    "background_color": BG,
    "theme_color": BG,
    "dir": "rtl",
    "lang": "fa",
    "description": DESC,
    "categories": ["social", "communication"],
    "prefer_related_applications": False,
    "icons": [
        {"src": "/icons/Icon-192.png", "sizes": "192x192",
         "type": "image/png", "purpose": "any"},
        {"src": "/icons/Icon-512.png", "sizes": "512x512",
         "type": "image/png", "purpose": "any"},
        {"src": "/icons/Icon-maskable-192.png", "sizes": "192x192",
         "type": "image/png", "purpose": "maskable"},
        {"src": "/icons/Icon-maskable-512.png", "sizes": "512x512",
         "type": "image/png", "purpose": "maskable"},
    ],
}

OFFLINE = f"""<!DOCTYPE html>
<html lang="fa" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1,
 viewport-fit=cover">
<title>{NAME}</title>
<style>
 html,body{{height:100%;margin:0;background:{BG};color:#e9e9f2;
  font-family:system-ui,-apple-system,"Segoe UI",Tahoma,sans-serif;
  display:flex;align-items:center;justify-content:center;text-align:center}}
 p{{opacity:.7;font-size:14px}}
</style></head><body><div>
<h2>{NAME}</h2><p>اتصالِ اینترنت نیست. وصل شو و صفحه را تازه کن.</p>
</div></body></html>
"""

# نگهبانِ بالا آمدن: اگر بعد از ۲۵ ثانیه هنوز چیزی رنگ نشده، جای صفحه‌ی
# سفیدِ خالی یک پیامِ فارسی + دکمه‌ی «تلاشِ دوباره» می‌آید. روی سافاریِ آیفون
# و نتِ کند، کاربر تا این را نداشت فقط سفیدی می‌دید و فکر می‌کرد خراب است.
BOOT_WATCH = """  <style id="boot-note-style">
    #boot-note {
      position: fixed; inset: auto 0 8vh 0; display: none; text-align: center;
      font-family: system-ui, -apple-system, "Segoe UI", Tahoma, sans-serif;
      color: #e9e9f2; padding: 0 24px; z-index: 9999;
    }
    #boot-note p { font-size: 14px; opacity: .78; margin: 0 0 12px }
    #boot-note button {
      font: inherit; font-size: 14px; color: #fff; border: 0; cursor: pointer;
      background: #E9518F; border-radius: 12px; padding: 10px 22px;
    }
  </style>
  <div id="boot-note" dir="rtl">
    <p>بالا آمدنِ برنامه طول کشید — اینترنتت را بسنج و دوباره تلاش کن.</p>
    <button onclick="location.reload()">تلاشِ دوباره</button>
  </div>
  <script id="boot-note-script">
    (function () {
      // «بالا آمد» را از خودِ پایتون می‌فهمیم، نه از اسپلش: فلت ۰.۸۶
      // `removeSplashFromWeb` را صدا نمی‌زند، پس اگر فقط به آن بند بودیم
      // این پیام روی برنامه‌ی سالم هم می‌ماند (دیده شد).
      function ready() {
        return !!(window.__fletPythonReady || !document.getElementById("splash"));
      }
      function done() {
        clearTimeout(shown);
        clearInterval(watch);
        document.getElementById("boot-note")?.remove();
      }
      var shown = setTimeout(function () {
        if (ready()) return done();
        var n = document.getElementById("boot-note");
        if (n) n.style.display = "block";
      }, 25000);
      var watch = setInterval(function () { if (ready()) done(); }, 500);
      var orig = window.removeSplashFromWeb;
      window.removeSplashFromWeb = function () {
        done();
        if (typeof orig === "function") orig();
      };
    })();
  </script>
"""

# نشانه‌ی «پایتون بالا آمد» را خودمان داخلِ `python.js` می‌گذاریم؛ همان‌جایی
# که فلت لاگِ «Python worker initialized» را می‌زند. نگهبانِ بالا به همین
# پرچم نگاه می‌کند.
PY_READY_SRC = 'console.log(`Python worker initialized: ${appId}`);'
PY_READY_DST = (PY_READY_SRC +
                '\n        window.__fletPythonReady = true;'
                '\n        window.dispatchEvent(new Event("flet-python-ready"));')

# سرویس‌ورکر: خودِ فلاتر یک ورکرِ «خودکُش» می‌فرستد که همین که فعال شد خودش را
# از ثبت درمی‌آورد و بعد به هر پنجره‌ی بازی `client.navigate(client.url)` می‌زند.
# هیچ چیزی را کش نمی‌کند (کشِ ما از هدرهای `vercel.json` می‌آید)، پس سودش صفر
# است — ولی همان `navigate` در حالتِ standaloneِ آیفون یک ناوبریِ اجباری است که
# پنجره‌ی برنامه‌ی روی صفحه‌ی خانه را می‌تواند به نشانیِ بی‌ربط ببرد. پس اصلاً
# ثبتش نمی‌کنیم و هر ثبتِ قدیمی را هم پاک می‌کنیم.
SW_OFF_SRC = re.compile(r'\s*serviceWorkerSettings\s*:\s*\{.*?\}\s*,?',
                        re.S)
SW_KILL = """  <script id="sw-kill">
    // هر سرویس‌ورکرِ به‌جا‌مانده از نسخه‌های پیشین را از ثبت درمی‌آورد. روی
    // آیفون، ورکرِ کهنه در حالتِ «افزوده به صفحه‌ی خانه» می‌توانست پنجره را
    // خودسرانه جابه‌جا کند؛ نتیجه‌اش خطای «نمی‌توانم به سرور وصل شوم» بود.
    if ("serviceWorker" in navigator) {
      navigator.serviceWorker.getRegistrations()
        .then(function (rs) {
          rs.forEach(function (r) {
            // ⚠️ عمداً «همه» را پاک نمی‌کند: ورکرِ اعلانِ خودمان
            // (`rafigram-push-sw.js`) هم یک ثبتِ سرویس‌ورکر است و پاک‌کردنش
            // یعنی اعلانِ مرورگر هرگز نیامدن. پس فقط ورکرِ فلاتر می‌رود.
            var w = r.active || r.installing || r.waiting;
            var u = (w && w.scriptURL) || "";
            if (u.indexOf("rafigram-push-sw") >= 0) { return; }
            if (u === "" || u.indexOf("service_worker") >= 0
                || u.indexOf("/sw.js") >= 0) { r.unregister(); }
          });
        })
        .catch(function () {});
    }
  </script>
"""

# ── سرویس‌ورکرِ خنثی («کلیدِ کشتن») ─────────────────────────────────
# برداشتنِ *ثبت* از `flutter_bootstrap.js` فقط بازدیدکننده‌های تازه را نجات
# می‌دهد. گوشی‌ای که ورکرِ کهنه رویش ثبت شده هنوز همان ورکر را دارد و مرورگر
# خودش دوره‌ای فایلِ `/flutter_service_worker.js` را از سرور می‌گیرد — پس
# چاره این است که سرِ همان نشانی یک ورکرِ بی‌آزار بگذاریم که خودش را از ثبت
# در می‌آورد و *ناوبری نمی‌کند*.
#
# چرا اسکریپتِ `sw-kill` بالا کافی نیست: آن باید صفحه بالا بیاید تا اجرا شود،
# و دقیقاً مشکلِ کاربر این است که صفحه بالا نمی‌آید («Safari can't open the
# page»). ورکرِ کهنه پیش از هر کدِ صفحه فعال می‌شود. پس نجات باید از خودِ
# فایلِ ورکر بیاید، نه از صفحه.
#
# هدرِ این فایل در `web/vercel.json` روی `max-age=0, must-revalidate` است،
# پس همین بسته که دیپلوی شود گوشی‌ها نسخه‌ی خنثی را می‌گیرند.
SW_DEAD = """'use strict';
// ورکرِ خنثی — جای ورکرِ خودکُشِ فلاتر.
//
// آن نسخه بعد از unregister روی هر پنجره‌ی باز `client.navigate(client.url)`
// می‌زد. در پنجره‌ی «افزوده‌شده به صفحه‌ی خانه»ی آیفون (standalone، بی نوارِ
// نشانی) همان یک خط پنجره را به نشانی‌ای می‌بُرد که کاربر هیچ راهی برای
// برگشتن از آن ندارد؛ نشانه‌اش نشانیِ `data:` در نوارِ سافاری و پیامِ
// «نمی‌توانم به سرور وصل شوم» بود.
//
// این نسخه همان پاک‌سازی را می‌کند، بی ناوبری.
self.addEventListener('install', function () {
  self.skipWaiting();
});

self.addEventListener('activate', function (event) {
  event.waitUntil((async function () {
    try {
      const keys = await caches.keys();
      await Promise.all(keys.map(function (k) { return caches.delete(k); }));
    } catch (e) {}
    try {
      await self.registration.unregister();
    } catch (e) {}
    // ⚠️ اینجا عمداً هیچ `clients.matchAll()`/`client.navigate()`ی نیست.
  })());
});
"""

# ── ورکرِ اعلان (Web Push) — این یکی *زنده* است ─────────────────────
# دو ورکرِ جدا، عمداً:
#   • `flutter_service_worker.js` ← خنثی می‌شود (بالا). ناوبریِ اجباری‌اش
#     پنجره‌ی standaloneِ آیفون را می‌شکست و هیچ سودی هم نداشت.
#   • `rafigram-push-sw.js`       ← همین. هیچ فایلی کش نمی‌کند، هیچ‌جا
#     ناوبری نمی‌کند، و تنها کارش گوش‌دادن به `push` است.
#
# چرا اصلاً ورکر لازم است: اعلانِ مرورگر با برگه‌ی *بسته* فقط از راهِ
# سرویس‌ورکر می‌آید — `new Notification` نیاز به صفحه‌ی باز دارد و کرومِ
# اندروید سازنده‌اش را کلاً ممنوع کرده. یعنی «نوتیف عینِ تلگرام» در وب بی
# این فایل ممکن نیست.
#
# چرا نامِ فایل «service_worker» ندارد: `neutralize_sw` و سنجه‌ی ناوبری با
# الگوی `*service_worker*.js` می‌گردند و این فایل نباید خنثی شود.
PUSH_SW = "rafigram-push-sw.js"
SW_PUSH = """'use strict';
// ورکرِ اعلانِ رفیگرام — تنها راهِ اعلان وقتی برگه بسته است.
//
// بارِ رسیده همان چیزی است که `server/app/push.py::_web_fanout` می‌فرستد:
//   {chat_id, title, body, icon, sender_id, message_id, group}
// رمزگشایی کارِ خودِ مرورگر است (RFC 8291)، پس این‌جا فقط JSON است.

self.addEventListener('install', function () {
  self.skipWaiting();
});

self.addEventListener('activate', function (event) {
  // بی `claim`، ورکرِ تازه تا بستنِ همه‌ی برگه‌ها بی‌کار می‌ماند و اعلانِ
  // نخست گم می‌شد.
  event.waitUntil(self.clients.claim());
});

self.addEventListener('push', function (event) {
  var d = {};
  try { d = event.data ? event.data.json() : {}; } catch (e) { d = {}; }
  var title = d.title || 'رفیگرام';
  var body = d.body || 'پیامِ تازه';
  var chat = String(d.chat_id || '');
  var opt = {
    body: body,
    // `tag` یعنی پیامِ دومِ همان چت روی اعلانِ اولی می‌نشیند (مثلِ تلگرام)
    // و `renotify` یعنی باز هم صدا/لرزش بدهد.
    tag: chat || 'rafigram',
    renotify: true,
    dir: 'rtl',
    lang: 'fa',
    data: { chat_id: chat, at: Date.now() }
  };
  if (d.icon) { opt.icon = d.icon; opt.badge = d.icon; }
  else { opt.icon = '/icons/Icon-192.png'; opt.badge = '/icons/Icon-192.png'; }
  event.waitUntil(self.registration.showNotification(title, opt));
});

self.addEventListener('notificationclick', function (event) {
  event.notification.close();
  var chat = '';
  try { chat = (event.notification.data || {}).chat_id || ''; } catch (e) {}
  var url = chat ? ('/?chat=' + encodeURIComponent(chat)) : '/';
  event.waitUntil((async function () {
    var list = [];
    try {
      list = await self.clients.matchAll({ type: 'window',
                                           includeUncontrolled: true });
    } catch (e) { list = []; }
    // ⚠️ عمداً `client.navigate()` صدا نمی‌خورد — همان یک خط بود که پنجره‌ی
    // «افزوده به صفحه‌ی خانه»ی آیفون را به نشانیِ بی‌بازگشت می‌برد. برگه‌ی باز
    // را فقط جلو می‌آوریم و چتِ مقصد را با پیام به خودش می‌گوییم.
    for (var i = 0; i < list.length; i++) {
      try {
        list[i].postMessage({ kind: 'rafigram-open', chat_id: chat });
        await list[i].focus();
        return;
      } catch (e) {}
    }
    try { await self.clients.openWindow(url); } catch (e) {}
  })());
});
"""

# اسکریپتِ صفحه: ورکرِ بالا را ثبت و اشتراکِ Web Push را به سرور می‌دهد.
#
# چرا این‌جا (جاواسکریپتِ صفحه) و نه در پایتون: پایتونِ وب داخلِ Web Worker
# اجرا می‌شود و آن‌جا `window`، `localStorage` و — مهم‌تر از همه —
# `navigator.serviceWorker` وجود ندارند. پس `services/notify.py` هرگز
# نمی‌توانست در مرورگر اعلان بدهد؛ باید از صفحه انجام شود.
#
# توکن را از `localStorage` برمی‌داریم چون افزونه‌ی `shared_preferences`
# (سمتِ Dart، روی رشته‌ی اصلی) نشستِ ما را همان‌جا می‌گذارد. نامِ دقیقِ کلید
# بینِ نسخه‌های افزونه عوض شده، پس دنبالِ *پسوند* می‌گردیم نه نامِ کامل.
SW_PUSH_REG = """  <script id="sw-push">
  (function () {
    if (!("serviceWorker" in navigator) || !("PushManager" in window)) return;
    var SW = "/rafigram-push-sw.js";
    var last = "";        // آخرین توکنی که برایش ثبت کردیم
    var busy = false;

    function token() {
      // هر کلیدی که به «rafigram.token» ختم می‌شود (با هر پیشوندی که
      // افزونه گذاشته باشد). مقدار ممکن است داخلِ کوتیشنِ JSON باشد.
      try {
        for (var i = 0; i < localStorage.length; i++) {
          var k = localStorage.key(i);
          if (!k || k.indexOf("rafigram.token") < 0) continue;
          var v = localStorage.getItem(k) || "";
          if (v.charAt(0) === '"') { try { v = JSON.parse(v); } catch (e) {} }
          v = String(v || "").trim();
          if (v.length > 20) return v;
        }
      } catch (e) {}
      return "";
    }

    function b64u(s) {
      var pad = "=".repeat((4 - (s.length % 4)) % 4);
      var raw = atob((s + pad).replace(/-/g, "+").replace(/_/g, "/"));
      var out = new Uint8Array(raw.length);
      for (var i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
      return out;
    }

    // ── اجازه‌ی اعلان ─────────────────────────────
    // ⚠️ این‌جا باید خواسته شود، نه در پایتون: پایتونِ وب داخلِ Web Worker
    // است و آن‌جا نه `window` هست نه `Notification`. پس `notify.ask()` در
    // مرورگر همیشه بی‌اثر بود — همان دلیلی که اعلانِ وب هرگز نمی‌آمد.
    //
    // و باید از دلِ یک *ژستِ واقعیِ کاربر* خواسته شود: سافاری (و آیفون)
    // `requestPermission` بی ژست را همان‌جا رد می‌کند. پس به نخستین
    // لمس/کلیکِ کاربر می‌چسبانیمش — نه به تایمر.
    var asked = false;

    function pref(name, dflt) {
      try {
        for (var i = 0; i < localStorage.length; i++) {
          var k = localStorage.key(i);
          if (!k || k.indexOf("rafigram." + name) < 0) continue;
          var v = localStorage.getItem(k) || "";
          if (v.charAt(0) === '"') { try { v = JSON.parse(v); } catch (e) {} }
          return String(v || "");
        }
      } catch (e) {}
      return dflt;
    }

    function askPerm() {
      if (asked || !("Notification" in window)) return;
      if (Notification.permission !== "default") { asked = true; return; }
      // کلیدِ «اعلان» در تنظیمات خاموش است ⇒ مزاحمِ کاربر نمی‌شویم.
      if (pref("notify", "1") === "0") return;
      // پیش از ورود نپرس: کاربر روی صفحه‌ی ورود «اجازه نده» می‌زند و بعد
      // دیگر هیچ راهی جز تنظیماتِ مرورگر نمی‌ماند.
      if (!token()) return;
      asked = true;
      try { Notification.requestPermission().then(hook).catch(function () {}); }
      catch (e) { try { Notification.requestPermission(hook); } catch (e2) {} }
    }

    window.rafigramAskNotify = function () { asked = false; askPerm(); };
    ["pointerup", "touchend", "click"].forEach(function (ev) {
      window.addEventListener(ev, askPerm, { capture: true, passive: true });
    });

    function keyOf(sub, name) {
      try {
        var b = sub.getKey(name);
        if (!b) return "";
        var a = new Uint8Array(b), s = "";
        for (var i = 0; i < a.length; i++) s += String.fromCharCode(a[i]);
        return btoa(s).replace(/\\+/g, "-").replace(/\\//g, "_")
                      .replace(/=+$/, "");
      } catch (e) { return ""; }
    }

    async function hook() {
      var tk = token();
      if (busy || tk === last) return;
      busy = true;
      try {
        if (!tk) {
          // خروج از حساب: اشتراک را از سرور و از مرورگر برمی‌داریم، وگرنه
          // اعلانِ حسابِ قبلی روی همین مرورگر می‌آمد.
          if (last) {
            var r0 = await navigator.serviceWorker.getRegistration(SW);
            var s0 = r0 && await r0.pushManager.getSubscription();
            if (s0) {
              try {
                await fetch("/push/unregister", {
                  method: "POST",
                  headers: { "Content-Type": "application/json",
                             "Authorization": "Bearer " + last },
                  body: JSON.stringify({ endpoint: s0.endpoint })
                });
              } catch (e) {}
              try { await s0.unsubscribe(); } catch (e) {}
            }
          }
          last = "";
          return;
        }
        if (Notification.permission !== "granted") { askPerm(); return; }
        var kr = await fetch("/push/key", { cache: "no-store" });
        var kj = await kr.json();
        if (!kj || !kj.key) return;                          // سرور VAPID ندارد
        var reg = await navigator.serviceWorker.register(SW, { scope: "/" });
        var sub = await reg.pushManager.getSubscription();
        if (!sub) {
          sub = await reg.pushManager.subscribe({
            userVisibleOnly: true,
            applicationServerKey: b64u(kj.key)
          });
        }
        var p = keyOf(sub, "p256dh"), a = keyOf(sub, "auth");
        if (!p || !a) return;
        var rr = await fetch("/push/register", {
          method: "POST",
          headers: { "Content-Type": "application/json",
                     "Authorization": "Bearer " + tk },
          body: JSON.stringify({ endpoint: sub.endpoint, p256dh: p, auth: a,
                                 kind: "web",
                                 device: (navigator.userAgent || "")
                                   .slice(0, 190) })
        });
        if (rr.ok) last = tk;
      } catch (e) {
        // شکست هیچ‌چیزی را نمی‌شکند: برنامه بی اعلانِ بیرونی کار می‌کند و
        // دورِ بعد از نو تلاش می‌شود.
      } finally { busy = false; }
    }

    // ضربه روی اعلان ⇒ ورکر شناسه‌ی چت را به صفحه می‌گوید. پایتون خودش
    // `?chat=` را سرِ بالا آمدن می‌خواند، پس این‌جا فقط نشانی را عوض
    // می‌کنیم (بی بارگذاریِ دوباره) تا هر دو راه یکی باشند.
    navigator.serviceWorker.addEventListener("message", function (ev) {
      var d = ev.data || {};
      if (d.kind !== "rafigram-open" || !d.chat_id) return;
      try {
        history.replaceState(null, "", "/?chat=" +
                             encodeURIComponent(d.chat_id));
        window.dispatchEvent(new CustomEvent("rafigram-open",
                                             { detail: d.chat_id }));
      } catch (e) {}
    });

    // توکن سرِ بالا آمدنِ صفحه هنوز نیامده (کاربر باید وارد شود) و بعدها هم
    // ممکن است عوض شود؛ پس دوره‌ای می‌پرسیم. هزینه‌اش یک نگاه به
    // localStorage است، و درخواستِ شبکه فقط وقتی توکن *عوض* شده باشد.
    setInterval(hook, 5000);
    window.addEventListener("focus", hook);
    hook();
  })();
  </script>
"""
def make_icons(out: Path) -> int:
    """آیکون‌ها را از `client/assets/icon.png` می‌سازد (نیازمندِ Pillow)."""
    if not SRC_ICON.is_file():
        print("[pwa] icon.png نیست؛ آیکون‌ها ساخته نشد")
        return 0
    try:
        from PIL import Image
    except Exception:
        print("[pwa] Pillow نصب نیست؛ آیکون‌ها دست‌نخورده ماند")
        return 0
    src = Image.open(SRC_ICON).convert("RGBA")
    n = 0
    for rel, size in SIZES.items():
        p = out / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if "maskable" in rel:
            # آیکونِ maskable باید حاشیه‌ی امن داشته باشد (٪۱۰ هر طرف)
            pad = int(size * 0.10)
            canvas = Image.new("RGBA", (size, size), BG)
            inner = src.resize((size - 2 * pad, size - 2 * pad),
                               Image.LANCZOS)
            canvas.paste(inner, (pad, pad), inner)
            img = canvas
        elif rel.endswith("apple-touch-icon.png"):
            # سافاری شفافیت را سیاه می‌کند ⇒ پس‌زمینه‌ی توپُر
            canvas = Image.new("RGBA", (size, size), BG)
            inner = src.resize((size, size), Image.LANCZOS)
            canvas.paste(inner, (0, 0), inner)
            img = canvas.convert("RGB")
        else:
            img = src.resize((size, size), Image.LANCZOS)
        img.save(p, "PNG")
        n += 1
    print(f"[pwa] {n} آیکون ساخته شد")
    return n


def patch_index(out: Path) -> None:
    """متاهای iOS/PWA را داخلِ <head> می‌گذارد و جهتِ فارسی را ست می‌کند."""
    p = out / "index.html"
    html = p.read_text("utf-8")
    # متاهای تکراری و ناقصِ فلاتر کنار می‌روند. هر چیزی که خودمان در HEAD
    # می‌گذاریم هم اول پاک می‌شود تا اجرای دوباره‌ی این ابزار روی همان پوشه
    # تگ‌ها را دو بار ننویسد (idempotent). ترتیبِ صفت‌ها مهم نیست: قالبِ
    # فلاتر جایی `<meta content="…" name="viewport">` می‌نویسد (برعکس)، پس
    # الگو باید صفت را هرجای تگ پیدا کند.
    for key in ("viewport", "theme-color", "description", "application-name",
                "color-scheme", "mobile-web-app-capable",
                "apple-mobile-web-app-capable",
                "apple-mobile-web-app-status-bar-style",
                "apple-mobile-web-app-title"):
        html = re.sub(r'\s*<meta [^>]*name="' + re.escape(key) + r'"[^>]*>',
                      "", html)
    for pat in (r'\s*<!-- PWA \([^>]*\) -->',
                r'\s*<link [^>]*rel="apple-touch-icon"[^>]*>',
                r'\s*<link rel="icon" type="image/png" sizes="\d+x\d+"[^>]*>',
                r'\s*<link [^>]*rel="manifest"[^>]*>'):
        html = re.sub(pat, "", html)
    html = re.sub(r'<html[^>]*>', '<html lang="fa" dir="rtl">', html, count=1)
    html = re.sub(r'\s*<script id="sw-kill">.*?</script>', "", html, flags=re.S)
    html = re.sub(r'\s*<script id="sw-push">.*?</script>', "", html, flags=re.S)
    # `<base href="/">` پایه‌ی حلِ نشانی‌های نسبیِ صفحه است و سافاری هنگامِ
    # «افزودن به صفحه‌ی خانه» هم به آن نگاه می‌کند. قالبِ فلاتر معمولاً خودش
    # می‌گذاردش، ولی اگر نگذاشته باشد یا نسبی نوشته باشد همین‌جا درست می‌شود.
    if re.search(r'<base [^>]*href="[^"]*"', html):
        html = re.sub(r'<base [^>]*href="[^"]*"[^>]*>', '<base href="/">',
                      html, count=1)
    else:
        html = re.sub(r'(<head[^>]*>)', r'\1\n  <base href="/">', html,
                      count=1)
    # viewport خودمان همیشه از HEAD می‌آید؛ قالبِ تازه‌ی فلاتر هیچ viewport
    # نمی‌گذارد، پس جایگزینی به تنهایی کافی نبود و «viewport-fit» جا می‌ماند.
    head = HEAD.format(name=NAME, bg=BG, desc=DESC, vp=VIEWPORT)
    html = html.replace("</head>", head + SW_KILL + SW_PUSH_REG + "</head>", 1)
    if "<title>" in html:
        html = re.sub(r"<title>.*?</title>", f"<title>{NAME}</title>", html,
                      count=1, flags=re.S)
    p.write_text(html, "utf-8")
    print("[pwa] index.html وصله شد")
def patch_boot(out: Path) -> None:
    """هر چه از CDN می‌آمد را محلی می‌کند + اسپلشِ تاریک + نگهبانِ بالا آمدن.

    مهم‌ترین وصله‌ی این فایل. قالبِ فلت بی `--no-cdn` پایتونِ مرورگر
    (Pyodide، ~۱۵ مگابایت) را از jsdelivr می‌گیرد و CanvasKit را از gstatic.
    هر دو در ایران بگیر‌نگیرند: نتیجه‌اش «برنامه‌ی وب روی گوشی تا ابد بالا
    نمی‌آید» و در سافاریِ آیفون صفحه‌ی سفید است. هر دو کتابخانه کنارِ همین
    صفحه هم کپی شده‌اند، پس فقط آدرس را به محلی برمی‌گردانیم. این کار
    idempotent است و روی خروجیِ ساخته‌شده هم می‌شود دوباره اجرایش کرد.
    """
    p = out / "index.html"
    html = p.read_text("utf-8")
    hits = []

    # کلیدِ ماجرا: قالبِ فلاتر آدرسِ محلیِ CanvasKit/فونت را *فقط* وقتی
    # به‌کار می‌گیرد که `flet.noCdn` روشن باشد (flutter_bootstrap.js:
    # `if (flet.noCdn) { flutterConfig.canvasKitBaseUrl = … }`). بی آن،
    # CanvasKit (~۷ مگ) از gstatic و فونتِ یدکی از fonts.gstatic می‌آید و
    # اگر آن دو نیایند، هیچ‌چیز رنگ نمی‌شود ⇒ همان «صفحه‌ی سفیدِ آیفون».
    if (out / "canvaskit" / "canvaskit.js").is_file():
        # دو نگارشِ فلت: هم `noCdn: false,` داخلِ آبجکت، هم `flet.noCdn=false;`
        new, n = re.subn(r'noCdn(\s*[:=]\s*)[^,;\r\n]+', r'noCdn\g<1>true',
                         html)
        if n and new != html:
            html, _ = new, hits.append("noCdn → روشن")

    # Pyodide: هم آدرسِ jsdelivr را محلی می‌کنیم، هم نشانیِ نسبی را با «./»
    # شروع می‌کنیم. ورکرِ فلت با `import(self.pyodideUrl)` بارش می‌کند و در
    # ورکرِ ماژولی، «pyodide/pyodide.mjs» بی «./» نامِ یک بسته حساب می‌شود ⇒
    # «Failed to resolve module specifier» و پایتون هرگز بالا نمی‌آید. این
    # باگ در خودِ `flet build web --no-cdn` هم هست (patch_index.py همین
    # نشانیِ بی‌ریشه را می‌نویسد)، پس وصله‌اش اینجا لازم است.
    if (out / "pyodide" / "pyodide.mjs").is_file():
        def _pyo(m: re.Match) -> str:
            url = m.group("u")
            if url.startswith("http") or not url.endswith("pyodide.mjs"):
                url = "./pyodide/pyodide.mjs"
            elif not url.startswith(("./", "../", "/")):
                url = "./" + url
            return m.group("h") + url + '"'
        new, n = re.subn(r'(?P<h>pyodideUrl\s*[:=]\s*")(?P<u>[^"]*)"',
                         _pyo, html)
        if n and new != html:
            html, _ = new, hits.append("pyodideUrl → محلی و قابلِ حل")
    # و برای هر شکلِ دیگری از نشانی، خودِ ورکر آن را کنارِ خودش حل کند
    wrk = out / "python-worker.js"
    if wrk.is_file():
        wt = wrk.read_text("utf-8")
        if "await import(self.pyodideUrl)" in wt:
            wrk.write_text(wt.replace(
                "await import(self.pyodideUrl)",
                "await import(new URL(self.pyodideUrl,"
                " self.location.href).href)"), "utf-8")
            hits.append("ورکرِ پایتون: حلِ نشانیِ نسبی")
    pyj = out / "python.js"
    if pyj.is_file():
        pt = pyj.read_text("utf-8")
        if "__fletPythonReady" not in pt and PY_READY_SRC in pt:
            pyj.write_text(pt.replace(PY_READY_SRC, PY_READY_DST, 1), "utf-8")
            hits.append("نشانه‌ی «پایتون بالا آمد»")
    if (out / "canvaskit" / "canvaskit.js").is_file():
        new, n = re.subn(r'canvasKitBaseUrl:\s*"https?://[^"]*"',
                         'canvasKitBaseUrl: "canvaskit/"', html)
        if n:
            html, _ = new, hits.append("canvasKitBaseUrl → محلی")
    if (out / "assets" / "fonts").is_dir():
        new, n = re.subn(r'fontFallbackBaseUrl:\s*"https?://[^"]*"',
                         'fontFallbackBaseUrl: "assets/fonts/"', html)
        if n:
            html, _ = new, hits.append("fontFallbackBaseUrl → محلی")

    # اسپلشِ سفیدِ قالبِ فلاتر ⇒ تاریک، همرنگِ برنامه. روی آیفون همان سفیدی
    # بود که کاربر «صفحه‌ی سفید» می‌دیدش.
    new, n = re.subn(r'background-color:\s*#(?:ffffff|fff)\b',
                     f'background-color: {BG}', html, flags=re.I)
    if n:
        html, _ = new, hits.append("پس‌زمینه‌ی اسپلش تاریک شد")
    new, n = re.subn(r'background-color:\s*#222222\b',
                     f'background-color: {BG}', html, flags=re.I)
    html = new

    # نگهبان (idempotent: نسخه‌ی قبلی‌اش اول پاک می‌شود)
    for pat in (r'\s*<style id="boot-note-style">.*?</style>',
                r'\s*<div id="boot-note".*?</div>',
                r'\s*<script id="boot-note-script">.*?</script>'):
        html = re.sub(pat, "", html, flags=re.S)
    html = html.replace("</body>", BOOT_WATCH + "</body>", 1)
    hits.append("نگهبانِ بالا آمدن")

    p.write_text(html, "utf-8")
    print("[pwa] boot: " + (", ".join(hits) if hits else "چیزی لازم نبود"))


def add_push_sw(out: Path) -> None:
    """ورکرِ اعلان را کنارِ `index.html` می‌گذارد.

    فایلی جدا از ورکرِ فلاتر است و نامش هم عمداً «service_worker» ندارد، پس
    `neutralize_sw` سراغش نمی‌رود. سرِ نشانیِ `/rafigram-push-sw.js` می‌نشیند
    و اسکریپتِ `sw-push` در صفحه ثبتش می‌کند.

    idempotent: اگر همان محتوا از قبل هست، دست نمی‌زند (تا زمانِ تغییرِ فایل
    الکی جابه‌جا نشود و کشِ مرورگر بی‌دلیل باطل نگردد).
    """
    p = out / PUSH_SW
    try:
        if p.is_file() and p.read_text("utf-8") == SW_PUSH:
            print("[pwa] ورکرِ اعلان از قبل بود")
            return
    except Exception:
        pass
    p.write_text(SW_PUSH, "utf-8")
    print(f"[pwa] ورکرِ اعلان نوشته شد: {PUSH_SW}")


def neutralize_sw(out: Path) -> int:
    """فایل‌های سرویس‌ورکر را با نسخه‌ی خنثی جایگزین می‌کند.

    `patch_sw` جلوی *ثبتِ تازه* را می‌گیرد؛ این تابع گوشی‌هایی را نجات
    می‌دهد که ورکرِ کهنه از قبل رویشان ثبت شده. آن ورکر پیش از هر کدِ صفحه
    فعال می‌شود، پس اسکریپتِ درونِ صفحه به دادش نمی‌رسد — تنها راه این است
    که سرِ همان نشانی چیزِ بی‌آزاری بنشیند.

    idempotent: اگر از قبل خنثی شده باشد دست نمی‌زند.
    """
    n = 0
    for p in sorted(set(list(out.glob("*service_worker*.js"))
                        + list(out.glob("sw.js")))):
        try:
            cur = p.read_text("utf-8")
        except Exception:
            cur = ""
        if "ورکرِ خنثی" in cur:
            continue                       # از قبل خنثی است
        p.write_text(SW_DEAD, "utf-8")
        n += 1
    print(f"[pwa] سرویس‌ورکر خنثی شد ({n} فایل)" if n
          else "[pwa] سرویس‌ورکر از قبل خنثی بود")
    return n


def patch_sw(out: Path) -> None:
    """ثبتِ سرویس‌ورکرِ فلاتر را از `flutter_bootstrap.js` برمی‌دارد.

    ورکری که فلاتر می‌فرستد یک قلابِ خودکُش است: `install` ⇒ `skipWaiting`،
    `activate` ⇒ `registration.unregister()` و بعد
    `client.navigate(client.url)` روی هر پنجره‌ی باز. هیچ فایلی کش نمی‌شود
    (کشِ ما از هدرهای `vercel.json` می‌آید)، پس نگه‌داشتنش هیچ سودی ندارد؛
    ولی همان ناوبریِ اجباری در پنجره‌ی «افزوده‌شده به صفحه‌ی خانه»ی آیفون
    خطرِ واقعی دارد — پنجره‌ی standalone نوارِ نشانی ندارد و اگر جایی برود
    که نباید، کاربر فقط «Safari can't open the page» می‌بیند و هیچ راهی
    برای برگشتن ندارد جز پاک‌کردنِ آیکون.

    `_flutter.loader.load` وقتی `serviceWorkerSettings` نداشته باشد هیچ
    ورکری ثبت نمی‌کند، پس فقط همان یک بند را برمی‌داریم. idempotent است.
    """
    p = out / "flutter_bootstrap.js"
    if not p.is_file():
        print("[pwa] flutter_bootstrap.js نیست؛ سرویس‌ورکر دست‌نخورده ماند")
        return
    js = p.read_text("utf-8")
    new, n = SW_OFF_SRC.subn("", js)
    if n:
        p.write_text(new, "utf-8")
        print(f"[pwa] ثبتِ سرویس‌ورکر برداشته شد ({n} جا)")
    else:
        print("[pwa] سرویس‌ورکر از قبل ثبت نمی‌شد")


def mirror_font_fallback(out: Path) -> int:
    """فونتِ یدکیِ رابوتو را سرِ همان نشانیِ نسخه‌دار هم می‌گذارد.

    فلاتر فونتِ یدکی را با نشانیِ نسخه‌دارِ گوگل صدا می‌زند
    (`roboto/v32/KFOmCnq….woff2`) ولی فلت کنارِ صفحه فقط
    `assets/fonts/roboto.woff2` را کپی می‌کند ⇒ با آدرسِ محلی، هر بار بالا
    آمدن یک ۴۰۴ می‌خورد. نشانیِ دقیق داخلِ `main.dart.js` نوشته شده، پس
    همان را می‌خوانیم و کپی می‌سازیم؛ این‌طور با نسخه‌ی بعدیِ فلاتر (v33…)
    هم خودش را پیدا می‌کند.

    فونت‌های «نوتو» (مثلِ Noto Sans Arabic) کنارِ صفحه نیستند و همان ۴۰۴ را
    می‌دهند؛ عیبی ندارد: فلاتر بی‌درنگ می‌افتد روی وزیرمتنِ خودِ برنامه و
    فارسی درست نقش می‌بندد (سنجیده شد). مهم این است که سرِ آن ۴۰۴ به گوگل
    وصل نشویم — پشتِ فیلتر، همان یک درخواست دقیقه‌ها معطلی است.
    """
    idx = (out / "index.html").read_text("utf-8")
    m = re.search(r'fontFallbackBaseUrl:\s*"([^"]*)"', idx)
    rel_base = m.group(1) if m and not m.group(1).startswith("http") \
        else "assets/fonts/"
    base = out / rel_base.strip("/")
    src = base / "roboto.woff2"
    if not src.is_file():
        return 0
    want: set[str] = set()
    for name in ("main.dart.js", "main.dart.mjs"):
        p = out / name
        if p.is_file():
            want |= set(re.findall(r"roboto/v\d+/[A-Za-z0-9_-]+\.woff2",
                                   p.read_text("utf-8", errors="ignore")))
    n = 0
    for rel in sorted(want):
        dst = base / rel
        if dst.is_file():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(src.read_bytes())
        n += 1
    print(f"[pwa] فونتِ یدکی: {n} کپیِ تازه ({len(want)} نشانی)")
    return n


def font_fallback_missing(out: Path) -> list[str]:
    """نشانی‌های نسخه‌دارِ فونتِ یدکی که سرِ جایشان نیستند."""
    idx = (out / "index.html").read_text("utf-8")
    m = re.search(r'fontFallbackBaseUrl:\s*"([^"]*)"', idx)
    if not m or m.group(1).startswith("http"):
        return []                            # از CDN می‌آید؛ به ما مربوط نیست
    base = out / m.group(1).strip("/")
    miss = []
    for name in ("main.dart.js", "main.dart.mjs"):
        p = out / name
        if not p.is_file():
            continue
        for rel in set(re.findall(r"roboto/v\d+/[A-Za-z0-9_-]+\.woff2",
                                  p.read_text("utf-8", errors="ignore"))):
            if not (base / rel).is_file():
                miss.append(f"{m.group(1)}{rel}")
    return sorted(set(miss))


def patch_manifest(out: Path) -> None:
    """`manifest.json` را با نسخه‌ی فارسیِ کامل جایگزین می‌کند."""
    p = out / "manifest.json"
    old = {}
    if p.is_file():
        try:
            old = json.loads(p.read_text("utf-8"))
        except Exception:
            old = {}
    man = dict(MANIFEST)
    # آیکون‌هایی که فلاتر ساخته و ما دست نزده‌ایم را از دست نمی‌دهیم
    keep = [i for i in (old.get("icons") or [])
            if i.get("src") not in {x["src"] for x in man["icons"]}]
    man["icons"] = man["icons"] + keep
    p.write_text(json.dumps(man, ensure_ascii=False, indent=2), "utf-8")
    print("[pwa] manifest.json نوشته شد")


def add_offline(out: Path) -> None:
    (out / "offline.html").write_text(OFFLINE, "utf-8")
    print("[pwa] offline.html اضافه شد")


def check(out: Path) -> bool:
    """سنجشِ نهایی — هرچه PWA لازم دارد سرِ جایش هست؟"""
    ok = True
    idx = (out / "index.html").read_text("utf-8")
    need = ["apple-mobile-web-app-capable", "apple-touch-icon",
            "viewport-fit=cover", 'dir="rtl"', "manifest.json",
            'name="theme-color"', 'id="sw-kill"', 'id="sw-push"',
            '<base href="/">']
    for s in need:
        if s not in idx:
            print(f"[pwa] ✗ در index.html نیست: {s}")
            ok = False
    # دو تگِ viewport یعنی یکی از آن‌ها `viewport-fit=cover` ندارد و ممکن است
    # همان اولی برنده شود ⇒ نوارِ سفید در iOS. پس تک‌بودنش را می‌سنجیم.
    n_vp = len(re.findall(r'<meta [^>]*name="viewport"', idx))
    if n_vp != 1:
        print(f"[pwa] ✗ تگِ viewport باید یکی باشد، {n_vp} تا هست"); ok = False
    # هیچ آدرسِ CDNی نماند: پشتِ فیلتر، همان یک خط برنامه‌ی وب را می‌خواباند.
    for key in ("pyodideUrl", "canvasKitBaseUrl", "fontFallbackBaseUrl"):
        m = re.search(key + r'\s*[:=]\s*"([^"]*)"', idx)
        if m and m.group(1).startswith("http"):
            print(f"[pwa] ✗ {key} از CDN می‌آید: {m.group(1)}")
            ok = False
    # نشانیِ Pyodide باید داخلِ ورکرِ ماژولی حل شود، وگرنه پایتون بالا نمی‌آید
    m = re.search(r'pyodideUrl\s*[:=]\s*"([^"]*)"', idx)
    if m and not m.group(1).startswith(("./", "../", "/", "http")):
        print(f"[pwa] ✗ pyodideUrl بی «./» است: {m.group(1)}")
        ok = False
    wrk = out / "python-worker.js"
    if wrk.is_file() and "await import(self.pyodideUrl)" in \
            wrk.read_text("utf-8"):
        print("[pwa] ✗ ورکرِ پایتون نشانیِ نسبی را حل نمی‌کند")
        ok = False
    if not re.search(r'noCdn\s*[:=]\s*true', idx):
        # بی این، آدرسِ محلیِ CanvasKit/فونت در قالبِ فلاتر نادیده می‌ماند
        print("[pwa] ✗ noCdn روشن نیست (CanvasKit از gstatic می‌آید)")
        ok = False
    if "boot-note" not in idx:
        print("[pwa] ✗ نگهبانِ بالا آمدن در index.html نیست"); ok = False
    if "__fletPythonReady" not in idx:
        # بی این پرچم، پیامِ «طول کشید» روی برنامه‌ی سالم هم می‌ماند
        print("[pwa] ✗ نگهبان به نشانه‌ی پایتون نگاه نمی‌کند"); ok = False
    pyj = out / "python.js"
    if pyj.is_file() and "__fletPythonReady" not in pyj.read_text("utf-8"):
        print("[pwa] ✗ python.js نشانه‌ی «بالا آمد» را نمی‌فرستد"); ok = False
    if not (out / "pyodide" / "pyodide.mjs").is_file():
        print("[pwa] ✗ کپیِ محلیِ Pyodide نیست (با --no-cdn بساز)"); ok = False
    for rel in font_fallback_missing(out):
        print(f"[pwa] ✗ فونتِ یدکی ۴۰۴ می‌دهد: {rel}"); ok = False
    man = json.loads((out / "manifest.json").read_text("utf-8"))
    if man.get("display") != "standalone":
        print("[pwa] ✗ display standalone نیست"); ok = False
    # نشانیِ شروع/دامنه باید مطلق باشد؛ نسبی‌بودنش همان چیزی است که آیفون را
    # سرِ «افزودن به صفحه‌ی خانه» به نشانیِ خراب می‌برد.
    for key in ("start_url", "scope"):
        if not str(man.get(key) or "").startswith("/"):
            print(f"[pwa] ✗ {key} مطلق نیست: {man.get(key)!r}"); ok = False
    if not any(i.get("purpose") == "maskable" for i in man["icons"]):
        print("[pwa] ✗ آیکونِ maskable نیست"); ok = False
    boot = out / "flutter_bootstrap.js"
    if boot.is_file() and re.search(r'serviceWorkerSettings\s*:\s*\{',
                                    boot.read_text("utf-8")):
        # ورکرِ خودکُشِ فلاتر روی آیفونِ standalone پنجره را جابه‌جا می‌کند.
        # (نامِ همین کلید داخلِ بارِ فشرده‌ی `flutter.js` هم هست — آنجا یک
        # پارامترِ تجزیه‌شده است، نه پیکربندی؛ پس دنبالِ «: {» می‌گردیم.)
        print("[pwa] ✗ سرویس‌ورکر هنوز ثبت می‌شود"); ok = False
    for rel in SIZES:
        if not (out / rel).is_file():
            print(f"[pwa] ✗ آیکون نیست: {rel}"); ok = False
    sw = list(out.glob("*service_worker*.js")) + list(out.glob("sw.js"))
    if not sw:
        print("[pwa] ✗ سرویس‌ورکر پیدا نشد (خروجیِ flet build web؟)")
        ok = False
    # و هیچ‌کدامشان نباید ناوبریِ اجباری داشته باشد. این سنجه مهم‌ترینِ بخشِ
    # iOS است: فایل روی سرور می‌ماند و گوشی‌هایی که ورکرِ کهنه دارند همین را
    # می‌گیرند؛ اگر `client.navigate` تویش باشد، همان پنجره‌ی standalone باز
    # هم می‌پرد. توضیح‌های خودمان هم اسمِ همان تابع را برده‌اند، پس پیش از
    # سنجش کامنت‌ها را برمی‌داریم تا هشدارِ الکی ندهد.
    for p in sw:
        body = re.sub(r'//[^\n]*|/\*.*?\*/', "",
                      p.read_text("utf-8", errors="ignore"), flags=re.S)
        if ".navigate(" in body:
            print(f"[pwa] ✗ سرویس‌ورکر ناوبری می‌کند: {p.name}")
            ok = False
    # ── ورکرِ اعلان: باید *باشد* و باید زنده باشد ──────
    # (برعکسِ ورکرِ فلاتر. اعلانِ برگه‌ی بسته فقط از این راه می‌آید.)
    pw = out / PUSH_SW
    if not pw.is_file():
        print(f"[pwa] ✗ ورکرِ اعلان نیست: {PUSH_SW}"); ok = False
    else:
        pb = pw.read_text("utf-8", errors="ignore")
        for s in ("addEventListener('push'", "showNotification",
                  "notificationclick"):
            if s not in pb:
                print(f"[pwa] ✗ ورکرِ اعلان ناقص است: {s}"); ok = False
        # همان درسِ آیفون: این یکی هم نباید ناوبریِ اجباری کند
        if ".navigate(" in re.sub(r'//[^\n]*|/\*.*?\*/', "", pb, flags=re.S):
            print("[pwa] ✗ ورکرِ اعلان ناوبری می‌کند"); ok = False
        if "unregister()" in pb:
            # اگر روزی محتوای خنثی رویش نوشته شود، اعلان بی‌صدا می‌مُرد
            print("[pwa] ✗ ورکرِ اعلان خودش را از ثبت درمی‌آورد"); ok = False
    # و `sw-kill` نباید *همه* را پاک کند، وگرنه ورکرِ اعلان هر بار می‌رفت
    if 'id="sw-kill"' in idx and "rafigram-push-sw" not in idx:
        print("[pwa] ✗ sw-kill ورکرِ اعلان را هم پاک می‌کند"); ok = False
    print("[pwa] " + ("همه‌چیز درست است" if ok else "ناقص"))
    return ok


def main(argv=None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    out = Path(args[0] if args else (ROOT / "build" / "web")).resolve()
    if not out.is_dir():
        print(f"[pwa] پوشه نیست: {out}")
        return 2
    make_icons(out)
    patch_index(out)
    patch_boot(out)
    patch_sw(out)
    neutralize_sw(out)
    add_push_sw(out)
    mirror_font_fallback(out)
    patch_manifest(out)
    add_offline(out)
    return 0 if check(out) else 1


if __name__ == "__main__":
    sys.exit(main())
