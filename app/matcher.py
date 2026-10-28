"""Kelgan kirimni ochiq to'lov bilan solishtirish va muddati o'tganlarini bekor qilish."""
import asyncio
import logging

from . import callbacks, config, db, parser

log = logging.getLogger("matcher")
_lock = asyncio.Lock()


async def handle_incoming(chat: str, msg_id: int, text: str, msg_time: int) -> str | None:
    """Karta botidan kelgan xabarni qayta ishlaydi. Mos to'lov payment_id sini qaytaradi."""
    amount = parser.parse(text)
    last4 = parser.extract_last4(text) if amount else None

    async with _lock:
        # 1) Bitta xabar ikki marta (reconnect / catch_up) — chat+msg_id bo'yicha
        row_id = await db.save_incoming(chat, msg_id, amount, last4, text, msg_time)
        if row_id is None:
            return None
        if amount is None:
            log.debug("kirim emas yoki summa topilmadi: %s", text[:80].replace("\n", " "))
            return None
        # 2) Bitta o'tkazma ikkala botdan (HumoCardBot + CardXabarBot) kelgan
        if await db.pair_cross_bot_duplicate(row_id, chat, amount, last4, msg_time, config.DUPLICATE_WINDOW):
            log.info("↩️ %s so'm — boshqa botdan kelgan nusxa, o'tkazib yuborildi", amount)
            return None
        # 3) Summa bo'yicha ochiq to'lovni topish (faqat pending -> paid)
        p = await db.find_pending_by_amount(amount, msg_time)
        if not p or not await db.set_status(p["payment_id"], "paid", text):
            log.warning("⚠️ kirim %s so'm — mos ochiq to'lov yo'q", amount)
            callbacks.fire(callbacks.notify_unmatched(amount, text))
            return None
        await db.set_incoming_match(chat, msg_id, p["payment_id"])

    log.info("✅ %s to'landi (%s so'm)", p["payment_id"], amount)
    callbacks.fire(callbacks.send_complete(p["payment_id"]))
    return p["payment_id"]


async def resend_undelivered() -> None:
    """Server qayta ishga tushganda yetib bormagan callbacklarni qayta yuborish (oxirgi 24 soat)."""
    for pid in await db.undelivered_callbacks(db.now() - 86400):
        log.info("🔁 %s callback qayta yuborilmoqda", pid)
        callbacks.fire(callbacks.send_complete(pid, notify=False))


async def expiry_loop(interval: int = 5) -> None:
    while True:
        try:
            for p in await db.expired_pending():
                async with _lock:
                    changed = await db.set_status(p["payment_id"], "cancel")
                if changed:
                    log.info("❌ %s muddati o'tdi — cancel", p["payment_id"])
                    callbacks.fire(callbacks.send_complete(p["payment_id"]))
        except Exception:
            log.exception("expiry_loop xato")
        await asyncio.sleep(interval)
