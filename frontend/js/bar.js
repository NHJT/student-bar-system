// 吧台頁面：兩欄訂單佇列，一張訂單一張卡片（卡片內列出所有品項）
// new → preparing → completed，推到 completed（待送達）就離開吧台佇列，
// 後面的送酒與「待送達超時」警示由服務生頁負責。
// 點卡片推進整張訂單；展開後可以單獨切換個別品項。
// Andon：超時門檻見 common.js 的 ANDON_THRESHOLDS，
//        超時除了卡片變紅，還會跳出視窗 + 播放警示音

const NEXT_STATUS = { new: "preparing", preparing: "completed" };
const NEXT_LABEL = { new: "開始製作 ▶", preparing: "完成 ✓" };

// 超時時的說明文字（不含 completed，那一類改在服務生頁觸發）
// 可以退回上一個狀態的，對應按鈕文字。delivered 不在裡面（外場已確認送達）
const REVERT_LABEL = {
  preparing: "↩ 退回新訂單",
  completed: "↩ 退回製作中",
  delivered: "↩ 退回待送達",   // 只限當日訂單，後端會擋
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
  const doneList = document.getElementById("queue-done");
  const doneToggle = document.getElementById("done-toggle");
  const doneCaret = document.getElementById("done-caret");
  const doneCount = document.getElementById("done-count");
  let doneOpen = false; // 預設收合

  setupAudioUnlock();
  const showModal = setupAndonModal();

  doneToggle.addEventListener("click", () => {
    doneOpen = !doneOpen;
    doneList.hidden = !doneOpen;
    doneCaret.textContent = doneOpen ? "▾" : "▸";
    doneToggle.setAttribute("aria-expanded", String(doneOpen));
  });

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
          ? `<span class="order-note">備註：${escapeHtml(item.special_request)}</span>`
          : ""}
        ${REVERT_LABEL[item.status]
          ? `<button class="btn btn-revert" data-item-revert="${item.id}"
                data-group="${order.id}"
                title="退回上一個狀態">${REVERT_LABEL[item.status]}</button>`
          : ""}
        ${next
          ? `<button class="btn btn-small btn-ghost"
                data-item-next="${item.id}" data-group="${order.id}"
                data-next-status="${next}">${NEXT_LABEL[item.status]}</button>`
          : ""}
      </div>`;
  }

  // 已完成／已送達的卡片：多列出四個階段的時間，方便事後核對
  function stageTimes(order) {
    const rows = [
      ["點單", order.placed_at],
      ["開始", order.started_at],
      ["完成", order.completed_at],
      ["送達", order.delivered_at],
    ];
    return `<div class="stage-times">${rows
      .map(([label, ts]) =>
        `<span><b>${label}</b> ${ts ? formatTime(ts) : "—"}</span>`)
      .join("")}</div>`;
  }

  function card(order) {
    const ref = order[ANDON_REF_FIELD[order.status]] ?? order.placed_at;
    const open = expanded.has(order.id);
    // 各品項狀態不一致時提示，讓吧台知道還有品項落後
    const mixed = new Set(order.items.map((i) => i.status)).size > 1;
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
        <div class="card-items" ${open ? "hidden" : ""}>
          ${order.items
            .map(
              (i) => `<div class="card-item-brief">${escapeHtml(i.drink_name)} × ${i.quantity}${
                i.special_request ? `<span class="order-note">備註：${escapeHtml(i.special_request)}</span>` : ""
              }</div>`
            )
            .join("")}
        </div>
        <div class="bar-items" ${open ? "" : "hidden"}>
          ${order.items.map((i) => itemRow(order, i)).join("")}
        </div>
        <div class="card-action">
          <span class="elapsed"></span>
          <span class="total-wait"></span>
          ${REVERT_LABEL[order.status]
            ? `<button class="btn btn-revert" data-group-revert="${order.id}"
                  title="退回上一個狀態">${REVERT_LABEL[order.status]}</button>`
            : ""}
          <button class="btn btn-small btn-ghost" data-expand="${order.id}">
            ${open ? "收合品項 ▴" : "分項處理 ▾"}
          </button>
          ${NEXT_STATUS[order.status]
            ? `<button class="btn btn-small btn-primary" data-group-next="${order.id}"
                    data-next-status="${NEXT_STATUS[order.status]}">
                 ${NEXT_LABEL[order.status]}
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

  function doneCard(order) {
    // 沿用同一張卡片，後面接四個階段的時間
    return card(order).replace("</li>", `${stageTimes(order)}</li>`);
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
    // 已完成／已送達只取當日，否則會把開店至今所有訂單都列出來
    const today = await apiGet("/orders?today=true");
    const done = today
      .filter((o) => o.status === "completed" || o.status === "delivered")
      .reverse(); // 新的在前
    doneCount.textContent = done.length;
    doneCount.hidden = done.length === 0;
    doneList.innerHTML = done.length
      ? done.map(doneCard).join("")
      : '<li class="placeholder">本日還沒有完成的訂單</li>';
    updateAndon();
    updateTotalWait();
  }

  // 綁在 body：第四區塊在另一個 <main> 裡，綁單一個 main 會接不到
  document.body.addEventListener("click", async (e) => {
    const btn = e.target.closest("button");
    if (!btn) return;
    const { expand, groupNext, itemNext, group, nextStatus,
            groupRevert, itemRevert } = btn.dataset;

    // 展開／收合只影響畫面
    if (expand !== undefined) {
      const id = Number(expand);
      expanded.has(id) ? expanded.delete(id) : expanded.add(id);
      await refresh();
      return;
    }

    try {
      if (groupRevert !== undefined) {
        // 誤觸要能快速修正，所以不做二次確認
        await apiSend("PATCH", `/orders/${groupRevert}/revert`);
      } else if (itemRevert !== undefined) {
        await apiSend("PATCH", `/orders/${group}/items/${itemRevert}/revert`);
      } else if (groupNext !== undefined) {
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
  // 總等待時間同一個節奏更新（10 秒一次，至少每分鐘會跳一次數字）
  setInterval(updateTotalWait, 10000);
});
