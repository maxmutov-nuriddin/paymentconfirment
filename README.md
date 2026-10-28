# PayGate — Multi-Loyiha To'lov Shlyuzi

HUMO va UZCARD kirimlarini Telegram karta botlari (@HumoCardBot, @CardXabarBot) orqali avtomatik aniqlab, bir nechta loyihaning to'lovlarini bitta serverda markazlashgan holda boshqaradi.

Barcha sozlamalar xavfsiz holda **Telegram boshqaruv boti** orqali amalga oshiriladi. Kodda yoki muhitda hech qanday ochiq kalitlar, karta raqamlari yoki parollar saqlanmaydi.

---

## 🚀 Tezkor Boshlash

### 1. `.env` fayli
`.env` faylida faqat **bitta** o'zgaruvchi beriladi:
```env
BOT_TOKEN=123456789:AA...real_bot_token
```

### 2. O'rnatish va ishga tushirish
```bash
pip install -r requirements.txt
python run.py
```

### 3. Boshqaruvni biriktirish
Telegram'da botingizga `/start` yuboring. Birinchi `/start` yuborgan foydalanuvchi PayGate egasi (admin) sifatida biriktiriladi. Boshqa hech kim botdan foydalana olmaydi.

---

## 🤖 Telegram Boshqaruv Buyruqlari

```text
Loyihalar:
/new nom                         yangi loyiha yaratish
/projects                        barcha loyihalar ro'yxati
/show SHOP_ID                    loyiha ma'lumotlari va maxfiy SHOP_KEY
/connect [SHOP_ID]               loyihani ulash bo'yicha to'liq qo'llanma (.md fayl)
/delete SHOP_ID                  loyihani o'chirish

Loyiha sozlamalari:
/card SHOP_ID 8600...            to'lov kartasi (bir nechta bo'lsa vergul bilan)
/holder SHOP_ID ISM FAMILIYA     karta egasining ismi
/callback SHOP_ID https://...    to'lov tasdiqlanganda callback URL
/prepare SHOP_ID https://...     to'lov yaratilgandagi prepare URL
/ttl SHOP_ID 300                 to'lov kutish muddati (soniya)
/limits SHOP_ID 1000 50000000    min/max summa limitlari
/stats [SHOP_ID]                 to'lovlar statistikasi

Tizim va Telegram:
/url https://pay.misol.uz        PayGate server manzili (BASE_URL)
/telegram API_ID API_HASH        my.telegram.org sozlamalari
/login                           QR-kod orqali userbotga ulanish
/cancel                          amalni bekor qilish
```

---

## 🔌 Loyihani PayGate'ga Ulash

Har qanday loyihani (Telegram bot, sayt, mobil ilova, CRM) ulash uchun:
1. Botda `/connect` yoki `🔌 Ulanish` tugmasini bosing (yoki `/connect SHOP_ID`).
2. Bot sizga loyihangiz ma'lumotlarini ko'rsatadi va to'liq **`INTEGRATION.md`** universal qo'llanma faylini yuboradi.

### Kerak bo'ladigan 3 ta parametr:
- `PAYGATE_URL` — Server manzili
- `SHOP_ID` — Do'kon identifikatori
- `SHOP_KEY` — Maxfiy kalit

### Qisqa API xulosasi:
- **To'lov yaratish:** `POST /payment/create` (params: `shop_id`, `shop_key`, `amount`, `order_id`)
- **Tasdiqlash (Callback):** PayGate sizning `complete_url` manzilingizga `POST` so'rov yuboradi. Imzo: `md5(shop_id + payment_id + amount + shop_key)`.
- **Holat tekshirish:** `GET /payment/status?shop_id=...&shop_key=...&payment_id=...`

Batafsil ma'lumot va barcha tillar (Python, PHP, Node.js, cURL) uchun namunalar: [`INTEGRATION.md`](file:///Users/nuriddin/Desktop/paymentconfirment/INTEGRATION.md).

---

## 🔒 Xavfsizlik

- **Minimal sozlama:** `.env` da faqat bot tokeni saqlanadi.
- **Koddagi tozalik:** Hech qanday maxfiy kalitlar yoki karta raqamlari kod ichida ochiq holda saqlanmaydi.
- **Baza himoyasi:** Barcha loyihalar, kalitlar va sessiyalar faqat serverdagi SQLite bazasida (`data/paygate.db`) saqlanadi.
- **Bir martalik admin:** Faqat birinchi ro'yxatdan o'tgan Telegram akkaunt tizimni boshqara oladi.
