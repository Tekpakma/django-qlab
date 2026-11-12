"""
Computed (read-only) fields for QLab queries.

Not every value a table needs is a column: a link derived from several tables, a label
built from a JSON document, a censored copy of a field. A computed field is declared per
model and resolved for one page of rows at a time, so it can load what it needs with a few
queries instead of one per row.

Register computed fields in ``QLAB_SETTINGS`` (``"app_label.Model.field"`` -> class, instance
or dotted path)::

    QLAB_SETTINGS = {
        "COMPUTED_FIELDS": {
            "shop.Order.customer_url": "shop.qlab.CustomerUrl",
        },
    }

and implement them::

    class CustomerUrl(ComputedField):
        requires = ("customer",)

        def resolve(self, rows, request):
            names = dict(Customer.objects.filter(pk__in={r["customer"] for r in rows})
                         .values_list("pk", "slug"))
            return [f"/customers/{names.get(r['customer'])}/" for r in rows]

Clients select the field like any other (``"select_fields": ["id", "customer_url"]``). It
cannot be filtered, sorted or aggregated on, because it does not exist in the database.
"""

import re
from typing import Optional

from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured
from django.utils.module_loading import import_string

from qlab.settings import qlab_settings

_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ComputedField:
    """
    Base class of a computed field.

    Subclass it, list the model field paths the value is derived from in ``requires`` and
    implement ``resolve``.

    Attributes:
        requires: Model field paths (``"scope"``, ``"team__name"``) that ``resolve`` reads.
            They are fetched together with the selected fields, but only returned to the
            client if it selected them itself. They may point at fields that are hidden
            through ``EXCLUDED_FIELDS``: this code is trusted, it decides what it returns.
        type: Field type reported by the metadata endpoint, e.g. ``"string"``.
        label: Human readable name; defaults to the field name.
        help_text: Optional description reported by the metadata endpoint.
    """

    requires: tuple = ()
    type: str = "string"
    label: Optional[str] = None
    help_text: str = ""

    def resolve(self, rows: list, request) -> list:
        """
        Return the value for every row of the current page.

        Args:
            rows: One dict per row, in output order, with the values of ``requires`` and the
                primary key (under the name of the pk field).
            request: The DRF request, e.g. for per-user results.

        Returns:
            A list with exactly one value per row, in the same order.
        """
        raise NotImplementedError(
            f"{type(self).__name__} must implement resolve(rows, request)."
        )


def _load(key: str, value) -> ComputedField:
    if isinstance(value, str):
        try:
            value = import_string(value)
        except ImportError as exc:
            raise ImproperlyConfigured(
                f"QLAB_SETTINGS['COMPUTED_FIELDS']['{key}']: cannot import '{value}'."
            ) from exc
    if isinstance(value, type):
        value = value()
    if not isinstance(value, ComputedField):
        raise ImproperlyConfigured(
            f"QLAB_SETTINGS['COMPUTED_FIELDS']['{key}'] must be a ComputedField "
            "(class, instance or dotted path)."
        )
    return value


def _validate(model, key: str, name: str, field: ComputedField) -> None:
    from qlab.helpers import find_to_many_relation_paths, resolve_field

    if not _NAME_RE.match(name):
        raise ImproperlyConfigured(
            f"QLAB_SETTINGS['COMPUTED_FIELDS']['{key}']: '{name}' is not a valid field name."
        )
    try:
        model._meta.get_field(name)
    except FieldDoesNotExist:
        pass
    else:
        raise ImproperlyConfigured(
            f"QLAB_SETTINGS['COMPUTED_FIELDS']['{key}']: {model.__name__} already has a "
            f"field called '{name}'."
        )
    for path in field.requires:
        if resolve_field(model, path) is None:
            raise ImproperlyConfigured(
                f"QLAB_SETTINGS['COMPUTED_FIELDS']['{key}'] requires '{path}', which is "
                f"not a field of {model.__name__}."
            )
        if find_to_many_relation_paths(model, path):
            raise ImproperlyConfigured(
                f"QLAB_SETTINGS['COMPUTED_FIELDS']['{key}'] requires '{path}', which "
                "crosses a to-many relation."
            )


def get_computed_fields(model) -> dict:
    """
    Return the computed fields registered for ``model`` as ``{name: ComputedField}``.

    Raises:
        ImproperlyConfigured: If an entry is malformed, clashes with a real field or
            requires a field that does not exist.
    """
    configured = qlab_settings.get("COMPUTED_FIELDS") or {}
    wanted = (model._meta.app_label.lower(), model.__name__.lower())

    fields = {}
    for key, value in configured.items():
        parts = key.split(".")
        if len(parts) != 3:
            raise ImproperlyConfigured(
                f"QLAB_SETTINGS['COMPUTED_FIELDS'] key '{key}' must look like "
                "'app_label.Model.field'."
            )
        app_label, model_name, name = parts
        if (app_label.lower(), model_name.lower()) != wanted:
            continue
        field = _load(key, value)
        _validate(model, key, name, field)
        fields[name] = field
    return fields


def get_computed_field_metadata(model) -> list:
    """Metadata entries (shaped like the ones of real fields) of the computed fields."""
    return [
        {
            "name": name,
            "type": field.type,
            "label": field.label or name.replace("_", " ").capitalize(),
            "help_text": field.help_text,
            "null": True,
            "blank": True,
            "required": False,
            "primary_key": False,
            "unique": False,
            "allowed_operations": [],
            "computed": True,
        }
        for name, field in get_computed_fields(model).items()
    ]
