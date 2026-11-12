from qlab.computed import ComputedField
from tests.testapp.models import Team


class DeviceLabel(ComputedField):
    """Reads regular fields; remembers how it was called."""

    requires = ("name", "team")
    label = "Device label"
    help_text = "Name and team id"
    calls = []

    def resolve(self, rows, request):
        DeviceLabel.calls.append(
            {"rows": len(rows), "keys": sorted(rows[0]), "user": request.user.username}
        )
        return [f"{row['name']} ({row['team']})" for row in rows]


class TeamName(ComputedField):
    """Looks the names up for the whole page with one query."""

    requires = ("team",)

    def resolve(self, rows, request):
        ids = {row["team"] for row in rows if row["team"] is not None}
        names = dict(Team.objects.filter(pk__in=ids).values_list("pk", "name"))
        return [names.get(row["team"]) for row in rows]


class MetaKeys(ComputedField):
    """Derives a value from a field that is hidden through EXCLUDED_FIELDS."""

    requires = ("meta",)
    type = "json"

    def resolve(self, rows, request):
        return [sorted(row["meta"]) for row in rows]


class TooShort(ComputedField):
    requires = ("name",)

    def resolve(self, rows, request):
        return [row["name"] for row in rows[:-1]]
