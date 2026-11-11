import pytest

QUERY = "/qlab/api/query/"


def run(client, **payload):
    payload.setdefault("model", "Device")
    payload.setdefault("select_fields", ["id", "name", "status", "size"])
    return client.post(QUERY, payload, format="json")


def names(response):
    return [row["name"] for row in response.json()["results"]]


def cond(field, op, value):
    return {"and_operation": [{"field": field, "op": op, "value": value}]}


@pytest.mark.parametrize(
    "op, value, expected",
    [
        ("istartswith", "AL", ["alpha", "alpine"]),
        ("iendswith", "ta", ["beta", "delta"]),
        ("icontains", "lph", ["alpha"]),
        ("in", ["beta", "gamma"], ["beta", "gamma"]),
        ("not_in", ["alpha", "alpine", "beta"], ["gamma", "delta"]),
    ],
)
def test_text_operators(client, devices, op, value, expected):
    response = run(client, filter_fields=cond("name", op, value), order_by=["name"])
    assert response.status_code == 200, response.json()
    assert sorted(names(response)) == sorted(expected)


def test_between(client, devices):
    response = run(client, filter_fields=cond("size", "between", ["20", "40"]))
    assert response.status_code == 200
    assert sorted(names(response)) == ["alpine", "beta", "gamma"]


def test_in_on_foreign_key(client, devices):
    team_ids = [str(devices[0].team_id)]
    response = run(client, filter_fields=cond("team", "in", team_ids))
    assert sorted(names(response)) == ["alpha", "alpine"]


@pytest.mark.parametrize(
    "field, op, value",
    [
        ("name", "in", "alpha"),  # list op with a scalar
        ("name", "in", []),  # empty list
        ("size", "between", ["1"]),  # needs exactly two
        ("name", "is", ["alpha"]),  # scalar op with a list
        ("size", "istartswith", "1"),  # not allowed for integers
        ("active", "in", ["true"]),  # not allowed for booleans
    ],
)
def test_invalid_operator_usage_is_rejected(client, devices, field, op, value):
    response = run(client, filter_fields=cond(field, op, value))
    assert response.status_code == 400


def test_too_many_in_values(client, devices, settings):
    settings.QLAB_SETTINGS = {**settings.QLAB_SETTINGS}
    values = [str(i) for i in range(500)]
    response = run(client, filter_fields=cond("size", "in", values))
    assert response.status_code == 400


def test_order_by(client, devices):
    asc = run(client, order_by=["size"])
    desc = run(client, order_by=["-size"])
    assert names(asc) == ["alpha", "beta", "alpine", "gamma", "delta"]
    assert names(desc) == ["delta", "gamma", "alpine", "beta", "alpha"]


def test_order_by_related_field_and_multiple(client, devices):
    response = run(client, order_by=["team__name", "-name"])
    assert response.status_code == 200
    # NULL team sorts first on SQLite; Dev before Ops after that
    assert names(response) == ["delta", "gamma", "beta", "alpine", "alpha"]


def test_order_by_rejects_unknown_field_and_too_many(client, devices):
    assert run(client, order_by=["nope"]).status_code == 400
    assert run(client, order_by=["id", "name", "size", "status"]).status_code == 400


def test_order_by_rejects_to_many_relation(client, devices):
    response = run(
        client,
        model="Team",
        select_fields=["id", "name"],
        order_by=["device__size"],
    )
    assert response.status_code == 400


def test_order_by_with_aggregation(client, devices):
    base = dict(
        model="Team",
        select_fields=["id", "name"],
        aggregations=[{"field": "device", "function": "count", "alias": "n"}],
    )
    by_name = run(client, order_by=["name"], **base).json()["results"]
    by_name_desc = run(client, order_by=["-name"], **base).json()["results"]
    assert [r["name"] for r in by_name] == ["Dev", "Ops"]
    assert [r["name"] for r in by_name_desc] == ["Ops", "Dev"]

    by_alias = run(client, order_by=["-n", "-name"], **base)
    assert by_alias.status_code == 200, by_alias.json()
    assert [(r["name"], r["n"]) for r in by_alias.json()["results"]] == [
        ("Ops", 2),
        ("Dev", 2),
    ]
    assert run(client, order_by=["device__size"], **base).status_code == 400


def test_pagination_is_stable_with_order_by(client, devices):
    page1 = run(client, order_by=["status"], page_size=2, page=1).json()
    page2 = run(client, order_by=["status"], page_size=2, page=2).json()
    page3 = run(client, order_by=["status"], page_size=2, page=3).json()
    seen = [r["id"] for p in (page1, page2, page3) for r in p["results"]]
    assert len(seen) == len(set(seen)) == 5


def test_default_response_keeps_exact_count(client, devices):
    body = run(client, page_size=2).json()
    assert body["count"] == 5
    assert body["count_exact"] is True
    assert body["total_pages"] == 3
    assert body["next"] == 2 and body["previous"] is None


def test_count_none_skips_total_but_paginates(client, devices):
    first = run(client, count="none", page_size=2, order_by=["id"]).json()
    assert first["count"] is None and first["count_exact"] is False
    assert first["total_pages"] is None
    assert first["next"] == 2 and first["previous"] is None
    assert len(first["results"]) == 2

    last = run(client, count="none", page_size=2, page=3, order_by=["id"]).json()
    assert len(last["results"]) == 1
    assert last["next"] is None and last["previous"] == 2


def test_count_none_out_of_range_page(client, devices):
    assert run(client, count="none", page_size=2, page=9).status_code == 400


def test_count_estimate_falls_back_to_exact_on_sqlite(client, devices):
    body = run(client, count="estimate", page_size=2).json()
    assert body["count"] == 5 and body["count_exact"] is True


def test_default_count_mode_setting(client, devices, settings):
    from qlab.settings import qlab_settings

    qlab_settings.settings["DEFAULT_COUNT_MODE"] = "none"
    try:
        body = run(client, page_size=2).json()
    finally:
        qlab_settings.settings["DEFAULT_COUNT_MODE"] = "exact"
    assert body["count"] is None


def test_invalid_count_mode_is_rejected(client, devices):
    assert run(client, count="approx").status_code == 400


def test_metadata_advertises_new_operators(client, devices):
    response = client.post("/qlab/api/metadata/", {"model": "Device"}, format="json")
    assert response.status_code == 200
    ops = {f["name"]: f["allowed_operations"] for f in response.json()["fields"]}
    assert {"istartswith", "iendswith", "in", "not_in"} <= set(ops["name"])
    assert {"between", "in", "not_in"} <= set(ops["size"])
    assert "between" in ops["created"]
    assert ops["active"] == ["is", "is_not"]
