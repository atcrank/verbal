import os
import shutil
from django.db import models
from django.conf import settings
from django.contrib.auth.models import User
from django.db.models.signals import post_delete
from django.dispatch import receiver
import uuid

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
    objects = ConversationManager()
    
    blueprint = models.ForeignKey(
        'metacognition.CognitiveBlueprint',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="conversations",
        help_text="The cognitive blueprint driving this conversation, if any."
    )

    class Meta:
        ordering = ['-start_time']

    def __str__(self):
        return f"Conversation with {self.user.username} (started {self.start_time.strftime('%Y-%m-%d')})"

    def as_messages(self):
        """
        Returns the conversation history as an LLM-ready "messages" list.

        This method relies on the `logs` having been prefetched
        (using .with_message_logs()) to be efficient.
        """
        messages = []
        system_prompt_count = 0
        # self.logs.all() will use the prefetched data.
        # The 'ordering' on PromptResponseLog.Meta ensures they are correct.
        print("generating messages")
        for log in self.logs.all():
            print("log", log)
            if log.system_prompt and system_prompt_count == 0:
                messages.append({
                    "role": "system",
                    "content": log.system_prompt
                })
                system_prompt_count += 1

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
            print(f"🗑️ Cleaned up workspace folder for deleted conversation {instance.id}")
        except Exception as e:
            print(f"⚠️ Failed to delete workspace {workspace_dir}: {e}")

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
    rag_selections = models.TextField(blank=True, null=True)
    git_commit_hash = models.CharField(max_length=40, blank=True, null=True)
    parent_log = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='child_logs')

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
    class Meta:
        ordering = ['-created_at']

class LocalAIModel(models.Model):
    """Configuration for an LLM loaded natively into VRAM on the inference server."""
    name = models.CharField(max_length=255, help_text="Friendly name (e.g. 'Qwen 2.5 3B')")
    hf_model_id = models.CharField(max_length=255, help_text="HuggingFace ID")
    description = models.TextField(blank=True, help_text="Notes on capabilities, VRAM usage, etc.")
    load_in_4bit = models.BooleanField(default=True, help_text="Use 4-bit quantization (Recommended for 6GB VRAM)")
    context_window = models.IntegerField(default=4096, help_text="Max tokens")

    def __str__(self):
        return f"{self.name}"


class SystemConfiguration(models.Model):
    """Singleton model for global system settings and VRAM management."""
    active_local_model = models.ForeignKey(
        LocalAIModel, on_delete=models.SET_NULL, null=True, blank=True,
        help_text="The PyTorch model loaded into VRAM. Leave blank to exclusively use Ollama/External APIs."
    )
    active_ollama_model = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text="The selected model currently running in the local Ollama container."
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
        except Exception:
            # Fails gracefully during initial DB migrations
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
    api_key = models.CharField(max_length=255, help_text="Stored as plaintext. Consider django-fernet-fields for production.")

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
from .ollama_client import set_ollama_model_state


@receiver(pre_save, sender=SystemConfiguration)
def track_ollama_model_changes(sender, instance, **kwargs):
    """Track the old model name so we know what to unload."""
    if instance.pk:
        old_instance = SystemConfiguration.objects.get(pk=instance.pk)
        instance._old_ollama_model = old_instance.active_ollama_model
    else:
        instance._old_ollama_model = None


@receiver(post_save, sender=SystemConfiguration)
def manage_ollama_vram(sender, instance, **kwargs):
    """Unload the old model and load the newly selected one."""
    old_model = getattr(instance, '_old_ollama_model', None)
    new_model = instance.active_ollama_model

    if old_model != new_model:
        # 1. Flush the previous model from VRAM
        if old_model:
            set_ollama_model_state(old_model, active=False)

        # 2. Pre-load the new model into VRAM
        if new_model:
            set_ollama_model_state(new_model, active=True)
