import os
from django.db import models
from django.conf import settings
from django.core.exceptions import ValidationError
from django.contrib.auth.models import User
import uuid


class SandboxConfiguration(models.Model):
    """
    Singleton model managing the Docker Sandbox environment.
    """
    requirements_txt = models.TextField(
        default="fastapi==0.104.1\nuvicorn==0.24.0\nnumpy\npandas\nnetworkx\npgmpy",
        help_text="Python packages to install in the sandbox. One per line."
    )
    execution_timeout = models.IntegerField(
        default=30,
        help_text="Maximum seconds a script can run before being killed."
    )
    last_rebuilt = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Sandbox Environment Configuration"
        verbose_name_plural = "Sandbox Environment Configuration"

    def clean(self):
        """Validates the PEP-508 syntax of the requirements before saving."""
        try:
            from packaging.requirements import Requirement
            from packaging.exceptions import InvalidRequirement
            
            for line in self.requirements_txt.splitlines():
                line = line.strip()
                # Skip empty lines, comments, and pip flags (e.g., --extra-index-url)
                if line and not line.startswith('#') and not line.startswith('-'):
                    try:
                        Requirement(line)
                    except InvalidRequirement as e:
                        raise ValidationError({'requirements_txt': f"Invalid package requirement '{line}': {e}"})
        except ImportError:
            pass  # Fallback if packaging library is missing

    def save(self, *args, **kwargs):
        self.pk = 1  # Enforce singleton
        super().save(*args, **kwargs)
        
        # Ensure the physical file stays in sync with the database on every save
        req_path = os.path.join(settings.BASE_DIR, 'sandbox', 'requirements.txt')
        os.makedirs(os.path.dirname(req_path), exist_ok=True)
        with open(req_path, 'w', encoding='utf-8') as f:
            f.write(self.requirements_txt)

    @classmethod
    def get_solo(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self):
        return "Sandbox Configuration"


class SandboxExecutionLog(models.Model):
    """
    Tracks individual code execution requests to identify missing packages or runaway loops.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    timestamp = models.DateTimeField(auto_now_add=True)
    conversation_id = models.CharField(max_length=255, blank=True, null=True)
    filepath = models.CharField(max_length=255)

    return_code = models.IntegerField(null=True, blank=True, help_text="0 is success, >0 is error, 124 is timeout.")
    stdout = models.TextField(blank=True, null=True)
    stderr = models.TextField(blank=True, null=True)

    class Meta:
        ordering = ['-timestamp']
