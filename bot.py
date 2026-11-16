import os, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from google import genai
from google.genai import types
import edge_tts
from telegram.ext import (ApplicationBuilder,
    MessageHandler, filters)

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
MODEL = "gemini-3.5-flash-lite"  # sizda ishlagan nom
VOICE = "uz-UZ-MadinaNeural"
PROMPT = "O'zbek tilida, qisqa va samimiy javob ber."

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

async def tts(text):
    buf = bytearray()
    async for ch in edge_tts.Communicate(text, VOICE).stream():
        if ch["type"] == "audio":
            buf += ch["data"]
    return bytes(buf)

async def handle(update, ctx):
    msg = update.message
    if msg.voice:
        f = await msg.voice.get_file()
        data = bytes(await f.download_as_bytearray())
        contents = [
            types.Part.from_bytes(data=data, mime_type="audio/ogg"),
            "Foydalanuvchi ovozli xabar yubordi. " + PROMPT]
    else:
        contents = PROMPT + "\n\n" + msg.text
    r = client.models.generate_content(model=MODEL, contents=contents)
    await msg.reply_text(r.text)
    if msg.voice:
        try:
            await msg.reply_voice(await tts(r.text))
        except Exception as e:
            print("TTS xato:", e)

threading.Thread(target=run_web, daemon=True).start()
app = ApplicationBuilder().token(os.environ["BOT_TOKEN"]).build()
app.add_handler(MessageHandler(
    filters.VOICE | (filters.TEXT & ~filters.COMMAND), handle))
app.run_polling()
