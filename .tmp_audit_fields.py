"""Rebuild a name -> field-set map from the installed flet controls package."""
import json
import os
import re

BASE = os.path.join('D:/coding/messenger/.venv/Lib/site-packages/flet/controls')
SEP = chr(92)  # backslash


def files():
    out = []
    for dp, dn, fn in os.walk(BASE):
        for f in fn:
            if f.endswith('.py'):
                out.append(os.path.join(dp, f))
    return out


FIELD = re.compile(r'^[ \t]+(\w+)[ \t]*:', re.M)
CLS = re.compile(r'^class[ \t]+(\w+)[ \t]*(?:\(([^)]*)\))?[ \t]*:', re.M)


def rank(p):
    return ('canvas' in p, 'cupertino' in p, len(p), p)


def build():
    classes = {}
    for fp in sorted(files(), key=rank):
        raw = open(fp, encoding='utf-8', errors='ignore').read().replace('\r\n', '\n')
        ms = list(CLS.finditer(raw))
        for i, m in enumerate(ms):
            name = m.group(1)
            if name in classes:
                continue
            seg = raw[m.end():ms[i + 1].start() if i + 1 < len(ms) else len(raw)]
            bases = [b.strip() for b in (m.group(2) or '').split(',') if b.strip()]
            classes[name] = (set(FIELD.findall(seg)), bases)
    return classes


def allfields(classes, name, seen=None):
    if seen is None:
        seen = set()
    if name in seen or name not in classes:
        return set()
    seen.add(name)
    fs, bases = classes[name]
    out = set(fs)
    for b in bases:
        out |= allfields(classes, b, seen)
    return out


if __name__ == '__main__':
    classes = build()
    data = {k: sorted(allfields(classes, k)) for k in classes}
    with open('D:/coding/messenger/.tmp_flet_fields.json', 'w', encoding='utf-8') as f:
        json.dump(data, f)
    for c in ['Text', 'TextButton', 'TextField', 'Container', 'Image',
              'ProgressBar', 'Switch', 'AlertDialog', 'GridView', 'Row', 'Icon']:
        print(c, len(data.get(c, [])), 'height' in data.get(c, []))