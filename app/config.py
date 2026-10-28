"""Barcha sozlamalar faqat .env / environment orqali o'qiladi."""
import os
from dotenv import load_dotenv

load_dotenv()


def _str(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _list(name: str, default: str = "") -> list[str]:
    return [x.strip() for x in os.getenv(name, default).split(",") if x.strip()]


# --- Umumiy ---
APP_NAME = _str("APP_NAME", "PayGate")
BASE_URL = _str("BASE_URL", "http://localhost:8000")
HOST = _str("HOST", "0.0.0.0")
PORT = _int("PORT", 8000)
DB_PATH = _str("DB_PATH", "data/paygate.db")
DEBUG = _bool("DEBUG", False)
TIMEZONE_OFFSET = _int("TIMEZONE_OFFSET", 5)  # faqat ko'rsatish uchun

# --- Do'kon (bitta egasi, rollar yo'q) ---
SHOP_ID = _str("SHOP_ID")
SHOP_KEY = _str("SHOP_KEY")

# --- Karta(lar) mijozga ko'rsatiladi ---
CARD_NUMBERS = _list("CARD_NUMBERS")  # 8600..., 9860... (vergul bilan)
CARD_HOLDER = _str("CARD_HOLDER")

# --- To'lov qoidalari ---
PAYMENT_TTL = _int("PAYMENT_TTL", 300)  # soniya
MIN_AMOUNT = _int("MIN_AMOUNT", 1000)
MAX_AMOUNT = _int("MAX_AMOUNT", 50_000_000)
# Bir xil summada ochiq to'lov bo'lsa: false -> xato, true -> summani +1, +2 ... so'mga oshiradi
AUTO_UNIQUE_AMOUNT = _bool("AUTO_UNIQUE_AMOUNT", False)
AUTO_UNIQUE_MAX_STEP = _int("AUTO_UNIQUE_MAX_STEP", 50)

# --- Callbacklar ---
PREPARE_URL = _str("PREPARE_URL")
COMPLETE_URL = _str("COMPLETE_URL")
ALLOW_URL_OVERRIDE = _bool("ALLOW_URL_OVERRIDE", True)  # so'rovda prepare_url/complete_url berish
CALLBACK_RETRIES = _int("CALLBACK_RETRIES", 5)
CALLBACK_TIMEOUT = _int("CALLBACK_TIMEOUT", 10)

# --- Telegram userbot (karta xabarlarini o'qish) ---
TG_API_ID = _int("TG_API_ID", 0)
TG_API_HASH = _str("TG_API_HASH")
TG_SESSION = _str("TG_SESSION")  # StringSession (python -m app.login orqali olinadi)
CARD_BOTS = _list("CARD_BOTS", "HumoCardBot,CardXabarBot")
CARD_LAST4 = _list("CARD_LAST4")  # bo'sh bo'lsa barcha kartalar qabul qilinadi
# Bir xil summa ikki XIL botdan shu soniya ichida kelsa — bitta o'tkazmaning nusxasi deb hisoblanadi
DUPLICATE_WINDOW = _int("DUPLICATE_WINDOW", 120)

CREDIT_KEYWORDS = _list(
    "CREDIT_KEYWORDS",
    "пополнение,поступление,зачисление,перевод на карту,to'ldirish,to‘ldirish,"
    "toldirish,kirim,tushum,o'tkazma qabul,popolnenie,credit,➕,🎉",
)
DEBIT_KEYWORDS = _list(
    "DEBIT_KEYWORDS",
    "списание,оплата,снятие,покупка,перевод с карты,yechib,xarid,chiqim,➖,debit",
)
BALANCE_KEYWORDS = _list(
    "BALANCE_KEYWORDS", "баланс,остаток,balans,qoldiq,balance,доступно"
)

# --- Ixtiyoriy: egasiga Telegram bot orqali bildirishnoma ---
NOTIFY_BOT_TOKEN = _str("NOTIFY_BOT_TOKEN")
NOTIFY_CHAT_ID = _str("NOTIFY_CHAT_ID")
ADMIN_CHAT_ID = _str("ADMIN_CHAT_ID") or NOTIFY_CHAT_ID
BOT_TOKEN = _str("BOT_TOKEN") or NOTIFY_BOT_TOKEN
# TG_SESSION bo'sh bo'lsa, egasining bot chatida bir martalik login boshlanadi
BOT_LOGIN_ENABLED = _bool("BOT_LOGIN_ENABLED", True)

# --- Landing sahifadagi havolalar ---
SUPPORT_URL = _str("SUPPORT_URL", "https://t.me/")
CHANNEL_URL = _str("CHANNEL_URL", "https://t.me/")


def validate() -> list[str]:
    """Ishga tushishdan oldin muhim sozlamalarni tekshiradi."""
    errors = []
    if not BOT_TOKEN:
        errors.append("BOT_TOKEN berilishi shart")
    return errors


def telegram_enabled() -> bool:
    return bool(TG_API_ID and TG_API_HASH and TG_SESSION)
