#!/usr/bin/env python3
import pathlib, sys

p = pathlib.Path(r"D:\coding\messenger\client\main.py")
txt = p.read_text(encoding="utf-8")
lines = txt.splitlines()
print(f"Total lines: {len(lines)}")

# Patch 1: Lines 1278-1280 (inside try/except fallback) - add guard
print("\n=== Patch 1: Lines 1278-1280 guard ===")
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

old_block_1 = """            page.views[-1].controls = [root]
            page.update()
            _nav_mark(page, back)"""

# Check if old block exists at the expected location
# Lines 1278-1280 in 1-indexed = indices 1277-1279
old_block_lines = lines[1277:1280]
old_block_text = '\n'.join(old_block_lines)

if old_block_text == old_block_1:
    print("  Old block matched at lines 1278-1280")
    # Replace lines 1278-1280 (indices 1277-1279) with new content
    new_block_1_lines = new_block_1.split('\n')
    lines = lines[:1277] + new_block_1_lines + lines[1280:]
    txt = '\n'.join(lines)
    p.write_text(txt, encoding="utf-8")
    print("  Patch 1 applied")
else:
    print(f"  Old block NOT matched. Got: {old_block_lines[:3]}")

# Patch 2: Lines 1262-1266 (before try/except) - add guard
print("\n=== Patch 2: Lines 1262-1266 guard ===")
# Check if line 1262 still has original code
if lines[1261].strip() == "page.views[-1].controls = [root]":
    print("  Line 1262 has original code")
    # Replace lines 1262-1266 (indices 1261-1265) with guarded version
    old_block_2 = lines[1261:1266]  # 5 lines
    new_block_2 = """        # امن: اگر views خالی بود، `[-1]` مستقیم IndexError میadott
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

    if old_block_2 == lines[1261:1266]:
        print("  Old block 2 matched at lines 1262-1266")
        # Replace lines 1262-1266 (5 lines) with new block (about 13 lines)
        new_block_2_lines = new_block_2.split('\n')
        lines = lines[:1261] + new_block_2_lines + lines[1266:]
        txt = '\n'.join(lines)
        p.write_text(txt, encoding="utf-8")
        print("  Patch 2 applied")
    else:
        print(f"  Block 2 mismatch. Expected: {old_block_2[:3]}, Got: {lines[1261:1266][:3]}")
else:
    print("  Line 1262 already modified")

# Patch 3: _show_lock_screen can_pop - find and replace
print("\n=== Patch 3: _show_lock_screen can_pop ===")
# Look for the pattern: try: _go(page, card, back=None) / page.views[-1].can_pop = False / page.update()
# This might be in _show_lock_screen function

# Search for the pattern in the file
search_text = """    try:
        _go(page, card, back=None)
        page.views[-1].can_pop = False
        page.update()
    except Exception:
        _go(page, card)"""

if search_text in txt:
    print("  Found can_pop pattern via text replacement")
    new_can_pop = """    try:
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

    txt = txt.replace(search_text, new_can_pop)
    lines = txt.splitlines()
    p.write_text(txt, encoding="utf-8")
    print("  Patch 3 applied via text replacement")
else:
    print("  can_pop pattern not found via text search - searching...)")
    # Try to find it anyway
    for i, l in enumerate(lines, 1):
        if 'can_pop' in l and 'views[-1]' in l:
            print(f"  Found can_pop at line {i}: {l[:80]}")

# Save
p.write_text(txt, encoding="utf-8")
print(f"\nSaved main.py with {len(lines)} lines")

# Verification
print("\n=== Verification ===")
txt2 = p.read_text(encoding="utf-8")
lines2 = txt2.splitlines()

checks = {
    "Syntax": False,
    "Patch 1 (1278-1280)": False,
    "Patch 2 (1262-1266)": False,
    "Patch 3 (can_pop)": False,
    "on_keyboard_event": False,
}

try:
    compile(pathlib.Path(r"D:\coding\messenger\client\main.py").read_text(encoding="utf-8"),
            r"D:\coding\messenger\client\main.py", "exec")
    checks["Syntax"] = True
    print("  Syntax: OK")
except:
    print("  Syntax: FAILED")

# Check Patch 1
for i, l in enumerate(lines2, 1):
    if i >= 1277 and i <= 1285 and 'vs = getattr(page, "views", None)' in l:
        checks["Patch 1 (1278-1280)"] = True
        print(f"  Patch 1 at line {i}: PRESENT")
        break

# Check Patch 2
for i, l in enumerate(lines2, 1):
    if i >= 1261 and i <= 1270 and 'vs = getattr(page, "views", None)' in l:
        checks["Patch 2 (1262-1266)"] = True
        print(f"  Patch 2 at line {i}: PRESENT")
        break

# Check Patch 3
if 'vs = getattr(page, "views", None)' in txt2 and 'can_pop' in txt2:
    idx = txt2.index('can_pop')
    context = txt2[max(0,idx-80):idx+80]
    if 'vs is not None and len(vs) > 0' in context:
        checks["Patch 3 (can_pop)"] = True
        print("  Patch 3 (can_pop): PRESENT")
    else:
        print("  Patch 3 (can_pop): present but different context")
else:
    print("  Patch 3 (can_pop): MISSING")

# on_keyboard_event
checks["on_keyboard_event"] = "on_keyboard_event" in txt2
print(f"  on_keyboard_event: {'PRESENT' if checks['on_keyboard_event'] else 'MISSING'}")

all_pass = all(checks.values())
print(f"\nAll checks passed: {all_pass}")
for k, v in checks.items():
    status = "✅" if v else "❌"
    print(f"  {status} {k}")