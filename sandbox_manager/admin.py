import os
import subprocess
from django.contrib import admin, messages
from django.conf import settings
from django.utils.html import format_html
from .models import SandboxConfiguration, SandboxExecutionLog
from .utils import rebuild_sandbox_image


@admin.action(description="Save and Rebuild Docker Sandbox Container")
def trigger_sandbox_rebuild(modeladmin, request, queryset):
    success, output = rebuild_sandbox_image()
    if success:
        modeladmin.message_user(request, "Sandbox rebuilt and restarted successfully!", level=messages.SUCCESS)
    else:
        modeladmin.message_user(request, f"Rebuild failed:\n{output}", level=messages.ERROR)


@admin.register(SandboxConfiguration)
class SandboxConfigurationAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'last_rebuilt', 'execution_timeout')
    actions = [trigger_sandbox_rebuild]
    readonly_fields = ('docker_dashboard', 'workspace_dashboard')
    
    fieldsets = (
        ("Configuration", {
            'fields': ('requirements_txt', 'execution_timeout', 'last_rebuilt')
        }),
        ('System Dashboard', {
            'fields': ('docker_dashboard', 'workspace_dashboard'),
        }),
    )

    def docker_dashboard(self, obj):
        try:
            # Get memory and CPU stats
            stats = subprocess.run(
                ["docker", "stats", "--no-stream", "--format", "Mem: {{.MemUsage}} | CPU: {{.CPUPerc}}", "verbal_sandbox"],
                capture_output=True, text=True
            ).stdout.strip()
            
            # Get container status and start time
            state = subprocess.run(
                ["docker", "inspect", "-f", "{{.State.Status}} (Since: {{.State.StartedAt}})", "verbal_sandbox"],
                capture_output=True, text=True
            ).stdout.strip()
            
            if not state:
                return format_html("<span style='color: red; font-weight: bold;'>Container 'verbal_sandbox' is offline or not created. Rebuild required.</span>")
                
            return format_html(
                "<div style='background: #1e1e1e; color: #00ff00; padding: 10px; border-radius: 5px; font-family: monospace; max-width: 600px;'>"
                "<strong>Status:</strong> {}<br>"
                "<strong>Stats:</strong> {}<br>"
                "</div>",
                state, stats or "N/A"
            )
        except Exception as e:
            return f"Unable to fetch Docker status: {e}"
    docker_dashboard.short_description = "Container Health"

    def workspace_dashboard(self, obj):
        try:
            workspace_dir = os.path.join(settings.BASE_DIR, 'workspaces')
            if not os.path.exists(workspace_dir):
                return "No workspaces folder found."
                
            total_size = 0
            file_count = 0
            for dirpath, _, filenames in os.walk(workspace_dir):
                if '.git' in dirpath:
                    continue # Ignore massive git trees
                for f in filenames:
                    fp = os.path.join(dirpath, f)
                    if not os.path.islink(fp):
                        total_size += os.path.getsize(fp)
                        file_count += 1
                        
            size_mb = total_size / (1024 * 1024)
            return format_html(
                "<div style='padding: 10px; border: 1px solid #ccc; border-radius: 5px; max-width: 600px;'>"
                "<strong>Total Managed Files:</strong> {}<br>"
                "<strong>Total Disk Usage:</strong> {} MB<br>"
                "<strong>Host Mount Path:</strong> <code>{}</code>"
                "</div>",
                file_count, size_mb, workspace_dir
            )
        except Exception as e:
            return f"Unable to fetch Workspace stats: {e}"
    workspace_dashboard.short_description = "Workspace Volume"

    def changelist_view(self, request, extra_context=None):
        # Auto-create the singleton if it doesn't exist so the admin page isn't empty
        SandboxConfiguration.get_solo()
        return super().changelist_view(request, extra_context=extra_context)


@admin.register(SandboxExecutionLog)
class SandboxExecutionLogAdmin(admin.ModelAdmin):
    list_display = ('timestamp', 'filepath', 'return_code', 'is_success')
    list_filter = ('return_code',)
    search_fields = ('filepath', 'stderr', 'stdout', 'conversation_id')
    readonly_fields = ('timestamp', 'conversation_id', 'filepath', 'return_code', 'stdout', 'stderr')

    def is_success(self, obj):
        return obj.return_code == 0

    is_success.boolean = True