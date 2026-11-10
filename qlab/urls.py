from django.urls import path

from .api_views import (
    QLabBootstrapViewSet,
    QLabFrontendApiViewSet,
    QLabSettingsViewSet,
    QueryRunHistoryViewSet,
    SavedQueryViewSet,
)
from .views import QLabView

app_name = "qlab"

urlpatterns = [
    path("api/query/", QLabFrontendApiViewSet.as_view({"post": "post"}), name="query"),
    path(
        "api/metadata/",
        QLabFrontendApiViewSet.as_view({"post": "metadata"}),
        name="metadata",
    ),
    path(
        "api/neighborhood/",
        QLabFrontendApiViewSet.as_view({"post": "neighborhood"}),
        name="neighborhood",
    ),
    path(
        "api/suggest/",
        QLabFrontendApiViewSet.as_view({"post": "suggest"}),
        name="suggest",
    ),
    path(
        "api/bootstrap/",
        QLabBootstrapViewSet.as_view({"get": "list"}),
        name="bootstrap",
    ),
    path(
        "api/settings/",
        QLabSettingsViewSet.as_view({"get": "retrieve", "patch": "partial_update"}),
        name="settings",
    ),
    path(
        "api/saved-queries/",
        SavedQueryViewSet.as_view({"get": "list", "post": "create"}),
        name="saved-query-list",
    ),
    path(
        "api/saved-queries/<int:pk>/",
        SavedQueryViewSet.as_view(
            {"get": "retrieve", "patch": "partial_update", "delete": "destroy"}
        ),
        name="saved-query-detail",
    ),
    path(
        "api/saved-queries/<int:pk>/run/",
        SavedQueryViewSet.as_view({"post": "run"}),
        name="saved-query-run",
    ),
    path(
        "api/history/", QueryRunHistoryViewSet.as_view({"get": "list"}), name="history"
    ),
    path("", QLabView.as_view(), name="index"),
]
