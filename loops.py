"""FON ISHLARI: eslatma sikli va botning o'zi birinchi yozishi (salomlar).
"""
import asyncio, random
from datetime import datetime, timedelta
from google.genai import types
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from ai import gen
from character import BASE, SLOT_PROMPTS, ANGLES
from config import (TZ, STATE, REPEATS, CHAT_MODELS,
                    JIMLIK_HOURS, JIMLIK_START, JIMLIK_END,
                    PROACTIVE_HOURS, PROACTIVE_PER_DAY,
                    PROACTIVE_GAP)
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

def period_of(h):
    if h < 11:
        return "ertalab, uyg'ongan bo'lishing mumkin"
    if h < 18:
        return "kunduzi, ishda yoki yo'lda bo'lishing mumkin"
    if h < 23:
        return "kechqurun, biroz charchagan bo'lishing mumkin"
    return "tun, uyqusiraysan"

def day_plan(cid, day):
    """Chat va kun uchun tasodifiy vaqtlar (daqiqada), har safar bir xil."""
    rnd = random.Random(f"plan-{cid}-{day}")
    lo, hi = PROACTIVE_HOURS
    n = rnd.randint(*PROACTIVE_PER_DAY)
    times = []
    for _ in range(300):
        if len(times) >= n:
            break
        m = rnd.randint(lo * 60, hi * 60 - 1)
        if all(abs(m - t) >= PROACTIVE_GAP for t in times):
            times.append(m)
    return sorted(times)

async def greet(app, cid, kind, prompt_kind=None):
    recent = await asyncio.to_thread(get_recent, cid, 2)
    frows = await asyncio.to_thread(list_facts, cid)
    ev = event_context(frows, kind)
    unanswered = (len(recent) == 2 and
                  all(m["role"] == "model" for m in recent))
    if unanswered and not ev:
        print("SALOM o'tkazib yuborildi: javob yo'q", cid)
        return
    if not ev and random.random() < 0.1:
        print("SALOM tasodifan o'tkazildi", cid)
        return
    history = await asyncio.to_thread(get_history, cid)
    facts = [x["fact"] for x in frows[-60:]]
    st = await asyncio.to_thread(get_settings, cid)
    system = (BASE + "\n\nHozirgi Toshkent vaqti: "
              + datetime.now(TZ).strftime("%A, %Y-%m-%d %H:%M"))
    system += style_text(st)
    system += mood_text(cid)
    hr = datetime.now(TZ).hour
    system += ("\n\nHozir " + period_of(hr) + ". Shu vaqtga mos his "
               "bilan yoz, lekin vaqtni aytib o'tirma.")
    if facts:
        system += ("\n\nFoydalanuvchi haqida bilganlaring:\n- "
                   + "\n- ".join(facts))
    summ = await asyncio.to_thread(get_summary, cid)
    if summ:
        system += ("\n\nOldingi suhbatlar xulosasi (faqat "
                   "kontekst uchun):\n" + summ)
    hist = "\n".join(f'{h["role"]}: {h["content"]}'
                     for h in history[-8:])
    prompt = SLOT_PROMPTS[prompt_kind or kind]
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
            rows = await asyncio.to_thread(proactive_chats)
            for row in rows:
                cid = row["chat_id"]
                last = row.get("last_slot")
                try:
                    last_dt = datetime.fromisoformat(last) if last else None
                except Exception:
                    last_dt = None
                # 1) Rejalashtirilgan vaqtlar
                for m in day_plan(cid, day):
                    t = now.replace(hour=m // 60, minute=m % 60,
                                    second=0, microsecond=0)
                    if t > now:
                        break
                    if now - t > timedelta(minutes=30):
                        continue  # eskirgan (server qayta ishga tushgan)
                    if last_dt and last_dt >= t:
                        continue  # bu vaqt allaqachon bajarilgan
                    await asyncio.to_thread(set_slot, cid, now.isoformat())
                    h = t.hour
                    kind = "ertalab" if h < 11 else (
                        "tushlik" if h < 18 else "kechqurun")
                    pk = kind if (kind != "tushlik" or
                                  random.random() < 0.3) else "tasodifiy"
                    if kind != "tushlik" and random.random() < 0.5:
                        pk = "tasodifiy"
                    try:
                        await greet(app, cid, kind, pk)
                    except Exception as e:
                        print("SALOM xato:", e)
                    break  # bir loop aylanishida bitta xabar
                # 2) Uzoq jimlik: user 8 soat yozmasa va bot ham 8 soatdan beri yozmagan bo'lsa
                if not (JIMLIK_START <= now.hour < JIMLIK_END):
                    continue
                user_last = await asyncio.to_thread(last_user_time, cid)
                if not user_last or now - user_last < timedelta(hours=JIMLIK_HOURS):
                    continue
                if last_dt and now - last_dt < timedelta(hours=JIMLIK_HOURS):
                    continue
                if random.random() < 0.5:
                    continue
                await asyncio.to_thread(set_slot, cid, now.isoformat())
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
