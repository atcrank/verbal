from django.apps import AppConfig


class MetacognitionConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "metacognition"

    def ready(self):
        import metacognition.signals  # noqa: F401
