import pathlib, sys

p = pathlib.Path(r"D:\coding\messenger\client\main.py")
txt = p.read_text(encoding="utf-8")

# Check if on_keyboard_event already exists
if "on_keyboard_event" in txt:
    print("on_keyboard_event ALREADY present in main.py")
else:
    print("on_keyboard_event NOT present - will add it")

# Find location to insert - after _wire_back block
lines = txt.splitlines()

# Search for the pattern that marks the end of _wire_back
insert_idx = None
for i, l in enumerate(lines, 1):
    # Look for the line after page.on_view_pop = _on_pop
    if 'page.on_view_pop = _on_pop' in l:
        # The next few lines should be the try/except block
        # We'll insert after the except block
        # Let's check a few lines ahead
        for j in range(i, min(i+10, len(lines))):
            if 'try:' in lines[j] and j > i:
                # Found the try block, insert after it
                # But let's be more precise - look for the pattern after _wire_back
                pass
        # Actually let's just insert after line 1470 which is where _on_pop assignment was
        insert_idx = 1470
        break

if insert_idx:
    # Insert the keyboard event handler
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

    new_lines = lines[:insert_idx] + [kb_code.strip()] + lines[insert_idx:]
    new_txt = "\n".join(new_lines)

    if txt != new_txt:
        p.write_text(new_txt, encoding="utf-8")
        print(f"Added on_keyboard_event handler after line {insert_idx}")
    else:
        print("No change made")
else:
    print("Could not find insertion point")

# Verify
print("\n=== Final checks ===")
if "on_keyboard_event" in p.read_text(encoding="utf-8"):
    print("✅ on_keyboard_event IS NOW present")
else:
    print("❌ on_keyboard_event still NOT present")

if "on_view_pop" in p.read_text(encoding="utf-8"):
    print("✅ on_view_pop IS present")
else:
    print("❌ on_view_pop NOT present")

print("\nDone")