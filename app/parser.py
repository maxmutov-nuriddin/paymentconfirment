"""HUMO / UZCARD bot xabarlaridan kirim summasini ajratib olish.

Formatlar har xil bo'lishi mumkin, shuning uchun kalit so'zlar .env orqali sozlanadi:
  🎉 Пополнение ➕ 50.000,00 UZS  💳 HUMOCARD *1234  💰 Баланс: 1.250.000,00 UZS
  💸 To'ldirish ➕ 50 000.00 so'm  💳 ***1234
"""
import re

from . import config

AMOUNT_RE = re.compile(
    r"(?P<int>\d{1,3}(?:[  .,']\d{3})+|\d+)(?:[.,](?P<dec>\d{1,2}))?\s*"
    r"(?:UZS|uzs|so['‘’`ʻ]?m|сум|sum|сўм)",
    re.IGNORECASE,
)
LAST4_RE = re.compile(r"(?:\*{1,}|x{2,}|•+)\s?(\d{4})", re.IGNORECASE)


def _has(text: str, words: list[str]) -> bool:
    return any(w.lower() in text for w in words)


def is_credit(text: str) -> bool:
    low = text.lower()
    return _has(low, config.CREDIT_KEYWORDS) and not _has(low, config.DEBIT_KEYWORDS)


def extract_amount(text: str) -> int | None:
    """Balans qatorlarini tashlab, birinchi summani qaytaradi (so'mda, butun)."""
    for line in text.splitlines():
        if _has(line.lower(), config.BALANCE_KEYWORDS):
            continue
        m = AMOUNT_RE.search(line)
        if m:
            digits = re.sub(r"\D", "", m.group("int"))
            if digits:
                return int(digits)
    return None


def extract_last4(text: str) -> str | None:
    m = LAST4_RE.search(text)
    return m.group(1) if m else None


def parse(text: str) -> int | None:
    """Kirim bo'lsa summani, aks holda None qaytaradi."""
    if not text or not is_credit(text):
        return None
    if config.CARD_LAST4:
        last4 = extract_last4(text)
        if last4 and last4 not in config.CARD_LAST4:
            return None
    return extract_amount(text)
