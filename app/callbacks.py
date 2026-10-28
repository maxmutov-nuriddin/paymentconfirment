import asyncio
import hashlib
import html
import logging

import httpx

from . import config, db

log = logging.getLogger("callbacks")


def make_sign(project: dict, payment_id: str, amount: int) -> str:
    raw = f"{project['shop_id']}{payment_id}{amount}{project['shop_key']}"
    return hashlib.md5(raw.encode()).hexdigest()


def payload(p: dict, project: dict, status: str) -> dict:
    return {
        "shop_id": project["shop_id"],
        "payment_id": p["payment_id"],
        "order_id": p.get("order_id") or "",
        "amount": p["amount"],
        "status": status,
        "card": p["card"],
        "paid_at": p.get("paid_at") or "",
        "sign": make_sign(project, p["payment_id"], p["amount"]),
    }


async def _post(url: str, data: dict) -> bool:
    delay = 2
    async with httpx.AsyncClient(timeout=config.CALLBACK_TIMEOUT) as client:
        for attempt in range(1, config.CALLBACK_RETRIES + 1):
            try:
                r = await client.post(url, data=data)
                if 200 <= r.status_code < 300:
                    log.info("callback %s -> %s OK (%s)", data["status"], url, r.status_code)
                    return True
                log.warning("callback %s -> %s HTTP %s (urinish %d)", data["status"], url, r.status_code, attempt)
            except httpx.HTTPError as e:
                log.warning("callback %s -> %s xato: %s (urinish %d)", data["status"], url, e, attempt)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)
    return False


async def send_prepare(p: dict) -> None:
    if p.get("prepare_url"):
        project = await db.get_project(p["shop_id"])
        if project:
            await _post(p["prepare_url"], payload(p, project, "prepare"))


async def send_complete(payment_id: str, notify: bool = True) -> None:
    p = await db.get_payment(payment_id)
    if not p:
        return
    project = await db.get_project(p.get("shop_id", ""))
    if not project:
        return
    if p.get("complete_url"):
        if await _post(p["complete_url"], payload(p, project, p["status"])):
            await db.mark_callback_ok(payment_id)
    if notify:
        await notify_owner(p)


def _fmt(amount: int) -> str:
    return f"{amount:,}".replace(",", " ")


async def notify_owner(p: dict) -> None:
    icon = "✅" if p["status"] == "paid" else "❌"
    await _notify(
        f"{icon} <b>{p['status'].upper()}</b>\n"
        f"🏪 <b>{html.escape(p.get('shop_id') or '-')}</b>\n"
        f"💰 {_fmt(p['amount'])} so'm\n"
        f"🆔 <code>{p['payment_id']}</code>\n"
        f"📦 {p.get('order_id') or '-'}"
    )


async def notify_unmatched(amount: int, text: str) -> None:
    """Kartaga pul tushdi, lekin hech qaysi to'lovga mos kelmadi — qo'lda tekshirish kerak."""
    await _notify(
        f"⚠️ <b>Mos to'lov topilmadi</b>\n💰 {_fmt(amount)} so'm\n"
        f"Noto'g'ri summa yoki kech to'lov bo'lishi mumkin — qo'lda tekshiring.\n\n"
        f"<i>{html.escape(text[:300])}</i>"
    )


async def _notify(text: str) -> None:
    chat_id = await db.get_setting("admin_chat_id", config.NOTIFY_CHAT_ID)
    if not (config.BOT_TOKEN and chat_id):
        return
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"https://api.telegram.org/bot{config.BOT_TOKEN}/sendMessage",
                data={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            )
    except httpx.HTTPError as e:
        log.warning("notify xato: %s", e)


def fire(coro) -> None:
    """Fon vazifasi sifatida ishga tushirish (API javobini kutdirmaslik uchun)."""
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


_tasks: set[asyncio.Task] = set()
