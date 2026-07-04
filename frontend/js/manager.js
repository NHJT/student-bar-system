// 經理頁面：全局訂單總覽 + 庫存警示

document.addEventListener("DOMContentLoaded", () => {
  const statsRow = document.getElementById("order-stats");
  const orderBody = document.getElementById("order-table-body");
  const alertList = document.getElementById("stock-alerts");
  const stockBody = document.getElementById("stock-table-body");

  async function refreshOrders() {
    const orders = await apiGet("/orders");

    // 各狀態統計 + 未付款數
    const counts = {};
    let unpaid = 0;
    for (const o of orders) {
      counts[o.status] = (counts[o.status] ?? 0) + 1;
      if (o.payment_status === "unpaid" && o.status !== "cancelled") unpaid++;
    }
    statsRow.innerHTML =
      ["new", "preparing", "completed", "delivered"]
        .map(
          (s) =>
            `<div class="stat-card">${STATUS_LABELS[s]}<br><strong>${counts[s] ?? 0}</strong></div>`
        )
        .join("") +
      `<div class="stat-card ${unpaid ? "stat-warn" : ""}">未付款<br><strong>${unpaid}</strong></div>`;

    if (orders.length === 0) {
      orderBody.innerHTML = '<tr><td colspan="7" class="placeholder">尚無資料</td></tr>';
      return;
    }
    orderBody.innerHTML = [...orders]
      .reverse() // 新的在上面
      .map(
        (o) => `
        <tr>
          <td>${o.id}</td>
          <td>桌 ${o.table_number}</td>
          <td>${escapeHtml(o.drink_name)}${
            o.special_request ? `<br><small>📝 ${escapeHtml(o.special_request)}</small>` : ""
          }</td>
          <td>${o.quantity}</td>
          <td>${statusBadge(o.status)}</td>
          <td><span class="badge ${o.payment_status === "paid" ? "badge-paid" : "badge-unpaid"}">
            ${o.payment_status === "paid" ? "已付款" : "未付款"}</span></td>
          <td>${formatTime(o.placed_at)}</td>
        </tr>`
      )
      .join("");
  }

  async function refreshStock() {
    const ingredients = await apiGet("/ingredients");

    const low = ingredients.filter((i) => i.current_stock < i.reorder_point);
    alertList.innerHTML = low.length
      ? low
          .map(
            (i) => `
            <li class="low-stock">⚠️ <strong>${escapeHtml(i.name)}</strong>
              剩 ${i.current_stock} ${escapeHtml(i.unit)}（補貨點 ${i.reorder_point}）</li>`
          )
          .join("")
      : '<li class="placeholder">庫存充足 ✓</li>';

    stockBody.innerHTML = ingredients.length
      ? ingredients
          .map(
            (i) => `
            <tr class="${i.current_stock < i.reorder_point ? "row-low" : ""}">
              <td>${escapeHtml(i.name)}</td>
              <td>${i.current_stock}</td>
              <td>${escapeHtml(i.unit)}</td>
              <td>${i.reorder_point}</td>
            </tr>`
          )
          .join("")
      : '<tr><td colspan="4" class="placeholder">尚無資料</td></tr>';
  }

  function refreshAll() {
    refreshOrders();
    refreshStock();
  }

  // 即時更新：訂單事件刷新訂單區；庫存事件（第六步加入）刷新庫存區；
  // 重連成功時全部補抓
  connectWebSocket((msg) => {
    if (msg.event?.startsWith("order_")) refreshOrders();
    if (msg.event === "stock_alert" || msg.event?.startsWith("ingredient_")) refreshStock();
  }, refreshAll);
});
