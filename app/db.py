import os
import time
import aiosqlite

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
    shop_id       TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    shop_key      TEXT NOT NULL,
    cards         TEXT NOT NULL DEFAULT '',
    card_holder   TEXT NOT NULL DEFAULT '',
    prepare_url   TEXT NOT NULL DEFAULT '',
    complete_url  TEXT NOT NULL DEFAULT '',
    payment_ttl   INTEGER NOT NULL DEFAULT 300,
    min_amount    INTEGER NOT NULL DEFAULT 1000,
    max_amount    INTEGER NOT NULL DEFAULT 50000000,
    auto_unique   INTEGER NOT NULL DEFAULT 1,
    active        INTEGER NOT NULL DEFAULT 1,
    created_at    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS payments (
    payment_id   TEXT PRIMARY KEY,
    order_id     TEXT,
    amount       INTEGER NOT NULL,
    card         TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending',   -- pending | paid | cancel
    prepare_url  TEXT,
    complete_url TEXT,
    created_at   INTEGER NOT NULL,
    expire_at    INTEGER NOT NULL,
    paid_at      INTEGER,
    source_msg   TEXT,
    callback_ok  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_pay_status_amount ON payments(status, amount);

CREATE TABLE IF NOT EXISTS incoming (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chat        TEXT NOT NULL,
    msg_id      INTEGER NOT NULL,
    amount      INTEGER,
    last4       TEXT,
    text        TEXT,
    matched     TEXT,
    paired      INTEGER NOT NULL DEFAULT 0,   -- boshqa botdagi nusxasi bilan juftlangan
    received_at INTEGER NOT NULL,
    UNIQUE(chat, msg_id)
);
"""

_db: aiosqlite.Connection | None = None


async def init() -> None:
    global _db
    os.makedirs(os.path.dirname(config.DB_PATH) or ".", exist_ok=True)
    _db = await aiosqlite.connect(config.DB_PATH)
    _db.row_factory = aiosqlite.Row
    await _db.execute("PRAGMA journal_mode=WAL")
    await _db.executescript(SCHEMA)
    columns = {r["name"] for r in await (await _db.execute("PRAGMA table_info(payments)")).fetchall()}
    if "shop_id" not in columns:
        await _db.execute("ALTER TABLE payments ADD COLUMN shop_id TEXT NOT NULL DEFAULT ''")
    await _db.execute("CREATE INDEX IF NOT EXISTS idx_pay_shop_status ON payments(shop_id,status)")
    # Eski .env sozlamalarini bir marta bazaga xavfsiz ko'chirish.
    seeds = {
        "admin_chat_id": str(config.ADMIN_CHAT_ID or config.NOTIFY_CHAT_ID or ""),
        "tg_api_id": str(config.TG_API_ID or ""),
        "tg_api_hash": config.TG_API_HASH,
        "tg_session": config.TG_SESSION,
    }
    for key, value in seeds.items():
        if value:
            await _db.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (key, value))
    await _db.commit()


async def close() -> None:
    if _db:
        await _db.close()


def conn() -> aiosqlite.Connection:
    assert _db is not None, "DB ishga tushirilmagan"
    return _db


def now() -> int:
    return int(time.time())


def notify_changed() -> None:
    try:
        from . import backup
        backup.schedule_backup()
    except Exception:
        pass


async def get_payment(payment_id: str) -> dict | None:
    cur = await conn().execute("SELECT * FROM payments WHERE payment_id=?", (payment_id,))
    row = await cur.fetchone()
    return dict(row) if row else None


async def pending_amount_exists(amount: int) -> bool:
    cur = await conn().execute(
        "SELECT 1 FROM payments WHERE status='pending' AND amount=? AND expire_at>? LIMIT 1",
        (amount, now()),
    )
    return await cur.fetchone() is not None


async def insert_payment(p: dict) -> None:
    await conn().execute(
        """INSERT INTO payments (payment_id, order_id, amount, card, status, prepare_url,
           complete_url, created_at, expire_at, shop_id) VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (p["payment_id"], p["order_id"], p["amount"], p["card"], "pending",
         p["prepare_url"], p["complete_url"], p["created_at"], p["expire_at"],
         p["shop_id"]),
    )
    await conn().commit()


async def set_status(payment_id: str, status: str, source_msg: str | None = None) -> bool:
    """Faqat pending holatdagi to'lovni o'zgartiradi. True — o'zgardi."""
    cur = await conn().execute(
        "UPDATE payments SET status=?, paid_at=?, source_msg=COALESCE(?, source_msg) "
        "WHERE payment_id=? AND status='pending'",
        (status, now() if status == "paid" else None, source_msg, payment_id),
    )
    await conn().commit()
    if cur.rowcount > 0 and status == "paid":
        notify_changed()
    return cur.rowcount > 0


async def find_pending_by_amount(amount: int, msg_time: int) -> dict | None:
    cur = await conn().execute(
        "SELECT * FROM payments WHERE status='pending' AND amount=? AND expire_at>=? "
        "AND created_at<=? ORDER BY created_at ASC LIMIT 1",
        (amount, msg_time, msg_time + 5),
    )
    row = await cur.fetchone()
    return dict(row) if row else None


async def expired_pending() -> list[dict]:
    cur = await conn().execute(
        "SELECT * FROM payments WHERE status='pending' AND expire_at<?", (now(),)
    )
    return [dict(r) for r in await cur.fetchall()]


async def mark_callback_ok(payment_id: str) -> None:
    await conn().execute("UPDATE payments SET callback_ok=1 WHERE payment_id=?", (payment_id,))
    await conn().commit()


async def save_incoming(chat: str, msg_id: int, amount: int | None, last4: str | None,
                        text: str, msg_time: int) -> int | None:
    """Yangi xabar bo'lsa uning id sini, avval ko'rilgan bo'lsa None qaytaradi."""
    try:
        cur = await conn().execute(
            "INSERT INTO incoming (chat, msg_id, amount, last4, text, received_at) VALUES (?,?,?,?,?,?)",
            (chat, msg_id, amount, last4, text, msg_time),
        )
        await conn().commit()
        return cur.lastrowid
    except aiosqlite.IntegrityError:
        return None


async def pair_cross_bot_duplicate(row_id: int, chat: str, amount: int, last4: str | None,
                                   msg_time: int, window: int) -> bool:
    """Boshqa botdan xuddi shu summa (va karta) yaqin vaqtda kelgan va hali juftlanmagan xabar
    bo'lsa — bu bitta o'tkazmaning nusxasi. Ikkalasi juftlanadi, shuning uchun bitta xabar
    faqat bitta nusxani "yeydi", keyingi haqiqiy o'tkazmalar bloklanmaydi."""
    cur = await conn().execute(
        "SELECT id FROM incoming WHERE id<>? AND chat<>? AND amount=? AND paired=0 "
        "AND ABS(received_at-?)<=? AND (last4 IS NULL OR ? IS NULL OR last4=?) "
        "ORDER BY ABS(received_at-?) LIMIT 1",
        (row_id, chat, amount, msg_time, window, last4, last4, msg_time),
    )
    row = await cur.fetchone()
    if not row:
        return False
    await conn().execute("UPDATE incoming SET paired=1 WHERE id IN (?,?)", (row["id"], row_id))
    await conn().commit()
    return True


async def undelivered_callbacks(since: int) -> list[str]:
    cur = await conn().execute(
        "SELECT payment_id FROM payments WHERE status<>'pending' AND callback_ok=0 "
        "AND complete_url<>'' AND created_at>=?",
        (since,),
    )
    return [r["payment_id"] for r in await cur.fetchall()]


async def set_incoming_match(chat: str, msg_id: int, payment_id: str) -> None:
    await conn().execute(
        "UPDATE incoming SET matched=? WHERE chat=? AND msg_id=?", (payment_id, chat, msg_id)
    )
    await conn().commit()


async def stats(shop_id: str | None = None) -> dict:
    if shop_id:
        cur = await conn().execute(
            "SELECT status, COUNT(*) c, COALESCE(SUM(amount),0) s FROM payments "
            "WHERE shop_id=? GROUP BY status", (shop_id,)
        )
    else:
        cur = await conn().execute("SELECT status, COUNT(*) c, COALESCE(SUM(amount),0) s FROM payments GROUP BY status")
    return {r["status"]: {"count": r["c"], "sum": r["s"]} for r in await cur.fetchall()}


async def get_setting(key: str, default: str = "") -> str:
    row = await (await conn().execute("SELECT value FROM settings WHERE key=?", (key,))).fetchone()
    return row["value"] if row else default


async def set_setting(key: str, value: str) -> None:
    await conn().execute(
        "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    await conn().commit()
    notify_changed()


async def get_base_url() -> str:
    url = await get_setting("base_url")
    return url.rstrip("/") if url else config.BASE_URL.rstrip("/")


async def create_project(project: dict) -> None:
    await conn().execute(
        """INSERT INTO projects(shop_id,name,shop_key,cards,card_holder,prepare_url,
        complete_url,payment_ttl,min_amount,max_amount,auto_unique,created_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        (project["shop_id"], project["name"], project["shop_key"], project.get("cards", ""),
         project.get("card_holder", ""), project.get("prepare_url", ""),
         project.get("complete_url", ""), project.get("payment_ttl", 300),
         project.get("min_amount", 1000), project.get("max_amount", 50_000_000),
         int(project.get("auto_unique", True)), now()),
    )
    await conn().commit()
    notify_changed()


async def get_project(shop_id: str) -> dict | None:
    row = await (await conn().execute(
        "SELECT * FROM projects WHERE shop_id=? AND active=1", (shop_id,)
    )).fetchone()
    return dict(row) if row else None


async def authorize_project(shop_id: str, shop_key: str) -> dict | None:
    row = await (await conn().execute(
        "SELECT * FROM projects WHERE shop_id=? AND shop_key=? AND active=1", (shop_id, shop_key)
    )).fetchone()
    return dict(row) if row else None


async def list_projects() -> list[dict]:
    rows = await (await conn().execute(
        "SELECT * FROM projects WHERE active=1 ORDER BY created_at DESC"
    )).fetchall()
    return [dict(r) for r in rows]


async def update_project(shop_id: str, field: str, value) -> bool:
    allowed = {"name", "cards", "card_holder", "prepare_url", "complete_url",
               "payment_ttl", "min_amount", "max_amount", "auto_unique", "active"}
    if field not in allowed:
        raise ValueError("unsupported project field")
    cur = await conn().execute(f"UPDATE projects SET {field}=? WHERE shop_id=?", (value, shop_id))
    await conn().commit()
    if cur.rowcount > 0:
        notify_changed()
    return cur.rowcount > 0
