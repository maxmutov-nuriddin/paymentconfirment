"""TG_SESSION olish uchun bir martalik skript:  python -m app.login

Natijadagi uzun satrni .env dagi TG_SESSION= ga qo'ying.
"""
import asyncio

from telethon import TelegramClient
from telethon.sessions import StringSession

from . import config


async def main() -> None:
    api_id = config.TG_API_ID or int(input("TG_API_ID: "))
    api_hash = config.TG_API_HASH or input("TG_API_HASH: ").strip()
    async with TelegramClient(StringSession(), api_id, api_hash) as client:
        await client.start()  # telefon raqam, kod va (bo'lsa) 2FA parol so'raladi
        me = await client.get_me()
        print(f"\nKirildi: {me.first_name} (id={me.id})")
        print("\n=== TG_SESSION (hech kimga bermang!) ===\n")
        print(client.session.save())
        print()


if __name__ == "__main__":
    asyncio.run(main())
