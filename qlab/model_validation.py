"""
Pydantic models for query filter validation.

This module defines the core validation models used in the dynamic query system:
- Condition: Represents a single filter condition (field, operation, value)
- Filter: Represents complex nested filter logic with AND/OR/NOT operations
- ValidationError: Custom exception for structured validation error reporting

These models ensure type safety and validate query structure before execution.
"""

from typing import List, Optional, Union

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

# Every filter operation the API accepts. Which of them a concrete field
# supports is decided by qlab.helpers.FIELD_OPS.
ALL_OPERATIONS = (
    "is",
    "is_not",
    "lt",
    "lte",
    "gt",
    "gte",
    "icontains",
    "istartswith",
    "iendswith",
    "in",
    "not_in",
    "between",
)
# Operations whose value is a list instead of a single string.
LIST_OPERATIONS = ("in", "not_in", "between")


def _to_bool_or_str(value: str):
    if value in ("true", "True"):
        return True
    if value in ("false", "False"):
        return False
    return value


class ValidationError(Exception):
    """
    Custom exception for query validation errors.

    Provides structured error information that can be serialized to JSON
    and returned to API clients with detailed validation feedback.

    Attributes:
        _errors: List of error dictionaries, each containing:
            - loc: Tuple indicating error location (e.g., ("field",))
            - msg: Human-readable error message
            - type: Error type identifier (e.g., "value_error")

    Example:
        >>> raise ValidationError([{
        ...     "loc": ("field",),
        ...     "msg": "Field 'invalid' does not exist",
        ...     "type": "value_error"
        ... }])
    """

    def __init__(self, errors: List[dict]):
        self._errors = errors
        super().__init__()

    def errors(self) -> dict:
        """
        Get formatted error dictionary for API response.

        Converts internal error list into a unified, stable error shape
        suitable for API consumers and typed clients.

        Returns:
            Dictionary with "errors" key containing list of formatted errors,
            each with loc (list), msg (str), type (str), and code (str).

        Example:
            >>> try:
            ...     raise ValidationError([{"loc": ("field",), "msg": "Invalid", "type": "value_error"}])
            ... except ValidationError as e:
            ...     print(e.errors())
            {'errors': [{'loc': ['field'], 'msg': 'Invalid', 'type': 'value_error', 'code': 'VALUE_ERROR'}]}
        """
        formatted = []
        for error in self._errors:
            error_type = error.get("type", "value_error")
            formatted.append(
                {
                    "loc": list(error.get("loc", [])),
                    "msg": error.get("msg", ""),
                    "type": error_type,
                    "code": error_type.upper().replace(".", "_"),
                }
            )
        return {"errors": formatted}


class Condition(BaseModel):
    """
    Represents a single filter condition in a query.

    A Condition defines one filtering criterion consisting of:
    - field: The field path to filter on (e.g., "backup_job__name")
    - op: The comparison operation (e.g., "is", "gte", "icontains")
    - value: The value to compare against

    Attributes:
        field: Field path using Django's __ syntax for relations
        op: Operation type (is, is_not, lt, lte, gt, gte, icontains)
        value: Value to compare (automatically converted for booleans)

    Example:
        >>> cond = Condition(field="backup_size", op="gte", value="1000")
        >>> cond.field
        'backup_size'
        >>> cond.op
        'gte'
        >>> cond.value
        '1000'

        >>> # Boolean conversion
        >>> cond = Condition(field="is_active", op="is", value="true")
        >>> cond.value
        True
    """

    field: str
    op: str
    value: Union[str, List[str]]

    @field_validator("op")
    def validate_op(cls, op: str) -> str:
        """
        Validate that the operation type is supported.

        Supported operations:
            - is: Exact match (equality)
            - is_not: Negated exact match (inequality)
            - lt: Less than
            - lte: Less than or equal to
            - gt: Greater than
            - gte: Greater than or equal to
            - icontains: Case-insensitive substring search
            - istartswith: Case-insensitive prefix match
            - iendswith: Case-insensitive suffix match
            - in: Value is one of a list
            - not_in: Value is none of a list
            - between: Inclusive range, value is a list of two

        Args:
            op: Operation string to validate

        Returns:
            Validated operation string

        Raises:
            ValidationError: If operation is not in allowed list

        Example:
            >>> Condition(field="size", op="gte", value="100")  # Valid
            >>> Condition(field="size", op="invalid", value="100")  # Raises ValidationError
        """
        allowed_ops = list(ALL_OPERATIONS)
        if op not in allowed_ops:
            raise ValidationError(
                [
                    {
                        "loc": ("operation_type",),
                        "msg": f"The chosen operation type is not valid. Valid types are: {allowed_ops}",
                        "type": "value_error",
                    }
                ]
            )
        return op

    @model_validator(mode="after")
    def validate_value_shape(self):
        """Check that the value is a list for list operations, a string otherwise."""
        if self.op in LIST_OPERATIONS:
            if not isinstance(self.value, list) or not self.value:
                raise ValidationError(
                    [
                        {
                            "loc": ("filter_fields", self.field),
                            "msg": f"Operation '{self.op}' requires a non-empty list value.",
                            "type": "value_error.value_shape",
                        }
                    ]
                )
            if self.op == "between" and len(self.value) != 2:
                raise ValidationError(
                    [
                        {
                            "loc": ("filter_fields", self.field),
                            "msg": "Operation 'between' requires exactly two values: [start, end].",
                            "type": "value_error.value_shape",
                        }
                    ]
                )
        elif isinstance(self.value, list):
            raise ValidationError(
                [
                    {
                        "loc": ("filter_fields", self.field),
                        "msg": f"Operation '{self.op}' requires a single value, not a list.",
                        "type": "value_error.value_shape",
                    }
                ]
            )
        return self

    @field_validator("value")
    def validate_value(cls, value):
        """
        Validate and convert value, handling boolean string conversion.

        Automatically converts string representations of booleans to actual
        boolean values for proper database querying.

        Args:
            value: String value from request

        Returns:
            Converted value (bool if "true"/"false", otherwise original string)

        Example:
            >>> # String to boolean conversion
            >>> cond = Condition(field="active", op="is", value="true")
            >>> cond.value
            True
            >>> cond = Condition(field="active", op="is", value="False")
            >>> cond.value
            False

            >>> # Non-boolean values pass through
            >>> cond = Condition(field="name", op="is", value="test")
            >>> cond.value
            'test'
        """
        if isinstance(value, list):
            return [_to_bool_or_str(item) for item in value]
        return _to_bool_or_str(value)


class Filter(BaseModel):
    """
    Represents complex nested filter logic with boolean operations.

    A Filter can contain multiple Conditions or nested Filters combined
    using AND, OR, and NOT operations. This allows building arbitrarily
    complex query logic.

    Operations:
        - and_operation: All conditions must be true (logical AND)
        - or_operation: At least one condition must be true (logical OR)
        - not_operation: Negate the conditions (logical NOT)

    Attributes:
        and_operation: List of Filters/Conditions to AND together
        or_operation: List of Filters/Conditions to OR together
        not_operation: List of Filters/Conditions to negate

    Note:
        At least one operation must be specified. Multiple operations can
        be combined in a single Filter (e.g., AND and OR together).

    Example - Simple AND:
        >>> filter_obj = Filter(
        ...     and_operation=[
        ...         Condition(field="size", op="gt", value="100"),
        ...         Condition(field="active", op="is", value="true")
        ...     ]
        ... )
        >>> # Equivalent SQL: WHERE size > 100 AND active = true

    Example - OR with nested AND:
        >>> filter_obj = Filter(
        ...     or_operation=[
        ...         Condition(field="priority", op="is", value="high"),
        ...         Filter(
        ...             and_operation=[
        ...                 Condition(field="priority", op="is", value="medium"),
        ...                 Condition(field="age", op="lt", value="7")
        ...             ]
        ...         )
        ...     ]
        ... )
        >>> # Equivalent SQL: WHERE priority = 'high' OR (priority = 'medium' AND age < 7)

    Example - NOT operation:
        >>> filter_obj = Filter(
        ...     not_operation=[
        ...         Condition(field="status", op="is", value="deleted")
        ...     ]
        ... )
        >>> # Equivalent SQL: WHERE NOT status = 'deleted'

    Example - Complex combination:
        >>> filter_obj = Filter(
        ...     and_operation=[
        ...         Condition(field="type", op="is", value="backup")
        ...     ],
        ...     or_operation=[
        ...         Condition(field="size", op="gt", value="1000"),
        ...         Condition(field="priority", op="is", value="high")
        ...     ],
        ...     not_operation=[
        ...         Condition(field="archived", op="is", value="true")
        ...     ]
        ... )
        >>> # Equivalent SQL: WHERE type = 'backup'
        >>> #                  AND (size > 1000 OR priority = 'high')
        >>> #                  AND NOT archived = true
    """

    # Unknown keys are rejected: otherwise a typo such as "and_operaton" (or a
    # condition missing its "op") would parse as an empty filter and silently
    # return every row.
    model_config = ConfigDict(extra="forbid")

    and_operation: Optional[List[Union["Filter", Condition]]] = None
    or_operation: Optional[List[Union["Filter", Condition]]] = None
    not_operation: Optional[List[Union["Filter", Condition]]] = None


# Rebuild model to resolve forward references (Filter referencing itself)
Filter.model_rebuild()
