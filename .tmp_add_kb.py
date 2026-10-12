import pathlib

p = pathlib.Path(r"D:\coding\messenger\client\main.py")
txt = p.read_text(encoding="utf-8")

# Check if on_keyboard_event already exists
if "on_keyboard_event" in txt:
    print("on_keyboard_event ALREADY present in main.py")
    # Check if it's near the _wire_back area
    lines = txt.splitlines()
    for i, l in enumerate(lines, 1):
        if "on_keyboard_event" in l:
            print(f"  Line {i}: {l[:180]}")
else:
    # Add on_keyboard_event handler AFTER the _wire_back block
    # Find the _wire_back function end and add the keyboard handler after it
    # The _wire_back function currently ends around line 1470+
    # We need to add the keyboard event setup after _wire_back completes

    # Strategy: find line with "try:" after page.on_view_pop = _on_pop
    # and add the keyboard event setup after that

    # Let's find the line numbers
    lines = txt.splitlines()
    kb_insert_after = None
    for i, l in enumerate(lines, 1):
        # Look for the pattern: after page.on_view_pop = _on_pop
        if i < len(lines) - 1 and 'page.on_view_pop = _on_pop' in l:
            # The next line should be "except Exception:" or similar
            kb_insert_after = i
            break

    if kb_insert_after:
        # Insert the keyboard event handler right after the _wire_back block
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

        # Insert after kb_insert_after line
        new_lines = lines[:kb_insert_after] + [kb_code.strip()] + lines[kb_insert_after:]
        new_txt = "\n".join(new_lines)

        if txt != new_txt:
            p.write_text(new_txt, encoding="utf-8")
            print(f"Added on_keyboard_event handler after line {kb_insert_after}")
        else:
            print("No change made - txt equals new_txt")
    else:
        print("Could not find insertion point for on_keyboard_event")

print("Done")