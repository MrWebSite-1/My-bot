# Qaysi o'zgarish qaysi faylda

| Nimani o'zgartirmoqchisiz | Fayl |
|---|---|
| Bot xarakteri, yozish uslubi, javob uzunligi | `character.py` (BASE) |
| JSON javob formati (eslatma, xotira, stiker, rasm maydonlari) | `character.py` (RULES) |
| Botning o'zi yozadigan salom matnlari | `character.py` (SLOT_PROMPTS, ANGLES) |
| Salom soatlari, xotira tezligi (EXTRACT_EVERY, SUMMARY_EVERY), tarix uzunligi | `config.py` |
| Uslublar ro'yxati, ovozlar, tezliklar | `config.py` |
| Ruxsat (ALLOWED_IDS) | `config.py` (+ Render) |
| Model nomlari (zaxira modellar) | `config.py` (FALLBACKS) |
| Gemini xavfsizlik filtri, qayta urinish | `ai.py` |
| Xabar kelganda nima bo'lishi (asosiy mantiq) | `chat.py` |
| Faktlarni chiqarish va xulosa promptlari | `memory.py` |
| Ovoz (Edge / Gemini) | `voice.py` |
| Rasm qidirish | `images.py` |
| Buyruqlar (/xotira, /uslub va h.k.) | `commands.py` + ro'yxat `bot.py` |
| Tugmalar bosilganda | `buttons.py` |
| Eslatma sikli, o'zi yozishi | `loops.py` |
| Baza so'rovlari | `database.py` |
| Yordamchilar (JSON o'qish, matnni bo'lib yuborish) | `utils.py` |
| Ping server | `server.py` |
| Ishga tushirish va buyruqlarni ulash | `bot.py` |

Ishga tushirish buyrug'i o'zgarmaydi: `python bot.py`.
