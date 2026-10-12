import pathlib, py_compile, sys, re

txt = pathlib.Path(r"D:\coding\messenger\client\main.py").read_text(encoding="utf-8")
lines = txt.splitlines()
# Find any remaining [-1] that is not guarded
unguarded = []
for i,l in enumerate(lines,1):
    if "[-1]" in l:
        ctx = "\n".join(lines[max(0,i-6):i+1])
        guarded = ("len(" in ctx or "if vs" in ctx or "if _nav_stack" in ctx or "_nav_same" in ctx or "if not _nav_stack" in ctx or "if _nav_stack else" in ctx)
        tag = "guarded" if guarded else "UNGUARDED"
        unguarded.append((i, tag, l.strip()))

for i, tag, l in unguarded:
    print(f"{i:5d} {tag:9s} | {l[:160]}")

# Check view rebuild correctness: should never do page.views[-1] without len check
# List exact lines where page.views[-1] appears
print("\n--- page.views[-1] exact ---")
for i,l in enumerate(lines,1):
    if "page.views[-1]" in l or "vs[-1]" in l:
        print(f"{i:5d} | {l}")

# Check on handlers
print("\n--- handlers ---")
print("on_view_pop" if "on_view_pop" in txt else "NO on_view_pop")
print("on_keyboard_event" if "on_keyboard_event" in txt else "NO on_keyboard_event")
# Count Back related
print(f"_nav_stack refs: {txt.count('_nav_stack')}")
print(f"_soft_back refs: {txt.count('_soft_back')}")
print(f"_wire_back refs: {txt.count('_wire_back')}")
# Check immutable laws: no build folder edit, no secret hardcode
# look for assignment of TELEGRAM_BOT_TOKEN
if re.search(r"TELEGRAM_BOT_TOKEN\s*=", txt):
    print("WARN: hardcoded TELEGRAM_BOT_TOKEN found!")
else:
    print("OK: no hardcoded TELEGRAM_BOT_TOKEN")
# check build exclusion
print("main.py size", len(txt))

# Verify the new _wire_back docstring contains requested snippet
if "if len(page.views) > 1:" in txt:
    print("OK: requested snippet pattern present")
else:
    print("NO: requested snippet pattern missing")

# Ensure no Persian literal in code outside i18n/t/log.warning (allowed) - just count
persian_lines = 0
for i,l in enumerate(lines,1):
    if re.search(r"[\u0600-\u06FF]", l) and "t(" not in l and "log.warning" not in l and '"""' not in l and "'''" not in l and "# " in l:
        # comments are allowed, skip pure comment
        if l.lstrip().startswith("#"):
            continue
        persian_lines += 1
print(f"persian literal outside allowed: {persian_lines}")
