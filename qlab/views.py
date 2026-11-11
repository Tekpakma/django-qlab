import hashlib
from functools import lru_cache

from django.contrib.staticfiles import finders
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import ensure_csrf_cookie


@lru_cache(maxsize=1)
def _asset_version() -> str:
    """
    Short content hash of the bundled UI assets.

    The bundle has fixed file names (qlab.js / qlab.css), so without a
    version in the URL browsers keep serving the old UI after an upgrade.
    Computed once per process.
    """
    digest = hashlib.sha256()
    for name in ("qlab/qlab.js", "qlab/qlab.css"):
        path = finders.find(name)
        if path:
            with open(path, "rb") as handle:
                digest.update(handle.read())
    return digest.hexdigest()[:12]


@method_decorator(ensure_csrf_cookie, name="dispatch")
class QLabView(View):
    """
    Serves the QLab frontend UI.

    Add to your Django project's urls.py::

        from qlab.views import QLabView

        urlpatterns = [
            path("qlab/", QLabView.as_view(), name="qlab"),
        ]

    Then run ``python manage.py collectstatic`` and make sure
    ``django.contrib.staticfiles`` is in INSTALLED_APPS.
    """

    graphiql_version: str = "0.1.0"

    def get(self, request: HttpRequest, **kwargs: object) -> HttpResponse:
        ui_path = request.path.rstrip("/") or "/"
        api_base = f"{ui_path}/api" if ui_path != "/" else "/api"
        return render(
            request,
            "qlab/index.html",
            context={
                "QLAB_VERSION": self.graphiql_version,
                "QLAB_API_BASE": api_base,
                "QLAB_ASSET_VERSION": _asset_version(),
            },
        )
