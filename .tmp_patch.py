import pathlib
p = pathlib.Path(r"D:\coding\messenger\client\main.py")
txt = p.read_text(encoding="utf-8")
orig = txt

old_fallback = """        try:
            # \u26a0\ufe0f `page.add` \u0631\u0648\u06cc `page.controls` \u0645\u06cc\u200c\u0646\u0648\u06cc\u0633\u062f \u0648 \u0622\u0646 \u06cc\u0639\u0646\u06cc
            # `views[0]` = View\u0650 *\u067e\u0646\u0647\u0627\u0646\u0650* `/hold` \u2014 \u06cc\u0639\u0646\u06cc \u062f\u0642\u06cc\u0642\u0627\u064b \u0647\u0645\u0627\u0646 \u00ab\u0635\u0641\u062d\u0647\u0654
            # \u0633\u06cc\u0627\u0647\u0650 \u0628\u0639\u062f \u0627\u0632 Back\u00bb. \u067e\u0633 \u0645\u062d\u062a\u0648\u0627 \u0631\u0627 \u062f\u0633\u062a\u06cc \u0645\u06cc\u200c\u0646\u0634\u0627\u0646\u06cc\u0645 \u0631\u0648\u06cc View\u06cc \u06a9\u0647
            # Navigator \u0646\u0634\u0627\u0646 \u0645\u06cc\u200c\u062f\u0647\u062f\u060c \u0648 \u0646\u0634\u0627\u0646\u0647 \u0631\u0627 \u0647\u0645 \u062a\u0627\u0632\u0647 \u0645\u06cc\u200c\u06a9\u0646\u06cc\u0645 \u0648\u06af\u0631\u0646\u0647
            # `page._nav_who` \u06a9\u0647\u0646\u0647 \u0645\u06cc\u200c\u0645\u0627\u0646\u062f \u0648 Back\u0650 \u0628\u0639\u062f\u06cc \u0627\u0634\u062a\u0628\u0627\u0647 \u0645\u06cc\u200c\u0631\u0648\u062f.
            page.views[-1].controls = [root]
            page.update()
            _nav_mark(page, back)
        except Exception:
            pass"""

new_fallback = """        try:
            # \u26a0\ufe0f `page.add` \u0631\u0648\u06cc `page.controls` \u0645\u06cc\u200c\u0646\u0648\u06cc\u0633\u062f \u0648 \u0622\u0646 \u06cc\u0639\u0646\u06cc
            # `views[0]` = View\u0650 *\u067e\u0646\u0647\u0627\u0646\u0650* `/hold` \u2014 \u06cc\u0639\u0646\u06cc \u062f\u0642\u06cc\u0642\u0627\u064b \u0647\u0645\u0627\u0646 \u00ab\u0635\u0641\u062d\u0647\u0654
            # \u0633\u06cc\u0627\u0647\u0650 \u0628\u0639\u062f \u0627\u0632 Back\u00bb. \u067e\u0633 \u0645\u062d\u062a\u0648\u0627 \u0631\u0627 \u062f\u0633\u062a\u06cc \u0645\u06cc\u200c\u0646\u0634\u0627\u0646\u06cc\u0645 \u0631\u0648\u06cc View\u06cc \u06a9\u0647
            # Navigator \u0646\u0634\u0627\u0646 \u0645\u06cc\u200c\u062f\u0647\u062f\u060c \u0648 \u0646\u0634\u0627\u0646\u0647 \u0631\u0627 \u0647\u0645 \u062a\u0627\u0632\u0647 \u0645\u06cc\u200c\u06a9\u0646\u06cc\u0645 \u0648\u06af\u0631\u0646\u0647
            # `page._nav_who` \u06a9\u0647\u0646\u0647 \u0645\u06cc\u200c\u0645\u0627\u0646\u062f \u0648 Back\u0650 \u0628\u0639\u062f\u06cc \u0627\u0634\u062a\u0628\u0627\u0647 \u0645\u06cc\u200c\u0631\u0648\u062f.
            # \u0627\u0645\u0646: \u0627\u06af\u0631 views \u062e\u0627\u0644\u06cc \u0628\u0648\u062f\u060c `[-1]` \u0645\u0633\u062a\u0642\u06cc\u0645 IndexError \u0645\u06cc\u200c\u062f\u0627\u062f
            # \u0648 \u00ab\u0627\u0633\u06a9\u0631\u06cc\u0646 \u0645\u0646\u0647\u0627\u06cc \u06cc\u06a9\u00bb \u0631\u0627 \u0645\u06cc\u200c\u0633\u0627\u062e\u062a \u2014 \u062d\u0627\u0644\u0627 \u0637\u0648\u0644 \u0686\u06a9 \u0645\u06cc\u200c\u0634\u0648\u062f.
            vs = getattr(page, "views", None)
            if vs is not None and len(vs) > 0:
                vs[-1].controls = [root]
                page.update()
                _nav_mark(page, back)
            else:
                try:
                    page.add(root)
                    page.update()
                    _nav_mark(page, back)
                except Exception:
                    pass
        except Exception:
            pass"""

if old_fallback in txt:
    txt = txt.replace(old_fallback, new_fallback)
    print("patched _go fallback")
else:
    print("fallback not found")
    # debug: search partial
    import re
    print("count _go", txt.count("page.views[-1].controls"))

old_lock = """    try:
        _go(page, card, back=None)
        page.views[-1].can_pop = False
        page.update()
    except Exception:
        _go(page, card)"""

new_lock = """    try:
        _go(page, card, back=None)
        vs = getattr(page, "views", None)
        if vs is not None and len(vs) > 0:
            try:
                vs[-1].can_pop = False
            except Exception:
                pass
        page.update()
    except Exception:
        _go(page, card)"""

if old_lock in txt:
    txt = txt.replace(old_lock, new_lock)
    print("patched lock can_pop")
else:
    print("lock not found")

# _wire_back replacement - use markers
old_wire_start = "def _wire_back(page):"
# We'll replace entire function via splitting
# Simpler: find old_wire block by reading lines
lines = txt.splitlines()
# Find _wire_back definition line
start = None
end = None
for i, l in enumerate(lines):
    if l.strip().startswith("def _wire_back(page):"):
        start = i
    if start is not None and i > start and l.strip().startswith("def _toast("):
        end = i
        break

if start is not None and end is not None:
    old_block = "\n".join(lines[start:end])
    # check old on_view_pop presence
    print(f"_wire_back block lines {start} to {end}, len {len(old_block)}")
    new_block = r'''def _wire_back(page):
    """دکمه‌ی Back سیستمیِ گوشی را به پشته‌ی خودمان وصل می‌کند.

    ⚠️ نکته‌ی حیاتی (از سورسِ flet-0.86.5 خوانده شد): `_handleSystemPopRoute`
    فقط وقتی `view_pop` می‌فرستد که `views.length > 1` باشد و *آخری* را
    pop می‌کند. پس `_go` پشته را همیشه `[حضانتی، ریشه]` نگه می‌دارد —
    ریشه دیده می‌شود (Navigator آخری را نشان می‌دهد) و Back، ریشه را pop
    می‌کند و `view_pop` به این‌جا می‌آید. این تابع *پیش* از برگشتِ نرم،
    هم Viewی popشده را بازسازی می‌کند و هم یک Viewِ حضانتیِ *تازه* پشتِ
    ریشه می‌نشاند؛ ترتیب نهایی دوباره `[حضانتی، ریشه]` است، پس صفحه‌ی
    سیاهِ Viewِ خالی (که کاربر گزارش کرد) هرگز دیده نمی‌شود و Backِ بعدی
    هم دوباره به ما می‌رسد نه به SystemNavigator.

    بازنویسیِ امنِ Back (درخواستِ کاربر):
      def on_view_pop(view):
          if len(page.views) > 1:
              page.views.pop()
              top_view = page.views[-1]
              page.go(top_view.route)
          else:
              pass
      page.on_view_pop = on_view_pop
    در این‌جا همان قاعده پیاده شد، ولی با پشته‌ی سفارشی `_nav_stack` و
    نگه‌داشتنِ View حضانتی — وگرنه اندروید با یک View مستقیم
    `SystemNavigator.pop()` = خروج از برنامه می‌کرد.
    """
    def _on_pop(view=None):
        # ⚠️ مهم‌ترین اصلاحِ بک: فلاتر در Backِ سیستمی *فقط* `page.views` را
        # نگاه می‌کند (نه `_dialogs`) — پس با یک دیالوگِ باز، بک به‌جای
        # بستنِ دیالوگ، کلِ صفحه را یک پله می‌برد عقب و دیالوگ روی صفحهٔ
        # تازه باقی می‌ماند. (سنجیده روی سورسِ flet-0.86.5: `_handleSystemPopRoute`
        # فقط `views.length` و `can_pop` را می‌خواند.) پس اول دیالوگ را
        # می‌بندیم و همان‌جا کار تمام می‌شود.
        if _close_top_dialog(page):
            # دیالوگ بسته شد — باید Viewِ popشده‌ی فلاتر را هم بازگردانیم،
            # وگرنه Navigator یک View کم دارد و دکمه‌ی بعدی مستقیم خروج می‌کند.
            try:
                vs = getattr(page, "views", None)
                if vs is not None and len(vs) == 1:
                    hold = next((v for v in list(vs)
                                 if getattr(v, "route", "") == "/hold"), None)
                    if hold is None:
                        hold = ft.View(route="/hold", padding=0, bgcolor=P["bg0"])
                    seq = next(_nav_seq)
                    page.views.clear()
                    page.views.extend([hold, ft.View(route=f"/app:{seq}", padding=0,
                                                     bgcolor=P["bg0"])])
                    page.update()
            except Exception:
                pass
            return
        # ── بازسازیِ امنِ Viewها (اصلِ درخواستِ کاربر: چکِ طول قبل از pop) ──
        try:
            vs = getattr(page, "views", None)
            views = list(vs or [])
            hold = next((v for v in views
                         if getattr(v, "route", "") == "/hold"), None)
            if hold is None:
                hold = ft.View(route="/hold", padding=0, bgcolor=P["bg0"])
            seq = next(_nav_seq)
            if vs is not None:
                try:
                    page.views.clear()
                except Exception:
                    pass
                page.views.extend([hold, ft.View(route=f"/app:{seq}", padding=0,
                                                 bgcolor=P["bg0"])])
                page.update()
            else:
                pass
        except Exception:
            pass
        try:
            _soft_back(page)
        except Exception:
            pass

    # ── Escape / Back سخت‌افزاری روی وب و ویندوز ──
    def _on_kb(e):
        try:
            k = getattr(e, "key", None) or getattr(e, "data", None) or ""
            k = str(k)
            if k.lower() in ("escape", "esc", "back"):
                if _close_top_dialog(page):
                    return
                _soft_back(page)
        except Exception:
            pass

    try:
        page.on_view_pop = _on_pop
    except Exception:
        pass
    try:
        page.on_keyboard_event = _on_kb
    except Exception:
        pass'''

    txt = "\n".join(lines[:start]) + "\n" + new_block + "\n\n\n" + "\n".join(lines[end:])
    print("patched _wire_back")
else:
    print("wire block not found")

if txt != orig:
    p.write_text(txt, encoding="utf-8")
    print("wrote main.py")
else:
    print("no changes")
