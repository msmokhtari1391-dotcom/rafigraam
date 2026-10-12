import pathlib

p = pathlib.Path(r"D:\coding\messenger\client\main.py")
txt = p.read_text(encoding="utf-8")
lines = txt.splitlines()
print("Total lines:", len(lines))

# The _wire_back function starts at line 1437
# Inside it, at line 1460, we have: page.on_view_pop = _on_pop
# We need to add the on_keyboard_event handler after the try/except block for _on_pop

# Find line with "page.on_view_pop = _on_pop"
insert_after = None
for i, l in enumerate(lines, 1):
    if 'page.on_view_pop = _on_pop' in l and 1450 < i < 1480:
        insert_after = i
        break

print("Insert after line:", insert_after)

if insert_after:
    # The keyboard handler code
    kb_code = """    # ── Escape / Back سخت‌افزاری روی وب و ویندوز ──
    # `page.on_view_pop` روی اندروید می‌آید، ولی روی وب/ویندوز کلیدِ Escape
    # باید دستی شنود شود — وگرنه کاربر در مرورگر گیر می‌کند.
    # این هندلر همان `_soft_back` را صدا می‌زند، پس AppBar و Backِ سیستمی
    # هر دو از یک مسیرِ امن می‌گذرند و هرگز `history[-1]` روی لیستِ خالی نمی‌خورد.
    def _on_kb(e):
        try:
            k = getattr(e, "key", None) or getattr(e, "data", None) or ""
            k = str(k)
            # Flet روی وب Escape را "Escape" و روی ویندوز "Esc" می‌فرستد؛ هر دو
            # را می‌گیریم. Backِ اندروید اصلاً این‌جا نمی‌آید (view_pop).
            if k.lower() in ("escape", "esc", "back"):
                if _close_top_dialog(page):
                    return
                _soft_back(page)
        except Exception:
            pass

    try:
        page.on_keyboard_event = _on_kb
    except Exception:
        pass"""

    # Insert after insert_after line
    new_lines = lines[:insert_after] + [kb_code.strip()] + lines[insert_after:]
    print("Before:", len(lines), "After:", len(new_lines))
    lines = new_lines
    txt = '\n'.join(lines)
    p.write_text(txt, encoding="utf-8")
    print("on_keyboard_event handler added")

# Verify
txt2 = p.read_text(encoding="utf-8")
print("on_keyboard_event now present:", "on_keyboard_event" in txt2)
print("on_view_pop still present:", "on_view_pop" in txt2)

# Check syntax
import py_compile
try:
    compile(pathlib.Path(r"D:\coding\messenger\client\main.py").read_text(encoding="utf-8"),
            r"D:\coding\messenger\client\main.py", "exec")
    print("Syntax: OK")
except SyntaxError as e:
    print("Syntax ERROR:", e)