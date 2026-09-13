// 經理頁面：儀表板（今日 KPI、熱門品項、超時訂單）+ 訂單總覽 + 庫存警示

document.addEventListener("DOMContentLoaded", () => {
  const kpiRow = document.getElementById("kpi-row");
  const topDrinks = document.getElementById("top-drinks");
  const overdueList = document.getElementById("overdue-list");
  const statsRow = document.getElementById("order-stats");
  const orderBody = document.getElementById("order-table-body");
  const alertList = document.getElementById("stock-alerts");
  const stockBody = document.getElementById("stock-table-body");
  const drinkBody = document.getElementById("drink-table-body");
  const recipeSelect = document.getElementById("recipe-drink-select");
  const recipeBody = document.getElementById("recipe-table-body");
  const recipeHint = document.getElementById("recipe-hint");

  let editingIngredient = null; // 正在編輯的原料 id
  let pendingStockRefresh = false; // 編輯期間收到的更新，等編輯結束再套用
  let editingDrink = null; // 正在編輯的飲品 id
  let drinks = [];
  let allIngredients = [];

  function kpiTile(label, value, warn = false) {
    return `
      <div class="stat-tile ${warn ? "stat-warn" : ""}">
        <div class="stat-label">${label}</div>
        <div class="stat-value">${value}</div>
      </div>`;
  }

  async function refreshStats() {
    const s = await apiGet("/stats");

    kpiRow.innerHTML = [
      kpiTile("今日訂單", s.today_orders),
      kpiTile("售出杯數", s.drinks_sold),
      kpiTile("平均製作時間", s.avg_prep_minutes == null ? "－" : `${s.avg_prep_minutes} 分`),
      kpiTile("未付款", s.unpaid, s.unpaid > 0),
      kpiTile("超時訂單", s.overdue.length, s.overdue.length > 0),
      kpiTile("訂單修改率", `${s.edit_rate}%`, s.edited_orders > 0),
    ].join("");

    // 熱門品項：單一色相水平長條，值直接標在條尾
    if (s.top_drinks.length === 0) {
      topDrinks.innerHTML = '<span class="placeholder">今日尚無售出</span>';
    } else {
      const max = s.top_drinks[0].qty;
      topDrinks.innerHTML = s.top_drinks
        .map(
          (d) => `
          <div class="bar-row" title="${escapeHtml(d.name)}：${d.qty} 杯">
            <span class="bar-name">${escapeHtml(d.name)}</span>
            <span class="bar-track"><span class="bar-fill" style="width:${(d.qty / max) * 100}%"></span></span>
            <span class="bar-value">${d.qty}</span>
          </div>`
        )
        .join("");
    }

    overdueList.innerHTML = s.overdue.length
      ? s.overdue
          .map(
            (o) => `
            <li class="overdue-item">⏱ <strong>桌 ${o.table_number}</strong>
              ${escapeHtml(o.drink_name)} × ${o.quantity}
              ・ ${STATUS_LABELS[o.status]} 已卡 ${o.minutes_stuck} 分
              （門檻 ${o.threshold_minutes} 分）</li>`
          )
          .join("")
      : '<li class="placeholder">目前沒有超時訂單 ✓</li>';
  }

  async function refreshOrders() {
    // 只看今天：每日 23:59 結算後，儀表板的訂單總覽就會歸零重新開始
    const orders = await apiGet("/orders?today=true");

    // 各狀態統計 + 未付款數
    const counts = {};
    let unpaid = 0;
    for (const o of orders) {
      counts[o.status] = (counts[o.status] ?? 0) + 1;
      if (o.payment_status === "unpaid" && o.status !== "cancelled") unpaid++;
    }
    statsRow.innerHTML =
      ["new", "preparing", "completed", "delivered"]
        .map(
          (s) =>
            `<div class="stat-card">${STATUS_LABELS[s]}<br><strong>${counts[s] ?? 0}</strong></div>`
        )
        .join("") +
      `<div class="stat-card ${unpaid ? "stat-warn" : ""}">未付款<br><strong>${unpaid}</strong></div>`;

    if (orders.length === 0) {
      orderBody.innerHTML = '<tr><td colspan="9" class="placeholder">尚無資料</td></tr>';
      return;
    }
    orderBody.innerHTML = [...orders]
      .reverse() // 新的在上面
      .map((o) => {
        // 超時的訂單整列標紅（門檻與吧台 Andon 看板相同）
        const andon = orderAndonState(o);
        return `
        <tr class="${andon?.overdue ? "row-overdue" : ""}"
            data-status="${o.status}" data-ref="${o[ANDON_REF_FIELD[o.status]] ?? o.placed_at}">
          <td>${o.id}</td>
          <td>桌 ${o.table_number}</td>
          <td>${o.items
            .map((i) => `${escapeHtml(i.drink_name)} × ${i.quantity}${
              i.special_request ? `<br><small>📝 ${escapeHtml(i.special_request)}</small>` : ""
            }`)
            .join("<br>")}</td>
          <td>${o.total_quantity}</td>
          <td>${statusBadge(o.status)}</td>
          <td><span class="badge ${o.payment_status === "paid" ? "badge-paid" : "badge-unpaid"}">
            ${o.payment_status === "paid" ? "已付款" : "未付款"}</span></td>
          <td>${formatTime(o.placed_at)}</td>
          <td>${formatTime(o.completed_at) || "—"}</td>
          <td>${formatTime(o.delivered_at) || "—"}</td>
        </tr>`;
      })
      .join("");
  }

  // 訂單會隨時間走到超時，但那不會產生任何事件，所以定期重算整列標紅
  function updateOverdueRows() {
    orderBody.querySelectorAll("tr[data-status]").forEach((tr) => {
      const state = andonState(tr.dataset.status, tr.dataset.ref);
      tr.classList.toggle("row-overdue", Boolean(state?.overdue));
    });
  }

  // 一般顯示模式的原料列
  function stockRow(i) {
    return `
      <tr class="${i.current_stock < i.reorder_point ? "row-low" : ""}">
        <td>${escapeHtml(i.name)}</td>
        <td>${fmtQty(i.current_stock)}</td>
        <td>${escapeHtml(i.unit)}</td>
        <td>${fmtQty(i.reorder_point)}</td>
        <td class="stock-actions">
          <button class="btn btn-small btn-ghost" data-edit-stock="${i.id}">編輯</button>
          <button class="btn btn-small btn-danger" data-delete-stock="${i.id}"
                  data-name="${escapeHtml(i.name)}">刪除</button>
        </td>
      </tr>`;
  }

  // 編輯模式：四個欄位都可以改
  function stockEditRow(i) {
    return `
      <tr class="row-editing" data-editing="${i.id}">
        <td><input type="text" value="${escapeHtml(i.name)}" data-field="name" class="stock-input"></td>
        <td><input type="number" step="0.01" min="0" value="${i.current_stock}"
                   data-field="stock" class="stock-input"></td>
        <td><input type="text" value="${escapeHtml(i.unit)}" data-field="unit" class="stock-input unit-input"></td>
        <td><input type="number" step="0.01" min="0" value="${i.reorder_point}"
                   data-field="reorder" class="stock-input"></td>
        <td class="stock-actions">
          <button class="btn btn-small btn-primary" data-save-stock="${i.id}">儲存</button>
          <button class="btn btn-small btn-ghost" data-cancel-stock="1">取消</button>
        </td>
      </tr>`;
  }

  async function refreshStock() {
    // 編輯中不重繪，避免輸入到一半被蓋掉
    if (editingIngredient !== null) {
      pendingStockRefresh = true;
      return;
    }
    const ingredients = await apiGet("/ingredients");

    const low = ingredients.filter((i) => i.current_stock < i.reorder_point);
    alertList.innerHTML = low.length
      ? low
          .map(
            (i) => `
            <li class="low-stock">⚠️ <strong>${escapeHtml(i.name)}</strong>
              剩 ${fmtQty(i.current_stock)} ${escapeHtml(i.unit)}（補貨點 ${fmtQty(i.reorder_point)}）</li>`
          )
          .join("")
      : '<li class="placeholder">庫存充足 ✓</li>';

    stockBody.innerHTML = ingredients.length
      ? ingredients.map(stockRow).join("")
      : '<tr><td colspan="5" class="placeholder">尚無資料</td></tr>';
  }

  // 原料列的編輯／儲存／取消／刪除（事件委派）
  stockBody.addEventListener("click", async (e) => {
    const btn = e.target.closest("button");
    if (!btn) return;
    const { editStock, saveStock, cancelStock, deleteStock } = btn.dataset;

    try {
      if (editStock) {
        const ingredient = await apiGet(`/ingredients/${editStock}`);
        editingIngredient = Number(editStock);
        btn.closest("tr").outerHTML = stockEditRow(ingredient);
      } else if (cancelStock) {
        await closeStockEditor();
      } else if (deleteStock) {
        if (!confirm(`確定刪除原料「${btn.dataset.name}」？用到它的配方會一併移除。`)) return;
        await apiSend("DELETE", `/ingredients/${deleteStock}`);
        await refreshStock();
        await loadRecipe(); // 配方可能被連帶刪掉，重讀目前選的飲品
      } else if (saveStock) {
        const tr = btn.closest("tr");
        const value = (f) => tr.querySelector(`[data-field="${f}"]`).value;
        const stock = Number(value("stock"));
        const reorder = Number(value("reorder"));
        if (!value("name").trim() || !value("unit").trim()) {
          alert("名稱與單位不可空白");
          return;
        }
        if (!Number.isFinite(stock) || !Number.isFinite(reorder) || stock < 0 || reorder < 0) {
          alert("庫存與補貨點必須是 0 或正數");
          return;
        }
        await apiSend("PATCH", `/ingredients/${saveStock}`, {
          name: value("name").trim(),
          unit: value("unit").trim(),
          current_stock: stock,
          reorder_point: reorder,
        });
        await closeStockEditor();
      }
    } catch (err) {
      alert(`原料更新失敗：${err.message}`);
      await closeStockEditor();
    }
  });

  // 新增原料
  document.getElementById("ingredient-add-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await apiSend("POST", "/ingredients", {
        name: document.getElementById("new-ingredient-name").value.trim(),
        unit: document.getElementById("new-ingredient-unit").value.trim(),
        current_stock: Number(document.getElementById("new-ingredient-stock").value),
        reorder_point: Number(document.getElementById("new-ingredient-reorder").value),
      });
      e.target.reset();
      document.getElementById("new-ingredient-stock").value = 0;
      document.getElementById("new-ingredient-reorder").value = 0;
      await refreshStock();
    } catch (err) {
      alert(`新增原料失敗：${err.message}`);
    }
  });

  async function closeStockEditor() {
    editingIngredient = null;
    pendingStockRefresh = false;
    await refreshStock();
  }

  // ---------------- 飲品管理 ----------------
  function drinkRow(d) {
    return `
      <tr>
        <td>${escapeHtml(d.name)}</td>
        <td>${escapeHtml(d.category)}</td>
        <td>${d.is_available
          ? '<span class="badge badge-paid">供應中</span>'
          : '<span class="badge badge-delivered">已停售</span>'}</td>
        <td class="stock-actions">
          <button class="btn btn-small btn-ghost" data-edit-drink="${d.id}">編輯</button>
          <button class="btn btn-small btn-danger" data-delete-drink="${d.id}"
                  data-name="${escapeHtml(d.name)}">刪除</button>
        </td>
      </tr>`;
  }

  function drinkEditRow(d) {
    return `
      <tr class="row-editing" data-editing-drink="${d.id}">
        <td><input type="text" value="${escapeHtml(d.name)}" data-field="name" class="stock-input"></td>
        <td><input type="text" value="${escapeHtml(d.category)}" data-field="category" class="stock-input"></td>
        <td><label class="inline-check">
          <input type="checkbox" data-field="available" ${d.is_available ? "checked" : ""}> 供應中
        </label></td>
        <td class="stock-actions">
          <button class="btn btn-small btn-primary" data-save-drink="${d.id}">儲存</button>
          <button class="btn btn-small btn-ghost" data-cancel-drink="1">取消</button>
        </td>
      </tr>`;
  }

  async function refreshDrinks() {
    if (editingDrink !== null) return; // 編輯中不重繪
    drinks = await apiGet("/drinks");
    drinkBody.innerHTML = drinks.length
      ? drinks.map(drinkRow).join("")
      : '<tr><td colspan="4" class="placeholder">尚無飲品</td></tr>';

    // 同步配方管理的飲品下拉選單，盡量保留目前選擇
    const keep = recipeSelect.value;
    recipeSelect.innerHTML = drinks
      .map((d) => `<option value="${d.id}">${escapeHtml(d.name)}</option>`)
      .join("");
    if (drinks.some((d) => String(d.id) === keep)) recipeSelect.value = keep;

    // 選中的飲品被刪掉時，選單會自動跳到別杯；程式改動 value 不會觸發 change，
    // 這裡要手動重載，否則畫面上留著前一杯的配方，按下儲存就會寫錯對象
    if (recipeSelect.value !== keep) await loadRecipe();
  }

  drinkBody.addEventListener("click", async (e) => {
    const btn = e.target.closest("button");
    if (!btn) return;
    const { editDrink, saveDrink, cancelDrink, deleteDrink } = btn.dataset;

    try {
      if (editDrink) {
        const drink = await apiGet(`/drinks/${editDrink}`);
        editingDrink = Number(editDrink);
        btn.closest("tr").outerHTML = drinkEditRow(drink);
      } else if (cancelDrink) {
        editingDrink = null;
        await refreshDrinks();
      } else if (deleteDrink) {
        if (!confirm(`確定刪除飲品「${btn.dataset.name}」？它的配方會一併移除。`)) return;
        await apiSend("DELETE", `/drinks/${deleteDrink}`);
        await refreshDrinks();
        await loadRecipe();
      } else if (saveDrink) {
        const tr = btn.closest("tr");
        const name = tr.querySelector('[data-field="name"]').value.trim();
        const category = tr.querySelector('[data-field="category"]').value.trim();
        if (!name || !category) {
          alert("名稱與分類不可空白");
          return;
        }
        await apiSend("PATCH", `/drinks/${saveDrink}`, {
          name,
          category,
          is_available: tr.querySelector('[data-field="available"]').checked,
        });
        editingDrink = null;
        await refreshDrinks();
      }
    } catch (err) {
      alert(`飲品更新失敗：${err.message}`);
      editingDrink = null;
      await refreshDrinks();
    }
  });

  document.getElementById("drink-add-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await apiSend("POST", "/drinks", {
        name: document.getElementById("new-drink-name").value.trim(),
        category: document.getElementById("new-drink-category").value.trim(),
        is_available: document.getElementById("new-drink-available").checked,
      });
      e.target.reset();
      document.getElementById("new-drink-available").checked = true;
      await refreshDrinks();
    } catch (err) {
      alert(`新增飲品失敗：${err.message}`);
    }
  });

  // ---------------- 配方管理 ----------------
  // 每列一個原料 + 用量；儲存時整組送出（PUT 會覆蓋該飲品的配方）
  function recipeRow(item, ingredients) {
    const unit = ingredients.find((i) => i.id === item.ingredient_id)?.unit ?? "";
    return `
      <tr data-recipe-row>
        <td>
          <select data-field="ingredient" class="stock-input">
            ${ingredients
              .map(
                (i) =>
                  `<option value="${i.id}" ${i.id === item.ingredient_id ? "selected" : ""}
                           data-unit="${escapeHtml(i.unit)}">${escapeHtml(i.name)}</option>`
              )
              .join("")}
          </select>
        </td>
        <td><input type="number" step="0.01" min="0" value="${item.quantity_needed}"
                   data-field="qty" class="stock-input qty-input"></td>
        <td class="recipe-unit">${escapeHtml(unit)}</td>
        <td><button class="btn btn-small btn-ghost" data-remove-row="1">移除</button></td>
      </tr>`;
  }

  async function loadRecipe() {
    const drinkId = recipeSelect.value;
    if (!drinkId) {
      recipeBody.innerHTML = '<tr><td colspan="4" class="placeholder">請先選擇飲品</td></tr>';
      return;
    }
    const [items, ingredients] = await Promise.all([
      apiGet(`/recipes/${drinkId}`),
      apiGet("/ingredients"),
    ]);
    allIngredients = ingredients;
    if (ingredients.length === 0) {
      recipeBody.innerHTML = '<tr><td colspan="4" class="placeholder">請先建立原料</td></tr>';
      return;
    }
    recipeBody.innerHTML = items.length
      ? items.map((it) => recipeRow(it, ingredients)).join("")
      : '<tr><td colspan="4" class="placeholder">這杯飲品目前不需要原料</td></tr>';
    recipeHint.textContent = "";
  }

  recipeSelect.addEventListener("change", loadRecipe);

  document.getElementById("recipe-add-row").addEventListener("click", () => {
    if (allIngredients.length === 0) return;
    const placeholder = recipeBody.querySelector(".placeholder");
    if (placeholder) recipeBody.innerHTML = "";
    recipeBody.insertAdjacentHTML(
      "beforeend",
      recipeRow({ ingredient_id: allIngredients[0].id, quantity_needed: 1 }, allIngredients)
    );
  });

  // 換原料時同步顯示單位
  recipeBody.addEventListener("change", (e) => {
    const select = e.target.closest('[data-field="ingredient"]');
    if (!select) return;
    select.closest("tr").querySelector(".recipe-unit").textContent =
      select.selectedOptions[0].dataset.unit;
  });

  recipeBody.addEventListener("click", (e) => {
    if (!e.target.closest("[data-remove-row]")) return;
    e.target.closest("tr").remove();
    if (!recipeBody.querySelector("[data-recipe-row]")) {
      recipeBody.innerHTML = '<tr><td colspan="4" class="placeholder">這杯飲品目前不需要原料</td></tr>';
    }
  });

  document.getElementById("recipe-save").addEventListener("click", async () => {
    const rows = [...recipeBody.querySelectorAll("[data-recipe-row]")];
    const items = rows.map((tr) => ({
      ingredient_id: Number(tr.querySelector('[data-field="ingredient"]').value),
      quantity_needed: Number(tr.querySelector('[data-field="qty"]').value),
    }));
    if (items.some((it) => !(it.quantity_needed > 0))) {
      alert("每項原料的用量都必須大於 0");
      return;
    }
    const ids = items.map((it) => it.ingredient_id);
    if (new Set(ids).size !== ids.length) {
      alert("同一原料只能出現一次");
      return;
    }
    try {
      await apiSend("PUT", `/recipes/${recipeSelect.value}`, items);
      await loadRecipe();
      recipeHint.textContent = "已儲存 ✓";
    } catch (err) {
      alert(`儲存配方失敗：${err.message}`);
    }
  });

  async function refreshAll() {
    refreshStats();
    refreshOrders();
    refreshStock();
    await refreshDrinks();
    await loadRecipe();
  }

  // 即時更新：訂單事件刷新統計與訂單區；庫存事件刷新庫存區；
  // 飲品／配方事件刷新管理區；重連成功時全部補抓
  connectWebSocket((msg) => {
    if (msg.event?.startsWith("order_")) {
      refreshStats();
      refreshOrders();
    }
    if (msg.event === "stock_alert" || msg.event?.startsWith("ingredient_")) refreshStock();
    if (msg.event?.startsWith("drink_")) refreshDrinks();
    if (msg.event === "recipe_updated" && Number(msg.drink_id) === Number(recipeSelect.value)) {
      loadRecipe();
    }
    // 每日結算完成，儀表板的今日數字要重新抓
    if (msg.event === "day_settled") {
      refreshStats();
      refreshOrders();
    }
  }, refreshAll);

  // 超時狀態會隨時間變化而沒有任何事件，定期重算
  setInterval(refreshStats, 30000); // 超時清單與 KPI（需要後端計算）
  setInterval(updateOverdueRows, 10000); // 訂單表格整列標紅（純前端計算）
});
