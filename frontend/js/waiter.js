// 服務生頁面：購物車式點餐（一張訂單可含多種飲料）、選桌號、標記付款
// 訂單在進入「製作中」之前可以修改品項或取消，
// 每次修改後端會把 edit_count +1，作為輸入錯誤率的量測依據

document.addEventListener("DOMContentLoaded", () => {
  const tableSelect = document.getElementById("table-number");
  const drinkSelect = document.getElementById("drink-select");
  const addForm = document.getElementById("add-item-form");
  const quantityInput = document.getElementById("quantity");
  const specialInput = document.getElementById("special-request");
  const cartList = document.getElementById("cart-list");
  const cartCount = document.getElementById("cart-count");
  const submitBtn = document.getElementById("cart-submit");
  const clearBtn = document.getElementById("cart-clear");
  const orderList = document.getElementById("order-list");

  let availableDrinks = []; // 供修改表單重建下拉選單
  let cart = []; // 本次訂單的品項：{drink_id, drink_name, quantity, special_request}
  let editingId = null; // 正在編輯中的訂單 id
  let pendingRefresh = false; // 編輯期間收到的更新，等編輯結束再套用

  // 店內共 13 張桌，全部一般桌
  for (let i = 1; i <= TABLE_COUNT; i++) {
    tableSelect.add(new Option(`桌 ${i}`, i));
  }

  async function loadDrinks() {
    const drinks = await apiGet("/drinks");
    availableDrinks = drinks.filter((d) => d.is_available);
    const keep = drinkSelect.value;
    drinkSelect.innerHTML = "";
    availableDrinks.forEach((d) =>
      drinkSelect.add(new Option(`${d.name}（${d.category}）`, d.id))
    );
    if (availableDrinks.some((d) => String(d.id) === keep)) drinkSelect.value = keep;
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
                <input type="number" class="cart-qty" min="1" value="${it.quantity}"
                       data-qty="${idx}" title="數量">
                <button class="btn btn-small btn-danger" data-remove="${idx}">移除</button>
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
    const quantity = Number(quantityInput.value);
    if (!(quantity >= 1)) return;
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

    quantityInput.value = 1;
    specialInput.value = "";
    renderCart();
  });

  // 車內品項的修改與移除
  cartList.addEventListener("click", (e) => {
    const idx = e.target.dataset.remove;
    if (idx === undefined) return;
    cart.splice(Number(idx), 1);
    renderCart();
  });
  cartList.addEventListener("change", (e) => {
    const { qty, note } = e.target.dataset;
    if (qty !== undefined) {
      const n = Number(e.target.value);
      if (n >= 1) cart[Number(qty)].quantity = n;
      renderCart();
    } else if (note !== undefined) {
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
        table_number: Number(tableSelect.value),
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

  function orderRow(o) {
    const editable = o.status === "new"; // 進入製作後就不能改了
    return `
      <li data-order="${o.id}">
        <div class="order-row">
          <strong>桌 ${o.table_number}</strong>
          <span class="order-summary">${o.item_count} 品項 / ${o.total_quantity} 杯</span>
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
        ${itemLines(o)}
      </li>`;
  }

  // 編輯模式：整張訂單的品項就地變成可編輯清單
  function editRow(o) {
    return `
      <li data-order="${o.id}" class="editing">
        <div class="edit-form">
          <strong>桌 ${o.table_number}　修改訂單 #${o.id}</strong>
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
        <button class="btn btn-small btn-danger" data-remove-item>移除</button>
      </div>`;
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
    pendingRefresh = false;
    await refreshOrders();
  }

  // 訂單列上的所有動作（事件委派，清單重繪也不會掉監聽）
  orderList.addEventListener("click", async (e) => {
    const btn = e.target.closest("button");
    if (!btn) return;
    const { pay, edit, cancel, save, discard, addItem, removeItem } = btn.dataset;

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
});
