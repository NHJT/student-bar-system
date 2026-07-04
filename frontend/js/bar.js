// 吧台頁面：三欄訂單佇列，點卡片推進狀態
// new → preparing → completed → delivered（delivered 後離開佇列）

const REFRESH_MS = 3000; // 第四步改用 WebSocket 後移除輪詢
const ANDON_TIMEOUT_MINUTES = 5; // 超時門檻（第五步使用）

const NEXT_STATUS = { new: "preparing", preparing: "completed", completed: "delivered" };
const NEXT_LABEL = { new: "開始製作 ▶", preparing: "完成 ✓", completed: "已送出 🚚" };

document.addEventListener("DOMContentLoaded", () => {
  const queues = {
    new: document.getElementById("queue-new"),
    preparing: document.getElementById("queue-preparing"),
    completed: document.getElementById("queue-completed"),
  };

  function card(order) {
    return `
      <li data-id="${order.id}" data-status="${order.status}" title="點一下 → ${NEXT_LABEL[order.status]}">
        <div class="order-row">
          <strong>桌 ${order.table_number}</strong>
          <span>${escapeHtml(order.drink_name)} × ${order.quantity}</span>
          <span class="order-time">${formatTime(order.placed_at)}</span>
        </div>
        ${order.special_request
          ? `<div class="order-note">📝 ${escapeHtml(order.special_request)}</div>`
          : ""}
        <div class="card-action">${NEXT_LABEL[order.status]}</div>
      </li>`;
  }

  async function refresh() {
    const orders = await apiGet("/orders");
    for (const status of Object.keys(queues)) {
      const items = orders.filter((o) => o.status === status);
      queues[status].innerHTML = items.length
        ? items.map(card).join("") // API 已依 placed_at 排序，先進先出
        : '<li class="placeholder">－</li>';
    }
    // TODO(第五步): 檢查 placed_at 超過 ANDON_TIMEOUT_MINUTES 的卡片加上 .overdue
  }

  // 點卡片 → 推進到下一個狀態（事件委派）
  document.querySelector("main").addEventListener("click", async (e) => {
    const li = e.target.closest("li[data-id]");
    if (!li) return;
    const next = NEXT_STATUS[li.dataset.status];
    if (!next) return;
    try {
      await apiSend("PATCH", `/orders/${li.dataset.id}/status`, { status: next });
      await refresh();
    } catch (err) {
      alert(`狀態更新失敗：${err.message}`);
      await refresh(); // 可能被別台裝置先更新了，重抓一次
    }
  });

  refresh();
  setInterval(refresh, REFRESH_MS);
  // TODO(第四步): connectWebSocket(...) 取代輪詢
});
