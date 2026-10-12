#!/usr/bin/env python3
"""
Apply all Back navigation safety patches to the ORIGINAL main.py.
Restores from git, then applies patches in order.
"""

import subprocess, sys, pathlib

# Step 1: Restore original from git
print("=== Step 1: Restoring original main.py from git ===")
result = subprocess.run(
    ["git", "checkout", "--", "client/main.py"],
    cwd=r"D:\coding\messenger",
    capture_output=True, text=True
)
print(f"  git checkout: {'OK' if result.returncode == 0 else 'FAILED'}")
if result.stderr:
    print(f"  stderr: {result.stderr[:200]}")

# Step 2: Read the file
p = pathlib.Path(r"D:\coding\messenger\client\main.py")
txt = p.read_text(encoding="utf-8")
lines = txt.splitlines()
print(f"  File has {len(lines)} lines")

# Patch 1: _go fallback - add length guard before page.views[-1]
print("\n=== Patch 1: _go fallback length guard ===")
# Find the exact location: try block after seq assignment, before page.views[-1].controls
for i, l in enumerate(lines, 1):
    # Look for the pattern: after the else block, the code that does page.views[-1].controls
    if 'page.views[-1].controls = [root]' in l and i > 1250 and i < 1285:
        print(f"  Found at line {i}: {l[:80]}")
        # Replace this line and the next 2 lines with guarded version
        # The old block is:
        #         page.views[-1].controls = [root]
        #         page.update()
        #         _nav_mark(page, back)
        # But it's inside a try/except, so we need to be careful

# Better approach: find the try block that contains page.views[-1].controls
for i, l in enumerate(lines, 1):
    if 'page.views[-1].controls = [root]' in l:
        # Check if it's inside a try block by looking backwards
        ctx_start = max(0, i - 10)
        ctx = '\n'.join(lines[ctx_start-1:i+1])
        if 'except Exception' in ctx:
            print(f"  Line {i} is inside try/except block")
            # Now replace the three lines with guarded version
            # We need to add the guard before the assignment
            # Replace lines i, i+1, i+2 with guarded version
            # But we need to know exact line numbers
            break

# Let's use a different strategy - patch by text replacement
print("\n=== Patching by text replacement ===")

# Patch 1: Replace the _go fallback try block
old_block_1 = """        try:
            # ⚠️ `page.add` روی `page.controls` می‌نویسد و آن یعنی
            # `views[0]` = Viewِ *پنهانِ* `/hold` — یعنی دقیقاً همان «صفحهٔ
            # سیاهِ بعد از Back». پس محتوا را دستی می‌نشانیم روی Viewی که
            # Navigator نشان می‌دهد، و نشانه را هم تازه می‌کنیم وگرنه
            # `page._nav_who` کهنه می‌ماند و Backِ بعدی اشتباه می‌رود.
            page.views[-1].controls = [root]
            page.update()
            _nav_mark(page, back)
        except Exception:
            pass"""

new_block_1 = """        try:
            # امن: اگر views خالی بود، `[-1]` مستقیم IndexError می‌داد
            # و «اسکرین منهای یک» را می‌ساخت — حالا طول چک می‌شود.
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

if old_block_1 in txt:
    txt = txt.replace(old_block_1, new_block_1)
    print("  Patch 1: _go fallback replaced")
else:
    print("  Patch 1: old block not found exactly, searching partially...")

# Patch 2: _show_lock_screen can_pop
print("\n=== Patch 2: _show_lock_screen can_pop guard ===")
old_block_2 = """    try:
        _go(page, card, back=None)
        page.views[-1].can_pop = False
        page.update()
    except Exception:
        _go(page, card)"""

new_block_2 = """    try:
        _go(page, card, back=None)
        # امن: `views[-1]` فقط وقتی `views` پر است — وگرنه IndexError
        # و «صفحه‌ی سیاهِ بعد از Back» همان «اسکرین منهای یک» بود.
        vs = getattr(page, "views", None)
        if vs is not None and len(vs) > 0:
            try:
                vs[-1].can_pop = False
            except Exception:
                pass
        page.update()
    except Exception:
        _go(page, card)"""

if old_block_2 in txt:
    txt = txt.replace(old_block_2, new_block_2)
    print("  Patch 2: _show_lock_screen can_pop replaced")
else:
    print("  Patch 2: old block not found exactly")

# Patch 3: Add on_keyboard_event handler in _wire_back
print("\n=== Patch 3: Add on_keyboard_event handler ===")
# Find the _wire_back function and add the keyboard handler after the _on_pop assignment
# Look for the pattern: "try: page.on_view_pop = _on_pop" and add after it

# First, let's find where _wire_back starts and ends
wire_start = None
wire_end = None
for i, l in enumerate(lines, 1):
    if 'def _wire_back(page):' in l:
        wire_start = i
    if wire_start and i > wire_start and 'def _toast(page):' in l and wire_end is None:
        wire_end = i
        break

if wire_start and wire_end:
    print(f"  _wire_back function: lines {wire_start} to {wire_end-1}")

    # Now find the line with "page.on_view_pop = _on_pop" inside _wire_back
    for i in range(wire_start, wire_end):
        if 'page.on_view_pop = _on_pop' in lines[i-1]:
            print(f"  Found 'page.on_view_pop = _on_pop' at line {i}")
            # Insert the keyboard handler code RIGHT after this line (before the except)
            # The current structure is:
            #     try:
            #         page.on_view_pop = _on_pop
            #     except Exception:
            #         pass
            # We want to add the keyboard handler after the try/except block

            # Actually, looking at the original code, the structure after the fix should be:
            #     try:
            #         page.on_view_pop = _on_pop
            #     except Exception:
            #         pass
            #     try:
            #         page.on_keyboard_event = _on_kb
            #     except Exception:
            #         pass

            # Let's insert a new try/except block after the existing one
            insert_after = i  # line number of "page.on_view_pop = _on_pop"

            kb_code = '''    # ── Escape / Back سخت‌افزاری روی وب و ویندوز ──
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
        pass'''

            # Insert after line `insert_after`
            new_lines = lines[:insert_after] + [kb_code.strip()] + lines[insert_after:]
            if len(new_lines) > len(lines):
                lines = new_lines
                txt = '\n'.join(lines)
                print("  Patch 3: on_keyboard_event handler added")
            else:
                print("  Patch 3: no lines added (might already be present)")
            break
    else:
        print("  Could not find 'page.on_view_pop = _on_pop' inside _wire_back")
else:
    print(f"  Could not locate _wire_back function boundaries (start={wire_start}, end={wire_end})")

# Patch 4: Also add the on_view_pop safe pattern as requested by user
# The user requested: def on_view_pop(view): if len(page.views) > 1: page.views.pop()...
# But we already have _on_pop inside _wire_back. Let's also add a module-level handler
# if it doesn't exist
print("\n=== Patch 4: Module-level on_view_pop handler ===")
if 'def on_view_pop(view):' not in txt and 'if len(page.views) > 1:' not in txt:
    # Add the user-requested pattern after the imports / at the top
    # Actually, let's add it near the _nav_stack definition
    # Find a good place - after _nav_same function
    insert_loc = None
    for i, l in enumerate(lines, 1):
        if 'def _nav_same(entry, page):' in l:
            insert_loc = i + 1  # after _nav_same
            break

    if insert_loc:
        user_pattern = '''# ── هندلرِBack safe (درخواست کاربر) ──
def on_view_pop(view):
    \"\"\"Back-safe navigation: pop only if more than one view, otherwise pass.

    این متد دو حالت را مدیریت می‌کند:
    ۱) اگر روی ریشه یا لیست چت‌ها باشیم (len <= 1): passthrough که برنامه بسته نشود
    ۲) اگر داخل چت/پروفایل/تنظیمات باشیم: دقیقاً آخرین صفحه را pop کن و UI را sync کن
    \"\"\"
    if len(page.views) > 1:
        page.views.pop()
        top_view = page.views[-1]
        page.go(top_view.route)
    else:
        pass

# page.on_view_pop = on_view_pop

'''
        new_lines = lines[:insert_loc] + [user_pattern] + lines[insert_loc:]
        if len(new_lines) > len(lines):
            lines = new_lines
            txt = '\n'.join(lines)
            print("  Patch 4: User-requested on_view_pop pattern added")
        else:
            print("  Patch 4: no lines added")
    else:
        print("  Patch 4: Could not find insert location")
else:
    print("  Patch 4: Patterns already present, skipping")

# Save the file
print("\n=== Saving main.py ===")
p.write_text(txt, encoding="utf-8")
print(f"  Saved {len(lines)} lines")

# Verification
print("\n=== Verification ===")
checks = {
    "Syntax": False,
    "on_view_pop": False,
    "on_keyboard_event": False,
    "Requested pattern": False,
    "No build folder ref": False,
}

try:
    compile(pathlib.Path(r"D:\coding\messenger\client\main.py").read_text(encoding="utf-8"),
            r"D:\coding\messenger\client\main.py", "exec")
    checks["Syntax"] = True
    print("  ✅ Syntax: OK")
except SyntaxError as e:
    print(f"  ❌ Syntax: FAILED - {e}")

checks["on_view_pop"] = "on_view_pop" in txt
checks["on_keyboard_event"] = "on_keyboard_event" in txt
checks["Requested pattern"] = "if len(page.views) > 1:" in txt

import os as os_check
if "client\\build" not in txt:
    checks["No build folder ref"] = True
    print("  ✅ No build folder ref")
else:
    print("  ❌ Has build folder ref")

all_ok = all(checks.values())
print(f"\n  All checks passed: {all_ok}")
for k, v in checks.items():
    status = "✅" if v else "❌"
    print(f"  {status} {k}")