import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    with TestClient(app) as c:
        yield c


def make_ingredient(client, name, allergens=()):
    r = client.post("/ingredients", json={"name": name, "allergens": list(allergens)})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def make_recipe(client, name, entries):
    r = client.post("/recipes", json={"name": name, "entries": entries})
    assert r.status_code == 201, r.text
    return r.json()


# ------------------------------------------------------------ 原料


def test_ingredient_crud(client):
    iid = make_ingredient(client, "面粉", ["麸质", "小麦"])
    got = client.get(f"/ingredients/{iid}").json()
    assert got["name"] == "面粉"
    assert got["allergens"] == ["小麦", "麸质"]  # 按字典序

    r = client.put(f"/ingredients/{iid}", json={"name": "面粉", "allergens": ["麸质"]})
    assert r.status_code == 200
    assert r.json()["allergens"] == ["麸质"]

    assert client.delete(f"/ingredients/{iid}").status_code == 204
    assert client.get(f"/ingredients/{iid}").status_code == 404


def test_ingredient_name_unique(client):
    make_ingredient(client, "糖")
    r = client.post("/ingredients", json={"name": "糖", "allergens": []})
    assert r.status_code == 409


# ------------------------------------------------------------ 配方保存校验


def test_recipe_rejects_unknown_references_and_saves_nothing(client):
    flour = make_ingredient(client, "面粉", ["麸质"])
    r = client.post(
        "/recipes",
        json={
            "name": "饼干",
            "entries": [
                {"ref_type": "ingredient", "ref_id": flour},
                {"ref_type": "ingredient", "ref_id": 9999},
                {"ref_type": "recipe", "ref_id": 8888},
            ],
        },
    )
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["missing_ingredients"] == [9999]
    assert detail["missing_recipes"] == [8888]
    assert client.get("/recipes").json() == []  # 未留下任何数据


def test_direct_cycle_rejected_on_update(client):
    flour = make_ingredient(client, "面粉", ["麸质"])
    recipe = make_recipe(client, "面团", [{"ref_type": "ingredient", "ref_id": flour, "quantity": 100, "unit": "g"}])
    rid = recipe["id"]

    r = client.put(
        f"/recipes/{rid}",
        json={"name": "面团", "entries": [{"ref_type": "recipe", "ref_id": rid}]},
    )
    assert r.status_code == 422

    after = client.get(f"/recipes/{rid}").json()  # 原配方未被改动
    assert after["entries"][0]["ref_type"] == "ingredient"
    assert after["entries"][0]["ref_id"] == flour


def test_indirect_cycle_rejected_and_original_kept(client):
    flour = make_ingredient(client, "面粉", ["麸质"])
    a = make_recipe(client, "A", [{"ref_type": "ingredient", "ref_id": flour}])
    b = make_recipe(client, "B", [{"ref_type": "recipe", "ref_id": a["id"]}])
    c = make_recipe(client, "C", [{"ref_type": "recipe", "ref_id": b["id"]}])

    # A -> C 会形成 A <- B <- C <- A 的间接循环
    r = client.put(
        f"/recipes/{a['id']}",
        json={"name": "A", "entries": [{"ref_type": "recipe", "ref_id": c["id"]}]},
    )
    assert r.status_code == 422

    after = client.get(f"/recipes/{a['id']}").json()
    assert [e["ref_type"] for e in after["entries"]] == ["ingredient"]  # 未被改动


def test_update_with_unknown_ref_keeps_original_entries(client):
    flour = make_ingredient(client, "面粉", ["麸质"])
    recipe = make_recipe(client, "面团", [{"ref_type": "ingredient", "ref_id": flour}])
    rid = recipe["id"]

    r = client.put(
        f"/recipes/{rid}",
        json={"name": "面团", "entries": [{"ref_type": "ingredient", "ref_id": 4242}]},
    )
    assert r.status_code == 422
    after = client.get(f"/recipes/{rid}").json()
    assert len(after["entries"]) == 1
    assert after["entries"][0]["ref_id"] == flour


# ------------------------------------------------------------ 展开与过敏原汇总


def test_expansion_aggregates_allergens_per_path(client):
    flour = make_ingredient(client, "面粉", ["麸质"])
    butter = make_ingredient(client, "黄油", ["乳"])
    sugar = make_ingredient(client, "糖")

    dough = make_recipe(
        client,
        "面团",
        [
            {"ref_type": "ingredient", "ref_id": flour, "quantity": 100, "unit": "g"},
            {"ref_type": "ingredient", "ref_id": butter, "quantity": 20, "unit": "g"},
        ],
    )
    cookie = make_recipe(
        client,
        "饼干",
        [
            {"ref_type": "recipe", "ref_id": dough["id"], "quantity": 1, "unit": "份"},
            {"ref_type": "ingredient", "ref_id": sugar, "quantity": 30, "unit": "g"},
        ],
    )

    data = client.get(f"/recipes/{cookie['id']}/expansion").json()
    assert data["allergens"] == ["乳", "麸质"]
    assert len(data["paths"]) == 3  # 面粉、黄油、糖 各一条路径

    gluten_sources = data["allergen_sources"]["麸质"]
    assert len(gluten_sources) == 1
    assert gluten_sources[0]["ingredient"] == "面粉"
    # 路径为 饼干条目 -> 面团条目，两级条目编号
    assert len(gluten_sources[0]["entry_path"]) == 2

    # 每条路径末端是原料，路径节点带条目编号
    for p in data["paths"]:
        assert p["entry_path"][-1]["ref_type"] == "ingredient"
        assert all("entry_id" in n for n in p["entry_path"])


def test_reused_subrecipe_keeps_distinct_entry_ids(client):
    flour = make_ingredient(client, "面粉", ["麸质"])
    dough = make_recipe(client, "面团", [{"ref_type": "ingredient", "ref_id": flour}])

    # 同一子配方被引用两次
    double = make_recipe(
        client,
        "双份",
        [
            {"ref_type": "recipe", "ref_id": dough["id"]},
            {"ref_type": "recipe", "ref_id": dough["id"]},
        ],
    )

    data = client.get(f"/recipes/{double['id']}/expansion").json()
    assert len(data["paths"]) == 2
    top_entry_ids = {p["entry_path"][0]["entry_id"] for p in data["paths"]}
    assert len(top_entry_ids) == 2  # 两次引用各有独立条目编号

    sources = data["allergen_sources"]["麸质"]
    assert len(sources) == 2  # 同一过敏原也按两条来源路径分别列出
    assert {tuple(s["entry_path"]) for s in sources} != set()


def test_expansion_reflects_allergen_updates(client):
    flour = make_ingredient(client, "面粉", ["麸质"])
    recipe = make_recipe(client, "面团", [{"ref_type": "ingredient", "ref_id": flour}])

    client.put(f"/ingredients/{flour}", json={"name": "面粉", "allergens": ["麸质", "小麦"]})
    data = client.get(f"/recipes/{recipe['id']}/expansion").json()
    assert data["allergens"] == ["小麦", "麸质"]


# ------------------------------------------------------------ 删除保护


def test_delete_referenced_ingredient_and_recipe_rejected(client):
    flour = make_ingredient(client, "面粉", ["麸质"])
    dough = make_recipe(client, "面团", [{"ref_type": "ingredient", "ref_id": flour}])

    r = client.delete(f"/ingredients/{flour}")
    assert r.status_code == 409
    assert r.json()["detail"]["referenced_by_recipes"] == [dough["id"]]

    make_recipe(client, "饼干", [{"ref_type": "recipe", "ref_id": dough["id"]}])
    assert client.delete(f"/recipes/{dough['id']}").status_code == 409
