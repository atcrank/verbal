from django.contrib import admin, messages
from django import forms
from django.urls import reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe
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
    list_display = ('name', 'hf_model_id', 'quantization_mode', 'compute_dtype', 'context_window')
    list_filter = ('quantization_mode', 'compute_dtype')
    search_fields = ('name', 'hf_model_id')
    actions = [activate_local_model, unload_local_models]
    
    fieldsets = (
        ('Model Identification', {
            'fields': ('name', 'hf_model_id', 'description', 'context_window'),
        }),
        ('Precision & Quantization', {
            'fields': ('quantization_mode', 'compute_dtype', 'load_in_4bit'),
            'description': 'Choose the tensor quantization strategy (e.g. 4-bit NF4, 8-bit, or unquantized FP16/BF16).'
        }),
        ('Container & vLLM Overrides', {
            'fields': ('vllm_gpu_memory_utilization', 'vllm_max_model_len'),
            'description': 'Optional custom tuning when served via containerized vLLM. Leave empty for automatic hardware-aware defaults.'
        }),
    )
    
    def change_view(self, request, object_id, form_url='', extra_context=None):
        extra_context = extra_context or {}
        model_instance = self.get_object(request, object_id)
        if model_instance:
            try:
                from huggingface_hub import scan_cache_dir
                hf_cache = scan_cache_dir()
                repo_info = next((r for r in hf_cache.repos if r.repo_id == model_instance.hf_model_id), None)
                if repo_info:
                    size_gb = repo_info.size_on_disk / (1024**3)
                    extra_context['hf_cache_status'] = f"Cached ({size_gb:.2f} GB)"
                    extra_context['is_cached'] = True
                else:
                    extra_context['hf_cache_status'] = "Not Cached"
                    extra_context['is_cached'] = False
            except Exception as e:
                extra_context['hf_cache_status'] = f"Error: {e}"
                extra_context['is_cached'] = False
        return super().change_view(request, object_id, form_url, extra_context=extra_context)

class PromptResponseLogInline(admin.TabularInline):
    model = PromptResponseLog
    extra = 0
    readonly_fields = ('view_link', 'created_at', 'model_name', 'step_status', 'truncated_prompt', 'truncated_response')
    fields = ('view_link', 'created_at', 'model_name', 'step_status', 'truncated_prompt', 'truncated_response')
    can_delete = False
    
    def view_link(self, obj):
        if obj.pk:
            url = reverse("admin:llm_api_promptresponselog_change", args=(obj.pk,))
            return format_html('<a href="{}">View Log</a>', url)
        return ""
    view_link.short_description = "Detail"
    
    def truncated_prompt(self, obj):
        return (obj.user_prompt[:250] + '...') if obj.user_prompt and len(obj.user_prompt) > 250 else obj.user_prompt
    
    def truncated_response(self, obj):
        return (obj.generated_response[:250] + '...') if obj.generated_response and len(obj.generated_response) > 250 else obj.generated_response


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
        return qs.annotate(log_count=Count('logs'))

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
    readonly_fields = ('hardware_advisor_display',)
    fieldsets = (
        ('Host Hardware & Resource Advisor', {
            'fields': ('hardware_advisor_display',),
            'description': 'Real-time detection of host compute capability, VRAM headroom, and recommended settings.'
        }),
        ('Hosting Strategy', {
            'fields': ('hosting_backend', 'system_tokenizer'),
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

    def hardware_advisor_display(self, obj):
        from .hardware import get_hardware_profile, get_backend_recommendations
        profile = get_hardware_profile()
        rec = get_backend_recommendations(profile)

        if profile.cuda_available and profile.primary_device:
            dev = profile.primary_device
            pct = round((dev.free_vram_mb / dev.total_vram_mb) * 100, 1) if dev.total_vram_mb else 0
            bf16_badge = "🟢 Native" if dev.supports_bf16 else "🟡 Emulated / FP16 Preferred"
            fa_badge = "🟢 Supported (Ampere+)" if dev.supports_flash_attention else "⚪ Not Supported"
            advisory_li = "".join(f"<li>{note}</li>" for note in rec.advisory_notes)
            html = f"""
            <div style="background: #0f172a; color: #f8fafc; padding: 16px 20px; border-radius: 8px; font-family: monospace; line-height: 1.6;">
                <div style="font-size: 1.15em; font-weight: bold; margin-bottom: 8px; color: #38bdf8;">
                    🖥️ {dev.name} (Compute Capability: {dev.compute_capability_str})
                </div>
                <div style="margin-bottom: 6px;">
                    <strong>VRAM Availability:</strong> {dev.free_vram_gb} GB Free / {dev.total_vram_gb} GB Total ({pct}% free)
                </div>
                <div style="margin-bottom: 8px;">
                    <strong>Precision Support:</strong> FP16: 🟢 Native &nbsp;|&nbsp; BF16: {bf16_badge} &nbsp;|&nbsp; FlashAttention-2: {fa_badge}
                </div>
                <div style="margin-top: 10px; padding-top: 10px; border-top: 1px solid #334155;">
                    <span style="color: #fbbf24; font-weight: bold;">⚡ Hardware Advisor Recommendations:</span>
                    <ul style="margin: 6px 0 0 16px; padding: 0; color: #cbd5e1;">
                        <li><strong>vLLM Memory Utilization:</strong> {rec.vllm_gpu_memory_utilization} (safely accommodates host display headroom)</li>
                        <li><strong>vLLM Max Sequence:</strong> {rec.vllm_max_model_len} tokens</li>
                        <li><strong>Precision / Dtype:</strong> {rec.vllm_dtype}</li>
                        <li><strong>Quantization:</strong> {rec.recommended_quantization.upper()}</li>
                        {advisory_li}
                    </ul>
                </div>
            </div>
            """
        else:
            html = f"""
            <div style="background: #0f172a; color: #f8fafc; padding: 16px 20px; border-radius: 8px;">
                <div style="font-size: 1.1em; font-weight: bold; color: #f59e0b;">
                    💻 CPU Mode (No CUDA GPU Detected)
                </div>
                <div>Cores: {profile.host_cpu.physical_cores} | Threads: {profile.host_cpu.total_threads} | RAM: {profile.host_cpu.available_ram_gb} GB free / {profile.host_cpu.total_ram_gb} GB total</div>
                <div style="margin-top: 6px; color: #94a3b8;">Recommended backend: Containerized Ollama with GGUF quantization.</div>
            </div>
            """
        return mark_safe(html)

    hardware_advisor_display.short_description = "Host Hardware & VRAM Diagnostics"


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
            return mark_safe('<span style="color: orange; font-weight: bold;">⚠️ Stale (Source data updated)</span>')
        return mark_safe('<span style="color: green; font-weight: bold;">🟢 Up to date</span>')