"""SQLite 连接与建表。数据库路径通过环境变量 DATABASE_PATH 配置。"""

from __future__ import annotations

import os
import sqlite3

DEFAULT_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "app.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS ingredients (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS ingredient_allergens (
    ingredient_id INTEGER NOT NULL REFERENCES ingredients(id) ON DELETE CASCADE,
    allergen      TEXT NOT NULL,
    PRIMARY KEY (ingredient_id, allergen)
);

CREATE TABLE IF NOT EXISTS recipes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 每个条目都有独立的条目编号(id)。ref_id 指向 ingredients.id 或 recipes.id，
-- 由 ref_type 区分，引用有效性在业务层校验。
CREATE TABLE IF NOT EXISTS recipe_entries (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    recipe_id  INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
    seq        INTEGER NOT NULL,
    ref_type   TEXT NOT NULL CHECK (ref_type IN ('ingredient', 'recipe')),
    ref_id     INTEGER NOT NULL,
    quantity   REAL,
    unit       TEXT NOT NULL DEFAULT '',
    note       TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_entries_recipe ON recipe_entries(recipe_id, seq);
CREATE INDEX IF NOT EXISTS idx_entries_ref ON recipe_entries(ref_type, ref_id);
"""


def database_path() -> str:
    return os.environ.get("DATABASE_PATH", DEFAULT_DB_PATH)


def connect(path: str | None = None) -> sqlite3.Connection:
    db_path = path or database_path()
    if db_path != ":memory:":
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(path: str | None = None) -> None:
    """建表（幂等），随应用启动执行。"""
    with connect(path) as conn:
        conn.executescript(SCHEMA)
