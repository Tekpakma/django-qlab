"""
Django REST Framework serializers for the QLab query API.

Defines serializers for:
- Field and model metadata responses
- Paginated query results
- Request validation
"""

from rest_framework import serializers

from qlab.helpers import model_exists
from qlab.models import QLabUserSettings, QueryRunHistory, SavedQuery
from qlab.settings import qlab_settings

# ---------------------------------------------------------------------------
# Field & Model Metadata
# ---------------------------------------------------------------------------


class FieldMetadataSerializer(serializers.Serializer):
    """Metadata for a single model field, including type, operations and relation info."""

    name = serializers.CharField(help_text="Full field path, e.g. 'author__first_name'")
    type = serializers.CharField(
        help_text="Field type: string, integer, boolean, foreignkey, reverse_relation, etc."
    )
    label = serializers.CharField(help_text="Human-readable field label")
    required = serializers.BooleanField(
        help_text="True if the field is not null and not blank"
    )
    primary_key = serializers.BooleanField(
        default=False, help_text="True if this field is the primary key"
    )
    allowed_operations = serializers.ListField(
        child=serializers.CharField(),
        help_text="Valid filter operations: is, is_not, lt, lte, gt, gte, icontains",
    )
    related_model = serializers.CharField(
        required=False,
        allow_null=True,
        help_text="Related model name (FK, M2M and reverse relations only)",
    )
    related_app_label = serializers.CharField(
        required=False,
        allow_null=True,
        help_text="Django app label of the related model.",
    )
    filter_name = serializers.CharField(
        required=False,
        allow_null=True,
        help_text="Query name to use in filter_fields (reverse relations only)",
    )
    max_length = serializers.IntegerField(
        required=False,
        allow_null=True,
        help_text="Maximum character length (text fields only)",
    )
    choices = serializers.ListField(
        required=False,
        allow_null=True,
        help_text="Available choices as {value, label} pairs (choice fields only)",
    )
    help_text = serializers.CharField(
        default="",
        allow_blank=True,
        allow_null=True,
        help_text="Django field help_text — human-readable description of the field",
    )
    blank = serializers.BooleanField(
        default=False,
        help_text="True if the field accepts empty/blank values",
    )
    null = serializers.BooleanField(
        default=False,
        help_text="True if the field stores NULL in the database",
    )


class ModelMetadataSerializer(serializers.Serializer):
    """Complete metadata for a Django model, including all fields and valid lookup paths."""

    model_name = serializers.CharField(help_text="Name of the Django model")
    app_label = serializers.CharField(
        help_text="Django app label containing this model"
    )
    primary_key_field = serializers.CharField(help_text="Name of the primary key field")
    fields = FieldMetadataSerializer(
        many=True, help_text="All field metadata including nested relations"
    )
    all_lookups = serializers.ListField(
        child=serializers.CharField(),
        help_text="Sorted list of all valid field paths for autocomplete",
    )


# ---------------------------------------------------------------------------
# Request Validation
# ---------------------------------------------------------------------------


class MetaDataRequestSerializer(serializers.Serializer):
    """Validates that the requested model exists before retrieving its metadata."""

    model = serializers.CharField(
        help_text="Model name to retrieve metadata for (case-insensitive)"
    )
    app_label = serializers.CharField(
        required=False,
        help_text="Optional Django app label containing the model.",
    )
    relation_depth = serializers.IntegerField(
        required=False,
        min_value=0,
        help_text="Optional relation expansion depth for metadata generation.",
    )
    include_reverse_relations = serializers.BooleanField(
        required=False,
        help_text="Whether reverse relations should be included in metadata expansion.",
    )

    def validate_model(self, value: str) -> str:
        app_label = self.initial_data.get("app_label")
        if not model_exists(value, app_label or None):
            raise serializers.ValidationError("This model does not exist.")
        return value


# ---------------------------------------------------------------------------
# Value Suggestions
# ---------------------------------------------------------------------------


class SuggestRequestSerializer(serializers.Serializer):
    """Request for typeahead value suggestions of a single field."""

    model = serializers.CharField(help_text="Model name (case-insensitive)")
    app_label = serializers.CharField(required=False, allow_blank=True)
    field = serializers.CharField(
        help_text="Field path to suggest values for, e.g. 'status' or 'owner__name'"
    )
    q = serializers.CharField(
        required=False, allow_blank=True, default="", help_text="Typed text to match"
    )
    match = serializers.ChoiceField(
        choices=["prefix", "contains"], required=False, default="prefix"
    )
    limit = serializers.IntegerField(required=False, min_value=1)
    cursor = serializers.CharField(
        required=False,
        allow_null=True,
        allow_blank=True,
        help_text="next_cursor of the previous response",
    )


# ---------------------------------------------------------------------------
# Query Response
# ---------------------------------------------------------------------------


class ResponseSerializer(serializers.Serializer):
    """Paginated query response with result list and pagination metadata."""

    count = serializers.IntegerField(
        allow_null=True,
        help_text=(
            "Total number of matching records across all pages. "
            "Null when the request used count mode 'none'; an estimate "
            "when count_exact is false."
        ),
    )
    count_exact = serializers.BooleanField(
        help_text="False if count is an estimate or was skipped"
    )
    page = serializers.IntegerField(help_text="Current page number (1-indexed)")
    page_size = serializers.IntegerField(help_text="Number of records per page")
    total_pages = serializers.IntegerField(
        allow_null=True, help_text="Total number of pages, null if unknown"
    )
    next = serializers.IntegerField(
        allow_null=True, help_text="Next page number, null if on last page"
    )
    previous = serializers.IntegerField(
        allow_null=True, help_text="Previous page number, null if on first page"
    )
    results = serializers.ListField(
        help_text="List of result objects with the requested fields"
    )


# ---------------------------------------------------------------------------
# Frontend Persistence
# ---------------------------------------------------------------------------


class QLabUserSettingsSerializer(serializers.ModelSerializer):
    def validate_default_page_size(self, value: int) -> int:
        if value < 1:
            raise serializers.ValidationError("default_page_size must be at least 1.")
        if value > qlab_settings.MAX_PAGE_SIZE:
            raise serializers.ValidationError(
                f"default_page_size cannot exceed {qlab_settings.MAX_PAGE_SIZE}."
            )
        return value

    class Meta:
        model = QLabUserSettings
        fields = [
            "theme",
            "default_page_size",
            "last_active_tab",
            "active_docs_key",
            "active_settings_key",
            "ui_state",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["created_at", "updated_at"]


class SavedQuerySerializer(serializers.ModelSerializer):
    def validate_tags(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError("tags must be a list.")
        if any(not isinstance(tag, str) for tag in value):
            raise serializers.ValidationError("tags must only contain strings.")
        return value

    def validate_query_payload(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("query_payload must be an object.")
        return value

    class Meta:
        model = SavedQuery
        fields = [
            "id",
            "name",
            "description",
            "app_label",
            "model_name",
            "query_payload",
            "tags",
            "is_shared",
            "last_run_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "last_run_at", "created_at", "updated_at"]

    def validate_model_name(self, value: str) -> str:
        if not value:
            raise serializers.ValidationError("model_name is required.")
        return value

    def validate_name(self, value: str) -> str:
        request = self.context.get("request")
        if request is None or not value:
            return value

        queryset = SavedQuery.objects.filter(user=request.user, name=value)
        if self.instance is not None:
            queryset = queryset.exclude(pk=self.instance.pk)
        if queryset.exists():
            raise serializers.ValidationError(
                "You already have a saved query with this name."
            )
        return value

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if not attrs.get("app_label"):
            attrs["app_label"] = (
                self.instance.app_label
                if self.instance and self.instance.app_label
                else qlab_settings.DEFAULT_APP_LABEL
            )
        return attrs


class QueryRunHistorySerializer(serializers.ModelSerializer):
    saved_query_name = serializers.CharField(source="saved_query.name", read_only=True)

    class Meta:
        model = QueryRunHistory
        fields = [
            "id",
            "title",
            "app_label",
            "model_name",
            "query_payload",
            "status",
            "duration_ms",
            "result_count",
            "error_message",
            "saved_query",
            "saved_query_name",
            "created_at",
        ]
        read_only_fields = fields
