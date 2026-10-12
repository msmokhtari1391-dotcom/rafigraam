import pathlib, py_compile, sys

files = [
    r"D:\coding\messenger\client\main.py",
    r"D:\coding\messenger\client\i18n.py",
    r"D:\coding\messenger\client\ui.py",
    r"D:\coding\messenger\client\config.py",
]
ok = True
for f in files:
    try:
        py_compile.compile(f, doraise=True)
        print(f"OK {f}")
    except Exception as e:
        print(f"FAIL {f}: {e}")
        ok = False

# Also check for forbidden patterns
import re
txt = pathlib.Path(r"D:\coding\messenger\client\main.py").read_text(encoding="utf-8")
# Check for literal secrets
bad = []
if "TELEGRAM_BOT_TOKEN" in txt and "os.environ" not in txt.split("TELEGRAM_BOT_TOKEN")[0][-200:] if "TELEGRAM_BOT_TOKEN" in txt else False:
    pass

# Check that no new ft.Image without cache
# naive: find ft.Image( not followed by cache_width
count_raw = txt.count("ft.Image(")
count_cache = txt.count("cache_width")
print(f"ft.Image count {count_raw}, cache_width count {count_cache}")

# Ensure i18n still has both fa/en
import i18n
# load outside

# Check views[-1] guards
lines = txt.splitlines()
unguarded = []
for i,l in enumerate(lines,1):
    if "views[-1]" in l or "page.views[-1]" in l:
        # check if previous 3 lines contain len check or if guarded
        ctx = "\n".join(lines[max(0,i-6):i])
        if "len(" not in ctx and "if vs" not in ctx:
            unguarded.append((i,l.strip()))

print(f"remaining views[-1] lines: {len(unguarded)}")
for i,l in unguarded[:20]:
    print(f"  {i}: {l}")

# Check on_view_pop and on_keyboard_event
print("on_view_pop" if "on_view_pop" in txt else "NO on_view_pop")
print("on_keyboard_event" if "on_keyboard_event" in txt else "NO on_keyboard_event")
print("ALL OK" if ok else "SOME FAIL")
