"""
Helper utilities for dynamic query system.

This module provides core functionality for:
- Model metadata extraction for autocomplete and validation
- Query filter construction (Pydantic Filter -> Django Q objects)
- Field path validation with helpful error messages
- Operation validation per field type
"""

import json
import re
from copy import deepcopy
from functools import lru_cache
from typing import List, Optional

from django.apps import apps
from django.core.exceptions import FieldDoesNotExist
from django.db import DatabaseError, connections, models
from django.db.models import ForeignKey, ManyToManyField, Q
from django.db.models.fields.related import (
    ManyToManyRel,
    ManyToOneRel,
    OneToOneField,
    OneToOneRel,
)

from qlab.model_validation import Condition, Filter
from qlab.settings import qlab_settings

_TEXT_OPS = ("is", "is_not", "icontains", "istartswith", "iendswith", "in", "not_in")
_NUMBER_OPS = ("is", "is_not", "lt", "lte", "gt", "gte", "between", "in", "not_in")
_DATE_OPS = ("is", "is_not", "lt", "lte", "gt", "gte", "between")

# Ordered: the first matching entry wins, so subclasses must come before
# their parents (e.g. DateTimeField before DateField).
FIELD_OPS = (
    # --- Text fields: equality, substring/prefix/suffix search, lists ---
    (models.CharField, _TEXT_OPS),
    (models.TextField, _TEXT_OPS),
    (models.FilePathField, ("is", "is_not", "icontains")),
    (models.GenericIPAddressField, ("is", "is_not", "icontains")),
    # --- Numeric fields: equality, comparison, ranges, lists ---
    (models.IntegerField, _NUMBER_OPS),
    (models.FloatField, _NUMBER_OPS),
    (models.DecimalField, _NUMBER_OPS),
    # --- Boolean: only equality checks ---
    (models.BooleanField, ("is", "is_not")),
    # --- Date/time fields: equality, comparison, ranges ---
    (models.DateTimeField, _DATE_OPS),
    (models.DateField, _DATE_OPS),
    (models.TimeField, _DATE_OPS),
    # --- Relations: equality and lists of primary keys ---
    (models.ForeignKey, ("is", "is_not", "in", "not_in")),
    (models.OneToOneField, ("is", "is_not", "in", "not_in")),
    (models.ManyToManyField, ("is", "is_not", "icontains")),
    # --- Special types ---
    (models.UUIDField, ("is", "is_not", "icontains", "istartswith", "in", "not_in")),
    (models.JSONField, ("is", "is_not", "icontains")),
    (models.FileField, ("is", "is_not")),
)


def _ops_for_field(field) -> tuple:
    """Return the filter operations allowed for a Django field instance."""
    for field_type, ops in FIELD_OPS:
        if isinstance(field, field_type):
            return ops
    return ()


def get_field_type_name(field) -> str:
    """
    Map Django field instances to string type names for frontend consumption.

    Args:
        field: Django model field instance

    Returns:
        String representation of field type (e.g., "string", "integer", "foreignkey")
        Returns "unknown" if field type is not mapped

    Example:
        >>> field = models.CharField(max_length=100)
        >>> get_field_type_name(field)
        'string'
    """
    type_mapping = {
        models.CharField: "string",
        models.TextField: "text",
        models.EmailField: "email",
        models.URLField: "url",
        models.SlugField: "slug",
        models.IntegerField: "integer",
        models.BigIntegerField: "bigint",
        models.SmallIntegerField: "smallint",
        models.PositiveIntegerField: "positiveint",
        models.FloatField: "float",
        models.DecimalField: "decimal",
        models.BooleanField: "boolean",
        models.DateField: "date",
        models.DateTimeField: "datetime",
        models.TimeField: "time",
        models.ForeignKey: "foreignkey",
        models.OneToOneField: "onetoone",
        models.ManyToManyField: "manytomany",
        models.UUIDField: "uuid",
        models.JSONField: "json",
        models.FileField: "file",
        models.ImageField: "image",
    }

    for field_class, type_name in type_mapping.items():
        if isinstance(field, field_class):
            return type_name

    return "unknown"


def get_allowed_operations(field) -> List[str]:
    """
    Determine which filter operations are valid for a given field type.

    Operations:
        - is/is_not: Exact match (available for all types)
        - lt/lte/gt/gte: Comparison operators (numbers, dates)
        - icontains: Case-insensitive substring search (text fields)

    Args:
        field: Django model field instance

    Returns:
        List of allowed operation strings for this field type

    Example:
        >>> field = models.IntegerField()
        >>> get_allowed_operations(field)
        ['is', 'is_not', 'lt', 'lte', 'gt', 'gte']
    """
    return list(_ops_for_field(field))


def extract_field_metadata(
    model: type[models.Model],
    prefix: str = "",
    max_depth: int = 2,
    current_depth: int = 0,
    visited_models: Optional[set] = None,
    include_reverse_relations: bool = False,
) -> tuple[List[dict], List[str]]:
    """
    Recursively extract metadata for all fields in a model, including related fields.

    This function traverses the model's field hierarchy, following ForeignKey and
    ManyToMany relationships up to a maximum depth. It prevents infinite loops
    by tracking visited models.

    Args:
        model: Django model class to extract metadata from
        prefix: Field path prefix for nested relations (e.g., "backup_job")
        max_depth: Maximum depth to traverse relations (default: 2)
        current_depth: Current recursion depth (internal use)
        visited_models: Set of already visited models to prevent cycles (internal use)

    Returns:
        Tuple of:
            - List of field metadata dictionaries, each containing:
                - name: Full field path (e.g., "backup_job__name")
                - type: Field type string
                - label: Human-readable label
                - required: Whether field is required
                - allowed_operations: Valid filter operations
                - max_length: Maximum length (text fields only)
                - choices: Available choices (choice fields only)
                - related_model: Related model name (FK/M2M only)
            - List of all valid lookup paths for autocomplete

    Example:
        >>> fields, lookups = extract_field_metadata(Book, max_depth=2)
        >>> lookups
        ['id', ...]
    """
    if visited_models is None:
        visited_models = set()

    model_id = (model._meta.app_label, model._meta.model_name)
    if model_id in visited_models:
        return [], []

    visited_models.add(model_id)

    fields_metadata = []
    all_lookups = []

    for field in model._meta.get_fields():
        is_reverse = isinstance(field, (ManyToOneRel, ManyToManyRel, OneToOneRel))
        if is_reverse and not include_reverse_relations:
            continue

        # Determine field name / accessor
        if is_reverse:
            # Use related_name if set, otherwise model name (e.g. book)
            field_name = field.related_name or field.related_model._meta.model_name
        else:
            field_name = field.name

        full_field_path = f"{prefix}__{field_name}" if prefix else field_name

        if is_reverse:
            # Reverse relation metadata
            related_model = field.related_model
            field_info = {
                "name": full_field_path,
                "type": "reverse_relation",
                "label": field_name,
                "help_text": str(getattr(field, "help_text", None)),
                "null": getattr(field, "null", False),
                "blank": getattr(field, "blank", False),
                "required": False,
                "allowed_operations": ["is", "is_not"],
                "related_model": related_model.__name__,
                "related_app_label": related_model._meta.app_label,
                "filter_name": field.related_name
                or field.related_model._meta.model_name,
            }
            fields_metadata.append(field_info)
            all_lookups.append(full_field_path)

            # Recurse into reverse related model
            if current_depth < max_depth:
                related_fields, related_lookups = extract_field_metadata(
                    model=related_model,
                    prefix=full_field_path,
                    max_depth=max_depth,
                    current_depth=current_depth + 1,
                    visited_models=visited_models.copy(),
                    include_reverse_relations=include_reverse_relations,
                )
                fields_metadata.extend(related_fields)
                all_lookups.extend(related_lookups)

        else:
            # Forward field metadata
            field_info = {
                "name": full_field_path,
                "type": get_field_type_name(field),
                "label": str(getattr(field, "verbose_name", field_name)),
                "help_text": str(getattr(field, "help_text", None)),
                "null": getattr(field, "null", False),
                "blank": getattr(field, "blank", False),
                "required": not getattr(field, "null", True)
                and not getattr(field, "blank", True),
                "primary_key": getattr(field, "primary_key", False),
                "allowed_operations": get_allowed_operations(field),
            }

            if hasattr(field, "max_length") and field.max_length:
                field_info["max_length"] = field.max_length

            if hasattr(field, "choices") and field.choices:
                field_info["choices"] = [
                    {"value": str(choice[0]), "label": str(choice[1])}
                    for choice in field.choices
                ]

            if isinstance(
                field, (models.ForeignKey, models.OneToOneField, models.ManyToManyField)
            ):
                field_info["related_model"] = field.related_model.__name__
                field_info["related_app_label"] = field.related_model._meta.app_label

            fields_metadata.append(field_info)
            all_lookups.append(full_field_path)

            # Recurse into forward related model
            if isinstance(
                field, (models.ForeignKey, models.OneToOneField, models.ManyToManyField)
            ):
                if current_depth < max_depth:
                    related_fields, related_lookups = extract_field_metadata(
                        model=field.related_model,
                        prefix=full_field_path,
                        max_depth=max_depth,
                        current_depth=current_depth + 1,
                        visited_models=visited_models.copy(),
                        include_reverse_relations=include_reverse_relations,
                    )
                    fields_metadata.extend(related_fields)
                    all_lookups.extend(related_lookups)

    return fields_metadata, all_lookups


@lru_cache(maxsize=256)
def _get_model_metadata_cached(
    model_name: str,
    app_label: str = "core",
    max_depth: int = 2,
    include_reverse_relations: bool = False,
) -> dict:
    """
    Get comprehensive metadata for a Django model.

    This is the main entry point for retrieving model metadata for use in
    autocomplete, validation, and query building UIs.

    Args:
        model_name: Name of the Django model (e.g., "Book")
        app_label: Django app containing the model (default: "core")
        max_depth: Maximum depth for traversing relations (default: 2)

    Returns:
        Dictionary containing:
            - model_name: Name of the model
            - app_label: Django app label
            - fields: List of all field metadata (including nested relations)
            - all_lookups: Sorted list of all valid field paths

    Raises:
        LookupError: If model doesn't exist

    Example:
        >>> metadata = get_model_metadata("Book", max_depth=2)
        >>> metadata['all_lookups'][:3]
        ['name', 'date', 'publisher__name']
    """
    if app_label is None:
        app_label = qlab_settings.DEFAULT_APP_LABEL
    if max_depth is None:
        max_depth = qlab_settings.MAX_RELATION_DEPTH
    model = apps.get_model(app_label, model_name)

    fields, lookups = extract_field_metadata(
        model,
        max_depth=max_depth,
        include_reverse_relations=include_reverse_relations,
    )

    return {
        "model_name": model.__name__,
        "app_label": app_label,
        "primary_key_field": model._meta.pk.name,
        "fields": fields,
        "all_lookups": sorted(lookups),
    }


def get_model_metadata(
    model_name: str,
    app_label: str = "core",
    max_depth: int = 2,
    include_reverse_relations: bool = False,
) -> dict:
    """
    Return model metadata with a defensive copy of the cached payload.
    """
    return deepcopy(
        _get_model_metadata_cached(
            model_name=model_name,
            app_label=app_label,
            max_depth=max_depth,
            include_reverse_relations=include_reverse_relations,
        )
    )


def build_q(filter_obj: Filter) -> Q:
    """
    Convert a Pydantic Filter object into a Django Q object.

    Recursively processes nested Filter objects and combines them using
    Django's Q object API for complex AND/OR/NOT queries.

    Args:
        filter_obj: Pydantic Filter containing and_operation, or_operation, or not_operation

    Returns:
        Django Q object ready to use in QuerySet.filter()

    Example:
        >>> filter_obj = Filter(
        ...     and_operation=[
        ...         Condition(field="price", op="gt", value="10"),
        ...         Condition(field="publisher__name", op="icontains", value="Sarah")
        ...     ]
        ... )
        >>> q = build_q(filter_obj)
        >>> Book.objects.filter(q)
    """
    q = Q()

    # AND: All conditions must be true
    if filter_obj.and_operation:
        for item in filter_obj.and_operation:
            q &= build_q(item) if isinstance(item, Filter) else condition_to_q(item)

    # OR: At least one condition must be true
    if filter_obj.or_operation:
        or_q = Q()
        for item in filter_obj.or_operation:
            or_q |= build_q(item) if isinstance(item, Filter) else condition_to_q(item)
        q |= or_q

    # NOT: Negate the conditions
    if filter_obj.not_operation:
        for item in filter_obj.not_operation:
            q &= ~build_q(item) if isinstance(item, Filter) else ~condition_to_q(item)

    return q


def condition_to_q(cond: Condition) -> Q:
    """
    Convert a single Condition into a Django Q object.

    Maps operation types to Django's field lookup syntax:
        - is -> exact match
        - is_not -> negated exact match
        - lt/lte/gt/gte -> comparison lookups
        - icontains/istartswith/iendswith -> case-insensitive text search
        - in/not_in -> membership in a list
        - between -> inclusive range (value is [start, end])

    Args:
        cond: Condition with field, operation, and value

    Returns:
        Django Q object for this condition

    Example:
        >>> cond = Condition(field="price", op="gte", value="10")
        >>> q = condition_to_q(cond)
        >>> # Equivalent to: Q(price__gte=10)
    """
    lookup = cond.field
    value = cond.value

    if cond.op == "is":
        return Q(**{lookup: value})
    if cond.op == "is_not":
        return ~Q(**{lookup: value})
    if cond.op in ("lt", "lte", "gt", "gte"):
        return Q(**{f"{lookup}__{cond.op}": value})
    if cond.op in ("icontains", "istartswith", "iendswith"):
        return Q(**{f"{lookup}__{cond.op}": value})
    if cond.op == "in":
        return Q(**{f"{lookup}__in": value})
    if cond.op == "not_in":
        return ~Q(**{f"{lookup}__in": value})
    if cond.op == "between":
        return Q(**{f"{lookup}__range": (value[0], value[1])})
    raise ValueError(f"Unsupported operation: {cond.op}")


def resolve_field(model, field_path: str):
    """
    Resolve a ``__`` field path to its final Django field instance.

    Returns None if any part of the path does not exist.
    """
    current = model
    field = None
    parts = field_path.split("__")
    for index, part in enumerate(parts):
        try:
            field = current._meta.get_field(part)
        except FieldDoesNotExist:
            return None
        if index < len(parts) - 1:
            current = getattr(field, "related_model", None)
            if current is None:
                return None
    return field


def estimate_count(queryset) -> tuple[Optional[int], bool]:
    """
    Cheaply estimate how many rows a queryset matches.

    On PostgreSQL the planner's row estimate (``EXPLAIN``, without executing
    the query) is used. Small results (below COUNT_ESTIMATE_THRESHOLD) and
    other database backends fall back to an exact ``count()``.

    Returns:
        ``(count, is_exact)``
    """
    if queryset.db and connections[queryset.db].vendor == "postgresql":
        try:
            plan = json.loads(queryset.explain(format="json"))
            estimate = int(plan[0]["Plan"]["Plan Rows"])
        except (ValueError, KeyError, IndexError, TypeError, DatabaseError):
            estimate = None
        if estimate is not None and estimate > qlab_settings.COUNT_ESTIMATE_THRESHOLD:
            return estimate, False
    return queryset.count(), True


def is_valid_lookup_syntax(lookup: str) -> bool:
    """
    Validate Django field lookup syntax.

    Valid syntax:
        - Starts with letter or underscore
        - Contains only alphanumeric and underscores
        - Relations separated by double underscore (__)

    Args:
        lookup: Field path to validate (e.g., "publisher__name")

    Returns:
        True if syntax is valid, False otherwise

    Example:
        >>> is_valid_lookup_syntax("publisher__name")
        True
        >>> is_valid_lookup_syntax("123invalid")
        False
        >>> is_valid_lookup_syntax("spaces not allowed")
        False
    """
    pattern = r"^[A-Za-z_][A-Za-z0-9_]*(__[A-Za-z_][A-Za-z0-9_]*)*$"
    return bool(re.match(pattern, lookup))


def flatten_filter_conditions(filter_obj: Filter) -> List[Condition]:
    """
    Extract all Condition objects from a nested Filter structure.

    Recursively flattens nested Filter objects to get a flat list of
    all Condition objects for validation purposes.

    Args:
        filter_obj: Nested Filter object

    Returns:
        Flat list of all Condition objects

    Example:
        >>> filter_obj = Filter(
        ...     and_operation=[
        ...         Condition(field="prcei", op="gt", value="10"),
        ...         Filter(or_operation=[
        ...             Condition(field="name", op="is", value="test")
        ...         ])
        ...     ]
        ... )
        >>> conditions = flatten_filter_conditions(filter_obj)
        >>> len(conditions)
        2
    """
    conditions = []

    for group in ["and_operation", "or_operation", "not_operation"]:
        items = getattr(filter_obj, group) or []
        for item in items:
            if isinstance(item, Filter):
                # Recursively flatten nested filters
                conditions.extend(flatten_filter_conditions(item))
            else:
                # Base case: it's a Condition
                conditions.append(item)

    return conditions


def validate_field_path(model, field_path: str, errors: list) -> bool:
    """
    Validate that a field path exists in the model and provide helpful suggestions.

    Recursively validates field paths including relations (using __ separator).
    Provides helpful error messages with suggestions for common mistakes.

    Args:
        model: Django model class to validate against
        field_path: Field path to validate (e.g., "publisher__name")
        errors: List to append error dictionaries to

    Returns:
        True if field path is valid, False otherwise

    Side Effects:
        Appends error dictionaries to the errors list if validation fails

    Example:
        >>> errors = []
        >>> validate_field_path(Book, "publisher__name", errors)
        True
        >>> validate_field_path(Book, "invalid_field", errors)
        False
        >>> errors
        [{'loc': ('field',), 'msg': "Field 'invalid_field' does not exist...", ...}]
    """
    if "__" not in field_path:
        # Simple field (no relations)
        field_names = [f.name for f in model._meta.get_fields()]
        if field_path not in field_names:
            # Try to provide helpful suggestion
            suggested = field_path.replace("_", "__")
            if any(
                suggested.startswith(f.name + "__") for f in model._meta.get_fields()
            ):
                errors.append(
                    {
                        "loc": ("field",),
                        "msg": f"Field '{field_path}' does not exist. Did you mean '{suggested}'?",
                        "type": "value_error",
                    }
                )
            else:
                errors.append(
                    {
                        "loc": ("field",),
                        "msg": f"Field '{field_path}' does not exist in model {model.__name__}",
                        "type": "value_error",
                    }
                )
            return False
        return True
    else:
        # Related field (has __ separator)
        related_name, remaining_path = field_path.split("__", 1)

        RELATION_TYPES = (
            ForeignKey,
            ManyToManyField,
            OneToOneField,  # forward
            ManyToOneRel,
            ManyToManyRel,
            OneToOneRel,  # reverse
        )

        # Try forward relation first
        try:
            related_field = model._meta.get_field(related_name)
        except FieldDoesNotExist:
            related_field = None

        # Try reverse relation accessors (related_name or model name e.g. book)
        if related_field is None:
            for f in model._meta.get_fields():
                if isinstance(f, (ManyToOneRel, ManyToManyRel, OneToOneRel)):
                    accessor = f.related_name or f.related_model._meta.model_name
                    if accessor == related_name:
                        related_field = f
                        break

        if related_field is None:
            errors.append(
                {
                    "loc": ("field",),
                    "msg": f"Field '{related_name}' does not exist in model {model.__name__}",
                    "type": "value_error",
                }
            )
            return False

        if not isinstance(related_field, RELATION_TYPES):
            errors.append(
                {
                    "loc": ("field",),
                    "msg": f"Field '{related_name}' is not a relation field in model {model.__name__}",
                    "type": "value_error",
                }
            )
            return False

        # Get related model — works for both forward and reverse
        related_model = (
            related_field.related_model
            if hasattr(related_field, "related_model")
            else related_field.field.model
        )
        return validate_field_path(related_model, remaining_path, errors)


def find_to_many_relation_paths(model, field_path: str) -> List[str]:
    """
    Walk a dunder field path and return every to-many relation it crosses
    (reverse foreign key, reverse many-to-many, or forward many-to-many).

    A to-many relation makes Django JOIN and return multiple rows per base
    object (e.g. one row per Team per Device). Ordering by the base object's
    pk alone leaves those rows in an arbitrary relative order, which can make
    LIMIT/OFFSET pagination split or duplicate rows across page boundaries.
    Callers use the returned relation paths to add each one's own pk as a
    secondary sort key, making the row order fully deterministic.

    Args:
        model: Django model class the path starts from.
        field_path: Dunder-separated field path (e.g. "devices__name").

    Returns:
        Relation paths crossed, in traversal order (e.g. ["devices"], or
        ["device_groups", "device_groups__devices"] for a path crossing two
        to-many relations in sequence). Empty list if the path never crosses
        one (safe to sort without a tiebreaker).
    """
    RELATION_TYPES = (
        ForeignKey,
        ManyToManyField,
        OneToOneField,
        ManyToOneRel,
        ManyToManyRel,
        OneToOneRel,
    )
    MANY_TYPES = (ManyToManyField, ManyToOneRel, ManyToManyRel)

    parts = field_path.split("__")
    current_model = model
    relation_paths = []

    for index, part in enumerate(parts):
        try:
            field = current_model._meta.get_field(part)
        except FieldDoesNotExist:
            field = None

        if field is None:
            for f in current_model._meta.get_fields():
                if isinstance(f, (ManyToOneRel, ManyToManyRel, OneToOneRel)):
                    accessor = f.related_name or f.related_model._meta.model_name
                    if accessor == part:
                        field = f
                        break

        if field is None or not isinstance(field, RELATION_TYPES):
            break

        if isinstance(field, MANY_TYPES):
            relation_paths.append("__".join(parts[: index + 1]))

        current_model = (
            field.related_model
            if hasattr(field, "related_model")
            else field.field.model
        )

    return relation_paths


NUMERIC_FIELD_TYPES = {
    "integer",
    "bigint",
    "smallint",
    "positiveint",
    "float",
    "decimal",
}


def is_numeric_field_path(model, field_path: str) -> bool:
    """
    Check whether a dunder field path resolves to a numeric field.

    Used to validate sum/avg aggregations before query execution: some
    database backends (SQLite in particular) silently return 0 for
    Sum()/Avg() on a non-numeric column instead of raising an error, so this
    can't be left to the database to catch.

    Args:
        model: Django model class the path starts from.
        field_path: Dunder-separated field path (e.g. "devices__id").

    Returns:
        True if the path resolves to a numeric field, False if it resolves
        to a non-numeric field or doesn't resolve at all.
    """
    parts = field_path.split("__")
    current_model = model
    field = None

    for part in parts:
        try:
            field = current_model._meta.get_field(part)
        except FieldDoesNotExist:
            field = None
            for f in current_model._meta.get_fields():
                if isinstance(f, (ManyToOneRel, ManyToManyRel, OneToOneRel)):
                    accessor = f.related_name or f.related_model._meta.model_name
                    if accessor == part:
                        field = f
                        break

        if field is None:
            return False

        if hasattr(field, "related_model") or hasattr(field, "field"):
            current_model = (
                field.related_model
                if hasattr(field, "related_model")
                else field.field.model
            )

    return field is not None and get_field_type_name(field) in NUMERIC_FIELD_TYPES


@lru_cache(maxsize=512)
def model_exists(model_name: str, app_label: str | None = None):
    """
    Check if a model exists in Django's app registry.

    When app_label is provided, uses Django's apps.get_model() for an exact
    lookup — safe when multiple apps share a model name. Falls back to a
    case-insensitive search across all apps when no app_label is given.

    Args:
        model_name: Name of the model to search for
        app_label: Optional Django app label for scoped lookup

    Returns:
        Model class if found, None otherwise

    Example:
        >>> model = model_exists("Book", app_label="library")
        >>> model.__name__
        'Book'
        >>> model_exists("NonExistentModel")
        None
    """
    if app_label:
        try:
            return apps.get_model(app_label, model_name)
        except LookupError:
            return None
    for app_config in apps.get_app_configs():
        for model in app_config.get_models():
            if model.__name__.lower() == model_name.lower():
                return model
    return None


def check_attribute_operation(model, field_name: str, op: str) -> bool:
    """
    Validate that an operation is allowed for a specific field type.
    Supports forward relations, reverse relations and related_name accessors via __ paths.
    """
    try:
        if "__" in field_name:
            parts = field_name.split("__")
            current_model = model
            for i, part in enumerate(parts):
                try:
                    field = current_model._meta.get_field(part)
                except FieldDoesNotExist:
                    # Try reverse relation accessor
                    field = None
                    for f in current_model._meta.get_fields():
                        if isinstance(f, (ManyToOneRel, ManyToManyRel, OneToOneRel)):
                            accessor = (
                                f.related_name or f.related_model._meta.model_name
                            )
                            if accessor == part:
                                field = f
                                break
                    if field is None:
                        return False

                if i < len(parts) - 1:
                    current_model = (
                        field.related_model
                        if hasattr(field, "related_model")
                        else field.field.model
                    )
        else:
            field = model._meta.get_field(field_name)
    except FieldDoesNotExist:
        return False

    return op in _ops_for_field(field)
