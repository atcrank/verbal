import json
from datetime import timedelta
from django.contrib import admin, messages
from django.db import models
from django.db.models import Sum, Avg, Count, Q
from django.http import JsonResponse, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from .models import TaskRecord, TaskRecordStatus, ScheduledTask


class HasErrorFilter(admin.SimpleListFilter):
    title = 'Has Error'
    parameter_name = 'has_error'

    def lookups(self, request, model_admin):
        return [
            ('yes', 'Has Error / Traceback'),
            ('no', 'No Error'),
        ]

    def queryset(self, request, queryset):
        if self.value() == 'yes':
            return queryset.filter(error_traceback__isnull=False).exclude(error_traceback="")
        if self.value() == 'no':
            return queryset.filter(Q(error_traceback__isnull=True) | Q(error_traceback=""))
        return queryset


class HasTokensFilter(admin.SimpleListFilter):
    title = 'LLM Token Usage'
    parameter_name = 'has_tokens'

    def lookups(self, request, model_admin):
        return [
            ('yes', 'Consumed Tokens (> 0)'),
            ('no', 'Zero Tokens'),
        ]

    def queryset(self, request, queryset):
        if self.value() == 'yes':
            return queryset.filter(total_tokens__gt=0)
        if self.value() == 'no':
            return queryset.filter(total_tokens=0)
        return queryset


class AppTaskFilter(admin.SimpleListFilter):
    title = 'Application Domain'
    parameter_name = 'app_domain'

    def lookups(self, request, model_admin):
        return [
            ('metacognition', 'Metacognition'),
            ('grips', 'GRIPS (Knowledge Graph)'),
            ('background_resources', 'Background Resources (RAG/NLP)'),
            ('llm_api', 'LLM API'),
            ('benchmarking', 'Benchmarking'),
            ('grobid_client', 'Grobid Client'),
            ('verbal_tasks', 'Tasks / System Maintenance'),
        ]

    def queryset(self, request, queryset):
        val = self.value()
        if val:
            return queryset.filter(task_path__startswith=f"{val}.")
        return queryset


@admin.register(TaskRecord)
class TaskRecordAdmin(admin.ModelAdmin):
    change_form_template = "admin/verbal_tasks/taskrecord_change_form.html"
    list_display = (
        'short_task_name_display',
        'status_badge',
        'queue_name',
        'priority',
        'duration_display',
        'tokens_display',
        'enqueued_at',
        'worker_id',
    )
    list_filter = (
        'status',
        'queue_name',
        AppTaskFilter,
        HasTokensFilter,
        HasErrorFilter,
        'enqueued_at',
    )
    search_fields = ('id', 'task_path', 'worker_id', 'args_json', 'kwargs_json', 'error_traceback')
    readonly_fields = (
        'id',
        'task_path',
        'queue_name',
        'priority',
        'status_badge',
        'enqueued_at',
        'started_at',
        'last_attempted_at',
        'finished_at',
        'duration_display',
        'queue_latency_display',
        'tokens_display',
        'worker_id',
        'attempts',
        'formatted_args',
        'formatted_kwargs',
        'formatted_return_value',
        'formatted_traceback',
        'formatted_metrics',
    )
    fieldsets = (
        ('Overview & Status', {
            'fields': (
                'id',
                'task_path',
                'status_badge',
                'queue_name',
                'priority',
                'worker_id',
                'attempts',
            )
        }),
        ('Performance & Telemetry', {
            'fields': (
                'duration_display',
                'queue_latency_display',
                'tokens_display',
                'formatted_metrics',
            )
        }),
        ('Timing Timestamps', {
            'fields': (
                'enqueued_at',
                'started_at',
                'last_attempted_at',
                'finished_at',
            )
        }),
        ('Arguments & Inputs', {
            'fields': (
                'formatted_args',
                'formatted_kwargs',
            )
        }),
        ('Execution Result & Traceback', {
            'fields': (
                'formatted_return_value',
                'formatted_traceback',
            )
        }),
    )
    actions = ['retry_selected_tasks', 'purge_completed_tasks']

    def has_add_permission(self, request):
        return False

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('dashboard/', self.admin_site.admin_view(self.dashboard_view), name="verbal_tasks_dashboard"),
            path('dashboard/api/stats/', self.admin_site.admin_view(self.dashboard_stats_api), name="verbal_tasks_dashboard_stats"),
            path('dashboard/api/retry/<str:task_id>/', self.admin_site.admin_view(self.dashboard_retry_task), name="verbal_tasks_dashboard_retry"),
        ]
        return custom_urls + urls

    # --- LIST / DETAIL FORMATTING HELPERS ---

    def short_task_name_display(self, obj):
        url = reverse("admin:verbal_tasks_taskrecord_change", args=[obj.id])
        return format_html('<a href="{}" style="font-weight: 600; font-family: monospace;">{}</a>', url, obj.short_task_name)
    short_task_name_display.short_description = "Task Name"
    short_task_name_display.admin_order_field = "task_path"

    def status_badge(self, obj):
        colors = {
            TaskRecordStatus.READY: ("#2563eb", "#dbeafe"),        # Blue
            TaskRecordStatus.RUNNING: ("#d97706", "#fef3c7"),      # Amber
            TaskRecordStatus.SUCCESSFUL: ("#16a34a", "#dcfce7"),   # Green
            TaskRecordStatus.FAILED: ("#dc2626", "#fee2e2"),       # Red
        }
        fg, bg = colors.get(obj.status, ("#4b5563", "#f3f4f6"))
        pulse = "animation: pulse 2s cubic-bezier(0.4, 0, 0.6, 1) infinite;" if obj.status == TaskRecordStatus.RUNNING else ""
        return format_html(
            '<span style="background-color: {}; color: {}; font-weight: 700; font-size: 0.75rem; '
            'padding: 3px 9px; border-radius: 9999px; text-transform: uppercase; letter-spacing: 0.05em; display: inline-block; {}">'
            '{}</span>',
            bg, fg, pulse, obj.status
        )
    status_badge.short_description = "Status"
    status_badge.admin_order_field = "status"

    def duration_display(self, obj):
        if obj.duration_ms is not None:
            ms = obj.duration_ms
            if ms > 30000:
                color = "#dc2626"  # Red for very slow
            elif ms > 5000:
                color = "#d97706"  # Amber
            else:
                color = "#16a34a"  # Green
            return format_html('<span style="color: {}; font-weight: 600;">{}</span>', color, obj.duration_formatted)
        return obj.duration_formatted
    duration_display.short_description = "Duration"
    duration_display.admin_order_field = "duration_ms"

    def queue_latency_display(self, obj):
        latency = obj.queue_latency_formatted
        return latency
    queue_latency_display.short_description = "Queue Latency"

    def tokens_display(self, obj):
        if not obj.total_tokens:
            return mark_safe('<span style="color: #9ca3af;">0</span>')
        return format_html(
            '<span title="Input: {} | Output: {}" style="font-weight: 600; color: #4338ca;">'
            '{} <span style="font-size: 0.75em; color: #6b7280; font-weight: normal;">(In: {} / Out: {})</span></span>',
            f"{obj.input_tokens:,}", f"{obj.output_tokens:,}",
            f"{obj.total_tokens:,}",
            f"{obj.input_tokens:,}", f"{obj.output_tokens:,}"
        )
    tokens_display.short_description = "Tokens (In/Out)"
    tokens_display.admin_order_field = "total_tokens"

    def formatted_args(self, obj):
        return format_html('<pre style="background: #f8fafc; padding: 10px; border-radius: 6px; border: 1px solid #e2e8f0; max-height: 250px; overflow: auto;">{}</pre>', json.dumps(obj.args_json, indent=2))
    formatted_args.short_description = "Positional Arguments"

    def formatted_kwargs(self, obj):
        return format_html('<pre style="background: #f8fafc; padding: 10px; border-radius: 6px; border: 1px solid #e2e8f0; max-height: 250px; overflow: auto;">{}</pre>', json.dumps(obj.kwargs_json, indent=2))
    formatted_kwargs.short_description = "Keyword Arguments"

    def formatted_return_value(self, obj):
        if obj.return_value_json is None:
            return "No return value."
        return format_html('<pre style="background: #f0fdf4; color: #166534; padding: 10px; border-radius: 6px; border: 1px solid #bbf7d0; max-height: 350px; overflow: auto;">{}</pre>', json.dumps(obj.return_value_json, indent=2))
    formatted_return_value.short_description = "Return Value"

    def formatted_traceback(self, obj):
        if not obj.error_traceback:
            return "None (Task completed without unhandled exceptions)."
        return format_html('<pre style="background: #1e1e1e; color: #f87171; padding: 12px; border-radius: 6px; font-family: monospace; font-size: 0.85rem; max-height: 450px; overflow: auto; white-space: pre-wrap;">{}</pre>', obj.error_traceback)
    formatted_traceback.short_description = "Error Traceback"

    def formatted_metrics(self, obj):
        if not obj.metrics_json:
            return "No additional telemetry metrics recorded."
        return format_html('<pre style="background: #f8fafc; padding: 10px; border-radius: 6px; border: 1px solid #e2e8f0; max-height: 250px; overflow: auto;">{}</pre>', json.dumps(obj.metrics_json, indent=2))
    formatted_metrics.short_description = "Telemetry Details"

    # --- ADMIN ACTIONS ---

    @admin.action(description="🔄 Re-enqueue / Retry selected tasks")
    def retry_selected_tasks(self, request, queryset):
        requeued = 0
        from django.utils.module_loading import import_string
        for record in queryset:
            try:
                task_func = import_string(record.task_path)
                task_func.enqueue(*record.args_json, **record.kwargs_json)
                requeued += 1
            except Exception as e:
                self.message_user(request, f"Failed to re-enqueue task {record.id}: {e}", level=messages.ERROR)
        if requeued:
            self.message_user(request, f"Successfully re-enqueued {requeued} task(s) into queue.", level=messages.SUCCESS)

    @admin.action(description="🗑️ Purge selected completed tasks (clean DB)")
    def purge_completed_tasks(self, request, queryset):
        deletable = queryset.filter(status__in=[TaskRecordStatus.SUCCESSFUL, TaskRecordStatus.FAILED])
        count = deletable.count()
        deletable.delete()
        self.message_user(request, f"Purged {count} completed task records from database.", level=messages.SUCCESS)

    # --- DASHBOARD & API VIEWS ---

    def dashboard_view(self, request):
        context = dict(
            self.admin_site.each_context(request),
            title="Task Queue & Worker Monitoring Dashboard",
        )
        return TemplateResponse(request, "admin/verbal_tasks/dashboard.html", context)

    def dashboard_stats_api(self, request):
        """Returns JSON metrics for dashboard real-time polling."""
        now = timezone.now()
        last_24h = now - timedelta(hours=24)

        # 1. High-level counts
        status_counts = dict(TaskRecord.objects.values_list('status').annotate(c=Count('id')))
        ready_count = status_counts.get(TaskRecordStatus.READY, 0)
        running_count = status_counts.get(TaskRecordStatus.RUNNING, 0)
        successful_count = status_counts.get(TaskRecordStatus.SUCCESSFUL, 0)
        failed_count = status_counts.get(TaskRecordStatus.FAILED, 0)

        # 2. 24h Window metrics
        tasks_24h = TaskRecord.objects.filter(enqueued_at__gte=last_24h)
        successful_24h = tasks_24h.filter(status=TaskRecordStatus.SUCCESSFUL).count()
        failed_24h = tasks_24h.filter(status=TaskRecordStatus.FAILED).count()
        total_24h = successful_24h + failed_24h
        success_rate_24h = round((successful_24h / total_24h * 100), 1) if total_24h > 0 else 100.0

        # 3. Token Analytics
        tokens_24h_agg = tasks_24h.aggregate(
            in_tokens=Sum('input_tokens'),
            out_tokens=Sum('output_tokens'),
            tot_tokens=Sum('total_tokens'),
            avg_duration=Avg('duration_ms')
        )
        total_tokens_24h = tokens_24h_agg['tot_tokens'] or 0
        total_in_24h = tokens_24h_agg['in_tokens'] or 0
        total_out_24h = tokens_24h_agg['out_tokens'] or 0
        avg_duration_24h = round(tokens_24h_agg['avg_duration'] or 0, 1)

        total_tokens_all_time = TaskRecord.objects.aggregate(t=Sum('total_tokens'))['t'] or 0

        # 4. Token Consumption Leaderboard by Task Type
        token_breakdown = list(
            TaskRecord.objects.filter(total_tokens__gt=0)
            .values('task_path')
            .annotate(
                tokens=Sum('total_tokens'),
                task_count=Count('id'),
                avg_dur=Avg('duration_ms')
            )
            .order_by('-tokens')[:8]
        )
        for item in token_breakdown:
            parts = item['task_path'].split('.')
            item['short_name'] = f"{parts[-2]}.{parts[-1]}" if len(parts) >= 2 else item['task_path']
            item['avg_dur_formatted'] = f"{round((item['avg_dur'] or 0) / 1000, 2)}s" if (item['avg_dur'] or 0) >= 1000 else f"{int(item['avg_dur'] or 0)}ms"

        # 5. Running Tasks
        running_records = TaskRecord.objects.filter(status=TaskRecordStatus.RUNNING).order_by('-started_at')[:10]
        running_list = []
        for r in running_records:
            elapsed_sec = (now - r.started_at).total_seconds() if r.started_at else 0
            running_list.append({
                "id": r.id,
                "task_name": r.short_task_name,
                "queue": r.queue_name,
                "worker_id": r.worker_id or "worker-default",
                "started_at": r.started_at.strftime("%H:%M:%S") if r.started_at else "-",
                "elapsed_seconds": round(elapsed_sec, 1),
            })

        # 6. Recent Task Stream
        recent_records = TaskRecord.objects.all().order_by('-enqueued_at')[:25]
        recent_list = []
        for r in recent_records:
            recent_list.append({
                "id": r.id,
                "task_name": r.short_task_name,
                "status": r.status,
                "queue": r.queue_name,
                "priority": r.priority,
                "duration": r.duration_formatted,
                "total_tokens": r.total_tokens,
                "input_tokens": r.input_tokens,
                "output_tokens": r.output_tokens,
                "enqueued_at": r.enqueued_at.strftime("%H:%M:%S"),
                "worker_id": r.worker_id or "-",
                "has_error": bool(r.error_traceback),
                "error_snippet": (r.error_traceback or "").strip().splitlines()[-1] if r.error_traceback else "",
                "admin_url": reverse("admin:verbal_tasks_taskrecord_change", args=[r.id]),
            })

        # 7. Scheduled Tasks
        scheduled_records = ScheduledTask.objects.all().order_by('name')
        scheduled_list = []
        for s in scheduled_records:
            scheduled_list.append({
                "id": s.id,
                "name": s.name,
                "task_name": s.task_name.split('.')[-1],
                "schedule": s.cron_expression or f"every {s.interval_seconds}s",
                "is_active": s.is_active,
                "last_run_at": s.last_run_at.strftime("%Y-%m-%d %H:%M:%S") if s.last_run_at else "Never",
                "total_run_count": s.total_run_count,
                "last_status": s.last_task_record.status if s.last_task_record else "NONE",
            })

        data = {
            "summary": {
                "ready_count": ready_count,
                "running_count": running_count,
                "successful_count": successful_count,
                "failed_count": failed_count,
                "successful_24h": successful_24h,
                "failed_24h": failed_24h,
                "success_rate_24h": success_rate_24h,
                "total_tokens_24h": total_tokens_24h,
                "total_in_24h": total_in_24h,
                "total_out_24h": total_out_24h,
                "total_tokens_all_time": total_tokens_all_time,
                "avg_duration_24h": avg_duration_24h,
            },
            "token_breakdown": token_breakdown,
            "running_tasks": running_list,
            "recent_tasks": recent_list,
            "scheduled_tasks": scheduled_list,
            "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
        }
        return JsonResponse(data)

    def dashboard_retry_task(self, request, task_id):
        """AJAX endpoint to retry a specific failed task directly from the dashboard."""
        if request.method != "POST":
            return JsonResponse({"error": "POST required"}, status=405)
        try:
            record = TaskRecord.objects.get(id=task_id)
            from django.utils.module_loading import import_string
            task_func = import_string(record.task_path)
            new_res = task_func.enqueue(*record.args_json, **record.kwargs_json)
            return JsonResponse({"success": True, "new_task_id": new_res.id})
        except TaskRecord.DoesNotExist:
            return JsonResponse({"error": "Task not found"}, status=404)
        except Exception as e:
            return JsonResponse({"error": str(e)}, status=500)


@admin.register(ScheduledTask)
class ScheduledTaskAdmin(admin.ModelAdmin):
    list_display = (
        'name',
        'task_name_display',
        'schedule_display',
        'is_active',
        'last_run_at',
        'total_run_count',
        'last_run_status_display',
    )
    list_filter = ('is_active', 'queue_name')
    search_fields = ('name', 'task_name')
    actions = ['trigger_now_action', 'activate_tasks_action', 'deactivate_tasks_action']

    def task_name_display(self, obj):
        parts = obj.task_name.split('.')
        return parts[-1] if parts else obj.task_name
    task_name_display.short_description = "Task Target"

    def schedule_display(self, obj):
        if obj.cron_expression:
            return format_html('<span style="font-family: monospace; font-weight: 600; color: #4338ca;">cron: {}</span>', obj.cron_expression)
        elif obj.interval_seconds:
            return format_html('<span style="color: #0d9488;">every {}s</span>', obj.interval_seconds)
        return "Not scheduled"
    schedule_display.short_description = "Schedule"

    def last_run_status_display(self, obj):
        if not obj.last_task_record:
            return mark_safe('<span style="color: #9ca3af;">Never Run</span>')
        record = obj.last_task_record
        url = reverse("admin:verbal_tasks_taskrecord_change", args=[record.id])
        colors = {
            TaskRecordStatus.SUCCESSFUL: "#16a34a",
            TaskRecordStatus.FAILED: "#dc2626",
            TaskRecordStatus.RUNNING: "#d97706",
            TaskRecordStatus.READY: "#2563eb",
        }
        color = colors.get(record.status, "#6b7280")
        return format_html(
            '<a href="{}" style="color: {}; font-weight: bold;">{}</a>',
            url, color, record.status
        )
    last_run_status_display.short_description = "Last Status"

    @admin.action(description="⚡ Trigger Immediately (Run Now)")
    def trigger_now_action(self, request, queryset):
        triggered = 0
        for schedule in queryset:
            try:
                record = schedule.trigger()
                triggered += 1
            except Exception as e:
                self.message_user(request, f"Failed to trigger {schedule.name}: {e}", level=messages.ERROR)
        if triggered:
            self.message_user(request, f"Successfully triggered {triggered} scheduled task(s).", level=messages.SUCCESS)

    @admin.action(description="✅ Enable selected schedules")
    def activate_tasks_action(self, request, queryset):
        count = queryset.update(is_active=True)
        self.message_user(request, f"Enabled {count} scheduled task(s).", level=messages.SUCCESS)

    @admin.action(description="⏸️ Disable selected schedules")
    def deactivate_tasks_action(self, request, queryset):
        count = queryset.update(is_active=False)
        self.message_user(request, f"Disabled {count} scheduled task(s).", level=messages.SUCCESS)
