"""RASM: tayyor rasm qidirish (Pixabay, Wikimedia) va yuborish.
"""
import random, re
import httpx
from telegram import InputMediaPhoto
from config import PIXABAY_KEY

TAG_RE = re.compile(r"<[^>]+>")

UA = {"User-Agent": "RezeBot/1.0 (personal Telegram bot)"}

async def search_pixabay(c, query, n):
    r = await c.get("https://pixabay.com/api/", params={
        "key": PIXABAY_KEY, "q": query[:100],
        "image_type": "photo", "safesearch": "true",
        "per_page": 20})
    r.raise_for_status()
    hits = r.json().get("hits", [])
    random.shuffle(hits)
    return [{"url": h.get("largeImageURL") or h["webformatURL"],
             "who": h.get("user") or "?", "src": "Pixabay"}
            for h in hits[:n + 2]]

async def search_commons(c, query, n):
    r = await c.get("https://commons.wikimedia.org/w/api.php", params={
        "action": "query", "generator": "search",
        "gsrsearch": query + " filetype:bitmap",
        "gsrnamespace": 6, "gsrlimit": 20, "prop": "imageinfo",
        "iiprop": "url|extmetadata", "iiurlwidth": 1024,
        "format": "json"}, headers=UA)
    r.raise_for_status()
    pages = (r.json().get("query") or {}).get("pages") or {}
    out = []
    for p in pages.values():
        ii = (p.get("imageinfo") or [{}])[0]
        u = ii.get("thumburl") or ii.get("url")
        if not u or not u.lower().split("?")[0].endswith(
                (".jpg", ".jpeg", ".png")):
            continue
        meta = ii.get("extmetadata") or {}
        who = TAG_RE.sub("", (meta.get("Artist") or {})
                         .get("value", "") or "").strip()[:60]
        lic = (meta.get("LicenseShortName") or {}).get("value", "")
        who = who or "noma'lum"
        out.append({"url": u, "src": "Wikimedia Commons",
                    "who": f"{who} ({lic})" if lic else who})
    random.shuffle(out)
    return out[:n + 2]

async def find_photos(query, n):
    async with httpx.AsyncClient(
            timeout=20, follow_redirects=True) as c:
        res = []
        if PIXABAY_KEY:
            try:
                res = await search_pixabay(c, query, n)
            except Exception as e:
                print("PIXABAY xato:", e)
        if not res:
            res = await search_commons(c, query, n)
        out = []
        for p in res:
            if len(out) >= n:
                break
            try:
                d = await c.get(p["url"], headers=UA)
                d.raise_for_status()
                if len(d.content) < 9000000:
                    out.append((d.content, p))
            except Exception as e:
                print("YUKLASH xato:", e)
        return out

async def send_photos(msg, query, count):
    got = await find_photos(query, count)
    if not got:
        await msg.reply_text("Bu haqda mos rasm topa olmadim 😔")
        return
    srcs = ", ".join(dict.fromkeys(
        f'{p["who"]} · {p["src"]}' for _, p in got))
    cap = f"📷 {srcs}"[:1000]
    if len(got) == 1:
        await msg.reply_photo(got[0][0], caption=cap)
    else:
        media = [InputMediaPhoto(b, caption=cap if i == 0 else None)
                 for i, (b, _) in enumerate(got)]
        await msg.reply_media_group(media)
