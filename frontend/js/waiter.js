// 服務生頁面：點餐、選桌號、標記付款

const REFRESH_MS = 3000; // 第四步改用 WebSocket 後移除輪詢

document.addEventListener("DOMContentLoaded", () => {
  const tableSelect = document.getElementById("table-number");
  const drinkSelect = document.getElementById("drink-select");
  const form = document.getElementById("order-form");
  const orderList = document.getElementById("order-list");

  // 桌號 1–20 先寫死，之後可改成設定
  for (let i = 1; i <= 20; i++) {
    tableSelect.add(new Option(`桌 ${i}`, i));
  }

  async function loadDrinks() {
    const drinks = await apiGet("/drinks");
    drinkSelect.innerHTML = "";
    drinks
      .filter((d) => d.is_available)
      .forEach((d) => drinkSelect.add(new Option(`${d.name}（${d.category}）`, d.id)));
  }

  async function refreshOrders() {
    const orders = await apiGet("/orders");
    const active = orders.filter((o) => o.status !== "cancelled").reverse(); // 新的在上面
    if (active.length === 0) {
      orderList.innerHTML = '<li class="placeholder">尚無訂單</li>';
      return;
    }
    orderList.innerHTML = active
      .map(
        (o) => `
        <li>
          <div class="order-row">
            <strong>桌 ${o.table_number}</strong>
            <span>${escapeHtml(o.drink_name)} × ${o.quantity}</span>
            ${statusBadge(o.status)}
            <span class="badge ${o.payment_status === "paid" ? "badge-paid" : "badge-unpaid"}">
              ${o.payment_status === "paid" ? "已付款" : "未付款"}
            </span>
            <span class="order-time">${formatTime(o.placed_at)}</span>
            ${o.payment_status === "unpaid"
              ? `<button class="btn btn-small" data-pay="${o.id}">標記付款</button>`
              : ""}
          </div>
          ${o.special_request
            ? `<div class="order-note">📝 ${escapeHtml(o.special_request)}</div>`
            : ""}
        </li>`
      )
      .join("");
  }

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await apiSend("POST", "/orders", {
        table_number: Number(tableSelect.value),
        drink_id: Number(drinkSelect.value),
        quantity: Number(document.getElementById("quantity").value),
        special_request: document.getElementById("special-request").value || null,
      });
      document.getElementById("quantity").value = 1;
      document.getElementById("special-request").value = "";
      await refreshOrders();
    } catch (err) {
      alert(`下單失敗：${err.message}`);
    }
  });

  // 標記付款（事件委派，清單重繪也不會掉監聽）
  orderList.addEventListener("click", async (e) => {
    const id = e.target.dataset.pay;
    if (!id) return;
    try {
      await apiSend("PATCH", `/orders/${id}/payment`, { payment_status: "paid" });
      await refreshOrders();
    } catch (err) {
      alert(`標記付款失敗：${err.message}`);
    }
  });

  loadDrinks();
  refreshOrders();
  setInterval(refreshOrders, REFRESH_MS);
  // TODO(第四步): connectWebSocket(...) 取代輪詢
});
