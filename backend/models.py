"""Pydantic 資料模型（API 的請求 / 回應格式）。"""

from typing import Annotated, Optional

from pydantic import BaseModel, StringConstraints


# 桌號是文字不是數字：除了「1」~「13」，還要能存「吧台」「站位」。
# 這裡只擋空白與過長的值，不寫死允許清單——之後加桌次只要改前端的選單，
# 不必跟著改後端（白名單對不上會變成看不懂的 422）。
TableNumber = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20)
]


# ---------- drinks ----------
class DrinkBase(BaseModel):
    name: str
    category: str
    is_available: bool = True
    price: Optional[int] = None  # 售價（NT$，整數）。未定價為 None


class DrinkCreate(DrinkBase):
    pass


class Drink(DrinkBase):
    id: int


class DrinkUpdate(BaseModel):
    """部分更新：只帶要改的欄位。"""

    name: Optional[str] = None
    category: Optional[str] = None
    is_available: Optional[bool] = None
    price: Optional[int] = None


# ---------- ingredients ----------
class IngredientBase(BaseModel):
    name: str
    unit: str
    current_stock: float = 0
    reorder_point: float = 0
    # 當天現買、不會缺貨的原料：不扣庫存、不擋下單、不進補貨警示。
    # current_stock / reorder_point 的數字仍然保留，取消勾選後可以接回去
    is_unlimited: bool = False


class IngredientCreate(IngredientBase):
    pass


class Ingredient(IngredientBase):
    id: int


class IngredientUpdate(BaseModel):
    """部分更新：只帶要改的欄位。"""

    name: Optional[str] = None
    unit: Optional[str] = None
    current_stock: Optional[float] = None
    reorder_point: Optional[float] = None
    is_unlimited: Optional[bool] = None


class StockAdjust(BaseModel):
    """庫存增減：正數進貨、負數耗損。"""

    delta: float


# ---------- recipes ----------
class RecipeItem(BaseModel):
    drink_id: int
    ingredient_id: int
    quantity_needed: float


class RecipeItemIn(BaseModel):
    """設定配方時的單項原料（drink_id 由路徑帶入）。"""

    ingredient_id: int
    quantity_needed: float


# ---------- orders ----------
class OrderItemIn(BaseModel):
    """訂單裡的一個品項。"""

    drink_id: int
    quantity: int = 1
    special_request: Optional[str] = None


class OrderCreate(BaseModel):
    """一張訂單：一個桌號 + 一到多個品項。"""

    table_number: TableNumber
    items: list[OrderItemIn]


class OrderEdit(BaseModel):
    """服務生修改訂單（僅限尚未進入製作的訂單）。

    items 一併帶上時會整組取代舊品項，庫存跟著重新計算。
    """

    table_number: Optional[TableNumber] = None
    items: Optional[list[OrderItemIn]] = None


class OrderItem(BaseModel):
    id: int
    drink_id: int
    drink_name: str
    quantity: int
    special_request: Optional[str] = None
    status: str
    unit_price: Optional[int] = None  # 下單當下的售價快照


class Order(BaseModel):
    id: int
    table_number: str
    items: list[OrderItem]
    item_count: int
    total_quantity: int
    edit_count: int = 0
    status: str  # new / preparing / completed / delivered / cancelled
    payment_status: str  # unpaid / paid
    payment_completed_at: Optional[str] = None
    placed_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    delivered_at: Optional[str] = None


class OrderStatusUpdate(BaseModel):
    status: str  # 目標狀態：preparing / completed / delivered / cancelled


class OrderPaymentUpdate(BaseModel):
    payment_status: str = "paid"  # unpaid / paid
