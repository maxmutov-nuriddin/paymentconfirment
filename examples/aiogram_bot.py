"""Misol: aiogram 3 bot + PayGate integratsiyasi.

pip install aiogram aiohttp httpx
.env: BOT_TOKEN, PAYGATE_URL, SHOP_ID, SHOP_KEY, CALLBACK_PORT
PayGate botida: /callback SHOP_ID http://<shu-bot-server>:8080/pay/complete
"""
import asyncio
import hashlib
import os

import httpx
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message
from aiohttp import web

BOT_TOKEN = os.environ["BOT_TOKEN"]
PAYGATE_URL = os.getenv("PAYGATE_URL", "http://localhost:8000")
SHOP_ID = os.environ["SHOP_ID"]
SHOP_KEY = os.environ["SHOP_KEY"]
CALLBACK_PORT = int(os.getenv("CALLBACK_PORT", "8080"))

bot = Bot(BOT_TOKEN)
dp = Dispatcher()
orders: dict[str, int] = {}  # order_id -> telegram user_id (real loyihada DB)


@dp.message(Command("pay"))
async def pay(msg: Message):
    order_id = f"U{msg.from_user.id}-{msg.message_id}"
    async with httpx.AsyncClient() as c:
        r = (await c.post(f"{PAYGATE_URL}/payment/create", data={
            "shop_id": SHOP_ID, "shop_key": SHOP_KEY, "amount": 10000, "order_id": order_id,
        })).json()
    if "error" in r:
        return await msg.answer(f"❌ {r['error']}")
    orders[order_id] = msg.from_user.id
    await msg.answer(
        f"💳 Karta: <code>{r['card']}</code>\n"
        f"👤 {r.get('card_holder', '')}\n"
        f"💰 Aynan <b>{r['amount']}</b> so'm o'tkazing\n"
        f"⏱ {r['expires_in'] // 60} daqiqa ichida",
        parse_mode="HTML",
    )


async def complete(request: web.Request):
    f = await request.post()
    sign = hashlib.md5(f"{SHOP_ID}{f['payment_id']}{f['amount']}{SHOP_KEY}".encode()).hexdigest()
    if sign != f.get("sign"):
        return web.Response(status=403, text="bad sign")
    user_id = orders.pop(f["order_id"], None)
    if user_id:
        text = "✅ To'lov qabul qilindi!" if f["status"] == "paid" else "❌ To'lov vaqti tugadi."
        await bot.send_message(user_id, text)
    return web.Response(text="OK")


async def main():
    app = web.Application()
    app.router.add_post("/pay/complete", complete)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", CALLBACK_PORT).start()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
