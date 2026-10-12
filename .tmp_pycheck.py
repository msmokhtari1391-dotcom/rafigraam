import pathlib, sys

# Read and check syntax with compile
files_to_check = [
    r"D:\coding\messenger\client\main.py",
    r"D:\coding\messenger\client\i18n.py",
    r"D:\coding\messenger\client\ui.py",
    r"D:\coding\messenger\client\config.py",
]

print("=== Syntax check with py_compile ===")
for f in files_to_check:
    try:
        compile(pathlib.Path(f).read_text(encoding="utf-8"), f, "exec")
        print(f"  OK: {f}")
    except SyntaxError as e:
        print(f"  SYNTAX ERROR {f}: {e}")
    except Exception as e:
        print(f"  ERROR {f}: {e}")

# Check specific patterns
print("\n=== Pattern checks on main.py ===")
txt = pathlib.Path(r"D:\coding\messenger\client\main.py").read_text(encoding="utf-8")
lines = txt.splitlines()

# Check for [-1] usage
print("\n--- views[-1] and page.views[-1] lines ---")
for i, l in enumerate(lines, 1):
    if "views[-1]" in l or "page.views[-1]" in l:
        print(f"  Line {i}: {l[:200]}")

# Check for on_view_pop handler
print("\n--- on_view_pop handler check ---")
if "on_view_pop" in txt:
    print("  on_view_pop IS present in main.py")
else:
    print("  on_view_pop IS NOT present in main.py")

# Check for on_keyboard_event handler
print("\n--- on_keyboard_event handler check ---")
if "on_keyboard_event" in txt:
    print("  on_keyboard_event IS present in main.py")
else:
    print("  on_keyboard_event IS NOT present in main.py")

# Check the requested pattern
print("\n--- Requested safe pop pattern ---")
if "if len(page.views) > 1:" in txt:
    print("  Pattern 'if len(page.views) > 1:' IS present")
else:
    print("  Pattern 'if len(page.views) > 1:' NOT present")

# Check for hard-coded secrets
print("\n--- Secret token check ---")
import re
# Check that no raw token assignments exist without os.environ
if re.search(r"TELEGRAM_BOT_TOKEN\s*=\s*", txt):
    print("  WARN: Possible hardcoded TELEGRAM_BOT_TOKEN")
else:
    print("  OK: No obvious hardcoded TELEGRAM_BOT_TOKEN assignment")

if re.search(r"GITHUB_TOKEN\s*=\s*", txt):
    print("  WARN: Possible hardcoded GITHUB_TOKEN")
else:
    print("  OK: No obvious hardcoded GITHUB_TOKEN assignment")

# Check for Persian strings outside t() and comments
print("\n--- Persian literal check ---")
persian_count = 0
for i, l in enumerate(lines, 1):
    # Check if line contains Persian characters (U+0600-U+06FF)
    has_persian = any('\u0600' <= ch <= '\u06FF' for ch in l)
    if has_persian:
        # Skip if in comment
        stripped = l.lstrip()
        if stripped.startswith('#'):
            continue
        # Skip if in t() call or log.warning
        if 't("' in l or 't(\'' in l or 'log.warning' in l:
            continue
        persian_count += 1

print(f"  Persian literals outside allowed contexts: {persian_count}")

# Check ft.Image without cache
raw_image = txt.count("ft.Image(")
print(f"  ft.Image( count: {raw_image}")

# Check build folder exclusion
print("\n--- build folder exclusion ---")
if "client\\build" in txt or "client_build" in txt:
    print("  WARNING: client\\build reference found in main.py")
else:
    print("  OK: No client\\build reference found")

print("\n=== Done ===")