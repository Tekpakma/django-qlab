from django.apps import apps
from django.db.models.signals import post_migrate
from django.dispatch import receiver

# Apps whose models are never offered for querying.
_INTERNAL_APPS = {"qlab", "admin", "contenttypes", "sessions", "migrations"}


@receiver(post_migrate)
def sync_qlab_registry(sender, **kwargs):  # noqa
    """
    Register every queryable model in the ModelRegistry after migrations.

    New models are added with status "submitted" (an admin enables them).
    Existing entries — including their status, restriction and groups — are
    left untouched; only entries for models that no longer exist are removed.
    Runs after every app's migrations; the work is idempotent.
    """
    from qlab.models import ModelRegistry
    from qlab.settings import qlab_settings

    allowed_apps = list(qlab_settings.ALLOWED_APPS or [])
    if allowed_apps:
        app_configs = []
        for app_label in allowed_apps:
            try:
                app_configs.append(apps.get_app_config(app_label))
            except LookupError:
                continue
    else:
        # Empty ALLOWED_APPS means "all apps" — previously this registered
        # nothing and then deleted every registry entry on each migrate.
        app_configs = [
            config
            for config in apps.get_app_configs()
            if config.label not in _INTERNAL_APPS
        ]

    for app_config in app_configs:
        for model in app_config.get_models():
            # Match on app + model name, not on model_label: entries created
            # by hand in the admin may use a different label format.
            exists = ModelRegistry.objects.filter(
                app_label=app_config.label, model_name=model.__name__
            ).exists()
            if exists:
                continue
            label = f"{app_config.label}_{model.__name__}"
            if ModelRegistry.objects.filter(model_label=label).exists():
                continue
            ModelRegistry.objects.create(
                model_label=label,
                app_label=app_config.label,
                model_name=model.__name__,
                status="submitted",
            )

    # Only drop entries whose model no longer exists. Entries outside
    # ALLOWED_APPS are kept, so their status/groups survive if the app is
    # allowed again later.
    stale = [
        entry.pk
        for entry in ModelRegistry.objects.all()
        if not _model_exists(entry.app_label, entry.model_name)
    ]
    if stale:
        ModelRegistry.objects.filter(pk__in=stale).delete()


def _model_exists(app_label: str, model_name: str) -> bool:
    try:
        apps.get_model(app_label, model_name)
    except (LookupError, ValueError):
        return False
    return True
