// 吧台頁面：兩欄訂單佇列，一張訂單一張卡片（卡片內列出所有品項）
// new → preparing → completed，推到 completed（待送達）就離開吧台佇列，
// 後面的送酒與「待送達超時」警示由服務生頁負責。
// 點卡片推進整張訂單；展開後可以單獨切換個別品項。
// Andon：超時門檻見 common.js 的 ANDON_THRESHOLDS，
//        超時除了卡片變紅，還會跳出視窗 + 播放警示音

const NEXT_STATUS = { new: "preparing", preparing: "completed" };
const NEXT_LABEL = { new: "開始製作 ▶", preparing: "完成 ✓" };

// 吧台搆得到的位置（吧台／外帶／站位，見 common.js）調完就直接遞給客人，
// 所以「完成」那一步順手把訂單推到已送達，不用再繞一趟服務生的待送達。
// 走的是跟服務生頁完全一樣的狀態流程，delivered_at 照常蓋。
const DELIVER_LABEL = "完成並送出 ✓";

// 超時時的說明文字（不含 completed，那一類改在服務生頁觸發）
// 可以退回上一個狀態的，對應按鈕文字。delivered 不在裡面（外場已確認送達）
// delivered 不在裡面：已送達的訂單只出現在服務生頁，退回也在那裡做
const REVERT_LABEL = {
  preparing: "↩ 退回新訂單",
  completed: "↩ 退回製作中",
};

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
  // 已完成／已送達的卡片：多列出四個階段的時間，方便事後核對
  function card(order) {
    const ref = order[ANDON_REF_FIELD[order.status]] ?? order.placed_at;
    // 各品項狀態不一致時提示，讓吧台知道還有品項落後
    const mixed = new Set(order.items.map((i) => i.status)).size > 1;
    // 只有「完成」那一步會順手送出；開始製作那一步跟平常一樣
    const selfDeliver = order.status === "preparing" && isBarReachable(order.table_number);
    return `
      <li data-id="${order.id}" data-status="${order.status}" data-ref="${ref}"
          data-table="${escapeHtml(order.table_number)}"
          data-placed="${escapeHtml(order.placed_at)}"
          ${order.delivered_at ? `data-ended="${escapeHtml(order.delivered_at)}"` : ""}
          data-summary="${escapeHtml(order.summary)}">
        <div class="order-row card-head">
          <strong>${escapeHtml(tableLabel(order.table_number))}</strong>
          <span class="card-count">${order.item_count} 品項 / ${order.total_quantity} 杯</span>
          ${mixed ? '<span class="badge badge-preparing">品項進度不一</span>' : ""}
          <span class="order-time">${formatTime(order.placed_at)}</span>
        </div>
        <div class="card-items">
          ${order.items
            .map(
              (i) => `<div class="card-item-brief">${escapeHtml(i.drink_name)} × ${i.quantity}${
                i.special_request ? `<span class="order-note">備註：${escapeHtml(i.special_request)}</span>` : ""
              }</div>`
            )
            .join("")}
        </div>
        <div class="card-action">
          <span class="elapsed"></span>
          <span class="total-wait"></span>
          ${REVERT_LABEL[order.status]
            ? `<button class="btn btn-revert" data-group-revert="${order.id}"
                  title="退回上一個狀態">${REVERT_LABEL[order.status]}</button>`
            : ""}
          ${NEXT_STATUS[order.status]
            ? `<button class="btn btn-small btn-primary" data-group-next="${order.id}"
                    data-next-status="${NEXT_STATUS[order.status]}"
                    ${selfDeliver ? "data-also-deliver" : ""}>
                 ${selfDeliver ? DELIVER_LABEL : NEXT_LABEL[order.status]}
               </button>`
            : ""}
        </div>
      </li>`;
  }

  // Andon：計算每張卡片在目前狀態已停留多久，超時變紅並提醒
  function updateAndon() {
    const fresh = [];
    document.querySelectorAll(
      "#queue-new li[data-id], #queue-preparing li[data-id]"
    ).forEach((li) => {
      const status = li.dataset.status;
      const state = andonState(status, li.dataset.ref);
      if (!state) return; // 沒有門檻的狀態（已送達）不計時
      const { minutes, limit, overdue } = state;

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

  // 總等待時間（下單到現在）。純顯示，不碰上面的超時判斷與警示音。
  function updateTotalWait() {
    document.querySelectorAll(".order-queue li[data-id]").forEach(renderTotalWait);
  }

  async function refresh() {
    // 新訂單／製作中不加日期條件，跨午夜還沒做完的訂單才不會從佇列消失
    const orders = await apiGet("/orders");
    for (const status of Object.keys(queues)) {
      const items = orders.filter((o) => o.status === status);
      queues[status].innerHTML = items.length
        ? items.map(card).join("") // API 已依 placed_at 排序，先進先出
        : '<li class="placeholder">－</li>';
    }
    updateAndon();
    updateTotalWait();
  }

  document.body.addEventListener("click", async (e) => {
    const btn = e.target.closest("button");
    if (!btn) return;
    const { groupNext, nextStatus, groupRevert } = btn.dataset;

    try {
      if (groupRevert !== undefined) {
        // 誤觸要能快速修正，所以不做二次確認
        await apiSend("PATCH", `/orders/${groupRevert}/revert`);
      } else if (groupNext !== undefined) {
        await apiSend("PATCH", `/orders/${groupNext}/status`, { status: nextStatus });
        // 吧台自送：完成之後再推一步到已送達。分兩次呼叫是刻意的，
        // 狀態流程一步一步走，completed_at 與 delivered_at 都會留下。
        // 第二步萬一失敗，訂單就停在待送達，服務生頁看得到，不會遺失。
        if (btn.dataset.alsoDeliver !== undefined) {
          await apiSend("PATCH", `/orders/${groupNext}/status`, { status: "delivered" });
        }
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
  // 總等待時間同一個節奏更新（10 秒一次，至少每分鐘會跳一次數字）
  setInterval(updateTotalWait, 10000);
});
