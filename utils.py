"""YORDAMCHILAR: JSON o'qish, matnni bo'lib yuborish, uslub matni, stiker paketi.
"""
import asyncio, json, random, re
from datetime import datetime
from config import STATE, STYLES, TZ
from character import MOODS

def parse_json(t):
    t = (t or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t).strip()
    try:
        d = json.loads(t)
        return d if isinstance(d, dict) else None
    except Exception:
        pass
    m = re.search(r'"answer"\s*:\s*"((?:[^"\\]|\\.)*)"', t, re.S)
    if m:
        try:
            return {"answer": json.loads('"' + m.group(1) + '"')}
        except Exception:
            return {"answer": m.group(1)}
    return None

async def send_text(msg, bot, text, extra=""):
    # Javob doim bitta xabar bo'lib yuboriladi
    await msg.reply_text((text or "...").strip() + extra)

def style_text(st):
    key = (st or {}).get("style") or "muloyim"
    extra = STYLES.get(key, STYLES["muloyim"])[1]
    return ("\n\n" + extra) if extra else ""

async def get_pack(bot, st):
    name = (st or {}).get("sticker_set")
    if not name or st.get("stickers") is False:
        return {}
    cache = STATE.setdefault("packs", {})
    if name in cache:
        return cache[name]
    try:
        ss = await bot.get_sticker_set(name)
        d = {}
        for x in ss.stickers:
            if x.emoji:
                d.setdefault(x.emoji, []).append(x.file_id)
        cache[name] = d
        return d
    except Exception as e:
        print("PAKET xato:", e)
        return {}

def mark(cur, key, label):
    return ("✔ " if cur == key else "") + label


def mood_text(cid):
    """Har chat va har kun uchun bir xil kayfiyat (kun ichida o'zgarmaydi)."""
    day = datetime.now(TZ).strftime("%Y-%m-%d")
    m = random.Random(f"{cid}-{day}").choice(MOODS)
    return ("\n\nBugungi kayfiyating: " + m + ". Buni javobingda "
            "tabiiy seze olsin, lekin o'zingni tushuntirib o'tirma.\n"
            "Faktlar orasida 'Ichki hazil' bo'lsa, ularni o'rinli "
            "ishlat, hammasini birdan emas.")


UZ_DAYS = ["dushanba", "seshanba", "chorshanba", "payshanba",
           "juma", "shanba", "yakshanba"]

def time_text(now=None):
    """Hafta kuni, kun vaqti va shunga mos ohang haqida qisqa kontekst."""
    now = now or datetime.now(TZ)
    wd, h = now.weekday(), now.hour
    if h < 5:
        per = "tun"
    elif h < 11:
        per = "ertalab"
    elif h < 17:
        per = "kunduzi"
    elif h < 22:
        per = "kechqurun"
    else:
        per = "kech tun"
    hint = ""
    if wd >= 5:
        hint = "Bugun dam olish kuni: ohang bo'shroq, rejalar haqida erkin."
    elif wd == 0:
        hint = "Bugun dushanba: odamlar odatda ish boshlanishidan charchoq bo'ladi."
    elif wd == 4:
        hint = "Bugun juma: hafta oxiri his-tuyg'usi bor."
    return ("Hozir " + UZ_DAYS[wd] + ", " + per + ", soat "
            + now.strftime("%H:%M") + ". " + hint
            + " Shu vaqtga mos his bilan yoz, vaqtni aynan aytib o'tirma.")
