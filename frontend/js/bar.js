// 吧台頁面：三欄訂單佇列，點卡片推進狀態
// new → preparing → completed → delivered（delivered 後離開佇列）
// Andon：三段式超時門檻（見 common.js 的 ANDON_THRESHOLDS），
//        超時除了卡片變紅，還會跳出視窗 + 播放警示音

const NEXT_STATUS = { new: "preparing", preparing: "completed", completed: "delivered" };
const NEXT_LABEL = { new: "開始製作 ▶", preparing: "完成 ✓", completed: "已送出 🚚" };

// Andon 計時基準：各欄從「進入該狀態」的時間開始算
const ANDON_REF_FIELD = { new: "placed_at", preparing: "started_at", completed: "completed_at" };

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

  function card(order) {
    const ref = order[ANDON_REF_FIELD[order.status]] ?? order.placed_at;
    return `
      <li data-id="${order.id}" data-status="${order.status}" data-ref="${ref}"
          data-table="${order.table_number}" data-drink="${escapeHtml(order.drink_name)}"
          data-qty="${order.quantity}"
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

  function showModal(items) {
    modalBody.innerHTML = items
      .map(
        (it) => `
        <li class="overdue-item">
          <strong>桌 ${it.table} ・ ${it.drink} × ${it.qty}</strong>
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
      const limit = ANDON_THRESHOLDS[status];
      const ref = new Date(li.dataset.ref.replace(" ", "T")).getTime();
      const minutes = Math.max(0, (Date.now() - ref) / 60000);
      const overdue = minutes >= limit;

      li.querySelector(".elapsed").textContent = `已等 ${Math.floor(minutes)} / ${limit} 分 ・ `;
      li.classList.toggle("overdue", overdue);

      const key = `${li.dataset.id}:${status}`;
      if (overdue && !alerted.has(key)) {
        alerted.add(key);
        fresh.push({
          table: li.dataset.table,
          drink: li.dataset.drink,
          qty: li.dataset.qty,
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
