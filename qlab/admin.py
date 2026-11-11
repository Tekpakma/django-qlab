from django.contrib import admin

from qlab.models import (
    ModelRegistry,
    QLabUserSettings,
    QueryRunHistory,
    SavedQuery,
    SharedQuery,
)


@admin.register(QLabUserSettings)
class QLabUserSettingsAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "theme",
        "default_page_size",
        "last_active_tab",
        "updated_at",
    )
    search_fields = ("user__username", "user__email")
    list_filter = ("theme", "last_active_tab")
    readonly_fields = ("created_at", "updated_at")


@admin.register(SavedQuery)
class SavedQueryAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "user",
        "app_label",
        "model_name",
        "is_shared",
        "last_run_at",
        "updated_at",
    )
    search_fields = ("name", "description", "user__username", "model_name", "app_label")
    list_filter = ("is_shared", "app_label", "model_name")
    readonly_fields = ("created_at", "updated_at", "last_run_at")


@admin.register(QueryRunHistory)
class QueryRunHistoryAdmin(admin.ModelAdmin):
    list_display = (
        "model_name",
        "user",
        "status",
        "saved_query",
        "result_count",
        "duration_ms",
        "created_at",
    )
    search_fields = (
        "title",
        "model_name",
        "app_label",
        "user__username",
        "error_message",
    )
    list_filter = ("status", "app_label", "model_name", "created_at")
    readonly_fields = ("created_at",)


@admin.register(SharedQuery)
class SharedQueryAdmin(admin.ModelAdmin):
    list_display = ("token", "model_name", "app_label", "created_by", "created_at")
    search_fields = ("token", "model_name", "app_label", "created_by__username")
    list_filter = ("app_label", "model_name")
    readonly_fields = ("created_at", "payload_hash")


@admin.action(description="Enable selected models")
def enable_models(modeladmin, request, queryset):
    queryset.update(status="enabled")


@admin.action(description="Disable selected models")
def disable_models(modeladmin, request, queryset):
    queryset.update(status="disabled")


@admin.action(description="Restrict selected models")
def restrict_models(modeladmin, request, queryset):
    queryset.update(is_restricted=True)


@admin.action(description="Allow selected models")
def allow_models(modeladmin, request, queryset):
    queryset.update(is_restricted=False)


@admin.register(ModelRegistry)
class ModelRegistryAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "updated_at",
        "app_label",
        "model_name",
        "status",
        "is_restricted",
    )
    search_fields = (
        "app_label",
        "model_name",
    )
    list_filter = ("status", "is_restricted", "app_label")
    readonly_fields = (
        "model_label",
        "app_label",
        "model_name",
        "created_at",
        "updated_at",
    )
    filter_horizontal = ("allowed_groups",)
    actions = [enable_models, disable_models, restrict_models, allow_models]
