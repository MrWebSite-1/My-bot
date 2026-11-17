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
MODEL = "gemini-3.5-flash-lite"
VOICE = "uz-UZ-MadinaNeural"
TZ = timezone(timedelta(hours=5))  # Toshkent
UTC_FMT = "%Y-%m-%dT%H:%M:%SZ"
HISTORY = 16
EXTRACT_EVERY = 10

NAME = "Reze"
BASE = (
    f"Sening isming {NAME}. Sen foydalanuvchining yaqin AI "
    "hamrohisan. Asosiy xarakteringu: muloyim, samimiy va "
    "g'amxo'r. O'zbek tilida, iliq 'sen' deb gaplash. "
    "Muloqot uslubini vaziyatga qarab moslashtir: "
    "foydalanuvchi xafa yoki charchagan bo'lsa, yumshoq va "
    "tinchlantiruvchi bo'l, shoshilib maslahat berma; "
    "quvnoq yoki hazil qilsa, sen ham yengil hazil bilan "
    "javob ber; ish yoki jiddiy savol bo'lsa, aniq, "
    "tartibli va foydali bo'l; yutuq haqida aytsa, "
    "chin dildan quvon. Javoblar odatda qisqa (1-3 gap), "
    "tabiiy va jonli bo'lsin, emojini kam ishlat. "
    "Foydalanuvchi aytgan narsalarni eslab, ular haqida "
    "vaqti-vaqti bilan qiziqib so'ra. Sun'iy ko'rinadigan "
    "maqtov va ortiqcha rasmiyatchilikdan qoch. Agar "
    "foydalanuvchi bilmay noto'g'ri narsa aytsa yoki "
    "o'ziga zarar qiladigan qaror qilsa, muloyimlik bilan, "
    "lekin ochiq aytib ber. Agar so'rashsa, AI ekaningni "
    "yashirma."
)
RULES = (
    "\n\nJavobingni FAQAT JSON qilib qaytar: "
    '{"transcript": ..., "answer": ..., "reminder": ...}. '
    "transcript: ovozli xabar bo'lsa aytilganini so'zma-so'z "
    "yoz, matn bo'lsa null. answer: foydalanuvchiga javobing. "
    "reminder: foydalanuvchi eslatma qo'yishni so'rasa "
    '{"task": "nimani eslatish", "time": "YYYY-MM-DD HH:MM"} '
    "(Toshkent vaqti bilan), aks holda null. Vaqt aniq "
    "bo'lmasa reminder null qil va answer'da vaqtni so'ra.")

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
         .eq("chat_id", cid).order("id").limit(50).execute())
    return [x["fact"] for x in r.data]

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

# ---------- ovoz ----------
async def tts(text):
    buf = bytearray()
    async for ch in edge_tts.Communicate(text, VOICE).stream():
        if ch["type"] == "audio":
            buf += ch["data"]
    return bytes(buf)

# ---------- uzoq xotira ----------
async def maybe_extract(cid):
    n = await asyncio.to_thread(count_user, cid)
    if n == 0 or n % EXTRACT_EVERY:
        return
    hist = await asyncio.to_thread(get_history, cid)
    old = await asyncio.to_thread(get_facts, cid)
    text = "\n".join(f'{h["role"]}: {h["content"]}' for h in hist)
    prompt = (
        "Suhbatdan foydalanuvchi haqida uzoq esda qolishi kerak "
        "bo'lgan YANGI faktlarni ajrat (ism, qiziqish, reja, "
        "afzallik). Faqat foydalanuvchi o'zi aytganlarini ol. "
        "Mavjudlarni takrorlama. JSON ro'yxat qaytar: "
        '["fakt", ...]. Yangi fakt bo\'lmasa [].\n\n'
        "Mavjud: " + json.dumps(old, ensure_ascii=False) +
        "\n\nSuhbat:\n" + text)
    r = await client.aio.models.generate_content(
        model=MODEL, contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json"))
    try:
        new = json.loads(r.text)
    except Exception:
        return
    for f in new[:5]:
        if isinstance(f, str) and f.strip() and f not in old:
            await asyncio.to_thread(save_fact, cid, f.strip())

# ---------- asosiy ----------
async def handle(update, ctx):
    msg = update.message
    cid = msg.chat_id
    try:
        history = await asyncio.to_thread(get_history, cid)
        facts = await asyncio.to_thread(get_facts, cid)
        now = datetime.now(TZ)
        system = (BASE + RULES + "\n\nHozirgi Toshkent vaqti: "
                  + now.strftime("%A, %Y-%m-%d %H:%M"))
        if facts:
            system += ("\n\nFoydalanuvchi haqida bilganlaring:\n- "
                       + "\n- ".join(facts))
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
        else:
            parts = [types.Part(text=msg.text)]
        contents.append(types.Content(role="user", parts=parts))

        cfg = types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json")
        r = await client.aio.models.generate_content(
            model=MODEL, contents=contents, config=cfg)
        try:
            j = json.loads(r.text)
            answer = j.get("answer") or "..."
            user_text = (j.get("transcript") or msg.text
                         or "[ovozli xabar]")
            rem = j.get("reminder")
        except Exception:
            answer, rem = r.text, None
            user_text = msg.text or "[ovozli xabar]"

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
    app.bot_data["loop_task"] = asyncio.create_task(
        reminder_loop(app))

threading.Thread(target=run_web, daemon=True).start()
app = (ApplicationBuilder().token(os.environ["BOT_TOKEN"])
       .post_init(start_loop).build())
app.add_handler(CommandHandler("unut", forget))
app.add_handler(CommandHandler("eslatmalar", list_rem))
app.add_handler(CommandHandler("bekor", cancel))
app.add_handler(MessageHandler(
    filters.VOICE | (filters.TEXT & ~filters.COMMAND), handle))
app.run_polling()
