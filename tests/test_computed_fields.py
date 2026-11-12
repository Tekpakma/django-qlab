import csv
import io

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import connection
from django.test.utils import CaptureQueriesContext

from qlab.computed import get_computed_fields
from qlab.settings import qlab_settings
from tests.testapp.computed import DeviceLabel, MetaKeys, TeamName, TooShort
from tests.testapp.models import Device, Team

QUERY = "/qlab/api/query/"
EXPORT = "/qlab/api/query/export/"
METADATA = "/qlab/api/metadata/"
SUGGEST = "/qlab/api/suggest/"


@pytest.fixture
def computed(monkeypatch):
    """Registers computed fields on Device: as dotted path, as class and as instance."""
    DeviceLabel.calls.clear()
    monkeypatch.setitem(
        qlab_settings.settings,
        "COMPUTED_FIELDS",
        {
            "testapp.Device.device_label": "tests.testapp.computed.DeviceLabel",
            "TestApp.device.team_name": TeamName,
            "testapp.Device.meta_keys": MetaKeys(),
        },
    )


def run(client, url=QUERY, **payload):
    payload.setdefault("model", "Device")
    return client.post(url, payload, format="json")


def rows_of(response):
    assert response.status_code == 200, response.content
    return response.json()["results"]


# --- Selecting --------------------------------------------------------------------


def test_computed_field_is_selected_like_a_model_field(client, devices, computed):
    rows = rows_of(
        run(client, select_fields=["id", "name", "device_label"], order_by=["name"])
    )
    teams = {d.name: d.team_id for d in devices}
    assert [(r["name"], r["device_label"]) for r in rows] == [
        (name, f"{name} ({teams[name]})") for name in sorted(teams)
    ]


def test_columns_only_the_resolver_needs_are_not_returned(client, devices, computed):
    rows = rows_of(run(client, select_fields=["device_label"]))
    assert all(set(row) == {"id", "device_label"} for row in rows)
    # ...but the resolver did get them, together with the primary key
    assert DeviceLabel.calls[0]["keys"] == ["id", "name", "team"]


def test_required_columns_are_returned_when_selected(client, devices, computed):
    rows = rows_of(run(client, select_fields=["name", "device_label"]))
    assert all(set(row) == {"id", "name", "device_label"} for row in rows)


def test_resolver_is_called_once_per_page_with_all_rows(client, devices, computed):
    rows_of(run(client, select_fields=["device_label"], page_size=3, order_by=["id"]))
    rows_of(
        run(
            client, select_fields=["device_label"], page_size=3, page=2, order_by=["id"]
        )
    )
    assert [call["rows"] for call in DeviceLabel.calls] == [3, 2]
    assert {call["user"] for call in DeviceLabel.calls} == {"tester"}


def test_a_batched_resolver_needs_one_query_for_the_page(client, devices, computed):
    with CaptureQueriesContext(connection) as queries:
        rows = rows_of(run(client, select_fields=["name", "team_name"]))
    team_lookups = [q for q in queries if 'FROM "testapp_team"' in q["sql"]]
    assert len(team_lookups) == 1
    assert {r["name"]: r["team_name"] for r in rows} == {
        "alpha": "Ops",
        "alpine": "Ops",
        "beta": "Dev",
        "gamma": "Dev",
        "delta": None,
    }


def test_values_stay_with_their_rows_when_sorting_and_paging(client, devices, computed):
    response = run(
        client,
        select_fields=["name", "team_name"],
        order_by=["-name"],
        page_size=2,
        page=2,
        count="none",
    )
    assert [(r["name"], r["team_name"]) for r in rows_of(response)] == [
        ("beta", "Dev"),
        ("alpine", "Ops"),
    ]


def test_several_computed_fields_do_not_see_each_others_values(
    client, devices, computed
):
    rows = rows_of(
        run(client, select_fields=["device_label", "team_name", "meta_keys"])
    )
    assert all(
        set(row) == {"id", "device_label", "team_name", "meta_keys"} for row in rows
    )
    assert all(row["meta_keys"] == ["k"] for row in rows)


def test_computed_fields_work_together_with_filters(client, devices, computed):
    rows = rows_of(
        run(
            client,
            select_fields=["name", "team_name"],
            filter_fields={
                "and_operation": [{"field": "status", "op": "is", "value": "running"}]
            },
        )
    )
    assert sorted(r["name"] for r in rows) == ["alpha", "beta", "delta"]


def test_computed_fields_of_other_models_are_ignored(client, devices, computed):
    rows = rows_of(run(client, model="Team", select_fields=["name"], order_by=["name"]))
    assert [r["name"] for r in rows] == ["Dev", "Ops"]
    response = run(client, model="Team", select_fields=["device_label"])
    assert response.status_code == 400


# --- Not usable everywhere ----------------------------------------------------------


@pytest.mark.parametrize(
    "extra",
    [
        {
            "filter_fields": {
                "and_operation": [
                    {"field": "device_label", "op": "is", "value": "alpha (1)"}
                ]
            }
        },
        {"order_by": ["device_label"]},
        {"order_by": ["-device_label"]},
        {"aggregations": [{"field": "device_label", "function": "count"}]},
    ],
)
def test_computed_fields_cannot_be_filtered_sorted_or_aggregated(
    client, devices, computed, extra
):
    response = run(client, select_fields=["name"], **extra)
    assert response.status_code == 400
    assert response.json()["errors"][0]["code"] == "VALUE_ERROR_COMPUTED_FIELD"


def test_computed_fields_cannot_be_selected_in_an_aggregation(
    client, devices, computed
):
    response = run(
        client,
        select_fields=["status", "device_label"],
        aggregations=[{"function": "count"}],
    )
    assert response.status_code == 400
    assert response.json()["errors"][0]["code"] == "VALUE_ERROR_COMPUTED_FIELD"


def test_suggest_does_not_offer_values_of_computed_fields(client, devices, computed):
    response = client.post(
        SUGGEST, {"model": "Device", "field": "device_label"}, format="json"
    )
    assert response.status_code == 400


# --- Metadata -----------------------------------------------------------------------


def test_metadata_lists_computed_fields(client, devices, computed):
    response = client.post(METADATA, {"model": "Device"}, format="json")
    assert response.status_code == 200
    fields = {f["name"]: f for f in response.json()["fields"]}

    label = fields["device_label"]
    assert label["computed"] is True
    assert label["type"] == "string"
    assert label["label"] == "Device label"
    assert label["help_text"] == "Name and team id"
    assert label["allowed_operations"] == []
    assert fields["team_name"]["label"] == "Team name"
    assert fields["meta_keys"]["type"] == "json"
    # real fields are not marked
    assert fields["name"]["computed"] is False


def test_metadata_without_computed_fields_is_unchanged(client, devices):
    response = client.post(METADATA, {"model": "Device"}, format="json")
    assert not any(f["computed"] for f in response.json()["fields"])


# --- Hidden fields ---------------------------------------------------------------------


def test_a_computed_field_may_read_a_field_that_is_excluded(
    client, devices, computed, monkeypatch
):
    monkeypatch.setitem(
        qlab_settings.settings, "EXCLUDED_FIELDS", ["testapp.Device.meta"]
    )

    rows = rows_of(run(client, select_fields=["meta_keys"]))
    assert all(row["meta_keys"] == ["k"] and "meta" not in row for row in rows)
    # the field itself stays hidden
    assert run(client, select_fields=["meta"]).status_code == 400


# --- Export --------------------------------------------------------------------------


def test_export_csv_contains_computed_columns(client, devices, computed):
    response = run(
        client, EXPORT, select_fields=["name", "team_name"], order_by=["name"]
    )
    text = b"".join(response.streaming_content).decode("utf-8").lstrip("\ufeff")
    rows = list(csv.DictReader(io.StringIO(text)))
    assert [(r["name"], r["team_name"]) for r in rows] == [
        ("alpha", "Ops"),
        ("alpine", "Ops"),
        ("beta", "Dev"),
        ("delta", ""),
        ("gamma", "Dev"),
    ]
    assert "meta" not in rows[0]


def test_export_resolves_computed_fields_in_chunks(client, devices, computed):
    response = run(
        client, EXPORT, select_fields=["device_label"], format="json", order_by=["id"]
    )
    body = b"".join(response.streaming_content).decode("utf-8")
    assert body.count("device_label") == 5
    assert [call["rows"] for call in DeviceLabel.calls] == [5]


# --- Registration errors ---------------------------------------------------------------


def test_a_resolver_must_return_one_value_per_row(client, devices, monkeypatch):
    monkeypatch.setitem(
        qlab_settings.settings,
        "COMPUTED_FIELDS",
        {"testapp.Device.short": TooShort},
    )
    with pytest.raises(ImproperlyConfigured, match="one value per row"):
        run(client, select_fields=["short"])


@pytest.mark.parametrize(
    "entry, message",
    [
        ({"Device.label": TeamName}, "must look like"),
        ({"testapp.Device.name": TeamName}, "already has a field"),
        ({"testapp.Device.1bad": TeamName}, "not a valid field name"),
        ({"testapp.Device.label": object}, "must be a ComputedField"),
        ({"testapp.Device.label": "tests.testapp.nope.Missing"}, "cannot import"),
        (
            {"testapp.Team.label": "tests.testapp.computed.MetaKeys"},
            "not a field of Team",
        ),
    ],
)
def test_a_wrong_registration_is_reported(monkeypatch, entry, message):
    monkeypatch.setitem(qlab_settings.settings, "COMPUTED_FIELDS", entry)
    model = Team if ".Team." in next(iter(entry)) else Device
    with pytest.raises(ImproperlyConfigured, match=message):
        get_computed_fields(model)


def test_a_resolver_may_not_require_a_to_many_relation(monkeypatch):
    class Bad(TeamName):
        requires = ("device__name",)

    monkeypatch.setitem(
        qlab_settings.settings, "COMPUTED_FIELDS", {"testapp.Team.names": Bad}
    )
    with pytest.raises(ImproperlyConfigured, match="to-many"):
        get_computed_fields(Team)
