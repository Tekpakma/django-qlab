"""Duplicates, inflated aggregates and access control on relation traversal."""

import csv
import io

import pytest
from django.contrib.auth.models import Group

from qlab.models import ModelRegistry, QueryRunHistory

QUERY = "/qlab/api/query/"


def run(client, **payload):
    payload.setdefault("model", "Team")
    payload.setdefault("select_fields", ["id", "name"])
    return client.post(QUERY, payload, format="json")


def cond(field, op, value):
    return {"and_operation": [{"field": field, "op": op, "value": value}]}


# --- No duplicate rows from filters across to-many relations ----------------


def test_filter_on_to_many_returns_each_object_once(client, devices):
    response = run(client, filter_fields=cond("device__size", "gt", "5"))
    body = response.json()
    assert response.status_code == 200, body
    assert sorted(r["name"] for r in body["results"]) == ["Dev", "Ops"]
    assert body["count"] == 2


def test_filter_on_to_many_paginates_without_duplicates(client, devices):
    ids = []
    for page in (1, 2, 3):
        body = run(
            client,
            filter_fields=cond("device__size", "gt", "5"),
            page_size=1,
            page=page,
        ).json()
        if "results" not in body:
            break
        ids += [r["id"] for r in body["results"]]
    assert len(ids) == len(set(ids)) == 2


def test_selecting_to_many_field_still_returns_one_row_per_related(client, devices):
    body = run(client, select_fields=["name", "device__name"], order_by=["name"]).json()
    assert body["count"] == 4


# --- Aggregates are not inflated by joins -----------------------------------


def test_sum_with_filter_on_other_to_many_is_not_multiplied(client, members):
    # Ops has 3 members; joining members would triple the device sizes.
    body = run(
        client,
        select_fields=["name"],
        aggregations=[{"field": "device__size", "function": "sum", "alias": "s"}],
        filter_fields=cond("member__name", "in", ["ann", "bob", "cid", "dan"]),
        order_by=["name"],
    ).json()
    assert body["results"] == [{"name": "Dev", "s": 60}, {"name": "Ops", "s": 40}]


def test_sum_across_two_to_many_relations_is_rejected(client, members):
    response = run(
        client,
        select_fields=["name"],
        aggregations=[
            {"field": "device__size", "function": "sum", "alias": "s"},
            {"field": "member", "function": "count", "alias": "m"},
        ],
    )
    assert response.status_code == 400
    assert "multiplied" in response.json()["errors"][0]["msg"]


def test_distinct_counts_across_two_to_many_relations_are_allowed(client, members):
    body = run(
        client,
        select_fields=["name"],
        aggregations=[
            {"field": "device", "function": "count", "distinct": True, "alias": "d"},
            {"field": "member", "function": "count", "distinct": True, "alias": "m"},
        ],
        order_by=["name"],
    ).json()
    assert body["results"] == [
        {"name": "Dev", "d": 2, "m": 1},
        {"name": "Ops", "d": 2, "m": 3},
    ]


def test_fieldless_count_counts_objects_not_joined_rows(client, members):
    body = run(
        client,
        model="Device",
        select_fields=["team__name"],
        aggregations=[{"function": "count", "alias": "n"}],
        filter_fields=cond("team__member__name", "in", ["ann", "bob", "cid"]),
    ).json()
    assert body["results"] == [{"team__name": "Ops", "n": 2}]


def test_filter_on_aggregated_relation_restricts_aggregated_rows(client, devices):
    body = run(
        client,
        select_fields=["name"],
        aggregations=[{"field": "device__size", "function": "sum", "alias": "s"}],
        filter_fields=cond("device__size", "gt", "25"),
        order_by=["name"],
    ).json()
    assert body["results"] == [{"name": "Dev", "s": 40}, {"name": "Ops", "s": 30}]


# --- Access control ----------------------------------------------------------


def test_password_hash_is_never_exposed(client, members):
    for select in (["name", "user__password"], ["name"]):
        response = run(
            client,
            model="Member",
            select_fields=select,
            **(
                {}
                if select != ["name"]
                else {"filter_fields": cond("user__password", "istartswith", "p")}
            ),
        )
        assert response.status_code == 400
    meta = client.post(
        "/qlab/api/metadata/",
        {"model": "Member", "relation_depth": 1},
        format="json",
    ).json()
    assert "user__password" not in meta["all_lookups"]
    assert "user__username" in meta["all_lookups"]
    suggest = client.post(
        "/qlab/api/suggest/",
        {"model": "Member", "field": "user__password"},
        format="json",
    )
    assert suggest.status_code == 400


def test_relation_into_disabled_model_is_blocked(client, devices):
    ModelRegistry.objects.filter(model_name="Team").update(status="disabled")
    response = run(client, model="Device", select_fields=["name", "team__name"])
    assert response.status_code == 403
    response = run(
        client,
        model="Device",
        select_fields=["name"],
        filter_fields=cond("team__name", "is", "Ops"),
    )
    assert response.status_code == 403
    suggest = client.post(
        "/qlab/api/suggest/", {"model": "Device", "field": "team__name"}, format="json"
    )
    assert suggest.status_code == 403
    # Without the relation the query still works.
    assert run(client, model="Device", select_fields=["name"]).status_code == 200


def test_relation_into_restricted_model_needs_group(client, devices):
    entry = ModelRegistry.objects.get(model_name="Team")
    entry.is_restricted = True
    entry.save()
    group = Group.objects.create(name="team-readers")
    entry.allowed_groups.add(group)
    assert run(client, model="Device", select_fields=["team__name"]).status_code == 403

    from django.contrib.auth import get_user_model

    get_user_model().objects.get(username="tester").groups.add(group)
    assert run(client, model="Device", select_fields=["team__name"]).status_code == 200


def test_relation_depth_is_limited(client, members):
    from qlab.settings import qlab_settings

    qlab_settings.settings["MAX_RELATION_DEPTH"] = 1
    try:
        response = run(client, model="Member", select_fields=["team__device__name"])
    finally:
        qlab_settings.settings["MAX_RELATION_DEPTH"] = 2
    assert response.status_code == 400


def test_bootstrap_hides_restricted_models(client, devices):
    ModelRegistry.objects.filter(model_name="Team").update(is_restricted=True)
    models = client.get("/qlab/api/bootstrap/").json()["models"]
    assert "Team" not in {m["model_name"] for m in models}
    assert "Device" in {m["model_name"] for m in models}


def test_share_of_restricted_model_is_not_revealed(client, devices):
    token = client.post(
        "/qlab/api/shares/",
        {
            "app_label": "testapp",
            "model_name": "Team",
            "query_payload": {"model": "Team", "select_fields": ["name"]},
        },
        format="json",
    ).json()["token"]
    ModelRegistry.objects.filter(model_name="Team").update(is_restricted=True)
    assert client.get(f"/qlab/api/shares/{token}/").status_code == 403


# --- History retention & CSV injection ----------------------------------------


def test_history_is_pruned(client, devices):
    from qlab.settings import qlab_settings

    qlab_settings.settings["HISTORY_RETENTION"] = 3
    try:
        for _ in range(6):
            run(client, model="Device", select_fields=["name"])
    finally:
        qlab_settings.settings["HISTORY_RETENTION"] = 1000
    assert QueryRunHistory.objects.count() == 3


def test_export_neutralises_formulas(client, devices):
    from tests.testapp.models import Device

    Device.objects.filter(name="alpha").update(name="=HYPERLINK(1)")
    response = client.post(
        "/qlab/api/query/export/",
        {"model": "Device", "select_fields": ["name", "size"], "order_by": ["size"]},
        format="json",
    )
    text = b"".join(response.streaming_content).decode().lstrip("﻿")
    rows = list(csv.DictReader(io.StringIO(text)))
    assert rows[0]["name"] == "'=HYPERLINK(1)"
    assert rows[0]["size"] == "10"


@pytest.mark.parametrize("value", ["-5", "+1"])
def test_formula_guard_only_touches_text(value):
    from qlab.mixins import _csv_cell

    assert _csv_cell(-5) == -5
    assert _csv_cell(value) == "'" + value


# --- Model registry sync -------------------------------------------------------


def test_registry_is_filled_after_migrate_and_keeps_settings(db):
    from django.apps import apps

    from qlab.signals import sync_qlab_registry

    # pytest-django ran migrations, so the signal must have registered models.
    entry = ModelRegistry.objects.get(model_label="testapp_Device")
    assert entry.status == "submitted"

    entry.status = "enabled"
    entry.is_restricted = True
    entry.save()
    ModelRegistry.objects.create(
        model_label="testapp_Gone", app_label="testapp", model_name="Gone"
    )

    sync_qlab_registry(sender=apps.get_app_config("qlab"))

    entry.refresh_from_db()
    assert (entry.status, entry.is_restricted) == ("enabled", True)
    assert not ModelRegistry.objects.filter(model_label="testapp_Gone").exists()


def test_registry_sync_with_all_apps_does_not_wipe_entries(db):
    from django.apps import apps

    from qlab.settings import qlab_settings
    from qlab.signals import sync_qlab_registry

    ModelRegistry.objects.filter(model_label="testapp_Device").update(status="enabled")
    previous = qlab_settings.settings["ALLOWED_APPS"]
    qlab_settings.settings["ALLOWED_APPS"] = []
    try:
        sync_qlab_registry(sender=apps.get_app_config("qlab"))
    finally:
        qlab_settings.settings["ALLOWED_APPS"] = previous
    assert ModelRegistry.objects.get(model_label="testapp_Device").status == "enabled"
    assert ModelRegistry.objects.filter(app_label="auth", model_name="User").exists()


# --- Typed filter values --------------------------------------------------------


@pytest.mark.parametrize(
    "field, op, value",
    [
        ("id", "is", "h"),
        ("size", "gt", "abc"),
        ("size", "between", ["1", "z"]),
        ("id", "in", ["1", "x"]),
        ("created", "gte", "gestern"),
        ("team", "is", "x"),
        ("active", "is", "vielleicht"),
    ],
)
def test_wrongly_typed_filter_values_are_400_with_message(
    client, devices, field, op, value
):
    response = run(
        client,
        model="Device",
        select_fields=["name"],
        filter_fields=cond(field, op, value),
    )
    assert response.status_code == 400, response.content
    msg = response.json()["errors"][0]["msg"]
    assert field in msg and "expects" in msg


@pytest.mark.parametrize(
    "field, op, value, expected",
    [
        ("id", "in", ["1", "2"], 2),
        ("size", "between", ["10", "20"], 2),
        ("created", "gte", "2025-03-15", 2),
        ("created", "lt", "2025-02-01T00:00:00Z", 1),
        ("active", "is", "true", 5),
        ("name", "icontains", "123", 0),  # text search never type-checked
    ],
)
def test_correctly_typed_filter_values_pass(
    client, devices, field, op, value, expected
):
    response = run(
        client,
        model="Device",
        select_fields=["name"],
        filter_fields=cond(field, op, value),
    )
    assert response.status_code == 200, response.content
    assert response.json()["count"] == expected


def test_having_value_must_be_numeric_for_count(client, devices):
    response = run(
        client,
        model="Device",
        select_fields=["status"],
        aggregations=[{"function": "count", "alias": "n"}],
        having=cond("n", "gt", "viele"),
    )
    assert response.status_code == 400


def test_saved_query_with_wrongly_typed_value_is_rejected_on_save(client, devices):
    created = client.post(
        "/qlab/api/saved-queries/",
        {
            "name": "by id",
            "app_label": "testapp",
            "model_name": "Device",
            "query_payload": {
                "model": "Device",
                "select_fields": ["name"],
                "filter_fields": cond("id", "is", "1"),
            },
        },
        format="json",
    )
    assert created.status_code == 201, created.json()
    response = client.patch(
        f"/qlab/api/saved-queries/{created.json()['id']}/",
        {
            "query_payload": {
                "model": "Device",
                "select_fields": ["name"],
                "filter_fields": cond("id", "is", "h"),
            }
        },
        format="json",
    )
    assert response.status_code == 400
    assert "expects a whole number" in str(response.json())


def test_ui_assets_are_cache_busted(client):
    html = client.get("/qlab/").content.decode()
    assert "qlab.css?v=" in html and "qlab.js?v=" in html


def test_registry_sync_keeps_hand_made_and_other_app_entries(db):
    from django.apps import apps

    from qlab.signals import sync_qlab_registry

    ModelRegistry.objects.filter(app_label="testapp", model_name="Team").delete()
    hand_made = ModelRegistry.objects.create(
        model_label="testapp.Team",
        app_label="testapp",
        model_name="Team",
        status="enabled",
    )
    other_app = ModelRegistry.objects.create(
        model_label="auth_Group",
        app_label="auth",
        model_name="Group",
        status="enabled",
    )
    sync_qlab_registry(sender=apps.get_app_config("qlab"))
    assert (
        ModelRegistry.objects.filter(pk__in=[hand_made.pk, other_app.pk]).count() == 2
    )
    assert (
        ModelRegistry.objects.filter(app_label="testapp", model_name="Team").count()
        == 1
    )


@pytest.mark.parametrize("value, expected", [("1", 5), ("t", 5), ("0", 0)])
def test_boolean_filter_accepts_django_spellings(client, devices, value, expected):
    response = run(
        client,
        model="Device",
        select_fields=["name"],
        filter_fields=cond("active", "is", value),
    )
    assert response.status_code == 200
    assert response.json()["count"] == expected
