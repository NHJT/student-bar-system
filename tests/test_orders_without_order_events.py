"""order_events 表不存在時，訂單查詢仍要正常回應。

order_events 是「退回上一個狀態」那次改版才加的表。還沒跑過 init_db.py 的
資料庫沒有它，而算「被退回幾次」的 _reverts_of 位在每一個訂單查詢的必經之路上，
沒防護的話吧台、外場、經理三頁會一起掛掉。這裡就是釘住那道防護。
"""

from sqlalchemy import text


def _seed_one_order(engine) -> int:
    """塞一張最小可用的訂單，回傳 group_id。"""
    with engine.connect() as conn:
        drink_id = conn.execute(
            text(
                "INSERT INTO drinks (name, category, price) "
                "VALUES ('Negroni', '琴酒', 150) RETURNING id"
            )
        ).scalar()
        group_id = conn.execute(
            text(
                "INSERT INTO order_groups (table_number, status) "
                "VALUES ('吧台左邊', 'completed') RETURNING id"
            )
        ).scalar()
        conn.execute(
            text(
                "INSERT INTO order_items (group_id, drink_id, quantity, status) "
                "VALUES (:g, :d, 2, 'completed')"
            ),
            {"g": group_id, "d": drink_id},
        )
        conn.commit()
    return group_id


def _client():
    """等 fresh_db 設好 DATABASE_URL 才 import，engine 才會連到測試資料庫。

    不用 with TestClient(app)：那會跑 lifespan，把 23:59 的結算排程也一起啟動。
    """
    from fastapi.testclient import TestClient

    from backend.main import app

    return TestClient(app)


def test_list_orders_ok_without_order_events(fresh_db):
    """沒有 order_events 表時 GET /api/orders 仍回 200，revert_count 當成 0。"""
    _seed_one_order(fresh_db)
    with fresh_db.connect() as conn:
        conn.execute(text("DROP TABLE order_events"))
        conn.commit()
        assert conn.execute(
            text("SELECT to_regclass('public.order_events')")
        ).scalar() is None

    response = _client().get("/api/orders")

    assert response.status_code == 200
    orders = response.json()
    assert len(orders) == 1
    assert orders[0]["revert_count"] == 0
    assert orders[0]["summary"] == "Negroni x2"


def test_get_one_order_ok_without_order_events(fresh_db):
    """單張訂單走的是同一條 _with_items，一併確認。"""
    group_id = _seed_one_order(fresh_db)
    with fresh_db.connect() as conn:
        conn.execute(text("DROP TABLE order_events"))
        conn.commit()

    response = _client().get(f"/api/orders/{group_id}")

    assert response.status_code == 200
    assert response.json()["revert_count"] == 0


def test_revert_says_to_run_init_db_without_order_events(fresh_db):
    """退回一定要留紀錄，所以表不在時明講要跑 migration，不是默默成功。"""
    group_id = _seed_one_order(fresh_db)
    with fresh_db.connect() as conn:
        conn.execute(text("DROP TABLE order_events"))
        conn.commit()

    response = _client().patch(f"/api/orders/{group_id}/revert")

    assert response.status_code == 503
    assert "init_db.py" in response.json()["detail"]
    with fresh_db.connect() as conn:  # 狀態沒有被改掉
        status = conn.execute(
            text("SELECT status FROM order_groups WHERE id = :id"), {"id": group_id}
        ).scalar()
    assert status == "completed"


def test_revert_count_still_works_with_order_events(fresh_db):
    """表存在時行為不變——防護不能把功能一起關掉。"""
    group_id = _seed_one_order(fresh_db)
    with fresh_db.connect() as conn:
        conn.execute(
            text(
                "INSERT INTO order_events "
                "(group_id, event_type, from_status, to_status) "
                "VALUES (:g, 'revert', 'completed', 'preparing')"
            ),
            {"g": group_id},
        )
        conn.commit()

    response = _client().get("/api/orders")

    assert response.status_code == 200
    assert response.json()[0]["revert_count"] == 1
