from django.apps import AppConfig


class QLabConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "qlab"
    verbose_name = "QLab"

    def ready(self):
        # Connect the post_migrate handler that fills the ModelRegistry.
        from qlab import signals  # noqa: F401
