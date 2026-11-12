"""
Pydantic filter for query validation.

This module defines QueryFilter, the main Pydantic model that validates
complete query requests including:
- Model existence and accessibility
- Field path validation (select and filter fields)
- Operation compatibility with field types
- Pagination parameters

QueryFilter performs comprehensive validation before query execution,
ensuring type safety and preventing invalid database queries.
"""

import re
from typing import Optional

from django.apps import apps
from pydantic import BaseModel, PrivateAttr, field_validator, model_validator

from qlab.computed import get_computed_fields
from qlab.helpers import (
    check_attribute_operation,
    check_value_type,
    find_to_many_relation_paths,
    flatten_filter_conditions,
    is_numeric_field_path,
    is_valid_lookup_syntax,
    model_exists,
    resolve_value_field,
    validate_field_path,
)
from qlab.model_validation import Filter, ValidationError
from qlab.settings import qlab_settings

AGGREGATION_FUNCTIONS = ("count", "sum", "avg", "min", "max")

# Operations allowed in ``having`` conditions. Aggregate values are numbers or
# (for min/max) plain column values, so text search operators make no sense.
HAVING_OPERATIONS = (
    "is",
    "is_not",
    "lt",
    "lte",
    "gt",
    "gte",
    "between",
    "in",
    "not_in",
)

_ALIAS_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class AggregationSpec(BaseModel):
    """
    A single aggregation to compute alongside select_fields.

    select_fields act as the GROUP BY columns — each aggregation is computed
    per group (e.g. number of devices per team).

    Attributes:
        field: Field path to aggregate (e.g. "devices" for count, or
            "devices__size" for sum/avg/min/max). Optional for count: without
            a field, the rows of each group are counted.
        function: Aggregate function to apply (count, sum, avg, min, max).
        alias: Result key for the computed value. Defaults to
            "{function}_{field}" (or "count" for a field-less count).
        distinct: Aggregate only distinct values of field (e.g. count of
            distinct regions rather than count of all matching rows).

    Example:
        >>> AggregationSpec(field="devices", function="count")
        AggregationSpec(field='devices', function='count', alias='count_devices', distinct=False)
    """

    field: Optional[str] = None
    function: str
    alias: Optional[str] = None
    distinct: bool = False

    @field_validator("function")
    def validate_function(cls, function: str) -> str:
        if function not in AGGREGATION_FUNCTIONS:
            raise ValidationError(
                [
                    {
                        "loc": ("aggregations", "function"),
                        "msg": (
                            f"Aggregation function '{function}' is not supported. "
                            f"Valid functions are: {', '.join(AGGREGATION_FUNCTIONS)}."
                        ),
                        "type": "value_error.aggregation_function",
                    }
                ]
            )
        return function

    @model_validator(mode="after")
    def set_default_alias(self):
        if self.field is not None and not self.field.strip():
            self.field = None
        if self.field is None and self.function != "count":
            raise ValidationError(
                [
                    {
                        "loc": ("aggregations", self.function),
                        "msg": f"Aggregation '{self.function}' requires a field.",
                        "type": "value_error.aggregation_field_missing",
                    }
                ]
            )
        if not self.alias:
            self.alias = (
                f"{self.function}_{self.field}" if self.field else self.function
            )
        return self


class QueryFilter(BaseModel):
    """
    Main validation model for dynamic query requests.

    Validates all aspects of a query request including model selection,
    field paths, filter operations, and pagination. Performs multi-stage
    validation to ensure the query is safe and executable.

    Validation Stages:
        1. Field-level: Validate individual fields (page, model)
        2. Model-level: Cross-validate fields and filters together
        3. Operation-level: Ensure operations match field types

    Attributes:
        model: Name of the Django model to query
        select_fields: List of field paths to return in results
        filter_fields: Optional Filter object with query conditions
        aggregations: Aggregations computed per group of select_fields
        having: Optional Filter on aggregation aliases (SQL HAVING)
        page: Page number for pagination (1-indexed, default: 1)

    Raises:
        ValidationError: If any validation fails, with detailed error information

    Example - Simple Query:
        >>> query = QueryFilter(
        ...     model="Backup",
        ...     select_fields=["id", "backup_size", "backup_date"],
        ...     page=1
        ... )

    Example - Query with Filters:
        >>> query = QueryFilter(
        ...     model="Backup",
        ...     select_fields=["id", "backup_job__name"],
        ...     filter_fields=Filter(
        ...         and_operation=[
        ...             Condition(field="backup_size", op="gte", value="1000"),
        ...             Condition(field="backup_date", op="gte", value="2024-01-01")
        ...         ]
        ...     ),
        ...     page=2
        ... )

    Example - Validation Error:
        >>> try:
        ...     query = QueryFilter(
        ...         model="InvalidModel",
        ...         select_fields=["id"],
        ...         page=1
        ...     )
        ... except ValidationError as e:
        ...     print(e.errors())
        {'validation_error': [{'loc': ('model',), 'msg': "Model 'InvalidModel' does not exist", ...}]}
    """

    model: str
    select_fields: list[str]
    filter_fields: Optional[Filter] = None
    aggregations: list[AggregationSpec] = []
    having: Optional[Filter] = None
    order_by: list[str] = []
    count: Optional[str] = None
    page: int = 1
    app_label: Optional[str] = None

    _computed: Optional[dict] = PrivateAttr(default=None)

    @field_validator("count")
    def validate_count(cls, count: Optional[str]) -> Optional[str]:
        if count is not None and count not in ("exact", "estimate", "none"):
            raise ValidationError(
                [
                    {
                        "loc": ("count",),
                        "msg": "count must be one of 'exact', 'estimate' or 'none'.",
                        "type": "value_error.count",
                    }
                ]
            )
        return count

    @field_validator("page")
    def validate_page(cls, page: int) -> int:
        """
        Validate that page number is positive.

        Pages are 1-indexed, so the minimum valid page is 1.

        Args:
            page: Page number from request

        Returns:
            Validated page number

        Raises:
            ValidationError: If page < 1

        Example:
            >>> QueryFilter(model="Test", select_fields=["id"], page=1)  # Valid
            >>> QueryFilter(model="Test", select_fields=["id"], page=0)  # ValidationError
            >>> QueryFilter(model="Test", select_fields=["id"], page=-1)  # ValidationError
        """
        if page < 1:
            raise ValidationError(
                [
                    {
                        "loc": ("page",),
                        "msg": "Page must be at least 1.",
                        "type": "value_error",
                    }
                ]
            )
        return page

    @field_validator("model")
    def validate_model(cls, model: str) -> str:
        """
        Validate that the requested model exists in any installed app.

        Performs a loose case-insensitive check across all apps. The scoped
        app_label + model lookup happens in validate_fields (model_validator)
        where both fields are accessible.

        Args:
            model: Model name from request

        Returns:
            Validated model name

        Raises:
            ValidationError: If model doesn't exist in any app
        """
        if not model_exists(model):
            raise ValidationError(
                [
                    {
                        "loc": ("model",),
                        "msg": f"Model '{model}' does not exist.",
                        "type": "value_error",
                    }
                ]
            )
        return model

    @model_validator(mode="after")
    def validate_fields(self):
        """
        Validate all field paths and filter operations.

        Performs comprehensive validation after all field-level validators
        have run. This includes:

        1. Select field validation:
           - Check field path syntax (__ for relations)
           - Verify each field exists in the model
           - Provide helpful suggestions for common mistakes

        2. Filter field validation:
           - Validate all filter field paths
           - Ensure operations are compatible with field types
           - Check nested filter conditions

        Returns:
            Self (validated instance)

        Raises:
            ValidationError: If any field or operation is invalid, with all errors collected

        Example Errors:
            # Invalid field path
            {'loc': ('field',), 'msg': "Field 'invalid_field' does not exist in model Backup"}

            # Wrong syntax (should use __ for relations)
            {'loc': ('field',), 'msg': "Field 'backup_job_name' does not exist. Did you mean 'backup_job__name'?"}

            # Invalid operation for field type
            {'loc': ('filter_fields', 'backup_size'),
            'msg': "Operation 'icontains' is not allowed for field 'backup_size'."}
        """
        # Get the actual Django model class — scoped to app_label to handle
        # duplicate model names across different apps.
        app_label = self.app_label or qlab_settings.DEFAULT_APP_LABEL
        try:
            model = apps.get_model(app_label, self.model)
        except LookupError:
            raise ValidationError(
                [
                    {
                        "loc": ("model",),
                        "msg": f"Model '{self.model}' does not exist in app '{app_label}'.",
                        "type": "value_error",
                    }
                ]
            )
        errors = []

        # --- Validate Select Fields ---
        if not self.select_fields:
            errors.append(
                {
                    "loc": ("select_fields",),
                    "msg": "At least one field must be selected.",
                    "type": "value_error.empty_select_fields",
                }
            )
        computed = self._computed_fields(model)
        for field in self.select_fields:
            if field in computed:
                if self.aggregations:
                    errors.append(
                        {
                            "loc": ("select_fields", field),
                            "msg": (
                                f"Computed field '{field}' cannot be selected in an "
                                "aggregation query."
                            ),
                            "type": "value_error.computed_field",
                        }
                    )
                continue
            self._validate_field(model, field, errors)

        # --- Validate Filter Fields ---
        if self.filter_fields:
            # Flatten nested filters to get all conditions
            all_conditions = flatten_filter_conditions(self.filter_fields)

            # Guardrail: max filter conditions
            max_conditions = qlab_settings.MAX_FILTER_CONDITIONS
            if len(all_conditions) > max_conditions:
                errors.append(
                    {
                        "loc": ["filter_fields"],
                        "msg": f"Too many filter conditions. Maximum allowed is {max_conditions}.",
                        "type": "value_error.max_filter_conditions",
                    }
                )
                raise ValidationError(errors)

            for cond in all_conditions:
                if isinstance(cond.value, list) and (
                    len(cond.value) > qlab_settings.MAX_IN_VALUES
                ):
                    errors.append(
                        {
                            "loc": ("filter_fields", cond.field),
                            "msg": f"Too many values. Maximum allowed is {qlab_settings.MAX_IN_VALUES}.",
                            "type": "value_error.max_in_values",
                        }
                    )
                    continue
                # First validate the field path exists
                valid_field = self._validate_field(model, cond.field, errors)

                # Then check if the operation is allowed for this field type
                if valid_field:
                    if not check_attribute_operation(model, cond.field, cond.op):
                        errors.append(
                            {
                                "loc": ("filter_fields", cond.field),
                                "msg": f"Operation '{cond.op}' is not allowed for field '{cond.field}'.",
                                "type": "operation_not_allowed",
                            }
                        )
                        continue
                    # And that the value fits the field type (id = "h" …)
                    problem = check_value_type(
                        resolve_value_field(model, cond.field), cond.op, cond.value
                    )
                    if problem:
                        errors.append(
                            {
                                "loc": ("filter_fields", cond.field),
                                "msg": f"Field '{cond.field}' {problem}.",
                                "type": "value_error.value_type",
                            }
                        )

        # --- Validate Aggregations ---
        self._validate_aggregations(model, errors)

        # --- Validate Having (filters on aggregation aliases) ---
        self._validate_having(model, errors)

        # --- Validate Order By ---
        self._validate_order_by(model, errors)

        # Raise all collected errors at once for better UX
        if errors:
            raise ValidationError(errors)

        return self

    def _validate_aggregations(self, model, errors: list) -> None:
        """Validate aggregation fields, function/field compatibility and aliases."""
        max_aggregations = qlab_settings.MAX_AGGREGATIONS
        if len(self.aggregations) > max_aggregations:
            errors.append(
                {
                    "loc": ["aggregations"],
                    "msg": f"Too many aggregations. Maximum allowed is {max_aggregations}.",
                    "type": "value_error.max_aggregations",
                }
            )
            return

        model_field_names = {f.name for f in model._meta.get_fields()}
        model_field_names.update(
            getattr(f, "attname", f.name) for f in model._meta.get_fields()
        )
        seen_aliases: set[str] = set()
        for agg in self.aggregations:
            alias = agg.alias or ""
            if not _ALIAS_PATTERN.match(alias) or "__" in alias:
                errors.append(
                    {
                        "loc": ("aggregations", alias),
                        "msg": (
                            f"Alias '{alias}' is invalid. Use letters, digits and "
                            "single underscores only."
                        ),
                        "type": "value_error.aggregation_alias",
                    }
                )
            elif alias in seen_aliases:
                errors.append(
                    {
                        "loc": ("aggregations", alias),
                        "msg": f"Alias '{alias}' is used more than once.",
                        "type": "value_error.aggregation_alias",
                    }
                )
            elif alias in model_field_names or alias in self.select_fields:
                # Django refuses annotations that shadow a model field, and a
                # clash with a select field would silently overwrite a column.
                errors.append(
                    {
                        "loc": ("aggregations", alias),
                        "msg": (
                            f"Alias '{alias}' conflicts with a field name; "
                            "choose a different alias."
                        ),
                        "type": "value_error.aggregation_alias",
                    }
                )
            seen_aliases.add(alias)

            if agg.field is None:
                continue
            valid_field = self._validate_field(model, agg.field, errors)

            # sum/avg on a non-numeric field silently returns 0 on some
            # database backends (SQLite in particular) instead of
            # raising — this can't be left to the database to catch.
            if (
                valid_field
                and agg.function in ("sum", "avg")
                and not is_numeric_field_path(model, agg.field)
            ):
                errors.append(
                    {
                        "loc": ("aggregations", agg.field),
                        "msg": (
                            f"Aggregation '{agg.function}' requires a numeric "
                            f"field; '{agg.field}' is not numeric."
                        ),
                        "type": "value_error.aggregation_field_type",
                    }
                )

    def _validate_having(self, model, errors: list) -> None:
        """
        Validate ``having`` conditions.

        ``having`` filters the grouped rows by their aggregated values (SQL
        HAVING), so every condition must reference an aggregation alias.
        """
        if not self.having:
            return

        if not self.aggregations:
            errors.append(
                {
                    "loc": ("having",),
                    "msg": "having can only be used together with aggregations.",
                    "type": "value_error.having",
                }
            )
            return

        conditions = flatten_filter_conditions(self.having)
        max_conditions = qlab_settings.MAX_FILTER_CONDITIONS
        if len(conditions) > max_conditions:
            errors.append(
                {
                    "loc": ("having",),
                    "msg": f"Too many having conditions. Maximum allowed is {max_conditions}.",
                    "type": "value_error.max_filter_conditions",
                }
            )
            return

        aliases = {agg.alias for agg in self.aggregations}
        for cond in conditions:
            if cond.field not in aliases:
                errors.append(
                    {
                        "loc": ("having", cond.field),
                        "msg": (
                            f"'{cond.field}' is not an aggregation alias. "
                            f"Available aliases: {', '.join(sorted(aliases))}."
                        ),
                        "type": "value_error.having",
                    }
                )
                continue
            agg = next(a for a in self.aggregations if a.alias == cond.field)
            values = cond.value if isinstance(cond.value, list) else [cond.value]
            if agg.function in ("count", "sum", "avg"):
                for item in values:
                    try:
                        float(item)
                    except (TypeError, ValueError):
                        errors.append(
                            {
                                "loc": ("having", cond.field),
                                "msg": f"'{cond.field}' is a number; '{item}' is not.",
                                "type": "value_error.value_type",
                            }
                        )
                        break
            elif agg.field:
                problem = check_value_type(
                    resolve_value_field(model, agg.field), cond.op, cond.value
                )
                if problem:
                    errors.append(
                        {
                            "loc": ("having", cond.field),
                            "msg": f"'{cond.field}' {problem}.",
                            "type": "value_error.value_type",
                        }
                    )
            if cond.op not in HAVING_OPERATIONS:
                errors.append(
                    {
                        "loc": ("having", cond.field),
                        "msg": f"Operation '{cond.op}' is not allowed on aggregated values.",
                        "type": "operation_not_allowed",
                    }
                )
            if isinstance(cond.value, list) and (
                len(cond.value) > qlab_settings.MAX_IN_VALUES
            ):
                errors.append(
                    {
                        "loc": ("having", cond.field),
                        "msg": f"Too many values. Maximum allowed is {qlab_settings.MAX_IN_VALUES}.",
                        "type": "value_error.max_in_values",
                    }
                )

    def _validate_order_by(self, model, errors: list) -> None:
        """
        Validate order_by entries ("field" ascending, "-field" descending).

        Ordering across a to-many relation would duplicate base rows, so it is
        rejected. For aggregation queries only the group-by columns
        (select_fields) and aggregation aliases can be ordered on.
        """
        if not self.order_by:
            return

        max_order = qlab_settings.MAX_ORDER_FIELDS
        if len(self.order_by) > max_order:
            errors.append(
                {
                    "loc": ["order_by"],
                    "msg": f"Too many order_by fields. Maximum allowed is {max_order}.",
                    "type": "value_error.max_order_by",
                }
            )
            return

        aliases = {agg.alias for agg in self.aggregations}
        for entry in self.order_by:
            name = entry[1:] if entry.startswith("-") else entry
            if self.aggregations:
                if name not in aliases and name not in self.select_fields:
                    errors.append(
                        {
                            "loc": ("order_by", name),
                            "msg": (
                                f"'{name}' cannot be ordered on in an aggregation "
                                "query; use a select field or an aggregation alias."
                            ),
                            "type": "value_error.order_by",
                        }
                    )
                continue
            if not self._validate_field(model, name, errors):
                continue
            if find_to_many_relation_paths(model, name):
                errors.append(
                    {
                        "loc": ("order_by", name),
                        "msg": f"Cannot order by '{name}': it crosses a to-many relation.",
                        "type": "value_error.order_by",
                    }
                )

    def _computed_fields(self, model) -> dict:
        """The computed fields of ``model``, looked up once per query."""
        if self._computed is None:
            self._computed = get_computed_fields(model)
        return self._computed

    def _validate_field(self, model, field: str, errors: list) -> bool:
        """
        Validate a single field path.

        Internal helper method that validates field syntax and existence.
        Used by both select field and filter field validation.

        Args:
            model: Django model class to validate against
            field: Field path to validate (e.g., "backup_job__name")
            errors: List to append error dictionaries to

        Returns:
            True if field is valid, False otherwise

        Side Effects:
            Appends error dictionaries to errors list if validation fails

        Validation Steps:
            1. Check syntax (alphanumeric, underscores, __ for relations)
            2. Verify field exists in model
            3. For related fields, recursively validate the path

        Example:
            >>> errors = []
            >>> query._validate_field(Backup, "id", errors)
            True
            >>> errors
            []

            >>> errors = []
            >>> query._validate_field(Backup, "invalid_field", errors)
            False
            >>> errors
            [{'loc': ('field',), 'msg': "Field 'invalid_field' does not exist...", ...}]
        """
        if field in self._computed_fields(model):
            errors.append(
                {
                    "loc": ("field",),
                    "msg": (
                        f"Field '{field}' is computed and can only be selected, "
                        "not filtered, sorted or aggregated on."
                    ),
                    "type": "value_error.computed_field",
                }
            )
            return False
        # First check if the syntax is valid (alphanumeric + underscores + __)
        if not is_valid_lookup_syntax(field):
            errors.append(
                {
                    "loc": ("field",),
                    "msg": f"Field '{field}' syntax is incorrect. Use __ for relations and _ for attributes.",
                    "type": "syntax_error",
                }
            )
            return False
        max_depth = qlab_settings.MAX_RELATION_DEPTH
        if field.count("__") > max_depth:
            # Every hop is a JOIN; unbounded paths are an easy way to put
            # heavy load on the database.
            errors.append(
                {
                    "loc": ("field",),
                    "msg": (
                        f"Field '{field}' follows too many relations. "
                        f"Maximum allowed is {max_depth}."
                    ),
                    "type": "value_error.max_relation_depth",
                }
            )
            return False
        # Syntax is valid, now check if the field actually exists
        return validate_field_path(model, field, errors)
