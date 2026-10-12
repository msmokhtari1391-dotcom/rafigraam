"""آشکارساز: متدهای async فلت که بدونِ await صدا زده شده‌اند."""
import ast
import inspect
import pkgutil
import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path("client")))

import flet as ft

ASYNC_NAMES = {}
for mod in list(sys.modules.values()) + [ft]:
    pass


def collect(pkg_name="flet"):
    pkg = importlib.import_module(pkg_name)
    for m in pkgutil.walk_packages(pkg.__path__, pkg_name + "."):
        try:
            mo = importlib.import_module(m.name)
        except Exception:
            continue
        for _, cls in inspect.getmembers(mo, inspect.isclass):
            if cls.__module__.split(".")[0] != "flet":
                continue
            for nm, fn in cls.__dict__.items():
                if nm.startswith("_"):
                    continue
                if inspect.iscoroutinefunction(fn):
                    ASYNC_NAMES.setdefault(nm, set()).add(cls.__name__)


collect()
# متدهایی که در کدِ ما هم‌نام دارند و مالِ فلت نیستند را دستی کنار می‌گذاریم
OURS = {"drain", "connect", "send", "close", "load", "save",
        "get", "set", "clear", "remove", "update", "append", "pop", "items",
        "keys", "values", "index", "count", "copy", "json", "read", "write",
        "start", "stop", "run", "cancel", "add", "discard", "extend"}
names = {n for n in ASYNC_NAMES if n not in OURS}
print(f"async flet method names: {len(names)}")

bad = []
for path in sorted(Path("client").rglob("*.py")):
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    awaited = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Await) and isinstance(node.value, ast.Call):
            awaited.add(id(node.value))
        # page.run_task(fn, ...) → ارجاع، نه صدا زدن
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or id(node) in awaited:
            continue
        f = node.func
        if isinstance(f, ast.Attribute) and f.attr in names:
            owner = ast.unparse(f.value)
            if owner.startswith(("api", "ws_client", "prefs", "crypto", "self._api")):
                continue
            bad.append(f"{path}:{node.lineno}: {ast.unparse(node)[:90]}"
                       f"   [{','.join(sorted(ASYNC_NAMES[f.attr]))[:60]}]")

print(f"un-awaited candidates: {len(bad)}")
for b in bad:
    print("  " + b)
