import uuid
from django.db import models
from django.db.models import Q
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.utils.text import slugify

User = get_user_model()


class GroupScopedQuerySet(models.QuerySet):
    """
    QuerySet mixin providing automatic group and user scoping.
    """
    def for_user(self, user):
        if not user or user.is_anonymous:
            # Only public projects/sessions for anonymous users
            if hasattr(self.model, 'is_public'):
                return self.filter(is_public=True)
            elif hasattr(self.model, 'access_mode'):
                return self.filter(access_mode='PUBLIC_OPTIONAL_USER')
            elif hasattr(self.model, 'workshop'):
                return self.filter(
                    Q(workshop__project__is_public=True) |
                    Q(access_mode='PUBLIC_OPTIONAL_USER')
                )
            elif hasattr(self.model, 'project'):
                return self.filter(project__is_public=True)
            elif hasattr(self.model, 'session'):
                return self.filter(
                    Q(session__workshop__project__is_public=True) |
                    Q(session__access_mode='PUBLIC_OPTIONAL_USER')
                )
            return self.none()

        if getattr(user, 'is_superuser', False):
            return self.all()

        user_groups = user.groups.all()

        if hasattr(self.model, 'groups') and hasattr(self.model, 'is_public'):
            # Model is Project
            return self.filter(
                Q(created_by=user) |
                Q(groups__in=user_groups) |
                Q(is_public=True)
            ).distinct()

        if hasattr(self.model, 'project') and hasattr(self.model, 'groups'):
            # Model is Workshop
            return self.filter(
                Q(created_by=user) |
                Q(groups__in=user_groups) |
                Q(project__created_by=user) |
                Q(project__groups__in=user_groups) |
                Q(project__is_public=True)
            ).distinct()

        if hasattr(self.model, 'workshop') and hasattr(self.model, 'access_mode'):
            # Model is WorkshopSession
            return self.filter(
                Q(workshop__created_by=user) |
                Q(workshop__groups__in=user_groups) |
                Q(workshop__project__created_by=user) |
                Q(workshop__project__groups__in=user_groups) |
                Q(workshop__project__is_public=True) |
                Q(access_mode='PUBLIC_OPTIONAL_USER')
            ).distinct()

        if hasattr(self.model, 'session'):
            # Model is WhiteboardCard or WhiteboardCluster
            return self.filter(
                Q(session__workshop__created_by=user) |
                Q(session__workshop__groups__in=user_groups) |
                Q(session__workshop__project__created_by=user) |
                Q(session__workshop__project__groups__in=user_groups) |
                Q(session__workshop__project__is_public=True) |
                Q(session__access_mode='PUBLIC_OPTIONAL_USER')
            ).distinct()

        return self.all()


class GroupScopedManager(models.Manager.from_queryset(GroupScopedQuerySet)):
    pass


class Project(models.Model):
    """
    Top-level organizational container for workshops, whiteboards, and experiments.
    """
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True)
    description = models.TextField(blank=True)
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="created_projects")
    groups = models.ManyToManyField(Group, blank=True, related_name="projects", help_text="Django Groups with access to this project.")
    is_public = models.BooleanField(default=False, help_text="If True, visible to all users.")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = GroupScopedManager()

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.name) or f"project-{uuid.uuid4().hex[:8]}"
            slug = base
            counter = 1
            while Project.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f"{base}-{counter}"
                counter += 1
            self.slug = slug
        super().save(*args, **kwargs)


class Workshop(models.Model):
    """
    A collaborative workshop or task within a Project.
    """
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="workshops")
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    objective = models.TextField(blank=True, help_text="High-level goal of this workshop.")
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="created_workshops")
    groups = models.ManyToManyField(Group, blank=True, related_name="workshops", help_text="Optional sub-group scoping. Inherits project groups if empty.")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = GroupScopedManager()

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.project.name} > {self.name}"


class WorkshopSession(models.Model):
    """
    An active collaborative workspace or session (whiteboard, interview, drafting).
    """
    SESSION_TYPE_CHOICES = [
        ('whiteboard', 'Whiteboard / Ideation'),
        ('conversation', 'Dialogue / Interview'),
        ('document', 'Collaborative Drafting'),
    ]

    ACCESS_MODE_CHOICES = [
        ('RESTRICTED_TRACKED', 'Restricted & Tracked'),
        ('RESTRICTED_ANONYMIZED_UI', 'Restricted & Anonymized in UI'),
        ('RESTRICTED_ANONYMIZED_DB', 'Restricted & Anonymized in DB'),
        ('PUBLIC_OPTIONAL_USER', 'Public & Optional User'),
    ]

    workshop = models.ForeignKey(Workshop, on_delete=models.CASCADE, related_name="sessions")
    title = models.CharField(max_length=255)
    session_type = models.CharField(max_length=32, choices=SESSION_TYPE_CHOICES, default='whiteboard')
    access_mode = models.CharField(max_length=32, choices=ACCESS_MODE_CHOICES, default='RESTRICTED_TRACKED')
    conversation = models.OneToOneField('llm_api.Conversation', null=True, blank=True, on_delete=models.SET_NULL, related_name="workshop_session")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = GroupScopedManager()

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.workshop.name} > {self.title} ({self.get_access_mode_display()})"


class ConversationMember(models.Model):
    """
    Tracks multi-user participation and roles in collaborative conversations and sessions.
    """
    ROLE_CHOICES = [
        ('owner', 'Owner'),
        ('editor', 'Editor'),
        ('viewer', 'Viewer'),
    ]

    conversation = models.ForeignKey('llm_api.Conversation', on_delete=models.CASCADE, related_name="members")
    user = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="conversation_memberships")
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='editor')
    display_alias = models.CharField(max_length=100, blank=True, help_text="Pseudonym used when session is anonymized in UI.")
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [('conversation', 'user')]
        ordering = ['joined_at']

    def __str__(self):
        u = self.user.username if self.user else "Anonymous"
        return f"{u} ({self.role}) on Conv {self.conversation_id}"


class WhiteboardCluster(models.Model):
    """
    A bounding thematic group of WhiteboardCards synthesized by LLM or user.
    """
    session = models.ForeignKey(WorkshopSession, on_delete=models.CASCADE, related_name="clusters")
    title = models.CharField(max_length=255)
    summary = models.TextField(blank=True)
    color = models.CharField(max_length=30, default="#3B82F6")
    pos_x = models.FloatField(default=0.0)
    pos_y = models.FloatField(default=0.0)
    width = models.FloatField(default=320.0)
    height = models.FloatField(default=220.0)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = GroupScopedManager()

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f"Cluster '{self.title}' ({self.session.title})"


class WhiteboardCard(models.Model):
    """
    A sticky note, causal factor, concept, or hypothesis card on the collaborative canvas.
    """
    CARD_TYPE_CHOICES = [
        ('idea', 'Sticky Note / Idea'),
        ('factor', 'Causal Factor'),
        ('concept', 'Grips Concept'),
        ('question', 'Open Question'),
        ('hypothesis', 'Working Hypothesis'),
    ]

    session = models.ForeignKey(WorkshopSession, on_delete=models.CASCADE, related_name="cards")
    text = models.TextField()
    card_type = models.CharField(max_length=32, choices=CARD_TYPE_CHOICES, default='idea')
    pos_x = models.FloatField(default=0.0)
    pos_y = models.FloatField(default=0.0)
    cluster = models.ForeignKey(WhiteboardCluster, null=True, blank=True, on_delete=models.SET_NULL, related_name="cards")
    author = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="whiteboard_cards")
    author_alias = models.CharField(max_length=100, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = GroupScopedManager()

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        alias = self.author_alias or (self.author.username if self.author else "Anon")
        return f"Card [{self.card_type}] '{self.text[:30]}' by {alias}"
