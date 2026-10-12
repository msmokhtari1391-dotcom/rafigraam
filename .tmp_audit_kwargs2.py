import ast
import json

FIELDS = json.load(open('D:/coding/messenger/.tmp_flet_fields.json', encoding='utf-8'))
ROOT = 'D:/coding/messenger/client/'

tree = ast.parse(open(ROOT + 'ui.py', encoding='utf-8').read())
hits = []
for node in ast.walk(tree):
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and getattr(node.func.value, 'id', None) == 'ft'):
        cls = node.func.attr
        if cls not in FIELDS:
            continue
        for kw in node.keywords:
            if kw.arg and kw.arg not in FIELDS[cls]:
                hits.append((node.lineno, cls, kw.arg))
print('unknown:', hits)