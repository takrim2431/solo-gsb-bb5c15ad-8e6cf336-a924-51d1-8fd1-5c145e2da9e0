# 配方标签核查 API

供小型食品工坊使用的配方标签核查服务：登记原料及其过敏原，用原料或子配方组成配方，
查询时递归展开全部引用路径并按过敏原汇总来源，用于核对成品标签上的过敏原声明。

技术栈：Python 3.12 · FastAPI · SQLite（标准库 `sqlite3`，无 ORM）。

## 快速启动

```bash
docker compose up --build
```

一条命令即可完成依赖安装与数据库初始化（建表在应用启动时自动执行）。

- 服务地址： http://localhost:8000
- 交互式文档（Swagger UI）： http://localhost:8000/docs
- 健康检查： http://localhost:8000/healthz

### 配置

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `DATABASE_PATH` | 容器内 `/data/app.db`；本地运行为 `./data/app.db` | SQLite 数据文件路径 |

Compose 已将 `/data` 挂载为命名卷 `recipe-data`，容器重建后数据保留。
如需修改对外端口，编辑 `docker-compose.yml` 中的 `ports`（如 `"8080:8000"`）。

### 本地开发（不用 Docker）

```bash
pip install -r requirements.txt -r requirements-dev.txt
uvicorn app.main:app --reload          # http://localhost:8000
pytest                                 # 运行测试
```

## 数据模型

- **原料（ingredient）**：名称 + 过敏原列表（如 `麸质`、`乳`、`花生`）。
- **配方（recipe）**：由有序条目组成。每个条目有**独立的条目编号**（`entry_id`，全局递增），
  可引用一种原料（`ref_type=ingredient`）或一个子配方（`ref_type=recipe`），并可带数量与单位。
- 同一子配方被多次引用时，每次引用都是独立条目，展开后各自保留自己的条目编号链。

## API 一览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/ingredients` | 登记原料及过敏原 |
| GET | `/ingredients` / `/ingredients/{id}` | 查询原料 |
| PUT | `/ingredients/{id}` | 修改原料（含过敏原） |
| DELETE | `/ingredients/{id}` | 删除原料（仍被配方引用时返回 409） |
| POST | `/recipes` | 创建配方（含条目） |
| GET | `/recipes` / `/recipes/{id}` | 查询配方（含条目编号） |
| PUT | `/recipes/{id}` | 整体替换配方条目 |
| DELETE | `/recipes/{id}` | 删除配方（仍被引用时返回 409） |
| GET | `/recipes/{id}/expansion` | **展开全部引用路径，按过敏原汇总来源** |

### 保存时的校验（失败不改动原数据）

创建/更新配方在同一事务中执行，任一校验失败即整体回滚：

- 引用不存在的原料或子配方 → `422`，响应列出缺失的 id；
- 直接或间接循环引用（如 A→B→A、配方引用自身）→ `422`；
- 名称重复 → `409`。

### 示例

```bash
# 1. 登记原料
curl -X POST localhost:8000/ingredients -H 'Content-Type: application/json' \
  -d '{"name": "面粉", "allergens": ["麸质"]}'
curl -X POST localhost:8000/ingredients -H 'Content-Type: application/json' \
  -d '{"name": "黄油", "allergens": ["乳"]}'

# 2. 子配方“面团”
curl -X POST localhost:8000/recipes -H 'Content-Type: application/json' -d '{
  "name": "面团",
  "entries": [
    {"ref_type": "ingredient", "ref_id": 1, "quantity": 100, "unit": "g"},
    {"ref_type": "ingredient", "ref_id": 2, "quantity": 20, "unit": "g"}
  ]}'

# 3. 成品“饼干”两次引用同一子配方
curl -X POST localhost:8000/recipes -H 'Content-Type: application/json' -d '{
  "name": "饼干",
  "entries": [
    {"ref_type": "recipe", "ref_id": 1},
    {"ref_type": "recipe", "ref_id": 1}
  ]}'

# 4. 展开核查标签
curl localhost:8000/recipes/2/expansion
```

`expansion` 响应要点：

- `allergens`：全部过敏原并集，直接对应标签上的过敏原声明；
- `allergen_sources`：按过敏原汇总，每种过敏原列出所有来源路径
  （`entry_path` 为从成品到原料的**条目编号链**，同一子配方重复使用会分别列出）；
- `paths`：每条来源路径的完整节点明细（条目编号、名称、数量、单位）及末端原料的过敏原。

```json
{
  "recipe_id": 2,
  "recipe_name": "饼干",
  "allergens": ["乳", "麸质"],
  "allergen_sources": {
    "麸质": [
      {"entry_path": [3, 1], "ingredient_id": 1, "ingredient": "面粉"},
      {"entry_path": [4, 1], "ingredient_id": 1, "ingredient": "面粉"}
    ]
  }
}
```

## 项目结构

```
app/
  main.py     # 路由与事务边界
  recipes.py  # 引用校验、循环检测、递归展开
  db.py       # SQLite 连接与建表
  schemas.py  # Pydantic 请求/响应模型
tests/test_api.py
Dockerfile · docker-compose.yml · requirements.txt
```
