"""Pydantic 資料模型（API 的請求 / 回應格式）。"""

from typing import Optional

from pydantic import BaseModel


# ---------- drinks ----------
class DrinkBase(BaseModel):
    name: str
    category: str
    is_available: bool = True


class DrinkCreate(DrinkBase):
    pass


class Drink(DrinkBase):
    id: int


# ---------- ingredients ----------
class IngredientBase(BaseModel):
    name: str
    unit: str
    current_stock: float = 0
    reorder_point: float = 0


class IngredientCreate(IngredientBase):
    pass


class Ingredient(IngredientBase):
    id: int


# ---------- recipes ----------
class RecipeItem(BaseModel):
    drink_id: int
    ingredient_id: int
    quantity_needed: float


# ---------- orders ----------
class OrderCreate(BaseModel):
    table_number: int
    drink_id: int
    quantity: int = 1
    special_request: Optional[str] = None


class Order(BaseModel):
    id: int
    table_number: int
    drink_id: int
    quantity: int
    special_request: Optional[str] = None
    status: str  # new / preparing / completed / delivered / cancelled
    payment_status: str  # unpaid / paid
    placed_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    delivered_at: Optional[str] = None
