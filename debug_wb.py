import pathlib, sys

# Read original backup and check structure
p = pathlib.Path(r"D:\coding\messenger\client\main.py")
txt = p.read_text(encoding="utf-8")
lines = txt.splitlines()

# Find the _wire_back function - it should start at line with "def _wire_back(page):"
# and end before _toast function
start_line = None
end_line = None
for i, l in enumerate(lines, 1):
    if 'def _wire_back(page):' in l:
        start_line = i
    if start_line and 'def _toast(page):' in l and end_line is None:
        end_line = i
        break

print(f"_wire_back function: lines {start_line} to {end_line-1}")

# The issue is that the keyboard handler insertion broke the structure.
# Let's restore a clean version by re-applying all patches in order.

# First, let's create a clean copy from the original audit file that was saved
# Actually, let's check what the original looked like before our patches
# We have the .tmp_audit.txt from earlier which had the original content

# Let's rebuild the _wire_back function properly
# The function should contain:
# 1. The docstring
# 2. _on_pop inner function
# 3. The try/except for setting page.on_view_pop = _on_pop

# Let's find and extract the original _on_pop function
for i, l in enumerate(lines, 1):
    if '_on_pop(view=None):' in l and i > 1400:
        print(f"_on_pop at line {i}: {l[:100]}")
        # Show surrounding context
        for j in range(max(1, i-5), min(len(lines)+1, i+30)):
            print(f"  {j}: {lines[j-1][:120]}")
        break