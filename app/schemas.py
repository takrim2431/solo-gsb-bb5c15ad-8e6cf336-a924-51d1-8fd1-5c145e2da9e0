"""请求/响应模型（Pydantic）。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


def _clean_str_list(values: list[str]) -> list[str]:
    """去空白、去空串、保序去重。"""
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        v = v.strip()
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out


class IngredientIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    allergens: list[str] = []

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name 不能为空")
        return v

    @field_validator("allergens")
    @classmethod
    def _clean(cls, v: list[str]) -> list[str]:
        return _clean_str_list(v)


class IngredientOut(BaseModel):
    id: int
    name: str
    description: str
    allergens: list[str]


class RecipeEntryIn(BaseModel):
    ref_type: Literal["ingredient", "recipe"]
    ref_id: int = Field(gt=0)
    quantity: float | None = Field(default=None, ge=0)
    unit: str = ""
    note: str = ""


class RecipeEntryOut(BaseModel):
    entry_id: int
    seq: int
    ref_type: str
    ref_id: int
    name: str
    quantity: float | None
    unit: str
    note: str


class RecipeIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    entries: list[RecipeEntryIn] = Field(min_length=1)

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name 不能为空")
        return v


class RecipeOut(BaseModel):
    id: int
    name: str
    description: str
    entries: list[RecipeEntryOut]


class RecipeSummary(BaseModel):
    id: int
    name: str
    description: str
    entry_count: int


class PathNode(BaseModel):
    entry_id: int
    seq: int
    ref_type: str
    ref_id: int
    name: str
    quantity: float | None
    unit: str


class ExpandedPath(BaseModel):
    """一条从根配方到某个原料的来源路径。"""

    entry_path: list[PathNode]
    ingredient_id: int
    ingredient: str
    allergens: list[str]


class AllergenSource(BaseModel):
    entry_path: list[int]  # 条目编号链
    ingredient_id: int
    ingredient: str


class ExpansionOut(BaseModel):
    recipe_id: int
    recipe_name: str
    allergens: list[str]  # 全部过敏原的并集（排序后）
    allergen_sources: dict[str, list[AllergenSource]]  # 按过敏原汇总来源路径
    paths: list[ExpandedPath]
