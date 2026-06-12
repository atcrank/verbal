# Register your models here.# llm_api/admin.py
from django.contrib import admin, messages
from django import forms
from .models import LocalAIModel, ExternalAIModel, UserActiveModel, UserAPIKey, SystemConfiguration, PromptResponseLog, Conversation
from .ollama_client import get_available_ollama_models

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

@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ('title', 'user', 'blueprint', 'start_time')
    list_filter = ('start_time', 'blueprint', 'user')
    search_fields = ('title', 'user__username')
    readonly_fields = ('id', 'start_time')
    autocomplete_fields = ('user', 'blueprint')

@admin.register(PromptResponseLog)
class PromptResponseLogAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'conversation', 'user_feedback', 'created_at')
    list_filter = ('user_feedback', 'created_at', 'user')
    search_fields = ('user_prompt', 'generated_response', 'system_prompt', 'conversation__title', 'user__username')
    readonly_fields = ('id', 'created_at')
    autocomplete_fields = ('user', 'conversation')


class SystemConfigurationForm(forms.ModelForm):
    class Meta:
        model = SystemConfiguration
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # 1. Fetch available models from Ollama
        available_models = get_available_ollama_models()

        # 2. Format as Django choices
        choices = [('', '--- None / External Only ---')] + [(m, m) for m in available_models]

        # 3. Apply the dynamic choices to the CharField
        if 'active_ollama_model' in self.fields:
            self.fields['active_ollama_model'].widget = forms.Select(choices=choices)


@admin.register(SystemConfiguration)
class SystemConfigurationAdmin(admin.ModelAdmin):
    form = SystemConfigurationForm


admin.site.register(ExternalAIModel)
admin.site.register(UserActiveModel)
admin.site.register(UserAPIKey)