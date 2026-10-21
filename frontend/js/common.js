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

// 店內桌數：桌號 1 ~ TABLE_COUNT，全部都是一般桌
const TABLE_COUNT = 13;

// 不是桌子的出餐位置，排在桌 1 之前。桌號在資料庫裡是文字，直接存這些字。
// 吧台分成左／中／右三個位置；舊訂單存的是「吧台」，不在這份清單裡但照常顯示
// （tableLabel 只判斷是不是純數字，不比對這份清單）。
const NON_TABLE_SEATS = ["吧台左邊", "吧台中間", "吧台右邊", "站位", "外帶"];

// 基酒分類的顯示順序（drinks.category）。
// API 是依字母排序，但點單時依基酒排比較好找；不在這份清單裡的
// （例如經理自己新增的分類）照字母接在後面，不會不見
const CATEGORY_ORDER = [
  "GIN BASE",
  "VODKA BASE",
  "RUM BASE",
  "TEQUILA BASE",
  "WHISKEY BASE",
  "OTHERS",
  "SIGNATURE",
];

// 取出這批飲品實際有的分類，依上面的順序排好
function sortedCategories(drinks) {
  const seen = [...new Set(drinks.map((d) => d.category))];
  const known = CATEGORY_ORDER.filter((c) => seen.includes(c));
  const rest = seen.filter((c) => !CATEGORY_ORDER.includes(c)).sort();
  return [...known, ...rest];
}

// 點餐選單用的完整桌號清單
function tableOptions() {
  return [
    ...NON_TABLE_SEATS,
    ...Array.from({ length: TABLE_COUNT }, (_, i) => String(i + 1)),
  ];
}

// 桌號顯示：數字要加「桌」字，吧台／站位本身就是名稱，不加
function tableLabel(value) {
  const text = String(value ?? "");
  return /^\d+$/.test(text) ? `桌 ${text}` : text;
}

// 三段式 Andon 超時門檻（分鐘）——與後端 stats.py 的 ANDON_THRESHOLDS 一致
const ANDON_THRESHOLDS = {
  new: 20, // 未開始製作
  preparing: 10, // 製作中
  completed: 10, // 完成後未送達
};

// 總等待時間（placed_at 到現在）變警示色的門檻，單位分鐘。
// 這是純顯示用的，和 ANDON_THRESHOLDS 無關，不會觸發紅底、彈窗或警示音。
const TOTAL_WAIT_WARN = 20;

// 從下單到現在的總分鐘數
function totalWaitMinutes(placedAt) {
  if (!placedAt) return null;
  return Math.max(0, (Date.now() - new Date(placedAt).getTime()) / 60000);
}

// 把一張卡片上的「總等 N 分」更新好（卡片上要有 data-placed 與 .total-wait）
function renderTotalWait(li) {
  const el = li.querySelector(".total-wait");
  const minutes = totalWaitMinutes(li.dataset.placed);
  if (!el || minutes == null) return;
  el.textContent = `總等 ${Math.floor(minutes)} 分`;
  el.classList.toggle("total-wait-warn", minutes >= TOTAL_WAIT_WARN);
}

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

// ---------- Andon 警示音（Web Audio 產生嗶聲，不需外部音檔）----------
// 吧台頁與服務生頁共用：吧台管「未開始」「製作中」，服務生管「待送達」
let audioCtx = null;

function ensureAudio() {
  if (!audioCtx) {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return null;
    audioCtx = new Ctx();
  }
  if (audioCtx.state === "suspended") audioCtx.resume();
  return audioCtx;
}

function playAlarm(beeps = 3) {
  const ctx = ensureAudio();
  if (!ctx || ctx.state !== "running") return false;
  for (let i = 0; i < beeps; i++) {
    const start = ctx.currentTime + i * 0.28;
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = "square";
    osc.frequency.value = 880;
    // 淡入淡出，避免爆音
    gain.gain.setValueAtTime(0.0001, start);
    gain.gain.exponentialRampToValueAtTime(0.25, start + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, start + 0.2);
    osc.connect(gain).connect(ctx.destination);
    osc.start(start);
    osc.stop(start + 0.22);
  }
  return true;
}

// 瀏覽器要求先有使用者互動才能播音；第一次點擊時解鎖並隱藏提示
function setupAudioUnlock(hintId = "sound-hint") {
  const hint = document.getElementById(hintId);
  const unlock = () => {
    const ctx = ensureAudio();
    if (ctx && ctx.state === "running" && hint) hint.hidden = true;
  };
  document.addEventListener("click", unlock, { once: true });
  document.addEventListener("keydown", unlock, { once: true });
}

// Andon 跳出視窗。回傳 showModal(items)，items 為 {table, summary, reason}
function setupAndonModal(modalId = "andon-modal") {
  const modal = document.getElementById(modalId);
  if (!modal) return () => {};
  const body = modal.querySelector(".alert-list");
  modal.querySelector("[data-modal-close]")?.addEventListener("click", () => {
    modal.hidden = true;
  });
  modal.addEventListener("click", (e) => {
    if (e.target === modal) modal.hidden = true; // 點背景關閉
  });
  return (items) => {
    body.innerHTML = items
      .map(
        (it) => `
        <li class="overdue-item">
          <strong>${escapeHtml(tableLabel(it.table))} ・ ${escapeHtml(it.summary)}</strong>
          <div class="modal-reason">${escapeHtml(it.reason)}</div>
        </li>`
      )
      .join("");
    modal.hidden = false;
  };
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
