// 服務生頁面：
//  1. 待送達佇列（置頂）：吧台標記完成後出現在這裡，送到桌邊後按鈕切成「已送達」。
//     送酒是服務生的工作，所以「完成後未送達」的 Andon 超時也在這一頁觸發。
//  2. 購物車式點餐（一張訂單可含多種飲料）、選桌號、標記付款
// 訂單在進入「製作中」之前可以修改品項或取消，
// 每次修改後端會把 edit_count +1，作為輸入錯誤率的量測依據

// 待送達的下一步只有「已送達」一種
const DELIVER_NEXT = "delivered";
const DELIVER_LABEL = "已送出 🚚";
const DELIVER_REASON = (m) => `完成後 ${m} 分鐘未送達`;
// 待送達在角色分工時搬到這一頁，所以「退回製作中」也放在這裡
const REVERT_LABEL = "↩ 退回製作中";

// 購物車的軟上限：超過這個杯數送出前先問一次。
// 一杯一張單，按一下就是 N 次下單，誤按的代價比以前高
const CART_WARN_SIZE = 10;

// 已完成／已送達區塊的退回按鈕文字。退回已送達只限當日訂單，後端會擋
const DONE_REVERT_LABEL = {
  completed: "↩ 退回製作中",
  delivered: "↩ 退回待送達",
};

document.addEventListener("DOMContentLoaded", () => {
  const tableSelect = document.getElementById("table-number");
  const categorySelect = document.getElementById("category-select");
  const drinkSelect = document.getElementById("drink-select");
  const addForm = document.getElementById("add-item-form");
  const specialInput = document.getElementById("special-request");
  const cartList = document.getElementById("cart-list");
  const cartCount = document.getElementById("cart-count");
  const submitBtn = document.getElementById("cart-submit");
  const clearBtn = document.getElementById("cart-clear");
  const orderList = document.getElementById("order-list");
  const deliverList = document.getElementById("queue-completed");
  const deliverCount = document.getElementById("deliver-count");
  const unpaidCount = document.getElementById("unpaid-count");
  const doneList = document.getElementById("queue-done");
  const doneToggle = document.getElementById("done-toggle");
  const doneCaret = document.getElementById("done-caret");
  const doneCount = document.getElementById("done-count");

  let availableDrinks = []; // 供修改表單重建下拉選單
  let cart = []; // 待送出的杯：{drink_id, drink_name, quantity, special_request}，一列一張單
  let editingId = null; // 正在編輯中的訂單 id
  let pendingRefresh = false; // 編輯期間收到的更新，等編輯結束再套用
  const expandedSettled = new Set(); // 已結清訂單被點開的，重繪後要保持
  let doneOpen = false; // 整個區塊預設收合
  const alerted = new Set(); // 已經跳過超時提醒的訂單，key = 訂單id:狀態

  setupAudioUnlock();
  const showModal = setupAndonModal();

  doneToggle.addEventListener("click", () => {
    doneOpen = !doneOpen;
    doneList.hidden = !doneOpen;
    doneCaret.textContent = doneOpen ? "▾" : "▸";
    doneToggle.setAttribute("aria-expanded", String(doneOpen));
  });

  // 出餐位置：吧台、站位排在桌 1 ~ 13 之前
  tableOptions().forEach((value) =>
    tableSelect.add(new Option(tableLabel(value), value))
  );

  // 兩段式選酒：先選基酒分類，飲料選單只列該分類的酒。
  // 兩個選單都會在重繪時盡量保留原本的選擇（經理改酒單時會透過 WebSocket 重繪）。
  function renderCategories() {
    const keep = categorySelect.value;
    const categories = sortedCategories(availableDrinks);
    categorySelect.innerHTML = "";
    categories.forEach((c) => {
      const count = availableDrinks.filter((d) => d.category === c).length;
      categorySelect.add(new Option(`${c}（${count}）`, c));
    });
    if (categories.includes(keep)) categorySelect.value = keep;
  }

  function drinksInCategory() {
    return availableDrinks.filter((d) => d.category === categorySelect.value);
  }

  function renderDrinks() {
    const keep = drinkSelect.value;
    const list = drinksInCategory();
    drinkSelect.innerHTML = "";
    // 分類已經在上面選過了，這裡不用再重複標分類
    list.forEach((d) => drinkSelect.add(new Option(d.name, d.id)));
    if (list.some((d) => String(d.id) === keep)) drinkSelect.value = keep;
  }

  async function loadDrinks() {
    const drinks = await apiGet("/drinks");
    availableDrinks = drinks.filter((d) => d.is_available);
    renderCategories();
    renderDrinks();
  }

  categorySelect.addEventListener("change", renderDrinks);

  // 修改訂單的那一列空間有限，不拆成兩個選單，改用 optgroup 分組
  function drinkOptions(selectedId) {
    return sortedCategories(availableDrinks)
      .map((cat) => {
        const options = availableDrinks
          .filter((d) => d.category === cat)
          .map(
            (d) =>
              `<option value="${d.id}" ${d.id === selectedId ? "selected" : ""}>
                 ${escapeHtml(d.name)}</option>`
          )
          .join("");
        return `<optgroup label="${escapeHtml(cat)}">${options}</optgroup>`;
      })
      .join("");
  }

  // ---------------- 本次訂單（購物車） ----------------
  // 一杯酒就是一張訂單，所以購物車每一列固定一杯，同款點兩次就是兩列。
  // 送出時一列送一張單，不再把同款合併成 quantity=2。
  function renderCart() {
    cartCount.textContent = cart.length
      ? `（${cart.length} 杯，將建立 ${cart.length} 張單）`
      : "";
    submitBtn.disabled = cart.length === 0;
    clearBtn.disabled = cart.length === 0;

    cartList.innerHTML = cart.length
      ? cart
          .map(
            (it, idx) => `
            <li class="cart-item">
              <div class="cart-row">
                <span class="cart-seq">${idx + 1}</span>
                <span class="cart-name">${escapeHtml(it.drink_name)}</span>
                <button class="btn btn-small btn-ghost" data-remove="${idx}">移除</button>
              </div>
              <input type="text" class="cart-note" placeholder="特殊需求"
                     value="${escapeHtml(it.special_request ?? "")}" data-note="${idx}">
            </li>`
          )
          .join("")
      : '<li class="placeholder">還沒加入任何品項</li>';
  }

  addForm.addEventListener("submit", (e) => {
    e.preventDefault();
    const drink = availableDrinks.find((d) => String(d.id) === drinkSelect.value);
    if (!drink) return;

    // 同款不合併：一列就是一杯，也就是之後的一張單
    cart.push({
      drink_id: drink.id,
      drink_name: drink.name,
      quantity: 1,
      special_request: specialInput.value.trim() || null,
    });

    specialInput.value = "";
    renderCart();
  });

  // 車內品項的加減、移除
  cartList.addEventListener("click", (e) => {
    const btn = e.target.closest("button");
    if (!btn) return;
    const { remove } = btn.dataset;
    if (remove === undefined) return;
    cart.splice(Number(remove), 1);
    renderCart();
  });
  cartList.addEventListener("change", (e) => {
    const { note } = e.target.dataset;
    if (note !== undefined) {
      cart[Number(note)].special_request = e.target.value.trim() || null;
    }
  });

  clearBtn.addEventListener("click", () => {
    cart = [];
    renderCart();
  });

  submitBtn.addEventListener("click", async () => {
    if (cart.length === 0) return;
    // 一次點太多杯通常是誤按，送出前確認一下——按下去就是 N 次下單
    if (cart.length > CART_WARN_SIZE && !confirm(`將建立 ${cart.length} 張單，確定送出？`)) {
      return;
    }
    submitBtn.disabled = true;

    // 一列一張單，循序送（不並行）：庫存檢查才不會互相搶，
    // 同一批的 placed_at 與 id 也會連號，吧台頁自然排在一起。
    const table = tableSelect.value;
    let done = 0;
    let failure = null;
    for (const { drink_id, quantity, special_request } of cart) {
      try {
        await apiSend("POST", "/orders", {
          table_number: table,
          items: [{ drink_id, quantity, special_request }],
        });
        done += 1;
      } catch (err) {
        failure = err.message;
        break; // 庫存不足之後的杯多半也會失敗，停下來讓服務生決定
      }
    }

    // 成立的已經寫進資料庫了，從購物車拿掉；失敗的那杯和後面的留著
    cart = cart.slice(done);
    renderCart();
    await refreshOrders();

    if (failure !== null) {
      alert(
        `已成立 ${done} 杯。\n` +
          `第 ${done + 1} 杯失敗：${failure}\n\n` +
          `失敗的那杯和後面 ${cart.length - 1} 杯留在購物車裡，` +
          "可以修改後重送，或移除不要的。"
      );
    }
    submitBtn.disabled = cart.length === 0;
  });

  // ---------------- 待送達佇列 ----------------
  // 卡片與操作方式沿用原本吧台頁的待送達欄位：整張切換，或展開逐項切換。
  // 狀態切換打的是同一組既有 API，後端邏輯（含寫入 delivered_at）完全沒變。
  function deliverCard(order) {
    const ref = order.completed_at ?? order.placed_at;
    const mixed = new Set(order.items.map((i) => i.status)).size > 1;
    // 吧台搆得到的位置提示一下，免得吧台已經遞出去了服務生又跑一趟。
    // 只是標示，按鈕照樣留著——站位這種兩邊都可能送的，誰按都算數。
    const barHint = barDeliveryHint(order.table_number);
    return `
      <li data-id="${order.id}" data-status="${order.status}" data-ref="${ref}"
          data-table="${escapeHtml(order.table_number)}"
          data-placed="${escapeHtml(order.placed_at)}"
          data-summary="${escapeHtml(order.summary)}">
        <div class="order-row card-head">
          <strong>${escapeHtml(tableLabel(order.table_number))}</strong>
          <span class="card-count">${order.item_count} 品項 / ${order.total_quantity} 杯</span>
          ${mixed ? '<span class="badge badge-preparing">品項進度不一</span>' : ""}
          ${barHint
            ? `<span class="badge badge-bar-deliver"
                     title="這個位置吧台搆得到，可能已經直接遞給客人了">${barHint}</span>`
            : ""}
          ${order.revert_count > 0
            ? `<span class="badge badge-reverted" title="這張訂單曾經被退回上一步">↩ 已退回</span>`
            : ""}
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
          <button class="btn btn-revert" data-group-revert="${order.id}"
                  title="退回上一個狀態">${REVERT_LABEL}</button>
          <button class="btn btn-small btn-primary" data-group-next="${order.id}"
                  data-next-status="${DELIVER_NEXT}">${DELIVER_LABEL}</button>
        </div>
      </li>`;
  }

  function renderDeliver(orders) {
    const pending = orders.filter((o) => o.status === "completed");
    deliverCount.textContent = pending.length;
    deliverCount.hidden = pending.length === 0;
    deliverList.innerHTML = pending.length
      ? pending.map(deliverCard).join("") // API 已依 placed_at 排序，先完成的先送
      : '<li class="placeholder">目前沒有待送達的訂單</li>';
    updateAndon();
    updateTotalWait();
  }

  // ---------------- 已完成／已送達（當日，回頭核對用） ----------------
  // 這一區只負責「看」和「退回」，不再有往前推進的按鈕——
  // 還沒送出的單在上面的待送達區處理，不要有兩個地方都按得到「已送出」。

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

  function doneCard(order) {
    const mixed = new Set(order.items.map((i) => i.status)).size > 1;
    // 用 waitAttrs：已送達的總等待時間凍結在送達當下，
    // 和下面付款清單同一張單顯示的數字才會一致
    return `
      <li data-id="${order.id}" data-status="${order.status}" ${waitAttrs(order)}>
        <div class="order-row card-head">
          <strong>${escapeHtml(tableLabel(order.table_number))}</strong>
          <span class="card-count">${order.item_count} 品項 / ${order.total_quantity} 杯</span>
          ${statusBadge(order.status)}
          ${mixed ? '<span class="badge badge-preparing">品項進度不一</span>' : ""}
          ${order.revert_count > 0
            ? `<span class="badge badge-reverted" title="這張訂單曾經被退回上一步">↩ 已退回</span>`
            : ""}
          <span class="order-time">${formatTime(order.placed_at)}</span>
          ${totalLabel(order)}
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
          <span class="total-wait"></span>
          ${DONE_REVERT_LABEL[order.status]
            ? `<button class="btn btn-revert" data-group-revert="${order.id}"
                  title="退回上一個狀態">${DONE_REVERT_LABEL[order.status]}</button>`
            : ""}
        </div>
        ${stageTimes(order)}
      </li>`;
  }

  function renderDone(orders) {
    const done = orders
      .filter((o) => o.status === "completed" || o.status === "delivered")
      .reverse(); // 新的在前
    doneCount.textContent = done.length;
    doneCount.hidden = done.length === 0;
    doneList.innerHTML = done.length
      ? done.map(doneCard).join("")
      : '<li class="placeholder">本日還沒有完成的訂單</li>';
    doneList.querySelectorAll("li[data-id]").forEach(renderTotalWait);
  }

  doneList.addEventListener("click", async (e) => {
    const btn = e.target.closest("button");
    if (!btn) return;
    const { groupRevert } = btn.dataset;

    try {
      if (groupRevert !== undefined) {
        // 誤觸要能快速修正，所以不做二次確認
        await apiSend("PATCH", `/orders/${groupRevert}/revert`);
      } else {
        return;
      }
      await refreshOrders();
    } catch (err) {
      alert(`退回失敗：${err.message}`);
      await refreshOrders(); // 可能被別台裝置先更新了，重抓一次
    }
  });

  // Andon：待送達停留超過門檻就整張變紅、跳視窗、響警示音。
  // 門檻與計時基準都沿用 common.js，和原本吧台頁的那一類完全相同。
  function updateAndon() {
    const fresh = [];
    deliverList.querySelectorAll("li[data-id]").forEach((li) => {
      const state = andonState(li.dataset.status, li.dataset.ref);
      if (!state) return;
      const { minutes, limit, overdue } = state;
      li.querySelector(".elapsed").textContent = `已等 ${Math.floor(minutes)} / ${limit} 分`;
      li.classList.toggle("overdue", overdue);

      const key = `${li.dataset.id}:${li.dataset.status}`;
      if (overdue && !alerted.has(key)) {
        alerted.add(key);
        fresh.push({
          table: li.dataset.table,
          summary: li.dataset.summary,
          reason: DELIVER_REASON(limit),
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
    deliverList.querySelectorAll("li[data-id]").forEach(renderTotalWait);
    orderList.querySelectorAll("li[data-order]").forEach(renderTotalWait);
  }

  deliverList.addEventListener("click", async (e) => {
    const btn = e.target.closest("button");
    if (!btn) return;
    const { groupNext, nextStatus, groupRevert } = btn.dataset;

    try {
      if (groupRevert !== undefined) {
        // 誤觸要能快速修正，所以不做二次確認
        await apiSend("PATCH", `/orders/${groupRevert}/revert`);
      } else if (groupNext !== undefined) {
        await apiSend("PATCH", `/orders/${groupNext}/status`, { status: nextStatus });
      } else {
        return;
      }
      await refreshOrders();
    } catch (err) {
      alert(`狀態更新失敗：${err.message}`);
      await refreshOrders(); // 可能被別台裝置先更新了，重抓一次
    }
  });

  // ---------------- 已送出的訂單 ----------------
  // 訂單總額 = Σ(unit_price × 數量)。
  // 只要有任何一個品項沒有價格，整筆就顯示「—」——這時候加總出來的數字
  // 一定少算，寧可不顯示也不要給錯的金額。已取消的品項不計入。
  function orderTotal(order) {
    const billable = order.items.filter((i) => i.status !== "cancelled");
    if (!billable.length) return null;
    if (billable.some((i) => i.unit_price == null)) return null;
    return billable.reduce((sum, i) => sum + i.unit_price * i.quantity, 0);
  }

  function totalLabel(order) {
    const total = orderTotal(order);
    return `<span class="order-total">${total == null ? "—" : `NT$ ${total}`}</span>`;
  }

  function itemLines(order) {
    return order.items
      .map(
        (i) => `
        <div class="order-item-line">
          <span>${escapeHtml(i.drink_name)} × ${i.quantity}</span>
          ${statusBadge(i.status)}
          ${i.special_request
            ? `<span class="order-note">備註：${escapeHtml(i.special_request)}</span>`
            : ""}
        </div>`
      )
      .join("");
  }

  // 收完錢又送達的訂單已經沒有要處理的事，縮成一行，點一下才展開品項，
  // 把版面留給還需要注意的訂單
  function isSettled(o) {
    return o.payment_status === "paid" && o.status === "delivered";
  }

  // 已送達的訂單等待已經結束，帶上 data-ended 讓總等待時間凍結在送達當下
  function waitAttrs(o) {
    return `data-placed="${escapeHtml(o.placed_at)}"` +
      (o.delivered_at ? ` data-ended="${escapeHtml(o.delivered_at)}"` : "");
  }

  function settledRow(o) {
    const open = expandedSettled.has(o.id);
    return `
      <li data-order="${o.id}" class="order-settled ${open ? "open" : ""}"
          ${waitAttrs(o)}>
        <button class="settled-row" data-toggle-settled="${o.id}"
                aria-expanded="${open}">
          <span class="settled-caret">${open ? "▴" : "▾"}</span>
          <strong>${escapeHtml(tableLabel(o.table_number))}</strong>
          <span class="order-summary">${o.total_quantity} 杯</span>
          <span class="badge badge-paid">已付款</span>
          <span class="order-time">${formatTime(o.placed_at)}</span>
          <span class="total-wait"></span>
          ${totalLabel(o)}
        </button>
        ${open ? itemLines(o) : ""}
      </li>`;
  }

  function orderRow(o) {
    if (isSettled(o)) return settledRow(o);
    // 品項進入製作後就不能改了；桌號到送達前都還能改（客人換位子）
    const editable = o.status === "new";
    const canEditTable = o.status !== "delivered";
    return `
      <li data-order="${o.id}" class="${o.payment_status === "unpaid" ? "order-unpaid" : ""}"
          ${waitAttrs(o)}>
        <div class="order-row">
          <strong>${escapeHtml(tableLabel(o.table_number))}</strong>
          <span class="order-summary">${o.item_count} 品項 / ${o.total_quantity} 杯</span>
          ${statusBadge(o.status)}
          <span class="badge ${o.payment_status === "paid" ? "badge-paid" : "badge-unpaid"}">
            ${o.payment_status === "paid" ? "已付款" : "未付款"}
          </span>
          ${o.edit_count > 0 ? `<span class="badge badge-edited">已改 ${o.edit_count} 次</span>` : ""}
          <span class="order-time">${formatTime(o.placed_at)}</span>
          <span class="total-wait"></span>
          ${totalLabel(o)}
          ${o.payment_status === "unpaid"
            ? `<button class="btn btn-small btn-primary" data-pay="${o.id}">標記付款</button>`
            : ""}
          ${canEditTable
            ? `<button class="btn btn-small btn-ghost" data-edit="${o.id}">${
                editable ? "修改" : "改桌號"
              }</button>`
            : ""}
          ${editable
            ? `<button class="btn btn-small btn-danger" data-cancel="${o.id}">取消訂單</button>`
            : ""}
        </div>
        ${itemLines(o)}
      </li>`;
  }

  function tableSelectOptions(selected) {
    return tableOptions()
      .map(
        (v) => `<option value="${escapeHtml(v)}" ${v === selected ? "selected" : ""}>${
          escapeHtml(tableLabel(v))
        }</option>`
      )
      .join("");
  }

  // 編輯模式：整張訂單的品項就地變成可編輯清單。
  // 已經開始製作的訂單只開放改桌號，品項區塊整個不渲染。
  function editRow(o) {
    const canEditItems = o.status === "new";
    return `
      <li data-order="${o.id}" class="editing">
        <div class="edit-form">
          <strong>${canEditItems ? "修改訂單" : "改桌號"} #${o.id}</strong>
          <label class="field">桌號
            <select id="edit-table">${tableSelectOptions(o.table_number)}</select>
          </label>
          ${canEditItems
            ? `<div id="edit-items">
                 ${o.items.map((i) => editItemRow(i)).join("")}
               </div>`
            : '<p class="edit-hint">已經開始製作，只能改桌號。</p>'}
          <div class="edit-actions">
            ${canEditItems
              ? `<button class="btn btn-small btn-ghost" data-add-item="${o.id}">＋ 新增品項</button>`
              : ""}
            <button class="btn btn-small btn-primary" data-save="${o.id}">儲存修改</button>
            <button class="btn btn-small btn-ghost" data-discard="${o.id}">放棄</button>
          </div>
        </div>
      </li>`;
  }

  function editItemRow(item) {
    // 新的單一律一杯，數量不給改；但 10/16 之前的舊單可能是多杯，
    // 那就把數字原樣帶回去送出，不要被壓成 1 讓杯數憑空消失。
    const quantity = item?.quantity ?? 1;
    return `
      <div class="edit-item" data-edit-item>
        <select data-field="drink">${drinkOptions(item?.drink_id ?? availableDrinks[0]?.id)}</select>
        <input type="hidden" value="${quantity}" data-field="quantity">
        ${quantity > 1 ? `<span class="edit-qty" title="舊格式的多杯訂單">× ${quantity}</span>` : ""}
        <input type="text" value="${escapeHtml(item?.special_request ?? "")}"
               data-field="special" placeholder="特殊需求">
        <button class="btn btn-small btn-ghost" data-remove-item>移除</button>
      </div>`;
  }

  async function refreshOrders() {
    const orders = await apiGet("/orders");
    // 待送達永遠要更新：服務生在改單時，吧台可能正好完成了另一張
    renderDeliver(orders);
    // 已完成／已送達只取當日，否則會把開店至今所有訂單都列出來
    renderDone(await apiGet("/orders?today=true"));
    // 下方清單在編輯中不重繪，避免使用者打到一半的內容被蓋掉
    if (editingId !== null) {
      pendingRefresh = true;
      return;
    }
    const active = orders.filter((o) => o.status !== "cancelled");
    const unpaidOrders = active.filter((o) => o.payment_status === "unpaid");
    const paidOrders = active
      .filter((o) => o.payment_status !== "unpaid")
      .sort((a, b) => b.id - a.id);

    unpaidCount.textContent = unpaidOrders.length
      ? `${unpaidOrders.length} 筆未付款`
      : "全部已收款 ✓";

    // 未付款的依桌分組排在最上面（這一欄的重點是收錢），
    // 已付款的照舊新的在前。分組只影響顯示順序，不動任何資料。
    const rows = [
      ...groupByTable(unpaidOrders).map(
        (group) =>
          (group.length > 1 ? tableGroupRow(group) : "") +
          group.map(orderRow).join("")
      ),
      ...paidOrders.map(orderRow),
    ];
    orderList.innerHTML = rows.length
      ? rows.join("")
      : '<li class="placeholder">尚無訂單</li>';
    updateTotalWait();
  }

  // 同一桌的單收在一起，最近下單的那桌排最前面；桌內新的在前
  function groupByTable(orders) {
    const byTable = new Map();
    for (const o of orders) {
      if (!byTable.has(o.table_number)) byTable.set(o.table_number, []);
      byTable.get(o.table_number).push(o);
    }
    return [...byTable.values()]
      .map((group) => group.sort((a, b) => b.id - a.id))
      .sort((a, b) => b[0].id - a[0].id);
  }

  // 一桌有兩張以上未付款時多一條標題列：小計加上「整桌標記付款」。
  // 單張的按鈕照樣留著，同一桌也可能是不同組客人分開付。
  function tableGroupRow(group) {
    const totals = group.map(orderTotal);
    const subtotal = totals.some((t) => t == null)
      ? "—"
      : `NT$ ${totals.reduce((sum, t) => sum + t, 0)}`;
    const cups = group.reduce((n, o) => n + o.total_quantity, 0);
    return `
      <li class="table-group">
        <strong>${escapeHtml(tableLabel(group[0].table_number))}</strong>
        <span class="order-summary">${group.length} 張單 / ${cups} 杯</span>
        <span class="order-total">${subtotal}</span>
        <button class="btn btn-small btn-primary"
                data-pay-table="${escapeHtml(group[0].table_number)}">
          整桌標記付款
        </button>
      </li>`;
  }

  async function openEditor(id) {
    const order = await apiGet(`/orders/${id}`);
    if (order.status === "delivered" || order.status === "cancelled") {
      alert("這筆訂單已經送達或取消，無法修改");
      return refreshOrders();
    }
    editingId = id;
    const li = orderList.querySelector(`li[data-order="${id}"]`);
    if (li) li.outerHTML = editRow(order);
  }

  async function closeEditor() {
    editingId = null;
    pendingRefresh = false;
    await refreshOrders();
  }

  // 整桌標記付款：按下去才重新抓一次訂單，只標記「現在還沒付款」的那幾張。
  // 這樣重複按不會把已經付款的單再蓋一次 payment_completed_at，
  // 別台裝置剛好先收了錢也不會被覆寫；中途失敗會說清楚哪幾張沒標到。
  async function payWholeTable(table, btn) {
    btn.disabled = true;
    const fresh = await apiGet("/orders");
    const pending = fresh.filter(
      (o) =>
        o.table_number === table &&
        o.payment_status === "unpaid" &&
        o.status !== "cancelled"
    );
    if (!pending.length) {
      await refreshOrders();
      alert(`${tableLabel(table)} 已經沒有未付款的單了。`);
      return;
    }

    const done = [];
    const failed = [];
    for (const order of pending) {
      try {
        await apiSend("PATCH", `/orders/${order.id}/payment`, {
          payment_status: "paid",
        });
        done.push(order.id);
      } catch (err) {
        failed.push(`#${order.id}（${err.message}）`);
      }
    }
    await refreshOrders();

    if (failed.length) {
      alert(
        `${tableLabel(table)}：已標記 ${done.length} 張` +
          (done.length ? `（#${done.join("、#")}）` : "") +
          `\n未標記 ${failed.length} 張：\n${failed.join("\n")}\n\n` +
          "沒標到的單還在清單上，可以單張再按一次。"
      );
    }
  }

  // 訂單列上的所有動作（事件委派，清單重繪也不會掉監聽）
  orderList.addEventListener("click", async (e) => {
    const btn = e.target.closest("button");
    if (!btn) return;
    const { pay, payTable, edit, cancel, save, discard, addItem, removeItem,
            toggleSettled } = btn.dataset;

    // 已結清訂單的展開／收合只影響畫面
    if (toggleSettled !== undefined) {
      const id = Number(toggleSettled);
      expandedSettled.has(id) ? expandedSettled.delete(id) : expandedSettled.add(id);
      await refreshOrders();
      return;
    }

    // 編輯表單內的加／減品項只動畫面，不打 API
    if (addItem !== undefined) {
      document
        .getElementById("edit-items")
        .insertAdjacentHTML("beforeend", editItemRow(null));
      return;
    }
    if (removeItem !== undefined) {
      const rows = document.querySelectorAll("#edit-items [data-edit-item]");
      if (rows.length <= 1) {
        alert("訂單至少要有一個品項；要整張取消請按「放棄」後選取消訂單");
        return;
      }
      btn.closest("[data-edit-item]").remove();
      return;
    }

    try {
      if (pay) {
        await apiSend("PATCH", `/orders/${pay}/payment`, { payment_status: "paid" });
        await refreshOrders();
      } else if (payTable !== undefined) {
        await payWholeTable(payTable, btn);
      } else if (edit) {
        await openEditor(edit);
      } else if (discard) {
        await closeEditor();
      } else if (cancel) {
        if (!confirm(`確定要取消訂單 #${cancel}？食材會退回庫存。`)) return;
        await apiSend("PATCH", `/orders/${cancel}/status`, { status: "cancelled" });
        await refreshOrders();
      } else if (save) {
        const payload = {
          table_number: document.getElementById("edit-table").value,
        };
        // 已開始製作的訂單沒有品項區塊，這時只送桌號
        const rows = [...document.querySelectorAll("#edit-items [data-edit-item]")];
        if (rows.length) {
          const items = rows.map((row) => ({
            drink_id: Number(row.querySelector('[data-field="drink"]').value),
            quantity: Number(row.querySelector('[data-field="quantity"]').value),
            special_request: row.querySelector('[data-field="special"]').value.trim() || null,
          }));
          if (items.some((i) => !(i.quantity >= 1))) {
            alert("每個品項的數量至少為 1");
            return;
          }
          payload.items = items;
        }
        await apiSend("PATCH", `/orders/${save}`, payload);
        await closeEditor();
      }
    } catch (err) {
      alert(`操作失敗：${err.message}`);
      if (save || cancel) await closeEditor();
    }
  });

  loadDrinks();
  renderCart();
  // 即時更新：訂單事件刷新訂單列表；經理端改動酒單時同步更新下拉選單；
  // 重連成功時補抓斷線期間的變化
  connectWebSocket(
    (msg) => {
      if (msg.event?.startsWith("order_")) refreshOrders();
      if (msg.event?.startsWith("drink_")) loadDrinks();
    },
    () => { loadDrinks(); refreshOrders(); }
  );

  // Andon 每 10 秒重算一次（不用重抓資料，只更新等待時間與變紅）
  setInterval(updateAndon, 10000);
  // 總等待時間同一個節奏更新（10 秒一次，至少每分鐘會跳一次數字）
  setInterval(updateTotalWait, 10000);
});
