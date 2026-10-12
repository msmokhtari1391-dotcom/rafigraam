#!/usr/bin/env python3
"""
Rebuild patches on the ORIGINAL main.py (restored via git checkout).
This applies all the fixes in order, being careful about structure.
"""

import pathlib, sys

p = pathlib.Path(r"D:\coding\messenger\client\main.py")
txt = p.read_text(encoding="utf-8")
lines = txt.splitlines()

print(f"Original file has {len(lines)} lines")

# 1. Patch 1: _go fallback (around line 1272-1282)
# Find the exact pattern
old_fallback_start = None
for i, l in enumerate(lines, 1):
    if 'page.views[-1].controls = [root]' in l and i > 1260 and i < 1290:
        old_fallback_start = i
        break

if old_fallback_start:
    print(f"Patch 1: Found fallback at line {old_fallback_start}")
    # Replace lines old_fallback_start to old_fallback_start+10
    # The old code is:
    #         try:
    #             page.views[-1].controls = [root]
    #             page.update()
    #             _nav_mark(page, back)
    #         except Exception:
    #             pass
    # New code with guard
    new_fallback = '''        try:
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
            pass'''

    # Replace the exact block
    # Find the exact old block ending
    block_end = old_fallback_start + 10
    while block_end < len(lines) and lines[block_end-1].strip() != 'except Exception:' and 'pass' not in lines[block_end-1]:
        block_end += 1
    if block_end < len(lines):
        # Replace from old_fallback_start to block_end
        new_lines = lines[:old_fallback_start-1] + [new_fallback] + lines[block_end:]
        if len(new_lines) == len(lines) + new_fallback.count('\n') - (block_end - old_fallback_start + 1):
            print(f"  Applied patch 1: replaced {block_end - old_fallback_start + 1} lines with {new_fallback.count(chr(10)) + 1} lines")
            lines = new_lines
            txt = '\n'.join(lines)
            print("  Patch 1 applied")

# 2. Patch 2: _show_lock_screen can_pop (around line 4369-4373)
# Find old pattern
for i, l in enumerate(lines, 1):
    if 'page.views[-1].can_pop = False' in l and i > 4360 and i < 4380:
        print(f"Patch 2: Found can_pop at line {i}")
        # Replace the 3 lines
        old_block = '\n'.join(lines[i-1:i+2])
        new_block = '''        vs = getattr(page, "views", None)
        if vs is not None and len(vs) > 0:
            try:
                vs[-1].can_pop = False
            except Exception:
                pass
        page.update()'''
        # Replace i-1, i, i+1 (3 lines) with new_block lines
        new_lines = lines[:i-2] + [new_block.strip()] + lines[i+2:]
        if len(new_lines) != len(lines):
            lines = new_lines
            txt = '\n'.join(lines)
            print("  Patch 2 applied (can_pop)")
            break

# 3. Patch 3: Add on_keyboard_event handler after _wire_back block
# Find the line with "page.on_view_pop = _on_pop" and add keyboard handler after the except
insert_after = None
for i, l in enumerate(lines, 1):
    if 'page.on_view_pop = _on_pop' in l:
        # The next line should be "except Exception:" or similar
        # Let's look for it
        for j in range(i+1, min(i+5, len(lines)+1)):
            if 'except Exception:' in lines[j-1]:
                insert_after = j  # insert after this except line
                break
        if insert_after:
            break

if insert_after:
    print(f"Patch 3: Found insertion point after line {insert_after}")
    # The keyboard handler code
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

    # Insert after insert_after line
    new_lines = lines[:insert_after] + [kb_code.strip()] + lines[insert_after:]
    if len(new_lines) > len(lines):
        lines = new_lines
        txt = '\n'.join(lines)
        print("  Patch 3 applied (on_keyboard_event)")
    else:
        print("  Patch 3: no lines added (already present?)")
else:
    print("Patch 3: Could not find insertion point - checking if already present")
    if 'on_keyboard_event' in txt:
        print("  on_keyboard_event already present - skipping")
    else:
        print("  Could not find where to insert")

# Save the file
if 'on_keyboard_event' in txt or True:  # always save to verify
    p.write_text(txt, encoding="utf-8")
    print(f"\nSaved main.py with {len(lines)} lines")
    print("Done - patches applied")
else:
    print("No changes made to file")