"""
QLab settings configuration.

Allows users to customize QLab behavior via Django settings.
"""

from django.conf import settings

# Default settings
QLAB_DEFAULTS = {
    "DEFAULT_APP_LABEL": "core",
    "PAGE_SIZE": 100,
    "MAX_PAGE_SIZE": 500,
    # Max relation hops in a field path (e.g. 2 allows "team__owner__name").
    "MAX_RELATION_DEPTH": 2,
    "METADATA_MAX_RELATION_DEPTH": 1,
    "METADATA_INCLUDE_REVERSE_RELATIONS": False,
    "MAX_FILTER_CONDITIONS": 10,
    "MAX_AGGREGATIONS": 10,
    "MAX_ORDER_FIELDS": 3,
    # Upper bound for rows written by the "export all" endpoint.
    "MAX_EXPORT_ROWS": 100000,
    "MAX_IN_VALUES": 200,
    # "exact" | "estimate" | "none"; can be overridden per request via "count".
    "DEFAULT_COUNT_MODE": "exact",
    # In "estimate" mode, results below this planner estimate are counted exactly.
    "COUNT_ESTIMATE_THRESHOLD": 10000,
    "SUGGEST_LIMIT": 20,
    "MAX_SUGGEST_LIMIT": 50,
    "MAX_NODES": 100,
    "MAX_HISTORY_ROWS": 100,
    # Max size (in bytes of JSON) of a query payload stored for a share link.
    "MAX_SHARE_PAYLOAD_BYTES": 65536,
    # Dotted path to the ViewSet serving query/export/metadata/suggest/
    # neighborhood. Point this at your scoped subclass so every endpoint —
    # including saved-query runs and exports — uses the same get_queryset().
    "QUERY_VIEWSET": "qlab.api_views.QLabFrontendApiViewSet",
    "ALLOWED_APPS": [],
    # "app_label.Model.field" entries that are never exposed (metadata, query,
    # suggest). None = default: the user model's password hash.
    "EXCLUDED_FIELDS": None,
    # "app_label.Model.field" -> ComputedField (class, instance or dotted path); see
    # qlab.computed. Selectable like model fields, resolved per page of rows.
    "COMPUTED_FIELDS": None,
    # Run history rows kept per user; older rows are pruned. 0 = keep all.
    "HISTORY_RETENTION": 1000,
    "INCLUDE_MODEL_COUNTS": False,
    "ENVIRONMENT_LABEL": "Production",
}


class QLABSettings:
    """
    Settings handler for QLab configuration.

    Allows users to override defaults via QLAB_SETTINGS in Django settings.

    Example in settings.py:
        QLAB_SETTINGS = {
            'DEFAULT_APP_LABEL': 'myapp',
            'PAGE_SIZE': 50,
        }
    """

    def __init__(self):
        # Get user settings from Django settings
        user_settings = getattr(settings, "QLAB_SETTINGS", {})

        # Merge with defaults
        self.settings = {**QLAB_DEFAULTS, **user_settings}

    def __getattr__(self, name):
        """Allow attribute-style access to settings."""
        if name in self.settings:
            return self.settings[name]
        raise AttributeError(f"'{self.__class__.__name__}' has no attribute '{name}'")

    def get(self, key, default=None):
        """Dictionary-style access to settings."""
        return self.settings.get(key, default)


qlab_settings = QLABSettings()
