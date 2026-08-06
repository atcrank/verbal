from django.contrib import admin, messages
from django import forms
from django.urls import reverse
from django.utils.html import format_html
from django.db.models import Count
from .models import LocalAIModel, ExternalAIModel, UserActiveModel, UserAPIKey, SystemConfiguration, PromptResponseLog, Conversation, LoRAAdapter

@admin.action(description="Load this Model into Inference Server VRAM")
def activate_local_model(modeladmin, request, queryset):
    if queryset.count() != 1:
        modeladmin.message_user(request, "Please select exactly one model.", level=messages.WARNING)
        return

    new_model = queryset.first()
    config = SystemConfiguration.get_solo()
    config.active_local_model = new_model
    config.save()
    modeladmin.message_user(request, f"System configured to load {new_model.name} into VRAM.", level=messages.SUCCESS)

@admin.action(description="Unload all Local Models (Free VRAM for Ollama)")
def unload_local_models(modeladmin, request, queryset):
    config = SystemConfiguration.get_solo()
    config.active_local_model = None
    config.save()
    modeladmin.message_user(request, "System configured to bypass local VRAM loading.", level=messages.SUCCESS)

@admin.register(LocalAIModel)
class LocalAIModelAdmin(admin.ModelAdmin):
    list_display = ('name', 'hf_model_id')
    search_fields = ('name', 'hf_model_id')
    actions = [activate_local_model, unload_local_models]

class PromptResponseLogInline(admin.TabularInline):
    model = PromptResponseLog
    extra = 0
    readonly_fields = ('id', 'created_at', 'model_name', 'step_status', 'truncated_prompt', 'truncated_response')
    fields = ('id', 'created_at', 'model_name', 'step_status', 'truncated_prompt', 'truncated_response')
    can_delete = False
    
    def truncated_prompt(self, obj):
        return (obj.user_prompt[:50] + '...') if obj.user_prompt and len(obj.user_prompt) > 50 else obj.user_prompt
    
    def truncated_response(self, obj):
        return (obj.generated_response[:50] + '...') if obj.generated_response and len(obj.generated_response) > 50 else obj.generated_response


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ('title', 'user', 'start_time', 'log_count', 'view_logs_link')
    list_filter = ('start_time', 'user')
    search_fields = ('title', 'user__username')
    readonly_fields = ('id', 'start_time')
    autocomplete_fields = ('user', )
    inlines = [PromptResponseLogInline]

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.annotate(log_count=Count('promptresponselog'))

    def log_count(self, obj):
        return obj.log_count
    log_count.admin_order_field = 'log_count'

    def view_logs_link(self, obj):
        url = reverse("admin:llm_api_promptresponselog_changelist") + f"?conversation__id__exact={obj.id}"
        return format_html('<a href="{}">View {} Logs</a>', url, obj.log_count)
    view_logs_link.short_description = "Logs"

@admin.register(PromptResponseLog)
class PromptResponseLogAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'conversation', 'feedback_display', 'step_status', 'reasoning_step', 'model_name', 'created_at')
    list_filter = ('user_feedback', 'step_status', 'reasoning_step__blueprint__name', 'model_name', 'created_at', 'user')
    search_fields = ('user_prompt', 'generated_response', 'system_prompt', 'conversation__title', 'user__username', 'reasoning_step__name')
    readonly_fields = ('id', 'created_at')
    autocomplete_fields = ('user', 'conversation')

    @admin.display(description='Feedback')
    def feedback_display(self, obj):
        return obj.get_user_feedback_display() if obj.user_feedback is not None else "—"


@admin.register(SystemConfiguration)
class SystemConfigurationAdmin(admin.ModelAdmin):
    fieldsets = (
        ('Hosting Strategy', {
            'fields': ('hosting_backend', 'system_tokenizer_id'),
            'description': 'Select which internal engine acts as your primary AI host. The tokenizer is always loaded to CPU RAM for local proxy validation.'
        }),
        ('Local PyTorch Inference', {
            'fields': ('active_local_model',),
            'description': 'Settings for loading models directly into VRAM on the inference server.'
        }),
        ('vLLM Integration', {
            'fields': ('active_vllm_model',),
            'description': 'Settings for using the vLLM Docker service as the backend.'
        }),
        ('Ollama Integration', {
            'fields': ('active_ollama_model',),
            'description': 'Settings for using the Ollama Docker service as the backend.'
        }),
    )


admin.site.register(ExternalAIModel)
admin.site.register(UserActiveModel)
admin.site.register(UserAPIKey)

@admin.register(LoRAAdapter)
class LoRAAdapterAdmin(admin.ModelAdmin):
    list_display = ('name', 'base_model', 'dataset', 'currency_status')
    search_fields = ('name', 'description')
    
    @admin.display(description='Currency Status')
    def currency_status(self, obj):
        if obj.is_stale:
            return format_html('<span style="color: orange; font-weight: bold;">⚠️ Stale (Source data updated)</span>')
        return format_html('<span style="color: green; font-weight: bold;">🟢 Up to date</span>')