from django.contrib import admin, messages
from django import forms
from django.conf import settings
from django.urls import reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.db.models import Count
from .models import (
    LocalAIModel, ExternalAIModel, UserActiveModel, UserAPIKey,
    SystemConfiguration, PromptResponseLog, Conversation, LoRAAdapter,
    MiniModel, discover_gguf_files,
)

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


class MiniModelForm(forms.ModelForm):
    """
    ModelForm for MiniModel that dynamically scans REASON_MODELS_DIR
    (/srv/reason/models/) for .gguf files and presents a dropdown selector
    when files are present on disk.
    """
    class Meta:
        model = MiniModel
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        models_dir = getattr(settings, "REASON_MODELS_DIR", "/srv/reason/models")
        discovered = discover_gguf_files(models_dir)
        current_val = self.instance.gguf_filename if (self.instance and self.instance.pk) else ""

        if discovered:
            choices = [("", "--- Select a .gguf file from " + models_dir + " ---")]
            for fname in discovered:
                choices.append((fname, fname))
            if current_val and current_val not in discovered:
                choices.append((current_val, f"{current_val} (not currently found in {models_dir})"))
            self.fields["gguf_filename"].widget = forms.Select(choices=choices)
            self.fields["gguf_filename"].help_text = (
                f"Select from the {len(discovered)} .gguf file(s) found in {models_dir}."
            )
        else:
            self.fields["gguf_filename"].help_text = (
                f"No .gguf files currently detected in {models_dir}. "
                "Enter a .gguf filename (e.g. 'Gemma-4-e4B-it-Q5_K_M.gguf') or place .gguf files in that directory to select from a dropdown."
            )


@admin.action(description="Set as Active CPU Mini-Model (llama.cpp)")
def activate_mini_model(modeladmin, request, queryset):
    if queryset.count() != 1:
        modeladmin.message_user(request, "Please select exactly one Mini-Model.", level=messages.WARNING)
        return
    chosen = queryset.first()
    config = SystemConfiguration.get_solo()
    config.active_mini_model = chosen
    config.save()
    modeladmin.message_user(
        request,
        f"Active CPU Mini-Model set to '{chosen.name}' ({chosen.gguf_filename}).",
        level=messages.SUCCESS,
    )


@admin.action(description="Scan /srv/reason/models/ and register any new .gguf files")
def sync_gguf_models_from_disk(modeladmin, request, queryset):
    models_dir = getattr(settings, "REASON_MODELS_DIR", "/srv/reason/models")
    discovered = discover_gguf_files(models_dir)
    if not discovered:
        modeladmin.message_user(
            request,
            f"No .gguf files found in {models_dir}.",
            level=messages.WARNING,
        )
        return
    created_count = 0
    for fname in discovered:
        _, created = MiniModel.objects.get_or_create(gguf_filename=fname)
        if created:
            created_count += 1
    modeladmin.message_user(
        request,
        f"Scanned {models_dir}: found {len(discovered)} .gguf file(s), registered {created_count} new Mini-Model(s).",
        level=messages.SUCCESS,
    )


@admin.register(MiniModel)
class MiniModelAdmin(admin.ModelAdmin):
    form = MiniModelForm
    list_display = ("name", "gguf_filename", "context_window", "threads", "file_status", "is_system_active")
    list_filter = ("is_active",)
    search_fields = ("name", "gguf_filename")
    actions = [activate_mini_model, sync_gguf_models_from_disk]

    fieldsets = (
        ("GGUF Model File", {
            "fields": ("gguf_filename", "name", "is_active"),
            "description": "Select a single-file .gguf model from /srv/reason/models/ (no Hugging Face repo ID required).",
        }),
        ("llama.cpp Runtime Parameters", {
            "fields": ("context_window", "threads"),
            "description": "Configure the context length (-c) and optional CPU thread count (-t) for llama-server.",
        }),
    )

    @admin.display(description="On Disk")
    def file_status(self, obj):
        if obj.file_exists:
            return mark_safe('<span style="color: #16a34a; font-weight: bold;">✓ Found</span>')
        return format_html('<span style="color: #d97706;">⚠ Missing ({})</span>', obj.gguf_path)

    @admin.display(description="Active Mini-Model", boolean=True)
    def is_system_active(self, obj):
        config = SystemConfiguration.get_solo()
        return bool(config and config.active_mini_model_id == obj.pk)


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
        ('Container & vLLM / GGUF Overrides', {
            'fields': ('vllm_gpu_memory_utilization', 'vllm_max_model_len', 'gguf_file_path'),
            'description': 'Optional custom tuning when served via containerized vLLM or local GGUF file path.'
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
    readonly_fields = ('backend_status_display', 'hardware_advisor_display')
    fieldsets = (
        ('Active Runtime Health', {
            'fields': ('backend_status_display',),
            'description': 'Live operational status of the primary inference host and CPU Mini-Model.'
        }),
        ('Hosting Strategy', {
            'fields': ('hosting_backend',),
            'description': 'Select which internal engine acts as your primary AI host.'
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
        ('CPU Mini-Model & llama.cpp (GGUF)', {
            'fields': ('active_mini_model', 'use_mini_model_for_utilities', 'active_llamacpp_model'),
            'description': 'Select a lightweight single-file GGUF CPU Mini-Model (in /srv/reason/models/) served by llama.cpp for non-blocking utility roles (Conversation Title maintenance, fast tokenization) and/or primary CPU inference when Hosting Backend is set to llama.cpp.'
        }),
        ('Advanced / Fallback Tokenizer', {
            'classes': ('collapse',),
            'fields': ('system_tokenizer',),
            'description': 'Legacy Hugging Face tokenizer reference. Only loaded when no CPU Mini-Model is configured.'
        }),
        ('Host Hardware & Resource Advisor', {
            'fields': ('hardware_advisor_display',),
            'description': 'Real-time detection of host compute capability, VRAM headroom, and recommended settings.'
        }),
    )

    def backend_status_display(self, obj):
        if not obj:
            return "—"

        from metacognition.context_processors import is_primary_backend_available_cached, _PRIMARY_HEALTH_CACHE
        from .llamacpp_client import is_llamacpp_available_cached

        backend_name = obj.get_hosting_backend_display()
        is_online = is_primary_backend_available_cached(obj, ttl_seconds=1.0, timeout=0.5)

        if obj.hosting_backend == SystemConfiguration.HostingBackend.PYTORCH:
            if not obj.active_local_model:
                primary_badge = '<span style="color: #94a3b8; font-weight: bold;">⚪ No PyTorch model selected</span>'
            elif is_online:
                primary_badge = f'<span style="color: #10b981; font-weight: bold;">🟢 Online & Loaded:</span> {obj.active_local_model.name} (<code style="color: #a5f3fc;">{obj.active_local_model.hf_model_id}</code>)'
            else:
                err = _PRIMARY_HEALTH_CACHE.get("pytorch_error")
                err_text = err[1] if err else "Inference service unreachable or unstarted"
                primary_badge = f'<span style="color: #ef4444; font-weight: bold;">🔴 Load Error / Offline:</span> {err_text} (Selected: {obj.active_local_model.name})'
        elif obj.hosting_backend == SystemConfiguration.HostingBackend.LLAMACPP:
            mini_obj = obj.active_mini_model or obj.active_llamacpp_model
            mini_name = getattr(mini_obj, "name", "None")
            if is_online:
                primary_badge = f'<span style="color: #10b981; font-weight: bold;">🟢 llama.cpp Online (Port 8080):</span> {mini_name}'
            else:
                primary_badge = '<span style="color: #f59e0b; font-weight: bold;">🟡 llama.cpp Offline / Unstarted (Port 8080)</span>'
        elif obj.hosting_backend == SystemConfiguration.HostingBackend.VLLM:
            mod_name = obj.active_vllm_model.name if obj.active_vllm_model else "None"
            primary_badge = f'<span style="color: {"#10b981" if is_online else "#f59e0b"}; font-weight: bold;">{"🟢 vLLM Online" if is_online else "🟡 vLLM Offline"}</span> ({mod_name})'
        elif obj.hosting_backend == SystemConfiguration.HostingBackend.OLLAMA:
            mod_name = obj.active_ollama_model.name if obj.active_ollama_model else "None"
            primary_badge = f'<span style="color: {"#10b981" if is_online else "#f59e0b"}; font-weight: bold;">{"🟢 Ollama Online" if is_online else "🟡 Ollama Offline"}</span> ({mod_name})'
        else:
            primary_badge = f'<span style="color: #94a3b8;">{backend_name}</span>'

        # CPU Mini-Model Status
        mini_online = is_llamacpp_available_cached(ttl_seconds=1.0, timeout=0.5)
        if obj.active_mini_model:
            if mini_online:
                mini_badge = f'<span style="color: #10b981; font-weight: bold;">🟢 Online:</span> {obj.active_mini_model.name} (<code style="color: #a5f3fc;">{obj.active_mini_model.gguf_filename}</code>)'
            else:
                mini_badge = f'<span style="color: #f59e0b; font-weight: bold;">🟡 Configured but llama.cpp is offline / unstarted</span> ({obj.active_mini_model.name})'
        else:
            mini_badge = '<span style="color: #94a3b8; font-weight: bold;">⚪ No CPU Mini-Model selected (utility acceleration disabled)</span>'

        html = f"""
        <div style="background: #0f172a; color: #f8fafc; padding: 14px 18px; border-radius: 8px; font-family: monospace; line-height: 1.6; border: 1px solid #334155;">
            <div style="font-size: 1.1em; font-weight: bold; margin-bottom: 8px; color: #38bdf8;">
                📡 Active Runtime Backend & Model Health
            </div>
            <div style="margin-bottom: 6px;">
                <strong>Primary Host ({backend_name}):</strong> {primary_badge}
            </div>
            <div>
                <strong>CPU Mini-Model (llama.cpp):</strong> {mini_badge}
            </div>
        </div>
        """
        return mark_safe(html)

    backend_status_display.short_description = "Live Backend & Model Status"

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