from django.apps import AppConfig


class MetacognitionConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "metacognition"

    def ready(self):
        import metacognition.signals  # noqa: F401
        
        from django.db.models.signals import post_migrate
        post_migrate.connect(self.seed_database, sender=self)

    def seed_database(self, sender, **kwargs):
        from metacognition.seed import seed_all
        seed_all()
