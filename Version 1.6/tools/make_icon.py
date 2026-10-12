"""آیکونِ برنامه — هواپیمای کاغذیِ تلگرامی با رنگِ رفیگرام.

    python tools/make_icon.py
خروجی: client/assets/icon.png (1024) و client/assets/icon.ico (چند اندازه).
"""
import math
import os

from PIL import Image, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.abspath(os.path.join(HERE, "..", "client", "assets"))
S = 1024                      # بومِ اصلی (بعد کوچک می‌شه)
SS = 4                        # ابَرنمونه‌برداری برای لبه‌های صاف
TOP = (255, 90, 88)           # قرمزِ روشنِ بالا
BOT = (168, 18, 26)           # قرمزِ تیره‌ی پایین


def _lerp(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def _rounded_gradient(size, radius):
    """مربعِ گردگوشه با گرادیانِ مورب (مثل کاشیِ آیکونِ تلگرام)."""
    grad = Image.new("RGB", (size, size))
    px = grad.load()
    for y in range(size):
        for x in range(size):
            t = (x / size * 0.35 + y / size * 0.65)
            px[x, y] = _lerp(TOP, BOT, min(1.0, max(0.0, t)))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, size - 1, size - 1), radius=radius, fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(grad, (0, 0), mask)
    return out


def _plane(size):
    """هواپیمای کاغذی: بدنه‌ی سفید + بالِ سایه‌دار (سه‌بعدیِ تلگرام)."""
    lay = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    k = size / 96.0                      # مختصات‌ها روی بومِ ۹۶ طراحی شدن

    def p(pts):
        return [(x * k, y * k) for x, y in pts]

    # بدنه‌ی اصلی — نوکِ راست، دنباله‌ی چپ، بریدگیِ پایین
    body = p([(20.0, 47.6), (74.6, 24.4), (66.0, 73.6),
              (49.6, 59.0), (41.0, 67.6), (40.6, 53.4)])
    d.polygon(body, fill=(255, 255, 255, 252))
    # لبه‌ی نرم دورِ بدنه
    d.line(body + [body[0]], fill=(255, 255, 255, 252),
           width=int(2.4 * k), joint="curve")
    # بالِ تاشده (کمی تیره‌تر، حسِ تاخوردنِ کاغذ)
    wing = p([(40.6, 53.4), (68.2, 29.0), (49.6, 59.0)])
    d.polygon(wing, fill=(238, 168, 168, 235))
    return lay


def main():
    big = S * SS
    card = _rounded_gradient(big, int(big * 0.235))
    # درخششِ ملایمِ گوشه‌ی بالا-چپ (شیشه‌ی مایع)
    glow = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse(
        (-big * 0.25, -big * 0.45, big * 0.85, big * 0.35),
        fill=(255, 255, 255, 46))
    glow = glow.filter(ImageFilter.GaussianBlur(big * 0.06))
    card = Image.alpha_composite(card, glow)

    shadow = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    sp = _plane(big).split()[3].point(lambda a: int(a * 0.42))
    shadow.paste((90, 8, 14, 255), (int(big * 0.012), int(big * 0.018)), sp)
    shadow = shadow.filter(ImageFilter.GaussianBlur(big * 0.012))
    card = Image.alpha_composite(card, shadow)
    card = Image.alpha_composite(card, _plane(big))

    icon = card.resize((S, S), Image.LANCZOS)
    os.makedirs(OUT, exist_ok=True)
    png = os.path.join(OUT, "icon.png")
    ico = os.path.join(OUT, "icon.ico")
    icon.save(png)
    icon.save(ico, sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                          (64, 64), (128, 128), (256, 256)])
    # نسخه‌ی کوچک برای نمایشِ درون‌برنامه‌ای
    icon.resize((256, 256), Image.LANCZOS).save(
        os.path.join(OUT, "icon_256.png"))
    print("SAVED", png, os.path.getsize(png))
    print("SAVED", ico, os.path.getsize(ico))
    print("angle-check", round(math.degrees(math.atan2(1, 2)), 1))


if __name__ == "__main__":
    main()
