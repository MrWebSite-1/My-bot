import os, json, asyncio, threading
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
BASE = "O'zbek tilida, aniq va samimiy javob ber."
HISTORY = 16
EXTRACT_EVERY = 10

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
        system = BASE
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
            contents.append(types.Content(role="user", parts=[
                types.Part.from_bytes(data=data,
                                      mime_type="audio/ogg"),
                types.Part(text='Bu ovozli xabar. Faqat JSON '
                    'qaytar: {"transcript": "...", "answer": "..."}')]))
            cfg = types.GenerateContentConfig(
                system_instruction=system,
                response_mime_type="application/json")
            r = await client.aio.models.generate_content(
                model=MODEL, contents=contents, config=cfg)
            try:
                j = json.loads(r.text)
                user_text, answer = j["transcript"], j["answer"]
            except Exception:
                user_text, answer = "[ovozli xabar]", r.text
        else:
            user_text = msg.text
            contents.append(types.Content(
                role="user", parts=[types.Part(text=user_text)]))
            cfg = types.GenerateContentConfig(
                system_instruction=system)
            r = await client.aio.models.generate_content(
                model=MODEL, contents=contents, config=cfg)
            answer = r.text

        await msg.reply_text(answer)
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

async def forget(update, ctx):
    await asyncio.to_thread(wipe, update.message.chat_id)
    await update.message.reply_text("Xotira tozalandi.")

threading.Thread(target=run_web, daemon=True).start()
app = ApplicationBuilder().token(os.environ["BOT_TOKEN"]).build()
app.add_handler(CommandHandler("unut", forget))
app.add_handler(MessageHandler(
    filters.VOICE | (filters.TEXT & ~filters.COMMAND), handle))
app.run_polling()
