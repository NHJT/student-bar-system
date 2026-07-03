// 服務生頁面（第三步實作）
// - 載入飲料清單到下拉選單
// - 送出訂單 POST /api/orders
// - 顯示本場訂單、標記付款 PATCH /api/orders/{id}/payment

document.addEventListener("DOMContentLoaded", () => {
  // 桌號 1–20 先寫死，之後可改成設定
  const tableSelect = document.getElementById("table-number");
  for (let i = 1; i <= 20; i++) {
    tableSelect.add(new Option(`桌 ${i}`, i));
  }

  // TODO(第三步): apiGet("/drinks") 填入 #drink-select
  // TODO(第三步): #order-form submit → apiSend("POST", "/orders", {...})
  // TODO(第三步): 渲染 #order-list 並提供「標記付款」按鈕
  // TODO(第四步): connectWebSocket(...) 收到 order_updated 時更新列表
});
