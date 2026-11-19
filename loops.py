"""FON ISHLARI: eslatma sikli va botning o'zi birinchi yozishi (salomlar).
"""
import asyncio, random
from datetime import datetime, timedelta
from google.genai import types
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from ai import gen
from character import BASE, SLOT_PROMPTS, ANGLES
from config import (TZ, SLOTS, STATE, REPEATS, CHAT_MODELS,
                    JIMLIK_HOURS, JIMLIK_START, JIMLIK_END)
from database import (get_recent, list_facts, get_history, get_settings,
                      last_user_time,
                      get_summary, save_msg, cleanup_db, proactive_chats,
                      set_slot, due_reminders, advance, mark_done)
from utils import style_text, mood_text

def event_context(rows, kind):
    today = datetime.now(TZ).date()
    tod, tom = [], []
    for x in rows:
        u = x.get("expires_at")
        if not u:
            continue
        try:
            d = (datetime.fromisoformat(u.replace("Z", "+00:00"))
                 .date() - timedelta(days=1))
        except Exception:
            continue
        if d == today:
            tod.append(x["fact"])
        elif d == today + timedelta(days=1):
            tom.append(x["fact"])
    parts = []
    if kind == "ertalab" and tod:
        parts.append("Bugun bo'ladigan voqealar: "
                     + "; ".join(tod)
                     + ". Omad tila yoki tayyorgarlikni so'ra.")
    if kind == "kechqurun":
        if tod:
            parts.append("Bugun bo'lgan voqealar: "
                         + "; ".join(tod)
                         + ". Qanday o'tganini so'ra.")
        if tom:
            parts.append("Ertaga bo'ladigan voqealar: "
                         + "; ".join(tom)
                         + ". Tayyorgarlik haqida iliq so'ra "
                         "yoki omad tila.")
    return " ".join(parts)

async def greet(app, cid, kind):
    recent = await asyncio.to_thread(get_recent, cid, 2)
    frows = await asyncio.to_thread(list_facts, cid)
    ev = event_context(frows, kind)
    unanswered = (len(recent) == 2 and
                  all(m["role"] == "model" for m in recent))
    if unanswered and not ev:
        print("SALOM o'tkazib yuborildi: javob yo'q", cid)
        return
    if not ev and random.random() < 0.25:
        print("SALOM tasodifan o'tkazildi", cid)
        return
    history = await asyncio.to_thread(get_history, cid)
    facts = [x["fact"] for x in frows[-60:]]
    st = await asyncio.to_thread(get_settings, cid)
    system = (BASE + "\n\nHozirgi Toshkent vaqti: "
              + datetime.now(TZ).strftime("%A, %Y-%m-%d %H:%M"))
    system += style_text(st)
    system += mood_text(cid)
    if facts:
        system += ("\n\nFoydalanuvchi haqida bilganlaring:\n- "
                   + "\n- ".join(facts))
    summ = await asyncio.to_thread(get_summary, cid)
    if summ:
        system += ("\n\nOldingi suhbatlar xulosasi (faqat "
                   "kontekst uchun):\n" + summ)
    hist = "\n".join(f'{h["role"]}: {h["content"]}'
                     for h in history[-8:])
    prompt = SLOT_PROMPTS[kind]
    if ev:
        prompt += "\n\nMUHIM: " + ev
    else:
        prompt += "\n\nBu safar: " + random.choice(ANGLES) + "."
    prompt += ("\n\nOldingi xabarlaringdagi gaplarni "
               "takrorlama.\n\nOxirgi suhbat:\n" + hist)
    r = await gen(
        models=CHAT_MODELS, contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=system, temperature=1.0))
    text = (r.text or "").strip()
    if text:
        await app.bot.send_message(cid, text)
        await asyncio.to_thread(save_msg, cid, "model", text)

async def proactive_loop(app):
    while True:
        try:
            now = datetime.now(TZ)
            day = now.strftime("%Y-%m-%d")
            if STATE.get("clean") != day:
                STATE["clean"] = day
                STATE["offs"] = {}
                try:
                    await asyncio.to_thread(cleanup_db)
                except Exception as e:
                    print("TOZALASH xato:", e)
            for kind, hour in SLOTS:
                key = f"{day}-{kind}"
                off = STATE.setdefault("offs", {}).setdefault(
                    key, random.randint(0, 90))
                start = now.replace(
                    hour=hour, minute=0, second=0,
                    microsecond=0) + timedelta(minutes=off)
                if (not (start <= now < start + timedelta(hours=2))
                        or now.hour >= 23):
                    continue
                rows = await asyncio.to_thread(proactive_chats)
                for row in rows:
                    if row.get("last_slot") == key:
                        continue
                    await asyncio.to_thread(
                        set_slot, row["chat_id"], key)
                    try:
                        await greet(app, row["chat_id"], kind)
                    except Exception as e:
                        print("SALOM xato:", e)
            if JIMLIK_START <= now.hour < JIMLIK_END:
                key = f"{day}-jimlik"
                for row in await asyncio.to_thread(proactive_chats):
                    cid = row["chat_id"]
                    if row.get("last_slot") == key:
                        continue
                    last = await asyncio.to_thread(last_user_time, cid)
                    if not last or now - last < timedelta(hours=JIMLIK_HOURS):
                        continue
                    if random.random() < 0.5:
                        continue
                    await asyncio.to_thread(set_slot, cid, key)
                    try:
                        await greet(app, cid, "jimlik")
                    except Exception as e:
                        print("JIMLIK xato:", e)
        except Exception as e:
            print("PROAKTIV xato:", e)
        await asyncio.sleep(300)

async def reminder_loop(app):
    while True:
        try:
            rows = await asyncio.to_thread(due_reminders)
            for x in rows:
                kb = InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        "⏰ 10 daq", callback_data=f"snz:{x['id']}:10"),
                    InlineKeyboardButton(
                        "⏰ 1 soat", callback_data=f"snz:{x['id']}:60")],
                    [InlineKeyboardButton(
                        "✅ Bajarildi", callback_data=f"ok:{x['id']}")]])
                try:
                    await app.bot.send_message(
                        x["chat_id"], "⏰ Eslatma: " + x["task"],
                        reply_markup=kb)
                except Exception as e:
                    print("Yuborish xato:", e)
                if x.get("repeat") in REPEATS:
                    await asyncio.to_thread(
                        advance, x["id"], x["remind_at"], x["repeat"])
                else:
                    await asyncio.to_thread(mark_done, x["id"])
        except Exception as e:
            print("SIKL xato:", e)
        await asyncio.sleep(30)

async def start_loop(app):
    app.bot_data["t1"] = asyncio.create_task(reminder_loop(app))
    app.bot_data["t2"] = asyncio.create_task(proactive_loop(app))
