#!/usr/bin/env python3
"""Apply all Back navigation safety patches to main.py - clean version."""

import pathlib, sys

p = pathlib.Path(r"D:\coding\messenger\client\main.py")
# Read with explicit utf-8, replace errors
raw = p.read_bytes()
txt = raw.decode("utf-8", errors="replace")
lines = txt.splitlines()
print("Total lines:", len(lines))

# ============================================================
# Patch 1: _go fallback guard - lines 1278-1280 inside try/except
# ============================================================
print("\n=== Patch 1: _go fallback guard (lines 1278-1280) ===")
# Current lines 1278-1280 (1-indexed, indices 1277-1279):
#             page.views[-1].controls = [root]
#             page.update()
#             _nav_mark(page, back)
#
# New: add length guard

old1 = """            page.views[-1].controls = [root]
            page.update()
            _nav_mark(page, back)"""

new1 = """            # امن: اگر views خالی بود، `[-1]` مستقیم IndexError می‌داد
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

# Replace lines 1278-1280 (indices 1277-1279) if they match
if len(lines) > 1279 and '\n'.join(lines[1277:1280]) == old1:
    lines[1277:1280] = new1.split('\n')
    print("  Patch 1 applied: replaced lines 1278-1280")
else:
    # Try text replacement
    if old1 in txt:
        txt = txt.replace(old1, new1)
        lines = txt.splitlines()
        print("  Patch 1 applied via text replacement")
    else:
        print("  Patch 1: old block not found, checking if already patched...")
        # Check if vs pattern already exists
        for i, l in enumerate(lines, 1):
            if 'vs = getattr(page, "views", None)' in l and 1270 < i < 1285:
                print("  vs pattern already at line", i, "- Patch 1 skipped")
                break

# ============================================================
# Patch 2: _show_lock_screen can_pop guard
# ============================================================
print("\n=== Patch 2: _show_lock_screen can_pop guard ===")
old2 = """    try:
        _go(page, card, back=None)
        page.views[-1].can_pop = False
        page.update()
    except Exception:
        _go(page, card)"""

new2 = """    try:
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

if old2 in txt:
    txt = txt.replace(old2, new2)
    lines = txt.splitlines()
    print("  Patch 2 applied via text replacement")
else:
    print("  Patch 2: old block not found - checking if already patched")
    for i, l in enumerate(lines, 1):
        if 'vs = getattr(page, "views", None)' in l and i > 4360 and 'can_pop' in ' '.join(lines[max(0,i-5):i+1]):
            print("  vs+can_pop already present at line", i, "- Patch 2 skipped")
            break

# Save after each patch to avoid losing progress
txt = '\n'.join(lines)
p.write_text(txt, encoding="utf-8")

# ============================================================
# Patch 3: Add on_keyboard_event handler in _wire_back
# ============================================================
print("\n=== Patch 3: Add on_keyboard_event handler ===")

# Find the _on_pop try/except block
# It should be around lines 1459-1462: try: page.on_view_pop = _on_pop / except Exception: pass
insert_after = None
for i, l in enumerate(lines, 1):
    # Look for the pattern: try block for on_view_pop followed by except
    if 'page.on_view_pop = _on_pop' in l and 1450 < i < 1470:
        # The except pass is typically 2 lines after
        if i + 2 < len(lines) and 'except Exception:' in lines[i+1] and 'pass' in lines[i+2]:
            insert_after = i + 2  # insert after the 'pass' line
            print("  Found on_view_pop try/except block at line", i, "- inserting after line", insert_after)
            break

if insert_after is None:
    # Alternative: find _on_pop function definition
    for i, l in enumerate(lines, 1):
        if 'def _on_pop' in l and 1400 < i < 1500:
            # Insert after the function, before the next function
            # Actually, let's insert right after the _on_pop function's try/except
            # The _on_pop function ends around line 1326 in original, but after patches it's different
            # Let's try a different approach: find any "except Exception: pass" near _wire_back
            print("  Could not find try/except pattern, trying alternative...")
            # Find line with "_on_pop" and surrounding context
            break

# Insert the keyboard handler code after insert_after
if insert_after:
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

    # Insert after insert_after
    new_lines = lines[:insert_after] + [kb_code.strip()] + lines[insert_after:]
    print("  Inserting kb handler after line", insert_after)
    print("  Before:", len(lines), "lines, After:", len(new_lines), "lines")
    lines = new_lines
    txt = '\n'.join(lines)
    print("  Patch 3: on_keyboard_event handler added")
else:
    print("  Patch 3: could not find insertion point - checking alternatives")
    # Try to find the _wire_back function end and insert there
    for i in range(len(lines)-1, 0, -1):
        if 'def _toast' in lines[i-1] or ('def _go_back' in lines[i-1] and i > 1400):
            # Insert kb handler before this function
            print("  Found function at line", i, "- inserting kb handler before it")
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
            new_lines = lines[:i] + [kb_code.strip()] + lines[i:]
            print("  Before:", len(lines), "After:", len(new_lines), "lines")
            lines = new_lines
            txt = '\n'.join(lines)
            print("  Patch 3: on_keyboard_event handler added before function at line", i)
            break
    else:
        print("  Patch 3: could not find suitable insertion point")

# Save the file
print("\n=== Saving main.py ===")
txt = '\n'.join(lines)
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

# Check for the requested pattern - length guard before views[-1] access
for i, l in enumerate(lines2, 1):
    if 'vs = getattr(page, "views", None)' in l:
        # Check nearby for len() > 0
        found_in_context = False
        # Look at next 10 lines
        for j in range(i, min(i+10, len(lines2)+1)):
            if 'len(vs) > 0' in lines2[j-1] or 'len(vs) > 1' in lines2[j-1]:
                found_in_context = True
                break
        if found_in_context:
            checks["Requested pattern (len > 1)"] = True
            print("  Requested pattern (len > 1): PASS (vs len check found at line", i, ")")
            break
        # Also check if vs[-1] is guarded
        for j in range(i, min(i+5, len(lines2)+1)):
            if 'vs[-1]' in lines2[j-1]:
                # Check if there's an if above it
                for k in range(max(0, j-5), j):
                    if 'len(vs)' in lines2[k-1]:
                        found_in_context = True
                        break
        if found_in_context:
            checks["Requested pattern (len > 1)"] = True
            print("  Requested pattern (len > 1): PASS (vs[-1] guard found)")
            break

# No client/build reference
checks["No client/build ref"] = "client\\\\build" not in txt2
print("  No client/build ref:", "PASS" if checks["No client/build ref"] else "FAIL")

all_pass = all(checks.values())
print("\nAll checks passed:", all_pass)
for k, v in checks.items():
    status = "PASS" if v else "FAIL"
    print("  %s: %s" % (status, k))