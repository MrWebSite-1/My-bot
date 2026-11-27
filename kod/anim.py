"""JONLANTIRISH KANALI (J1): botning javoblari mascot/web sahifa uchun hodisaga aylanadi.
Faqat lichka (cid > 0). Xotira/tarix kanalga CHIQMAYDI. Har qanday xato suhbatni buzmaydi.
Hodisa: {"id","t","bot","kind","emotion","text","speak_ms"}  (04-Jonlantirish-yol-xaritasi.md, 3-bo'lim)
"""
import os, re, threading, time
from collections import deque

QUEUE = deque(maxlen=20)
_LOCK = threading.Lock()
_ID = [0]

# bubble matni: ANIM_TEXT=0 bo'lsa hech qachon matn chiqmaydi (faqat hissiyot)
TEXT_ON = os.environ.get("ANIM_TEXT", "1").strip() != "0"
MAX_TEXT = 110
MS_PER_CHAR = 60

# sezgir mavzular: matn chiqmaydi, faqat hissiyot (chat.py dagi regex bilan mos + kengaytirilgan)
SENSITIVE = re.compile(
    r"kasal|dori|og'ri|shifokor|o'lim|yo'qot|depress|xafa|yig'la|qarz|pul|"
    r"ajrash|uyqu|qiyin|vrach|kasalxona|jarroh|o'ldi|vafot|kredit|to'lov|"
    r"karta|parol|manzil", re.I)

# kun kayfiyati (MOODS matni) -> hissiyot belgisi; kalit so'z bo'yicha
_MOOD_KEYS = [("quvnoq", "sevinch"), ("sho'x", "erkalash"), ("o'ynoq", "erkalash"),
              ("mehribon", "erkalash"), ("charchagan", "uyqu"), ("bezovta", "hayron"),
              ("sokin", "o'ylash"), ("o'ychan", "o'ylash"), ("fikrga", "o'ylash")]

# botning o'zi yozganda sukut hissiyot
PROACTIVE_EMOTION = {"greet": "salomlashish", "kun_oxiri": "erkalash", "ask": "o'ylash"}


def _bot_name():
    try:
        from character import NAME
        return NAME
    except Exception:
        return "bot"


def _labels():
    from utils import STICKER_LABELS, EMOJI_LABEL, _ne
    return STICKER_LABELS, EMOJI_LABEL, _ne


def pick_emotion(sticker=None, reaction=None, mood=None):
    """Tartib: 1) sticker belgisi, 2) reaction emoji, 3) kun kayfiyati, 4) 'boshqa'."""
    try:
        labels, emoji_label, ne = _labels()
        if isinstance(sticker, str):
            s = sticker.strip().lower()
            if s in labels and s != "boshqa":
                return s
            lab = emoji_label.get(ne(sticker))
            if lab:
                return lab
        if isinstance(reaction, str) and reaction.strip():
            lab = emoji_label.get(ne(reaction))
            if lab:
                return lab
        if isinstance(mood, str):
            low = mood.lower()
            for k, lab in _MOOD_KEYS:
                if k in low:
                    return lab
    except Exception:
        pass
    return "boshqa"


def safe_text(text, *context):
    """Bubble uchun qisqa matn. Sezgir mavzuda yoki ANIM_TEXT=0 da bo'sh qaytadi."""
    if not TEXT_ON:
        return ""
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    if not t:
        return ""
    if SENSITIVE.search(" ".join(str(c or "") for c in (t,) + context)):
        return ""
    if len(t) <= MAX_TEXT:
        return t
    cut = t[:MAX_TEXT]
    m = list(re.finditer(r"[.!?…]\s", cut))
    if m and m[-1].end() > 30:
        return cut[:m[-1].end()].strip()
    return cut.rsplit(" ", 1)[0].rstrip(",;:- ") + "…"


def emit(bot, kind, emotion, text="", speak_ms=0):
    """Hodisani navbatga qo'shadi. Xato bo'lsa None (hech qachon exception ko'tarmaydi)."""
    try:
        with _LOCK:
            _ID[0] += 1
            ev = {"id": _ID[0], "t": int(time.time()), "bot": bot, "kind": kind,
                  "emotion": emotion, "text": text or "", "speak_ms": int(speak_ms or 0)}
            QUEUE.append(ev)
            return ev
    except Exception as e:
        print("ANIM emit xato:", e)
        return None


def get_since(since=0):
    """since dan keyingi hodisalar (eskidan yangiga) + oxirgi id."""
    with _LOCK:
        return [e for e in QUEUE if e["id"] > since], _ID[0]


# ---- chat.py / loops.py uchun qulay o'ramlar (cid > 0 va xato-xavfsiz) ----
def typing(cid):
    if cid > 0:
        emit(_bot_name(), "typing", "o'ylash")


def on_reply(cid, answer, user_text="", sticker=None, reaction=None, mood=None, speech=None):
    if cid <= 0:
        return
    try:
        text = safe_text(answer, user_text)
        if not mood:   # qo'lda qo'yilmagan bo'lsa, kunning avtomatik kayfiyati
            from utils import auto_mood
            mood = auto_mood(cid)
        base = speech if isinstance(speech, str) and speech.strip() else answer
        emit(_bot_name(), "reply", pick_emotion(sticker, reaction, mood), text,
             min(12000, len(str(base or "")) * MS_PER_CHAR))
    except Exception as e:
        print("ANIM xato:", e)


def proactive(cid, what, text=""):
    """what: greet | kun_oxiri | ask"""
    if cid <= 0:
        return
    try:
        emit(_bot_name(), "proactive", PROACTIVE_EMOTION.get(what, "boshqa"),
             safe_text(text), min(12000, len(str(text or "")) * MS_PER_CHAR))
    except Exception as e:
        print("ANIM xato:", e)
