"""ASOSIY SUHBAT, ikki bosqichda:
  1) javob   - erkin matn (xarakter + xotira). Ovozli xabar ham shu yerda.
  2) yordamchi - alohida yengil chaqiruv JSON qaytaradi: transkript,
                 eslatma, xotira, stiker, rasm. Xato bo'lsa ham javob yuboriladi.
"""
import asyncio, random
from datetime import datetime
from google.genai import types
from ai import gen, SAFETY
from character import BASE, PLAIN, EXTRACT_RULES
from config import (TZ, UTC_FMT, REPEATS, CHAT_MODELS, FALLBACKS,
                    IMAGES_ON)
from database import (ensure_settings, get_settings, get_history,
                      list_facts, get_summary, save_msg, save_fact,
                      del_fact, save_reminder, update_setting)
from images import send_photos
from memory import maybe_extract
from utils import parse_json, send_text, style_text, get_pack
from voice import speak


async def build_system(cid, st, pack, msg, vm, frows):
    facts = [f'{i}. {x["fact"]}' for i, x in enumerate(frows, 1)]
    now = datetime.now(TZ)
    system = (BASE + PLAIN + "\n\nHozirgi Toshkent vaqti: "
              + now.strftime("%A, %Y-%m-%d %H:%M"))
    system += style_text(st)
    if vm == "always" or (vm == "auto" and msg.voice):
        system += ("\n\nBu javob OVOZ bilan yuboriladi: qisqa (1-3 gap), "
                   "og'zaki, emojisiz, ro'yxatsiz yoz; tabiiy pauzalar "
                   "uchun vergul va nuqta ishlat, raqam va sanalarni "
                   "so'z bilan yoz.")
    if facts:
        system += ("\n\nFoydalanuvchi haqida bilganlaring:\n- "
                   + "\n- ".join(facts))
    summ = await asyncio.to_thread(get_summary, cid)
    if summ:
        system += ("\n\nOldingi suhbatlar xulosasi (faqat "
                   "kontekst uchun, o'zing qayta eslatib "
                   "turma):\n" + summ)
    return system


def extract_system(pack):
    now = datetime.now(TZ).strftime("%A, %Y-%m-%d %H:%M")
    s = EXTRACT_RULES + f"\n\nHozirgi Toshkent vaqti: {now}"
    if pack:
        s += ("\n\nsticker: javobingga mos bo'lsa quyidagi emojilardan "
              "BITTASINI yoz, aks holda null: "
              + " ".join(list(pack)[:40]))
    if IMAGES_ON:
        s += ('\n\nimage: foydalanuvchi biror narsaning haqiqiy rasmini '
              'ko\'rsatishni so\'rasa {"query": "ingliz tilida 2-4 '
              'so\'zli qidiruv", "count": 1}; count 1 dan 4 gacha. '
              "So'ralmasa null.")
    return s


def history_contents(history):
    contents = [types.Content(
        role="user" if h["role"] == "user" else "model",
        parts=[types.Part(text=h["content"])])
        for h in history]
    while contents and contents[0].role == "model":
        contents.pop(0)
    return contents


async def build_parts(msg, ctx, cid, st, pack):
    """Xabarni (matn, ovoz, rasm, stiker) Gemini qismlariga aylantiradi.
    Yangi stiker paketi topilsa, pack ham yangilanib qaytadi."""
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
    return parts, pack


async def extract_side(parts, answer, frows, pack):
    """2-bosqich: eslatma, xotira, stiker, rasm, transkript (JSON).
    Xato bo'lsa bo'sh dict qaytaradi, javob baribir yuboriladi."""
    facts = "\n".join(f'{i}. {x["fact"]}'
                      for i, x in enumerate(frows, 1)) or "(yo'q)"
    prompt = ("Bot javobi:\n" + answer + "\n\nMavjud faktlar:\n"
              + facts + "\n\nYuqoridagi foydalanuvchi xabari va bot "
              "javobi asosida JSON ni to'ldir.")
    cfg = types.GenerateContentConfig(
        system_instruction=extract_system(pack), temperature=0.2,
        safety_settings=SAFETY,
        response_mime_type="application/json")
    try:
        r = await gen(models=FALLBACKS, contents=[types.Content(
            role="user", parts=parts + [types.Part(text=prompt)])],
            config=cfg)
        j = parse_json(r.text)
        return j if isinstance(j, dict) else {}
    except Exception as e:
        print("YORDAMCHI xato:", e)
        return {}


def user_text_for(msg, transcript):
    if msg.photo:
        return "[rasm] " + (msg.caption or "")
    if msg.sticker:
        return "[stiker] " + (msg.sticker.emoji or "")
    if msg.voice:
        return transcript or "[ovozli xabar]"
    return msg.text or "[ovozli xabar]"


async def apply_reminder(cid, rem):
    """Eslatma qo'yadi. Javobga qo'shiladigan izohni qaytaradi."""
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
    return extra


async def apply_memory(cid, mem, frows):
    """Foydalanuvchi so'raganda faktni saqlaydi yoki o'chiradi."""
    extra = ""
    if isinstance(mem, dict):
        try:
            saved, gone, seen = [], [], set()
            for f in (mem.get("remember") or [])[:2]:
                if isinstance(f, str) and f.strip():
                    t = f.strip()[:200]
                    await asyncio.to_thread(save_fact, cid, t)
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
            extra = ""
    return extra


async def deliver(msg, ctx, cid, vm, st, answer, extra):
    """Javobni yuboradi: matn, ovoz yoki ikkalasi (ovoz sozlamasiga qarab)."""
    want_voice = (vm == "always"
                  or (vm == "auto" and bool(msg.voice)))
    audio = None
    if want_voice:
        try:
            await ctx.bot.send_chat_action(cid, "record_voice")
            audio = await speak(answer, st)
        except Exception as e:
            print("TTS xato:", e)
    if audio and vm == "auto":
        try:
            await msg.reply_voice(audio)
            if extra.strip():
                await msg.reply_text(extra.strip())
        except Exception as e:
            print("OVOZ yuborish xato:", e)
            await send_text(msg, ctx.bot, answer, extra)
    else:
        await send_text(msg, ctx.bot, answer, extra)
        if audio:
            try:
                await msg.reply_voice(audio)
            except Exception as e:
                print("OVOZ yuborish xato:", e)


async def handle(update, ctx):
    msg = update.message
    cid = msg.chat_id
    await asyncio.to_thread(ensure_settings, cid)
    try:
        await ctx.bot.send_chat_action(cid, "typing")
    except Exception:
        pass
    st, pack = {}, {}
    try:
        st = await asyncio.to_thread(get_settings, cid)
        pack = await get_pack(ctx.bot, st)
        history = await asyncio.to_thread(get_history, cid)
        frows = (await asyncio.to_thread(
            list_facts, cid))[-60:]
        vm = st.get("vmode") or "auto"

        # 1-bosqich: erkin matn javob
        system = await build_system(cid, st, pack, msg, vm, frows)
        contents = history_contents(history)
        parts, pack = await build_parts(msg, ctx, cid, st, pack)
        contents.append(types.Content(role="user", parts=parts))
        cfg = types.GenerateContentConfig(
            system_instruction=system, temperature=1.0,
            safety_settings=SAFETY)
        r = await gen(models=CHAT_MODELS, contents=contents,
                      config=cfg)
        answer = (r.text or "").strip() or "..."

        # 2-bosqich: yordamchi ishlar (JSON)
        j = await extract_side(parts, answer, frows, pack)
        transcript = None if msg.photo else j.get("transcript")
        user_text = user_text_for(msg, transcript)
        extra = await apply_reminder(cid, j.get("reminder"))
        extra += await apply_memory(cid, j.get("memory"), frows)

        # yuborish va saqlash
        await deliver(msg, ctx, cid, vm, st, answer, extra)
        await asyncio.to_thread(save_msg, cid, "user", user_text)
        await asyncio.to_thread(save_msg, cid, "model", answer)
        img = j.get("image")
        if isinstance(img, dict) and img.get("query"):
            try:
                c_ = img.get("count")
                c_ = c_ if isinstance(c_, int) else 1
                await send_photos(
                    msg, str(img["query"])[:80], max(1, min(c_, 4)))
            except Exception as e:
                print("RASM xato:", e)
        stk = j.get("sticker")
        if isinstance(stk, str) and pack.get(stk.strip()):
            try:
                await msg.reply_sticker(
                    random.choice(pack[stk.strip()]))
            except Exception as e:
                print("STIKER xato:", e)
        await maybe_extract(cid)
    except Exception as e:
        print("XATO:", e)
        if "BLOK" in str(e):
            await msg.reply_text("Hmm, buni shu tarzda ayta "
                                 "olmadim. Boshqacha so'z bilan "
                                 "davom etamizmi?")
        else:
            await msg.reply_text("Xatolik yoki limit tugadi, "
                                 "birozdan keyin yozib ko'ring.")
