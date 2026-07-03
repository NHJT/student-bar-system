// 經理頁面（第六、七步實作）
// - 全局訂單總覽表格 + 各狀態統計
// - 庫存警示：current_stock < reorder_point 的原料列表

document.addEventListener("DOMContentLoaded", () => {
  // TODO(第七步): apiGet("/orders") 渲染 #order-table-body 與 #order-stats
  // TODO(第六步): apiGet("/ingredients") 渲染 #stock-table-body，
  //               低於 reorder_point 者加入 #stock-alerts（.low-stock）
  // TODO(第四步): connectWebSocket(...) 收到 order_* / stock_alert 事件時刷新
});
