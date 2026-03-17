from django.contrib import admin
from .models import PromptResponseLog, Conversation
# Register your models here.
from django.contrib import admin, messages
from django.utils import timezone
from .models import Conversation, PromptResponseLog, AIModel
from .apps import service_registry


@admin.action(description="Activate this Model (Unloads current model)")
def activate_model(modeladmin, request, queryset):
    if queryset.count() != 1:
        modeladmin.message_user(request, "Please select exactly one model to activate.", level=messages.WARNING)
        return

    new_model = queryset.first()

    # 1. Update Database State
    AIModel.objects.update(is_active=False)
    new_model.is_active = True
    new_model.activated_by = request.user
    new_model.activated_at = timezone.now()
    # Optional: We could prompt for intent, but for now we just clear it or keep it?
    # Let's verify if the admin form has a save override, but for an action, we just set it.
    new_model.save()

    # 2. Trigger Hot Reload
    try:
        service_registry.reload_ai_service()
        modeladmin.message_user(request, f"Successfully loaded {new_model.name}.", level=messages.SUCCESS)
    except Exception as e:
        modeladmin.message_user(request, f"Error loading model: {e}", level=messages.ERROR)
        # Revert active state if failed? Maybe risky if we are now in limbo.


@admin.register(AIModel)
class AIModelAdmin(admin.ModelAdmin):
    list_display = ('name', 'is_active', 'activated_by', 'activated_at', 'usage_intent')
    list_filter = ('is_active',)
    actions = [activate_model]
    readonly_fields = ('activated_by', 'activated_at')

    def save_model(self, request, obj, form, change):
        # If creating a new active model manually via checkbox
        if obj.is_active:
            AIModel.objects.exclude(pk=obj.pk).update(is_active=False)
            obj.activated_by = request.user
            obj.activated_at = timezone.now()
        super().save_model(request, obj, form, change)


class DialogAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "created_at", "user_prompt", "conversation_id")
    search_fields = ("user", "system_prompt", "user_prompt", "generated_response", "rag_selections")

    class Meta:
        model = PromptResponseLog

class ConversationAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "start_time", "title")
    search_fields = ("user", "system prompt")

    class Meta:
        model = Conversation


admin.site.register(PromptResponseLog, DialogAdmin)
admin.site.register(Conversation, ConversationAdmin)