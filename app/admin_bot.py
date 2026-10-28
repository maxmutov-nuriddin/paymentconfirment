"""PayGate'ni Telegram orqali to'liq boshqarish boti."""
import asyncio
import html
import io
import json
import logging
from pathlib import Path
import re
import secrets

import httpx
import qrcode
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from telethon.sessions import StringSession

from . import config, db

log = logging.getLogger("admin_bot")
_task: asyncio.Task | None = None
_states: dict[str, tuple[str, str]] = {}
_login_client: TelegramClient | None = None
_login_task: asyncio.Task | None = None
_client: httpx.AsyncClient | None = None


def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(40.0, connect=15.0),
            limits=httpx.Limits(max_keepalive_connections=10, max_connections=20, keepalive_expiry=30.0),
        )
    return _client


async def api(method: str, **data):
    url = f"https://api.telegram.org/bot{config.BOT_TOKEN}/{method}"
    for attempt in range(1, 4):
        client = get_client()
        try:
            response = await client.post(url, data=data)
            response.raise_for_status()
            body = response.json()
            if not body.get("ok"):
                raise RuntimeError(body.get("description", "Bot API error"))
            return body.get("result")
        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as e:
            if attempt == 3:
                log.warning("Telegram API ulanish xatosi (%s, urinish %d): %s", method, attempt, e)
                raise
            await asyncio.sleep(1.0 * attempt)


async def send(
    chat_id: str, text: str, inline_keyboard: dict | None = None
) -> int | None:
    if inline_keyboard:
        reply_markup = inline_keyboard
    else:
        reply_markup = {
            "keyboard": [
                [{"text": "➕ Loyiha"}, {"text": "📂 Loyihalar"}],
                [{"text": "🔌 Ulanish"}, {"text": "📊 Statistika"}],
                [{"text": "ℹ️ Yordam"}],
            ],
            "resize_keyboard": True,
        }
    res = await api(
        "sendMessage",
        chat_id=chat_id,
        text=text,
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=json.dumps(reply_markup, ensure_ascii=False),
    )
    return res.get("message_id") if isinstance(res, dict) else None


async def edit_message(
    chat_id: str, message_id: int, text: str, reply_markup=None
) -> None:
    data = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        data["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
    try:
        await api("editMessageText", **data)
    except Exception:
        await send(chat_id, text, inline_keyboard=reply_markup)


async def delete(chat_id: str, message_id: int) -> None:
    try:
        await api("deleteMessage", chat_id=chat_id, message_id=message_id)
    except Exception:
        pass


async def send_qr(chat_id: str, url: str) -> int:
    image = qrcode.make(url)
    output = io.BytesIO()
    image.save(output, format="PNG")
    api_url = f"https://api.telegram.org/bot{config.BOT_TOKEN}/sendPhoto"
    for attempt in range(1, 4):
        client = get_client()
        try:
            response = await client.post(
                api_url,
                data={
                    "chat_id": chat_id,
                    "caption": (
                        "📲 <b>Telegram QR-Login:</b>\n"
                        "Telegram mobil ilovangizda: <b>Sozlamalar (Settings) → Qurilmalar (Devices) → Link Desktop Device</b> "
                        "orqali ushbu QR-kodni skan qiling."
                    ),
                    "parse_mode": "HTML",
                },
                files={"photo": ("paygate-login.png", output.getvalue(), "image/png")},
            )
            response.raise_for_status()
            return response.json()["result"]["message_id"]
        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as e:
            if attempt == 3:
                raise
            await asyncio.sleep(1.0 * attempt)


async def send_document(
    chat_id: str, filename: str, content: bytes, caption: str = ""
) -> None:
    api_url = f"https://api.telegram.org/bot{config.BOT_TOKEN}/sendDocument"
    files = {"document": (filename, content, "text/markdown; charset=utf-8")}
    data = {"chat_id": chat_id}
    if caption:
        data["caption"] = caption
        data["parse_mode"] = "HTML"
    for attempt in range(1, 4):
        client = get_client()
        try:
            response = await client.post(
                api_url,
                data=data,
                files=files,
            )
            response.raise_for_status()
            return
        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as e:
            if attempt == 3:
                raise
            await asyncio.sleep(1.0 * attempt)


async def qr_login(chat_id: str) -> None:
    global _login_client
    api_id = int(await db.get_setting("tg_api_id", "0"))
    api_hash = await db.get_setting("tg_api_hash")
    if not (api_id and api_hash):
        await send(
            chat_id,
            "⚙️ <b>Telegram API sozlanmagan:</b>\n\n"
            "<i>Nega kerak: Karta botlaridan (@HumoCardBot, @CardXabarBot) keladigan to‘lov xabarlarini avtomatik tutish uchun.</i>\n\n"
            "Avval Telegram API ma’lumotlaringizni yuboring:\n"
            "<code>/telegram API_ID API_HASH</code>\n\n"
            "💡 <i>(my.telegram.org saytiga telefoningiz bilan kirib 'API development tools' bo‘limidan bepul olasiz)</i>",
        )
        return
    await send(
        chat_id,
        "🔄 QR-kod tayyorlanmoqda, iltimos kuting...",
    )
    _login_client = TelegramClient(StringSession(), api_id, api_hash)
    await _login_client.connect()
    qr = await _login_client.qr_login()
    while True:
        message_id = await send_qr(chat_id, qr.url)
        try:
            await qr.wait(timeout=55)
            break
        except SessionPasswordNeededError:
            _states[chat_id] = ("2fa", "")
            await send(
                chat_id,
                "🔐 Akkauntingizda 2FA (ikki bosqichli) parol yoqilgan.\n"
                "Iltimos, 2FA parolingizni yuboring (xabar darhol o‘chiriladi):",
            )
            return
        except asyncio.TimeoutError:
            await delete(chat_id, message_id)
            await qr.recreate()
    await delete(chat_id, message_id)
    await finish_login(chat_id)


async def finish_login(chat_id: str) -> None:
    global _login_client
    session = _login_client.session.save()
    me = await _login_client.get_me()
    await db.set_setting("tg_session", session)
    await _login_client.disconnect()
    _login_client = None
    from . import watcher

    await watcher.start()
    await send(
        chat_id,
        f"✅ <b>Telegram userbot muvaffaqiyatli ulandi!</b>\n"
        f"Akkaunt: <b>{html.escape(me.first_name or str(me.id))}</b>\n\n"
        f"Endi @HumoCardBot va @CardXabarBot orqali tushgan barcha to‘lovlar avtomatik aniqlanadi va loyihalaringizga tasdiqlanadi.",
    )


def slug(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:20] or "shop"
    return f"{base}_{secrets.token_hex(2)}"


async def owner() -> str:
    return await db.get_setting("admin_chat_id")


async def get_target_project(
    shop_id_or_empty: str = "",
) -> tuple[dict | None, list[dict]]:
    projects = await db.list_projects()
    if shop_id_or_empty:
        proj = await db.get_project(shop_id_or_empty)
        if proj:
            return proj, projects
    if len(projects) == 1:
        return projects[0], projects
    return None, projects


async def project_text(p: dict, show_key: bool = False) -> str:
    base_url = await db.get_base_url()
    st = await db.stats(p["shop_id"])
    paid_count = st.get("paid", {}).get("count", 0)
    paid_sum = st.get("paid", {}).get("sum", 0)
    pending_count = st.get("pending", {}).get("count", 0)
    cancel_count = st.get("cancel", {}).get("count", 0)

    lines = [
        f"🏪 <b>{html.escape(p['name'])}</b>",
        f"🆔 SHOP_ID: <code>{p['shop_id']}</code>",
    ]
    if show_key:
        lines.append(f"🔑 SHOP_KEY: <code>{p['shop_key']}</code>")
    lines.extend([
        f"🌐 Server: <code>{html.escape(base_url)}</code>",
        f"💳 Karta: <code>{html.escape(p['cards'] or 'sozlanmagan')}</code>",
        f"👤 Egasi: <b>{html.escape(p['card_holder'] or 'sozlanmagan')}</b>",
        f"📬 Callback: <code>{html.escape(p['complete_url'] or 'sozlanmagan')}</code>",
        f"⏱ TTL: {p['payment_ttl']} soniya",
        (
            f"📊 To‘lovlar: ✅ {paid_count} ta ({paid_sum:,} so‘m)"
            f" | ⏳ {pending_count} ta | ❌ {cancel_count} ta"
        ).replace(",", " "),
    ])
    return "\n".join(lines)


def project_markup(shop_id: str) -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "🔑 Kalit (SHOP_KEY)", "callback_data": f"show:{shop_id}"},
                {"text": "🔌 Ulash (.md)", "callback_data": f"connect:{shop_id}"},
            ],
            [
                {"text": "💳 Karta", "callback_data": f"set_card:{shop_id}"},
                {"text": "👤 Egasi", "callback_data": f"set_holder:{shop_id}"},
                {"text": "🌐 Callback", "callback_data": f"set_cb:{shop_id}"},
            ],
            [
                {"text": "🗑 O‘chirish", "callback_data": f"del_ask:{shop_id}"},
            ],
        ]
    }


HELP = """<b>PayGate Boshqaruv Paneli — Qo‘llanma</b>

PayGate orqali siz HUMO va UZCARD kartalaringizga tushgan pullarni avtomatik aniqlab, har qanday bot yoki saytingizga to‘lov tizimini ulashingiz mumkin.

━━━━━━━━━━━━━━━━━━━━━
<b>📁 LOYIHALAR BOSHQARUVI:</b>

➕ <b>/new [nom]</b>
• <i>Nima qiladi:</i> Yangi loyiha (do‘kon) yaratadi.
• <i>Nega kerak:</i> Har bir saytingiz yoki botingiz uchun alohida to‘lov nuqtasi ochish. Har bir loyiha o‘z kartasi, kaliti va statistikasiga ega bo‘ladi.

📂 <b>/projects</b>
• <i>Nima qiladi:</i> Barcha faol loyihalaringiz ro‘yxatini ko‘rsatadi.
• <i>Nega kerak:</i> Loyihalaringiz holati, to‘lovlari va sozlamalarini bitta joyda boshqarish uchun.

🔑 <b>/show [SHOP_ID]</b>
• <i>Nima qiladi:</i> Loyihaning maxfiy kalitini (SHOP_KEY) ko‘rsatadi.
• <i>Nega kerak:</i> Loyihangiz (sayt/bot) PayGate bilan xavfsiz bog‘lanishi uchun ushbu maxfiy kalit kerak.

🔌 <b>/connect [SHOP_ID]</b>
• <i>Nima qiladi:</i> Loyihani PayGate'ga ulash bo‘yicha to‘liq universal .md qo‘llanma faylini yuboradi.
• <i>Nega kerak:</i> Barcha tillar (Python, PHP, Node.js va h.k.) uchun tayyor kod namunalarini olib, loyihangizga tezda o‘rnatish uchun.

🗑 <b>/delete [SHOP_ID]</b>
• <i>Nima qiladi:</i> Keraksiz loyihani o‘chiradi (faolsizlantiradi).
• <i>Nega kerak:</i> Ishlatilmayotgan loyihalarni to‘xtatish uchun.

━━━━━━━━━━━━━━━━━━━━━
<b>⚙️ LOYIHA SOZLAMALARI:</b>

💳 <b>/card [SHOP_ID] 8600...</b>
• <i>Nega kerak:</i> Xaridorlar pulni qaysi kartaga o‘tkazishi kerakligini belgilash uchun.

👤 <b>/holder [SHOP_ID] ISM FAMILIYA</b>
• <i>Nega kerak:</i> Xaridor to‘lov qilayotganda kartaning egasi ismini ko‘rib, to‘g‘ri kartaga o‘tkazayotganiga ishonch hosil qilishi uchun.

🌐 <b>/callback [SHOP_ID] https://...</b>
• <i>Nega kerak:</i> Kartaga pul tushishi bilan PayGate sizning saytingiz yoki botingizga avtomatik "To‘lov qabul qilindi, xaridni tasdiqla!" deb xabar yuborishi uchun webhook manzili.

⏱ <b>/ttl [SHOP_ID] 300</b>
• <i>Nega kerak:</i> Xaridor to‘lovni necha soniya ichida amalga oshirishi kerakligini belgilash (masalan 300 = 5 daqiqa). Vaqti o‘tsa to‘lov bekor bo‘ladi.

📊 <b>/limits [SHOP_ID] 1000 50000000</b>
• <i>Nega kerak:</i> Minimal va maksimal to‘lov miqdorini cheklash.

📈 <b>/stats [SHOP_ID]</b>
• <i>Nega kerak:</i> Qancha pul tushgani va qabul qilingan to‘lovlar statistikasini ko‘rish uchun.

━━━━━━━━━━━━━━━━━━━━━
<b>📲 TIZIM VA TELEGRAM SOZLAMALARI:</b>

🔗 <b>/url [https://...]</b>
• <i>Nega kerak:</i> PayGate serveringiz internetdagi qaysi manzilda (domen yoki IP) joylashganini belgilash.

⚙️ <b>/telegram API_ID API_HASH</b>
• <i>Nega kerak:</i> Telegram karta botlaridan (@HumoCardBot, @CardXabarBot) keladigan xabarlarni o‘qish uchun zarur ruxsat.

📲 <b>/login</b>
• <i>Nega kerak:</i> Telefoningizdagi Telegram orqali QR-kodni skan qilib, karta xabarlarini avtomatik tutib oluvchi userbotni ishga tushirish.

💾 <b>/backup</b>
• <i>Nega kerak:</i> Barcha kartalar, loyihalar va to‘lovlar bazasini darhol Telegramga zaxira (backup) qilib yuborish va mahkamlash.

🔄 <b>/restore</b>
• <i>Nega kerak:</i> Mahkamlangan (pinned) zaxira xabaridan ma’lumotlar bazasini qayta tiklash.

❌ <b>/cancel</b>
• <i>Nega kerak:</i> Boshlangan har qanday amalni darhol bekor qilish."""


async def handle_connect(chat_id: str, shop_id: str = "") -> None:
    base_url = await db.get_base_url()
    project, projects = await get_target_project(shop_id)

    doc_path = Path(__file__).resolve().parent.parent / "INTEGRATION.md"
    if doc_path.exists():
        raw_text = doc_path.read_text(encoding="utf-8")
    else:
        raw_text = "# PayGate Loyihani Ulash Qo'llanmasi"

    if project:
        custom_md = (
            raw_text.replace("https://pay.example.uz", base_url)
            .replace("http://localhost:8000", base_url)
            .replace("your_shop_id", project["shop_id"])
            .replace("your_shop_key", project["shop_key"])
        )
        filename = f"PayGate_Ulanish_{project['shop_id']}.md"
        doc_bytes = custom_md.encode("utf-8")
        summary = (
            f"🔌 <b>Loyihani ulash ma’lumotlari:</b>\n\n"
            f"<i>Nega kerak: Saytingiz yoki botingiz PayGate orqali to‘lov qabul qilishi uchun quyidagi 3 ta parametrni kodingizga yoki .env faylingizga kiritasiz:</i>\n\n"
            f"• <b>PAYGATE_URL:</b> <code>{html.escape(base_url)}</code>\n"
            f"• <b>SHOP_ID:</b> <code>{html.escape(project['shop_id'])}</code>\n"
            f"• <b>SHOP_KEY:</b> <code>{html.escape(project['shop_key'])}</code>\n\n"
            f"📌 <b>To‘lov yaratish:</b> <code>POST {html.escape(base_url)}/payment/create</code>\n"
            f"📬 <b>Callback (Tasdiqlash):</b> <code>/callback {html.escape(project['shop_id'])} https://...</code>\n\n"
            f"📄 <i>Quyida barcha dasturlash tillari (Python, PHP, Node.js, aiogram, cURL) uchun to‘liq tayyor <b>{filename}</b> universal qo‘llanmasi yuborilmoqda:</i>"
        )
    else:
        filename = "PayGate_INTEGRATION.md"
        custom_md = raw_text.replace("https://pay.example.uz", base_url).replace(
            "http://localhost:8000", base_url
        )
        doc_bytes = custom_md.encode("utf-8")
        summary = (
            f"🔌 <b>Loyihani ulash bo‘yicha universal qo‘llanma:</b>\n\n"
            f"• <b>PAYGATE_URL:</b> <code>{html.escape(base_url)}</code>\n\n"
            f"ℹ️ Hozircha loyiha yaratilmagan. Yangi loyiha yaratish uchun: ➕ Loyiha yoki <code>/new nom</code> yuboring.\n\n"
            f"📄 <i>Barcha dasturlash tillari uchun tayyor universal <b>{filename}</b> qo‘llanmasi yuborilmoqda:</i>"
        )

    await send(chat_id, summary)
    try:
        await send_document(
            chat_id,
            filename,
            doc_bytes,
            caption=f"📘 PayGate integratsiya qo‘llanmasi ({filename})",
        )
    except Exception as e:
        log.exception("sendDocument xatosi: %s", e)
        await send(
            chat_id, f"⚠️ Fayl yuborishda xatolik yuz berdi: {html.escape(str(e))}"
        )


async def handle_callback(chat_id: str, data: str, message_id: int = 0) -> None:
    admin = await owner()
    if chat_id != admin:
        return

    parts = data.split(":", 1)
    action = parts[0]
    shop_id = parts[1] if len(parts) > 1 else ""

    if action == "del_cancel":
        await edit_message(chat_id, message_id, "❌ O‘chirish bekor qilindi.")
        return

    if action == "del_ask":
        project = await db.get_project(shop_id)
        if not project:
            await edit_message(chat_id, message_id, "❌ Loyiha topilmadi.")
            return
        confirm_markup = {
            "inline_keyboard": [
                [
                    {
                        "text": "🗑 Ha, o‘chirish",
                        "callback_data": f"del_confirm:{shop_id}",
                    },
                    {"text": "❌ Bekor qilish", "callback_data": "del_cancel"},
                ]
            ]
        }
        await edit_message(
            chat_id,
            message_id,
            f"⚠️ <b>Haqiqatan ham ushbu loyihani o‘chirmoqchimisiz?</b>\n\n"
            f"🏪 Nomi: <b>{html.escape(project['name'])}</b>\n"
            f"🆔 SHOP_ID: <code>{shop_id}</code>\n\n"
            f"<i>O‘chirilgandan so‘ng bu do‘kon orqali to‘lov qabul qilinmaydi.</i>",
            reply_markup=confirm_markup,
        )
        return

    if action == "del_confirm":
        project = await db.get_project(shop_id)
        name = project["name"] if project else shop_id
        await db.update_project(shop_id, "active", 0)
        await edit_message(
            chat_id,
            message_id,
            f"🗑 <b>{html.escape(name)}</b> loyihasi muvaffaqiyatli o‘chirildi.",
        )
        return

    if action == "show":
        project = await db.get_project(shop_id)
        if not project:
            await send(chat_id, "❌ Loyiha topilmadi.")
            return
        await send(
            chat_id,
            await project_text(project, show_key=True),
            inline_keyboard=project_markup(shop_id),
        )
        return

    if action == "connect":
        await handle_connect(chat_id, shop_id)
        return

    if action == "set_card":
        project = await db.get_project(shop_id)
        if not project:
            await send(chat_id, "❌ Loyiha topilmadi.")
            return
        _states[chat_id] = ("set_card", shop_id)
        await send(
            chat_id,
            f"💳 <b>{html.escape(project['name'])}</b> uchun to‘lov karta raqam(lar)ini yuboring (masalan: <code>8600123456789012</code>):\n\n"
            f"<i>Nega kerak: Xaridorlar pulni aynan shu kartaga o‘tkazadi. Bir nechta bo‘lsa vergul bilan yozing.</i>\n\n"
            f"<i>(Bekor qilish uchun: /cancel)</i>",
        )
        return

    if action == "set_holder":
        project = await db.get_project(shop_id)
        if not project:
            await send(chat_id, "❌ Loyiha topilmadi.")
            return
        _states[chat_id] = ("set_holder", shop_id)
        await send(
            chat_id,
            f"👤 <b>{html.escape(project['name'])}</b> uchun karta egasining ism-familiyasini yuboring (masalan: <i>ALISHER VALIYEV</i>):\n\n"
            f"<i>Nega kerak: Xaridor to‘lov qilayotganda to‘g‘ri odamga to‘layotganini ko‘rib ishonch hosil qilishi uchun.</i>\n\n"
            f"<i>(Bekor qilish uchun: /cancel)</i>",
        )
        return

    if action == "set_cb":
        project = await db.get_project(shop_id)
        if not project:
            await send(chat_id, "❌ Loyiha topilmadi.")
            return
        _states[chat_id] = ("set_callback", shop_id)
        await send(
            chat_id,
            f"🌐 <b>{html.escape(project['name'])}</b> uchun Callback URL manzilini yuboring (masalan: <code>https://sayt.uz/pay/callback</code>):\n\n"
            f"<i>Nega kerak: Kartaga pul tushishi bilan PayGate sizning saytingizga avtomatik 'To‘lov tushdi, xaridni ber!' deb bildirishnoma yuborishi uchun.</i>\n\n"
            f"<i>(Bekor qilish uchun: /cancel)</i>",
        )
        return


async def handle(chat_id: str, text: str, message_id: int = 0) -> None:
    admin = await owner()
    if not admin:
        if text != "/start":
            return
        await db.set_setting("admin_chat_id", chat_id)
        await send(
            chat_id,
            "✅ <b>Xush kelibsiz!</b> Siz PayGate egasi (administrator) sifatida muvaffaqiyatli biriktirildingiz.\n\n"
            + HELP,
        )
        return
    if chat_id != admin:
        return

    # Har qanday buyruq yoki pastki tugma bosilganda ochiq qolgan savol holatini darhol tozalash:
    # Bu /delete yoki boshqa komandani yangi loyiha deb saqlab qo'yish xatosini 100% oldini oladi!
    if text.startswith("/") or text in (
        "➕ Loyiha",
        "📂 Loyihalar",
        "🔌 Ulanish",
        "📊 Statistika",
        "ℹ️ Yordam",
    ):
        _states.pop(chat_id, None)

    # 1. Asosiy menyu tugmalari
    if text in ("/start", "/help", "ℹ️ Yordam"):
        await send(chat_id, HELP)
        return

    if text == "/cancel":
        _states.pop(chat_id, None)
        await send(chat_id, "❌ Amal bekor qilindi.")
        return

    if text == "➕ Loyiha":
        _states[chat_id] = ("new", "")
        await send(
            chat_id,
            "➕ <b>Yangi loyiha yaratish</b>\n\n"
            "<i>Nega kerak: Har bir veb-saytingiz yoki Telegram botingiz uchun alohida to‘lov nuqtasi ochish.</i>\n\n"
            "Iltimos, yangi loyiha nomini yuboring (masalan: <i>Mening Do‘konim</i>):\n"
            "<i>(Bekor qilish uchun: /cancel)</i>",
        )
        return

    if text in ("📂 Loyihalar", "/projects"):
        projects = await db.list_projects()
        if not projects:
            await send(
                chat_id,
                "📂 <b>Hozircha faol loyihalar mavjud emas.</b>\n\n"
                "<i>Nega kerak: Loyihalar orqali siz to‘lovlarni qabul qilasiz.</i>\n\n"
                "Yangi loyiha yaratish uchun pastdagi <b>➕ Loyiha</b> tugmasini bosing yoki <code>/new nom</code> yuboring.",
            )
            return

        await send(
            chat_id,
            f"📂 <b>Barcha faol loyihalaringiz ro‘yxati ({len(projects)} ta):</b>\n"
            f"<i>Har bir loyiha ostidagi tugmalar orqali sozlash, ulash yoki o‘chirish mumkin:</i>",
        )
        for p in projects:
            p_text = await project_text(p)
            await send(
                chat_id, p_text, inline_keyboard=project_markup(p["shop_id"])
            )
        return

    if text in ("🔌 Ulanish", "/connect", "/ulanish"):
        await handle_connect(chat_id)
        return

    if text in ("📊 Statistika", "/stats"):
        from . import watcher

        projects = await db.list_projects()
        connected = "🟢 Ulangan" if watcher.is_connected() else "🔴 Ulanmagan (/login)"
        total_stats = await db.stats()
        paid = total_stats.get("paid", {})
        count = paid.get("count", 0)
        total_sum = paid.get("sum", 0)

        header = (
            f"📊 <b>Umumiy statistika</b>\n\n"
            f"<i>Nega kerak: Tizimdagi barcha loyihalardan qancha pul tushganini va Telegram bot aloqasini bilish uchun.</i>\n\n"
            f"• Telegram userbot holati: <b>{connected}</b>\n"
            f"• Jami muvaffaqiyatli to‘lovlar: <b>{count} ta</b>\n"
            f"• Jami qabul qilingan summa: <b>{total_sum:,} so‘m</b>\n\n"
        ).replace(",", " ")
        if projects:
            body = "\n\n".join([await project_text(p) for p in projects])
            await send(chat_id, header + body)
        else:
            await send(chat_id, header + "Hozircha faol loyihalar yo‘q.")
        return

    # 2. Savol-javob (interactive state) holatlari
    state = _states.pop(chat_id, None)
    if state:
        st_type, st_arg = state
        if st_type == "2fa":
            await delete(chat_id, message_id)
            try:
                await _login_client.sign_in(password=text)
                await finish_login(chat_id)
            except Exception:
                _states[chat_id] = ("2fa", "")
                await send(
                    chat_id,
                    "❌ 2FA paroli noto‘g‘ri kiritildi. Qaytadan yuboring:",
                )
            return

        if st_type == "new":
            name = text.strip()[:60]
            shop_id = slug(name)
            project = {
                "shop_id": shop_id,
                "name": name,
                "shop_key": "sk_" + secrets.token_hex(24),
            }
            await db.create_project(project)
            p = await db.get_project(shop_id)
            await send(
                chat_id,
                f"✅ <b>'{html.escape(name)}' loyihasi yaratildi!</b>\n\n"
                + await project_text(p, show_key=True),
                inline_keyboard=project_markup(shop_id),
            )
            return

        if st_type == "set_card":
            await db.update_project(st_arg, "cards", text.strip())
            await send(
                chat_id,
                f"✅ Karta raqam(lar)i saqlandi: <code>{html.escape(text.strip())}</code>",
                inline_keyboard=project_markup(st_arg),
            )
            return

        if st_type == "set_holder":
            await db.update_project(st_arg, "card_holder", text.strip()[:100])
            await send(
                chat_id,
                f"✅ Karta egasi saqlandi: <b>{html.escape(text.strip())}</b>",
                inline_keyboard=project_markup(st_arg),
            )
            return

        if st_type == "set_callback":
            val = text.strip()
            if not val.startswith(("http://", "https://")):
                await send(
                    chat_id,
                    "⚠️ URL manzili http:// yoki https:// bilan boshlanishi shart. Qaytadan yuboring:",
                )
                _states[chat_id] = ("set_callback", st_arg)
                return
            await db.update_project(st_arg, "complete_url", val)
            await send(
                chat_id,
                f"✅ Callback URL saqlandi: <code>{html.escape(val)}</code>",
                inline_keyboard=project_markup(st_arg),
            )
            return

        if st_type == "set_ttl":
            try:
                ttl_val = max(30, int(text.strip()))
                await db.update_project(st_arg, "payment_ttl", ttl_val)
                await send(
                    chat_id,
                    f"✅ To‘lov muddati (TTL) {ttl_val} soniyaga saqlandi.",
                    inline_keyboard=project_markup(st_arg),
                )
            except ValueError:
                await send(
                    chat_id,
                    "⚠️ To‘lov muddati son bo‘lishi kerak (masalan: 300). Qaytadan yuboring:",
                )
                _states[chat_id] = ("set_ttl", st_arg)
            return

        if st_type == "set_limits":
            nums = text.strip().split()
            if len(nums) == 2 and nums[0].isdigit() and nums[1].isdigit():
                await db.update_project(st_arg, "min_amount", int(nums[0]))
                await db.update_project(st_arg, "max_amount", int(nums[1]))
                await send(
                    chat_id,
                    f"✅ Limitlar saqlandi: minimal {nums[0]} so‘m, maksimal {nums[1]} so‘m.",
                    inline_keyboard=project_markup(st_arg),
                )
            else:
                await send(
                    chat_id,
                    "⚠️ Format: <code>1000 50000000</code> ko‘rinishida 2 ta son yuboring:",
                )
                _states[chat_id] = ("set_limits", st_arg)
            return

    # 3. Buyruqlar parse qilish
    parts = text.split(maxsplit=2)
    command = parts[0].lower()

    if command == "/new":
        if len(parts) >= 2:
            name = text.split(maxsplit=1)[1][:60]
            shop_id = slug(name)
            project = {
                "shop_id": shop_id,
                "name": name,
                "shop_key": "sk_" + secrets.token_hex(24),
            }
            await db.create_project(project)
            await send(
                chat_id,
                f"✅ <b>'{html.escape(name)}' loyihasi yaratildi!</b>\n\n"
                + await project_text(await db.get_project(shop_id), True),
                inline_keyboard=project_markup(shop_id),
            )
        else:
            _states[chat_id] = ("new", "")
            await send(
                chat_id,
                "➕ <b>Yangi loyiha yaratish</b>\n\n"
                "<i>Nega kerak: Saytingiz yoki botingiz uchun alohida to‘lov nuqtasi ochish.</i>\n\n"
                "Loyiha nomini yuboring (masalan: <i>Mening Do‘konim</i>):\n"
                "<i>(Bekor qilish: /cancel)</i>",
            )
        return

    if command == "/url":
        if len(parts) == 1:
            base_url = await db.get_base_url()
            await send(
                chat_id,
                f"🔗 <b>Server manzili (BASE_URL):</b>\n\n"
                f"<i>Nega kerak: Loyihangiz PayGate serveriga murojaat qilishi uchun serveringizning internetdagi ochiq manzili.</i>\n\n"
                f"Joriy manzil: <code>{html.escape(base_url)}</code>\n\n"
                f"O‘zgartirish uchun quyidagicha yuboring:\n<code>/url https://pay.sizning-domeningiz.uz</code>",
            )
        else:
            new_url = parts[1].strip()
            if not new_url.startswith(("http://", "https://")):
                await send(
                    chat_id,
                    "⚠️ URL manzili http:// yoki https:// bilan boshlanishi kerak.",
                )
                return
            await db.set_setting("base_url", new_url.rstrip("/"))
            await send(
                chat_id,
                f"✅ Server manzili (BASE_URL) saqlandi:\n<code>{html.escape(new_url.rstrip('/'))}</code>",
            )
        return

    if command in ("/connect", "/ulanish"):
        shop_id = parts[1] if len(parts) >= 2 else ""
        await handle_connect(chat_id, shop_id)
        return

    if command == "/telegram":
        if len(parts) == 1:
            await send(
                chat_id,
                "⚙️ <b>Telegram API sozlamasi:</b>\n\n"
                "<i>Nega kerak: HumoCardBot va CardXabarBot'dan keladigan xabarlarni o‘qish uchun kerak.</i>\n\n"
                "Format: <code>/telegram API_ID API_HASH</code>\n\n"
                "💡 <i>(my.telegram.org saytidan 'API development tools' bo‘limidan olinadi)</i>",
            )
            return
        values = text.split()
        if len(values) != 3 or not values[1].isdigit():
            await send(
                chat_id,
                "⚠️ Noto‘g‘ri format. Masalan: <code>/telegram 12345678 abcdef0123456789</code>",
            )
            return
        await delete(chat_id, message_id)
        await db.set_setting("tg_api_id", values[1])
        await db.set_setting("tg_api_hash", values[2])
        await send(
            chat_id,
            "✅ Telegram API sozlamasi saqlandi. Endi karta botlarini kuzatish uchun <b>/login</b> yuboring.",
        )
        return

    if command == "/login":
        global _login_task
        if _login_task and not _login_task.done():
            await send(chat_id, "⏳ Login jarayoni allaqachon ishlamoqda.")
        else:
            _login_task = asyncio.create_task(qr_login(chat_id))
        return

    if command == "/backup":
        from . import backup
        await send(chat_id, "📦 <b>Zaxira olinmoqda va Telegramga yuklanmoqda...</b>")
        ok = await backup.send_backup(chat_id=chat_id, pin=True)
        if ok:
            await send(chat_id, "✅ <b>Muvaffaqiyatli!</b> Baza to‘liq zaxiralanib, xabar mahkamlandi (pinned).")
        else:
            await send(chat_id, "❌ Zaxira olishda xatolik yuz berdi yoki baza bo‘sh.")
        return

    if command == "/restore":
        from . import backup
        await send(chat_id, "🔄 <b>Mahkamlangan (pinned) xabardan baza tiklanmoqda...</b>")
        ok = await backup.restore_from_telegram(chat_id=chat_id)
        if ok:
            await send(chat_id, "✅ <b>Baza muvaffaqiyatli tiklandi!</b> Loyihalar ro‘yxatini ko‘rish uchun: 📂 Loyihalar")
        else:
            await send(chat_id, "❌ Mahkamlangan (pinned) zaxira xabari topilmadi yoki yuklab bo‘lmadi.")
        return

    # Loyihani aniqlash (bitta loyiha bo'lsa yoki SHOP_ID berilgan bo'lsa)
    target_shop_id = parts[1] if len(parts) >= 2 else ""
    project, all_projects = await get_target_project(target_shop_id)

    if command == "/show":
        if project:
            await send(
                chat_id,
                await project_text(project, True),
                inline_keyboard=project_markup(project["shop_id"]),
            )
        elif not all_projects:
            await send(
                chat_id,
                "Loyihalar mavjud emas. Yangi loyiha yaratish: ➕ Loyiha yoki <code>/new nom</code>",
            )
        else:
            await send(
                chat_id,
                "Qaysi loyihani ko‘rmoqchisiz? Tanlang:",
            )
            for p in all_projects:
                await send(
                    chat_id,
                    await project_text(p),
                    inline_keyboard=project_markup(p["shop_id"]),
                )
        return

    if command == "/card":
        if not all_projects:
            await send(
                chat_id,
                "Avval loyiha yarating: ➕ Loyiha yoki <code>/new nom</code>",
            )
            return
        if len(parts) == 1:
            if project:
                _states[chat_id] = ("set_card", project["shop_id"])
                await send(
                    chat_id,
                    f"💳 <b>To‘lov kartasini sozlash</b>\n\n"
                    f"<i>Nega kerak: Xaridorlar to‘lovni aynan qaysi kartaga o‘tkazishini belgilash uchun.</i>\n\n"
                    f"<b>{html.escape(project['name'])}</b> uchun karta raqam(lar)ini yuboring (masalan: <code>8600123456789012</code>):\n"
                    f"<i>(Bekor qilish: /cancel)</i>",
                )
            else:
                await send(
                    chat_id,
                    "Qaysi loyihaga karta kiritmoqchisiz? Tanlang:",
                )
                for p in all_projects:
                    await send(
                        chat_id,
                        f"🏪 <b>{p['name']}</b> (<code>{p['shop_id']}</code>)",
                        inline_keyboard=project_markup(p["shop_id"]),
                    )
            return
        if len(parts) == 2:
            if len(all_projects) == 1 and parts[1] != all_projects[0]["shop_id"]:
                await db.update_project(all_projects[0]["shop_id"], "cards", parts[1])
                await send(
                    chat_id,
                    f"✅ <b>{html.escape(all_projects[0]['name'])}</b> uchun karta saqlandi: <code>{parts[1]}</code>",
                    inline_keyboard=project_markup(all_projects[0]["shop_id"]),
                )
                return
            _states[chat_id] = ("set_card", parts[1])
            await send(
                chat_id,
                f"💳 <code>{parts[1]}</code> loyihasi uchun karta raqam(lar)ini yuboring:",
            )
            return
        proj = await db.get_project(parts[1])
        if not proj:
            await send(chat_id, f"❌ '{parts[1]}' identifikatorli loyiha topilmadi.")
            return
        await db.update_project(parts[1], "cards", parts[2])
        await send(
            chat_id,
            f"✅ <b>{html.escape(proj['name'])}</b> uchun karta saqlandi: <code>{parts[2]}</code>",
            inline_keyboard=project_markup(parts[1]),
        )
        return

    if command == "/holder":
        if not all_projects:
            await send(
                chat_id,
                "Avval loyiha yarating: ➕ Loyiha yoki <code>/new nom</code>",
            )
            return
        if len(parts) == 1:
            if project:
                _states[chat_id] = ("set_holder", project["shop_id"])
                await send(
                    chat_id,
                    f"👤 <b>Karta egasini sozlash</b>\n\n"
                    f"<i>Nega kerak: Xaridor to‘lov paytida to‘g‘ri kartaga pul o‘tkazayotganini tekshirishi uchun.</i>\n\n"
                    f"<b>{html.escape(project['name'])}</b> uchun karta egasining ism-familiyasini yuboring (masalan: <i>ALISHER VALIYEV</i>):\n"
                    f"<i>(Bekor qilish: /cancel)</i>",
                )
            else:
                await send(
                    chat_id,
                    "Qaysi loyihaga karta egasini kiritmoqchisiz? Tanlang:",
                )
                for p in all_projects:
                    await send(
                        chat_id,
                        f"🏪 <b>{p['name']}</b> (<code>{p['shop_id']}</code>)",
                        inline_keyboard=project_markup(p["shop_id"]),
                    )
            return
        if len(parts) >= 2:
            if len(all_projects) == 1 and parts[1] != all_projects[0]["shop_id"]:
                holder_name = text.split(maxsplit=1)[1][:100]
                await db.update_project(
                    all_projects[0]["shop_id"], "card_holder", holder_name
                )
                await send(
                    chat_id,
                    f"✅ <b>{html.escape(all_projects[0]['name'])}</b> uchun karta egasi saqlandi: <b>{html.escape(holder_name)}</b>",
                    inline_keyboard=project_markup(all_projects[0]["shop_id"]),
                )
                return
            if len(parts) >= 3:
                proj = await db.get_project(parts[1])
                if not proj:
                    await send(chat_id, f"❌ '{parts[1]}' loyihasi topilmadi.")
                    return
                await db.update_project(
                    parts[1], "card_holder", parts[2][:100]
                )
                await send(
                    chat_id,
                    f"✅ <b>{html.escape(proj['name'])}</b> uchun karta egasi saqlandi: <b>{html.escape(parts[2])}</b>",
                    inline_keyboard=project_markup(parts[1]),
                )
                return
            _states[chat_id] = ("set_holder", parts[1])
            await send(
                chat_id,
                f"👤 <code>{parts[1]}</code> uchun karta egasining ismini yuboring:",
            )
        return

    if command in ("/callback", "/prepare"):
        setting_key = "complete_url" if command == "/callback" else "prepare_url"
        cmd_name = "Callback" if command == "/callback" else "Prepare"
        if not all_projects:
            await send(
                chat_id,
                "Avval loyiha yarating: ➕ Loyiha yoki <code>/new nom</code>",
            )
            return
        if len(parts) == 1:
            if project:
                _states[chat_id] = (
                    "set_" + ("callback" if command == "/callback" else "prepare"),
                    project["shop_id"],
                )
                await send(
                    chat_id,
                    f"🌐 <b>{cmd_name} URL sozlash</b>\n\n"
                    f"<i>Nega kerak: Pul tushishi bilan PayGate sizning saytingizga avtomatik 'To‘lov tasdiqlandi' deb xabar yuborishi uchun.</i>\n\n"
                    f"<b>{html.escape(project['name'])}</b> uchun URL manzilini yuboring (masalan: <code>https://sayt.uz/pay/callback</code>):\n"
                    f"<i>(Bekor qilish: /cancel)</i>",
                )
            else:
                await send(
                    chat_id,
                    f"Qaysi loyihaga {cmd_name} URL kiritmoqchisiz? Tanlang:",
                )
                for p in all_projects:
                    await send(
                        chat_id,
                        f"🏪 <b>{p['name']}</b> (<code>{p['shop_id']}</code>)",
                        inline_keyboard=project_markup(p["shop_id"]),
                    )
            return
        if len(parts) == 2:
            if (
                parts[1].startswith(("http://", "https://"))
                and len(all_projects) == 1
            ):
                await db.update_project(
                    all_projects[0]["shop_id"], setting_key, parts[1]
                )
                await send(
                    chat_id,
                    f"✅ <b>{html.escape(all_projects[0]['name'])}</b> uchun {cmd_name} URL saqlandi: <code>{parts[1]}</code>",
                    inline_keyboard=project_markup(all_projects[0]["shop_id"]),
                )
                return
            _states[chat_id] = (
                "set_" + ("callback" if command == "/callback" else "prepare"),
                parts[1],
            )
            await send(
                chat_id,
                f"🌐 <code>{parts[1]}</code> uchun {cmd_name} URL manzilini yuboring (https://...):",
            )
            return
        if not parts[2].startswith(("http://", "https://")):
            await send(
                chat_id,
                "⚠️ URL manzili http:// yoki https:// bilan boshlanishi kerak.",
            )
            return
        proj = await db.get_project(parts[1])
        if not proj:
            await send(chat_id, f"❌ '{parts[1]}' loyihasi topilmadi.")
            return
        await db.update_project(parts[1], setting_key, parts[2])
        await send(
            chat_id,
            f"✅ <b>{html.escape(proj['name'])}</b> uchun {cmd_name} URL saqlandi: <code>{parts[2]}</code>",
            inline_keyboard=project_markup(parts[1]),
        )
        return

    if command == "/ttl":
        if not all_projects:
            await send(chat_id, "Avval loyiha yarating: ➕ Loyiha")
            return
        if len(parts) == 1:
            if project:
                _states[chat_id] = ("set_ttl", project["shop_id"])
                await send(
                    chat_id,
                    f"⏱ <b>To‘lov muddati (TTL) sozlash</b>\n\n"
                    f"<i>Nega kerak: Xaridor to‘lovni necha soniya ichida amalga oshirishi kerakligini belgilash uchun (masalan 300 = 5 daqiqa).</i>\n\n"
                    f"<b>{html.escape(project['name'])}</b> uchun to‘lov muddatini soniyalarda yuboring (masalan: <code>300</code>):",
                )
            else:
                await send(
                    chat_id,
                    "Format: <code>/ttl SHOP_ID 300</code>",
                )
            return
        if len(parts) == 2:
            if parts[1].isdigit() and len(all_projects) == 1:
                val = max(30, int(parts[1]))
                await db.update_project(
                    all_projects[0]["shop_id"], "payment_ttl", val
                )
                await send(
                    chat_id,
                    f"✅ <b>{html.escape(all_projects[0]['name'])}</b> uchun TTL {val} soniyaga saqlandi.",
                    inline_keyboard=project_markup(all_projects[0]["shop_id"]),
                )
                return
            _states[chat_id] = ("set_ttl", parts[1])
            await send(
                chat_id,
                f"⏱ <code>{parts[1]}</code> uchun to‘lov muddatini soniyalarda yuboring:",
            )
            return
        if parts[2].isdigit():
            val = max(30, int(parts[2]))
            await db.update_project(parts[1], "payment_ttl", val)
            await send(
                chat_id,
                f"✅ TTL {val} soniyaga saqlandi.",
                inline_keyboard=project_markup(parts[1]),
            )
        else:
            await send(
                chat_id,
                "⚠️ TTL musbat butun son bo‘lishi kerak (masalan: 300).",
            )
        return

    if command == "/limits":
        if not all_projects:
            await send(chat_id, "Avval loyiha yarating: ➕ Loyiha")
            return
        if len(parts) == 1:
            if project:
                _states[chat_id] = ("set_limits", project["shop_id"])
                await send(
                    chat_id,
                    f"📊 <b>To‘lov limitlari sozlash</b>\n\n"
                    f"<i>Nega kerak: Minimal va maksimal to‘lov summasini cheklash uchun.</i>\n\n"
                    f"<b>{html.escape(project['name'])}</b> uchun min va max summani yuboring (masalan: <code>1000 50000000</code>):",
                )
            else:
                await send(
                    chat_id,
                    "Format: <code>/limits SHOP_ID 1000 50000000</code>",
                )
            return
        vals = parts[1].split() if len(parts) == 2 else parts[2].split()
        target_id = (
            parts[1]
            if (len(parts) >= 3)
            else (
                all_projects[0]["shop_id"] if len(all_projects) == 1 else ""
            )
        )
        if (
            target_id
            and len(vals) == 2
            and vals[0].isdigit()
            and vals[1].isdigit()
        ):
            await db.update_project(target_id, "min_amount", int(vals[0]))
            await db.update_project(target_id, "max_amount", int(vals[1]))
            await send(
                chat_id,
                f"✅ Limitlar saqlandi: minimal {vals[0]} so‘m, maksimal {vals[1]} so‘m.",
                inline_keyboard=project_markup(target_id),
            )
        else:
            await send(
                chat_id,
                "⚠️ Format: <code>/limits SHOP_ID 1000 50000000</code>",
            )
        return

    if command == "/delete":
        if not all_projects:
            await send(chat_id, "Loyihalar mavjud emas.")
            return
        if len(parts) == 1:
            if project:
                confirm_markup = {
                    "inline_keyboard": [
                        [
                            {
                                "text": "🗑 Ha, o‘chirish",
                                "callback_data": f"del_confirm:{project['shop_id']}",
                            },
                            {
                                "text": "❌ Bekor qilish",
                                "callback_data": "del_cancel",
                            },
                        ]
                    ]
                }
                await send(
                    chat_id,
                    f"⚠️ <b>Haqiqatan ham ushbu loyihani o‘chirmoqchimisiz?</b>\n\n"
                    f"🏪 Nomi: <b>{html.escape(project['name'])}</b>\n"
                    f"🆔 SHOP_ID: <code>{project['shop_id']}</code>\n\n"
                    f"<i>O‘chirish uchun quyidagi tugmani bosing:</i>",
                    inline_keyboard=confirm_markup,
                )
            else:
                await send(chat_id, "Qaysi loyihani o‘chirmoqchisiz? Tanlang:")
                for p in all_projects:
                    confirm_markup = {
                        "inline_keyboard": [
                            [
                                {
                                    "text": f"🗑 {p['name']} ni o‘chirish",
                                    "callback_data": f"del_ask:{p['shop_id']}",
                                }
                            ]
                        ]
                    }
                    await send(
                        chat_id,
                        f"🏪 <b>{p['name']}</b> (<code>{p['shop_id']}</code>)",
                        inline_keyboard=confirm_markup,
                    )
            return
        proj = await db.get_project(parts[1])
        if not proj:
            await send(chat_id, f"❌ '{parts[1]}' loyihasi topilmadi.")
            return
        confirm_markup = {
            "inline_keyboard": [
                [
                    {
                        "text": "🗑 Ha, o‘chirish",
                        "callback_data": f"del_confirm:{parts[1]}",
                    },
                    {"text": "❌ Bekor qilish", "callback_data": "del_cancel"},
                ]
            ]
        }
        await send(
            chat_id,
            f"⚠️ <b>Haqiqatan ham '{html.escape(proj['name'])}' loyihasini o‘chirmoqchimisiz?</b>",
            inline_keyboard=confirm_markup,
        )
        return

    # Tushunarsiz buyruq bo'lsa
    await send(chat_id, HELP)


async def run() -> None:
    offset = 0
    while True:
        try:
            updates = await api(
                "getUpdates",
                offset=offset,
                timeout=25,
                allowed_updates='["message", "callback_query"]',
            )
            for update in updates or []:
                offset = update["update_id"] + 1

                # 1. Inline button bosilganda (callback_query)
                if "callback_query" in update:
                    cq = update["callback_query"]
                    cq_id = cq["id"]
                    msg = cq.get("message") or {}
                    chat_id = str(
                        (msg.get("chat") or {}).get("id", "")
                        or (cq.get("from") or {}).get("id", "")
                    )
                    data = cq.get("data", "")
                    msg_id = int(msg.get("message_id", 0))
                    try:
                        await api("answerCallbackQuery", callback_query_id=cq_id)
                    except Exception:
                        pass
                    if chat_id and data:
                        try:
                            await handle_callback(chat_id, data, msg_id)
                        except Exception:
                            log.exception("Callback query xatosi")
                    continue

                # 2. Xabar kelganda (message)
                message = update.get("message") or {}
                chat_id = str((message.get("chat") or {}).get("id", ""))
                doc = message.get("document")
                if doc and chat_id:
                    admin = await owner()
                    if admin and chat_id == admin:
                        fname = doc.get("file_name", "")
                        if fname.endswith(".db"):
                            file_id = doc.get("file_id")
                            finfo = await api("getFile", file_id=file_id)
                            if finfo and finfo.get("file_path"):
                                from . import backup
                                client = get_client()
                                url = f"https://api.telegram.org/file/bot{config.BOT_TOKEN}/{finfo['file_path']}"
                                r = await client.get(url)
                                if backup.restore_from_bytes(r.content):
                                    await send(
                                        chat_id,
                                        f"✅ <b>'{html.escape(fname)}' bazasi o‘rnatildi va tiklandi!</b>\n"
                                        f"Loyihalarni ko‘rish uchun: 📂 Loyihalar",
                                    )
                                else:
                                    await send(chat_id, "❌ Yuborilgan fayl SQLite formati emas yoki buzilgan.")
                            continue

                text = (message.get("text") or message.get("caption") or "").strip()
                if text and chat_id:
                    try:
                        await handle(
                            chat_id, text, int(message.get("message_id", 0))
                        )
                    except (ValueError, TypeError):
                        await send(chat_id, "Qiymat noto‘g‘ri formatda.")
                    except httpx.HTTPError as e:
                        log.warning("Tarmoq xatosi tufayli bot javob berolmadi: %s", e)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Admin bot polling xatosi")
            await asyncio.sleep(3)


def start() -> None:
    global _task
    _task = asyncio.create_task(run())


async def stop() -> None:
    global _task, _login_task, _login_client, _client
    if _task:
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
        _task = None
    if _login_task and not _login_task.done():
        _login_task.cancel()
    if _login_client and _login_client.is_connected():
        await _login_client.disconnect()
    if _client and not _client.is_closed:
        await _client.aclose()
        _client = None
