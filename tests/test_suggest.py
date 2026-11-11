import pytest

SUGGEST = "/qlab/api/suggest/"


def suggest(client, **payload):
    payload.setdefault("model", "Device")
    return client.post(SUGGEST, payload, format="json")


def test_suggest_prefix_distinct_sorted(client, devices):
    body = suggest(client, field="status").json()
    assert body["values"] == ["failed", "running", "stopped"]
    assert body["next_cursor"] is None


def test_suggest_prefix_is_case_insensitive(client, devices):
    body = suggest(client, field="name", q="AL").json()
    assert body["values"] == ["alpha", "alpine"]


def test_suggest_contains(client, devices):
    body = suggest(client, field="name", q="lt", match="contains").json()
    assert body["values"] == ["delta"]


def test_suggest_cursor_paging(client, devices):
    seen, cursor = [], None
    for _ in range(10):
        body = suggest(client, field="name", limit=2, cursor=cursor).json()
        assert len(body["values"]) <= 2
        seen += body["values"]
        cursor = body["next_cursor"]
        if cursor is None:
            break
    assert seen == ["alpha", "alpine", "beta", "delta", "gamma"]


def test_suggest_limit_is_capped(client, devices, settings):
    from qlab.settings import qlab_settings

    qlab_settings.settings["MAX_SUGGEST_LIMIT"] = 2
    try:
        body = suggest(client, field="name", limit=100).json()
    finally:
        qlab_settings.settings["MAX_SUGGEST_LIMIT"] = 50
    assert len(body["values"]) == 2 and body["next_cursor"] == "alpine"


def test_suggest_skips_null_and_empty(client, devices):
    from tests.testapp.models import Device

    Device.objects.create(name="", status="x")
    values = suggest(client, field="name").json()["values"]
    assert "" not in values
    assert suggest(client, field="team__name").json()["values"] == ["Dev", "Ops"]


def test_suggest_integer_field(client, devices):
    assert suggest(client, field="size", q="3").json()["values"] == [30]


@pytest.mark.parametrize("field", ["meta", "active", "nope", "bad field", "devices"])
def test_suggest_rejects_unsupported_fields(client, devices, field):
    model = "Team" if field == "devices" else "Device"
    assert suggest(client, model=model, field=field).status_code == 400


def test_suggest_rejects_unknown_model_and_missing_params(client, devices):
    # same behaviour as /api/query/: unknown models are reported as restricted
    assert suggest(client, model="Nope", field="name").status_code == 403
    assert client.post(SUGGEST, {"model": "Device"}, format="json").status_code == 400


def test_suggest_uses_scoped_queryset(client, devices, monkeypatch):
    from qlab.api_views import QLabFrontendApiViewSet

    monkeypatch.setattr(
        QLabFrontendApiViewSet,
        "get_queryset",
        lambda self, model: model.objects.filter(status="running"),
    )
    assert suggest(client, field="status").json()["values"] == ["running"]
    assert suggest(client, field="name").json()["values"] == ["alpha", "beta", "delta"]
