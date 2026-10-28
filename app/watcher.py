"""Telegram userbot: @HumoCardBot / @CardXabarBot xabarlarini real vaqtda tinglaydi."""
import logging

from telethon import TelegramClient, events
from telethon.sessions import StringSession

from . import config, db, matcher

log = logging.getLogger("watcher")
_client: TelegramClient | None = None


async def start() -> None:
    global _client
    if _client and _client.is_connected():
        return
    api_id = int(await db.get_setting("tg_api_id", str(config.TG_API_ID or "0")))
    api_hash = await db.get_setting("tg_api_hash", config.TG_API_HASH)
    session = await db.get_setting("tg_session", config.TG_SESSION)
    if not (api_id and api_hash and session):
        log.warning("Telegram bot menyusidan /telegram va /login ni sozlang — karta kuzatuvi o'chiq")
        return

    # catch_up=True — server o'chiq turgan paytda kelgan xabarlarni ham qayta oladi
    _client = TelegramClient(StringSession(session), api_id, api_hash,
                             catch_up=True, auto_reconnect=True)
    await _client.connect()
    if not await _client.is_user_authorized():
        log.error("TG_SESSION yaroqsiz. `python -m app.login` bilan qaytadan oling")
        await _client.disconnect()
        _client = None
        return

    chats = []
    for name in config.CARD_BOTS:
        try:
            chats.append(await _client.get_input_entity(name))
        except Exception as e:
            log.error("%s topilmadi (botni akkauntda /start qiling): %s", name, e)
    if not chats:
        log.error("Kuzatiladigan karta boti yo'q")
        return

    @_client.on(events.NewMessage(chats=chats, incoming=True))
    async def on_message(event):
        text = event.raw_text or ""
        chat = str(event.chat_id)
        msg_time = int(event.message.date.timestamp())
        await matcher.handle_incoming(chat, event.id, text, msg_time)

    me = await _client.get_me()
    log.info("Userbot ulandi: %s (id=%s). Kuzatilmoqda: %s",
             me.username or me.first_name, me.id, ", ".join(config.CARD_BOTS))


async def stop() -> None:
    if _client:
        await _client.disconnect()


def is_connected() -> bool:
    return bool(_client and _client.is_connected())
