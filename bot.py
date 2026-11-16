import os, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from google import genai
from telegram.ext import (ApplicationBuilder,
    MessageHandler, filters)

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
MODEL = "gemini-3.5-flash-lite"  # AI Studio'da tekshiring

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

async def reply(update, ctx):
    r = client.models.generate_content(
        model=MODEL, contents=update.message.text)
    await update.message.reply_text(r.text)

threading.Thread(target=run_web, daemon=True).start()
app = ApplicationBuilder().token(os.environ["BOT_TOKEN"]).build()
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, reply))
app.run_polling()
