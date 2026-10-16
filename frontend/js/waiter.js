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

document.addEventListener("DOMContentLoaded", () => {
  const tableSelect = document.getElementById("table-number");
  const categorySelect = document.getElementById("category-select");
  const drinkSelect = document.getElementById("drink-select");
  const addForm = document.getElementById("add-item-form");
  const qtyValue = document.getElementById("quantity-value");
  const qtyMinus = document.getElementById("qty-minus");
  const qtyPlus = document.getElementById("qty-plus");
  const specialInput = document.getElementById("special-request");
  const cartList = document.getElementById("cart-list");
  const cartCount = document.getElementById("cart-count");
  const submitBtn = document.getElementById("cart-submit");
  const clearBtn = document.getElementById("cart-clear");
  const orderList = document.getElementById("order-list");
  const deliverList = document.getElementById("queue-completed");
  const deliverCount = document.getElementById("deliver-count");
  const unpaidCount = document.getElementById("unpaid-count");

  let availableDrinks = []; // 供修改表單重建下拉選單
  let cart = []; // 本次訂單的品項：{drink_id, drink_name, quantity, special_request}
  let itemQty = 1; // 加減器目前的數量，最小 1
  let editingId = null; // 正在編輯中的訂單 id
  let pendingRefresh = false; // 編輯期間收到的更新，等編輯結束再套用
  const expandedDeliver = new Set(); // 待送達卡片展開狀態，重繪後要保持
  const expandedSettled = new Set(); // 已結清訂單被點開的，重繪後要保持
  const alerted = new Set(); // 已經跳過超時提醒的訂單，key = 訂單id:狀態

  setupAudioUnlock();
  const showModal = setupAndonModal();

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

  // ---------------- 數量加減器 ----------------
  function setQty(n) {
    itemQty = Math.max(1, n); // 不能減到 0 以下
    qtyValue.textContent = itemQty;
    qtyMinus.disabled = itemQty <= 1;
  }
  qtyMinus.addEventListener("click", () => setQty(itemQty - 1));
  qtyPlus.addEventListener("click", () => setQty(itemQty + 1));

  // ---------------- 本次訂單（購物車） ----------------
  function renderCart() {
    const cups = cart.reduce((n, it) => n + it.quantity, 0);
    cartCount.textContent = cart.length ? `（${cart.length} 品項 / 共 ${cups} 杯）` : "";
    submitBtn.disabled = cart.length === 0;
    clearBtn.disabled = cart.length === 0;

    cartList.innerHTML = cart.length
      ? cart
          .map(
            (it, idx) => `
            <li class="cart-item">
              <div class="cart-row">
                <span class="cart-name">${escapeHtml(it.drink_name)}</span>
                <span class="cart-stepper">
                  <button class="stepper-btn" data-step="${idx}" data-delta="-1"
                          aria-label="減少數量" ${it.quantity <= 1 ? "disabled" : ""}>−</button>
                  <span class="cart-qty-value">${it.quantity}</span>
                  <button class="stepper-btn" data-step="${idx}" data-delta="1"
                          aria-label="增加數量">＋</button>
                </span>
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
    const quantity = itemQty;
    const note = specialInput.value.trim() || null;

    // 同一款飲料且需求相同就直接加數量，不另開一列
    const same = cart.find(
      (it) => it.drink_id === drink.id && (it.special_request ?? null) === note
    );
    if (same) same.quantity += quantity;
    else
      cart.push({
        drink_id: drink.id,
        drink_name: drink.name,
        quantity,
        special_request: note,
      });

    setQty(1);
    specialInput.value = "";
    renderCart();
  });

  // 車內品項的加減、移除
  cartList.addEventListener("click", (e) => {
    const btn = e.target.closest("button");
    if (!btn) return;
    const { remove, step, delta } = btn.dataset;
    if (remove !== undefined) {
      cart.splice(Number(remove), 1);
    } else if (step !== undefined) {
      const item = cart[Number(step)];
      item.quantity = Math.max(1, item.quantity + Number(delta)); // 最少留 1
    } else return;
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
    submitBtn.disabled = true;
    try {
      await apiSend("POST", "/orders", {
        table_number: tableSelect.value,
        items: cart.map(({ drink_id, quantity, special_request }) => ({
          drink_id,
          quantity,
          special_request,
        })),
      });
      cart = [];
      renderCart();
      await refreshOrders();
    } catch (err) {
      alert(`下單失敗：${err.message}`);
      submitBtn.disabled = false;
    }
  });

  // ---------------- 待送達佇列 ----------------
  // 卡片與操作方式沿用原本吧台頁的待送達欄位：整張切換，或展開逐項切換。
  // 狀態切換打的是同一組既有 API，後端邏輯（含寫入 delivered_at）完全沒變。
  function deliverItemRow(order, item) {
    const canDeliver = item.status === "completed";
    return `
      <div class="bar-item ${canDeliver ? "" : "item-done"}">
        <span class="bar-item-name">${escapeHtml(item.drink_name)} × ${item.quantity}</span>
        ${statusBadge(item.status)}
        ${item.special_request
          ? `<span class="order-note">📝 ${escapeHtml(item.special_request)}</span>`
          : ""}
        ${canDeliver
          ? `<button class="btn btn-small btn-ghost"
                data-item-next="${item.id}" data-group="${order.id}"
                data-next-status="${DELIVER_NEXT}">${DELIVER_LABEL}</button>`
          : ""}
      </div>`;
  }

  function deliverCard(order) {
    const ref = order.completed_at ?? order.placed_at;
    const open = expandedDeliver.has(order.id);
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
          ${order.items.map((i) => deliverItemRow(order, i)).join("")}
        </div>
        <div class="card-action">
          <span class="elapsed"></span>
          <button class="btn btn-small btn-ghost" data-expand="${order.id}">
            ${open ? "收合品項 ▴" : "分項處理 ▾"}
          </button>
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
  }

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

  deliverList.addEventListener("click", async (e) => {
    const btn = e.target.closest("button");
    if (!btn) return;
    const { expand, groupNext, itemNext, group, nextStatus } = btn.dataset;

    if (expand !== undefined) {
      const id = Number(expand);
      expandedDeliver.has(id) ? expandedDeliver.delete(id) : expandedDeliver.add(id);
      await refreshOrders();
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
      await refreshOrders();
    } catch (err) {
      alert(`狀態更新失敗：${err.message}`);
      await refreshOrders(); // 可能被別台裝置先更新了，重抓一次
    }
  });

  // ---------------- 已送出的訂單 ----------------
  function itemLines(order) {
    return order.items
      .map(
        (i) => `
        <div class="order-item-line">
          <span>${escapeHtml(i.drink_name)} × ${i.quantity}</span>
          ${statusBadge(i.status)}
          ${i.special_request
            ? `<span class="order-note">📝 ${escapeHtml(i.special_request)}</span>`
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

  function settledRow(o) {
    const open = expandedSettled.has(o.id);
    return `
      <li data-order="${o.id}" class="order-settled ${open ? "open" : ""}">
        <button class="settled-row" data-toggle-settled="${o.id}"
                aria-expanded="${open}">
          <span class="settled-caret">${open ? "▴" : "▾"}</span>
          <strong>${escapeHtml(tableLabel(o.table_number))}</strong>
          <span class="order-summary">${o.total_quantity} 杯</span>
          <span class="badge badge-paid">已付款</span>
          <span class="order-time">${formatTime(o.placed_at)}</span>
        </button>
        ${open ? itemLines(o) : ""}
      </li>`;
  }

  function orderRow(o) {
    if (isSettled(o)) return settledRow(o);
    const editable = o.status === "new"; // 進入製作後就不能改了
    return `
      <li data-order="${o.id}" class="${o.payment_status === "unpaid" ? "order-unpaid" : ""}">
        <div class="order-row">
          <strong>${escapeHtml(tableLabel(o.table_number))}</strong>
          <span class="order-summary">${o.item_count} 品項 / ${o.total_quantity} 杯</span>
          ${statusBadge(o.status)}
          <span class="badge ${o.payment_status === "paid" ? "badge-paid" : "badge-unpaid"}">
            ${o.payment_status === "paid" ? "已付款" : "未付款"}
          </span>
          ${o.edit_count > 0 ? `<span class="badge badge-edited">已改 ${o.edit_count} 次</span>` : ""}
          <span class="order-time">${formatTime(o.placed_at)}</span>
          ${o.payment_status === "unpaid"
            ? `<button class="btn btn-small btn-primary" data-pay="${o.id}">標記付款</button>`
            : ""}
          ${editable
            ? `<button class="btn btn-small btn-ghost" data-edit="${o.id}">修改</button>
               <button class="btn btn-small btn-danger" data-cancel="${o.id}">取消訂單</button>`
            : ""}
        </div>
        ${itemLines(o)}
      </li>`;
  }

  // 編輯模式：整張訂單的品項就地變成可編輯清單
  function editRow(o) {
    return `
      <li data-order="${o.id}" class="editing">
        <div class="edit-form">
          <strong>${escapeHtml(tableLabel(o.table_number))}　修改訂單 #${o.id}</strong>
          <div id="edit-items">
            ${o.items.map((i) => editItemRow(i)).join("")}
          </div>
          <div class="edit-actions">
            <button class="btn btn-small btn-ghost" data-add-item="${o.id}">＋ 新增品項</button>
            <button class="btn btn-small btn-primary" data-save="${o.id}">儲存修改</button>
            <button class="btn btn-small btn-ghost" data-discard="${o.id}">放棄</button>
          </div>
        </div>
      </li>`;
  }

  function editItemRow(item) {
    return `
      <div class="edit-item" data-edit-item>
        <select data-field="drink">${drinkOptions(item?.drink_id ?? availableDrinks[0]?.id)}</select>
        <input type="number" min="1" value="${item?.quantity ?? 1}" data-field="quantity">
        <input type="text" value="${escapeHtml(item?.special_request ?? "")}"
               data-field="special" placeholder="特殊需求">
        <button class="btn btn-small btn-ghost" data-remove-item>移除</button>
      </div>`;
  }

  async function refreshOrders() {
    const orders = await apiGet("/orders");
    // 待送達永遠要更新：服務生在改單時，吧台可能正好完成了另一張
    renderDeliver(orders);
    // 下方清單在編輯中不重繪，避免使用者打到一半的內容被蓋掉
    if (editingId !== null) {
      pendingRefresh = true;
      return;
    }
    const active = orders.filter((o) => o.status !== "cancelled");
    // 未付款的排最上面（這一欄的重點是收錢），同一組裡新的在前
    const sorted = active.sort((a, b) => {
      const rank = (o) => (o.payment_status === "unpaid" ? 0 : 1);
      return rank(a) - rank(b) || b.id - a.id;
    });
    const unpaid = active.filter((o) => o.payment_status === "unpaid").length;
    unpaidCount.textContent = unpaid ? `${unpaid} 筆未付款` : "全部已收款 ✓";
    orderList.innerHTML = sorted.length
      ? sorted.map(orderRow).join("")
      : '<li class="placeholder">尚無訂單</li>';
  }

  async function openEditor(id) {
    const order = await apiGet(`/orders/${id}`);
    if (order.status !== "new") {
      alert("這筆訂單已進入製作，無法修改");
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

  // 訂單列上的所有動作（事件委派，清單重繪也不會掉監聽）
  orderList.addEventListener("click", async (e) => {
    const btn = e.target.closest("button");
    if (!btn) return;
    const { pay, edit, cancel, save, discard, addItem, removeItem,
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
      } else if (edit) {
        await openEditor(edit);
      } else if (discard) {
        await closeEditor();
      } else if (cancel) {
        if (!confirm(`確定要取消訂單 #${cancel}？食材會退回庫存。`)) return;
        await apiSend("PATCH", `/orders/${cancel}/status`, { status: "cancelled" });
        await refreshOrders();
      } else if (save) {
        const items = [...document.querySelectorAll("#edit-items [data-edit-item]")].map(
          (row) => ({
            drink_id: Number(row.querySelector('[data-field="drink"]').value),
            quantity: Number(row.querySelector('[data-field="quantity"]').value),
            special_request: row.querySelector('[data-field="special"]').value.trim() || null,
          })
        );
        if (items.some((i) => !(i.quantity >= 1))) {
          alert("每個品項的數量至少為 1");
          return;
        }
        await apiSend("PATCH", `/orders/${save}`, { items });
        await closeEditor();
      }
    } catch (err) {
      alert(`操作失敗：${err.message}`);
      if (save || cancel) await closeEditor();
    }
  });

  loadDrinks();
  setQty(1);
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
});
