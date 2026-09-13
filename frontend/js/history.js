// 歷史數據頁：列出每個營業日，點一下展開當天明細

document.addEventListener("DOMContentLoaded", () => {
  const list = document.getElementById("history-list");
  const countLabel = document.getElementById("history-count");

  const fmtMinutes = (v) => (v == null ? "－" : `${fmtQty(v)} 分`);

  // 摘要列：日期、訂單數、售出杯數、平均製作時間
  function summaryRow(day) {
    return `
      <div class="history-row" role="button" tabindex="0">
        <strong class="history-date">${escapeHtml(day.business_date)}</strong>
        <span>訂單 <strong>${day.total_orders}</strong></span>
        <span>售出 <strong>${day.drinks_sold}</strong> 杯</span>
        <span class="history-prep">平均製作 ${fmtMinutes(day.avg_prep_minutes)}</span>
        <span class="history-toggle">展開 ▾</span>
      </div>`;
  }

  // 展開後的明細：其餘指標 + 熱門飲品前三名
  function detail(day) {
    const top3 = day.top_drinks.slice(0, 3);
    return `
      <div class="history-detail" hidden>
        <div class="history-stats">
          <div class="stat-tile"><div class="stat-label">總訂單數</div>
            <div class="stat-value">${day.total_orders}</div></div>
          <div class="stat-tile"><div class="stat-label">售出杯數</div>
            <div class="stat-value">${day.drinks_sold}</div></div>
          <div class="stat-tile"><div class="stat-label">平均製作時間</div>
            <div class="stat-value">${fmtMinutes(day.avg_prep_minutes)}</div></div>
          <div class="stat-tile"><div class="stat-label">最長等待時間</div>
            <div class="stat-value">${fmtMinutes(day.max_wait_minutes)}</div></div>
          <div class="stat-tile ${day.overdue_orders ? "stat-warn" : ""}">
            <div class="stat-label">超時訂單</div>
            <div class="stat-value">${day.overdue_orders}</div></div>
          <div class="stat-tile ${day.unpaid_orders ? "stat-warn" : ""}">
            <div class="stat-label">未付款</div>
            <div class="stat-value">${day.unpaid_orders}</div></div>
          <div class="stat-tile"><div class="stat-label">訂單修改率</div>
            <div class="stat-value">${fmtQty(day.edit_rate)}%</div></div>
        </div>

        <h3 class="history-subtitle">熱門飲品前三名</h3>
        ${top3.length
          ? `<div class="bar-list">${top3
              .map((d) => {
                const max = top3[0].qty;
                return `
                  <div class="bar-row" title="${escapeHtml(d.name)}：${d.qty} 杯">
                    <span class="bar-name">${escapeHtml(d.name)}</span>
                    <span class="bar-track"><span class="bar-fill"
                      style="width:${(d.qty / max) * 100}%"></span></span>
                    <span class="bar-value">${d.qty}</span>
                  </div>`;
              })
              .join("")}</div>`
          : '<span class="placeholder">當天沒有售出紀錄</span>'}
      </div>`;
  }

  async function load() {
    let data;
    try {
      data = await apiGet("/history");
    } catch (err) {
      list.innerHTML = `<li class="placeholder">讀取失敗：${escapeHtml(err.message)}</li>`;
      return;
    }

    countLabel.textContent = `（保留最近 ${data.limit} 個營業日，目前 ${data.days.length} 筆）`;
    if (data.days.length === 0) {
      list.innerHTML =
        '<li class="placeholder">尚無歷史資料。每天 23:59 結算，當天有訂單才會留下紀錄。</li>';
      return;
    }
    list.innerHTML = data.days
      .map((day) => `<li class="history-item">${summaryRow(day)}${detail(day)}</li>`)
      .join("");
  }

  // 點整列展開／收合（鍵盤也可操作）
  function toggle(row) {
    const item = row.closest(".history-item");
    const panel = item.querySelector(".history-detail");
    panel.hidden = !panel.hidden;
    row.querySelector(".history-toggle").textContent = panel.hidden ? "展開 ▾" : "收合 ▴";
    item.classList.toggle("open", !panel.hidden);
  }

  list.addEventListener("click", (e) => {
    const row = e.target.closest(".history-row");
    if (row) toggle(row);
  });
  list.addEventListener("keydown", (e) => {
    if (e.key !== "Enter" && e.key !== " ") return;
    const row = e.target.closest(".history-row");
    if (!row) return;
    e.preventDefault();
    toggle(row);
  });

  load();
  // 結算完成時（每天 23:59）自動更新列表
  connectWebSocket((msg) => {
    if (msg.event === "day_settled") load();
  });
});
