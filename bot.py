"""BOT: ishga tushirish fayli. Faqat buyruqlarni ulaydi.
Yangi buyruq qo'shish: commands.py da funksiya yozing, pastda ro'yxatga qo'shing.
"""
import os, threading
from telegram.ext import (ApplicationBuilder, MessageHandler,
                          CommandHandler, CallbackQueryHandler, filters)
from config import ACCESS
from server import run_web
from loops import start_loop
from chat import handle
from buttons import on_button
from commands import (forget, list_rem, cancel, msg_on, msg_off, my_id,
                      memory_cmd, fact_del, fact_add, summary_cmd,
                      refresh_cmd, style_cmd, voice_cmd, voice_test,
                      sticker_pack_cmd, sticker_off, sticker_on)

threading.Thread(target=run_web, daemon=True).start()
app = (ApplicationBuilder().token(os.environ["BOT_TOKEN"])
       .post_init(start_loop).build())

# buyruq nomi -> funksiya
COMMANDS = [
    ("unut", forget),
    ("eslatmalar", list_rem),
    ("bekor", cancel),
    ("xabar_bor", msg_on),
    ("xabar_yoq", msg_off),
    ("xotira", memory_cmd),
    ("fakt_ochir", fact_del),
    ("eslab_qol", fact_add),
    ("uslub", style_cmd),
    ("ovoz", voice_cmd),
    ("ovoz_test", voice_test),
    ("stiker_paket", sticker_pack_cmd),
    ("stiker_yoq", sticker_off),
    ("stiker_bor", sticker_on),
    ("xulosa", summary_cmd),
    ("yangila", refresh_cmd),
]

app.add_handler(CommandHandler("id", my_id))
app.add_handler(CallbackQueryHandler(on_button))
for name, fn in COMMANDS:
    app.add_handler(CommandHandler(name, fn, filters=ACCESS))
app.add_handler(MessageHandler(
    (filters.VOICE | filters.PHOTO | filters.Sticker.ALL |
     (filters.TEXT & ~filters.COMMAND)) & ACCESS, handle))
app.run_polling()
