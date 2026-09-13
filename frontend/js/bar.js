// 吧台頁面：三欄訂單佇列，一張訂單一張卡片（卡片內列出所有品項）
// new → preparing → completed → delivered（delivered 後離開佇列）
// 點卡片推進整張訂單；展開後可以單獨切換個別品項。
// Andon：三段式超時門檻（見 common.js 的 ANDON_THRESHOLDS），
//        超時除了卡片變紅，還會跳出視窗 + 播放警示音

const NEXT_STATUS = { new: "preparing", preparing: "completed", completed: "delivered" };
const NEXT_LABEL = { new: "開始製作 ▶", preparing: "完成 ✓", completed: "已送出 🚚" };

// 超時時的說明文字
const ANDON_REASON = {
  new: (m) => `下單後超過 ${m} 分鐘仍未開始製作`,
  preparing: (m) => `製作中已超過 ${m} 分鐘`,
  completed: (m) => `完成後 ${m} 分鐘未送達`,
};

// ---------- 警示音（Web Audio 產生嗶聲，不需外部音檔）----------
let audioCtx = null;

function ensureAudio() {
  if (!audioCtx) {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return null;
    audioCtx = new Ctx();
  }
  if (audioCtx.state === "suspended") audioCtx.resume();
  return audioCtx;
}

function playAlarm(beeps = 3) {
  const ctx = ensureAudio();
  if (!ctx || ctx.state !== "running") return false;
  for (let i = 0; i < beeps; i++) {
    const start = ctx.currentTime + i * 0.28;
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = "square";
    osc.frequency.value = 880;
    // 淡入淡出，避免爆音
    gain.gain.setValueAtTime(0.0001, start);
    gain.gain.exponentialRampToValueAtTime(0.25, start + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, start + 0.2);
    osc.connect(gain).connect(ctx.destination);
    osc.start(start);
    osc.stop(start + 0.22);
  }
  return true;
}

document.addEventListener("DOMContentLoaded", () => {
  const queues = {
    new: document.getElementById("queue-new"),
    preparing: document.getElementById("queue-preparing"),
    completed: document.getElementById("queue-completed"),
  };
  const modal = document.getElementById("andon-modal");
  const modalBody = document.getElementById("andon-modal-body");
  const soundHint = document.getElementById("sound-hint");

  // 瀏覽器要求先有使用者互動才能播音；第一次點擊時解鎖並隱藏提示
  function unlockAudio() {
    const ctx = ensureAudio();
    if (ctx && ctx.state === "running" && soundHint) soundHint.hidden = true;
  }
  document.addEventListener("click", unlockAudio, { once: true });
  document.addEventListener("keydown", unlockAudio, { once: true });

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
          data-table="${order.table_number}"
          data-summary="${escapeHtml(order.summary)}">
        <div class="order-row card-head">
          <strong>桌 ${order.table_number}</strong>
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

  function showModal(items) {
    modalBody.innerHTML = items
      .map(
        (it) => `
        <li class="overdue-item">
          <strong>桌 ${it.table} ・ ${it.summary}</strong>
          <div class="modal-reason">${it.reason}</div>
        </li>`
      )
      .join("");
    modal.hidden = false;
  }

  document.getElementById("andon-modal-close").addEventListener("click", () => {
    modal.hidden = true;
  });
  modal.addEventListener("click", (e) => {
    if (e.target === modal) modal.hidden = true; // 點背景關閉
  });

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
