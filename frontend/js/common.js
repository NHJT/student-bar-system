// 共用工具：API 呼叫與 WebSocket 連線（三個角色頁面共用）

const API_BASE = "/api";

async function apiGet(path) {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) throw new Error(`GET ${path} 失敗: ${res.status}`);
  return res.json();
}

async function apiSend(method, path, body) {
  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error(`${method} ${path} 失敗: ${res.status}`);
  return res.json();
}

// 建立 WebSocket 連線；onMessage 會收到伺服器廣播的事件物件
// （第四步實作伺服器端廣播後即可運作）
function connectWebSocket(onMessage) {
  const statusEl = document.getElementById("ws-status");
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);

  ws.onopen = () => {
    if (statusEl) { statusEl.textContent = "● 已連線"; statusEl.className = "ws-status connected"; }
  };
  ws.onmessage = (e) => onMessage(JSON.parse(e.data));
  ws.onclose = () => {
    if (statusEl) { statusEl.textContent = "● 已斷線，5 秒後重連"; statusEl.className = "ws-status disconnected"; }
    setTimeout(() => connectWebSocket(onMessage), 5000);
  };
  return ws;
}
