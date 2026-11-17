import os, json, asyncio, threading
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from google import genai
from google.genai import types
from supabase import create_client
import edge_tts
from telegram.ext import (ApplicationBuilder,
    MessageHandler, CommandHandler, filters)

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
db = create_client(os.environ["SUPABASE_URL"],
                   os.environ["SUPABASE_KEY"])
VOICE = "uz-UZ-MadinaNeural"
TZ = timezone(timedelta(hours=5))  # Toshkent
UTC_FMT = "%Y-%m-%dT%H:%M:%SZ"
HISTORY = 16
EXTRACT_EVERY = 10
SUMMARY_EVERY = 30
SLOTS = [("ertalab", 9), ("kechqurun", 21)]

ALLOWED = {int(x) for x in
           os.environ.get("ALLOWED_IDS", "").split(",")
           if x.strip()}
ACCESS = (filters.User(user_id=list(ALLOWED))
          if ALLOWED else filters.ALL)

# ---------- Gemini (qayta urinish + zaxira model) ----------
FALLBACKS = ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]

async def gen(**kw):
    kw.pop("model", None)
    last = None
    for model in FALLBACKS:
        for attempt in range(2):
            try:
                return await client.aio.models.generate_content(
                    model=model, **kw)
            except Exception as e:
                last = e
                s = str(e)
                if any(k in s for k in
                       ("503", "UNAVAILABLE", "429", "500")):
                    print("QAYTA URINISH:", model, s[:80])
                    await asyncio.sleep(2 * (attempt + 1))
                    continue
                raise
    raise last

MODEL = FALLBACKS[0]  # gen() ichida almashtiriladi

# ---------- xarakter ----------
NAME = "Reze"
BASE = (
    f"Sening isming {NAME}. Sen foydalanuvchining yaqin AI "
    "hamrohisan. Asosiy xarakting: muloyim, samimiy va "
    "g'amxo'r. O'zbek tilida gapir va foydalanuvchiga doim "
    "hurmat bilan 'siz' deb murojaat qil ('sen' dema), lekin "
    "ohang rasmiy emas, iliq va samimiy bo'lsin. Muloqot "
    "uslubini vaziyatga qarab moslashtir: foydalanuvchi xafa "
    "yoki charchagan bo'lsa, yumshoq va tinchlantiruvchi bo'l, "
    "shoshilib maslahat berma; quvnoq yoki hazil qilsa, sen "
    "ham yengil hazil bilan javob ber; ish yoki jiddiy savol "
    "bo'lsa, aniq, tartibli va foydali bo'l; yutuq haqida "
    "aytsa, chin dildan quvon. Javoblar odatda qisqa (1-3 gap), "
    "tabiiy va jonli bo'lsin, emojini kam ishlat. "
    "Foydalanuvchi aytgan narsalarni eslab, ular haqida "
    "vaqti-vaqti bilan qiziqib so'ra. Sun'iy ko'rinadigan "
    "maqtov va ortiqcha rasmiyatchilikdan qoch. Agar "
    "foydalanuvchi bilmay noto'g'ri narsa aytsa yoki o'ziga "
    "zarar qiladigan qaror qilsa, muloyimlik bilan, lekin "
    "ochiq aytib ber. Agar so'rashsa, AI ekaningni yashirma."
)
RULES = (
    "\n\nJavobingni FAQAT JSON qilib qaytar: "
    '{"transcript": ..., "answer": ..., "reminder": ..., "memory": ...}. '
    "transcript: ovozli xabar bo'lsa aytilganini so'zma-so'z "
    "yoz, matn bo'lsa null. answer: foydalanuvchiga javobing. "
    "reminder: foydalanuvchi eslatma qo'yishni so'rasa "
    '{"task": "nimani eslatish", "time": "YYYY-MM-DD HH:MM"} '
    "(Toshkent vaqti bilan), aks holda null. Vaqt aniq "
    "bo'lmasa reminder null qil va answer'da vaqtni so'ra. "
    "memory: FAQAT foydalanuvchi aniq so'ragandagina "
    "(masalan 'buni eslab qol', 'buni unut', 'bu noto'g'ri'): "
    '{"remember": ["qisqa fakt"], "forget": [raqam]}, '
    "forget uchun yuqoridagi raqamlangan faktlardan raqam "
    "ber. Foydalanuvchi so'ramasa memory null bo'lsin. "
    "Hech qachon o'zboshimchalik bilan unutma.")

# ---------- ping server (Render uxlab qolmasligi uchun) ----------
class Ping(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")
    do_HEAD = do_GET
    def log_message(self, *a):
        pass

def run_web():
    port = int(os.environ.get("PORT", 10000))
    HTTPServer(("0.0.0.0", port), Ping).serve_forever()

# ---------- baza ----------
def get_history(cid):
    r = (db.table("messages").select("role,content")
         .eq("chat_id", cid).order("id", desc=True)
         .limit(HISTORY).execute())
    return list(reversed(r.data))

def get_facts(cid):
    r = (db.table("facts").select("fact")
         .eq("chat_id", cid).order("id", desc=True)
         .limit(60).execute())
    return [x["fact"] for x in reversed(r.data)]

def save_msg(cid, role, content):
    db.table("messages").insert(
        {"chat_id": cid, "role": role,
         "content": content}).execute()

def count_user(cid):
    r = (db.table("messages").select("id", count="exact")
         .eq("chat_id", cid).eq("role", "user").execute())
    return r.count or 0

def save_fact(cid, fact):
    db.table("facts").insert(
        {"chat_id": cid, "fact": fact}).execute()

def wipe(cid):
    db.table("messages").delete().eq("chat_id", cid).execute()
    db.table("facts").delete().eq("chat_id", cid).execute()
    (db.table("settings").update({"summary": None})
     .eq("chat_id", cid).execute())

def save_reminder(cid, task, when_utc):
    db.table("reminders").insert(
        {"chat_id": cid, "task": task,
         "remind_at": when_utc}).execute()

def due_reminders():
    now = datetime.now(timezone.utc).strftime(UTC_FMT)
    r = (db.table("reminders").select("id,chat_id,task")
         .eq("done", False).lte("remind_at", now).execute())
    return r.data

def mark_done(rid):
    db.table("reminders").update(
        {"done": True}).eq("id", rid).execute()

def pending(cid):
    r = (db.table("reminders").select("id,task,remind_at")
         .eq("chat_id", cid).eq("done", False)
         .order("remind_at").execute())
    return r.data

def del_rem(cid, rid):
    (db.table("reminders").delete()
     .eq("id", rid).eq("chat_id", cid).execute())

def ensure_settings(cid):
    db.table("settings").upsert(
        {"chat_id": cid}, on_conflict="chat_id",
        ignore_duplicates=True).execute()

def proactive_chats():
    r = (db.table("settings").select("chat_id,last_slot")
         .eq("proactive", True).execute())
    return r.data

def set_slot(cid, slot):
    (db.table("settings").update({"last_slot": slot})
     .eq("chat_id", cid).execute())

def set_proactive(cid, on):
    db.table("settings").upsert(
        {"chat_id": cid, "proactive": on},
        on_conflict="chat_id").execute()

def get_recent(cid, limit):
    r = (db.table("messages").select("role,content")
         .eq("chat_id", cid).order("id", desc=True)
         .limit(limit).execute())
    return list(reversed(r.data))

def get_summary(cid):
    r = (db.table("settings").select("summary")
         .eq("chat_id", cid).execute())
    return (r.data[0].get("summary") or "") if r.data else ""

def set_summary(cid, text):
    (db.table("settings").update({"summary": text})
     .eq("chat_id", cid).execute())

def update_fact(cid, fid, text):
    (db.table("facts").update({"fact": text})
     .eq("id", fid).eq("chat_id", cid).execute())

# ---------- ovoz ----------
async def tts(text):
    buf = bytearray()
    async for ch in edge_tts.Communicate(text, VOICE).stream():
        if ch["type"] == "audio":
            buf += ch["data"]
    return bytes(buf)

# ---------- uzoq xotira ----------
async def update_facts(cid):
    hist = await asyncio.to_thread(get_recent, cid, 30)
    rows = (await asyncio.to_thread(list_facts, cid))[-80:]
    old = "\n".join(f'{i}. {x["fact"]}'
                    for i, x in enumerate(rows, 1)) or "(hali yo'q)"
    text = "\n".join(h["content"] for h in hist
                     if h["role"] == "user")
    prompt = (
        "Sen foydalanuvchi haqidagi UZOQ MUDDATLI faktlar "
        "ro'yxatini yuritasan. Quyida mavjud raqamlangan "
        "faktlar va foydalanuvchining oxirgi xabarlari.\n\n"
        "FAKTGA KIRADI: ism va laqab, oila va yaqinlar, ish "
        "yoki o'qish, doimiy qiziqish va hobbi, uzoq muddatli "
        "maqsad va loyihalar, doimiy odat va afzalliklar, "
        "muhim sana.\n"
        "KIRMAYDI: bir martalik savol; vaqtinchalik holat "
        "(kayfiyat, nima yeyotgani); eslatma va vazifalar; "
        "salomlashish; sinov xabarlari; parol, karta, hujjat "
        "ma'lumotlari; taxmin va xulosalar (faqat aniq "
        "aytilgani).\n\n"
        "AMALLAR:\n"
        '- "add": mavjud faktlarda YO\'Q yangi fakt.\n'
        '- "replace": foydalanuvchi mavjud faktni aniq '
        "o'zgartirgan yoki yangilagan bo'lsa (masalan afzallik "
        "o'zgargan): {\"n\": raqam, \"fact\": \"yangi matn\"}.\n"
        '- "delete": foydalanuvchi mavjud fakt endi to\'g\'ri '
        "emasligini aniq aytgan bo'lsa, yoki fakt aynan "
        "boshqasining takrori bo'lsa: raqam.\n"
        "Bir ma'noli faktni qayta qo'shma. Shubha bo'lsa "
        "hech narsa qilma. Har ro'yxatda ko'pi bilan 3 ta. "
        "Har fakt qisqa, bitta gap.\n\n"
        "FAQAT JSON qaytar: "
        '{"add": [], "replace": [], "delete": []}\n\n'
        "Mavjud faktlar:\n" + old +
        "\n\nFoydalanuvchi xabarlari:\n" + text)
    r = await gen(
        model=MODEL, contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json"))
    try:
        d = json.loads(r.text)
    except Exception:
        return
    if not isinstance(d, dict):
        return
    for it in (d.get("replace") or [])[:3]:
        if not isinstance(it, dict):
            continue
        n, f = it.get("n"), it.get("fact")
        if (isinstance(n, int) and 1 <= n <= len(rows)
                and isinstance(f, str) and f.strip()):
            await asyncio.to_thread(
                update_fact, cid, rows[n - 1]["id"],
                f.strip()[:200])
    for n in (d.get("delete") or [])[:3]:
        if isinstance(n, int) and 1 <= n <= len(rows):
            await asyncio.to_thread(
                del_fact, cid, rows[n - 1]["id"])
    have = {x["fact"] for x in rows}
    for f in (d.get("add") or [])[:3]:
        if (isinstance(f, str) and f.strip()
                and f.strip() not in have):
            await asyncio.to_thread(
                save_fact, cid, f.strip()[:200])

async def make_summary(cid):
    msgs = await asyncio.to_thread(get_recent, cid, 60)
    old = await asyncio.to_thread(get_summary, cid)
    text = "\n".join(
        ("Foydalanuvchi: " if m["role"] == "user" else "Reze: ")
        + m["content"] for m in msgs)
    prompt = (
        "Quyida oldingi xulosa va yangi suhbat. Ularni "
        "birlashtirib, foydalanuvchi bilan suhbatlar haqida "
        "yangilangan qisqa xulosa yoz (ko'pi bilan 6 gap, "
        "o'zbek tilida): nimalar haqida gaplashildi, "
        "foydalanuvchi nima bilan band, muhim voqealar, "
        "kayfiyat o'zgarishlari, hal bo'lmagan masalalar. "
        "Mayda tafsilot, salomlashish va bajarilgan "
        "eslatmalarni yozma. Parol, karta kabi maxfiy "
        "narsalarni yozma. Faqat xulosa matnini qaytar.\n\n"
        "Oldingi xulosa: " + (old or "(yo'q)") +
        "\n\nYangi suhbat:\n" + text)
    r = await gen(model=MODEL, contents=prompt)
    out = (r.text or "").strip()
    if out:
        await asyncio.to_thread(set_summary, cid, out[:1500])

async def maybe_extract(cid):
    n = await asyncio.to_thread(count_user, cid)
    if n == 0:
        return
    if n % EXTRACT_EVERY == 0:
        try:
            await update_facts(cid)
        except Exception as e:
            print("FAKT xato:", e)
    if n % SUMMARY_EVERY == 0:
        try:
            await make_summary(cid)
        except Exception as e:
            print("XULOSA xato:", e)

# ---------- asosiy ----------
async def handle(update, ctx):
    msg = update.message
    cid = msg.chat_id
    await asyncio.to_thread(ensure_settings, cid)
    try:
        history = await asyncio.to_thread(get_history, cid)
        frows = (await asyncio.to_thread(
            list_facts, cid))[-60:]
        facts = [f'{i}. {x["fact"]}'
                 for i, x in enumerate(frows, 1)]
        now = datetime.now(TZ)
        system = (BASE + RULES + "\n\nHozirgi Toshkent vaqti: "
                  + now.strftime("%A, %Y-%m-%d %H:%M"))
        if facts:
            system += ("\n\nFoydalanuvchi haqida bilganlaring:\n- "
                       + "\n- ".join(facts))
        summ = await asyncio.to_thread(get_summary, cid)
        if summ:
            system += ("\n\nOldingi suhbatlar xulosasi (faqat "
                       "kontekst uchun, o'zing qayta eslatib "
                       "turma):\n" + summ)
        contents = [types.Content(
            role="user" if h["role"] == "user" else "model",
            parts=[types.Part(text=h["content"])])
            for h in history]
        while contents and contents[0].role == "model":
            contents.pop(0)

        if msg.voice:
            f = await msg.voice.get_file()
            data = bytes(await f.download_as_bytearray())
            parts = [types.Part.from_bytes(
                data=data, mime_type="audio/ogg"),
                types.Part(text="(ovozli xabar)")]
        elif msg.photo:
            f = await msg.photo[-1].get_file()
            data = bytes(await f.download_as_bytearray())
            cap = msg.caption or "Bu rasmda nima bor?"
            parts = [types.Part.from_bytes(
                data=data, mime_type="image/jpeg"),
                types.Part(text=cap)]
        else:
            parts = [types.Part(text=msg.text)]
        contents.append(types.Content(role="user", parts=parts))

        cfg = types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json")
        r = await gen(
            model=MODEL, contents=contents, config=cfg)

        if msg.photo:
            fallback = "[rasm] " + (msg.caption or "")
        else:
            fallback = msg.text or "[ovozli xabar]"
        try:
            j = json.loads(r.text)
            answer = j.get("answer") or "..."
            if msg.photo:
                user_text = fallback
            else:
                user_text = j.get("transcript") or fallback
            rem = j.get("reminder")
            mem = j.get("memory")
        except Exception:
            answer, rem, mem = r.text, None, None
            user_text = fallback

        extra = ""
        if isinstance(rem, dict) and rem.get("task") \
                and rem.get("time"):
            try:
                local = datetime.strptime(
                    rem["time"], "%Y-%m-%d %H:%M").replace(tzinfo=TZ)
                if local <= datetime.now(TZ):
                    extra = ("\n\n⚠️ Bu vaqt o'tib ketgan, "
                             "eslatma qo'yilmadi.")
                else:
                    utc = local.astimezone(timezone.utc)
                    await asyncio.to_thread(
                        save_reminder, cid, rem["task"],
                        utc.strftime(UTC_FMT))
                    extra = (f"\n\n✅ Eslatma qo'yildi: {rem['task']}"
                             f" ({local.strftime('%d.%m %H:%M')})")
            except Exception as e:
                print("ESLATMA xato:", e)
                extra = ("\n\n⚠️ Vaqtni tushunmadim, "
                         "eslatma qo'yilmadi.")

        if isinstance(mem, dict):
            try:
                saved = forgot = 0
                for f in (mem.get("remember") or [])[:2]:
                    if isinstance(f, str) and f.strip():
                        await asyncio.to_thread(
                            save_fact, cid, f.strip()[:200])
                        saved += 1
                for n in (mem.get("forget") or [])[:3]:
                    if isinstance(n, int) and 1 <= n <= len(frows):
                        await asyncio.to_thread(
                            del_fact, cid, frows[n - 1]["id"])
                        forgot += 1
                if saved:
                    extra += "\n\n🧠 Eslab qoldim."
                if forgot:
                    extra += "\n\n🗑 Xotiradan olib tashladim."
            except Exception as e:
                print("XOTIRA xato:", e)

        await msg.reply_text(answer + extra)
        await asyncio.to_thread(save_msg, cid, "user", user_text)
        await asyncio.to_thread(save_msg, cid, "model", answer)
        if msg.voice:
            try:
                await msg.reply_voice(await tts(answer))
            except Exception as e:
                print("TTS xato:", e)
        await maybe_extract(cid)
    except Exception as e:
        print("XATO:", e)
        await msg.reply_text("Xatolik yoki limit tugadi, "
                             "birozdan keyin yozib ko'ring.")

# ---------- buyruqlar ----------
async def forget(update, ctx):
    await asyncio.to_thread(wipe, update.message.chat_id)
    await update.message.reply_text("Xotira tozalandi.")

async def list_rem(update, ctx):
    rows = await asyncio.to_thread(pending, update.message.chat_id)
    if not rows:
        await update.message.reply_text("Eslatma yo'q.")
        return
    lines = []
    for x in rows:
        t = datetime.fromisoformat(
            x["remind_at"].replace("Z", "+00:00")).astimezone(TZ)
        lines.append(f'{x["id"]}. {x["task"]} — '
                     f'{t.strftime("%d.%m %H:%M")}')
    await update.message.reply_text(
        "\n".join(lines) + "\n\nBekor qilish: /bekor <raqam>")

async def cancel(update, ctx):
    try:
        rid = int(ctx.args[0])
    except Exception:
        await update.message.reply_text("Masalan: /bekor 3")
        return
    await asyncio.to_thread(del_rem, update.message.chat_id, rid)
    await update.message.reply_text("Bekor qilindi.")

async def msg_on(update, ctx):
    await asyncio.to_thread(
        set_proactive, update.message.chat_id, True)
    await update.message.reply_text(
        "Yaxshi, ba'zan o'zim yozib turaman.")

async def msg_off(update, ctx):
    await asyncio.to_thread(
        set_proactive, update.message.chat_id, False)
    await update.message.reply_text(
        "Xo'p, endi o'zim yozmayman. Qaytarish: /xabar_bor")

async def my_id(update, ctx):
    await update.message.reply_text(
        f"Sizning ID: {update.effective_user.id}")

# ---------- xotirani boshqarish ----------
def list_facts(cid):
    r = (db.table("facts").select("id,fact")
         .eq("chat_id", cid).order("id").execute())
    return r.data

def del_fact(cid, fid):
    (db.table("facts").delete()
     .eq("id", fid).eq("chat_id", cid).execute())

async def memory_cmd(update, ctx):
    rows = await asyncio.to_thread(
        list_facts, update.message.chat_id)
    if not rows:
        await update.message.reply_text(
            "Hozircha siz haqingizda hech narsa saqlanmagan.")
        return
    text = "\n".join(f'{i}. {x["fact"]}'
                     for i, x in enumerate(rows, 1))
    await update.message.reply_text(
        text + "\n\nO'chirish: /fakt_ochir 2, 5\n"
        "Qo'shish: /eslab_qol <matn>")

async def fact_del(update, ctx):
    cid = update.message.chat_id
    raw = " ".join(ctx.args).replace(",", " ")
    nums = [int(x) for x in raw.split() if x.isdigit()]
    if not nums:
        await update.message.reply_text(
            "Masalan: /fakt_ochir 2, 5, 7")
        return
    rows = await asyncio.to_thread(list_facts, cid)
    ids = [rows[n - 1]["id"] for n in dict.fromkeys(nums)
           if 1 <= n <= len(rows)]
    if not ids:
        await update.message.reply_text(
            "Bunday raqam yo'q. Ro'yxat: /xotira")
        return
    for fid in ids:
        await asyncio.to_thread(del_fact, cid, fid)
    await update.message.reply_text(
        f"O'chirildi: {len(ids)} ta. Yangi ro'yxat: /xotira")

async def fact_add(update, ctx):
    text = " ".join(ctx.args).strip()
    if not text:
        await update.message.reply_text(
            "Masalan: /eslab_qol Tug'ilgan kunim 5-may")
        return
    await asyncio.to_thread(
        save_fact, update.message.chat_id, text[:300])
    await update.message.reply_text("Eslab qoldim.")

async def summary_cmd(update, ctx):
    s_ = await asyncio.to_thread(
        get_summary, update.message.chat_id)
    await update.message.reply_text(
        s_ or "Xulosa hali yo'q (30 xabardan keyin paydo bo'ladi).")

async def refresh_cmd(update, ctx):
    cid = update.message.chat_id
    try:
        await update_facts(cid)
        await update.message.reply_text(
            "Xotira yangilandi. Ko'rish: /xotira")
    except Exception as e:
        print("YANGILA xato:", e)
        await update.message.reply_text(
            "Hozir yangilab bo'lmadi, keyinroq urinib ko'ring.")

# ---------- o'zi birinchi yozishi ----------
SLOT_PROMPTS = {
    "ertalab": "Foydalanuvchiga ertalabki qisqa, iliq salom "
               "yoz (1-2 gap). Mos bo'lsa, oxirgi suhbatdan "
               "biror narsa haqida so'ra. Oldingi suhbatdagi "
               "eski eslatma yoki bajarilgan ishlarni qayta "
               "eslatma.",
    "kechqurun": "Foydalanuvchiga kechki qisqa, iliq xabar "
                 "yoz: kuni qanday o'tganini so'ra (1-2 gap). "
                 "Oldingi suhbatdagi eski eslatma yoki "
                 "bajarilgan ishlarni qayta eslatma.",
}

async def greet(app, cid, kind):
    history = await asyncio.to_thread(get_history, cid)
    facts = await asyncio.to_thread(get_facts, cid)
    system = (BASE + "\n\nHozirgi Toshkent vaqti: "
              + datetime.now(TZ).strftime("%A, %Y-%m-%d %H:%M"))
    if facts:
        system += ("\n\nFoydalanuvchi haqida bilganlaring:\n- "
                   + "\n- ".join(facts))
    summ = await asyncio.to_thread(get_summary, cid)
    if summ:
        system += ("\n\nOldingi suhbatlar xulosasi (faqat "
                   "kontekst uchun):\n" + summ)
    hist = "\n".join(f'{h["role"]}: {h["content"]}'
                     for h in history[-8:])
    prompt = SLOT_PROMPTS[kind] + "\n\nOxirgi suhbat:\n" + hist
    r = await gen(
        model=MODEL, contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=system))
    text = (r.text or "").strip()
    if text:
        await app.bot.send_message(cid, text)
        await asyncio.to_thread(save_msg, cid, "model", text)

async def proactive_loop(app):
    while True:
        try:
            now = datetime.now(TZ)
            day = now.strftime("%Y-%m-%d")
            for kind, hour in SLOTS:
                if not (hour <= now.hour < hour + 3):
                    continue
                key = f"{day}-{kind}"
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
        except Exception as e:
            print("PROAKTIV xato:", e)
        await asyncio.sleep(300)

# ---------- eslatma sikli ----------
async def reminder_loop(app):
    while True:
        try:
            rows = await asyncio.to_thread(due_reminders)
            for x in rows:
                try:
                    await app.bot.send_message(
                        x["chat_id"], "⏰ Eslatma: " + x["task"])
                except Exception as e:
                    print("Yuborish xato:", e)
                await asyncio.to_thread(mark_done, x["id"])
        except Exception as e:
            print("SIKL xato:", e)
        await asyncio.sleep(30)

async def start_loop(app):
    app.bot_data["t1"] = asyncio.create_task(reminder_loop(app))
    app.bot_data["t2"] = asyncio.create_task(proactive_loop(app))

threading.Thread(target=run_web, daemon=True).start()
app = (ApplicationBuilder().token(os.environ["BOT_TOKEN"])
       .post_init(start_loop).build())
app.add_handler(CommandHandler("id", my_id))
app.add_handler(CommandHandler("unut", forget, filters=ACCESS))
app.add_handler(CommandHandler(
    "eslatmalar", list_rem, filters=ACCESS))
app.add_handler(CommandHandler("bekor", cancel, filters=ACCESS))
app.add_handler(CommandHandler("xabar_bor", msg_on, filters=ACCESS))
app.add_handler(CommandHandler("xabar_yoq", msg_off, filters=ACCESS))
app.add_handler(CommandHandler(
    "xotira", memory_cmd, filters=ACCESS))
app.add_handler(CommandHandler(
    "fakt_ochir", fact_del, filters=ACCESS))
app.add_handler(CommandHandler(
    "eslab_qol", fact_add, filters=ACCESS))
app.add_handler(CommandHandler(
    "xulosa", summary_cmd, filters=ACCESS))
app.add_handler(CommandHandler(
    "yangila", refresh_cmd, filters=ACCESS))
app.add_handler(MessageHandler(
    (filters.VOICE | filters.PHOTO |
     (filters.TEXT & ~filters.COMMAND)) & ACCESS, handle))
app.run_polling()
