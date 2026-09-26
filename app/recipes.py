"""配方业务逻辑：引用校验、循环检测、递归展开。"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable

from fastapi import HTTPException

from .schemas import RecipeEntryIn


def _existing_ids(conn: sqlite3.Connection, table: str, ids: Iterable[int]) -> set[int]:
    ids = set(ids)
    if not ids:
        return set()
    placeholders = ",".join("?" * len(ids))
    rows = conn.execute(f"SELECT id FROM {table} WHERE id IN ({placeholders})", sorted(ids))
    return {r["id"] for r in rows}


def validate_references(conn: sqlite3.Connection, entries: list[RecipeEntryIn]) -> None:
    """所有引用的原料/子配方必须已存在，否则 422。"""
    ingredient_ids = {e.ref_id for e in entries if e.ref_type == "ingredient"}
    recipe_ids = {e.ref_id for e in entries if e.ref_type == "recipe"}

    missing_ingredients = ingredient_ids - _existing_ids(conn, "ingredients", ingredient_ids)
    missing_recipes = recipe_ids - _existing_ids(conn, "recipes", recipe_ids)

    if missing_ingredients or missing_recipes:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "存在无效引用，配方未保存",
                "missing_ingredients": sorted(missing_ingredients),
                "missing_recipes": sorted(missing_recipes),
            },
        )


def _reachable_recipe_ids(conn: sqlite3.Connection, start_id: int) -> set[int]:
    """从某个配方出发，沿子配方引用可到达的所有配方 id（含自身）。"""
    seen = {start_id}
    stack = [start_id]
    while stack:
        rid = stack.pop()
        rows = conn.execute(
            "SELECT ref_id FROM recipe_entries WHERE recipe_id = ? AND ref_type = 'recipe'",
            (rid,),
        )
        for row in rows:
            sub = row["ref_id"]
            if sub not in seen:
                seen.add(sub)
                stack.append(sub)
    return seen


def validate_no_cycle(conn: sqlite3.Connection, recipe_id: int, entries: list[RecipeEntryIn]) -> None:
    """若新条目会让 recipe_id 经由子配方引用回到自身（直接或间接），则 422。"""
    for entry in entries:
        if entry.ref_type != "recipe":
            continue
        if entry.ref_id == recipe_id:
            raise HTTPException(
                status_code=422,
                detail={"message": f"配方不能直接引用自身（子配方 {recipe_id}），配方未保存"},
            )
        if recipe_id in _reachable_recipe_ids(conn, entry.ref_id):
            raise HTTPException(
                status_code=422,
                detail={
                    "message": f"引用子配方 {entry.ref_id} 会形成间接循环，配方未保存",
                    "cycle_via": entry.ref_id,
                },
            )


def replace_entries(
    conn: sqlite3.Connection,
    recipe_id: int,
    entries: list[RecipeEntryIn],
) -> None:
    """先删后插，整体替换配方条目。须在外层事务中调用，失败随事务回滚。"""
    conn.execute("DELETE FROM recipe_entries WHERE recipe_id = ?", (recipe_id,))
    conn.executemany(
        "INSERT INTO recipe_entries (recipe_id, seq, ref_type, ref_id, quantity, unit, note)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (recipe_id, i, e.ref_type, e.ref_id, e.quantity, e.unit, e.note)
            for i, e in enumerate(entries, start=1)
        ],
    )


def _entry_name(conn: sqlite3.Connection, ref_type: str, ref_id: int) -> str:
    table = "ingredients" if ref_type == "ingredient" else "recipes"
    row = conn.execute(f"SELECT name FROM {table} WHERE id = ?", (ref_id,)).fetchone()
    return row["name"] if row else f"#{ref_id}"


def _ingredient_allergens(conn: sqlite3.Connection, ingredient_id: int) -> list[str]:
    rows = conn.execute(
        "SELECT allergen FROM ingredient_allergens WHERE ingredient_id = ? ORDER BY allergen",
        (ingredient_id,),
    )
    return [r["allergen"] for r in rows]


def expand_recipe(conn: sqlite3.Connection, recipe_id: int) -> list[dict]:
    """递归展开配方的全部来源路径，每条路径终止于一个原料。

    同一子配方被多次引用时，各次引用的条目编号不同，因此路径彼此独立保留。
    """
    paths: list[dict] = []

    def walk(rid: int, prefix: list[dict], recipe_stack: frozenset[int]) -> None:
        rows = conn.execute(
            "SELECT id, seq, ref_type, ref_id, quantity, unit FROM recipe_entries"
            " WHERE recipe_id = ? ORDER BY seq",
            (rid,),
        ).fetchall()
        for row in rows:
            node = {
                "entry_id": row["id"],
                "seq": row["seq"],
                "ref_type": row["ref_type"],
                "ref_id": row["ref_id"],
                "name": _entry_name(conn, row["ref_type"], row["ref_id"]),
                "quantity": row["quantity"],
                "unit": row["unit"],
            }
            path = prefix + [node]
            if row["ref_type"] == "ingredient":
                paths.append(
                    {
                        "entry_path": path,
                        "ingredient_id": row["ref_id"],
                        "ingredient": node["name"],
                        "allergens": _ingredient_allergens(conn, row["ref_id"]),
                    }
                )
            else:
                if row["ref_id"] in recipe_stack:  # 写入时已拒绝循环，此处仅防御
                    raise RuntimeError(f"数据中存在循环引用：配方 {row['ref_id']}")
                walk(row["ref_id"], path, recipe_stack | {row["ref_id"]})

    walk(recipe_id, [], frozenset({recipe_id}))
    return paths


def build_expansion(conn: sqlite3.Connection, recipe_id: int, recipe_name: str) -> dict:
    """展开路径并按过敏原汇总来源。"""
    paths = expand_recipe(conn, recipe_id)

    allergen_sources: dict[str, list[dict]] = {}
    for p in paths:
        entry_ids = [n["entry_id"] for n in p["entry_path"]]
        for allergen in p["allergens"]:
            allergen_sources.setdefault(allergen, []).append(
                {
                    "entry_path": entry_ids,
                    "ingredient_id": p["ingredient_id"],
                    "ingredient": p["ingredient"],
                }
            )

    return {
        "recipe_id": recipe_id,
        "recipe_name": recipe_name,
        "allergens": sorted(allergen_sources),
        "allergen_sources": {k: allergen_sources[k] for k in sorted(allergen_sources)},
        "paths": paths,
    }
