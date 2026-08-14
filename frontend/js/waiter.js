// 服務生頁面：點餐、選桌號、標記付款
// 訂單在進入「製作中」之前可以修改（品項／數量／特殊需求）或取消，
// 每次修改後端會把 edit_count +1，作為輸入錯誤率的量測依據

document.addEventListener("DOMContentLoaded", () => {
  const tableSelect = document.getElementById("table-number");
  const drinkSelect = document.getElementById("drink-select");
  const form = document.getElementById("order-form");
  const orderList = document.getElementById("order-list");

  let availableDrinks = []; // 供修改表單重建下拉選單
  let editingId = null; // 正在編輯中的訂單 id
  let pendingRefresh = false; // 編輯期間收到的更新，等編輯結束再套用

  // 桌號 1–20 先寫死，之後可改成設定
  for (let i = 1; i <= 20; i++) {
    tableSelect.add(new Option(`桌 ${i}`, i));
  }

  async function loadDrinks() {
    const drinks = await apiGet("/drinks");
    availableDrinks = drinks.filter((d) => d.is_available);
    drinkSelect.innerHTML = "";
    availableDrinks.forEach((d) =>
      drinkSelect.add(new Option(`${d.name}（${d.category}）`, d.id))
    );
  }

  function drinkOptions(selectedId) {
    return availableDrinks
      .map(
        (d) =>
          `<option value="${d.id}" ${d.id === selectedId ? "selected" : ""}>
             ${escapeHtml(d.name)}（${escapeHtml(d.category)}）</option>`
      )
      .join("");
  }

  // 一般顯示模式的訂單列
  function orderRow(o) {
    const editable = o.status === "new"; // 進入製作後就不能改了
    return `
      <li data-order="${o.id}">
        <div class="order-row">
          <strong>桌 ${o.table_number}</strong>
          <span>${escapeHtml(o.drink_name)} × ${o.quantity}</span>
          ${statusBadge(o.status)}
          <span class="badge ${o.payment_status === "paid" ? "badge-paid" : "badge-unpaid"}">
            ${o.payment_status === "paid" ? "已付款" : "未付款"}
          </span>
          ${o.edit_count > 0 ? `<span class="badge badge-edited">已改 ${o.edit_count} 次</span>` : ""}
          <span class="order-time">${formatTime(o.placed_at)}</span>
          ${o.payment_status === "unpaid"
            ? `<button class="btn btn-small" data-pay="${o.id}">標記付款</button>`
            : ""}
          ${editable
            ? `<button class="btn btn-small btn-ghost" data-edit="${o.id}">修改</button>
               <button class="btn btn-small btn-danger" data-cancel="${o.id}">取消訂單</button>`
            : ""}
        </div>
        ${o.special_request
          ? `<div class="order-note">📝 ${escapeHtml(o.special_request)}</div>`
          : ""}
      </li>`;
  }

  // 編輯模式的訂單列（就地展開表單）
  function editRow(o) {
    return `
      <li data-order="${o.id}" class="editing">
        <div class="edit-form">
          <strong>桌 ${o.table_number}　修改訂單 #${o.id}</strong>
          <label>飲料
            <select data-field="drink">${drinkOptions(o.drink_id)}</select>
          </label>
          <label>數量
            <input type="number" min="1" value="${o.quantity}" data-field="quantity">
          </label>
          <label>特殊需求
            <input type="text" value="${escapeHtml(o.special_request ?? "")}"
                   data-field="special" placeholder="例：少冰、去薄荷">
          </label>
          <div class="edit-actions">
            <button class="btn btn-small btn-primary" data-save="${o.id}">儲存修改</button>
            <button class="btn btn-small btn-ghost" data-discard="${o.id}">放棄</button>
          </div>
        </div>
      </li>`;
  }

  async function refreshOrders() {
    // 編輯中不重繪，避免使用者打到一半的內容被蓋掉
    if (editingId !== null) {
      pendingRefresh = true;
      return;
    }
    const orders = await apiGet("/orders");
    const active = orders.filter((o) => o.status !== "cancelled").reverse(); // 新的在上面
    orderList.innerHTML = active.length
      ? active.map(orderRow).join("")
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
    if (pendingRefresh) pendingRefresh = false;
    await refreshOrders();
  }

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await apiSend("POST", "/orders", {
        table_number: Number(tableSelect.value),
        drink_id: Number(drinkSelect.value),
        quantity: Number(document.getElementById("quantity").value),
        special_request: document.getElementById("special-request").value || null,
      });
      document.getElementById("quantity").value = 1;
      document.getElementById("special-request").value = "";
      await refreshOrders();
    } catch (err) {
      alert(`下單失敗：${err.message}`);
    }
  });

  // 訂單列上的所有動作（事件委派，清單重繪也不會掉監聽）
  orderList.addEventListener("click", async (e) => {
    const btn = e.target.closest("button");
    if (!btn) return;
    const { pay, edit, cancel, save, discard } = btn.dataset;

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
        const li = btn.closest("li");
        await apiSend("PATCH", `/orders/${save}`, {
          drink_id: Number(li.querySelector('[data-field="drink"]').value),
          quantity: Number(li.querySelector('[data-field="quantity"]').value),
          special_request: li.querySelector('[data-field="special"]').value || null,
        });
        await closeEditor();
      }
    } catch (err) {
      alert(`操作失敗：${err.message}`);
      if (save || cancel) await closeEditor();
    }
  });

  loadDrinks();
  // 即時更新：訂單事件刷新訂單列表；經理端改動酒單時同步更新下拉選單；
  // 重連成功時補抓斷線期間的變化
  connectWebSocket(
    (msg) => {
      if (msg.event?.startsWith("order_")) refreshOrders();
      if (msg.event?.startsWith("drink_")) loadDrinks();
    },
    () => { loadDrinks(); refreshOrders(); }
  );
});
