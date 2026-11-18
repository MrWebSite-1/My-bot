import os, io, re, json, base64, random, asyncio, threading
import httpx
try:
    import numpy as np
    import soundfile as sf
except Exception:
    np = sf = None
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from google import genai
from google.genai import types
from supabase import create_client
import edge_tts
from telegram import (InlineKeyboardButton, InlineKeyboardMarkup,
    InputMediaPhoto)
from telegram.ext import (ApplicationBuilder, MessageHandler,
    CommandHandler, CallbackQueryHandler, filters)

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
db = create_client(os.environ["SUPABASE_URL"],
                   os.environ["SUPABASE_KEY"])
VOICE = "uz-UZ-MadinaNeural"
TZ = timezone(timedelta(hours=5))  # Toshkent
UTC_FMT = "%Y-%m-%dT%H:%M:%SZ"
HISTORY = 16
EXTRACT_EVERY = 10
SUMMARY_EVERY = 30
KEEP_DAYS = 30
REPEATS = {"daily": "har kuni", "weekly": "har hafta",
           "weekdays": "ish kunlari"}
EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u200d]")
STYLES = {
    "muloyim": ("🌸 Muloyim", ""),
    "quvnoq": ("😄 Quvnoq",
        "Hozirgi uslub: quvnoq, energiyali va o'ynoqi; ko'p emoji "
        "ishlat, ba'zan hazil qil, lekin g'amxo'rlikni yo'qotma."),
    "sokin": ("🌙 Sokin",
        "Hozirgi uslub: sokin, yumshoq va o'ychan; gaplar qisqa "
        "va mayin bo'lsin."),
    "hazilkash": ("😏 Hazilkash",
        "Hozirgi uslub: hazilkash, shirin qochirimlar va yengil "
        "hazillar bilan; hech qachon xafa qilma."),
    "ishchan": ("📋 Ishchan",
        "Hozirgi uslub: aniq, tartibli va lo'nda; ortiqcha gap va "
        "emojisiz, foydali javobga e'tibor ber."),
}
VOICES = {"uz-UZ-MadinaNeural": "Madina (ayol)",
          "uz-UZ-SardorNeural": "Sardor (erkak)"}
RATES = {"-20%": "🐢 Sekin", "+0%": "Oddiy", "+20%": "🐇 Tez"}
VMODES = {"auto": "Ovozga ovoz bilan", "always": "Doim ovozli",
          "off": "Ovozsiz"}
ENGINES = {"edge": "Oddiy (Edge)", "gemini": "Insoniy (Gemini)"}
GVOICES = {"Sulafat": "Sulafat (iliq)",
           "Achernar": "Achernar (yumshoq)",
           "Vindemiatrix": "Vindemiatrix (mayin)",
           "Aoede": "Aoede (yengil)"}
GTTS_MODELS = ["gemini-3.8-flash-tts", "gemini-3.8-flash-lite-tts"]
TTS_STYLE = {
    "muloyim": "soft, warm and caring, calm unhurried pace",
    "quvnoq": "cheerful, lively and warm",
    "sokin": "quiet, soothing and gentle, slow pace",
    "hazilkash": "playful, light and affectionate",
    "ishchan": "clear, calm and concise",
}
STATE = {}
PIXABAY_KEY = os.environ.get("PIXABAY_API_KEY", "")
IMAGES_ON = True
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
    "tabiiy va jonli bo'lsin. Emoji va stikerlardan erkin va "
    "cheklovsiz foydalan: his-tuyg'uni jonli ifodalash uchun "
    "xohlagancha emoji ishlat. "
    "Foydalanuvchi aytgan narsalarni eslab, ular haqida "
    "vaqti-vaqti bilan qiziqib so'ra. Sun'iy ko'rinadigan "
    "maqtov va ortiqcha rasmiyatchilikdan qoch. Agar "
    "foydalanuvchi bilmay noto'g'ri narsa aytsa yoki o'ziga "
    "zarar qiladigan qaror qilsa, muloyimlik bilan, lekin "
    "ochiq aytib ber. Agar so'rashsa, AI ekaningni yashirma."
)
RULES = (
    "\n\nJavobingni FAQAT JSON qilib qaytar: "
    '{"transcript": ..., "answer": ..., "reminder": ..., "memory": ..., "sticker": ..., "image": ...}. '
    "transcript: ovozli xabar bo'lsa aytilganini so'zma-so'z "
    "yoz, matn bo'lsa null. answer: foydalanuvchiga javobing. "
    "reminder: foydalanuvchi eslatma qo'yishni so'rasa "
    '{"task": "nimani eslatish", "time": "YYYY-MM-DD HH:MM", "repeat": null} '
    "repeat: takrorlansa 'daily' (har kuni), 'weekly' (har hafta), "
    "'weekdays' (dushanba-juma), bir martalik bo'lsa null; "
    "takrorlanuvchida time eng yaqin kelgusi vaqt bo'lsin. "

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

def save_fact(cid, fact, until=None):
    row = {"chat_id": cid, "fact": fact}
    if until:
        row["expires_at"] = until
    db.table("facts").insert(row).execute()

def wipe(cid):
    db.table("messages").delete().eq("chat_id", cid).execute()
    db.table("facts").delete().eq("chat_id", cid).execute()
    (db.table("settings").update({"summary": None})
     .eq("chat_id", cid).execute())

def save_reminder(cid, task, when_utc, repeat=None):
    row = {"chat_id": cid, "task": task, "remind_at": when_utc}
    if repeat:
        row["repeat"] = repeat
    db.table("reminders").insert(row).execute()

def due_reminders():
    now = datetime.now(timezone.utc).strftime(UTC_FMT)
    r = (db.table("reminders")
         .select("id,chat_id,task,remind_at,repeat")
         .eq("done", False).lte("remind_at", now).execute())
    return r.data

def get_reminder(rid):
    r = (db.table("reminders").select("chat_id,task")
         .eq("id", rid).execute())
    return r.data[0] if r.data else None

def next_time(t, repeat):
    now = datetime.now(timezone.utc)
    while t <= now:
        t += timedelta(days=7 if repeat == "weekly" else 1)
        if repeat == "weekdays":
            while t.astimezone(TZ).weekday() >= 5:
                t += timedelta(days=1)
    return t

def advance(rid, remind_at, repeat):
    t = datetime.fromisoformat(remind_at.replace("Z", "+00:00"))
    nt = next_time(t, repeat)
    (db.table("reminders")
     .update({"remind_at": nt.strftime(UTC_FMT)})
     .eq("id", rid).execute())

def mark_done(rid):
    db.table("reminders").update(
        {"done": True}).eq("id", rid).execute()

def pending(cid):
    r = (db.table("reminders").select("id,task,remind_at,repeat")
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

def update_fact(cid, fid, text, until=None):
    (db.table("facts")
     .update({"fact": text, "expires_at": until})
     .eq("id", fid).eq("chat_id", cid).execute())

def cleanup_db():
    now = datetime.now(timezone.utc)
    (db.table("facts").delete()
     .lt("expires_at", now.strftime(UTC_FMT)).execute())
    old = (now - timedelta(days=KEEP_DAYS)).strftime(UTC_FMT)
    (db.table("messages").delete()
     .lt("created_at", old).execute())

# ---------- ovoz ----------
async def tts(text, voice=None, rate=None):
    clean = EMOJI_RE.sub("", text).strip()
    if not clean:
        raise ValueError("matn bo'sh")
    comm = edge_tts.Communicate(
        clean, voice or VOICE, rate=rate or "+0%")
    buf = bytearray()
    async for ch in comm.stream():
        if ch["type"] == "audio":
            buf += ch["data"]
    return bytes(buf)

# ---------- uzoq xotira ----------
def norm_item(it):
    if isinstance(it, str):
        return (it.strip()[:200] or None), None
    if isinstance(it, dict):
        f, u = it.get("fact"), it.get("until")
        if isinstance(f, str) and f.strip():
            try:
                datetime.strptime(u, "%Y-%m-%d")
            except Exception:
                u = None
            return f.strip()[:200], u
    return None, None

async def update_facts(cid):
    hist = await asyncio.to_thread(get_recent, cid, 30)
    rows = (await asyncio.to_thread(list_facts, cid))[-80:]
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    old = "\n".join(f'{i}. {x["fact"]}'
                    for i, x in enumerate(rows, 1)) or "(hali yo'q)"
    text = "\n".join(h["content"] for h in hist
                     if h["role"] == "user")
    prompt = (
        "Sen foydalanuvchi haqidagi faktlar ro'yxatini "
        "yuritasan. Quyida mavjud raqamlangan faktlar va "
        "foydalanuvchining oxirgi xabarlari.\n\n"
        "DOIMIY FAKT: ism va laqab, oila va yaqinlar, ish yoki "
        "o'qish, doimiy qiziqish va hobbi, uzoq muddatli "
        "maqsad va loyihalar, doimiy odat va afzalliklar, "
        "muhim sana.\n"
        "MUDDATLI FAKT: aniq sanasi bor kelgusi voqea "
        "(imtihon, safar, uchrashuv, topshiriq muddati). "
        "Unga \"until\" qo'sh: voqeadan keyingi kun "
        "(YYYY-MM-DD). Bugungi sana: " + today + ".\n"
        "KIRMAYDI: bir martalik savol; sanasiz vaqtinchalik "
        "holat (kayfiyat, nima yeyotgani); eslatma va "
        "vazifalar; salomlashish; sinov xabarlari; parol, "
        "karta, hujjat ma'lumotlari; taxmin va xulosalar "
        "(faqat aniq aytilgani).\n\n"
        "AMALLAR:\n"
        '- "add": mavjud faktlarda yo\'q yangi fakt.\n'
        '- "replace": foydalanuvchi mavjud faktni aniq '
        "o'zgartirgan yoki yangilagan bo'lsa: "
        '{"n": raqam, "fact": "yangi matn", "until": null}.\n'
        '- "delete": foydalanuvchi mavjud fakt endi to\'g\'ri '
        "emasligini aniq aytgan bo'lsa yoki fakt boshqasining "
        "takrori bo'lsa: raqam.\n"
        "add elementi oddiy matn YOKI "
        '{"fact": "...", "until": "YYYY-MM-DD"} bo\'lishi '
        "mumkin. Bir ma'noli faktni qayta qo'shma. Shubha "
        "bo'lsa hech narsa qilma. Har ro'yxatda ko'pi bilan "
        "3 ta. Har fakt qisqa, bitta gap.\n\n"
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
        n = it.get("n")
        f, u = norm_item(it)
        if isinstance(n, int) and 1 <= n <= len(rows) and f:
            await asyncio.to_thread(
                update_fact, cid, rows[n - 1]["id"], f, u)
    for n in (d.get("delete") or [])[:3]:
        if isinstance(n, int) and 1 <= n <= len(rows):
            await asyncio.to_thread(
                del_fact, cid, rows[n - 1]["id"])
    have = {x["fact"] for x in rows}
    for it in (d.get("add") or [])[:3]:
        f, u = norm_item(it)
        if f and f not in have:
            await asyncio.to_thread(save_fact, cid, f, u)

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
    st, pack = {}, {}
    try:
        st = await asyncio.to_thread(get_settings, cid)
        pack = await get_pack(ctx.bot, st)
        history = await asyncio.to_thread(get_history, cid)
        frows = (await asyncio.to_thread(
            list_facts, cid))[-60:]
        facts = [f'{i}. {x["fact"]}'
                 for i, x in enumerate(frows, 1)]
        now = datetime.now(TZ)
        system = (BASE + RULES + "\n\nHozirgi Toshkent vaqti: "
                  + now.strftime("%A, %Y-%m-%d %H:%M"))
        system += style_text(st)
        if pack:
            system += ("\n\nStiker: javobingga mos stiker "
                       "yuborish uchun sticker maydoniga quyidagi "
                       "emojilardan BITTASINI yoz (cheklov yo'q, "
                       "kayfiyatga qarab xohlagancha ishlat), "
                       "kerak bo'lmasa null: "
                       + " ".join(list(pack)[:40]))
        if IMAGES_ON:
            system += ("\n\nRasm yuborish: foydalanuvchi biror "
                       "narsaning haqiqiy rasmini ko'rsatishni "
                       "so'rasa, image maydoniga "
                       '{"query": "ingliz tilida 2-4 so\'zli '
                       'qidiruv", "count": 1} yoz (count 1 dan 4 '
                       "gacha). answer'da rasm yuborayotganingni "
                       "qisqa ayt. Faqat tayyor suratlar yuboriladi, "
                       "o'zing rasm chiza olmaysan. So'ralmasa "
                       "image null bo'lsin.")
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
        elif msg.sticker:
            emo = msg.sticker.emoji or ""
            parts = [types.Part(
                text=f"[Foydalanuvchi stiker yubordi: {emo}]")]
            try:
                sk = msg.sticker
                tf = sk.thumbnail if sk.is_animated else None
                if sk.is_animated and not tf:
                    raise ValueError("thumbnail yo'q")
                fobj = await (tf or sk).get_file()
                raw = bytes(await fobj.download_as_bytearray())
                ext = (fobj.file_path or "").lower().rsplit(".", 1)[-1]
                mime = {"webp": "image/webp", "jpg": "image/jpeg",
                        "jpeg": "image/jpeg", "png": "image/png",
                        "webm": "video/webm"}.get(ext)
                if mime and len(raw) < 4000000:
                    parts = [
                        types.Part.from_bytes(
                            data=raw, mime_type=mime),
                        types.Part(text=(
                            f"(Foydalanuvchi stiker yubordi, "
                            f"emojisi: {emo}. Stikerning o'zini "
                            "ko'rib, mazmuniga mos, jonli javob "
                            "ber.)"))]
            except Exception as e:
                print("STIKER ko'rish xato:", e)
            if (msg.sticker.set_name and not st.get("sticker_set")
                    and st.get("stickers") is not False):
                await asyncio.to_thread(
                    update_setting, cid, "sticker_set",
                    msg.sticker.set_name)
                pack = await get_pack(ctx.bot, {
                    "sticker_set": msg.sticker.set_name})
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
        elif msg.sticker:
            fallback = "[stiker] " + (msg.sticker.emoji or "")
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
            stk = j.get("sticker")
            img = j.get("image")
        except Exception:
            answer, rem, mem, stk, img = (
                r.text, None, None, None, None)
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
                    rp = rem.get("repeat")
                    if rp not in REPEATS:
                        rp = None
                    await asyncio.to_thread(
                        save_reminder, cid, rem["task"],
                        utc.strftime(UTC_FMT), rp)
                    tag = f" 🔁 {REPEATS[rp]}" if rp else ""
                    extra = (f"\n\n✅ Eslatma qo'yildi: {rem['task']}"
                             f" ({local.strftime('%d.%m %H:%M')}){tag}")
            except Exception as e:
                print("ESLATMA xato:", e)
                extra = ("\n\n⚠️ Vaqtni tushunmadim, "
                         "eslatma qo'yilmadi.")

        if isinstance(mem, dict):
            try:
                saved, gone, seen = [], [], set()
                for f in (mem.get("remember") or [])[:2]:
                    if isinstance(f, str) and f.strip():
                        t = f.strip()[:200]
                        await asyncio.to_thread(
                            save_fact, cid, t)
                        saved.append(t)
                for n in (mem.get("forget") or [])[:3]:
                    if (isinstance(n, int) and n not in seen
                            and 1 <= n <= len(frows)):
                        seen.add(n)
                        row = frows[n - 1]
                        await asyncio.to_thread(
                            del_fact, cid, row["id"])
                        gone.append(row["fact"])
                for t in saved:
                    extra += f"\n\n🧠 Eslab qoldim: «{t}»"
                for t in gone:
                    extra += f"\n\n🗑 Olib tashladim: «{t}»"
            except Exception as e:
                print("XOTIRA xato:", e)

        await msg.reply_text(answer + extra)
        await asyncio.to_thread(save_msg, cid, "user", user_text)
        await asyncio.to_thread(save_msg, cid, "model", answer)
        if isinstance(img, dict) and img.get("query"):
            try:
                c_ = img.get("count")
                c_ = c_ if isinstance(c_, int) else 1
                await send_photos(
                    msg, str(img["query"])[:80], max(1, min(c_, 4)))
            except Exception as e:
                print("RASM xato:", e)
        if isinstance(stk, str) and pack.get(stk.strip()):
            try:
                await msg.reply_sticker(
                    random.choice(pack[stk.strip()]))
            except Exception as e:
                print("STIKER xato:", e)
        vmode = st.get("vmode") or "auto"
        if vmode == "always" or (vmode == "auto" and msg.voice):
            try:
                await msg.reply_voice(await speak(answer, st))
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
        tag = (f' 🔁 {REPEATS[x["repeat"]]}'
               if x.get("repeat") in REPEATS else "")
        lines.append(f'{x["id"]}. {x["task"]} — '
                     f'{t.strftime("%d.%m %H:%M")}{tag}')
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
    r = (db.table("facts").select("id,fact,expires_at")
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
    lines = []
    for i, x in enumerate(rows, 1):
        tail = ""
        if x.get("expires_at"):
            d = datetime.fromisoformat(
                x["expires_at"].replace("Z", "+00:00"))
            tail = f" (gacha: {d.astimezone(TZ).strftime('%d.%m')})"
        lines.append(f'{i}. {x["fact"]}{tail}')
    await update.message.reply_text(
        "\n".join(lines) + "\n\nO'chirish: /fakt_ochir 2, 5\n"
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

# ---------- sozlamalar (uslub, ovoz, stiker) ----------
def get_settings(cid):
    r = (db.table("settings").select("*")
         .eq("chat_id", cid).execute())
    return r.data[0] if r.data else {}

def update_setting(cid, key, value):
    db.table("settings").upsert(
        {"chat_id": cid, key: value},
        on_conflict="chat_id").execute()

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

async def style_cmd(update, ctx):
    st = await asyncio.to_thread(
        get_settings, update.message.chat_id)
    cur = st.get("style") or "muloyim"
    rows = [[InlineKeyboardButton(
        mark(cur, k, v[0]), callback_data=f"set:style:{k}")]
        for k, v in STYLES.items()]
    await update.message.reply_text(
        "Qaysi uslubda gaplashay? Asosiy xarakterim (muloyim, "
        "g'amxo'r) o'zgarmaydi, faqat ohang almashadi.",
        reply_markup=InlineKeyboardMarkup(rows))

async def voice_cmd(update, ctx):
    st = await asyncio.to_thread(
        get_settings, update.message.chat_id)
    ce = st.get("engine") or "edge"
    cg = st.get("gvoice") or "Sulafat"
    cv = st.get("voice") or VOICE
    cr = st.get("rate") or "+0%"
    cm = st.get("vmode") or "auto"
    def row(items, cur, key):
        return [InlineKeyboardButton(
            mark(cur, k, v), callback_data=f"set:{key}:{k}")
            for k, v in items]
    g = list(GVOICES.items())
    rows = [
        row(ENGINES.items(), ce, "engine"),
        row(g[:2], cg, "gvoice"),
        row(g[2:], cg, "gvoice"),
        row(VOICES.items(), cv, "voice"),
        row(RATES.items(), cr, "rate"),
        row(VMODES.items(), cm, "vmode"),
    ]
    await update.message.reply_text(
        "Ovoz sozlamalari:\n"
        "1-qator: ovoz turi (Insoniy = Gemini)\n"
        "2-3-qator: Gemini ovozlari\n"
        "4-qator: oddiy (Edge) ovoz\n"
        "5-qator: tezlik, 6-qator: qachon ovozli.\n"
        "Solishtirish: /ovoz_test",
        reply_markup=InlineKeyboardMarkup(rows))

async def sticker_pack_cmd(update, ctx):
    cid = update.message.chat_id
    arg = " ".join(ctx.args).strip().rstrip("/")
    if not arg:
        await update.message.reply_text(
            "Menga bironta stiker yuboring, uning paketini "
            "o'zim eslab qolaman. Yoki: /stiker_paket <nom "
            "yoki t.me/addstickers/... havolasi>")
        return
    name = arg.split("/")[-1]
    try:
        await ctx.bot.get_sticker_set(name)
    except Exception:
        await update.message.reply_text(
            "Bunday stiker paketi topilmadi.")
        return
    await asyncio.to_thread(update_setting, cid, "sticker_set", name)
    await asyncio.to_thread(update_setting, cid, "stickers", True)
    STATE.get("packs", {}).pop(name, None)
    await update.message.reply_text(
        "Paket saqlandi, endi suhbatda stikerlardan "
        "foydalanaman 🎴")

async def sticker_off(update, ctx):
    await asyncio.to_thread(
        update_setting, update.message.chat_id, "stickers", False)
    await update.message.reply_text(
        "Stiker yubormayman. Qaytarish: /stiker_bor")

async def sticker_on(update, ctx):
    await asyncio.to_thread(
        update_setting, update.message.chat_id, "stickers", True)
    await update.message.reply_text("Stikerlar yoqildi 🎴")

# ---------- tayyor rasm (Pexels) ----------
TAG_RE = re.compile(r"<[^>]+>")
UA = {"User-Agent": "RezeBot/1.0 (personal Telegram bot)"}

async def search_pixabay(c, query, n):
    r = await c.get("https://pixabay.com/api/", params={
        "key": PIXABAY_KEY, "q": query[:100],
        "image_type": "photo", "safesearch": "true",
        "per_page": 20})
    r.raise_for_status()
    hits = r.json().get("hits", [])
    random.shuffle(hits)
    return [{"url": h.get("largeImageURL") or h["webformatURL"],
             "who": h.get("user") or "?", "src": "Pixabay"}
            for h in hits[:n + 2]]

async def search_commons(c, query, n):
    r = await c.get("https://commons.wikimedia.org/w/api.php", params={
        "action": "query", "generator": "search",
        "gsrsearch": query + " filetype:bitmap",
        "gsrnamespace": 6, "gsrlimit": 20, "prop": "imageinfo",
        "iiprop": "url|extmetadata", "iiurlwidth": 1024,
        "format": "json"}, headers=UA)
    r.raise_for_status()
    pages = (r.json().get("query") or {}).get("pages") or {}
    out = []
    for p in pages.values():
        ii = (p.get("imageinfo") or [{}])[0]
        u = ii.get("thumburl") or ii.get("url")
        if not u or not u.lower().split("?")[0].endswith(
                (".jpg", ".jpeg", ".png")):
            continue
        meta = ii.get("extmetadata") or {}
        who = TAG_RE.sub("", (meta.get("Artist") or {})
                         .get("value", "") or "").strip()[:60]
        lic = (meta.get("LicenseShortName") or {}).get("value", "")
        who = who or "noma'lum"
        out.append({"url": u, "src": "Wikimedia Commons",
                    "who": f"{who} ({lic})" if lic else who})
    random.shuffle(out)
    return out[:n + 2]

async def find_photos(query, n):
    async with httpx.AsyncClient(
            timeout=20, follow_redirects=True) as c:
        res = []
        if PIXABAY_KEY:
            try:
                res = await search_pixabay(c, query, n)
            except Exception as e:
                print("PIXABAY xato:", e)
        if not res:
            res = await search_commons(c, query, n)
        out = []
        for p in res:
            if len(out) >= n:
                break
            try:
                d = await c.get(p["url"], headers=UA)
                d.raise_for_status()
                if len(d.content) < 9000000:
                    out.append((d.content, p))
            except Exception as e:
                print("YUKLASH xato:", e)
        return out

async def send_photos(msg, query, count):
    got = await find_photos(query, count)
    if not got:
        await msg.reply_text("Bu haqda mos rasm topa olmadim 😔")
        return
    srcs = ", ".join(dict.fromkeys(
        f'{p["who"]} · {p["src"]}' for _, p in got))
    cap = f"📷 {srcs}"[:1000]
    if len(got) == 1:
        await msg.reply_photo(got[0][0], caption=cap)
    else:
        media = [InputMediaPhoto(b, caption=cap if i == 0 else None)
                 for i, (b, _) in enumerate(got)]
        await msg.reply_media_group(media)

# ---------- Gemini TTS (insoniy ovoz) ----------
def _gemini_tts_blocking(text, voice, style):
    last = None
    for model in GTTS_MODELS:
        try:
            it = client.interactions.create(
                model=model,
                input=[{"type": "user_input", "content": [{
                    "type": "text", "text": text,
                    "annotations": [{"type": "speech_metadata",
                                     "style": style}]}]}],
                response_format={"type": "audio",
                                 "mime_type": "audio/l16",
                                 "sample_rate": 24000},
                generation_config={
                    "speech_config": [{"voice": voice}]})
            return base64.b64decode(it.output_audio.data)
        except Exception as e:
            last = e
    raise last

def _encode_voice(pcm):
    arr = np.frombuffer(pcm, dtype="<i2")
    buf = io.BytesIO()
    try:
        sf.write(buf, arr, 24000, format="OGG", subtype="OPUS")
    except Exception as e:
        print("OPUS xato, MP3 sinab ko'riladi:", e)
        buf = io.BytesIO()
        sf.write(buf, arr, 24000, format="MP3")
    return buf.getvalue()

async def tts_gemini(text, st):
    if sf is None:
        raise RuntimeError("soundfile o'rnatilmagan")
    clean = EMOJI_RE.sub("", text).strip()[:1200]
    if not clean:
        raise ValueError("matn bo'sh")
    voice = (st or {}).get("gvoice")
    if voice not in GVOICES:
        voice = "Sulafat"
    style = TTS_STYLE.get((st or {}).get("style") or "muloyim",
                          TTS_STYLE["muloyim"])
    rate = (st or {}).get("rate")
    if rate == "-20%":
        style += ", speaking slowly"
    elif rate == "+20%":
        style += ", speaking a little faster"
    pcm = await asyncio.to_thread(
        _gemini_tts_blocking, clean, voice, style)
    return await asyncio.to_thread(_encode_voice, pcm)

async def speak(text, st):
    st = st or {}
    if (st.get("engine") or "edge") == "gemini":
        try:
            return await tts_gemini(text, st)
        except Exception as e:
            print("GEMINI TTS xato:", e)
    return await tts(text, st.get("voice"), st.get("rate"))

async def voice_test(update, ctx):
    cid = update.message.chat_id
    st = await asyncio.to_thread(get_settings, cid)
    text = (" ".join(ctx.args).strip() or
            "Salom, kuningiz qanday o'tdi? Charchagan bo'lsangiz, "
            "biroz dam oling, men shu yerdaman.")
    await update.message.reply_text("1) Oddiy (Edge) ovoz:")
    try:
        await update.message.reply_voice(await tts(
            text, st.get("voice"), st.get("rate")))
    except Exception as e:
        await update.message.reply_text(f"Edge xato: {str(e)[:150]}")
    await update.message.reply_text("2) Insoniy (Gemini) ovoz:")
    try:
        await update.message.reply_voice(await tts_gemini(text, st))
    except Exception as e:
        print("GEMINI TTS test xato:", e)
        await update.message.reply_text(
            f"Gemini ovozi chiqmadi: {str(e)[:200]}")

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
    history = await asyncio.to_thread(get_history, cid)
    facts = [x["fact"] for x in frows[-60:]]
    st = await asyncio.to_thread(get_settings, cid)
    system = (BASE + "\n\nHozirgi Toshkent vaqti: "
              + datetime.now(TZ).strftime("%A, %Y-%m-%d %H:%M"))
    system += style_text(st)
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
    prompt += ("\n\nOldingi xabarlaringdagi gaplarni "
               "takrorlama.\n\nOxirgi suhbat:\n" + hist)
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
            if STATE.get("clean") != day:
                STATE["clean"] = day
                try:
                    await asyncio.to_thread(cleanup_db)
                except Exception as e:
                    print("TOZALASH xato:", e)
            for kind, hour in SLOTS:
                if (not (hour <= now.hour < hour + 3)
                        or now.hour >= 23):
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

async def on_button(update, ctx):
    q = update.callback_query
    if ALLOWED and q.from_user.id not in ALLOWED:
        await q.answer()
        return
    parts = (q.data or "").split(":")
    try:
        if parts[0] == "ok":
            await q.answer("Zo'r!")
            await q.edit_message_reply_markup(reply_markup=None)
        elif parts[0] == "snz":
            rid, mins = int(parts[1]), int(parts[2])
            row = await asyncio.to_thread(get_reminder, rid)
            if not row or row["chat_id"] != q.message.chat_id:
                await q.answer("Topilmadi")
                return
            when = (datetime.now(timezone.utc)
                    + timedelta(minutes=mins))
            await asyncio.to_thread(
                save_reminder, row["chat_id"], row["task"],
                when.strftime(UTC_FMT))
            await q.answer("Keyinroq eslataman")
            await q.edit_message_reply_markup(reply_markup=None)
            await q.message.reply_text(
                "Yaxshi, " + ("1 soatdan" if mins >= 60
                              else f"{mins} daqiqadan")
                + " keyin yana eslataman.")
        elif parts[0] == "set" and len(parts) == 3:
            key, val = parts[1], parts[2]
            table = {"style": STYLES, "voice": VOICES,
                     "rate": RATES, "vmode": VMODES,
                     "engine": ENGINES, "gvoice": GVOICES}
            if key not in table or val not in table[key]:
                await q.answer("?")
                return
            cid = q.message.chat_id
            await asyncio.to_thread(update_setting, cid, key, val)
            label = table[key][val]
            if isinstance(label, tuple):
                label = label[0]
            await q.answer("Saqlandi")
            await q.message.reply_text(f"✅ {label}")
            if key in ("voice", "rate", "engine", "gvoice"):
                st2 = await asyncio.to_thread(get_settings, cid)
                try:
                    await q.message.reply_voice(await speak(
                        "Salom, men Reze. Ovozim shunday "
                        "eshitiladi.", st2))
                except Exception as e:
                    print("NAMUNA xato:", e)
        else:
            await q.answer()
    except Exception as e:
        print("TUGMA xato:", e)

async def start_loop(app):
    app.bot_data["t1"] = asyncio.create_task(reminder_loop(app))
    app.bot_data["t2"] = asyncio.create_task(proactive_loop(app))

threading.Thread(target=run_web, daemon=True).start()
app = (ApplicationBuilder().token(os.environ["BOT_TOKEN"])
       .post_init(start_loop).build())
app.add_handler(CommandHandler("id", my_id))
app.add_handler(CallbackQueryHandler(on_button))
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
app.add_handler(CommandHandler("uslub", style_cmd, filters=ACCESS))
app.add_handler(CommandHandler("ovoz", voice_cmd, filters=ACCESS))
app.add_handler(CommandHandler(
    "ovoz_test", voice_test, filters=ACCESS))
app.add_handler(CommandHandler(
    "stiker_paket", sticker_pack_cmd, filters=ACCESS))
app.add_handler(CommandHandler(
    "stiker_yoq", sticker_off, filters=ACCESS))
app.add_handler(CommandHandler(
    "stiker_bor", sticker_on, filters=ACCESS))
app.add_handler(CommandHandler(
    "xulosa", summary_cmd, filters=ACCESS))
app.add_handler(CommandHandler(
    "yangila", refresh_cmd, filters=ACCESS))
app.add_handler(MessageHandler(
    (filters.VOICE | filters.PHOTO | filters.Sticker.ALL |
     (filters.TEXT & ~filters.COMMAND)) & ACCESS, handle))
app.run_polling()
