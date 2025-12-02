from django.contrib import admin
from .models import PromptResponseLog, Conversation
# Register your models here.

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