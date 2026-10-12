"""Report every ft.X(...) keyword that is not a real field of X in flet 0.86.5."""
import ast
import collections
import json

ROOT = 'D:/coding/messenger/client/'
FIELDS = json.load(open('D:/coding/messenger/.tmp_flet_fields.json', encoding='utf-8'))


class V(ast.NodeVisitor):
    def __init__(self):
        self.rows = []

    def visit_Call(self, node):
        fn = node.func
        cls = None
        if (isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name)
                and fn.value.id == 'ft'):
            cls = fn.attr
        if cls and cls in FIELDS:
            for kw in node.keywords:
                if kw.arg and kw.arg not in FIELDS[cls]:
                    self.rows.append((node.lineno, cls, kw.arg))
        self.generic_visit(node)


rows = []
for f in ['main.py', 'ui.py', 'editor.py', 'i18n.py']:
    tree = ast.parse(open(ROOT + f, encoding='utf-8').read())
    v = V()
    v.visit(tree)
    for ln, cls, kw in v.rows:
        rows.append((f, ln, cls, kw))

cnt = collections.Counter((r[2], r[3]) for r in rows)
out = []
for (cls, kw), n in sorted(cnt.items()):
    ln = sorted(set(r[1] for r in rows if r[2] == cls and r[3] == kw))
    fs = sorted(set(r[0] for r in rows if r[2] == cls and r[3] == kw))
    out.append('%s.%s  x%d  %s  lines=%s' % (cls, kw, n, fs, ln))
print('rows:', len(rows))
print('\n'.join(out))