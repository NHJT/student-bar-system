// 吧台頁面（第三步實作）
// - 三欄佇列：new / preparing / completed
// - 點一下訂單卡片 → 切到下一個狀態
// - 第五步 Andon：new 超過 N 分鐘未開始製作 → 卡片加上 .overdue 變紅

const ANDON_TIMEOUT_MINUTES = 5; // 超時門檻（第五步使用）

document.addEventListener("DOMContentLoaded", () => {
  // TODO(第三步): apiGet("/orders") 依 status 分配到 #queue-new / #queue-preparing / #queue-completed
  // TODO(第三步): 卡片點擊 → apiSend("PATCH", `/orders/${id}/status`, ...) 推進狀態
  // TODO(第四步): connectWebSocket(...) 即時搬移卡片
  // TODO(第五步): setInterval 每秒檢查 placed_at，超時加上 .overdue
});
