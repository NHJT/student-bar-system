// 經理頁面：儀表板（今日 KPI、熱門品項、超時訂單）+ 訂單總覽 + 庫存警示

document.addEventListener("DOMContentLoaded", () => {
  const kpiRow = document.getElementById("kpi-row");
  const topDrinks = document.getElementById("top-drinks");
  const overdueList = document.getElementById("overdue-list");
  const statsRow = document.getElementById("order-stats");
  const orderBody = document.getElementById("order-table-body");
  const alertList = document.getElementById("stock-alerts");
  const stockBody = document.getElementById("stock-table-body");

  let editingIngredient = null; // 正在編輯的原料 id
  let pendingStockRefresh = false; // 編輯期間收到的更新，等編輯結束再套用

  function kpiTile(label, value, warn = false) {
    return `
      <div class="stat-tile ${warn ? "stat-warn" : ""}">
        <div class="stat-label">${label}</div>
        <div class="stat-value">${value}</div>
      </div>`;
  }

  async function refreshStats() {
    const s = await apiGet("/stats");

    kpiRow.innerHTML = [
      kpiTile("今日訂單", s.today_orders),
      kpiTile("售出杯數", s.drinks_sold),
      kpiTile("平均製作時間", s.avg_prep_minutes == null ? "－" : `${s.avg_prep_minutes} 分`),
      kpiTile("未付款", s.unpaid, s.unpaid > 0),
      kpiTile("超時訂單", s.overdue.length, s.overdue.length > 0),
      kpiTile("訂單修改率", `${s.edit_rate}%`, s.edited_orders > 0),
    ].join("");

    // 熱門品項：單一色相水平長條，值直接標在條尾
    if (s.top_drinks.length === 0) {
      topDrinks.innerHTML = '<span class="placeholder">今日尚無售出</span>';
    } else {
      const max = s.top_drinks[0].qty;
      topDrinks.innerHTML = s.top_drinks
        .map(
          (d) => `
          <div class="bar-row" title="${escapeHtml(d.name)}：${d.qty} 杯">
            <span class="bar-name">${escapeHtml(d.name)}</span>
            <span class="bar-track"><span class="bar-fill" style="width:${(d.qty / max) * 100}%"></span></span>
            <span class="bar-value">${d.qty}</span>
          </div>`
        )
        .join("");
    }

    overdueList.innerHTML = s.overdue.length
      ? s.overdue
          .map(
            (o) => `
            <li class="overdue-item">⏱ <strong>桌 ${o.table_number}</strong>
              ${escapeHtml(o.drink_name)} × ${o.quantity}
              ・ ${STATUS_LABELS[o.status]} 已卡 ${o.minutes_stuck} 分
              （門檻 ${o.threshold_minutes} 分）</li>`
          )
          .join("")
      : '<li class="placeholder">目前沒有超時訂單 ✓</li>';
  }

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
      orderBody.innerHTML = '<tr><td colspan="9" class="placeholder">尚無資料</td></tr>';
      return;
    }
    orderBody.innerHTML = [...orders]
      .reverse() // 新的在上面
      .map((o) => {
        // 超時的訂單整列標紅（門檻與吧台 Andon 看板相同）
        const andon = orderAndonState(o);
        return `
        <tr class="${andon?.overdue ? "row-overdue" : ""}"
            data-status="${o.status}" data-ref="${o[ANDON_REF_FIELD[o.status]] ?? o.placed_at}">
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
          <td>${formatTime(o.completed_at) || "—"}</td>
          <td>${formatTime(o.delivered_at) || "—"}</td>
        </tr>`;
      })
      .join("");
  }

  // 訂單會隨時間走到超時，但那不會產生任何事件，所以定期重算整列標紅
  function updateOverdueRows() {
    orderBody.querySelectorAll("tr[data-status]").forEach((tr) => {
      const state = andonState(tr.dataset.status, tr.dataset.ref);
      tr.classList.toggle("row-overdue", Boolean(state?.overdue));
    });
  }

  // 一般顯示模式的原料列
  function stockRow(i) {
    return `
      <tr class="${i.current_stock < i.reorder_point ? "row-low" : ""}">
        <td>${escapeHtml(i.name)}</td>
        <td>${fmtQty(i.current_stock)}</td>
        <td>${escapeHtml(i.unit)}</td>
        <td>${fmtQty(i.reorder_point)}</td>
        <td><button class="btn btn-small btn-ghost" data-edit-stock="${i.id}">編輯</button></td>
      </tr>`;
  }

  // 編輯模式：庫存與補貨點就地變成輸入框
  function stockEditRow(i) {
    return `
      <tr class="row-editing" data-editing="${i.id}">
        <td>${escapeHtml(i.name)}</td>
        <td><input type="number" step="0.01" min="0" value="${i.current_stock}"
                   data-field="stock" class="stock-input"></td>
        <td>${escapeHtml(i.unit)}</td>
        <td><input type="number" step="0.01" min="0" value="${i.reorder_point}"
                   data-field="reorder" class="stock-input"></td>
        <td class="stock-actions">
          <button class="btn btn-small btn-primary" data-save-stock="${i.id}">儲存</button>
          <button class="btn btn-small btn-ghost" data-cancel-stock="1">取消</button>
        </td>
      </tr>`;
  }

  async function refreshStock() {
    // 編輯中不重繪，避免輸入到一半被蓋掉
    if (editingIngredient !== null) {
      pendingStockRefresh = true;
      return;
    }
    const ingredients = await apiGet("/ingredients");

    const low = ingredients.filter((i) => i.current_stock < i.reorder_point);
    alertList.innerHTML = low.length
      ? low
          .map(
            (i) => `
            <li class="low-stock">⚠️ <strong>${escapeHtml(i.name)}</strong>
              剩 ${fmtQty(i.current_stock)} ${escapeHtml(i.unit)}（補貨點 ${fmtQty(i.reorder_point)}）</li>`
          )
          .join("")
      : '<li class="placeholder">庫存充足 ✓</li>';

    stockBody.innerHTML = ingredients.length
      ? ingredients.map(stockRow).join("")
      : '<tr><td colspan="5" class="placeholder">尚無資料</td></tr>';
  }

  // 庫存列的編輯／儲存／取消（事件委派）
  stockBody.addEventListener("click", async (e) => {
    const btn = e.target.closest("button");
    if (!btn) return;
    const { editStock, saveStock, cancelStock } = btn.dataset;

    try {
      if (editStock) {
        const ingredient = await apiGet(`/ingredients/${editStock}`);
        editingIngredient = Number(editStock);
        btn.closest("tr").outerHTML = stockEditRow(ingredient);
      } else if (cancelStock) {
        await closeStockEditor();
      } else if (saveStock) {
        const tr = btn.closest("tr");
        const stock = Number(tr.querySelector('[data-field="stock"]').value);
        const reorder = Number(tr.querySelector('[data-field="reorder"]').value);
        if (!Number.isFinite(stock) || !Number.isFinite(reorder) || stock < 0 || reorder < 0) {
          alert("庫存與補貨點必須是 0 或正數");
          return;
        }
        await apiSend("PATCH", `/ingredients/${saveStock}`, {
          current_stock: stock,
          reorder_point: reorder,
        });
        await closeStockEditor();
      }
    } catch (err) {
      alert(`庫存更新失敗：${err.message}`);
      await closeStockEditor();
    }
  });

  async function closeStockEditor() {
    editingIngredient = null;
    pendingStockRefresh = false;
    await refreshStock();
  }

  function refreshAll() {
    refreshStats();
    refreshOrders();
    refreshStock();
  }

  // 即時更新：訂單事件刷新統計與訂單區；庫存事件刷新庫存區；
  // 重連成功時全部補抓
  connectWebSocket((msg) => {
    if (msg.event?.startsWith("order_")) {
      refreshStats();
      refreshOrders();
    }
    if (msg.event === "stock_alert" || msg.event?.startsWith("ingredient_")) refreshStock();
  }, refreshAll);

  // 超時狀態會隨時間變化而沒有任何事件，定期重算
  setInterval(refreshStats, 30000); // 超時清單與 KPI（需要後端計算）
  setInterval(updateOverdueRows, 10000); // 訂單表格整列標紅（純前端計算）
});
