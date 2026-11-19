"""YORDAMCHILAR: JSON o'qish, matnni bo'lib yuborish, uslub matni, stiker paketi.
"""
import asyncio, json, re
from config import STATE, STYLES

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
    parts = [p.strip() for p in text.split("\n\n") if p.strip()]
    if len(parts) > 3:
        parts = parts[:2] + ["\n\n".join(parts[2:])]
    if not parts:
        parts = [text or "..."]
    for i, p in enumerate(parts):
        last = i == len(parts) - 1
        if i:
            try:
                await bot.send_chat_action(msg.chat_id, "typing")
            except Exception:
                pass
            await asyncio.sleep(min(1.5, 0.4 + len(p) / 60))
        await msg.reply_text(p + (extra if last else ""))

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
