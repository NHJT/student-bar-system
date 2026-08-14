// 共用工具：API 呼叫、WebSocket 連線、顯示用小函式（三個角色頁面共用）

const API_BASE = "/api";

// 訂單狀態的中文標籤（class 名同時用於徽章顏色）
const STATUS_LABELS = {
  new: "新訂單",
  preparing: "製作中",
  completed: "待送達",
  delivered: "已送達",
  cancelled: "已取消",
};

// 三段式 Andon 超時門檻（分鐘）——與後端 stats.py 的 ANDON_THRESHOLDS 一致
const ANDON_THRESHOLDS = {
  new: 5, // 未開始製作
  preparing: 10, // 製作中
  completed: 3, // 完成後未送達
};

// Andon 計時基準：各狀態從「進入該狀態」的時間開始算
const ANDON_REF_FIELD = {
  new: "placed_at",
  preparing: "started_at",
  completed: "completed_at",
};

// 算出在目前狀態停留幾分鐘、是否超時；不列入計時的狀態（已送達／已取消）回 null
function andonState(status, refTimestamp) {
  const limit = ANDON_THRESHOLDS[status];
  if (limit == null || !refTimestamp) return null;
  const ref = new Date(refTimestamp.replace(" ", "T")).getTime();
  const minutes = Math.max(0, (Date.now() - ref) / 60000);
  return { minutes, limit, overdue: minutes >= limit };
}

// 同上，但直接吃一筆訂單物件
function orderAndonState(order) {
  const field = ANDON_REF_FIELD[order.status];
  if (!field) return null;
  return andonState(order.status, order[field] ?? order.placed_at);
}

async function apiGet(path) {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) throw new Error(`GET ${path} 失敗: ${res.status}`);
  return res.json();
}

async function apiSend(method, path, body) {
  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let detail = `${res.status}`;
    try { detail = (await res.json()).detail ?? detail; } catch (_) {}
    throw new Error(detail);
  }
  if (res.status === 204) return null;
  return res.json();
}

// 防止使用者輸入（特殊需求等）被當成 HTML
function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text ?? "";
  return div.innerHTML;
}

// 數量顯示：去掉浮點誤差與多餘的 0（1.7999999999999998 → 1.8、3.0 → 3）
function fmtQty(n) {
  return String(Number(Number(n).toFixed(3)));
}

// "2026-07-04 18:30:12" → "18:30"
function formatTime(dbTimestamp) {
  if (!dbTimestamp) return "";
  return dbTimestamp.slice(11, 16);
}

function statusBadge(status) {
  return `<span class="badge badge-${status}">${STATUS_LABELS[status] ?? status}</span>`;
}

// 建立 WebSocket 連線
// - onMessage: 收到伺服器廣播的事件物件（{event: "order_created", order: {...}} 等）
// - onOpen: 連上（含斷線重連成功）時呼叫，用來補抓斷線期間錯過的資料
function connectWebSocket(onMessage, onOpen) {
  const statusEl = document.getElementById("ws-status");
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);

  ws.onopen = () => {
    if (statusEl) { statusEl.textContent = "● 已連線"; statusEl.className = "ws-status connected"; }
    if (onOpen) onOpen();
  };
  ws.onmessage = (e) => onMessage(JSON.parse(e.data));
  ws.onclose = () => {
    if (statusEl) { statusEl.textContent = "● 已斷線，5 秒後重連"; statusEl.className = "ws-status disconnected"; }
    setTimeout(() => connectWebSocket(onMessage, onOpen), 5000);
  };
  return ws;
}
