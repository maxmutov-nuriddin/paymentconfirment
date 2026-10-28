/**
 * PayGate integratsiyasi: Node.js (Express + Axios) misoli.
 *
 * O'rnatish: npm install express axios
 * Ishga tushirish: node node_example.js
 */
const express = require('express');
const crypto = require('crypto');
const axios = require('axios');

const app = express();
app.use(express.urlencoded({ extended: true }));
app.use(express.json());

// Sozlamalar (Botdan olingan ma'lumotlar)
const PAYGATE_URL = process.env.PAYGATE_URL || 'http://localhost:8000';
const SHOP_ID = process.env.SHOP_ID || 'your_shop_id';
const SHOP_KEY = process.env.SHOP_KEY || 'your_shop_key';
const PORT = process.env.PORT || 3000;

// 1. To'lov yaratish funksiyasi
async function createPayment(orderId, amount) {
  try {
    const response = await axios.post(`${PAYGATE_URL}/payment/create`, {
      shop_id: SHOP_ID,
      shop_key: SHOP_KEY,
      amount: amount,
      order_id: orderId,
    });
    return response.data;
  } catch (err) {
    console.error('To‘lov yaratishda xatolik:', err.response?.data || err.message);
    throw err;
  }
}

// 2. Callback (Webhook) qabul qilish
app.post('/payment/callback', (req, res) => {
  const { shop_id, payment_id, amount, order_id, status, sign } = req.body;

  // Imzo tekshirish (Sign verification)
  const raw = `${SHOP_ID}${payment_id}${amount}${SHOP_KEY}`;
  const expectedSign = crypto.createHash('md5').update(raw).digest('hex');

  if (sign !== expectedSign) {
    console.warn('⚠️ Noto‘g‘ri sign:', sign);
    return res.status(403).send('Bad sign');
  }

  if (status === 'paid') {
    console.log(`✅ To‘lov tasdiqlandi! Order: ${order_id}, Summa: ${amount} so'm`);
    // Foydalanuvchiga tovar / obunani taqdim qiling
  } else if (status === 'cancel') {
    console.log(`❌ To‘lov bekor bo‘ldi: ${order_id}`);
  }

  res.send('OK');
});

app.listen(PORT, () => {
  console.log(`Server ${PORT}-portda ishlamoqda.`);
});

