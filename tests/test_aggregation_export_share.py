import csv
import io
import json

import pytest

QUERY = "/qlab/api/query/"
EXPORT = "/qlab/api/query/export/"
SHARES = "/qlab/api/shares/"


def run(client, url=QUERY, **payload):
    payload.setdefault("model", "Device")
    payload.setdefault("select_fields", ["id", "name", "status", "size"])
    return client.post(url, payload, format="json")


def body_text(response):
    return b"".join(response.streaming_content).decode("utf-8").lstrip("﻿")


# --- Aggregations / GROUP BY -------------------------------------------------


def test_group_by_non_pk_field_collapses_rows(client, devices):
    """Grouping by status must yield one row per status, not one per device."""
    response = run(
        client,
        select_fields=["status"],
        aggregations=[{"field": "id", "function": "count", "alias": "n"}],
        order_by=["status"],
    )
    assert response.status_code == 200, response.json()
    rows = response.json()["results"]
    assert rows == [
        {"status": "failed", "n": 1},
        {"status": "running", "n": 3},
        {"status": "stopped", "n": 1},
    ]
    assert response.json()["count"] == 3


def test_group_by_relation_path_with_sum(client, devices):
    response = run(
        client,
        select_fields=["team__name"],
        aggregations=[{"field": "size", "function": "sum", "alias": "total"}],
        order_by=["-total"],
    )
    assert response.status_code == 200, response.json()
    assert [(r["team__name"], r["total"]) for r in response.json()["results"]] == [
        ("Dev", 60),
        (None, 50),
        ("Ops", 40),
    ]


def test_count_without_field(client, devices):
    response = run(
        client,
        select_fields=["status"],
        aggregations=[{"function": "count"}],
        order_by=["status"],
    )
    assert response.status_code == 200, response.json()
    assert [r["count"] for r in response.json()["results"]] == [1, 3, 1]


@pytest.mark.parametrize(
    "aggregation",
    [
        {"function": "sum"},  # non-count needs a field
        {"field": "size", "function": "median"},  # unknown function
        {"field": "size", "function": "sum", "alias": "name"},  # clashes w/ field
        {"field": "size", "function": "sum", "alias": "bad alias"},
        {"field": "size", "function": "sum", "alias": "a__b"},
    ],
)
def test_invalid_aggregations_are_400(client, devices, aggregation):
    response = run(client, select_fields=["status"], aggregations=[aggregation])
    assert response.status_code == 400, response.content
    assert response.json()["errors"]


def test_duplicate_alias_is_rejected(client, devices):
    response = run(
        client,
        select_fields=["status"],
        aggregations=[
            {"field": "size", "function": "sum", "alias": "x"},
            {"field": "size", "function": "max", "alias": "x"},
        ],
    )
    assert response.status_code == 400


# --- HAVING ------------------------------------------------------------------


def test_having_filters_on_aggregation_alias(client, devices):
    response = run(
        client,
        select_fields=["status"],
        aggregations=[{"function": "count", "alias": "n"}],
        having={"and_operation": [{"field": "n", "op": "gte", "value": "2"}]},
    )
    assert response.status_code == 200, response.json()
    assert response.json()["results"] == [{"status": "running", "n": 3}]
    assert response.json()["count"] == 1


def test_having_combined_with_where(client, devices):
    response = run(
        client,
        select_fields=["team__name"],
        filter_fields={"and_operation": [{"field": "size", "op": "gt", "value": "15"}]},
        aggregations=[{"field": "size", "function": "sum", "alias": "total"}],
        having={
            "or_operation": [
                {"field": "total", "op": "between", "value": ["55", "65"]},
                {"field": "total", "op": "is", "value": "30"},
            ]
        },
        order_by=["team__name"],
    )
    assert response.status_code == 200, response.json()
    assert response.json()["results"] == [
        {"team__name": "Dev", "total": 60},
        {"team__name": "Ops", "total": 30},
    ]


@pytest.mark.parametrize(
    "having, aggregations",
    [
        # unknown alias
        (
            {"and_operation": [{"field": "nope", "op": "gt", "value": "1"}]},
            [{"function": "count", "alias": "n"}],
        ),
        # text search op on aggregate
        (
            {"and_operation": [{"field": "n", "op": "icontains", "value": "1"}]},
            [{"function": "count", "alias": "n"}],
        ),
        # having without aggregations
        ({"and_operation": [{"field": "n", "op": "gt", "value": "1"}]}, []),
    ],
)
def test_invalid_having_is_400(client, devices, having, aggregations):
    response = run(
        client, select_fields=["status"], aggregations=aggregations, having=having
    )
    assert response.status_code == 400


# --- Robustness: malformed input must be 400, never 500 ----------------------


@pytest.mark.parametrize(
    "extra",
    [
        {"select_fields": "name"},
        {"page": "x"},
        {"page_size": "abc"},
        {"page_size": 0},
        {"page_size": -5},
        {"filter_fields": {"and_operation": [{"field": "size"}]}},
    ],
)
def test_malformed_payload_is_400(client, devices, extra):
    response = run(client, **extra)
    assert response.status_code == 400, response.content
    assert response.json()["errors"]


def test_page_size_is_capped(client, devices):
    body = run(client, page_size=10_000).json()
    assert body["page_size"] == 500


# --- Export all ----------------------------------------------------------------


def test_export_csv_contains_all_rows(client, devices):
    response = run(client, EXPORT, page_size=2, order_by=["size"])
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/csv")
    assert response["X-QLab-Truncated"] == "false"
    rows = list(csv.DictReader(io.StringIO(body_text(response))))
    assert [r["name"] for r in rows] == ["alpha", "beta", "alpine", "gamma", "delta"]
    assert list(rows[0].keys()) == ["id", "name", "status", "size"]


def test_export_json_with_aggregation_and_having(client, devices):
    response = run(
        client,
        EXPORT,
        format="json",
        select_fields=["status"],
        aggregations=[{"function": "count", "alias": "n"}],
        having={"and_operation": [{"field": "n", "op": "lt", "value": "3"}]},
        order_by=["status"],
    )
    assert response.status_code == 200
    assert json.loads(body_text(response)) == [
        {"status": "failed", "n": 1},
        {"status": "stopped", "n": 1},
    ]


def test_export_is_truncated_at_max_rows(client, devices):
    from qlab.settings import qlab_settings

    qlab_settings.settings["MAX_EXPORT_ROWS"] = 2
    try:
        response = run(client, EXPORT, format="json")
        rows = json.loads(body_text(response))
    finally:
        qlab_settings.settings["MAX_EXPORT_ROWS"] = 100000
    assert len(rows) == 2
    assert response["X-QLab-Truncated"] == "true"
    assert response["X-QLab-Total-Count"] == "5"


def test_export_validates_like_query(client, devices):
    assert run(client, EXPORT, select_fields=["nope"]).status_code == 400
    assert run(client, EXPORT, format="xlsx").status_code == 400


# --- Share links -----------------------------------------------------------------


def big_payload():
    # Far more than fits in a URL once base64-encoded.
    return {
        "model": "Device",
        "app_label": "testapp",
        "select_fields": ["id", "name"],
        "filter_fields": {
            "and_operation": [
                {
                    "field": "name",
                    "op": "not_in",
                    "value": [f"n{i}" for i in range(190)],
                }
            ]
        },
    }


def test_share_roundtrip_for_large_query(client, devices):
    payload = big_payload()
    created = client.post(
        SHARES,
        {"app_label": "testapp", "model_name": "Device", "query_payload": payload},
        format="json",
    )
    assert created.status_code == 201, created.json()
    token = created.json()["token"]
    assert len(token) <= 16

    resolved = client.get(f"{SHARES}{token}/")
    assert resolved.status_code == 200
    assert resolved.json()["query_payload"] == payload
    assert resolved.json()["model_name"] == "Device"

    # Sharing the same query again reuses the token.
    again = client.post(
        SHARES,
        {"app_label": "testapp", "model_name": "Device", "query_payload": payload},
        format="json",
    )
    assert again.json()["token"] == token


def test_share_unknown_token_is_404(client, devices):
    assert client.get(f"{SHARES}doesnotexist/").status_code == 404


def test_share_rejects_oversized_payload(client, devices):
    from qlab.settings import qlab_settings

    qlab_settings.settings["MAX_SHARE_PAYLOAD_BYTES"] = 100
    try:
        response = client.post(
            SHARES,
            {"model_name": "Device", "query_payload": big_payload()},
            format="json",
        )
    finally:
        qlab_settings.settings["MAX_SHARE_PAYLOAD_BYTES"] = 65536
    assert response.status_code == 400


# --- Saved queries --------------------------------------------------------------


def test_saved_query_payload_update_and_run(client, devices):
    created = client.post(
        "/qlab/api/saved-queries/",
        {
            "name": "big ones",
            "app_label": "testapp",
            "model_name": "Device",
            "query_payload": {
                "model": "Device",
                "select_fields": ["name"],
                "filter_fields": {
                    "and_operation": [{"field": "size", "op": "gte", "value": "40"}]
                },
            },
        },
        format="json",
    ).json()

    payload = created["query_payload"]
    payload["filter_fields"]["and_operation"][0]["value"] = "30"
    updated = client.patch(
        f"/qlab/api/saved-queries/{created['id']}/",
        {"query_payload": payload},
        format="json",
    )
    assert updated.status_code == 200

    result = client.post(f"/qlab/api/saved-queries/{created['id']}/run/")
    assert result.status_code == 200
    assert sorted(r["name"] for r in result.json()["results"]) == [
        "alpine",
        "delta",
        "gamma",
    ]


def test_misspelled_filter_key_is_rejected(client, devices):
    response = run(
        client,
        filter_fields={"and_operaton": [{"field": "size", "op": "gt", "value": "1"}]},
    )
    assert response.status_code == 400


def test_group_by_alias_from_issue_7(client, devices):
    response = client.post(
        QUERY,
        {
            "model": "Device",
            "group_by": ["status"],
            "aggregations": [{"function": "count", "alias": "total"}],
            "order_by": ["-total", "status"],
        },
        format="json",
    )
    assert response.status_code == 200, response.json()
    assert response.json()["results"][0] == {"status": "running", "total": 3}


def test_group_by_without_aggregations_is_rejected(client, devices):
    response = client.post(
        QUERY, {"model": "Device", "group_by": ["status"]}, format="json"
    )
    assert response.status_code == 400


def test_metadata_marks_unique_fields(client, devices):
    fields = {
        f["name"]: f
        for f in client.post(
            "/qlab/api/metadata/", {"model": "Device"}, format="json"
        ).json()["fields"]
    }
    assert fields["id"]["unique"] is True
    assert fields["name"]["unique"] is False
