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

// 狀態的前後順序，和後端 orders.py 的 STAGE_ORDER 一致。
// 後端的流轉規則一次只准走一步，要把落後的單補到已送達就得逐段推。
const STAGE_ORDER = ["new", "preparing", "completed", "delivered"];

// 坐在吧台的客人：調酒師隔著檯面直接把酒遞過去，不經過服務生。
// 用前綴比對而不是等值比對：舊訂單的桌號只存「吧台」，一樣算在內。
//
// 站位與外帶**不算**：站位的客人不在檯面邊，外帶要走到櫃檯交付，
// 兩者都由服務生送，吧台按「完成」之後進待送達。
function isBarCounterSeat(tableNumber) {
  return String(tableNumber ?? "").startsWith("吧台");
}

// 服務生頁的提示文字：只有坐吧台的單是吧台自己送，標出來免得重複跑一趟。
// null 代表這張單就是服務生負責，不用特別標。
function barDeliveryHint(tableNumber) {
  return isBarCounterSeat(tableNumber) ? "🍸 吧台自送" : null;
}

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

// 從下單到現在（或到 endedAt）的總分鐘數。
// 已送達的訂單等待已經結束，要凍結在送達當下，不能繼續往上跳。
function totalWaitMinutes(placedAt, endedAt) {
  if (!placedAt) return null;
  const end = endedAt ? new Date(endedAt).getTime() : Date.now();
  return Math.max(0, (end - new Date(placedAt).getTime()) / 60000);
}

// 把一張卡片上的「總等 N 分」更新好。
// 卡片上要有 data-placed 與 .total-wait；已結束的另外帶 data-ended。
function renderTotalWait(li) {
  const el = li.querySelector(".total-wait");
  const minutes = totalWaitMinutes(li.dataset.placed, li.dataset.ended);
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

// ---------------------------------------------------------------------------
// 防誤動：避免「手指按下去的那張單」和「放開時真正按到的那張單」不是同一張。
//
// 訂單清單是整批 innerHTML 重繪的，WebSocket 一有推播就重排。吧台按下去的
// 瞬間，上面那張單可能剛被別人處理掉，卡片整排往上跳一格，手指放開時就按到
// 下一張——結果動到完全不相干的訂單。現場很難察覺，事後也查不出來。
//
// 四道防線：
//   1. 點擊鎖：上一個動作還沒回來之前，不接受新的點擊
//   2. 重繪後 500ms 內的點擊一律忽略（畫面剛跳動，手指還沒反應過來）
//   3. pointerdown 記下當時的重繪版本，放開時版本不同就不算數
//   4. 剛離開這一區的卡片留 2 秒並標灰，後面的卡片不會瞬間往上補位
// ---------------------------------------------------------------------------
const GUARD_SETTLE_MS = 300; // 卡片位移後多久內忽略點擊
const CARD_LEAVING_MS = 2000; // 離開的卡片先留多久

function createClickGuard(root = document) {
  let version = 0; // 只有「卡片真的位移」時才加一
  let shiftedAt = 0;
  let busy = false;
  let pressVersion = null;

  // 用捕獲階段：不管點到哪個元素都先記下按下去當時的版本
  root.addEventListener(
    "pointerdown",
    () => {
      pressVersion = version;
    },
    true
  );

  // 被擋下來時要讓人看得出來。無聲吃掉的話，按的人會以為按鈕壞了，
  // 再按一次反而更亂。
  function flash(btn) {
    if (!btn) return;
    btn.classList.remove("btn-blocked");
    void btn.offsetWidth; // 強制重算，同一顆按鈕連續被擋才會重播動畫
    btn.classList.add("btn-blocked");
    setTimeout(() => btn.classList.remove("btn-blocked"), 500);
  }

  return {
    /** 卡片位置有變動時才呼叫：只有這種時候手指才可能按錯張。 */
    markShifted() {
      version += 1;
      shiftedAt = Date.now();
    },
    /**
     * 這一次點擊可不可以執行。擋下來時會閃一下按鈕並說明原因，
     * 不會無聲吃掉。btn 傳進來只是為了給回饋。
     */
    allow(btn) {
      const pressed = pressVersion;
      pressVersion = null;
      if (busy) {
        flash(btn); // 上一個動作還沒回來，不另外跳字，避免連點時洗版
        return false;
      }
      if (Date.now() - shiftedAt < GUARD_SETTLE_MS) {
        flash(btn);
        showActionToast("清單剛更新，請再按一次");
        return false;
      }
      // 鍵盤觸發的 click 沒有 pointerdown，pressed 會是 null，那就不擋
      if (pressed !== null && pressed !== version) {
        flash(btn);
        showActionToast("清單剛更新，請再按一次");
        return false;
      }
      return true;
    },
    /** 包住會打 API 的動作，期間擋掉其他點擊。 */
    async lock(fn) {
      busy = true;
      try {
        return await fn();
      } finally {
        busy = false;
      }
    },
  };
}

/**
 * 整批重繪一個清單，並讓「這次消失的卡片」先留 2 秒、標灰、不吃點擊。
 * 回傳 render(html, ids)，ids 是這次還在的卡片 id 字串集合。
 *
 * 三種情況分開處理，因為「重繪」和「卡片位移」不是同一件事：
 *   1. html 一模一樣       → 什麼都不做。尖峰時每幾秒一次推播，多數都是這種，
 *                            不碰 DOM 就不會打斷正在按的人（計時紅字是另外更新的）
 *   2. 內容變了但順序沒變   → 重繪，但不冷卻。手指底下還是同一張單的按鈕
 *   3. 卡片增減或順序改變   → 重繪並冷卻。只有這時候才可能按錯張
 */
function createListRenderer(listEl, guard, idAttr = "data-id") {
  const leaving = new Map(); // id -> {html, index, expires}
  let lastHtml = null;
  let lastOrder = ""; // 上一次的 id 順序

  // 這個清單裡的「卡片」。placeholder 與「顯示全部」那一列沒有 idAttr，
  // 算位置時不能把它們算進去，插回去也不能壓到它們。
  const cards = () => [...listEl.querySelectorAll(`[${idAttr}]`)];

  return function render(html, ids) {
    if (html === lastHtml && !leaving.size) return; // 情況 1
    const order = [...ids].join(",");
    const shifted = order !== lastOrder;
    lastHtml = html;
    lastOrder = order;

    // 先記下這一輪不見的卡片：長相，還有它原本排在第幾張。
    // 要連 index 一起記，灰卡才能留在原位；只 append 到最後面的話，
    // 它後面的卡片照樣立刻往上補位，等於沒擋到。
    cards().forEach((el, index) => {
      if (el.classList.contains("card-leaving")) return;
      const id = el.getAttribute(idAttr);
      if (!ids.has(id)) {
        leaving.set(id, {
          html: el.outerHTML,
          index,
          expires: Date.now() + CARD_LEAVING_MS,
        });
      }
    });

    listEl.innerHTML = html;

    // 由小到大插回去：先插前面的，後面的 index 才不會被自己擠掉
    const now = Date.now();
    const back = [...leaving.entries()]
      .filter(([id, item]) => {
        if (item.expires <= now || ids.has(id)) {
          leaving.delete(id); // 過期了，或是它又回來了（例如被退回上一步）
          return false;
        }
        return true;
      })
      .sort((a, b) => a[1].index - b[1].index);

    for (const [id, item] of back) {
      const here = cards();
      const ghost = document.createElement("template");
      ghost.innerHTML = item.html.trim();
      const node = ghost.content.firstElementChild;
      if (!node) {
        leaving.delete(id);
        continue;
      }
      node.classList.add("card-leaving");
      // 插在原本那個位置的卡片前面；超過長度就接在最後一張卡片後面。
      // 用卡片當定位點，placeholder 與「顯示全部」那一列不會被插到中間。
      if (item.index < here.length) here[item.index].before(node);
      else if (here.length) here[here.length - 1].after(node);
      else listEl.prepend(node);

      setTimeout(() => {
        if (Date.now() >= item.expires) leaving.delete(id);
        node.remove();
      }, item.expires - now);
    }

    if (shifted) guard.markShifted(); // 情況 3 才冷卻
  };
}

// Andon 超時提示：右下角的小卡片，不是全螢幕視窗。
// 以前用 modal，整頁會變黑、調酒師手上在調酒還得先去點掉它才能操作。
// 現在只佔右下角一小塊，而且整張卡片都不吃點擊（CSS 的 pointer-events:none），
// 只有那顆 X 例外。提示就算正好蓋在「完成」按鈕上，手指點下去還是按到按鈕。
// 提示裡的階段名稱。和 STATUS_LABELS 分開：那是訂單徽章上的字（「新訂單」），
// 這裡講的是「誰該動了」，用和門檻設定一致的講法（待接單 20 分、待送達 10 分）。
const ANDON_STAGE_LABELS = {
  new: "待接單",
  preparing: "製作中",
  completed: "待送達",
};

const ANDON_TOAST_MAX = 4; // 同時最多顯示幾張，其餘排隊
const ANDON_TOAST_TTL = 15000; // 每張顯示多久（毫秒）後自動消失

// 回傳 showAndon(items)，items 為 {table, summary, stage, minutes}
function toastStack(containerId = "andon-toasts") {
  let box = document.getElementById(containerId);
  if (!box) {
    box = document.createElement("div");
    box.id = containerId;
    box.className = "toast-stack";
    // polite 而不是 assertive：讀螢幕的人不會被打斷，但還是會唸到
    box.setAttribute("role", "status");
    box.setAttribute("aria-live", "polite");
    document.body.appendChild(box);
  }
  return box;
}

// 操作成功的回報：「桌 5 Negroni → 製作中」。
// 畫面上同一張單可能已經跳到別區了，這行字是唯一能確認「我剛剛動到的是哪一張」
// 的依據，所以一定要帶桌號與飲料名，不能只說「已更新」。
const ACTION_TOAST_TTL = 4000;

function showActionToast(text) {
  const box = toastStack();
  const card = document.createElement("div");
  card.className = "toast toast-action";
  card.textContent = text;
  box.appendChild(card);
  setTimeout(() => card.remove(), ACTION_TOAST_TTL);
}

function setupAndonToasts(containerId = "andon-toasts") {
  const box = toastStack(containerId);

  const pending = []; // 超過上限時排隊，前面的消失後再補上
  let moreEl = null;

  function renderMore() {
    if (!pending.length) {
      moreEl?.remove();
      moreEl = null;
      return;
    }
    if (!moreEl) {
      moreEl = document.createElement("div");
      moreEl.className = "toast toast-more";
      box.appendChild(moreEl);
    }
    moreEl.textContent = `還有 ${pending.length} 則`;
    box.appendChild(moreEl); // 一直保持在最下面
  }

  function dismiss(card) {
    if (!card.isConnected) return; // X 按過了就不要再被計時器關一次
    card.remove();
    if (pending.length) show(pending.shift());
    renderMore();
  }

  function show(item) {
    const card = document.createElement("div");
    card.className = "toast";
    card.innerHTML = `
      <button class="toast-close" aria-label="關閉提示">✕</button>
      <div class="toast-title">⚠️ ${escapeHtml(tableLabel(item.table))}</div>
      <div class="toast-drink">${escapeHtml(item.summary)}</div>
      <div class="toast-meta">${escapeHtml(item.stage)}・已等 ${Math.floor(item.minutes)} 分</div>`;
    card.querySelector(".toast-close").addEventListener("click", () => dismiss(card));
    box.appendChild(card);
    renderMore(); // 把「還有 N 則」推回最下面
    setTimeout(() => dismiss(card), ANDON_TOAST_TTL);
  }

  return (items) => {
    for (const item of items) {
      if (box.querySelectorAll(".toast:not(.toast-more)").length >= ANDON_TOAST_MAX) {
        pending.push(item);
        renderMore();
      } else {
        show(item);
      }
    }
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
