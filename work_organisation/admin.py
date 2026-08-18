from django.contrib import admin
from django.utils.html import format_html
from .models import (
    Project, Workshop, WorkshopSession,
    ConversationMember, WhiteboardCluster, WhiteboardCard
)


class WorkshopInline(admin.TabularInline):
    model = Workshop
    extra = 1
    fields = ('name', 'description', 'objective', 'created_by')
    show_change_link = True


class WorkshopSessionInline(admin.TabularInline):
    model = WorkshopSession
    extra = 1
    fields = ('title', 'session_type', 'access_mode', 'conversation')
    show_change_link = True


class ConversationMemberInline(admin.TabularInline):
    model = ConversationMember
    extra = 1
    fields = ('user', 'role', 'display_alias')


class WhiteboardClusterInline(admin.TabularInline):
    model = WhiteboardCluster
    extra = 0
    fields = ('title', 'color', 'summary')
    show_change_link = True


class WhiteboardCardInline(admin.TabularInline):
    model = WhiteboardCard
    extra = 0
    fields = ('text', 'card_type', 'cluster', 'author', 'author_alias')
    show_change_link = True


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'created_by', 'is_public', 'group_list', 'created_at')
    list_filter = ('is_public', 'created_at')
    search_fields = ('name', 'description', 'slug')
    filter_horizontal = ('groups',)
    prepopulated_fields = {'slug': ('name',)}
    inlines = [WorkshopInline]

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        return qs.for_user(request.user)

    def group_list(self, obj):
        return ", ".join([g.name for g in obj.groups.all()]) or "None (Private)"
    group_list.short_description = "Assigned Groups"

    def save_model(self, request, obj, form, change):
        if not obj.created_by:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(Workshop)
class WorkshopAdmin(admin.ModelAdmin):
    list_display = ('name', 'project', 'created_by', 'session_count', 'created_at')
    list_filter = ('project', 'created_at')
    search_fields = ('name', 'description', 'objective')
    filter_horizontal = ('groups',)
    inlines = [WorkshopSessionInline]

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        return qs.for_user(request.user)

    def session_count(self, obj):
        return obj.sessions.count()
    session_count.short_description = "Active Sessions"

    def save_model(self, request, obj, form, change):
        if not obj.created_by:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(WorkshopSession)
class WorkshopSessionAdmin(admin.ModelAdmin):
    list_display = ('title', 'workshop', 'session_type', 'access_mode', 'card_count', 'created_at')
    list_filter = ('session_type', 'access_mode', 'created_at')
    search_fields = ('title', 'workshop__name')
    inlines = [WhiteboardClusterInline, WhiteboardCardInline]

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        return qs.for_user(request.user)

    def card_count(self, obj):
        return obj.cards.count()
    card_count.short_description = "Cards"


@admin.register(WhiteboardCluster)
class WhiteboardClusterAdmin(admin.ModelAdmin):
    list_display = ('title', 'session', 'color_badge', 'card_count', 'created_at')
    search_fields = ('title', 'summary', 'session__title')

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        return qs.for_user(request.user)

    def color_badge(self, obj):
        return format_html(
            '<span style="background-color: {}; padding: 3px 10px; border-radius: 4px; color: white; font-weight: bold;">{}</span>',
            obj.color,
            obj.color
        )
    color_badge.short_description = "Color"

    def card_count(self, obj):
        return obj.cards.count()
    card_count.short_description = "Cards in Cluster"


@admin.register(WhiteboardCard)
class WhiteboardCardAdmin(admin.ModelAdmin):
    list_display = ('id', 'truncated_text', 'card_type', 'session', 'cluster', 'author_display', 'created_at')
    list_filter = ('card_type', 'created_at')
    search_fields = ('text', 'author_alias', 'session__title')

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        return qs.for_user(request.user)

    def truncated_text(self, obj):
        return obj.text[:50] + ("..." if len(obj.text) > 50 else "")
    truncated_text.short_description = "Content"

    def author_display(self, obj):
        return obj.author_alias or (obj.author.username if obj.author else "Anonymous")
    author_display.short_description = "Author"


@admin.register(ConversationMember)
class ConversationMemberAdmin(admin.ModelAdmin):
    list_display = ('conversation', 'user', 'role', 'display_alias', 'joined_at')
    list_filter = ('role', 'joined_at')
    search_fields = ('user__username', 'display_alias')
