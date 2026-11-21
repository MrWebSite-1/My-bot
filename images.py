"""RASM: tayyor rasm qidirish (Pixabay, Pexels, Wikimedia) va yuborish.
Qidiruv inglizcha ishlaydi: o'zbek va rus so'zlari avval inglizchaga o'tkaziladi.
"""
import os, random, re
import httpx
from telegram import InputMediaPhoto
from config import PIXABAY_KEY

PEXELS_KEY = os.environ.get("PEXELS_KEY", "")

TAG_RE = re.compile(r"<[^>]+>")
UA = {"User-Agent": "RezeBot/1.0 (personal Telegram bot)"}

# O'zbek va rus so'zlari -> inglizcha (Pixabay va Pexels inglizchada yaxshi ishlaydi)
WORDS = {
    "kofe": "coffee", "qahva": "coffee", "кофе": "coffee",
    "choy": "tea", "чай": "tea",
    "kitob": "book", "книга": "book", "kitob o'qish": "reading book",
    "o'qish": "reading", "чтение": "reading",
    "sport": "sport", "спорт": "sport",
    "yugurish": "running", "бег": "running",
    "yurish": "walk", "sayr": "walk", "прогулка": "walk",
    "tabiat": "nature", "природа": "nature",
    "dengiz": "sea", "море": "sea",
    "tog'": "mountain", "tog": "mountain", "горы": "mountains",
    "daraxt": "tree", "дерево": "tree",
    "gul": "flower", "цветы": "flowers", "гул": "flower",
    "quyosh": "sun", "солнце": "sun",
    "yomg'ir": "rain", "yomgir": "rain", "дождь": "rain",
    "ob-havo": "weather", "погода": "weather",
    "ertalab": "morning", "утро": "morning",
    "kechqurun": "evening", "kechki": "evening", "вечер": "evening",
    "uyqu": "sleep", "сон": "sleep",
    "ovqat": "food", "еда": "food", "oshxona": "kitchen", "кухня": "kitchen",
    "shahar": "city", "город": "city",
    "mushuk": "cat", "кот": "cat", "кошка": "cat",
    "it": "dog", "собака": "dog",
    "musiqa": "music", "музыка": "music",
    "kino": "movie", "кино": "movie",
    "uy": "home", "дом": "home",
    "ish": "work", "работа": "work",
    "dam olish": "relaxation", "отдых": "relaxation",
    "kun": "day", "день": "day",
}

def to_english(query):
    q = query.lower().strip()
    if q in WORDS:
        return WORDS[q]
    words = re.findall(r"[a-zA-Z\u0400-\u04FF\u00C0-\u024F']+", q)
    return " ".join(WORDS.get(w, w) for w in words)

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

async def search_pexels(c, query, n):
    r = await c.get("https://api.pexels.com/v1/search", params={
        "query": query[:100], "per_page": 15, "size": "medium"},
        headers={"Authorization": PEXELS_KEY})
    r.raise_for_status()
    photos = r.json().get("photos", [])
    random.shuffle(photos)
    return [{"url": p["src"]["large"],
             "who": p.get("photographer") or "?", "src": "Pexels"}
            for p in photos[:n + 2]]

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

async def _search_term(c, term, n):
    """Bitta so'z uchun manbalar navbati bilan; birinchi topilgan natija."""
    if PIXABAY_KEY:
        try:
            res = await search_pixabay(c, term, n)
            if res:
                return res
        except Exception as e:
            print("PIXABAY xato:", e)
    if PEXELS_KEY:
        try:
            res = await search_pexels(c, term, n)
            if res:
                return res
        except Exception as e:
            print("PEXELS xato:", e)
    return []

async def find_photos(query, n):
    eng = to_english(query)
    terms = [t for t in dict.fromkeys([eng] + eng.split()) if t][:4]
    async with httpx.AsyncClient(
            timeout=20, follow_redirects=True) as c:
        res = []
        for term in terms:
            res = await _search_term(c, term, n)
            if res:
                break
        if not res:
            try:
                res = await search_commons(c, eng or query, n)
            except Exception as e:
                print("COMMONS xato:", e)
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
