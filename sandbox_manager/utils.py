import os
import subprocess
from django.conf import settings
from django.utils import timezone
from .models import SandboxConfiguration


def rebuild_sandbox_image():
    """
    Writes the requirements.txt and triggers Docker Compose to rebuild the sandbox.
    """
    config = SandboxConfiguration.get_solo()

    # 1. Write the requirements.txt to the sandbox folder
    req_path = os.path.join(settings.BASE_DIR, 'sandbox', 'requirements.txt')
    with open(req_path, 'w', encoding='utf-8') as f:
        f.write(config.requirements_txt)

    # 2. Execute docker compose build
    try:
        # Using docker compose up -d --build ensures it rebuilds and restarts smoothly
        result = subprocess.run(
            ["docker", "compose", "up", "-d", "--build", "sandbox"],
            cwd=settings.BASE_DIR,
            capture_output=True,
            text=True,
            check=True
        )
        config.last_rebuilt = timezone.now()
        config.save()
        return True, result.stdout
    except subprocess.CalledProcessError as e:
        return False, e.stderr
