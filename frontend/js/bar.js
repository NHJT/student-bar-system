// 吧台頁面：兩欄訂單佇列，一張訂單一張卡片（卡片內列出所有品項）
// new → preparing → completed，推到 completed（待送達）就離開吧台佇列，
// 後面的送酒與「待送達超時」警示由服務生頁負責。
// 點卡片推進整張訂單；展開後可以單獨切換個別品項。
// Andon：超時門檻見 common.js 的 ANDON_THRESHOLDS，
//        超時除了卡片變紅，還會跳出視窗 + 播放警示音

const NEXT_STATUS = { new: "preparing", preparing: "completed" };
const NEXT_LABEL = { new: "開始製作 ▶", preparing: "完成 ✓" };

// 超時時的說明文字（不含 completed，那一類改在服務生頁觸發）
const ANDON_REASON = {
  new: (m) => `下單後超過 ${m} 分鐘仍未開始製作`,
  preparing: (m) => `製作中已超過 ${m} 分鐘`,
};

document.addEventListener("DOMContentLoaded", () => {
  const queues = {
    new: document.getElementById("queue-new"),
    preparing: document.getElementById("queue-preparing"),
  };
  setupAudioUnlock();
  const showModal = setupAndonModal();

  // 已經跳過提醒的訂單，key = 訂單id:狀態（換一站會再提醒一次）
  const alerted = new Set();
  // 展開中的卡片，重繪後要保持展開
  const expanded = new Set();

  function itemRow(order, item) {
    const next = NEXT_STATUS[item.status];
    return `
      <div class="bar-item ${item.status === "completed" ? "item-done" : ""}">
        <span class="bar-item-name">${escapeHtml(item.drink_name)} × ${item.quantity}</span>
        ${statusBadge(item.status)}
        ${item.special_request
          ? `<span class="order-note">📝 ${escapeHtml(item.special_request)}</span>`
          : ""}
        ${next
          ? `<button class="btn btn-small btn-ghost"
                data-item-next="${item.id}" data-group="${order.id}"
                data-next-status="${next}">${NEXT_LABEL[item.status]}</button>`
          : ""}
      </div>`;
  }

  function card(order) {
    const ref = order[ANDON_REF_FIELD[order.status]] ?? order.placed_at;
    const open = expanded.has(order.id);
    // 各品項狀態不一致時提示，讓吧台知道還有品項落後
    const mixed = new Set(order.items.map((i) => i.status)).size > 1;
    return `
      <li data-id="${order.id}" data-status="${order.status}" data-ref="${ref}"
          data-table="${escapeHtml(order.table_number)}"
          data-summary="${escapeHtml(order.summary)}">
        <div class="order-row card-head">
          <strong>${escapeHtml(tableLabel(order.table_number))}</strong>
          <span class="card-count">${order.item_count} 品項 / ${order.total_quantity} 杯</span>
          ${mixed ? '<span class="badge badge-preparing">品項進度不一</span>' : ""}
          <span class="order-time">${formatTime(order.placed_at)}</span>
        </div>
        <div class="card-items" ${open ? "hidden" : ""}>
          ${order.items
            .map(
              (i) => `<div class="card-item-brief">${escapeHtml(i.drink_name)} × ${i.quantity}${
                i.special_request ? ` <span class="order-note">📝 ${escapeHtml(i.special_request)}</span>` : ""
              }</div>`
            )
            .join("")}
        </div>
        <div class="bar-items" ${open ? "" : "hidden"}>
          ${order.items.map((i) => itemRow(order, i)).join("")}
        </div>
        <div class="card-action">
          <span class="elapsed"></span>
          <button class="btn btn-small btn-ghost" data-expand="${order.id}">
            ${open ? "收合品項 ▴" : "分項處理 ▾"}
          </button>
          <button class="btn btn-small btn-primary" data-group-next="${order.id}"
                  data-next-status="${NEXT_STATUS[order.status]}">
            ${NEXT_LABEL[order.status]}
          </button>
        </div>
      </li>`;
  }

  // Andon：計算每張卡片在目前狀態已停留多久，超時變紅並提醒
  function updateAndon() {
    const fresh = [];
    document.querySelectorAll(".order-queue li[data-id]").forEach((li) => {
      const status = li.dataset.status;
      const { minutes, limit, overdue } = andonState(status, li.dataset.ref);

      li.querySelector(".elapsed").textContent = `已等 ${Math.floor(minutes)} / ${limit} 分`;
      li.classList.toggle("overdue", overdue);

      const key = `${li.dataset.id}:${status}`;
      if (overdue && !alerted.has(key)) {
        alerted.add(key);
        fresh.push({
          table: li.dataset.table,
          summary: li.dataset.summary,
          reason: ANDON_REASON[status](limit),
        });
      }
    });

    if (fresh.length) {
      showModal(fresh);
      playAlarm();
    }
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

  document.querySelector("main").addEventListener("click", async (e) => {
    const btn = e.target.closest("button");
    if (!btn) return;
    const { expand, groupNext, itemNext, group, nextStatus } = btn.dataset;

    // 展開／收合只影響畫面
    if (expand !== undefined) {
      const id = Number(expand);
      expanded.has(id) ? expanded.delete(id) : expanded.add(id);
      await refresh();
      return;
    }

    try {
      if (groupNext !== undefined) {
        await apiSend("PATCH", `/orders/${groupNext}/status`, { status: nextStatus });
      } else if (itemNext !== undefined) {
        await apiSend("PATCH", `/orders/${group}/items/${itemNext}/status`, {
          status: nextStatus,
        });
      } else {
        return;
      }
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
