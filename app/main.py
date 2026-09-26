"""配方标签核查 API：原料/过敏原登记、配方组成、标签展开核查。"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException

from . import db, recipes
from .schemas import (
    ExpansionOut,
    IngredientIn,
    IngredientOut,
    RecipeIn,
    RecipeOut,
    RecipeSummary,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()  # 启动时自动建表
    yield


app = FastAPI(
    title="配方标签核查 API",
    version="1.0.0",
    lifespan=lifespan,
)


def get_db() -> Iterator[sqlite3.Connection]:
    conn = db.connect()
    try:
        yield conn
    finally:
        conn.close()


Conn = Depends(get_db)


# ---------------------------------------------------------------- 基础


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


# ---------------------------------------------------------------- 原料


def _ingredient_out(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    allergens = conn.execute(
        "SELECT allergen FROM ingredient_allergens WHERE ingredient_id = ? ORDER BY allergen",
        (row["id"],),
    )
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "allergens": [r["allergen"] for r in allergens],
    }


@app.post("/ingredients", response_model=IngredientOut, status_code=201)
def create_ingredient(payload: IngredientIn, conn: sqlite3.Connection = Conn):
    try:
        with conn:
            cur = conn.execute(
                "INSERT INTO ingredients (name, description) VALUES (?, ?)",
                (payload.name, payload.description),
            )
            ingredient_id = cur.lastrowid
            conn.executemany(
                "INSERT INTO ingredient_allergens (ingredient_id, allergen) VALUES (?, ?)",
                [(ingredient_id, a) for a in payload.allergens],
            )
    except sqlite3.IntegrityError:
        raise HTTPException(409, f"原料名称已存在：{payload.name}")
    row = conn.execute("SELECT * FROM ingredients WHERE id = ?", (ingredient_id,)).fetchone()
    return _ingredient_out(conn, row)


@app.get("/ingredients", response_model=list[IngredientOut])
def list_ingredients(conn: sqlite3.Connection = Conn):
    rows = conn.execute("SELECT * FROM ingredients ORDER BY id").fetchall()
    return [_ingredient_out(conn, r) for r in rows]


@app.get("/ingredients/{ingredient_id}", response_model=IngredientOut)
def get_ingredient(ingredient_id: int, conn: sqlite3.Connection = Conn):
    row = conn.execute("SELECT * FROM ingredients WHERE id = ?", (ingredient_id,)).fetchone()
    if not row:
        raise HTTPException(404, "原料不存在")
    return _ingredient_out(conn, row)


@app.put("/ingredients/{ingredient_id}", response_model=IngredientOut)
def update_ingredient(ingredient_id: int, payload: IngredientIn, conn: sqlite3.Connection = Conn):
    if not conn.execute("SELECT 1 FROM ingredients WHERE id = ?", (ingredient_id,)).fetchone():
        raise HTTPException(404, "原料不存在")
    try:
        with conn:
            conn.execute(
                "UPDATE ingredients SET name = ?, description = ? WHERE id = ?",
                (payload.name, payload.description, ingredient_id),
            )
            conn.execute("DELETE FROM ingredient_allergens WHERE ingredient_id = ?", (ingredient_id,))
            conn.executemany(
                "INSERT INTO ingredient_allergens (ingredient_id, allergen) VALUES (?, ?)",
                [(ingredient_id, a) for a in payload.allergens],
            )
    except sqlite3.IntegrityError:
        raise HTTPException(409, f"原料名称已存在：{payload.name}")
    row = conn.execute("SELECT * FROM ingredients WHERE id = ?", (ingredient_id,)).fetchone()
    return _ingredient_out(conn, row)


@app.delete("/ingredients/{ingredient_id}", status_code=204)
def delete_ingredient(ingredient_id: int, conn: sqlite3.Connection = Conn):
    if not conn.execute("SELECT 1 FROM ingredients WHERE id = ?", (ingredient_id,)).fetchone():
        raise HTTPException(404, "原料不存在")
    refs = conn.execute(
        "SELECT DISTINCT recipe_id FROM recipe_entries WHERE ref_type = 'ingredient' AND ref_id = ?",
        (ingredient_id,),
    ).fetchall()
    if refs:
        raise HTTPException(
            409,
            {"message": "原料仍被配方引用，不能删除", "referenced_by_recipes": [r["recipe_id"] for r in refs]},
        )
    with conn:
        conn.execute("DELETE FROM ingredients WHERE id = ?", (ingredient_id,))


# ---------------------------------------------------------------- 配方


def _recipe_out(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    entries = conn.execute(
        "SELECT id, seq, ref_type, ref_id, quantity, unit, note FROM recipe_entries"
        " WHERE recipe_id = ? ORDER BY seq",
        (row["id"],),
    ).fetchall()
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "entries": [
            {
                "entry_id": e["id"],
                "seq": e["seq"],
                "ref_type": e["ref_type"],
                "ref_id": e["ref_id"],
                "name": recipes._entry_name(conn, e["ref_type"], e["ref_id"]),
                "quantity": e["quantity"],
                "unit": e["unit"],
                "note": e["note"],
            }
            for e in entries
        ],
    }


@app.post("/recipes", response_model=RecipeOut, status_code=201)
def create_recipe(payload: RecipeIn, conn: sqlite3.Connection = Conn):
    try:
        with conn:  # 任一步失败整体回滚，不留半截配方
            cur = conn.execute(
                "INSERT INTO recipes (name, description) VALUES (?, ?)",
                (payload.name, payload.description),
            )
            recipe_id = cur.lastrowid
            recipes.validate_references(conn, payload.entries)
            recipes.replace_entries(conn, recipe_id, payload.entries)
    except sqlite3.IntegrityError:
        raise HTTPException(409, f"配方名称已存在：{payload.name}")
    row = conn.execute("SELECT * FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
    return _recipe_out(conn, row)


@app.get("/recipes", response_model=list[RecipeSummary])
def list_recipes(conn: sqlite3.Connection = Conn):
    rows = conn.execute(
        "SELECT r.*, COUNT(e.id) AS entry_count FROM recipes r"
        " LEFT JOIN recipe_entries e ON e.recipe_id = r.id"
        " GROUP BY r.id ORDER BY r.id"
    ).fetchall()
    return [
        {"id": r["id"], "name": r["name"], "description": r["description"], "entry_count": r["entry_count"]}
        for r in rows
    ]


@app.get("/recipes/{recipe_id}", response_model=RecipeOut)
def get_recipe(recipe_id: int, conn: sqlite3.Connection = Conn):
    row = conn.execute("SELECT * FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
    if not row:
        raise HTTPException(404, "配方不存在")
    return _recipe_out(conn, row)


@app.put("/recipes/{recipe_id}", response_model=RecipeOut)
def update_recipe(recipe_id: int, payload: RecipeIn, conn: sqlite3.Connection = Conn):
    if not conn.execute("SELECT 1 FROM recipes WHERE id = ?", (recipe_id,)).fetchone():
        raise HTTPException(404, "配方不存在")
    try:
        with conn:  # 校验失败回滚，原配方保持不变
            conn.execute(
                "UPDATE recipes SET name = ?, description = ?, updated_at = datetime('now') WHERE id = ?",
                (payload.name, payload.description, recipe_id),
            )
            recipes.validate_references(conn, payload.entries)
            recipes.validate_no_cycle(conn, recipe_id, payload.entries)
            recipes.replace_entries(conn, recipe_id, payload.entries)
    except sqlite3.IntegrityError:
        raise HTTPException(409, f"配方名称已存在：{payload.name}")
    row = conn.execute("SELECT * FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
    return _recipe_out(conn, row)


@app.delete("/recipes/{recipe_id}", status_code=204)
def delete_recipe(recipe_id: int, conn: sqlite3.Connection = Conn):
    if not conn.execute("SELECT 1 FROM recipes WHERE id = ?", (recipe_id,)).fetchone():
        raise HTTPException(404, "配方不存在")
    refs = conn.execute(
        "SELECT DISTINCT recipe_id FROM recipe_entries WHERE ref_type = 'recipe' AND ref_id = ?",
        (recipe_id,),
    ).fetchall()
    if refs:
        raise HTTPException(
            409,
            {"message": "配方仍被其他配方引用，不能删除", "referenced_by_recipes": [r["recipe_id"] for r in refs]},
        )
    with conn:
        conn.execute("DELETE FROM recipes WHERE id = ?", (recipe_id,))


@app.get("/recipes/{recipe_id}/expansion", response_model=ExpansionOut)
def expand_recipe(recipe_id: int, conn: sqlite3.Connection = Conn):
    """展开全部引用路径，按过敏原汇总来源，供标签核对。"""
    row = conn.execute("SELECT * FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
    if not row:
        raise HTTPException(404, "配方不存在")
    return recipes.build_expansion(conn, recipe_id, row["name"])
