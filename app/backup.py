"""PayGate SQLite bazasini Telegram orqali avtomatik zaxiralash va qayta tiklash."""
import asyncio
import datetime
import logging
import os
from pathlib import Path
import sqlite3

import httpx

from . import config

log = logging.getLogger("backup")
_client: httpx.AsyncClient | None = None
_backup_task: asyncio.Task | None = None
_periodic_task: asyncio.Task | None = None
_lock = asyncio.Lock()


def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=20.0),
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
        )
    return _client


async def telegram_api(method: str, files: dict | None = None, **data) -> dict | None:
    """Telegram Bot API bilan xavfsiz so'rov almashish."""
    if not config.BOT_TOKEN:
        return None
    url = f"https://api.telegram.org/bot{config.BOT_TOKEN}/{method}"
    client = get_client()
    for attempt in range(1, 4):
        try:
            if files:
                resp = await client.post(url, data=data, files=files)
            else:
                resp = await client.post(url, data=data)
            resp.raise_for_status()
            res = resp.json()
            if not res.get("ok"):
                log.warning("Telegram API (%s) xatosi: %s", method, res.get("description"))
                return None
            return res.get("result")
        except Exception as e:
            if attempt == 3:
                log.warning("Telegram API so'rov xatosi (%s): %s", method, e)
                return None
            await asyncio.sleep(1.0 * attempt)
    return None


def export_backup_bytes() -> bytes | None:
    """SQLite bazasidan xavfsiz hot-backup olib, baytlarini qaytaradi."""
    db_path = Path(config.DB_PATH)
    if not db_path.exists() or db_path.stat().st_size == 0:
        return None

    temp_path = db_path.with_name("paygate_temp_backup.db")
    try:
        with sqlite3.connect(db_path) as disk_db, sqlite3.connect(temp_path) as out_db:
            disk_db.backup(out_db)
        return temp_path.read_bytes()
    except Exception as e:
        log.warning("SQLite backup xatoligi: %s", e)
        return None
    finally:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except Exception:
                pass


async def get_target_chat(chat_id: str | None = None) -> str:
    if chat_id:
        return str(chat_id)
    try:
        from . import db
        owner_id = await db.get_setting("admin_chat_id")
        if owner_id:
            return str(owner_id)
    except Exception:
        pass
    return str(config.ADMIN_CHAT_ID or config.NOTIFY_CHAT_ID or "")


async def send_backup(chat_id: str | None = None, pin: bool = True) -> bool:
    """Baza nusxasini Telegram chatiga yuboradi va uni mahkamlab (pin) qo'yadi."""
    async with _lock:
        target_chat = await get_target_chat(chat_id)
        if not target_chat:
            log.info("Zaxirani yuborish uchun chat_id topilmadi.")
            return False

        backup_bytes = export_backup_bytes()
        if not backup_bytes:
            log.info("Baza bo'sh yoki topilmadi, zaxira yuborilmadi.")
            return False

        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        size_kb = len(backup_bytes) / 1024
        caption = (
            f"📦 <b>PayGate Baza Zaxirasi (Auto-Backup)</b>\n\n"
            f"🕒 <b>Vaqt:</b> <code>{now}</code>\n"
            f"💾 <b>Hajmi:</b> {size_kb:.1f} KB\n\n"
            f"📌 <i>Ushbu xabar mahkamlandi (pinned). Server qayta ishga tushganda yoki Render deploy bo‘lganda ma’lumotlar shu yerdan avtomatik tiklanadi.</i>"
        )

        files = {"document": ("paygate.db", backup_bytes, "application/x-sqlite3")}
        res = await telegram_api(
            "sendDocument",
            files=files,
            chat_id=target_chat,
            caption=caption,
            parse_mode="HTML",
        )

        if not res:
            return False

        msg_id = res.get("message_id")
        if pin and msg_id:
            try:
                await telegram_api(
                    "pinChatMessage",
                    chat_id=target_chat,
                    message_id=msg_id,
                    disable_notification=True,
                )
            except Exception as e:
                log.debug("Xabarni pin qilishda xatolik: %s", e)

        log.info("Baza zaxirasi muvaffaqiyatli Telegram'ga yuborildi (chat: %s)", target_chat)
        return True


def schedule_backup(delay: float = 3.0) -> None:
    """O'zgarish bo'lganda bir necha soniyadan so'ng zaxira olish (debounce)."""
    global _backup_task
    if _backup_task and not _backup_task.done():
        _backup_task.cancel()

    async def _delayed():
        try:
            await asyncio.sleep(delay)
            await send_backup()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.warning("Zaxira olishda xatolik: %s", e)

    try:
        loop = asyncio.get_running_loop()
        _backup_task = loop.create_task(_delayed())
    except RuntimeError:
        pass


def restore_from_bytes(data: bytes) -> bool:
    """Baytlardan SQLite bazasini tekshirib tiklaydi."""
    if not data or not data.startswith(b"SQLite format 3"):
        log.warning("Tiklanmoqchi bo'lgan ma'lumot haqiqiy SQLite fayli emas!")
        return False

    db_path = Path(config.DB_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # Avval vaqtinchalik tekshiruv
    temp_check = db_path.with_name("restore_check.db")
    try:
        temp_check.write_bytes(data)
        with sqlite3.connect(temp_check) as con:
            cur = con.cursor()
            cur.execute("PRAGMA integrity_check")
            row = cur.fetchone()
            if not row or row[0] != "ok":
                log.warning("SQLite integrity_check muvaffaqiyatsiz bo'ldi")
                return False
        # Agar to'g'ri bo'lsa, asosiy faylga yozamiz
        db_path.write_bytes(data)
        log.info("SQLite bazasi muvaffaqiyatli tiklandi: %d bayt", len(data))
        return True
    except Exception as e:
        log.exception("Bazani tiklashda xatolik: %s", e)
        return False
    finally:
        if temp_check.exists():
            try:
                temp_check.unlink()
            except Exception:
                pass


async def restore_from_telegram(chat_id: str | None = None) -> bool:
    """Telegram'dagi mahkamlangan (pinned) xabardan oxirgi paygate.db ni yuklab olib tiklaydi."""
    target_chat = await get_target_chat(chat_id)
    if not target_chat:
        log.info("Tiklash uchun chat_id topilmadi.")
        return False

    chat = await telegram_api("getChat", chat_id=target_chat)
    if not chat:
        log.warning("Chat ma'lumotlarini olib bo'lmadi (chat: %s)", target_chat)
        return False

    pinned = chat.get("pinned_message")
    if not pinned:
        log.info("Chatda mahkamlangan (pinned) xabar topilmadi.")
        return False

    doc = pinned.get("document")
    if not doc:
        log.info("Mahkamlangan xabarda fayl (document) yo'q.")
        return False

    file_name = doc.get("file_name", "")
    if not file_name.endswith(".db"):
        log.info("Mahkamlangan fayl .db kengaytmali emas: %s", file_name)
        return False

    file_id = doc.get("file_id")
    file_info = await telegram_api("getFile", file_id=file_id)
    if not file_info or not file_info.get("file_path"):
        log.warning("Fayl yo'lini (file_path) olib bo'lmadi.")
        return False

    file_path = file_info["file_path"]
    download_url = f"https://api.telegram.org/file/bot{config.BOT_TOKEN}/{file_path}"
    client = get_client()
    try:
        resp = await client.get(download_url)
        resp.raise_for_status()
        success = restore_from_bytes(resp.content)
        return success
    except Exception as e:
        log.exception("Faylni Telegram'dan yuklab olishda xatolik: %s", e)
        return False


async def try_auto_restore() -> bool:
    """Agar serverda baza bo'lmasa yoki bo'sh bo'lsa, Telegram'dan tiklashga urinadi."""
    db_path = Path(config.DB_PATH)
    # Agar fayl mavjud va ichida loyihalar bo'lsa, tiklash shart emas
    if db_path.exists() and db_path.stat().st_size > 0:
        try:
            with sqlite3.connect(db_path) as con:
                cur = con.cursor()
                cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='projects'")
                if cur.fetchone():
                    cur.execute("SELECT count(*) FROM projects")
                    cnt = cur.fetchone()[0]
                    if cnt > 0:
                        log.info("Mahalliy baza mavjud va loyihalar bor (%d ta), tiklash shart emas.", cnt)
                        return False
        except Exception:
            pass

    log.info("Mahalliy baza bo'sh yoki topilmadi. Telegram'dan avtomatik tiklashga urinilmoqda...")
    restored = await restore_from_telegram()
    if restored:
        log.info("✅ Baza Telegram'dan muvaffaqiyatli tiklandi!")
    else:
        log.info("Telegram'dan tiklanmadi (ehtimol birinchi marta ishga tushmoqda).")
    return restored


async def periodic_backup_loop() -> None:
    """Har 30 daqiqada bir marta bazani Telegram'ga zaxiralab turadi."""
    while True:
        try:
            await asyncio.sleep(1800)  # 30 daqiqa
            await send_backup()
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.warning("Davriy zaxira xatoligi: %s", e)


def start() -> None:
    global _periodic_task
    if _periodic_task is None or _periodic_task.done():
        _periodic_task = asyncio.create_task(periodic_backup_loop())


async def stop() -> None:
    global _periodic_task, _backup_task, _client
    if _backup_task:
        _backup_task.cancel()
    if _periodic_task:
        _periodic_task.cancel()
        try:
            await _periodic_task
        except asyncio.CancelledError:
            pass
    if _client and not _client.is_closed:
        await _client.aclose()

