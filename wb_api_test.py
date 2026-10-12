"""
آزمونِ زنده‌ی متدهای تازه‌ی client/services/api.py (موجِ B).

اجرا:  .venv/Scripts/python.exe wb_api_test.py
سرور باید روی 127.0.0.1:8000 بالا باشه.
"""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "client"))

import config  # noqa: E402

# پیش‌فرضِ برنامه سرورِ زنده‌ی Railway است؛ آزمون هرگز نباید آن‌جا کاربرِ
# آزمایشی بسازد. پس صریحاً روی سرورِ همین کامپیوتر قفل می‌شود.
config.set_base(config.LOCAL_API_BASE)

from services.api import APIClient  # noqa: E402

TAG = str(int(time.time()))[-6:]
PW = "abtest1234"
OK = FAIL = 0


def check(name, cond, extra=""):
    global OK, FAIL
    if cond:
        OK += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {extra}")


async def reg(c: APIClient, uname, dname):
    try:
        return await c.register(uname, dname, PW)
    except Exception:
        return await c.login(uname, PW)


async def main():
    a, b = APIClient(), APIClient()
    ua, ub = f"api_a{TAG}", f"api_b{TAG}"
    await reg(a, ua, "Ali API")
    await reg(b, ub, "Bita API")
    check("register/login both", a.is_logged_in and b.is_logged_in)

    # ── کانالِ عمومی + جست‌وجو + عضو شدن ──
    print("\n[1] channel + search + join")
    ch = await a.create_group(f"Chan {TAG}", [], kind="channel",
                              is_public=True, handle=f"chan{TAG}")
    check("create public channel", ch.get("kind") == "channel" and ch.get("is_public"), ch)
    hits = await b.search_all(f"Chan {TAG}")
    hit = next((h for h in hits if h.get("chat_id") == ch["chat_id"]), None)
    check("channel found by title", hit is not None, hits)
    hits2 = await b.search_all(f"@chan{TAG}")
    check("channel found by @handle",
          any(h.get("chat_id") == ch["chat_id"] for h in hits2), hits2)
    j = await b.join_chat(chat_id=ch["chat_id"])
    check("join channel", j.get("chat_id") == ch["chat_id"] and not j.get("existing"), j)

    prof = await b.chat_profile(ch["chat_id"])
    check("member cannot post in channel",
          prof.get("my_role") == "member" and prof.get("can_post") is False, prof)
    prof_a = await a.chat_profile(ch["chat_id"])
    check("owner can post", prof_a.get("my_role") == "owner" and prof_a.get("can_post"), prof_a)

    try:
        await b.send_message(ch["chat_id"], "should fail")
        check("member post rejected", False)
    except Exception as e:
        check("member post rejected", "403" in str(e), str(e)[:80])

    # ── نقش و اخراج ──
    print("\n[2] roles + ban")
    r = await a.set_role(ch["chat_id"], ub, "admin")
    check("promote to admin", r.get("role") == "admin", r)
    m = await b.send_message(ch["chat_id"], "admin post ok")
    check("admin can post now", m.get("message_id") is not None, m)
    r = await a.set_role(ch["chat_id"], ub, "member")
    check("demote to member", r.get("role") == "member", r)

    # ── گروه خصوصی ──
    print("\n[3] private group")
    gp = await a.create_group(f"Priv {TAG}", [ub], kind="group",
                              is_public=False, handle=f"priv{TAG}")
    gid = gp["chat_id"]
    check("create private group", bool(gid), gp)
    hits3 = await b.search_all(f"Priv {TAG}")
    check("private hidden from title search",
          not any(h.get("chat_id") == gid for h in hits3), hits3)

    # ── عملیاتِ پیام ──
    print("\n[4] message ops")
    msg = await a.send_message(gid, "hello wave B", enc=False)
    mid = msg["message_id"]
    check("send", msg["content"] == "hello wave B", msg)
    ed = await a.edit_message(gid, mid, "hello edited")
    check("edit", ed["content"] == "hello edited" and ed["edited"], ed)
    rc = await b.react(gid, mid, "🔥")
    check("react add", rc["reactions"].get("🔥") == 1, rc)
    rc = await b.react(gid, mid, "🔥")
    check("react toggle off", not rc["reactions"].get("🔥"), rc)
    pn = await a.pin_message(gid, mid, True)
    check("pin", pn.get("ok"), pn)
    got = await a.get_pinned(gid)
    check("get_pinned", got and got["message_id"] == mid, got)
    fw = await a.forward_message(gid, mid, ch["chat_id"])
    check("forward keeps origin", fw.get("forward_from") == "Ali API", fw)
    enc_msg = await a.send_message(gid, "rg1:ZmFrZQ==", enc=True)
    check("enc flag round-trip", enc_msg.get("enc") is True, enc_msg)
    # ── حذف: فقط برای من / برای همه ──
    mine_only = await a.send_message(gid, "hide me only")
    hid_mid = mine_only["message_id"]
    dh = await a.delete_message(gid, hid_mid, for_everyone=False)
    check("delete for me returns for_everyone=False",
          dh.get("ok") and dh.get("for_everyone") is False, dh)
    my_msgs = await a.get_messages(gid)
    check("hidden for me",
          not any(x["message_id"] == hid_mid for x in my_msgs),
          [x["message_id"] for x in my_msgs][-3:])
    their_msgs = await b.get_messages(gid)
    theirs = next((x for x in their_msgs if x["message_id"] == hid_mid), None)
    check("still visible for the other side (no tombstone)",
          theirs is not None and theirs["content"] == "hide me only"
          and not theirs["deleted"], theirs)

    dl = await a.delete_message(gid, mid, for_everyone=True)
    check("delete for everyone", dl.get("ok") and dl.get("for_everyone"), dl)
    msgs = await a.get_messages(gid)
    check("message fully gone for sender",
          not any(x["message_id"] == mid for x in msgs), len(msgs))
    msgs_b = await b.get_messages(gid)
    check("message fully gone for the other side (no 'deleted' bubble)",
          not any(x["message_id"] == mid for x in msgs_b)
          and not any(x["msg_type"] == "deleted" for x in msgs_b), len(msgs_b))
    gp2 = await a.get_pinned(gid)
    check("pinned cleared when the pinned message is deleted", gp2 is None, gp2)
    ch_list = await a.list_chats()
    row = next((c for c in ch_list if c["chat_id"] == gid), None)
    check("chat list never shows a deleted last message",
          row is not None and row.get("last_message_type") != "deleted", row)

    # ── بایگانی ──
    print("\n[5] archive")
    await b.set_archived(gid, True)
    check("archived_count = 1", await b.archived_count() == 1)
    plain = await b.list_chats()
    arch = await b.list_chats(archived=True)
    check("archived hidden from main list",
          not any(c["chat_id"] == gid for c in plain))
    check("archived listed with flag",
          any(c["chat_id"] == gid and c["archived"] for c in arch), arch)
    await b.set_archived(gid, False)
    check("unarchive", await b.archived_count() == 0)

    # ── گالریِ عکس ──
    print("\n[6] photo gallery (max 5)")
    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
        "890000000a49444154789c6300010000050001"
        "0d0a2db40000000049454e44ae426082"
    )
    urls = []
    for i in range(5):
        res = await a.add_photo_bytes(f"p{i}.png", png)
        urls = res["photos"]
    check("5 photos accepted", len(urls) == 5, urls)
    try:
        await a.add_photo_bytes("p6.png", png)
        check("6th photo rejected", False)
    except Exception as e:
        check("6th photo rejected", "400" in str(e), str(e)[:80])
    gal = await a.my_photos()
    check("my_photos shape",
          gal["max"] == 5 and len(gal["photos"]) == 5 and len(gal["urls"]) == 5, gal)
    main_url = gal["urls"][2]
    sm = await a.set_main_photo(main_url)
    check("set_main_photo first in list",
          sm["avatar_url"] == main_url and sm["photos"][0] == main_url, sm)
    dp = await a.delete_photo(main_url)
    check("delete_photo", len(dp["photos"]) == 4, dp)

    # ── آپلودِ هر فایلی با نوارِ پیشرفت ──
    print("\n[7] any-format upload with progress")
    blob = os.urandom(300_000)
    seen = []
    up = await a.upload_media_bytes("weird.qqq", blob,
                                    on_progress=lambda s, t: seen.append((s, t)))
    check("no-extension-restriction upload", up.get("url", "").startswith("/media/"), up)
    check("size reported", up.get("size") == len(blob), up)
    check("progress callback fired", len(seen) >= 4, seen[:3])
    check("progress ends at 100%", seen and seen[-1][0] == seen[-1][1], seen[-1:])

    # ── دانلود با پیشرفت ──
    print("\n[8] download with progress")
    dst = os.path.join(os.path.dirname(__file__), f"_dl_{TAG}.bin")
    dseen = []
    await a.download_to(up["url"], dst, on_progress=lambda s, t: dseen.append((s, t)))
    check("downloaded byte-identical", open(dst, "rb").read() == blob)
    check("download progress fired", len(dseen) >= 1, dseen[:2])
    os.remove(dst)

    # ── ترکِ گروه ──
    print("\n[9] leave")
    lv = await b.leave_chat(gid)
    check("member leaves", lv.get("ok"), lv)
    try:
        await a.leave_chat(gid)
        check("owner cannot leave", False)
    except Exception as e:
        check("owner cannot leave", "400" in str(e), str(e)[:80])

    print(f"\n=== PASS {OK}  FAIL {FAIL} ===")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
