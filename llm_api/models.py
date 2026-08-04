import logging
logger = logging.getLogger(__name__)

import os
import shutil
import uuid
import typing
from django.db import models
from django_cryptography.fields import encrypt
from django.conf import settings
from django.contrib.auth.models import User
from django.db.models.signals import post_delete
from django.dispatch import receiver


# Create your models here.
class ConversationQuerySet(models.QuerySet):
    """
    A custom QuerySet for the Conversation model to add optimized queries.
    """

    def with_message_logs(self):
        """
        Returns a QuerySet that has pre-fetched all related logs
        and the conversation's user, to prevent N+1 queries.

        - select_related('user'): Fetches the User with the Conversation
          in a single SQL JOIN.
        - prefetch_related('logs'): Fetches all PromptResponseLog entries
          for the Conversation(s) in a separate, efficient query.
        """
        return self.select_related('user').prefetch_related('logs')


class ConversationManager(models.Manager):
    """
    A custom Manager for the Conversation model.
    """

    def get_queryset(self):
        """
        Tells the manager to use our custom ConversationQuerySet.
        """
        return ConversationQuerySet(self.model, using=self._db)

    def with_message_logs(self):
        """
        A helper to easily call the QuerySet's method.
        Usage: Conversation.objects.with_message_logs().get(id=...)
        """
        return self.get_queryset().with_message_logs()


class Conversation(models.Model):
    """
    A single, continuous chat session.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    start_time = models.DateTimeField(auto_now_add=True)

    # You can auto-generate this title from the first user_prompt!
    title = models.CharField(max_length=255, blank=True, default="New Conversation")
    state_tree = models.JSONField(blank=True, null=True, default=dict, help_text="Hierarchical task and topic tracking tree (WS7).")
    objects = ConversationManager()
    


    class Meta:
        ordering = ['-start_time']

    def __str__(self):
        return f"Conversation with {self.user.username} (started {self.start_time.strftime('%Y-%m-%d')})"

    def as_messages(self, leaf_log_id=None, max_logs: int | None = None):
        """
        Reconstructs the conversation as a list of message dicts.

        Traces the path from the specified leaf_log_id back to the root.
        If no leaf is provided, it defaults to the most recently created log.

        When max_logs is set, only the most recent N logs are included in the
        returned messages, with the original system prompt preserved and a
        condensation note inserted. This prevents unbounded context growth
        for long-running conversations.

        If max_logs is None, a default is derived from the active model's
        context_window (roughly context_window / 200 tokens per log pair,
        capped at 50). Pass max_logs=0 to disable limiting.
        """
        messages = []
        logs = list(self.logs.order_by('created_at'))
        if not logs:
            return messages

        # Resolve max_logs default from model context window
        if max_logs is None:
            max_logs = self._default_max_logs()

        # Check if the tree is linked
        is_linked = any(log.parent_log_id for log in logs)

        path = []
        if is_linked:
            log_dict = {log.id: log for log in logs}
            if leaf_log_id and leaf_log_id in log_dict:
                current_log = log_dict[leaf_log_id]
            else:
                # Default to the most recent log as the active leaf
                current_log = logs[-1]

            while current_log:
                path.append(current_log)
                current_log = log_dict.get(current_log.parent_log_id)
            path.reverse()
        else:
            path = logs  # Fallback to chronological order

        # Apply log window limit
        condensed_count = 0
        original_system_prompt = None
        if max_logs > 0 and len(path) > max_logs:
            # Preserve the system prompt from the earliest log
            original_system_prompt = path[0].system_prompt
            condensed_count = len(path) - max_logs
            path = path[-max_logs:]

        for i, log in enumerate(path):
            if log.system_prompt:
                # Only append if it's different from the last system prompt
                # (prevents cluttering when looping in the same step)
                if not messages or messages[-1].get("role") != "system" or messages[-1].get("content") != log.system_prompt:
                    messages.append({
                        "role": "system",
                        "content": log.system_prompt
                    })

            # Insert condensation note after restoring the system prompt
            if i == 0 and condensed_count > 0:
                if original_system_prompt and (not messages or messages[-1].get("content") != original_system_prompt):
                    messages.insert(0, {
                        "role": "system",
                        "content": original_system_prompt
                    })
                messages.append({
                    "role": "system",
                    "content": f"[Context note: {condensed_count} earlier conversation turns were omitted to fit the context window. The conversation continues from this point.]"
                })

            # This reconstructs the chat history turn-by-turn
            if log.user_prompt:
                messages.append({
                    "role": "user",
                    "content": log.user_prompt
                })

            # Use 'assistant_response' (or your field name)
            if log.generated_response:
                messages.append({
                    "role": "assistant",
                    "content": log.generated_response
                })

        return messages

    def _default_max_logs(self) -> int:
        """
        Derives a sensible max_logs from the active model's context_window.

        Heuristic: each log pair (user + assistant) averages ~200 tokens.
        We reserve 30% of the context window for the new prompt + generation,
        so usable_tokens = context_window * 0.7, and max_logs ≈ usable / 200.
        Clamped to [6, 50].
        """
        try:
            from llm_api.models import SystemConfiguration
            config = SystemConfiguration.get_solo()
            if config and config.active_local_model:
                context_window = config.active_local_model.context_window
            else:
                context_window = 8000  # Conservative default
            usable = int(context_window * 0.7)
            return max(6, min(50, usable // 200))
        except Exception:
            return 20  # Safe fallback

    def get_workspace_dir(self):
        """Returns the absolute path to this conversation's dedicated workspace."""
        return os.path.join(settings.BASE_DIR, 'workspaces', str(self.id))

    def get_workspace_files(self) -> str:
        """Produces a directory listing of the workspace."""
        workspace_dir = self.get_workspace_dir()
        if not os.path.exists(workspace_dir):
            return "(workspace does not exist)"
            
        import stat
        entries = []
        for root, dirs, files in os.walk(workspace_dir):
            if '.git' in dirs:
                dirs.remove('.git')  # Hide version control internals
            for name in files:
                file_path = os.path.join(root, name)
                rel_path = os.path.relpath(file_path, workspace_dir)
                try:
                    info = os.stat(file_path)
                    mode = stat.filemode(info.st_mode)
                    size = info.st_size
                    entries.append(f"{mode} {size:>8} bytes  {rel_path}")
                except OSError:
                    pass
        return "\n".join(entries) if entries else "(empty workspace)"

    def get_git_history(self) -> str:
        """Produces a summary of the Git commit history for this workspace."""
        workspace_dir = self.get_workspace_dir()
        if not os.path.exists(os.path.join(workspace_dir, '.git')):
            return "No git history."
            
        import subprocess
        try:
            result = subprocess.run(["git", "log", "--oneline", "--decorate", "--all", "-n", "10"], cwd=workspace_dir, capture_output=True, text=True, check=True)
            return result.stdout.strip() or "No commits yet."
        except Exception as e:
            return f"Git history error: {str(e)}"
            
    def get_file_at_commit(self, filename: str, commit_hash: str) -> typing.Optional[str]:
        """Extracts the file content from a specific git commit hash."""
        workspace_dir = self.get_workspace_dir()
        if not os.path.exists(workspace_dir):
            return None
            
        import subprocess
        try:
            safe_filename = filename.replace("\\", "/").lstrip("/")
            result = subprocess.run(["git", "show", f"{commit_hash}:{safe_filename}"], cwd=workspace_dir, capture_output=True, text=True, check=True)
            return result.stdout
        except Exception:
            return None


@receiver(post_delete, sender=Conversation)
def delete_conversation_workspace(sender, instance, **kwargs):
    """
    Automatically wipes the physical Git workspace folder when a Conversation 
    is deleted from the database to prevent disk space leaks.
    """
    if not instance.id:
        return
        
    workspace_dir = os.path.join(settings.BASE_DIR, 'workspaces', str(instance.id))
    
    if os.path.exists(workspace_dir):
        try:
            shutil.rmtree(workspace_dir)
            logger.info(f'🗑️ Cleaned up workspace folder for deleted conversation {instance.id}')
        except Exception as e:
            logger.info(f'⚠️ Failed to delete workspace {workspace_dir}: {e}')

class PromptResponseLog(models.Model):
    class Feedback(models.IntegerChoices):
        THUMB_UP = 1, 'Thumb Up'
        THUMB_DOWN = -1, 'Thumb Down'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    system_prompt = models.TextField()
    user_prompt = models.TextField(blank=True, null=True)
    generated_response = models.TextField()
    rag_selections = models.JSONField(blank=True, null=True, default=list)
    git_commit_hash = models.CharField(max_length=40, blank=True, null=True)
    parent_log = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='child_logs')
    input_tokens = models.IntegerField(default=0, help_text="Number of tokens in the prompt")
    output_tokens = models.IntegerField(default=0, help_text="Number of tokens in the generated response")
    generation_duration_ms = models.FloatField(null=True, blank=True, 
        help_text="Total wall-clock duration of the generation call in milliseconds")
    tokens_per_second = models.FloatField(null=True, blank=True,
        help_text="Output tokens generated per second")
    model_name = models.CharField(max_length=255, null=True, blank=True,
        help_text="The AI model used for this generation")

    # Telemetry for NightManager Performance Tracking
    reasoning_step = models.ForeignKey('metacognition.ReasoningStep', on_delete=models.SET_NULL, null=True, blank=True, related_name="prompt_logs")
    
    class StepStatus(models.TextChoices):
        SUCCESS = 'SUCCESS', 'Success'
        RETRY = 'RETRY', 'Retry (Self)'
        FAILURE = 'FAILURE', 'Failure (Re-plan)'
        
    step_status = models.CharField(max_length=20, choices=StepStatus.choices, null=True, blank=True)

    user_feedback = models.SmallIntegerField(
        choices=Feedback.choices,
        null=True,  # null=True means 'no feedback given'
        blank=True
    )
    conversation = models.ForeignKey(
        Conversation,
        null=True, blank=True,
        on_delete=models.CASCADE,
        related_name="logs"  # Lets you do conversation.logs.all()
    )
    blueprint = models.ForeignKey(
        'metacognition.CognitiveBlueprint',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="conversations",
        help_text="The cognitive blueprint driving this conversation, if any."
    )
    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['conversation']),
            # A GinIndex speeds up JSON containment queries e.g., contains '[{"id": "xxx"}]'
            # Note: Requires django.contrib.postgres in INSTALLED_APPS
            # GinIndex(fields=['rag_selections'])
        ]

class LocalAIModel(models.Model):
    """Configuration for an LLM loaded natively into VRAM on the inference server."""
    name = models.CharField(max_length=255, help_text="Friendly name (e.g. 'Qwen 2.5 3B')")
    hf_model_id = models.CharField(max_length=255, help_text="HuggingFace ID")
    description = models.TextField(blank=True, help_text="Notes on capabilities, VRAM usage, etc.")
    load_in_4bit = models.BooleanField(default=True, help_text="Use 4-bit quantization (Recommended for 6GB VRAM)")
    context_window = models.IntegerField(default=4096, help_text="Max tokens")

    def __str__(self):
        return f"{self.name}"


class LoRAAdapter(models.Model):
    """Configuration for a LoRA adapter that can be dynamically loaded over the base model."""
    name = models.CharField(max_length=255, help_text="Adapter short name (e.g., 'python_coder')")
    file_path = models.CharField(max_length=500, help_text="Absolute path to the adapter directory on disk or HF hub ID")
    description = models.TextField(blank=True, help_text="What this adapter specializes in.")
    base_model = models.ForeignKey(LocalAIModel, on_delete=models.CASCADE, related_name="adapters", help_text="The base model this adapter was trained for.")
    dataset = models.ForeignKey('benchmarking.FineTuningDataset', on_delete=models.SET_NULL, null=True, blank=True, help_text="The dataset this LoRA was trained on.")

    @property
    def is_stale(self):
        """Returns True if the underlying dataset is stale."""
        if self.dataset:
            return self.dataset.is_stale
        return False

    def __str__(self):
        return f"{self.name} (LoRA for {self.base_model.name})"

class SystemConfiguration(models.Model):
    """Singleton model for global system settings and VRAM management."""
    
    class HostingBackend(models.TextChoices):
        PYTORCH = 'pytorch', 'Local PyTorch (CPU/GPU)'
        VLLM = 'vllm', 'Local vLLM Container'
        OLLAMA = 'ollama', 'Local Ollama Container'

    hosting_backend = models.CharField(
        max_length=20,
        choices=HostingBackend.choices,
        default=HostingBackend.PYTORCH,
        help_text="Select the primary inference engine used by the local proxy server."
    )

    active_local_model = models.ForeignKey(
        LocalAIModel, on_delete=models.SET_NULL, null=True, blank=True,
        help_text="The PyTorch model loaded into VRAM. Used only when 'Local PyTorch' is the active hosting backend."
    )
    active_ollama_model = models.ForeignKey(
        LocalAIModel, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="ollama_configurations",
        help_text="The selected model running in the local Ollama container. Used only when 'Ollama Container' is the active hosting backend."
    )

    active_vllm_model = models.ForeignKey(
        LocalAIModel, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="vllm_configurations",
        help_text="Model loaded in the vLLM service. Used only when 'Local vLLM Container' is the active hosting backend."
    )
    system_tokenizer_id = models.CharField(
        max_length=255, default="Qwen/Qwen2.5-3B-Instruct",
        help_text="Loaded into CPU RAM to count tokens, even if local VRAM model is disabled."
    )

    def save(self, *args, **kwargs):
        self.pk = 1  # Enforce singleton pattern
        super().save(*args, **kwargs)

    @classmethod
    def get_solo(cls):
        try:
            obj, _ = cls.objects.get_or_create(pk=1)
            return obj
        except Exception as e:
            # Fails gracefully during initial DB migrations
            logger.info(f'Warning: SystemConfiguration.get_solo() failed: {e}')
            return None
        
    def __str__(self):
        return "Global System Configuration"


class ExternalAIModel(models.Model):
    """Configuration for an external API like OpenAI or Anthropic."""
    name = models.CharField(max_length=255, help_text="e.g. 'OpenAI GPT-4o'")
    provider = models.CharField(max_length=50, default="openai")
    api_url = models.URLField(default="https://api.openai.com/v1/chat/completions")
    api_model_name = models.CharField(max_length=255, help_text="e.g. 'gpt-4o'")
    context_window = models.IntegerField(default=128000)

    def __str__(self):
        return f"{self.name} ({self.provider})"

class UserAPIKey(models.Model):
    """Secure storage for user API keys. (Prototype: Plaintext)."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="api_keys")
    provider = models.CharField(max_length=50, help_text="Must match ExternalAIModel provider (e.g., 'openai')")
    api_key = encrypt(models.CharField(max_length=255))

    def __str__(self):
        return f"{self.provider} key for {self.user.username}"

class UserActiveModel(models.Model):
    """User preference: Route generation to local GPU or external API."""
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="ai_settings")
    active_external = models.ForeignKey(ExternalAIModel, null=True, blank=True, on_delete=models.SET_NULL)
    use_external = models.BooleanField(default=False, help_text="Route requests to external API instead of local GPU.")

    def __str__(self):
        return f"AI Settings for {self.user.username}"


from django.db.models.signals import pre_save, post_save
from django.dispatch import receiver
from . import ollama_client
from . import vllm_client


@receiver(pre_save, sender=SystemConfiguration)
def track_ollama_model_changes(sender, instance, **kwargs):
    """Track the old model name so we know what to unload."""
    if instance.pk:
        try:
            old_instance = SystemConfiguration.objects.get(pk=instance.pk)
            instance._old_ollama_model = old_instance.active_ollama_model
            instance._old_hosting_backend = old_instance.hosting_backend
        except SystemConfiguration.DoesNotExist:
            instance._old_ollama_model = None
            instance._old_hosting_backend = None
    else:
        instance._old_ollama_model = None
        instance._old_hosting_backend = None


@receiver(post_save, sender=SystemConfiguration)
def manage_hosting_backend(sender, instance, **kwargs):
    """Unload old models and manage Docker containers for the backend."""
    import sys
    if 'test' in sys.argv:
        return

    old_model = getattr(instance, '_old_ollama_model', None)
    new_model = instance.active_ollama_model
    
    old_backend = getattr(instance, '_old_hosting_backend', None)
    new_backend = instance.hosting_backend

    # Case 1: Switching away from Ollama or changing its model
    if old_backend == 'ollama' and old_model:
        if new_backend != 'ollama' or old_model != new_model:
            ollama_client.set_ollama_model_state(old_model.hf_model_id, active=False)

    # Manage Docker Containers if backend changed
    if old_backend != new_backend:
        if new_backend == 'vllm':
            ollama_client.stop_container()
            if instance.active_vllm_model:
                vllm_client.start_container(instance.active_vllm_model.hf_model_id)
        elif new_backend == 'ollama':
            vllm_client.stop_container()
            ollama_client.start_container()
        elif new_backend == 'pytorch':
            ollama_client.stop_container()
            vllm_client.stop_container()

    # Case 2: We are now using Ollama, and either just switched to it OR changed model
    if new_backend == 'ollama' and new_model:
        if old_backend != 'ollama' or old_model != new_model:
            ollama_client.set_ollama_model_state(new_model.hf_model_id, active=True)
