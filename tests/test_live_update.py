"""「不用按 F5，畫面也會自己更新」的復發防護（端對端測試）。

這個 bug 出現過不只一次：吧台頁停在舊資料，要按 F5 才看得到新訂單，而且畫面
上看不出任何異狀。現在靠四個機制一起擋住，這份測試就是用來確認它們都還在：

    1. WebSocket 推播                 → test_push_...
    2. 斷線後自動重連                 → test_reconnects_...、test_recovers_after_server_restart
    3. 保底輪詢（前景每 10 秒）       → test_polling_...
    4. 分頁切回前景立即補抓           → test_refreshes_immediately_...

**凡是動到連線、輪詢、畫面更新的程式，改完都要跑這一份，全部 passed 才算數**
（skipped 不算——那代表根本沒有測到）。相關程式在：

    frontend/js/common.js   guardedRefresh、wsStartPolling、wsScheduleReconnect、
                            wsWireVisibility、connectWebSocket
    frontend/js/bar.js      最底下的 connectWebSocket(...) 呼叫
    frontend/js/waiter.js   最底下的 connectWebSocket(...) 呼叫
    backend/ws.py           broadcast
    backend/main.py         /ws 端點

怎麼跑（只需要做一次的準備）::

    pip install -r requirements-dev.txt
    python -m playwright install chromium

每次::

    設好 TEST_DATABASE_URL（見 conftest.py，資料庫名稱必須含 test）
    python -m pytest tests/test_live_update.py -v

整份大約一分半鐘。慢是因為真的在等：輪詢要等滿 10 秒、重連要等 5 秒，
這些時間本身就是要驗的規格，不能用假時鐘跳過去。

做法：對測試資料庫另外起一個真的 uvicorn，用真的 Chromium 開吧台頁與服務生頁，
再從旁邊用 API 建單，看訂單卡片有沒有在時限內自己出現。全程不重新載入頁面。
"""

import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine, text

sync_api = pytest.importorskip(
    "playwright.sync_api",
    reason="沒有安裝 Playwright，畫面更新的測試沒有跑。"
    "請先 pip install -r requirements-dev.txt 再 python -m playwright install chromium",
)

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "backend" / "schema.sql"

# ---------------------------------------------------------------------------
# 時限。這些數字是「規格」，不是從前端程式讀出來的——有人把輪詢改成 60 秒、
# 或把重連拿掉，測試就應該要紅。要放寬之前請先想清楚現場能不能接受。
# ---------------------------------------------------------------------------
PUSH_MS = 2_000  # 推播正常時，新單多久內要出現
RECONNECT_MS = 8_000  # 斷線後多久內要重新連上（前端是 5 秒後重連，留 3 秒餘裕）
POLL_MS = 12_000  # 推播失效時，新單多久內要出現（規格 10 秒 + 抓資料與重繪的餘裕）
VISIBLE_MS = 2_000  # 分頁切回前景後，新單多久內要出現
RESTART_MS = 15_000  # 伺服器重啟回來後，多久內要重新連上

# 各頁面的網址，以及「某張訂單的卡片」在那一頁長什麼樣子
SCREENS = {
    "bar": ("吧台頁", "/bar.html", '#queue-new li[data-id="{id}"]'),
    "waiter": ("服務生頁", "/waiter.html", '#order-list li[data-order="{id}"]'),
}


def _is_orders_fetch(request) -> bool:
    return request.method == "GET" and "/api/orders" in request.url


# ---------------------------------------------------------------------------
# 測試用伺服器：真的 uvicorn，連測試資料庫
# ---------------------------------------------------------------------------
class LiveServer:
    def __init__(self, database_url: str, log_path: Path):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        self.base = f"http://127.0.0.1:{self.port}"
        self._env = {**os.environ, "DATABASE_URL": database_url}
        self._log_path = log_path
        self._proc = None
        self._log = None
        # trust_env=False：本機若設了 HTTP(S)_PROXY，打 127.0.0.1 不該繞出去
        self.http = httpx.Client(base_url=self.base, trust_env=False, timeout=5)
        self.drink_id = None

    def start(self) -> None:
        self._log = open(self._log_path, "ab")
        self._proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "backend.main:app",
             "--host", "127.0.0.1", "--port", str(self.port)],
            cwd=str(ROOT), env=self._env,
            stdout=self._log, stderr=subprocess.STDOUT,
        )
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if self._proc.poll() is not None:
                break
            try:
                if self.http.get("/api/health").status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
        self.stop()
        # 只給 log 的位置，不把內容印出來：連線失敗的訊息裡可能帶有主機與帳號
        raise RuntimeError(f"測試用伺服器起不來，請看 log：{self._log_path}")

    def stop(self) -> None:
        if self._proc is not None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait(timeout=10)
            self._proc = None
        if self._log is not None:
            self._log.close()
            self._log = None

    def new_order(self, table: str) -> int:
        """從旁邊建一張單（等於另一台裝置下單），回傳訂單 id。"""
        res = self.http.post("/api/orders", json={
            "table_number": table,
            "items": [{"drink_id": self.drink_id, "quantity": 1}],
        })
        assert res.status_code == 201, f"建單失敗：{res.status_code}"
        return res.json()["id"]


@pytest.fixture(scope="module")
def server(test_database_url, tmp_path_factory):
    # 和 backend/database.py 一樣明確指定 psycopg2：SQLAlchemy 2.1 起
    # postgresql:// 的預設驅動改成 psycopg 3，而專案裝的是 psycopg2
    url = test_database_url
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            url = "postgresql+psycopg2://" + url[len(prefix):]
            break

    # 重建一套乾淨的資料表，外加一款沒有配方的飲料（不扣庫存，建單一定成功）
    engine = create_engine(url)
    with engine.connect() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
        conn.execute(text(SCHEMA_PATH.read_text(encoding="utf-8")))
        conn.execute(text(
            "INSERT INTO drinks (name, category, price) VALUES ('測試用飲料', 'TEST', 100)"
        ))
        conn.commit()
    engine.dispose()

    live = LiveServer(test_database_url, tmp_path_factory.mktemp("live") / "uvicorn.log")
    live.start()
    live.drink_id = live.http.get("/api/drinks").json()[0]["id"]
    yield live
    live.stop()
    live.http.close()


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as p:
        chromium = p.chromium.launch()
        yield chromium
        chromium.close()


# ---------------------------------------------------------------------------
# 一個開著的頁面，外加可以從測試這邊動手腳的 WebSocket
# ---------------------------------------------------------------------------
class Screen:
    """開著的吧台頁或服務生頁。

    頁面的 WebSocket 會經過測試這一層再轉給真的伺服器，所以可以模擬兩種壞法：
      - swallow_incoming()：連線還開著，但伺服器的訊息再也送不到頁面（半開連線，
        onclose 不會觸發——現場「要按 F5」就是這一種）
      - close_socket()：連線被關掉（頁面會收到 onclose）
    """

    def __init__(self, page, key: str, base: str):
        self.page = page
        self.label, self._path, self._card = SCREENS[key]
        self._base = base
        self.links = []  # 頁面開過的每一條 WebSocket：{route, upstream, frames}
        self.swallowing = False

        def on_socket(route):
            upstream = route.connect_to_server()
            link = {"route": route, "upstream": upstream, "frames": []}
            self.links.append(link)

            def to_page(message):
                if self.swallowing:
                    return
                link["frames"].append(message)
                route.send(message)

            # 只攔「伺服器 → 頁面」；頁面送出去的（心跳）照常轉給伺服器
            upstream.on_message(to_page)

        page.route_web_socket(re.compile(r"/ws$"), on_socket)

    def open(self) -> None:
        # 等到第一次抓訂單回來、而且狀態列顯示已連線，頁面才算就緒
        with self.page.expect_response(lambda r: "/api/orders" in r.url, timeout=15_000):
            self.page.goto(self._base + self._path)
        self.wait_status("connected", 10_000, "頁面一開始就沒有連上")

    def wait_status(self, css_class: str, within_ms: int, why: str) -> None:
        try:
            self.page.wait_for_function(
                "c => document.getElementById('ws-status').classList.contains(c)",
                arg=css_class, timeout=within_ms,
            )
        except sync_api.TimeoutError:
            now = self.page.evaluate("document.getElementById('ws-status').textContent")
            pytest.fail(
                f"{self.label}：{why}（等了 {within_ms / 1000:g} 秒，"
                f"右上角狀態仍是「{now.strip()}」）"
            )

    def wait_card(self, order_id: int, within_ms: int, why: str) -> float:
        """等某張訂單的卡片自己出現在畫面上，回傳等了幾秒。不會重新載入頁面。"""
        started = time.monotonic()
        try:
            self.page.wait_for_selector(self._card.format(id=order_id), timeout=within_ms)
        except sync_api.TimeoutError:
            pytest.fail(
                f"{self.label}：{why}（訂單 #{order_id} 在 {within_ms / 1000:g} 秒內"
                "沒有出現在畫面上，現場就是要按 F5 才看得到）"
            )
        return time.monotonic() - started

    def swallow_incoming(self) -> None:
        self.swallowing = True

    def close_socket(self) -> None:
        link = self.links[-1]
        link["route"].close()
        link["upstream"].close()

    def pushed(self, order_id: int) -> bool:
        """最新那條連線有沒有真的收到這張單的推播。"""
        return any(
            '"order_created"' in f and f'"id":{order_id}' in f.replace(" ", "")
            for f in self.links[-1]["frames"] if isinstance(f, str)
        )

    def set_visibility(self, state: str) -> None:
        """模擬分頁進背景／回前景（平板休眠、切到別的 App 再切回來）。"""
        self.page.evaluate(
            """state => {
                Object.defineProperty(document, 'visibilityState',
                    { configurable: true, get: () => state });
                Object.defineProperty(document, 'hidden',
                    { configurable: true, get: () => state !== 'visible' });
                document.dispatchEvent(new Event('visibilitychange'));
            }""",
            state,
        )


@pytest.fixture(params=list(SCREENS))
def screen(request, browser, server):
    page = browser.new_page()
    opened = Screen(page, request.param, server.base)
    opened.open()
    yield opened
    page.close()


# ---------------------------------------------------------------------------
# 測試
# ---------------------------------------------------------------------------
def test_push_shows_new_order_without_reload(screen, server):
    """一切正常時：別台裝置下單，這一頁 2 秒內自己出現。

    這一條壞掉時輪詢會把它蓋過去（變成慢 10 秒而不是完全不更新），
    所以要另外確認訂單真的是從 WebSocket 推過來的。
    """
    order = server.new_order("1")
    screen.wait_card(order, PUSH_MS, "WebSocket 推播沒有讓畫面更新")
    assert screen.pushed(order), (
        f"{screen.label}：畫面有更新，但 WebSocket 上沒有收到這張單的 order_created，"
        "推播本身是壞的（檢查 backend/ws.py 的 broadcast）"
    )


def test_reconnects_after_socket_closed(screen, server):
    """連線被關掉之後：自動重連、補上斷線期間漏掉的單、之後的推播也恢復。"""
    screen.close_socket()
    screen.wait_status("disconnected", 3_000, "連線被關掉了，畫面卻沒有顯示斷線")

    missed = server.new_order("2")  # 斷線期間進來的單

    screen.wait_status("connected", RECONNECT_MS, "斷線之後沒有自動重連")
    assert len(screen.links) >= 2, f"{screen.label}：狀態顯示已連線，但沒有開出新的 WebSocket"
    screen.wait_card(missed, 3_000, "重連之後沒有補上斷線期間的訂單")

    after = server.new_order("3")
    screen.wait_card(after, PUSH_MS, "重連之後的推播沒有讓畫面更新")
    assert screen.pushed(after), f"{screen.label}：重連後的新連線沒有收到推播"


def test_polling_shows_new_order_when_socket_is_silent(screen, server):
    """主連線失效（連線還開著但收不到任何訊息）：靠保底輪詢，10 秒內仍看得到新單。

    這是現場最常見、也最難發現的壞法：onclose 不會觸發、不會重連、
    狀態列還寫著已連線。沒有輪詢的話，這一頁會永遠停在舊資料。
    """
    screen.swallow_incoming()
    order = server.new_order("4")
    screen.wait_card(order, POLL_MS, "推播收不到時，保底輪詢沒有把新單抓回來")
    assert not screen.pushed(order), "測試本身有問題：推播沒有被擋掉，這張單可能是推播送到的"


def test_refreshes_immediately_when_tab_becomes_visible(screen, server):
    """分頁切回前景（平板休眠醒來）：不等下一次輪詢，立刻重新抓資料。"""
    screen.swallow_incoming()  # 休眠期間連線通常已經默默死掉了

    # 先對準輪詢的節拍，之後才能確定「切回來時的那次抓取」不是剛好碰上輪詢。
    # 等不到輪詢（輪詢壞了，那是另一條測試的事）就直接往下——沒有輪詢，
    # 也就沒有「剛好碰上」的問題，這一條只管切回前景有沒有補抓。
    try:
        with screen.page.expect_request(_is_orders_fetch, timeout=POLL_MS):
            pass
    except sync_api.TimeoutError:
        pass
    tick = time.monotonic()

    screen.set_visibility("hidden")
    order = server.new_order("5")

    # 等到離上一次輪詢 4 秒：下一次輪詢還要 6 秒才會到
    screen.page.wait_for_timeout(max(0.0, 4.0 - (time.monotonic() - tick)) * 1000)
    try:
        with screen.page.expect_request(_is_orders_fetch, timeout=1_500):
            screen.set_visibility("visible")
    except sync_api.TimeoutError:
        pytest.fail(
            f"{screen.label}：分頁切回前景後沒有立刻重新抓訂單"
            "（visibilitychange 的處理不見了，或沒有接上這一頁的 refresh）"
        )
    screen.wait_card(order, VISIBLE_MS, "分頁切回前景後畫面沒有更新")
    assert time.monotonic() - tick < 8, "測試本身有問題：耗時太久，已經跨到下一次輪詢"


def test_recovers_after_server_restart(browser, server):
    """伺服器整個停掉再回來（等於營業中重新部署）：頁面自己恢復，不用按 F5。

    這裡不攔 WebSocket，走的是完全真實的路徑。伺服器停著的時間刻意超過
    一次重連間隔，所以也涵蓋「重連失敗之後還會繼續重試」。
    """
    label, path, card = SCREENS["bar"]
    page = browser.new_page()
    sockets, frames = [], []

    def on_socket(ws):
        sockets.append(ws)
        ws.on("framereceived", lambda payload: frames.append(payload))

    page.on("websocket", on_socket)
    status = "c => document.getElementById('ws-status').classList.contains(c)"
    try:
        with page.expect_response(lambda r: "/api/orders" in r.url, timeout=15_000):
            page.goto(server.base + path)
        page.wait_for_function(status, arg="connected", timeout=10_000)

        server.stop()
        try:
            page.wait_for_function(status, arg="disconnected", timeout=5_000)
        except sync_api.TimeoutError:
            pytest.fail(f"{label}：伺服器停了，畫面卻沒有顯示斷線")
        page.wait_for_timeout(6_500)  # 第一次重連（5 秒後）會在這段時間內失敗
    finally:
        server.start()  # 不管上面成不成功都要把伺服器開回來，後面的測試還要用

    try:
        try:
            page.wait_for_function(status, arg="connected", timeout=RESTART_MS)
        except sync_api.TimeoutError:
            pytest.fail(f"{label}：伺服器回來 {RESTART_MS / 1000:g} 秒後仍然沒有重新連上")
        assert len(sockets) >= 2, f"{label}：狀態顯示已連線，但沒有開出新的 WebSocket"

        order = server.new_order("6")
        try:
            page.wait_for_selector(card.format(id=order), timeout=PUSH_MS)
        except sync_api.TimeoutError:
            pytest.fail(f"{label}：伺服器重啟後，新訂單 #{order} 沒有自己出現在畫面上")
        assert any(
            isinstance(f, str) and '"order_created"' in f for f in frames
        ), f"{label}：重新連上的 WebSocket 沒有收到推播"
    finally:
        page.close()
