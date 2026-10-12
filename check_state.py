import pathlib

p = pathlib.Path(r"D:\coding\messenger\client\main.py")
txt = p.read_text(encoding="utf-8")
lines = txt.splitlines()
print("Total lines:", len(lines))

on_view_pop = "on_view_pop" in txt
on_keyboard_event = "on_keyboard_event" in txt
# Check for the vs pattern differently
has_vs = False
for l in lines:
    if "vs" in l and "getattr" in l and "views" in l:
        has_vs = True
        break

print("on_view_pop:", on_view_pop)
print("on_keyboard_event:", on_keyboard_event)
print("has vs pattern:", has_vs)

# Find _wire_back and _on_pop
for i, l in enumerate(lines, 1):
    if "_wire_back(page):" in l:
        print("_wire_back starts at line", i)
    if "def _on_pop" in l and 1450 < i < 1500:
        print("_on_pop at line", i)

# Show lines around 1460
print()
print("Lines 1455-1475:")
for i in range(1454, min(1476, len(lines)+1)):
    print("  %5d | %s" % (i, lines[i-1][:100]))

# Search for can_pop
print()
print("Searching for can_pop:")
for i, l in enumerate(lines, 1):
    if "can_pop" in l:
        print("  Line %d: %s" % (i, l[:80]))