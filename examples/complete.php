<?php
// PayGate complete_url handler misoli
$shopId  = getenv('SHOP_ID')  ?: 'my_shop';
$shopKey = getenv('SHOP_KEY') ?: 'sk_xxxxxxxx';

$sign = md5($shopId . ($_POST['payment_id'] ?? '') . ($_POST['amount'] ?? '') . $shopKey);
if (!hash_equals($sign, $_POST['sign'] ?? '')) {
    http_response_code(403);
    exit('bad sign');
}

$orderId = $_POST['order_id'];
switch ($_POST['status']) {
    case 'prepare': /* to'lov yaratildi */ break;
    case 'paid':    /* buyurtmani tasdiqlang: $orderId */ break;
    case 'cancel':  /* buyurtmani bekor qiling */ break;
}
echo 'OK';
