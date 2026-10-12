"""
تست زنده‌ی سرور برای موج B — نقش‌ها، کانال، جست‌وجو، واکنش، سنجاق،
بایگانی، عکس‌های پروفایل و رویدادهای ریل‌تایم.

اجرا:  .venv/Scripts/python.exe wb_server_test.py
"""
import asyncio
import io
import json
import sys
import time

import httpx
import websockets

BASE = "http://127.0.0.1:8000"
WS = "ws://127.0.0.1:8000/ws"
PW = "wbtest1234"
TAG = str(int(time.time()))[-6:]

ok_count = 0
fail: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> bool:
    global ok_count
    if cond:
        ok_count += 1
        print(f"  PASS  {name}")
    else:
        fail.append(f"{name} :: {extra}")
        print(f"  FAIL  {name}  {extra}")
    return bool(cond)


async def reg(c: httpx.AsyncClient, uname: str, dname: str) -> dict:
    r = await c.post(f"{BASE}/auth/register", json={
        "username": uname, "display_name": dname, "password": PW})
    if r.status_code == 409:
        r = await c.post(f"{BASE}/auth/login", json={
            "username": uname, "password": PW})
    r.raise_for_status()
    return r.json()


def H(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


async def main() -> int:
    async with httpx.AsyncClient(timeout=30) as c:
        A = await reg(c, f"wba{TAG}", "آلفا")
        B = await reg(c, f"wbb{TAG}", "بتا")
        C = await reg(c, f"wbc{TAG}", "گاما")
        ha, hb, hc = H(A["token"]), H(B["token"]), H(C["token"])
        print(f"\nusers: {A['username']} / {B['username']} / {C['username']}")

        # ── ۱) کانالِ عمومی با شناسه ────────────────
        print("\n[1] ساخت کانال عمومی + جست‌وجو")
        chan_handle = f"news{TAG}"
        r = await c.post(f"{BASE}/chat/create_group", headers=ha, json={
            "title": "کانالِ خبری", "kind": "channel",
            "is_public": True, "handle": chan_handle, "members": []})
        check("create public channel", r.status_code == 200, r.text)
        chan = r.json()["chat_id"]
        check("kind=channel", r.json().get("kind") == "channel", r.text)

        r = await c.post(f"{BASE}/chat/create_group", headers=ha, json={
            "title": "کانالِ تکراری", "kind": "channel",
            "is_public": True, "handle": chan_handle})
        check("duplicate handle -> 409", r.status_code == 409, r.text)

        r = await c.post(f"{BASE}/chat/create_group", headers=ha, json={
            "title": "بی‌شناسه", "kind": "channel", "is_public": True})
        check("public without handle -> 400", r.status_code == 400, r.text)

        # جست‌وجو با اسم
        r = await c.get(f"{BASE}/chat/search", headers=hb, params={"q": "کانالِ خبری"})
        hits = r.json()
        check("search public by title", any(h["chat_id"] == chan for h in hits), r.text)
        # جست‌وجو با @handle
        r = await c.get(f"{BASE}/chat/search", headers=hb, params={"q": "@" + chan_handle})
        hits = r.json()
        check("search by @handle", any(h["chat_id"] == chan for h in hits), r.text)
        check("hit not joined yet", all(not h["joined"] for h in hits if h["chat_id"] == chan), r.text)
        # جست‌وجوی کاربر
        r = await c.get(f"{BASE}/chat/search", headers=hb, params={"q": A["username"]})
        check("search finds user", any(h["kind"] == "user" and h["username"] == A["username"]
                                      for h in r.json()), r.text)

        # ── ۲) عضویت + محدودیتِ نوشتن در کانال ──────
        print("\n[2] عضویت در کانال و اجازه‌ی نوشتن")
        r = await c.post(f"{BASE}/chat/join", headers=hb, json={"handle": chan_handle})
        check("B joins public channel", r.status_code == 200 and r.json()["chat_id"] == chan, r.text)
        r = await c.post(f"{BASE}/chat/{chan}/send", headers=hb, json={"content": "من عضوم"})
        check("member cannot post in channel", r.status_code == 403, r.text)
        r = await c.post(f"{BASE}/chat/{chan}/send", headers=ha, json={"content": "خبر اول"})
        check("owner can post in channel", r.status_code == 200, r.text)
        r = await c.post(f"{BASE}/chat/{chan}/role", headers=ha,
                         json={"username": B["username"], "role": "admin"})
        check("promote B to admin", r.status_code == 200 and r.json()["role"] == "admin", r.text)
        r = await c.post(f"{BASE}/chat/{chan}/send", headers=hb, json={"content": "حالا می‌تونم"})
        check("admin can post in channel", r.status_code == 200, r.text)
        r = await c.get(f"{BASE}/chat/{chan}/profile", headers=hb)
        p = r.json()
        check("profile my_role=admin", p.get("my_role") == "admin", r.text[:200])
        check("profile can_post", p.get("can_post") is True, r.text[:200])
        check("profile roles listed",
              {m["username"]: m["role"] for m in p["members"]}.get(A["username"]) == "owner",
              json.dumps(p["members"], ensure_ascii=False)[:300])

        # تنزیل + محافظتِ هم‌رده
        r = await c.post(f"{BASE}/chat/{chan}/role", headers=hb,
                         json={"username": A["username"], "role": "member"})
        check("admin cannot demote owner", r.status_code == 403, r.text)

        # ── ۳) گروهِ خصوصی ─────────────────────────
        print("\n[3] گروه خصوصی")
        priv_handle = f"secret{TAG}"
        r = await c.post(f"{BASE}/chat/create_group", headers=ha, json={
            "title": "گروهِ خصوصی", "kind": "group", "is_public": False,
            "handle": priv_handle, "members": [C["username"]]})
        check("create private group", r.status_code == 200, r.text)
        grp = r.json()["chat_id"]
        r = await c.get(f"{BASE}/chat/search", headers=hb, params={"q": "گروهِ خصوصی"})
        check("private hidden from title search",
              all(h["chat_id"] != grp for h in r.json()), r.text)
        r = await c.get(f"{BASE}/chat/search", headers=hb, params={"q": "@" + priv_handle})
        check("private findable by exact @handle",
              any(h["chat_id"] == grp for h in r.json()), r.text)
        r = await c.post(f"{BASE}/chat/join", headers=hb, json={"handle": priv_handle})
        check("join private by handle", r.status_code == 200, r.text)

        # ── ۴) اخراج ───────────────────────────────
        print("\n[4] اخراج و رفع اخراج")
        r = await c.post(f"{BASE}/chat/{grp}/ban", headers=ha,
                         json={"username": C["username"], "banned": True})
        check("owner bans C", r.status_code == 200 and r.json()["banned"] is True, r.text)
        r = await c.get(f"{BASE}/chat/{grp}/messages", headers=hc)
        check("banned user blocked from messages", r.status_code == 403, r.text)
        r = await c.post(f"{BASE}/chat/join", headers=hc, json={"handle": priv_handle})
        check("banned user cannot rejoin", r.status_code == 403, r.text)
        r = await c.get(f"{BASE}/chat/list", headers=hc)
        check("banned chat hidden from list",
              all(x["chat_id"] != grp for x in r.json()), r.text[:200])
        r = await c.post(f"{BASE}/chat/{grp}/ban", headers=ha,
                         json={"username": C["username"], "banned": False})
        check("unban C", r.status_code == 200 and r.json()["banned"] is False, r.text)
        r = await c.get(f"{BASE}/chat/{grp}/messages", headers=hc)
        check("unbanned user can read", r.status_code == 200, r.text[:200])

        # ── ۵) عملیاتِ پیام ────────────────────────
        print("\n[5] ویرایش / واکنش / سنجاق / فوروارد / حذف")
        r = await c.post(f"{BASE}/chat/{grp}/send", headers=ha,
                         json={"content": "سلام گروه", "enc": True})
        check("send with enc flag", r.status_code == 200 and r.json()["enc"] is True, r.text)
        mid = r.json()["message_id"]

        r = await c.put(f"{BASE}/chat/{grp}/message/{mid}", headers=ha,
                        json={"content": "سلام گروه (ویرایش‌شده)"})
        check("edit own message", r.status_code == 200 and r.json()["edited"] is True, r.text)
        r = await c.put(f"{BASE}/chat/{grp}/message/{mid}", headers=hb,
                        json={"content": "دستکاری"})
        check("cannot edit others' message", r.status_code == 403, r.text)

        r = await c.post(f"{BASE}/chat/{grp}/message/{mid}/react", headers=hb,
                         json={"emoji": "🔥"})
        check("react adds", r.status_code == 200 and r.json()["reactions"].get("🔥") == 1, r.text)
        r = await c.post(f"{BASE}/chat/{grp}/message/{mid}/react", headers=ha,
                         json={"emoji": "🔥"})
        check("second react counts 2", r.json()["reactions"].get("🔥") == 2, r.text)
        r = await c.post(f"{BASE}/chat/{grp}/message/{mid}/react", headers=ha,
                         json={"emoji": "🔥"})
        check("react toggles off", r.json()["reactions"].get("🔥") == 1, r.text)

        r = await c.get(f"{BASE}/chat/{grp}/messages", headers=ha)
        msgs = r.json()
        m0 = next((m for m in msgs if m["message_id"] == mid), None)
        check("messages carry reactions", bool(m0) and m0["reactions"].get("🔥") == 1,
              json.dumps(m0, ensure_ascii=False)[:300] if m0 else "no msg")
        check("messages carry edited flag", bool(m0) and m0["edited"] is True, "")

        r = await c.post(f"{BASE}/chat/{grp}/message/{mid}/pin", headers=ha,
                         json={"pinned": True})
        check("owner pins", r.status_code == 200 and r.json()["pinned"] is True, r.text)
        r = await c.get(f"{BASE}/chat/{grp}/pinned", headers=hb)
        check("pinned message readable",
              r.status_code == 200 and (r.json() or {}).get("message_id") == mid, r.text[:200])

        r = await c.post(f"{BASE}/chat/{grp}/message/{mid}/forward", headers=ha,
                         json={"to_chat_id": chan})
        check("forward keeps origin",
              r.status_code == 200 and r.json()["forward_from"] == "آلفا", r.text)

        # حذفِ بی‌پارامتر = «فقط برای خودم» (طرف مقابل خبردار نمی‌شه)
        r = await c.delete(f"{BASE}/chat/{grp}/message/{mid}", headers=hb)
        check("member hides a message just for themselves",
              r.status_code == 200 and (r.json() or {}).get("for_everyone") is False,
              r.text)
        r = await c.get(f"{BASE}/chat/{grp}/messages", headers=hb)
        check("hidden message gone for the hider",
              not any(m["message_id"] == mid for m in r.json()), r.text[:160])
        r = await c.get(f"{BASE}/chat/{grp}/messages", headers=ha)
        check("hidden message still there for the other side",
              any(m["message_id"] == mid for m in r.json()), r.text[:160])

        r = await c.delete(f"{BASE}/chat/{grp}/message/{mid}?for_everyone=true",
                           headers=ha)
        check("sender deletes for everyone", r.status_code in (200, 404), r.text)
        r = await c.get(f"{BASE}/chat/{grp}/messages", headers=ha)
        check("deleted message leaves no trace",
              not any(m["message_id"] == mid for m in r.json())
              and not any(m["msg_type"] == "deleted" for m in r.json()),
              r.text[:200])

        # ── ۶) بایگانی ─────────────────────────────
        print("\n[6] بایگانی")
        r = await c.post(f"{BASE}/chat/{chan}/archive", headers=hb, json={"archived": True})
        check("archive chat", r.status_code == 200 and r.json()["archived"] is True, r.text)
        r = await c.get(f"{BASE}/chat/list", headers=hb)
        check("archived hidden from main list",
              all(x["chat_id"] != chan for x in r.json()), r.text[:200])
        r = await c.get(f"{BASE}/chat/list", headers=hb, params={"archived": "true"})
        rows = r.json()
        check("archived list shows it", any(x["chat_id"] == chan for x in rows), r.text[:300])
        check("archived flag in output",
              any(x["chat_id"] == chan and x["archived"] for x in rows), "")
        r = await c.get(f"{BASE}/chat/archived_count", headers=hb)
        check("archived_count", r.json().get("count") == 1, r.text)
        r = await c.post(f"{BASE}/chat/{chan}/archive", headers=hb, json={"archived": False})
        check("unarchive", r.json()["archived"] is False, r.text)

        # ── ۷) عکس‌های پروفایل ─────────────────────
        print("\n[7] چند عکسِ پروفایل")
        png = bytes.fromhex(
            "89504e470d0a1a0a0000000d494844520000000100000001080600000"
            "01f15c4890000000a49444154789c6360000002000154a24f8b000000"
            "0049454e44ae426082")
        urls = []
        for i in range(5):
            r = await c.post(f"{BASE}/profile/me/photos", headers=ha,
                             files={"file": (f"p{i}.png", io.BytesIO(png), "image/png")})
            if r.status_code == 200:
                urls.append(r.json()["url"])
        check("uploaded 5 photos", len(urls) == 5, str(len(urls)))
        r = await c.post(f"{BASE}/profile/me/photos", headers=ha,
                         files={"file": ("p6.png", io.BytesIO(png), "image/png")})
        check("6th photo rejected", r.status_code == 400, r.text)
        r = await c.get(f"{BASE}/profile/me/photos", headers=ha)
        check("photos listed", len(r.json()["urls"]) == 5, r.text[:200])
        r = await c.put(f"{BASE}/profile/me/photos/main", headers=ha, json={"url": urls[0]})
        check("set main photo", r.json()["avatar_url"] == urls[0], r.text[:200])
        check("main photo first in list", r.json()["photos"][0] == urls[0], "")
        r = await c.request("DELETE", f"{BASE}/profile/me/photos", headers=ha,
                           params={"url": urls[0]})
        check("delete photo", r.status_code == 200 and len(r.json()["photos"]) == 4, r.text[:200])
        r = await c.get(f"{BASE}/profile/{B['username']}", headers=ha)
        check("public profile has photos key", "photos" in r.json(), r.text[:200])

        # ── ۸) رویدادهای ریل‌تایم ──────────────────
        print("\n[8] رویدادهای ریل‌تایم روی وب‌سوکت")
        got: list[dict] = []

        async def listen(token: str, seconds: float) -> None:
            try:
                async with websockets.connect(f"{WS}?token={token}") as w:
                    end = time.monotonic() + seconds
                    while time.monotonic() < end:
                        try:
                            raw = await asyncio.wait_for(w.recv(), timeout=seconds)
                        except asyncio.TimeoutError:
                            return
                        got.append(json.loads(raw))
            except Exception as e:  # noqa: BLE001
                got.append({"type": "ws_error", "err": str(e)})

        task = asyncio.create_task(listen(B["token"], 6.0))
        await asyncio.sleep(1.2)
        await c.put(f"{BASE}/profile/me", headers=ha, json={"display_name": "آلفای تازه"})
        m2 = (await c.post(f"{BASE}/chat/{grp}/send", headers=ha,
                           json={"content": "پیامِ ریل‌تایم"})).json()["message_id"]
        await c.put(f"{BASE}/chat/{grp}/message/{m2}", headers=ha,
                    json={"content": "ویرایشِ ریل‌تایم"})
        await c.post(f"{BASE}/chat/{grp}/message/{m2}/react", headers=ha, json={"emoji": "❤️"})
        await c.post(f"{BASE}/chat/{grp}/message/{m2}/pin", headers=ha, json={"pinned": True})
        await c.delete(f"{BASE}/chat/{grp}/message/{m2}?for_everyone=true",
                       headers=ha)
        await asyncio.sleep(2.0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        types = [g.get("type") for g in got]
        print("   events:", types)
        pu = next((g for g in got if g.get("type") == "profile_updated"), None)
        check("profile_updated broadcast", bool(pu), str(types))
        check("profile_updated carries name",
              bool(pu) and pu.get("display_name") == "آلفای تازه", str(pu)[:200])
        check("profile_updated carries photos", bool(pu) and isinstance(pu.get("photos"), list), "")
        check("message event", "message" in types, str(types))
        check("message_edited event", "message_edited" in types, str(types))
        check("reaction event", "reaction" in types, str(types))
        check("message_pinned event", "message_pinned" in types, str(types))
        check("message_deleted event", "message_deleted" in types, str(types))

        # ── ۹) آپلودِ هر فرمتی ─────────────────────
        print("\n[9] آپلود بدون محدودیتِ پسوند")
        for name, data in (("t.xyz", b"hello"), ("no_ext", b"x" * 10),
                           ("a.exe", b"MZ"), ("b.tar.gz", b"\x1f\x8b")):
            r = await c.post(f"{BASE}/chat/upload", headers=ha,
                             files={"file": (name, io.BytesIO(data), "application/octet-stream")})
            check(f"upload {name}", r.status_code == 200 and "url" in r.json(), r.text[:150])
            if r.status_code == 200:
                check(f"upload {name} reports size",
                      r.json().get("size") == len(data), r.text[:150])

        # ── ۱۰) خروج از گروه ──────────────────────
        print("\n[10] خروج")
        r = await c.post(f"{BASE}/chat/{grp}/leave", headers=ha)
        check("owner cannot leave", r.status_code == 400, r.text)
        r = await c.post(f"{BASE}/chat/{grp}/leave", headers=hb)
        check("member leaves", r.status_code == 200, r.text)
        r = await c.get(f"{BASE}/chat/{grp}/messages", headers=hb)
        check("after leave -> 403", r.status_code == 403, r.text)

    print(f"\n{'='*58}\nPASS {ok_count}   FAIL {len(fail)}")
    for f in fail:
        print("  ! " + f)
    return 0 if not fail else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
