"""BUYRUQLAR (/unut, /xotira, /uslub, /ovoz, /stiker_... va boshqalar).
"""
import asyncio
from datetime import datetime
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from config import (TZ, REPEATS, STYLES, VOICE, VOICES, RATES, VMODES,
                    ENGINES, GVOICES, STATE)
from database import (wipe, pending, del_rem, set_proactive, list_facts,
                      del_fact, save_fact, get_summary, get_settings,
                      update_setting)
from memory import update_facts
from utils import mark, tier_of, today_state, STATE_TAG
from voice import tts, tts_gemini

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

def _fact_tail(x):
    if x.get("expires_at"):
        d = datetime.fromisoformat(
            x["expires_at"].replace("Z", "+00:00"))
        return f" (gacha: {d.astimezone(TZ).strftime('%d.%m')})"
    return ""

def _tier_of_row(fact):
    return "holat" if fact.startswith(STATE_TAG) else tier_of(fact)

async def _tier_reply(update, want, title, empty):
    rows = await asyncio.to_thread(
        list_facts, update.message.chat_id)
    lines = []
    # Raqamlar umumiy ro'yxatdagidek qoladi, shunda /fakt_ochir to'g'ri ishlaydi
    for i, x in enumerate(rows, 1):
        if _tier_of_row(x["fact"]) == want:
            lines.append(f'{i}. {x["fact"]}{_fact_tail(x)}')
    if not lines:
        await update.message.reply_text(empty)
        return
    await update.message.reply_text(
        title + "\n" + "\n".join(lines) + "\n\n"
        "O'chirish: /fakt_ochir 2, 5")

async def memory_cmd(update, ctx):
    rows = await asyncio.to_thread(
        list_facts, update.message.chat_id)
    if not rows:
        await update.message.reply_text(
            "Hozircha siz haqingizda hech narsa saqlanmagan.")
        return
    cnt = {"muhim": 0, "oddiy": 0, "holat": 0}
    for x in rows:
        t = _tier_of_row(x["fact"])
        if t in cnt:
            cnt[t] += 1
    now_state = today_state(rows)
    holat = now_state if now_state else "aytilmagan"
    await update.message.reply_text(
        "Xotira bo'limlari:\n\n"
        f"🔒 Muhim: {cnt['muhim']} ta (ism, oila, muhim sanalar)\n"
        f"📝 Oddiy: {cnt['oddiy']} ta (hobbi, odat, loyiha)\n"
        f"💭 Bugungi holat: {holat}\n\n"
        "Ko'rish: /xotira_muhim, /xotira_oddiy, /xotira_holat\n"
        "Qo'shish: /eslab_qol <matn>")

async def memory_muhim(update, ctx):
    await _tier_reply(update, "muhim", "🔒 Muhim:",
                      "Muhim fakt yo'q.")

async def memory_oddiy(update, ctx):
    await _tier_reply(update, "oddiy", "📝 Oddiy:",
                      "Oddiy fakt yo'q.")

async def memory_holat(update, ctx):
    rows = await asyncio.to_thread(
        list_facts, update.message.chat_id)
    st_now = today_state(rows)
    if st_now:
        await update.message.reply_text(
            f"💭 Bugungi holat: {st_now}\n\n"
            "Yangisini aytsangiz, shu almashadi.")
    else:
        await update.message.reply_text(
            "Bugun kayfiyat yoki holat aytilmagan.")

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
    ce = st.get("engine") or "gemini"
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
