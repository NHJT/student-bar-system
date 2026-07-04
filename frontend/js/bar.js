// 吧台頁面：三欄訂單佇列，點卡片推進狀態
// new → preparing → completed → delivered（delivered 後離開佇列）

const ANDON_TIMEOUT_MINUTES = 5; // Andon 超時門檻（分鐘）

const NEXT_STATUS = { new: "preparing", preparing: "completed", completed: "delivered" };
const NEXT_LABEL = { new: "開始製作 ▶", preparing: "完成 ✓", completed: "已送出 🚚" };

// Andon 計時基準：各欄從「進入該狀態」的時間開始算
const ANDON_REF_FIELD = { new: "placed_at", preparing: "started_at", completed: "completed_at" };

document.addEventListener("DOMContentLoaded", () => {
  const queues = {
    new: document.getElementById("queue-new"),
    preparing: document.getElementById("queue-preparing"),
    completed: document.getElementById("queue-completed"),
  };

  function card(order) {
    const ref = order[ANDON_REF_FIELD[order.status]] ?? order.placed_at;
    return `
      <li data-id="${order.id}" data-status="${order.status}" data-ref="${ref}"
          title="點一下 → ${NEXT_LABEL[order.status]}">
        <div class="order-row">
          <strong>桌 ${order.table_number}</strong>
          <span>${escapeHtml(order.drink_name)} × ${order.quantity}</span>
          <span class="order-time">${formatTime(order.placed_at)}</span>
        </div>
        ${order.special_request
          ? `<div class="order-note">📝 ${escapeHtml(order.special_request)}</div>`
          : ""}
        <div class="card-action"><span class="elapsed"></span>${NEXT_LABEL[order.status]}</div>
      </li>`;
  }

  // Andon：計算每張卡片在目前狀態已停留多久，超時加上 .overdue 變紅
  function updateAndon() {
    document.querySelectorAll(".order-queue li[data-id]").forEach((li) => {
      const started = new Date(li.dataset.ref.replace(" ", "T")).getTime();
      const minutes = Math.max(0, (Date.now() - started) / 60000);
      li.querySelector(".elapsed").textContent = `已等 ${Math.floor(minutes)} 分 ・ `;
      li.classList.toggle("overdue", minutes >= ANDON_TIMEOUT_MINUTES);
    });
  }

  async function refresh() {
    const orders = await apiGet("/orders");
    for (const status of Object.keys(queues)) {
      const items = orders.filter((o) => o.status === status);
      queues[status].innerHTML = items.length
        ? items.map(card).join("") // API 已依 placed_at 排序，先進先出
        : '<li class="placeholder">－</li>';
    }
    updateAndon();
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

  // 即時更新：收到訂單事件就刷新；重連成功時補抓斷線期間的變化
  connectWebSocket(
    (msg) => { if (msg.event?.startsWith("order_")) refresh(); },
    () => refresh()
  );

  // Andon 每 10 秒重算一次（不用重抓資料，只更新等待時間與變紅）
  setInterval(updateAndon, 10000);
});
