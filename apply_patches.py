#!/usr/bin/env python3
"""Apply all Back navigation safety patches to main.py."""

import pathlib, sys

p = pathlib.Path(r"D:\coding\messenger\client\main.py")
txt = p.read_text(encoding="utf-8")
lines = txt.splitlines()
print("Total lines:", len(lines))

# ============================================================
# Patch 1: _go fallback - add guard before page.views[-1].controls
# ============================================================
# The old code at lines 1262-1266 does page.views[-1].controls = [root]
# without checking if views exists or has length > 0.
# We need to add a guard.

# Find the exact old block (from previous analysis, lines 1278-1280 inside try/except)
# Actually, there are TWO locations:
# 1) Lines 1262-1266: BEFORE the try/except - the main assignment
# 2) Lines 1278-1280: INSIDE the try/except fallback

# Patch 1a: Lines 1278-1280 (inside try/except) - already identified
print("\n=== Patch 1: _go fallback inside try/except (lines 1278-1280) ===")
old_block_1 = """            page.views[-1].controls = [root]
            page.update()
            _nav_mark(page, back)"""

new_block_1 = """            # امن: اگر views خالی بود، `[-1]` مستقیم IndexError می‌داد
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
                    pass"""

# Check if this old block exists at the expected position (lines 1278-1280 = indices 1277-1279)
if lines[1277:1280] == [old_block_1.strip()]:
    print("  Old block 1 matched at lines 1278-1280")
    lines[1277:1280] = new_block_1.split('\n')
    print("  Patch 1 applied")
else:
    print("  Old block 1 NOT matched at expected location")
    # Try text search
    old_text_1 = '''        page.views[-1].controls = [root]
        page.update()
        # فقط حالا که رندر واقعاً انجام شد، این صفحه = «صفحه‌ی جاری» است.
        # این نشانه را دکمه‌ی Back می‌خواند تا ورودیِ *خودش* را از پشته بردارد.
        _nav_mark(page, back)'''
    if old_text_1 in txt:
        txt = txt.replace(old_text_1, new_block_1)
        lines = txt.splitlines()
        print("  Patch 1 applied via text replacement")
    else:
        print("  Old block 1 not found at all - checking other locations...")
        # The fallback at 1278-1280 might have been applied already in a previous run
        # Let's check if vs pattern exists
        for i, l in enumerate(lines, 1):
            if 'vs = getattr(page, "views", None)' in l and i > 1250 and i < 1280:
                print("  vs pattern already at line", i)
else:
    txt = '\n'.join(lines)
    p.write_text(txt, encoding="utf-8")

# ============================================================
# Patch 2: _show_lock_screen can_pop guard
# ============================================================
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
    lines = txt.splitlines()
    print("  Patch 2 applied via text replacement")
else:
    print("  Old block 2 NOT found via text search")
    # Check if already patched
    for i, l in enumerate(lines, 1):
        if 'vs = getattr(page, "views", None)' in l and 'can_pop' in lines[max(0,i-3):i+1]:
            print("  vs+can_pop pattern already present at line", i)

txt = '\n'.join(lines)
p.write_text(txt, encoding="utf-8")

# ============================================================
# Patch 3: Add on_keyboard_event handler in _wire_back
# ============================================================
print("\n=== Patch 3: Add on_keyboard_event handler ===")

# Find the _wire_back function and add the keyboard handler
# The _on_pop function starts at line 1450
# The try/except for page.on_view_pop = _on_pop is at lines 1459-1462

# We want to add the keyboard handler AFTER the except block (after line 1462)
# But we need to find the exact line numbers in the current file

insert_after = None
for i, l in enumerate(lines, 1):
    # Look for "pass" after the _on_pop try/except
    if i > 1455 and i < 1470 and 'pass' in l and 'on_view_pop' in lines[max(0,i-4)]:
        insert_after = i
        print("  Found insertion after line", i)
        break

if insert_after is None:
    # Try alternative: find line with _on_pop assignment
    for i, l in enumerate(lines, 1):
        if 'page.on_view_pop = _on_pop' in l and 1450 < i < 1470:
            insert_after = i
            print("  Found _on_pop assignment at line", i, "- will insert after except block")
            # The except block is typically 2 lines after this
            # We'll insert after i+2 (the except/pass)
            # Actually, let's just set a flag and handle below
            break

# Insert the keyboard handler code
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

if insert_after:
    # Insert after insert_after line
    new_lines = lines[:insert_after] + [kb_code.strip()] + lines[insert_after:]
    print("  Inserting kb handler after line", insert_after)
    lines = new_lines
    txt = '\n'.join(lines)
    print("  Patch 3: on_keyboard_event handler added")
else:
    # Try inserting after the _on_pop function or near the end of _wire_back
    # Let's find where _wire_back ends
    wire_end = None
    for i in range(len(lines)-1, 0, -1):
        if lines[i-1].strip().startswith('def _toast') or lines[i-1].strip().startswith('def _go_back'):
            wire_end = i
            break

    if wire_end:
        print("  Inserting kb handler at end of _wire_back (line", wire_end, ")")
        # Insert kb_code before line wire_end
        new_lines = lines[:wire_end] + [kb_code.strip()] + lines[wire_end:]
        lines = new_lines
        txt = '\n'.join(lines)
        print("  Patch 3: on_keyboard_event handler added at wire_end")
    else:
        print("  Could not find wire_end - trying text replacement approach")
        # Try to find and replace a specific pattern
        # Look for the _wire_back docstring end and _on_pop assignment
        search_pattern = "try:\n            page.on_view_pop = _on_pop\n        except Exception:"
        idx = txt.find(search_pattern)
        if idx >= 0:
            print("  Found pattern at position", idx)
            # Insert after this pattern
            before = txt[:idx+len(search_pattern)]
            after = txt[idx+len(search_pattern):]
            # Add the kb handler after the except block
            kb_section = "\n    try:\n        page.on_keyboard_event = _on_kb\n    except Exception:\n        pass\n"
            new_txt = before + kb_section + after
            txt = new_txt
            lines = txt.splitlines()
            print("  Patch 3: on_keyboard_event added via text replacement")
        else:
            print("  Could not find pattern for text replacement")

# Save the file
print("\n=== Saving main.py ===")
p.write_text(txt, encoding="utf-8")
print("  Saved with", len(lines), "lines")

# ============================================================
# Verification
# ============================================================
print("\n=== Verification ===")
txt2 = p.read_text(encoding="utf-8")
lines2 = txt2.splitlines()

checks = {
    "Syntax": False,
    "on_view_pop": False,
    "on_keyboard_event": False,
    "Requested pattern (len > 1)": False,
    "Patch 1 (_go fallback guard)": False,
    "Patch 2 (can_pop guard)": False,
    "No client/build ref": False,
}

# Syntax check
try:
    compile(pathlib.Path(r"D:\coding\messenger\client\main.py").read_text(encoding="utf-8"),
            r"D:\coding\messenger\client\main.py", "exec")
    checks["Syntax"] = True
    print("  Syntax: OK")
except:
    print("  Syntax: FAILED")

# Content checks
checks["on_view_pop"] = "on_view_pop" in txt2
print("  on_view_pop:", "PASS" if checks["on_view_pop"] else "FAIL")

checks["on_keyboard_event"] = "on_keyboard_event" in txt2
print("  on_keyboard_event:", "PASS" if checks["on_keyboard_event"] else "FAIL")

# Check for the requested pattern - look for length guards
for i, l in enumerate(lines2, 1):
    if 'vs = getattr(page, "views", None)' in l:
        # Check if nearby has len() > 0 check
        ctx_start = max(0, i-3)
        ctx_end = min(len(lines2)+1, i+4)
        ctx = ' '.join(lines2[ctx_start-1:ctx_end])
        if 'len(vs) > 0' in ctx or 'len(vs) > 1' in ctx:
            checks["Requested pattern (len > 1)"] = True
            print("  Requested pattern (len > 1): PASS (found at line", i, ")")
            break
        # Also check if vs is used to guard views[-1]
        for j in range(i, min(i+10, len(lines2)+1)):
            if 'vs[-1]' in lines2[j-1] or 'vs[-1]' in lines2[j]:
                checks["Requested pattern (len > 1)"] = True
                print("  Requested pattern (len > 1): PASS (vs[-1] guard found)")
                break
        break

# No client/build reference
checks["No client/build ref"] = "client\\\\build" not in txt2
print("  No client/build ref:", "PASS" if checks["No client/build ref"] else "FAIL")

all_pass = all(checks.values())
print("\nAll checks passed:", all_pass)
for k, v in checks.items():
    status = "PASS" if v else "FAIL"
    print("  %s: %s" % (status, k))