"""
QLabMixin and NeighborhoodMixin for Django REST Framework ViewSets.

Provides dynamic QLab query functionality as a reusable mixin.
Permissions are handled entirely via DRF permission_classes.

Usage:
    class MyQueryViewSet(QLabMixin, viewsets.ViewSet):
        permission_classes = [IsAuthenticated, MyCustomPermission]

    class MyNeighborhoodViewSet(NeighborhoodMixin, viewsets.ViewSet):
        permission_classes = [IsAuthenticated, MyCustomPermission]
"""

import csv
import json
from time import monotonic
from typing import Optional

from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from django.db.models import Avg, Count, Max, Min, Q, Sum
from django.http import StreamingHttpResponse
from django.utils import timezone
from drf_spectacular.utils import OpenApiExample, extend_schema, inline_serializer
from pydantic import ValidationError as PydanticValidationError
from rest_framework import serializers
from rest_framework.response import Response

from qlab.computed import get_computed_fields
from qlab.helpers import (
    build_q,
    estimate_count,
    find_to_many_relation_paths,
    flatten_filter_conditions,
    get_model_metadata,
    is_valid_lookup_syntax,
    related_models_on_path,
    resolve_field,
    validate_field_path,
)
from qlab.model_validation import ValidationError
from qlab.pydantic_filters import QueryFilter
from qlab.serializers import (
    MetaDataRequestSerializer,
    ModelMetadataSerializer,
    ResponseSerializer,
    SuggestRequestSerializer,
)
from qlab.settings import qlab_settings

# Reusable error response schema for Swagger
_ERROR_SCHEMA = inline_serializer(
    name="QLabErrorResponse",
    fields={
        "errors": serializers.ListField(
            child=inline_serializer(
                name="QLabError",
                fields={
                    "loc": serializers.ListField(child=serializers.CharField()),
                    "msg": serializers.CharField(),
                    "type": serializers.CharField(),
                    "code": serializers.CharField(),
                },
            )
        )
    },
)


AGGREGATE_FUNCTIONS = {
    "count": Count,
    "sum": Sum,
    "avg": Avg,
    "min": Min,
    "max": Max,
}


def _parse_page_size(raw) -> tuple[int, Optional[str]]:
    """Return ``(page_size, error)``; the size is capped at MAX_PAGE_SIZE."""
    if raw is None or raw == "":
        return min(qlab_settings.PAGE_SIZE, qlab_settings.MAX_PAGE_SIZE), None
    if isinstance(raw, bool):
        return 0, "page_size must be a positive integer."
    try:
        page_size = int(raw)
    except (TypeError, ValueError):
        return 0, "page_size must be a positive integer."
    if page_size < 1:
        return 0, "page_size must be a positive integer."
    return min(page_size, qlab_settings.MAX_PAGE_SIZE), None


class _Echo:
    """File-like object whose write() just returns the value (for csv.writer)."""

    def write(self, value):
        return value


# Cells starting with these characters are interpreted as formulas by Excel /
# LibreOffice ("CSV injection"); text values get a leading apostrophe.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _csv_cell(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return "'" + value if value.startswith(_FORMULA_PREFIXES) else value
    if isinstance(value, (dict, list)):
        return json.dumps(value, cls=DjangoJSONEncoder, ensure_ascii=False)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _stream_csv(columns, rows):
    writer = csv.writer(_Echo())
    # UTF-8 BOM so spreadsheet apps detect the encoding of umlauts etc.
    yield "\ufeff" + writer.writerow(columns)
    for row in rows:
        yield writer.writerow([_csv_cell(row.get(column)) for column in columns])


def _stream_json(rows):
    yield "["
    first = True
    for row in rows:
        yield ("" if first else ",") + json.dumps(
            row, cls=DjangoJSONEncoder, ensure_ascii=False
        )
        first = False
    yield "]"


def _field_error(loc: str, msg: str) -> Response:
    return Response(
        ValidationError([{"loc": (loc,), "msg": msg, "type": "value_error"}]).errors(),
        status=400,
    )


def _syntax_error(field_path: str) -> dict:
    return {
        "loc": ("field",),
        "msg": f"Field '{field_path}' syntax is incorrect. Use __ for relations and _ for attributes.",
        "type": "syntax_error",
    }


# Fields whose values are not meaningful as typeahead suggestions.
SUGGEST_UNSUPPORTED_FIELDS = (
    models.JSONField,
    models.FileField,
    models.ManyToManyField,
    models.BooleanField,
)


def _check_restricted(
    model_name: str, app_label: str, user: object
) -> Optional[Response]:
    """Return a 403 Response if the model is restricted, else None."""
    from qlab.models import ModelRegistry

    model_obj = ModelRegistry.objects.filter(
        model_name__iexact=model_name, app_label=app_label
    ).first()

    if not model_obj or model_obj.status != "enabled":
        restricted = True
    elif model_obj.is_restricted:
        restricted = not model_obj.allowed_groups.filter(
            pk__in=user.groups.all()
        ).exists()
    else:
        restricted = False

    if restricted:
        return Response(
            {
                "errors": [
                    {
                        "loc": ["model"],
                        "msg": f"Model '{model_name}' is restricted and cannot be queried.",
                        "type": "value_error.restricted",
                        "code": "VALUE_ERROR_RESTRICTED",
                    }
                ]
            },
            status=403,
        )
    return None


def _blocked_related_model(model, field_paths, user) -> Optional[str]:
    """
    Return the name of a disabled/restricted model reached through any of the
    field paths, or None if every traversed model may be read by ``user``.

    Models that are not in the registry at all (e.g. auth.User behind an
    "owner" foreign key) stay reachable; only explicitly disabled models and
    restricted models the user has no group for are blocked.
    """
    from qlab.models import ModelRegistry

    reached = {}
    for path in field_paths:
        for related in related_models_on_path(model, path):
            reached[(related._meta.app_label, related.__name__)] = related
    if not reached:
        return None

    entries = ModelRegistry.objects.filter(
        app_label__in={app for app, _ in reached},
        model_name__in={name for _, name in reached},
    )
    user_groups = None
    for entry in entries:
        if (entry.app_label, entry.model_name) not in reached:
            continue
        if entry.status == "disabled":
            return entry.model_name
        if entry.is_restricted:
            if user_groups is None:
                user_groups = (
                    set(user.groups.values_list("pk", flat=True))
                    if getattr(user, "is_authenticated", False)
                    else set()
                )
            allowed = set(entry.allowed_groups.values_list("pk", flat=True))
            if not (allowed & user_groups):
                return entry.model_name
    return None


def _check_allowed_app(app_label: str) -> Optional[Response]:
    """Return a 403 Response if the app is not allowed, else None."""
    allowed_apps = qlab_settings.ALLOWED_APPS or []
    if allowed_apps and app_label not in allowed_apps:
        return Response(
            {
                "errors": [
                    {
                        "loc": ["app_label"],
                        "msg": f"App '{app_label}' is not enabled for QLab.",
                        "type": "value_error.app_not_allowed",
                        "code": "VALUE_ERROR_APP_NOT_ALLOWED",
                    }
                ]
            },
            status=403,
        )
    return None


def _prune_query_history(user) -> None:
    """Keep only the newest HISTORY_RETENTION runs per user."""
    from qlab.models import QueryRunHistory

    keep = int(qlab_settings.HISTORY_RETENTION or 0)
    if keep <= 0:
        return
    # Cheap check first: only delete once the limit is exceeded, in batches.
    stale_ids = list(
        QueryRunHistory.objects.filter(user=user)
        .order_by("-created_at", "-pk")
        .values_list("pk", flat=True)[keep : keep + 500]
    )
    if stale_ids:
        QueryRunHistory.objects.filter(pk__in=stale_ids).delete()


def _record_query_history(
    request,
    *,
    payload,
    app_label: str,
    model_name: str,
    status: str,
    duration_ms: Optional[int] = None,
    result_count: Optional[int] = None,
    error_message: str = "",
) -> None:
    """Persist query execution history for authenticated users only."""
    if not getattr(request.user, "is_authenticated", False):
        return

    try:
        from qlab.models import QueryRunHistory, SavedQuery

        saved_query = None
        saved_query_id = payload.get("saved_query_id")
        if saved_query_id:
            saved_query = SavedQuery.objects.filter(
                pk=saved_query_id,
                user=request.user,
            ).first()
            if saved_query and status == "success":
                saved_query.last_run_at = timezone.now()
                saved_query.save(update_fields=["last_run_at"])

        QueryRunHistory.objects.create(
            user=request.user,
            saved_query=saved_query,
            title=payload.get("title", ""),
            app_label=app_label,
            model_name=model_name,
            query_payload=payload,
            status=status,
            duration_ms=duration_ms,
            result_count=result_count,
            error_message=error_message,
        )
        _prune_query_history(request.user)
    except Exception:
        # Query execution must not fail because optional history logging failed.
        pass


class QLabMixin:
    """
    Mixin for DRF ViewSets that enables dynamic QLab queries.

    Permissions and queryset scoping are handled entirely via DRF,
    allowing standard permission_classes and custom permissions to be used.

    Override get_queryset() to apply custom filtering or scoping before
    QLab processes the request.

    Example:
        class MyQueryViewSet(QLabMixin, viewsets.ViewSet):
            permission_classes = [IsAuthenticated, MyCustomPermission]

            def get_queryset(self, model):
                # Only return records belonging to the user's tenant
                return model.objects.filter(tenant=self.request.user.tenant)
    """

    def get_queryset(self, model):
        """
        Return the base queryset for QLab to filter against.

        Override this method to apply custom scoping, e.g. filtering by
        tenant, business group, or any other access restriction.

        QLab will apply its own filter conditions on top of this queryset,
        so only return the base scope here — not the final filtered result.

        Args:
            model: The resolved Django model class from the request.

        Returns:
            A QuerySet of model instances to query against.
        """
        return model.objects.all()

    def _error_response(
        self,
        request,
        payload,
        *,
        app_label: str,
        model_name: str,
        started_at: float,
        loc: str,
        msg: str,
        error_type: str = "value_error",
        status: int = 400,
    ) -> Response:
        """Record a failed run in the history and build a QLab error response."""
        _record_query_history(
            request,
            payload=payload,
            app_label=app_label,
            model_name=model_name or "unknown",
            status="failed",
            duration_ms=int((monotonic() - started_at) * 1000),
            error_message=msg,
        )
        return Response(
            ValidationError([{"loc": (loc,), "msg": msg, "type": error_type}]).errors(),
            status=status,
        )

    def prepare_query(self, request, payload, started_at: float):
        """
        Validate a query payload and build the (unpaginated) queryset.

        Shared by the paginated query endpoint and the export endpoint so both
        apply exactly the same validation, access checks and ``get_queryset``
        scoping.

        Returns:
            ``(prepared, None)`` on success, where ``prepared`` is a dict with
            ``query``, ``model``, ``app_label``, ``queryset``, ``pk_field`` and
            ``add_pk_column``; or ``(None, Response)`` on failure.
        """
        requested_model = payload.get("model", "") if hasattr(payload, "get") else ""
        app_label = (
            payload.get("app_label") if hasattr(payload, "get") else None
        ) or qlab_settings.DEFAULT_APP_LABEL

        if not isinstance(payload, dict) and not hasattr(payload, "get"):
            return None, self._error_response(
                request,
                {},
                app_label=app_label,
                model_name="unknown",
                started_at=started_at,
                loc="body",
                msg="The request body must be a JSON object.",
            )

        try:
            # "group_by" (issue #7) is an explicit alias for the select fields
            # of an aggregation query: those are the GROUP BY columns.
            group_by = payload.get("group_by")
            if group_by is not None and not payload.get("aggregations"):
                raise ValidationError(
                    [
                        {
                            "loc": ("group_by",),
                            "msg": "group_by requires at least one aggregation.",
                            "type": "value_error.group_by",
                        }
                    ]
                )
            query = QueryFilter(
                model=payload["model"],
                select_fields=(
                    group_by if group_by is not None else payload["select_fields"]
                ),
                filter_fields=payload.get("filter_fields", None),
                aggregations=payload.get("aggregations", None) or [],
                having=payload.get("having", None),
                order_by=payload.get("order_by", None) or [],
                count=payload.get("count", None),
                page=payload.get("page", 1),
                app_label=app_label,
            )
        except ValidationError as e:
            _record_query_history(
                request,
                payload=payload,
                app_label=app_label,
                model_name=requested_model or "unknown",
                status="failed",
                duration_ms=int((monotonic() - started_at) * 1000),
                error_message=str(e.errors()),
            )
            return None, Response(e.errors(), status=400)
        except PydanticValidationError as e:
            # Structural problems (wrong types, malformed filter trees) are
            # reported by pydantic itself; turn them into the QLab error shape
            # instead of letting them bubble up as a 500.
            errors = [
                {
                    "loc": tuple(str(part) for part in err.get("loc", ())),
                    "msg": err.get("msg", "Invalid value."),
                    "type": "value_error.invalid",
                }
                for err in e.errors()
            ]
            _record_query_history(
                request,
                payload=payload,
                app_label=app_label,
                model_name=requested_model or "unknown",
                status="failed",
                duration_ms=int((monotonic() - started_at) * 1000),
                error_message="; ".join(err["msg"] for err in errors),
            )
            return None, Response(ValidationError(errors).errors(), status=400)
        except KeyError as e:
            msg = f"{e.args[0]} is required."
            _record_query_history(
                request,
                payload=payload,
                app_label=app_label,
                model_name=requested_model or "unknown",
                status="failed",
                duration_ms=int((monotonic() - started_at) * 1000),
                error_message=msg,
            )
            return None, Response(
                {
                    "errors": [
                        {
                            "loc": [e.args[0]],
                            "msg": msg,
                            "type": "value_error.missing",
                            "code": "VALUE_ERROR_MISSING",
                        }
                    ]
                },
                status=400,
            )

        app_label = query.app_label or qlab_settings.DEFAULT_APP_LABEL
        allowed_app = _check_allowed_app(app_label)
        if allowed_app:
            _record_query_history(
                request,
                payload=payload,
                app_label=app_label,
                model_name=query.model,
                status="failed",
                duration_ms=int((monotonic() - started_at) * 1000),
                error_message=f"App '{app_label}' is not enabled for QLab.",
            )
            return None, allowed_app
        restricted = _check_restricted(query.model, app_label, request.user)
        if restricted:
            _record_query_history(
                request,
                payload=payload,
                app_label=app_label,
                model_name=query.model,
                status="failed",
                duration_ms=int((monotonic() - started_at) * 1000),
                error_message=f"Model '{query.model}' is restricted and cannot be queried.",
            )
            return None, restricted
        try:
            model = apps.get_model(app_label, query.model)
        except LookupError:
            return None, self._error_response(
                request,
                payload,
                app_label=app_label,
                model_name=query.model,
                started_at=started_at,
                loc="model",
                msg=f"Model '{query.model}' does not exist in app '{app_label}'.",
            )

        # --- Access control on relation traversal ---
        # The model a query starts from is checked above; paths like
        # "owner__group__name" must not reach into disabled or restricted
        # models either.
        filter_conditions = (
            flatten_filter_conditions(query.filter_fields)
            if query.filter_fields
            else []
        )
        computed = get_computed_fields(model)
        computed_selected = {
            name: computed[name] for name in query.select_fields if name in computed
        }
        # Model paths the computed fields read; fetched with the rows, but only
        # returned if the client selected them itself.
        required = list(
            dict.fromkeys(
                path for field in computed_selected.values() for path in field.requires
            )
        )
        all_paths = (
            [name for name in query.select_fields if name not in computed_selected]
            + required
            + [condition.field for condition in filter_conditions]
            + [agg.field for agg in query.aggregations if agg.field]
            + [
                entry.lstrip("-")
                for entry in query.order_by
                if entry.lstrip("-") not in {agg.alias for agg in query.aggregations}
            ]
        )
        blocked = _blocked_related_model(model, all_paths, request.user)
        if blocked:
            return None, self._error_response(
                request,
                payload,
                app_label=app_label,
                model_name=query.model,
                started_at=started_at,
                loc="field",
                msg=f"Model '{blocked}' is restricted and cannot be queried through a relation.",
                error_type="value_error.restricted",
                status=403,
            )

        q_obj = build_q(query.filter_fields) if query.filter_fields else Q()

        pk_field = model._meta.pk.name
        select_fields = [
            name for name in query.select_fields if name not in computed_selected
        ]
        helper_columns: list[str] = []

        def to_many_paths(paths) -> list[str]:
            found: list[str] = []
            for path in paths:
                for relation_path in find_to_many_relation_paths(model, path):
                    if relation_path not in found:
                        found.append(relation_path)
            return found

        select_joins = to_many_paths(select_fields)
        filter_joins = to_many_paths(c.field for c in filter_conditions)

        if query.aggregations:
            agg_chains = {
                agg.alias: to_many_paths([agg.field]) if agg.field else []
                for agg in query.aggregations
            }
            aggregated_joins = {path for chain in agg_chains.values() for path in chain}
            # A filter on the relation that is being aggregated restricts the
            # aggregated rows ("sum of sizes of devices > 5"), like SQL WHERE.
            # A filter on any other to-many relation only selects base
            # objects; applying it as a JOIN would multiply the aggregated
            # rows, so it goes through a pk subquery instead.
            filter_as_join = set(filter_joins) <= aggregated_joins
            joins = set(select_joins) | aggregated_joins
            if filter_as_join:
                joins |= set(filter_joins)

            for agg in query.aggregations:
                inflating = agg.function in ("sum", "avg") or (
                    agg.function == "count" and agg.field and not agg.distinct
                )
                extra = joins - set(agg_chains[agg.alias])
                if inflating and extra:
                    return None, self._error_response(
                        request,
                        payload,
                        app_label=app_label,
                        model_name=query.model,
                        started_at=started_at,
                        loc="aggregations",
                        msg=(
                            f"Aggregation '{agg.alias}' would be multiplied by the "
                            f"join over {', '.join(sorted(extra))}. Use count with "
                            "distinct, min/max, or split this into separate queries."
                        ),
                        error_type="value_error.aggregation_join",
                    )

            aggregate_kwargs = {
                agg.alias: (
                    AGGREGATE_FUNCTIONS[agg.function](agg.field, distinct=agg.distinct)
                    if agg.field
                    # Field-less count counts the base objects of each group;
                    # distinct keeps joins from inflating it.
                    else Count("pk", distinct=True)
                )
                for agg in query.aggregations
            }
            # select_fields are the GROUP BY columns here. The primary key
            # must NOT be added: values(pk, ...) would put it into the
            # GROUP BY and collapse every group to a single row. Ordering by
            # the group-by columns is fully deterministic, since their
            # combination is unique per output row by construction.
            add_pk_column = False
            order_fields = list(select_fields)
        else:
            aggregate_kwargs = {}
            # A filter across a to-many relation only decides WHICH objects
            # match; it goes through a pk subquery so a Team with three
            # matching devices is still returned once.
            filter_as_join = False
            # Always include the PK in plain row queries, even when it is not
            # selected, so the UI can identify rows.
            add_pk_column = pk_field not in select_fields
            if add_pk_column:
                select_fields = [pk_field] + select_fields
            helper_columns = [path for path in required if path not in select_fields]
            select_fields = select_fields + helper_columns

            # Selecting a to-many field (e.g. "device__name") intentionally
            # yields one row per related record. Ordering by the base pk
            # alone would leave those rows in arbitrary relative order and
            # LIMIT/OFFSET pagination could split or repeat them, so each
            # relation's pk is added as a tiebreaker.
            order_fields = [pk_field] + [f"{relation}__pk" for relation in select_joins]

        # User ordering comes first; the deterministic keys above stay as
        # tiebreakers so LIMIT/OFFSET pagination remains stable.
        requested_order = list(query.order_by)
        requested_names = {entry.lstrip("-") for entry in requested_order}
        order_fields = requested_order + [
            f for f in order_fields if f not in requested_names
        ]

        # Apply custom scoping via get_queryset(), then apply QLab filters on top.
        # Value conversion happens here (Django prepares lookup values while
        # building the query), so anything validation missed becomes a 400.
        try:
            base = self.get_queryset(model)
            if filter_joins and not filter_as_join:
                base = base.filter(pk__in=base.filter(q_obj).values("pk"))
            else:
                base = base.filter(q_obj)
            queryset = base.values(*select_fields)
            if aggregate_kwargs:
                queryset = queryset.annotate(**aggregate_kwargs)
                if query.having:
                    # Filtering on an aggregate annotation becomes SQL HAVING.
                    queryset = queryset.filter(build_q(query.having))
        except (ValueError, TypeError, DjangoValidationError) as e:
            return None, self._invalid_values_response(
                request,
                payload,
                {"app_label": app_label, "query": query},
                started_at,
                e,
            )
        queryset = queryset.order_by(*order_fields)

        return (
            {
                "query": query,
                "model": model,
                "app_label": app_label,
                "queryset": queryset,
                "pk_field": pk_field,
                "add_pk_column": add_pk_column,
                "computed": computed_selected,
                "helper_columns": helper_columns,
            },
            None,
        )

    def _finalize_rows(self, rows, prepared, request):
        """
        Turn the rows of the queryset into the rows of the response.

        Computed fields are resolved here, for all given rows at once (one page,
        or one chunk of an export). The columns only they needed are dropped, and
        the pk is exposed as "id" when it was added just to identify rows.
        """
        computed = prepared["computed"]
        rows = list(rows)
        if computed and rows:
            resolved = {}
            for name, field in computed.items():
                values = list(field.resolve(rows, request))
                if len(values) != len(rows):
                    raise ImproperlyConfigured(
                        f"Computed field '{name}' returned {len(values)} values for "
                        f"{len(rows)} rows; resolve() must return one value per row."
                    )
                resolved[name] = values
            helper = set(prepared["helper_columns"])
            rows = [
                {
                    **{key: value for key, value in row.items() if key not in helper},
                    **{name: resolved[name][index] for name in computed},
                }
                for index, row in enumerate(rows)
            ]
        return self._with_id_column(rows, prepared)

    @staticmethod
    def _with_id_column(rows, prepared):
        """Expose the pk as "id" when it was added only for row identification."""
        if not prepared["add_pk_column"]:
            return rows
        pk_field = prepared["pk_field"]
        return [{"id": row[pk_field], **row} for row in rows]

    def _invalid_values_response(self, request, payload, prepared, started_at, exc):
        _record_query_history(
            request,
            payload=payload,
            app_label=prepared["app_label"],
            model_name=prepared["query"].model,
            status="failed",
            duration_ms=int((monotonic() - started_at) * 1000),
            error_message=str(exc),
        )
        return Response(
            {
                "errors": [
                    {
                        "loc": ["filter_fields"],
                        "msg": "One or more filter values are invalid for the selected field.",
                        "type": "value_error.filter",
                        "code": "VALUE_ERROR_FILTER",
                    }
                ]
            },
            status=400,
        )

    def execute_query(self, request, payload):
        """Execute a QLab query payload and return a DRF Response."""
        started_at = monotonic()
        prepared, error_response = self.prepare_query(request, payload, started_at)
        if error_response is not None:
            return error_response

        query = prepared["query"]
        app_label = prepared["app_label"]
        raw_results = prepared["queryset"]

        page_size, page_size_error = _parse_page_size(payload.get("page_size"))
        if page_size_error:
            return self._error_response(
                request,
                payload,
                app_label=app_label,
                model_name=query.model,
                started_at=started_at,
                loc="page_size",
                msg=page_size_error,
            )

        def page_out_of_range():
            return self._error_response(
                request,
                payload,
                app_label=app_label,
                model_name=query.model,
                started_at=started_at,
                loc="page",
                msg=f"Page '{query.page}' is out of range.",
                error_type="value_error.page",
            )

        count_mode = query.count or qlab_settings.DEFAULT_COUNT_MODE

        try:
            if count_mode == "exact":
                paginator = Paginator(raw_results, page_size)
                try:
                    page_obj = paginator.page(query.page)
                except (PageNotAnInteger, EmptyPage):
                    return page_out_of_range()
                rows = list(page_obj.object_list)
                total_count = paginator.count
                count_exact = True
                total_pages = paginator.num_pages
                has_next = page_obj.has_next()
                has_previous = page_obj.has_previous()
            else:
                # Skip the full count(*): fetch one extra row to know whether
                # a next page exists, and optionally estimate the total.
                offset = (query.page - 1) * page_size
                rows = list(raw_results[offset : offset + page_size + 1])
                has_next = len(rows) > page_size
                rows = rows[:page_size]
                if not rows and query.page > 1:
                    return page_out_of_range()
                has_previous = query.page > 1
                total_count, count_exact = None, False
                total_pages = None
                if count_mode == "estimate":
                    total_count, count_exact = estimate_count(raw_results)
                    if total_count is not None:
                        total_pages = max(1, -(-total_count // page_size))
        except (ValueError, TypeError, DjangoValidationError) as e:
            return self._invalid_values_response(
                request, payload, prepared, started_at, e
            )

        data = {
            "count": total_count,
            "count_exact": count_exact,
            "page": query.page,
            "page_size": page_size,
            "total_pages": total_pages,
            "next": query.page + 1 if has_next else None,
            "previous": query.page - 1 if has_previous else None,
            "results": self._finalize_rows(rows, prepared, request),
        }

        _record_query_history(
            request,
            payload=payload,
            app_label=app_label,
            model_name=query.model,
            status="success",
            duration_ms=int((monotonic() - started_at) * 1000),
            result_count=total_count,
        )

        return Response(ResponseSerializer(data).data)

    def export_query(self, request, payload):
        """
        Export every row of a query (not just one page) as CSV or JSON.

        The rows are streamed in chunks, so large exports don't have to be
        held in memory. At most ``MAX_EXPORT_ROWS`` rows are written; the
        ``X-QLab-Total-Count`` and ``X-QLab-Truncated`` headers tell the
        client whether the export was cut off.
        """
        started_at = monotonic()
        export_format = (
            payload.get("format", "csv") if hasattr(payload, "get") else "csv"
        )
        if export_format not in ("csv", "json"):
            return _field_error("format", "format must be 'csv' or 'json'.")

        prepared, error_response = self.prepare_query(request, payload, started_at)
        if error_response is not None:
            return error_response

        query = prepared["query"]
        queryset = prepared["queryset"]
        max_rows = int(qlab_settings.MAX_EXPORT_ROWS)

        try:
            total_count = queryset.count()
        except (ValueError, TypeError, DjangoValidationError) as e:
            return self._invalid_values_response(
                request, payload, prepared, started_at, e
            )

        truncated = total_count > max_rows
        exported = min(total_count, max_rows)
        _record_query_history(
            request,
            payload={**payload, "export_format": export_format},
            app_label=prepared["app_label"],
            model_name=query.model,
            status="success",
            duration_ms=int((monotonic() - started_at) * 1000),
            result_count=exported,
        )

        columns = list(query.select_fields)
        if prepared["add_pk_column"]:
            columns = ["id"] + columns
        columns += [agg.alias for agg in query.aggregations]

        def iter_rows():
            chunk = []
            for row in queryset[:max_rows].iterator(chunk_size=2000):
                chunk.append(row)
                if len(chunk) >= 2000:
                    yield from self._finalize_rows(chunk, prepared, request)
                    chunk = []
            if chunk:
                yield from self._finalize_rows(chunk, prepared, request)

        timestamp = timezone.now().strftime("%Y%m%d-%H%M%S")
        filename = f"{query.model.lower()}-{timestamp}.{export_format}"
        if export_format == "csv":
            content_type = "text/csv; charset=utf-8"
            stream = _stream_csv(columns, iter_rows())
        else:
            content_type = "application/json"
            stream = _stream_json(iter_rows())

        response = StreamingHttpResponse(stream, content_type=content_type)
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        response["X-QLab-Total-Count"] = str(total_count)
        response["X-QLab-Exported-Count"] = str(exported)
        response["X-QLab-Truncated"] = "true" if truncated else "false"
        response["Access-Control-Expose-Headers"] = (
            "Content-Disposition, X-QLab-Total-Count, X-QLab-Exported-Count, "
            "X-QLab-Truncated"
        )
        return response

    @extend_schema(
        summary="Execute Dynamic Query",
        description=(
            "Execute a dynamic query against a Django model.\n\n"
            "Supports field selection, AND/OR/NOT filtering, pagination, "
            "and relation traversal via __ syntax."
        ),
        request=inline_serializer(
            name="QLabQueryRequest",
            fields={
                "model": serializers.CharField(),
                "select_fields": serializers.ListField(child=serializers.CharField()),
                "filter_fields": serializers.DictField(required=False),
                "aggregations": serializers.ListField(
                    child=serializers.DictField(),
                    required=False,
                    help_text=(
                        "Aggregations computed per group of select_fields: "
                        "{field?, function, alias?, distinct?}."
                    ),
                ),
                "having": serializers.DictField(
                    required=False,
                    help_text="Filter tree on aggregation aliases (SQL HAVING).",
                ),
                "order_by": serializers.ListField(
                    child=serializers.CharField(),
                    required=False,
                    help_text="Fields to sort by; prefix with '-' for descending.",
                ),
                "count": serializers.ChoiceField(
                    choices=["exact", "estimate", "none"],
                    required=False,
                    help_text="How to count the total; defaults to DEFAULT_COUNT_MODE.",
                ),
                "page": serializers.IntegerField(required=False, default=1),
                "page_size": serializers.IntegerField(required=False),
                "app_label": serializers.CharField(required=False),
            },
        ),
        responses={200: ResponseSerializer, 400: _ERROR_SCHEMA},
        examples=[
            OpenApiExample(
                "Simple Query",
                value={
                    "model": "Book",
                    "select_fields": ["id", "title", "published", "author__first_name"],
                },
                request_only=True,
            ),
            OpenApiExample(
                "Query with AND Filter",
                value={
                    "model": "Book",
                    "select_fields": ["id", "title"],
                    "filter_fields": {
                        "and_operation": [
                            {"field": "published", "op": "is", "value": "true"}
                        ]
                    },
                },
                request_only=True,
            ),
        ],
    )
    def post(self, request):
        """Execute a dynamic QLab query."""
        return self.execute_query(request, request.data)

    @extend_schema(
        summary="Export Dynamic Query",
        description=(
            "Export all rows of a query (every page) as CSV or JSON.\n\n"
            "Takes the same payload as the query endpoint plus an optional "
            "format ('csv' or 'json'). page and page_size are ignored. At most "
            "MAX_EXPORT_ROWS rows are exported; the X-QLab-Truncated response "
            "header is 'true' when the result was cut off."
        ),
        request=inline_serializer(
            name="QLabExportRequest",
            fields={
                "model": serializers.CharField(),
                "select_fields": serializers.ListField(child=serializers.CharField()),
                "filter_fields": serializers.DictField(required=False),
                "aggregations": serializers.ListField(
                    child=serializers.DictField(), required=False
                ),
                "having": serializers.DictField(required=False),
                "order_by": serializers.ListField(
                    child=serializers.CharField(), required=False
                ),
                "format": serializers.ChoiceField(
                    choices=["csv", "json"], required=False, default="csv"
                ),
                "app_label": serializers.CharField(required=False),
            },
        ),
        responses={
            (200, "text/csv"): str,
            (200, "application/json"): str,
            400: _ERROR_SCHEMA,
        },
    )
    def export(self, request):
        """Export every row of a dynamic QLab query."""
        return self.export_query(request, request.data)


class QLabSuggestMixin:
    """
    Mixin for DRF ViewSets that suggests distinct values of a model field.

    Meant for typeahead filter inputs: instead of preloading every distinct
    value of a column, the client asks for a small, prefix-matched, cursor
    paged slice as the user types. The queryset is scoped through
    ``get_queryset(model)``, so values a user may not see are never returned.

    For large tables, back the suggested columns with an index (for prefix
    matching on PostgreSQL: ``text_pattern_ops`` or a trigram index).
    """

    def get_queryset(self, model):
        return model.objects.all()

    @extend_schema(
        summary="Suggest Field Values",
        description=(
            "Return distinct values of a field for typeahead inputs.\n\n"
            "Values are ordered ascending and paged with a cursor: pass the "
            "returned next_cursor as cursor to load the following slice."
        ),
        request=SuggestRequestSerializer,
        responses={
            200: inline_serializer(
                name="QLabSuggestResponse",
                fields={
                    "field": serializers.CharField(),
                    "values": serializers.ListField(),
                    "next_cursor": serializers.CharField(allow_null=True),
                },
            ),
            400: _ERROR_SCHEMA,
        },
    )
    def suggest(self, request):
        """Return distinct, cursor-paged values of a model field."""
        serializer = SuggestRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=400)
        params = serializer.validated_data

        model_name = params["model"]
        app_label = params.get("app_label") or qlab_settings.DEFAULT_APP_LABEL
        field_path = params["field"]

        allowed_app = _check_allowed_app(app_label)
        if allowed_app:
            return allowed_app
        restricted = _check_restricted(model_name, app_label, request.user)
        if restricted:
            return restricted
        try:
            model = apps.get_model(app_label, model_name)
        except LookupError:
            return _field_error(
                "model", f"Model '{model_name}' does not exist in app '{app_label}'."
            )

        errors = []
        if not is_valid_lookup_syntax(field_path) or not validate_field_path(
            model, field_path, errors
        ):
            return Response(
                ValidationError(errors or [_syntax_error(field_path)]).errors(),
                status=400,
            )
        blocked = _blocked_related_model(model, [field_path], request.user)
        if blocked:
            return Response(
                ValidationError(
                    [
                        {
                            "loc": ("field",),
                            "msg": f"Model '{blocked}' is restricted and cannot be queried through a relation.",
                            "type": "value_error.restricted",
                        }
                    ]
                ).errors(),
                status=403,
            )
        field = resolve_field(model, field_path)
        if (
            field is None
            or isinstance(field, SUGGEST_UNSUPPORTED_FIELDS)
            or find_to_many_relation_paths(model, field_path)
        ):
            return _field_error(
                "field", f"Field '{field_path}' does not support value suggestions."
            )

        limit = min(
            params.get("limit") or qlab_settings.SUGGEST_LIMIT,
            qlab_settings.MAX_SUGGEST_LIMIT,
        )
        text = params.get("q", "")
        lookup = "icontains" if params.get("match") == "contains" else "istartswith"

        queryset = self.get_queryset(model).filter(**{f"{field_path}__isnull": False})
        if isinstance(field, (models.CharField, models.TextField)):
            queryset = queryset.exclude(**{field_path: ""})
        if text:
            queryset = queryset.filter(**{f"{field_path}__{lookup}": text})
        cursor = params.get("cursor")
        if cursor not in (None, ""):
            queryset = queryset.filter(**{f"{field_path}__gt": cursor})

        try:
            values = list(
                queryset.order_by(field_path)
                .values_list(field_path, flat=True)
                .distinct()[: limit + 1]
            )
        except (ValueError, TypeError, DjangoValidationError):
            return _field_error(
                "cursor", "The query or cursor is invalid for this field."
            )

        has_next = len(values) > limit
        values = values[:limit]
        return Response(
            {
                "field": field_path,
                "values": values,
                "next_cursor": values[-1] if has_next else None,
            }
        )


class NeighborhoodMixin:
    """
    Mixin for DRF ViewSets that resolves all relations for a set of records.

    Automatically reads all FK, OneToOne and M2M relations from the given
    model and returns the PKs of related records per node.

    Override get_queryset() to restrict which records can be resolved,
    e.g. to enforce tenant or business group scoping.

    Guardrails:
        - MAX_NODES: Maximum number of node_pks per request (default: 100)

    Example:
        class MyNeighborhoodViewSet(NeighborhoodMixin, viewsets.ViewSet):
            permission_classes = [IsAuthenticated, MyCustomPermission]

            def get_queryset(self, model):
                return model.objects.filter(tenant=self.request.user.tenant)
    """

    def get_queryset(self, model):
        """
        Return the base queryset for neighborhood resolution.

        Override this method to restrict which records are accessible,
        e.g. by tenant, business group, or any other access restriction.

        Records not included in this queryset will be silently excluded
        from the neighborhood response, even if their IDs were requested.

        Args:
            model: The resolved Django model class from the request.

        Returns:
            A QuerySet of model instances to resolve neighborhoods for.
        """
        return model.objects.all()

    @extend_schema(
        summary="Resolve Neighborhood",
        description=(
            "Resolve all relations for a set of record IDs.\n\n"
            "Returns all FK, OneToOne and M2M relation PKs per node, "
            "including reverse relations. Each relation includes a filter_name "
            "for use in QLab query filter_fields."
        ),
        request=inline_serializer(
            name="QLabNeighborhoodRequest",
            fields={
                "model": serializers.CharField(),
                "node_pks": serializers.ListField(child=serializers.CharField()),
                "app_label": serializers.CharField(required=False),
            },
        ),
        responses={
            200: inline_serializer(
                name="QLabNeighborhoodResponse",
                fields={
                    "model": serializers.CharField(),
                    "records": serializers.ListField(child=serializers.DictField()),
                },
            ),
            400: _ERROR_SCHEMA,
        },
        examples=[
            OpenApiExample(
                "Neighborhood Request",
                value={"model": "Author", "node_pks": ["1", "2"]},
                request_only=True,
            ),
        ],
    )
    def neighborhood(self, request):
        """Resolve all relations for a set of record IDs."""
        model_name = request.data.get("model")
        app_label = request.data.get("app_label", qlab_settings.DEFAULT_APP_LABEL)
        node_pks = request.data.get("node_pks", [])

        # --- Input validation ---
        if not model_name:
            return Response(
                {
                    "errors": [
                        {
                            "loc": ["model"],
                            "msg": "model is required.",
                            "type": "value_error.missing",
                            "code": "VALUE_ERROR_MISSING",
                        }
                    ]
                },
                status=400,
            )

        if not node_pks:
            return Response(
                {
                    "errors": [
                        {
                            "loc": ["node_pks"],
                            "msg": "node_pks is required.",
                            "type": "value_error.missing",
                            "code": "VALUE_ERROR_MISSING",
                        }
                    ]
                },
                status=400,
            )

        # --- Guardrail: MAX_NODES ---
        max_nodes = qlab_settings.MAX_NODES
        if len(node_pks) > max_nodes:
            return Response(
                {
                    "errors": [
                        {
                            "loc": ["node_pks"],
                            "msg": f"Too many node_pks. Maximum allowed is {max_nodes}.",
                            "type": "value_error.max_nodes",
                            "code": "VALUE_ERROR_MAX_NODES",
                        }
                    ]
                },
                status=400,
            )

        # --- Resolve model ---
        allowed_app = _check_allowed_app(app_label)
        if allowed_app:
            return allowed_app
        restricted = _check_restricted(model_name, app_label, request.user)
        if restricted:
            return restricted
        try:
            model = apps.get_model(app_label, model_name)
        except LookupError:
            return Response(
                {
                    "errors": [
                        {
                            "loc": ["model"],
                            "msg": f"Model '{model_name}' does not exist in app '{app_label}'.",
                            "type": "value_error",
                            "code": "VALUE_ERROR",
                        }
                    ]
                },
                status=400,
            )

        # --- Fetch records using get_queryset() for scoping ---
        queryset = self.get_queryset(model).filter(pk__in=node_pks)

        # --- Resolve relations ---
        from django.db.models.fields.related import (
            ForeignKey,
            ManyToManyField,
            ManyToManyRel,
            ManyToOneRel,
            OneToOneField,
            OneToOneRel,
        )

        relation_fields = [
            field
            for field in model._meta.get_fields()
            if isinstance(
                field,
                (
                    ForeignKey,
                    OneToOneField,
                    ManyToManyField,  # forward
                    ManyToOneRel,
                    ManyToManyRel,
                    OneToOneRel,  # reverse
                ),
            )
        ]

        records = []
        for obj in queryset:
            relations = {}

            for field in relation_fields:
                # Forward relations: use field.name
                # Reverse relations: use related_name if set, otherwise auto-generated accessor
                if isinstance(field, (ForeignKey, OneToOneField, ManyToManyField)):
                    accessor = field.name
                else:
                    accessor = field.related_name or field.get_accessor_name()

                try:
                    if isinstance(field, (ForeignKey, OneToOneField)):
                        related_id = getattr(obj, f"{field.name}_id", None)
                        relations[accessor] = {
                            "pks": [related_id] if related_id is not None else [],
                            "filter_name": field.name,
                        }
                    elif isinstance(field, OneToOneRel):
                        related_obj = getattr(obj, accessor, None)
                        relations[accessor] = {
                            "pks": [related_obj.pk] if related_obj is not None else [],
                            "filter_name": field.related_query_name(),
                        }
                    elif isinstance(
                        field, (ManyToManyField, ManyToOneRel, ManyToManyRel)
                    ):
                        relations[accessor] = {
                            "pks": list(
                                getattr(obj, accessor).values_list("pk", flat=True)
                            ),
                            "filter_name": (
                                field.name
                                if hasattr(field, "name")
                                else field.related_query_name()
                            ),
                        }
                except Exception:
                    relations[accessor] = {"pks": [], "filter_name": accessor}

            records.append(
                {
                    "nodeId": str(obj.pk),
                    "relations": relations,
                }
            )

        return Response(
            {
                "model": f"{app_label}.{model_name}",
                "records": records,
            }
        )


class QLabMetadataMixin:
    """
    Mixin for DRF ViewSets that exposes model metadata for query building.

    Returns field types, allowed operations, relation info, and all valid
    lookup paths for a given model. Useful for building dynamic query UIs
    with autocomplete and validation.

    Permissions are handled entirely via DRF permission_classes.

    Example:
        class MyMetadataViewSet(QLabMetadataMixin, viewsets.ViewSet):
            permission_classes = [IsAuthenticated, IsAdminUser]
    """

    @extend_schema(
        summary="Get Model Metadata",
        description=(
            "Retrieve metadata for a Django model.\n\n"
            "Returns all fields, types, allowed operations, relation info, "
            "and valid lookup paths for autocomplete and validation."
        ),
        request=MetaDataRequestSerializer,
        responses={200: ModelMetadataSerializer, 400: _ERROR_SCHEMA},
        examples=[
            OpenApiExample(
                "Metadata Request",
                value={"model": "Book"},
                request_only=True,
            ),
        ],
    )
    def metadata(self, request):
        """Return metadata for a Django model."""
        serializer = MetaDataRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=400)

        model_name = serializer.validated_data["model"]
        app_label = serializer.validated_data.get(
            "app_label", qlab_settings.DEFAULT_APP_LABEL
        )
        relation_depth = serializer.validated_data.get(
            "relation_depth", qlab_settings.METADATA_MAX_RELATION_DEPTH
        )
        relation_depth = min(max(relation_depth, 0), qlab_settings.MAX_RELATION_DEPTH)
        include_reverse_relations = serializer.validated_data.get(
            "include_reverse_relations",
            qlab_settings.METADATA_INCLUDE_REVERSE_RELATIONS,
        )

        allowed_app = _check_allowed_app(app_label)
        if allowed_app:
            return allowed_app
        restricted = _check_restricted(model_name, app_label, request.user)
        if restricted:
            return restricted

        metadata = get_model_metadata(
            model_name,
            app_label=app_label,
            max_depth=relation_depth,
            include_reverse_relations=include_reverse_relations,
        )

        return Response(ModelMetadataSerializer(metadata).data)
