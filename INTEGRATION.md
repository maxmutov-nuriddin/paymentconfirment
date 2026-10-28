# 🔌 PayGate — Loyihani Ulash Bo'yicha Universal Qo'llanma

Ushbu qo'llanma orqali har qanday loyihani (**Telegram botlar, Veb-saytlar, Mobil ilovalar, CRM va barcha backend tizimlari**) PayGate to'lov shlyuziga bir necha daqiqada osongina ulashingiz mumkin.

---

## 📌 1. Ulanish uchun nimalar kerak?

Loyihangizdan to'lov qabul qilish uchun sizga faqat **3 ta ma'lumot** kerak:

| Parametr | Tavsif | Qayerdan olinadi? |
| :--- | :--- | :--- |
| `PAYGATE_URL` | PayGate server manzili (masalan: `https://pay.example.uz` yoki `http://localhost:8000`) | Boshqaruv botida `/url` buyrug'i |
| `SHOP_ID` | Loyiha (do'kon) identifikatori | Boshqaruv botida `/projects` yoki `/new` |
| `SHOP_KEY` | Loyihaning maxfiy kaliti (begonalarga ko'rsatmang) | Boshqaruv botida `/show SHOP_ID` |

---

## 🔄 2. To'lov Qanday Ishlaydi? (Oddiy 4 qadam)

```
[Mijoz] ──── 1. "To'lash" tugmasini bosadi ────> [Sizning Loyihangiz]
                                                           │
                                             2. POST /payment/create
                                                           │
[Mijoz] <─── 3. Karta raqami va summa beriladi ◄───────────┘
   │
   └──────── 4. Karta orqali to'laydi (Payme / Click / Humo)
                               │
                       [PayGate Server]
                               │
             5. POST complete_url (Avtomatik tasdiqlash)
                               ▼
                    [Sizning Loyihangiz] ──> [Mijozga xizmat / balans beriladi]
```

1. **Sizning loyihangiz** PayGate'ga `POST /payment/create` so'rovi yuboradi (summa va buyurtma ID si bilan).
2. **PayGate** to'lov uchun karta raqami, karta egasi ismi, to'lanishi kerak bo'lgan aniq summa va to'lov vaqtini qaytaradi.
3. **Mijoz** ushbu kartaga ko'rsatilgan summani o'tkazadi.
4. **PayGate** tushgan pulni SMS / Telegram xabaridan avtomatik aniqlaydi va sizning serveringizga (`complete_url`) tasdiqlash xabarini yuboradi.

---

## 🚀 3. API Qo'llanmasi

### A) To'lov yaratish: `POST /payment/create`

To'lov yaratish uchun PayGate serveriga `POST` so'rov yuborasiz (`application/json` yoki `application/x-www-form-urlencoded`).

#### Yuboriladigan parametrlar:
- `shop_id` *(string, majburiy)* — Do'kon identifikatori (masalan: `shop_1234`)
- `shop_key` *(string, majburiy)* — Do'kon maxfiy kaliti (masalan: `sk_abc123...`)
- `amount` *(integer, majburiy)* — To'lov summasi (so'mda, masalan: `50000`)
- `order_id` *(string, ixtiyoriy)* — Sizning tizimingizdagi buyurtma yoki foydalanuvchi ID si (masalan: `ORD_987` yoki `USER_123456`)
- `complete_url` *(string, ixtiyoriy)* — Shu to'lov uchun maxsus callback URL (agar loyiha umumiy callback'idan boshqa manzil kerak bo'lsa)

#### Qaytariladigan javob (JSON):
```json
{
  "payment_id": "a9c2e4f107bd",
  "order_id": "ORD_987",
  "amount": 50000,
  "card": "8600123456789012",
  "card_holder": "FALONCHI PISTONCHIYEV",
  "expires_in": 300,
  "expire_at": 1740000300,
  "message": "To'lov muvaffaqiyatli yaratildi!"
}
```

> **Mijozga nima ko'rsatiladi?**
> Mijozga `card` (karta raqami), `card_holder` (karta egasi) va `amount` (aynan shu summani to'lash kerakligi) ko'rsatiladi.

---

### B) To'lov tasdiqlanganda Callback qabul qilish (`complete_url`)

To'lov amalga oshirilganda, PayGate avtomatik tarzda siz ko'rsatgan `complete_url` manziliga `POST` so'rov yuboradi.  
*(Callback manzilini botda `/callback SHOP_ID https://sizning-sayt.uz/api/payment/callback` buyrug'i bilan o'rnatasiz).*

#### PayGate yuboradigan parametrlar (POST form-data):
- `shop_id` — Do'kon ID si
- `payment_id` — PayGate to'lov ID si
- `order_id` — Siz yaratishda yuborgan buyurtma ID si
- `amount` — To'langan summa
- `status` — `"paid"` (muvaffaqiyatli to'landi) yoki `"cancel"` (muddati tugab bekor qilindi)
- `card` — Pul qabul qilingan karta
- `paid_at` — To'langan vaqt (timestamp)
- `sign` — Xavfsizlik nazorat imzosi (MD5)

#### 🔐 Imzoni tekshirish (Sign tekshirish):
Xavfsizlik uchun yuborilgan ma'lumotlar soxta emasligini tekshirish lozim. Formula:
```
sign = md5(shop_id + payment_id + amount + shop_key)
```
1. Agar hisoblangan MD5 imzo kelgan `sign` bilan bir xil bo'lsa:
2. Va `status == 'paid'` bo'lsa:
   - Foydalanuvchiga obunani, mahsulotni yoki balansni berasiz.
   - Javob sifatida oddiy HTTP 200 `OK` qaytarasiz.

---

### C) To'lov holatini qo'lda tekshirish: `GET /payment/status`

Agar webhook kutmasdan to'lov holatini tekshirmoqchi bo'lsangiz:
```http
GET {PAYGATE_URL}/payment/status?shop_id={SHOP_ID}&shop_key={SHOP_KEY}&payment_id={PAYMENT_ID}
```

#### Javob:
```json
{
  "payment_id": "a9c2e4f107bd",
  "order_id": "ORD_987",
  "amount": 50000,
  "card": "8600123456789012",
  "card_holder": "FALONCHI PISTONCHIYEV",
  "status": "paid",
  "created_at": 1740000000,
  "expire_at": 1740000300,
  "paid_at": 1740000120
}
```
`status` qiymatlari:
- `pending` — to'lov kutilmoqda
- `paid` — to'lov muvaffaqiyatli qabul qilingan
- `cancel` — vaqti tugagan yoki bekor qilingan

---

## 💻 4. Tayyor Kod Namunalari

Quyidagi kodlarni loyihangizga to'g'ridan-to'g'ri nusxalab ishlatishingiz mumkin:

### 🐍 1. Python (Telegram Bot — Aiogram 3)

```python
import hashlib
import os
import httpx
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message
from aiohttp import web

PAYGATE_URL = os.getenv("PAYGATE_URL", "https://pay.example.uz")
SHOP_ID = os.getenv("SHOP_ID", "your_shop_id")
SHOP_KEY = os.getenv("SHOP_KEY", "your_shop_key")
BOT_TOKEN = os.getenv("BOT_TOKEN", "123456:ABC-DEF")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
orders_db = {}  # Real loyihada PostgreSQL yoki SQLite ishlatiladi

# 1. Foydalanuvchi /pay buyrug'ini yuborganda to'lov yaratish
@dp.message(Command("pay"))
async def cmd_pay(message: Message):
    order_id = f"USER_{message.from_user.id}_{message.message_id}"
    amount = 25000  # 25 000 so'm

    async with httpx.AsyncClient(timeout=10) as client:
        res = await client.post(f"{PAYGATE_URL}/payment/create", data={
            "shop_id": SHOP_ID,
            "shop_key": SHOP_KEY,
            "amount": amount,
            "order_id": order_id,
        })
        data = res.json()

    if "error" in data:
        return await message.answer(f"❌ Xatolik: {data['error']}")

    orders_db[order_id] = message.from_user.id

    text = (
        f"💳 <b>To'lov ma'lumotlari:</b>\n\n"
        f"Karta: <code>{data['card']}</code>\n"
        f"Egasi: <b>{data.get('card_holder') or 'Mavjud'}</b>\n"
        f"Summa: <b>{data['amount']:,} so'm</b>\n\n"
        f"⏱ <i>Iltimos, {data['expires_in'] // 60} daqiqa ichida to'lovni bajaring. "
        f"Pul tushishi bilan xarid avtomatik tasdiqlanadi.</i>"
    ).replace(",", " ")
    await message.answer(text, parse_mode="HTML")

# 2. PayGate dan keladigan webhook (complete_url) ni qabul qilish
async def paygate_webhook(request: web.Request):
    data = await request.post()
    payment_id = data.get("payment_id", "")
    amount = data.get("amount", "")
    order_id = data.get("order_id", "")
    status = data.get("status", "")
    sign = data.get("sign", "")

    # Imzoni tekshirish
    expected_sign = hashlib.md5(f"{SHOP_ID}{payment_id}{amount}{SHOP_KEY}".encode()).hexdigest()
    if sign != expected_sign:
        return web.Response(text="Noto'g'ri imzo", status=403)

    user_id = orders_db.pop(order_id, None)
    if user_id:
        if status == "paid":
            await bot.send_message(user_id, f"✅ To'lovingiz qabul qilindi! Buyurtma tasdiqlandi.")
        elif status == "cancel":
            await bot.send_message(user_id, f"❌ To'lov muddati tugadi va bekor qilindi.")

    return web.Response(text="OK")

# Webhook server va botni ishga tushirish
async def main():
    app = web.Application()
    app.router.add_post("/payment/callback", paygate_webhook)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", 8080).start()
    await dp.start_polling(bot)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
```

---

### 🐘 2. PHP (Oddiy PHP yoki Laravel)

#### To'lov yaratish (`create_payment.php`):
```php
<?php
$paygateUrl = "https://pay.example.uz";
$shopId     = "your_shop_id";
$shopKey    = "your_shop_key";

$ch = curl_init("$paygateUrl/payment/create");
curl_setopt_array($ch, [
    CURLOPT_POST           => true,
    CURLOPT_RETURNTRANSFER => true,
    CURLOPT_POSTFIELDS     => http_build_query([
        "shop_id"   => $shopId,
        "shop_key"  => $shopKey,
        "amount"    => 50000,
        "order_id"  => "ORDER_1001"
    ])
]);
$response = json_decode(curl_exec($ch), true);
curl_close($ch);

if (isset($response['card'])) {
    echo "Karta: " . $response['card'] . "\n";
    echo "Summa: " . $response['amount'] . " so'm\n";
    echo "Egasi: " . $response['card_holder'] . "\n";
} else {
    echo "Xatolik: " . ($response['error'] ?? 'Nomaʼlum');
}
```

#### Callback qabul qilish (`complete.php`):
```php
<?php
$shopId  = "your_shop_id";
$shopKey = "your_shop_key";

$paymentId = $_POST['payment_id'] ?? '';
$amount    = $_POST['amount'] ?? '';
$orderId   = $_POST['order_id'] ?? '';
$status    = $_POST['status'] ?? '';
$sign      = $_POST['sign'] ?? '';

// Imzoni tekshirish
$expectedSign = md5($shopId . $paymentId . $amount . $shopKey);
if (!hash_equals($expectedSign, $sign)) {
    http_response_code(403);
    exit('Noto\'g\'ri imzo');
}

if ($status === 'paid') {
    // 1. Ma'lumotlar bazasida buyurtmani "To'landi" deb belgilang
    // 2. Foydalanuvchiga xarid qilingan xizmatni bering
}

echo 'OK';
```

---

### 🟢 3. Node.js (Express / TypeScript)

#### To'lov yaratish va Webhook qabul qilish:
```javascript
const express = require('express');
const crypto = require('crypto');
const axios = require('axios');

const app = express();
app.use(express.urlencoded({ extended: true }));
app.use(express.json());

const PAYGATE_URL = 'https://pay.example.uz';
const SHOP_ID = 'your_shop_id';
const SHOP_KEY = 'your_shop_key';

// 1. To'lov yaratish
async function createPayment(orderId, amount) {
  const { data } = await axios.post(`${PAYGATE_URL}/payment/create`, {
    shop_id: SHOP_ID,
    shop_key: SHOP_KEY,
    amount: amount,
    order_id: orderId,
  });
  return data;
}

// 2. Webhook qabul qilish (complete_url)
app.post('/api/paygate/callback', (req, res) => {
  const { shop_id, payment_id, amount, order_id, status, sign } = req.body;

  // Imzo tekshirish
  const raw = `${SHOP_ID}${payment_id}${amount}${SHOP_KEY}`;
  const expectedSign = crypto.createHash('md5').update(raw).digest('hex');

  if (sign !== expectedSign) {
    return res.status(403).send('Noto‘g‘ri imzo');
  }

  if (status === 'paid') {
    console.log(`✅ To'lov muvaffaqiyatli: Order ${order_id}, Summa: ${amount} so'm`);
    // Foydalanuvchiga xaridni tasdiqlang
  }

  res.send('OK');
});

app.listen(3000, () => console.log('Server 3000-portda ishlamoqda'));
```

---

### 💻 4. cURL (Terminal / Postman)

#### To'lov yaratish:
```bash
curl -X POST "https://pay.example.uz/payment/create" \
     -d "shop_id=your_shop_id" \
     -d "shop_key=your_shop_key" \
     -d "amount=50000" \
     -d "order_id=ORD_123"
```

#### To'lov holatini tekshirish:
```bash
curl -X GET "https://pay.example.uz/payment/status?shop_id=your_shop_id&shop_key=your_shop_key&payment_id=PAYMENT_ID"
```

---

## 💡 Foydali Maslahatlar

1. **Callback URL ni sozlash:** Telegram botda `/callback SHOP_ID https://sizning-saytingiz.uz/api/payment/callback` buyrug'ini yuborib bir marta o'rnatasiz.
2. **Karta qo'shish:** `/card SHOP_ID 8600123456789012,9860123456789012` orqali bir yoki bir nechta kartalarni kiritishingiz mumkin (bir nechta bo'lsa navbatma-navbat beriladi).
3. **Karta egasi:** `/holder SHOP_ID ISM FAMILIYA` buyrug'i orqali karta egasini o'rnatasiz.
4. **Xavfsizlik:** `SHOP_KEY` ni faqat backend serveringizda saqlang, brauzer (frontend) kodiga kiritmang!

