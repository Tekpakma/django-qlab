from django.urls import path

from .api_views import (
    QLabBootstrapViewSet,
    QLabSettingsViewSet,
    QueryRunHistoryViewSet,
    SavedQueryViewSet,
    SharedQueryViewSet,
    get_query_viewset_class,
)
from .views import QLabView

app_name = "qlab"

# Configurable via QLAB_SETTINGS["QUERY_VIEWSET"] so a scoped subclass covers
# every endpoint that reads model data.
QueryViewSet = get_query_viewset_class()

urlpatterns = [
    path("api/query/", QueryViewSet.as_view({"post": "post"}), name="query"),
    path(
        "api/query/export/",
        QueryViewSet.as_view({"post": "export"}),
        name="query-export",
    ),
    path(
        "api/metadata/",
        QueryViewSet.as_view({"post": "metadata"}),
        name="metadata",
    ),
    path(
        "api/neighborhood/",
        QueryViewSet.as_view({"post": "neighborhood"}),
        name="neighborhood",
    ),
    path(
        "api/suggest/",
        QueryViewSet.as_view({"post": "suggest"}),
        name="suggest",
    ),
    path(
        "api/shares/",
        SharedQueryViewSet.as_view({"post": "create"}),
        name="share-create",
    ),
    path(
        "api/shares/<str:token>/",
        SharedQueryViewSet.as_view({"get": "retrieve"}),
        name="share-detail",
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
