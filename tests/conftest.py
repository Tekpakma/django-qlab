from datetime import datetime, timezone

import pytest
from rest_framework.test import APIClient

from qlab.models import ModelRegistry
from tests.testapp.models import Device, Team


@pytest.fixture
def client(db):
    from django.contrib.auth import get_user_model

    ModelRegistry.objects.bulk_create(
        [
            ModelRegistry(
                model_label=f"testapp_{name}",
                app_label="testapp",
                model_name=name,
                status="enabled",
            )
            for name in ("Device", "Team")
        ]
    )
    api = APIClient()
    api.force_authenticate(get_user_model().objects.create(username="tester"))
    return api


@pytest.fixture
def devices(db):
    ops, dev = Team.objects.create(name="Ops"), Team.objects.create(name="Dev")
    rows = [
        ("alpha", "running", 10, ops, datetime(2025, 1, 1, tzinfo=timezone.utc)),
        ("alpine", "stopped", 30, ops, datetime(2025, 2, 1, tzinfo=timezone.utc)),
        ("beta", "running", 20, dev, datetime(2025, 3, 1, tzinfo=timezone.utc)),
        ("gamma", "failed", 40, dev, datetime(2025, 4, 1, tzinfo=timezone.utc)),
        ("delta", "running", 50, None, datetime(2025, 5, 1, tzinfo=timezone.utc)),
    ]
    return [
        Device.objects.create(
            name=n, status=s, size=sz, team=t, created=c, meta={"k": n}
        )
        for n, s, sz, t, c in rows
    ]
