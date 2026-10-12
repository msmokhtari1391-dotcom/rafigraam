"""اثباتِ ریشه‌ی خطای «Frozen controls cannot be updated.» با موتور واقعیِ Flet."""
import msgpack
import flet as ft
from flet.controls.base_control import BaseControl
from flet.controls.object_patch import ObjectPatch
from flet.messaging.protocol import configure_encode_object_for_msgpack

enc = configure_encode_object_for_msgpack(BaseControl)


def serialize(tree):
    """همان کاری که session برای فرستادنِ درخت می‌کند — snapshotها ساخته می‌شوند."""
    return msgpack.packb(tree, default=enc)


def diff(tree):
    """همان کاری که session.update() می‌کند: diff درخت با خودش."""
    return ObjectPatch.from_diff(tree, tree, control_cls=BaseControl)


def build_bubble(mid, keyed):
    inner = ft.Container(ft.Text(f"msg {mid}"), bgcolor="#202020")
    kw = {"key": ft.ScrollKey(mid)} if keyed else {}
    return ft.Container(ft.Row([inner]), bgcolor="#101010", **kw)


def scenario(keyed):
    lv = ft.ListView(controls=[build_bubble("m1", keyed), build_bubble("m2", keyed)])
    root = ft.Container(lv)
    serialize(root)                      # mount
    diff(root)                           # اولین update

    # همان کاری که _render() می‌کند: حباب‌ها از نو ساخته می‌شوند
    lv.controls = [build_bubble("m1", keyed), build_bubble("m2", keyed),
                   build_bubble("m3", keyed)]
    diff(root)                           # update بعدی → reconciliation کلیدی

    b0 = lv.controls[0]
    frozen_outer = hasattr(b0, "_frozen")
    row = b0.content
    frozen_row = hasattr(row, "_frozen")
    frozen_inner = hasattr(row.controls[0], "_frozen")

    err = None
    try:
        b0.bgcolor = "#ff0000"   # همان کاری که _jump_to می‌کند: box.bgcolor = ...
    except Exception as ex:
        err = f"{type(ex).__name__}: {ex}"

    err2 = None
    try:
        b0.scale = 1.1           # همان کاری که reaction_chip در hover می‌کند
    except Exception as ex:
        err2 = f"{type(ex).__name__}: {ex}"

    print(f"keyed={keyed}: frozen(bubble,row,inner)="
          f"({frozen_outer},{frozen_row},{frozen_inner})")
    print(f"   bubble.bgcolor= -> {err}")
    print(f"   bubble.scale=   -> {err2}")


scenario(keyed=False)
scenario(keyed=True)


# ── راهکار: کلید روی یک لنگرِ صفربُعدیِ بدونِ فرزند، نه روی حبابِ قابلِ تغییر ──
def build_anchored(mid, n):
    anchor = ft.Container(width=0, height=0, key=ft.ScrollKey(mid))
    inner = ft.Container(ft.Text(f"msg {mid} v{n}"), bgcolor="#202020")
    return ft.Container(ft.Row([anchor, inner]), bgcolor="#101010")


def scenario_fix():
    lv = ft.ListView(controls=[build_anchored("m1", 1), build_anchored("m2", 1)])
    root = ft.Container(lv)
    serialize(root)
    diff(root)
    lv.controls = [build_anchored("m1", 2), build_anchored("m2", 2),
                   build_anchored("m3", 1)]
    diff(root)

    b0 = lv.controls[0]
    row = b0.content
    anchor, inner = row.controls
    errs = []
    for label, obj, field, val in (("wrap.bgcolor", b0, "bgcolor", "#ff0000"),
                                   ("inner.bgcolor", inner, "bgcolor", "#00ff00"),
                                   ("inner.scale", inner, "scale", 1.1),
                                   ("inner.content", inner, "content",
                                    ft.Text("x"))):
        try:
            setattr(obj, field, val)
            errs.append(f"{label}=OK")
        except Exception as ex:
            errs.append(f"{label}=RAISED({ex})")
    print(f"anchored fix: frozen(wrap,anchor,inner)="
          f"({hasattr(b0, '_frozen')},{hasattr(anchor, '_frozen')},"
          f"{hasattr(inner, '_frozen')})")
    print("   " + " | ".join(errs))


scenario_fix()
