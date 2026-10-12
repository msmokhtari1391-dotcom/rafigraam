#!/usr/bin/env python3
"""Final patch: add on_keyboard_event to main.py"""

import pathlib, sys

p = pathlib.Path(r"D:\coding\messenger\client\main.py")
# Read with explicit utf-8 and handle encoding
try:
    txt = p.read_text(encoding="utf-8")
except Exception:
    # Fallback: read as bytes and decode
    raw = p.read_bytes()
    txt = raw.decode("utf-8", errors="replace")

lines = txt.splitlines()
print("Total lines:", len(lines))

# Find the right location to insert on_keyboard_event
# It should go inside _wire_back, after the _on_pop try/except block
# Let's find "page.on_view_pop = _on_pop" assignment

insert_idx = None
for i, l in enumerate(lines, 1):
    # Look for the assignment pattern
    if 'page.on_view_pop = _on_pop' in l and 1450 < i < 1470:
        insert_idx = i
        print("Found 'page.on_view_pop = _on_pop' at line", i)
        break

if insert_idx is None:
    print("Could not find insertion point - checking alternatives...")
    # Try finding _on_pop function start
    for i, l in enumerate(lines, 1):
        if 'def _on_pop' in l and 1400 < i < 1500:
            print("Found _on_pop at line", i)
            insert_idx = i
            break

if insert_idx is None:
    print("No insertion point found - exiting")
    sys.exit(1)

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

# Insert the code after insert_idx
# The current structure after line insert_idx is typically:
# ... page.on_view_pop = _on_pop
# ... except Exception: pass
# We want to add the kb handler after this entire block

# Let's insert after insert_idx lines
new_lines = lines[:insert_idx] + [kb_code.strip()] + lines[insert_idx:]

print("Before:", len(lines), "lines")
print("After:", len(new_lines), "lines")

lines = new_lines
txt = '\n'.join(lines)
p.write_text(txt, encoding="utf-8")

# Verify
txt2 = p.read_text(encoding="utf-8")
print("\nVerification:")
print("  on_view_pop present:", "on_view_pop" in txt2)
print("  on_keyboard_event present:", "on_keyboard_event" in txt2)
print("  if len(page.views) > 1 present:", "if len(page.views) > 1:" in txt2)
print("  ft.Image count:", txt2.count("ft.Image("))

# Check syntax
try:
    compile(pathlib.Path(r"D:\coding\messenger\client\main.py").read_text(encoding="utf-8"),
            r"D:\coding\messenger\client\main.py", "exec")
    print("Syntax: OK")
except SyntaxError as e:
    print("Syntax ERROR:", e)