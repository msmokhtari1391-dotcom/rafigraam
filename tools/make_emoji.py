"""ساختِ دادگانِ ایموجی — از فایلِ رسمیِ یونیکد + نامِ فارسیِ کتابخانه‌ی emoji.

    python tools/make_emoji.py [مسیرِ emoji-test.txt]

خروجی: client/emoji_data.py (دسته‌بندی + نامِ انگلیسی و فارسی برای جست‌وجو).
دادگان به‌صورتِ ماژولِ ثابت نوشته می‌شه تا برنامه به کتابخانه‌ی emoji نیازی
نداشته باشه (مهم برای بسته‌ی exe).
"""
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
OUT = os.path.join(ROOT, "client", "emoji_data.py")
URL = "https://unicode.org/Public/emoji/15.1/emoji-test.txt"

# گروه‌های یونیکد → کلیدِ ما + گلیفِ زبانه (به ترتیبِ نمایش)
GROUPS = [
    ("Smileys & Emotion", "smileys", "😀"),
    ("People & Body", "people", "🧑"),
    ("Animals & Nature", "nature", "🐻"),
    ("Food & Drink", "food", "🍔"),
    ("Travel & Places", "travel", "🚗"),
    ("Activities", "activity", "⚽"),
    ("Objects", "objects", "💡"),
    ("Symbols", "symbols", "❤️"),
    ("Flags", "flags", "🚩"),
]
GROUP_KEY = {g: k for g, k, _ in GROUPS}


def _source(path=None):
    if path and os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            return f.read()
    cache = os.path.join(ROOT, ".tmp", "emoji-test.txt")
    if os.path.isfile(cache):
        with open(cache, encoding="utf-8") as f:
            return f.read()
    print("downloading", URL)
    data = urllib.request.urlopen(URL, timeout=40).read().decode("utf-8")
    os.makedirs(os.path.dirname(cache), exist_ok=True)
    with open(cache, "w", encoding="utf-8") as f:
        f.write(data)
    return data


def _fa_names():
    """{ایموجی: نامِ فارسی} از کتابخانه‌ی emoji (اگه نصب بود)."""
    try:
        import emoji
        from emoji import unicode_codes as uc
        uc.load_from_json("fa")
    except Exception as ex:                              # pragma: no cover
        print("WARN: no Persian names —", ex)
        return {}
    out = {}
    for ch, val in emoji.EMOJI_DATA.items():
        fa = val.get("fa")
        if fa:
            out[ch] = fa.strip(":").replace("_", " ")
    return out


def main():
    text = _source(sys.argv[1] if len(sys.argv) > 1 else None)
    fa_map = _fa_names()
    buckets = {k: [] for _, k, _ in GROUPS}
    names = {}
    group = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("# group:"):
            group = GROUP_KEY.get(line.split(":", 1)[1].strip())
            continue
        if not line or line.startswith("#") or group is None:
            continue
        try:
            codes, rest = line.split(";", 1)
            status, comment = rest.split("#", 1)
        except ValueError:
            continue
        if status.strip() != "fully-qualified":
            continue
        bits = comment.strip().split(" ", 2)
        if len(bits) < 3:
            continue
        ch, en = bits[0], bits[2].strip()
        if "skin tone" in en:            # تن‌های پوست را جدا نشان نمی‌دهیم
            continue
        buckets[group].append(ch)
        names[ch] = (en, fa_map.get(ch, ""))

    total = sum(len(v) for v in buckets.values())
    lines = ['"""دادگانِ ایموجی — خودکار ساخته شده با tools/make_emoji.py."""',
             "# ruff: noqa: E501",
             "",
             "# (کلید، گلیفِ زبانه)",
             "GROUPS = ["]
    for _, k, glyph in GROUPS:
        lines.append(f'    ({k!r}, {glyph!r}),')
    lines.append("]")
    lines.append("")
    lines.append("EMOJI = {")
    for _, k, _g in GROUPS:
        lines.append(f"    {k!r}: [")
        row = []
        for ch in buckets[k]:
            row.append(f"{ch!r},")
            if len(row) == 12:
                lines.append("        " + " ".join(row))
                row = []
        if row:
            lines.append("        " + " ".join(row))
        lines.append("    ],")
    lines.append("}")
    lines.append("")
    lines.append("# ایموجی → (نامِ انگلیسی، نامِ فارسی) برای جست‌وجو")
    lines.append("NAMES = {")
    for ch, (en, fa) in names.items():
        lines.append(f"    {ch!r}: ({en!r}, {fa!r}),")
    lines.append("}")
    lines.append("")
    lines.append("ALL = [c for k, _g in GROUPS for c in EMOJI[k]]")
    lines.append("")
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("SAVED", OUT, total, "emojis",
          {k: len(v) for k, v in buckets.items()},
          "fa:", sum(1 for v in names.values() if v[1]))


if __name__ == "__main__":
    main()
