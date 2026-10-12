import pathlib

p = pathlib.Path(r"D:\coding\messenger\client\main.py")
txt = p.read_text(encoding="utf-8")
lines = txt.splitlines()

# Count braces/indentation issues
print("=== File structure analysis ===")
# Find function definitions
in_wire_back = False
brace_count = 0
for i, l in enumerate(lines, 1):
    if 'def _wire_back(page):' in l:
        in_wire_back = True
        print(f"Line {i}: {l}")
    if in_wire_back:
        # Count braces to check balance
        brace_count += l.count('{') - l.count('}')
        if brace_count < 0:
            print(f"Line {i}: NEGATIVE braces after: {l[:80]}")
            brace_count = 0  # reset to not cascade
        if 'def _toast(' in l and i > 1450:
            print(f"Line {i}: End of _wire_back area: {l[:80]}")
            print(f"  brace_count at exit: {brace_count}")
            in_wire_back = False

# Also check the _on_kb function was properly added
print()
print("=== _on_kb function presence ===")
for i, l in enumerate(lines, 1):
    if '_on_kb' in l:
        print(f"Line {i}: {l[:100]}")

# Check try/except balance
print()
print("=== try/except balance ===")
try_count = 0
except_count = 0
for i, l in enumerate(lines, 1):
    if 'try:' in l:
        try_count += 1
    if 'except' in l:
        except_count += 1
print(f"try blocks: {try_count}")
print(f"except blocks: {except_count}")

# The main issue: the kb_code insertion added lines that broke the try/except structure
# Let's see lines around 1476-1492
print()
print("=== Lines 1470-1505 ===")
for i in range(1469, min(1506, len(lines)+1)):
    print(f"{i:5d} | {lines[i-1][:150]}")