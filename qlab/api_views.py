import hashlib
import json
import secrets

from django.apps import apps
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.module_loading import import_string
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from qlab.mixins import (
    NeighborhoodMixin,
    QLabMetadataMixin,
    QLabMixin,
    QLabSuggestMixin,
    _check_allowed_app,
    _check_restricted,
)
from qlab.models import (
    ModelRegistry,
    QLabUserSettings,
    QueryRunHistory,
    SavedQuery,
    SharedQuery,
)
from qlab.serializers import (
    QLabUserSettingsSerializer,
    QueryRunHistorySerializer,
    SavedQuerySerializer,
    SharedQuerySerializer,
)
from qlab.settings import qlab_settings


def _get_models_index(
    allowed_apps: tuple[str, ...],
    include_model_counts: bool,
    user=None,
):
    enabled = ModelRegistry.objects.filter(status="enabled")
    if allowed_apps:
        enabled = enabled.filter(app_label__in=allowed_apps)
    # Don't list restricted models the user has no group for — they would
    # only fail with 403 when picked.
    user_group_ids = (
        list(user.groups.values_list("pk", flat=True)) if user is not None else []
    )
    enabled = enabled.filter(
        Q(is_restricted=False) | Q(allowed_groups__in=user_group_ids)
    ).distinct()

    rows = []
    for entry in enabled:
        try:
            model = apps.get_model(entry.app_label, entry.model_name)
        except LookupError:
            continue

        row = {
            "app_label": entry.app_label,
            "model_name": entry.model_name,
            "verbose_name": str(model._meta.verbose_name),
            "verbose_name_plural": str(model._meta.verbose_name_plural),
            "count": None,
        }
        if include_model_counts:
            try:
                row["count"] = model.objects.count()
            except Exception:
                row["count"] = None
        rows.append(row)

    return sorted(rows, key=lambda item: (item["app_label"], item["model_name"]))


class QLabFrontendApiViewSet(
    QLabMixin,
    NeighborhoodMixin,
    QLabMetadataMixin,
    QLabSuggestMixin,
    viewsets.ViewSet,
):
    """
    Packaged API surface for the bundled frontend.

    Host projects can still provide their own ViewSet for custom permissions
    or queryset scoping, but these routes make the packaged UI usable out of
    the box.
    """

    permission_classes = [permissions.IsAuthenticated]


def get_query_viewset_class():
    """
    Return the ViewSet class configured via ``QLAB_SETTINGS["QUERY_VIEWSET"]``.

    Used for every route that executes a query, so a scoped subclass with a
    custom ``get_queryset()`` applies to queries, exports and saved-query runs
    alike.
    """
    configured = qlab_settings.QUERY_VIEWSET
    if not configured:
        return QLabFrontendApiViewSet
    if isinstance(configured, str):
        return import_string(configured)
    return configured


class QLabSettingsViewSet(viewsets.ViewSet):
    permission_classes = [permissions.IsAuthenticated]

    def _get_object(self):
        settings_obj, _ = QLabUserSettings.objects.get_or_create(user=self.request.user)
        return settings_obj

    @extend_schema(
        summary="Get QLab User Settings",
        responses=QLabUserSettingsSerializer,
    )
    def retrieve(self, request):
        return Response(QLabUserSettingsSerializer(self._get_object()).data)

    @extend_schema(
        summary="Update QLab User Settings",
        request=QLabUserSettingsSerializer,
        responses=QLabUserSettingsSerializer,
    )
    def partial_update(self, request):
        serializer = QLabUserSettingsSerializer(
            self._get_object(),
            data=request.data,
            partial=True,
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class SavedQueryViewSet(viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = SavedQuerySerializer
    pagination_class = None

    def get_queryset(self):
        return SavedQuery.objects.filter(user=self.request.user).order_by("name")

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    @extend_schema(
        summary="Run Saved Query",
        request=None,
        responses={200: "application/json"},
    )
    @action(detail=True, methods=["post"])
    def run(self, request, pk=None):
        saved_query = self.get_object()
        payload = {
            **saved_query.query_payload,
            "app_label": saved_query.app_label,
            "model": saved_query.model_name,
            "saved_query_id": saved_query.id,
            "title": saved_query.name,
        }
        # Run through the configured query ViewSet so host-project scoping
        # (get_queryset overrides) applies to saved queries as well.
        viewset = get_query_viewset_class()()
        viewset.request = request
        viewset.format_kwarg = None
        response = viewset.execute_query(request, payload)
        if response.status_code == status.HTTP_200_OK:
            saved_query.last_run_at = timezone.now()
            saved_query.save(update_fields=["last_run_at"])
        return response


class SharedQueryViewSet(viewsets.GenericViewSet):
    """
    Short share links for queries.

    ``POST /api/shares/`` stores a payload and returns a token; the UI puts
    only ``?share=<token>`` into the link. ``GET /api/shares/<token>/``
    resolves it again. Identical payloads shared by the same user reuse the
    existing token.
    """

    permission_classes = [permissions.IsAuthenticated]
    serializer_class = SharedQuerySerializer
    queryset = SharedQuery.objects.all()
    lookup_field = "token"
    pagination_class = None

    @extend_schema(summary="Create Share Link", responses=SharedQuerySerializer)
    def create(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        app_label = data.get("app_label") or qlab_settings.DEFAULT_APP_LABEL
        canonical = json.dumps(
            {
                "app_label": app_label,
                "model_name": data["model_name"],
                "query_payload": data["query_payload"],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        payload_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

        existing = SharedQuery.objects.filter(
            created_by=request.user, payload_hash=payload_hash
        ).first()
        if existing:
            return Response(self.get_serializer(existing).data, status=200)

        for _ in range(5):
            try:
                with transaction.atomic():
                    shared = SharedQuery.objects.create(
                        token=secrets.token_urlsafe(9),
                        created_by=request.user,
                        app_label=app_label,
                        model_name=data["model_name"],
                        query_payload=data["query_payload"],
                        payload_hash=payload_hash,
                    )
                return Response(self.get_serializer(shared).data, status=201)
            except IntegrityError:
                continue  # token collision — astronomically rare, retry
        return Response(
            {"detail": "Could not create a share link, please retry."}, status=503
        )

    @extend_schema(summary="Resolve Share Link", responses=SharedQuerySerializer)
    def retrieve(self, request, token=None):
        shared = self.get_object()
        # The payload contains filter values; only reveal it to users who may
        # query the model at all.
        denied = _check_allowed_app(shared.app_label) or _check_restricted(
            shared.model_name, shared.app_label, request.user
        )
        if denied:
            return denied
        return Response(self.get_serializer(shared).data)


class QueryRunHistoryViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = QueryRunHistorySerializer
    pagination_class = None

    def get_queryset(self):
        queryset = QueryRunHistory.objects.filter(user=self.request.user)
        model_name = self.request.query_params.get("model")
        if model_name:
            queryset = queryset.filter(model_name=model_name)
        try:
            requested_limit = int(
                self.request.query_params.get("limit", qlab_settings.MAX_HISTORY_ROWS)
            )
        except (TypeError, ValueError):
            requested_limit = qlab_settings.MAX_HISTORY_ROWS
        limit = min(max(requested_limit, 1), qlab_settings.MAX_HISTORY_ROWS)
        return queryset[:limit]


class QLabBootstrapViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = SavedQuerySerializer

    @extend_schema(summary="Get QLab Bootstrap Data")
    def list(self, request, *args, **kwargs):
        settings_obj, _ = QLabUserSettings.objects.get_or_create(user=request.user)
        models_index = _get_models_index(
            tuple(qlab_settings.ALLOWED_APPS or ()),
            bool(qlab_settings.INCLUDE_MODEL_COUNTS),
            request.user,
        )

        return Response(
            {
                "user": {
                    "id": request.user.pk,
                    "username": getattr(request.user, request.user.USERNAME_FIELD, ""),
                    "is_authenticated": True,
                },
                "settings": QLabUserSettingsSerializer(settings_obj).data,
                "config": {
                    "environment": qlab_settings.ENVIRONMENT_LABEL,
                    "metadata": {
                        "relation_depth": qlab_settings.METADATA_MAX_RELATION_DEPTH,
                        "include_reverse_relations": qlab_settings.METADATA_INCLUDE_REVERSE_RELATIONS,
                    },
                },
                "models": models_index,
                "saved_queries": SavedQuerySerializer(
                    SavedQuery.objects.filter(user=request.user).order_by("name")[:20],
                    many=True,
                ).data,
                "history": QueryRunHistorySerializer(
                    QueryRunHistory.objects.filter(user=request.user)[:10],
                    many=True,
                ).data,
            }
        )
