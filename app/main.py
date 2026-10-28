import asyncio
import logging
import secrets
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from . import admin_bot, callbacks, config, db, matcher, parser, watcher

logging.basicConfig(
    level=logging.DEBUG if config.DEBUG else logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
logging.getLogger("telethon").setLevel(logging.WARNING)
for _name in ("httpx", "httpcore", "aiosqlite"):
    logging.getLogger(_name).setLevel(logging.WARNING)
log = logging.getLogger("app")

_create_lock = asyncio.Lock()
_card_indexes: dict[str, int] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    errors = config.validate()
    if errors:
        for e in errors:
            log.error("Sozlama xatosi: %s", e)
        sys.exit(1)
    await db.init()
    await matcher.resend_undelivered()
    await watcher.start()
    admin_bot.start()
    expiry = asyncio.create_task(matcher.expiry_loop())
    log.info("%s ishga tushdi: %s", config.APP_NAME, config.BASE_URL)
    yield
    expiry.cancel()
    await admin_bot.stop()
    await watcher.stop()
    await db.close()


app = FastAPI(title=config.APP_NAME, lifespan=lifespan, docs_url="/swagger", redoc_url=None)


# ---------- yordamchilar ----------

def err(msg: str, code: int = 400) -> JSONResponse:
    return JSONResponse({"error": msg}, status_code=code)


async def params(request: Request) -> dict:
    """JSON, form-data, x-www-form-urlencoded va query parametrlarni birlashtiradi."""
    data = dict(request.query_params)
    ctype = request.headers.get("content-type", "")
    try:
        if "application/json" in ctype:
            body = await request.json()
            if isinstance(body, dict):
                data.update(body)
        elif "form" in ctype:
            data.update(dict(await request.form()))
    except Exception:
        pass
    return {k: (str(v).strip() if v is not None else "") for k, v in data.items()}


async def authorized(p: dict) -> dict | None:
    return await db.authorize_project(p.get("shop_id", ""), p.get("shop_key", ""))


def public(p: dict, project: dict | None = None) -> dict:
    return {
        "payment_id": p["payment_id"],
        "order_id": p["order_id"] or "",
        "amount": p["amount"],
        "card": p["card"],
        "card_holder": (project or {}).get("card_holder", ""),
        "status": p["status"],
        "created_at": p["created_at"],
        "expire_at": p["expire_at"],
        "paid_at": p["paid_at"],
    }


# ---------- API ----------

@app.post("/payment/create")
async def payment_create(request: Request):
    p = await params(request)
    project = await authorized(p)
    if not project:
        return err("Do'kon topilmadi!", 401)
    try:
        amount = int(float(p.get("amount", "")))
    except ValueError:
        return err("amount noto'g'ri")
    if not project["min_amount"] <= amount <= project["max_amount"]:
        return err(f"amount {project['min_amount']} — {project['max_amount']} oralig'ida bo'lishi kerak")

    prepare_url, complete_url = project["prepare_url"], project["complete_url"]
    if config.ALLOW_URL_OVERRIDE:
        prepare_url = p.get("prepare_url") or prepare_url
        complete_url = p.get("complete_url") or complete_url

    async with _create_lock:
        if await db.pending_amount_exists(amount):
            if not project["auto_unique"]:
                return err("Bu summada ochiq to'lov mavjud. Summani 1-2 so'mga o'zgartiring.")
            for step in range(1, config.AUTO_UNIQUE_MAX_STEP + 1):
                if not await db.pending_amount_exists(amount + step):
                    amount += step
                    break
            else:
                return err("Bo'sh summa topilmadi, keyinroq urinib ko'ring.")

        cards = [x.strip() for x in project["cards"].split(",") if x.strip()]
        if not cards:
            return err("Loyiha kartasi bot orqali sozlanmagan", 409)
        index = _card_indexes.get(project["shop_id"], 0)
        card = cards[index % len(cards)]
        _card_indexes[project["shop_id"]] = index + 1
        now = db.now()
        pay = {
            "payment_id": secrets.token_hex(6),
            "order_id": p.get("order_id", "")[:64],
            "amount": amount,
            "card": card,
            "prepare_url": prepare_url,
            "complete_url": complete_url,
            "created_at": now,
            "expire_at": now + project["payment_ttl"],
            "shop_id": project["shop_id"],
        }
        await db.insert_payment(pay)

    callbacks.fire(callbacks.send_prepare(pay))
    return {
        "payment_id": pay["payment_id"],
        "order_id": pay["order_id"],
        "amount": amount,
        "card": pay["card"],
        "card_holder": project["card_holder"],
        "expires_in": project["payment_ttl"],
        "expire_at": pay["expire_at"],
        "message": "To'lov muvaffaqiyatli yaratildi!",
    }


@app.api_route("/payment/status", methods=["GET", "POST"])
async def payment_status(request: Request):
    p = await params(request)
    project = await authorized(p)
    if not project:
        return err("Do'kon topilmadi!", 401)
    pay = await db.get_payment(p.get("payment_id", ""))
    if not pay or pay.get("shop_id") != project["shop_id"]:
        return err("To'lov topilmadi!", 404)
    return public(pay, project)


@app.post("/payment/cancel")
async def payment_cancel(request: Request):
    p = await params(request)
    project = await authorized(p)
    if not project:
        return err("Do'kon topilmadi!", 401)
    pay = await db.get_payment(p.get("payment_id", ""))
    if not pay or pay.get("shop_id") != project["shop_id"]:
        return err("To'lov topilmadi!", 404)
    if not await db.set_status(pay["payment_id"], "cancel"):
        return err(f"To'lov allaqachon yakunlangan: {pay['status']}")
    callbacks.fire(callbacks.send_complete(pay["payment_id"]))
    return {"payment_id": pay["payment_id"], "status": "cancel", "message": "To'lov bekor qilindi"}


@app.api_route("/stats", methods=["GET", "POST"])
async def stats(request: Request):
    p = await params(request)
    project = await authorized(p)
    if not project:
        return err("Do'kon topilmadi!", 401)
    return {"telegram": watcher.is_connected(), "payments": await db.stats(project["shop_id"])}


@app.post("/debug/incoming")
async def debug_incoming(request: Request):
    """Faqat DEBUG=true bo'lganda: karta xabarini simulyatsiya qilish."""
    if not config.DEBUG:
        return err("Not found", 404)
    p = await params(request)
    if not await authorized(p):
        return err("Do'kon topilmadi!", 401)
    text = p.get("text", "")
    chat = p.get("chat") or "debug"
    msg_id = int(p.get("msg_id") or secrets.randbelow(10**9))
    matched = await matcher.handle_incoming(chat, msg_id, text, db.now())
    return {"parsed_amount": parser.parse(text), "matched_payment_id": matched}


@app.get("/health")
async def health():
    return {"ok": True, "telegram": watcher.is_connected()}


# ---------- Landing + docs ----------

_LANDING = (Path(__file__).parent / "templates" / "index.html").read_text(encoding="utf-8")


@app.get("/", response_class=HTMLResponse)
async def landing():
    base_url = await db.get_base_url()
    html = _LANDING
    for key, val in {
        "APP_NAME": config.APP_NAME,
        "BASE_URL": base_url,
        "PAYMENT_TTL_MIN": str(max(1, config.PAYMENT_TTL // 60)),
        "SUPPORT_URL": config.SUPPORT_URL,
        "CHANNEL_URL": config.CHANNEL_URL,
    }.items():
        html = html.replace("{{" + key + "}}", val)
    return html
