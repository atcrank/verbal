import logging
import threading

logger = logging.getLogger(__name__)

_thread_locals = threading.local()

class bypass_canonical_lock:
    """Context manager to bypass the copy-on-write lock during seed.py operations."""
    def __enter__(self):
        _thread_locals.bypass = True
    def __exit__(self, exc_type, exc_val, exc_tb):
        _thread_locals.bypass = False

def is_lock_bypassed():
    return getattr(_thread_locals, 'bypass', False)


from django.db import models
from llm_api.api import OUTPUT_TYPES  as LLM_OUTPUT_TYPES
from background_resources.rag_service import OUTPUT_TYPES as RAG_OUTPUT_TYPES
from .actions import OUTPUT_TYPES as ACTION_OUTPUT_TYPES

OUTPUT_TYPES = LLM_OUTPUT_TYPES | RAG_OUTPUT_TYPES | ACTION_OUTPUT_TYPES

def get_schema_choices():
    """
    Returns a dynamically generated list of choices for the Django admin.
    Using a callable prevents Django from creating new migration files
    every time a schema is added to OUTPUT_TYPES.
    """
    choices = []
    for key, value in OUTPUT_TYPES.items():
        # Safely extract field names handling both Pydantic v1 and v2/Ninja
        fields = getattr(value, 'model_fields', getattr(value, '__fields__', {}))
        field_names = ", ".join(fields.keys())
        display_name = f"{key} ({field_names})"
        # Truncate if there are tons of fields so the dropdown isn't massive
        choices.append((key, display_name[:97] + "..." if len(display_name) > 100 else display_name))
    return choices
logger.info(" ".join([str(x) for x in ['Schema_choices', get_schema_choices()]]))

class ModerationList(models.Model):
    """
    A reusable set of banned concepts/lemmas to be applied across blueprints.
    """
    name = models.CharField(max_length=255)
    concepts = models.TextField(help_text="Comma-separated concepts (lemmas). E.g., 'arson, bomb, explosive'")

    def __str__(self):
        return self.name

class ResponseSchema(models.Model):
    """
    A reusable output schema for LLM generation. 
    Can be either a dynamic JSON schema or a reference to a hardcoded Pydantic model.
    """

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    
    SCHEMA_TYPES = [
        ('json', 'JSON Schema'),
        ('pydantic', 'Hardcoded Pydantic Model'),
    ]
    schema_type = models.CharField(max_length=20, choices=SCHEMA_TYPES, default='json')
    
    json_schema = models.TextField(
        blank=True, 
        help_text='Enter valid JSON Schema. Example for a simple string wrapper:\n{\n  "type": "object",\n  "properties": {\n    "result": {"type": "string"}\n  },\n  "required": ["result"]\n}'
    )
    
    pydantic_model_name = models.CharField(
        max_length=255, blank=True, choices=get_schema_choices,
        help_text="Name of a registered model (e.g. 'Factor'). Sourced from registered OUTPUT_TYPES."
    )

    def __str__(self):
        return self.name

class CognitiveBlueprint(models.Model):
    """
    A container for a specific cognitive strategy or thinking pattern.
    (e.g., 'Causal Experiment Designer', 'Devil's Advocate Critique')
    """
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, help_text="Describes when the Router should select this blueprint.")
    parent = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, 
                                related_name='descendants',
                                help_text="The Blueprint this was cloned from, for lineage tracking.")
    moderation_lists = models.ManyToManyField(ModerationList, blank=True, help_text="Reusable moderation rules applied to all steps in this blueprint.")
    is_autonomous = models.BooleanField(default=False, help_text="If True, self-routing on failure (route_to=SELF) is handled automatically rather than pausing for user input.")
    is_canonical = models.BooleanField(default=False, help_text="If True, this object is maintained by seed.py and cannot be mutated.")

    @property
    def family_success_probability(self):
        from metacognition.compiler import resolve_active_steps
        try:
            resolved, _ = resolve_active_steps(self)
        except Exception:
            return None
        if not resolved:
            return None
        scores = [step.performance_score for step in resolved.values()]
        if all(s == 0.0 for s in scores):
            return None
        prod = 1.0
        for s in scores:
            prod *= s
        return prod

    def save(self, *args, force_canonical_update=False, **kwargs):
        if self.pk and not force_canonical_update and not is_lock_bypassed():
            # Check the original DB state to see if it was ALREADY canonical
            orig = type(self).objects.filter(pk=self.pk).first()
            if orig and orig.is_canonical:
                raise ValueError("Cannot mutate a canonical CognitiveBlueprint. Clone it instead.")
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class ReasoningStepQuerySet(models.QuerySet):
    def active_for_blueprint(self, blueprint) -> dict:
        """
        Returns active steps for a blueprint, grouped by their canonical root lineage.
        Returns: {canonical_root_id: [active_variant, ...]}
        """
        all_steps = list(blueprint.steps.all())
        step_map = {s.id: s for s in all_steps}
        
        def get_root(step):
            curr = step
            seen = set()
            while curr.parent_step_id and curr.parent_step_id not in seen:
                seen.add(curr.id)
                if curr.parent_step_id in step_map:
                    curr = step_map[curr.parent_step_id]
                else:
                    curr = curr.parent_step
            return curr

        groups = {}
        for step in all_steps:
            if step.is_active:
                root = get_root(step)
                groups.setdefault(root.id, []).append(step)
                
        return groups

class ReasoningStepManager(models.Manager):
    def get_queryset(self):
        return ReasoningStepQuerySet(self.model, using=self._db)

    def active_for_blueprint(self, blueprint):
        return self.get_queryset().active_for_blueprint(blueprint)

class ReasoningStep(models.Model):
    """
    A single node in the cognitive state machine graph.
    Represents one interaction or execution step with the LLM or sub-blueprint.

    Graph Topology & Routing Notes:
    - Directed Graph (Not Acyclic): The graph formed by ReasoningStep nodes is a directed graph
      and is NOT strictly acyclic. Edges (on_success_step and on_failure_step) can route
      backwards to any previously visited step, form loops, or self-loop (route_to=SELF).
    - Unconditional Continuation: Routing both on_success_step and on_failure_step to the same
      target step allows unconditional step progress (executing the next step regardless of outcome).
    - Variant Evolution Lineage: When generating new variants of a step, agents should inspect
      the `parent` foreign key to trace prompt history and examine neighbor steps (preceding
      and succeeding nodes) to preserve context continuity and prevent regressing to old wordings.
    """
    blueprint = models.ForeignKey(CognitiveBlueprint, on_delete=models.CASCADE, related_name="steps")
    name = models.CharField(max_length=255, help_text="e.g., 'Draft Hypothesis', 'Critique Draft'")

    is_start_node = models.BooleanField(default=False,
                                        help_text="Check this if this is the first step in the blueprint.")
    
    is_canonical = models.BooleanField(default=False, help_text="If True, this object is maintained by seed.py and cannot be mutated.")
    is_active = models.BooleanField(default=True, help_text="Set to False to soft-delete a bad leaf node variant while retaining it for historical tracking.")
    lora_adapter = models.ForeignKey('llm_api.LoRAAdapter', on_delete=models.SET_NULL, null=True, blank=True, help_text="Optional specialized LoRA weights to load when running this specific step.")

    is_pending_review = models.BooleanField(default=False, 
        help_text="Set by NightManager when proposing a new variant for human review.")
    proposed_by = models.CharField(max_length=20, choices=[('system', 'System'), ('user', 'User')], 
        default='user')
    proposed_at = models.DateTimeField(null=True, blank=True)

    objects = ReasoningStepManager()

    # The core instruction for this specific step
    system_prompt = models.TextField(help_text="The prompt driving this step. For a pre-written plan, instruct the LLM to output a specific sequence of tools in the ExecutionPlan.")

    # The Routing / Graph Edges
    on_success_step = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True,
                                        related_name='success_sources',
                                        help_text="The next step if this step completes successfully.")
    on_failure_step = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True,
                                        related_name='failure_sources',
                                        help_text="The step to route to if this step fails its evaluation criteria.")
                                        
    sub_blueprint = models.ForeignKey(CognitiveBlueprint, on_delete=models.SET_NULL, null=True, blank=True,
                                      related_name='invoked_by_steps',
                                      help_text="Optional: Execute another blueprint as a sub-routine before moving to the next step.")
    parallel_steps = models.ManyToManyField('self', blank=True, symmetrical=False,
                                            related_name='parallel_sources',
                                            help_text="Optional: Execute these steps in parallel (fan-out) upon success.")
    max_retries = models.IntegerField(default=3, help_text="Maximum times this step can loop before forcing a failure.")
    
    available_tools = models.ManyToManyField('ToolDefinition', blank=True, related_name='reasoning_steps',
                                             help_text="Tools available to the LLM during this step.")
    
    # Constraints & Formatting
    
    output_schema = models.ForeignKey(ResponseSchema, on_delete=models.SET_NULL, null=True, blank=True, help_text="Select a structured output format for this step.")
    max_new_tokens = models.IntegerField(default=500, help_text="Maximum tokens to generate for this step. Increase for long-form analysis, decrease for short routing decisions.")

    # Quality Control
    evaluation_criteria = models.TextField(blank=True,
                                           help_text="Optional: What defines a 'success' for this step? Used by the LLM-as-a-judge to decide whether to follow the success or failure edge.")

    # Speciation & Evolutionary Tracking
    parent_step = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='variants',
                                    help_text="The ancestral ReasoningStep this variant evolved from.")
    variant_intent = models.CharField(max_length=255, blank=True,
                                      help_text="What this speciated variant is optimized for (e.g. 'Spatial Execution').")
    performance_score = models.FloatField(default=0.0, help_text="Calculated periodically by the NightManager (e.g. success rate discount).")
    selection_weight = models.FloatField(default=1.0, help_text="Weight used for A/B testing or RL multi-armed bandit variant selection.")

    class Meta:
        ordering = ['id']  # Basic ordering, though the actual execution order is defined by the graph edges

    def save(self, *args, force_canonical_update=False, **kwargs):
        # Automatically inherit canonical status from parent blueprint on creation
        if not self.pk and getattr(self, 'blueprint', None) and self.blueprint.is_canonical:
            self.is_canonical = True
            
        if self.pk and not force_canonical_update and not is_lock_bypassed():
            orig = type(self).objects.filter(pk=self.pk).first()
            if orig and orig.is_canonical:
                raise ValueError("Cannot mutate a canonical ReasoningStep. Call .create_variant() instead.")
        super().save(*args, **kwargs)

    def create_variant(self, variant_intent="", **overrides):
        """Duplicates this step as a child variant, setting parent_step=self."""
        new_step = self.__class__.objects.get(pk=self.pk)
        new_step.pk = None
        new_step.parent_step = self
        new_step.is_canonical = False
        new_step.variant_intent = variant_intent
        new_step.performance_score = 0.0
        
        for key, value in overrides.items():
            setattr(new_step, key, value)
            
        new_step.save()
        
        # Copy M2M relationships (available_tools)
        new_step.available_tools.set(self.available_tools.all())
        
        # Parallel steps M2M
        new_step.parallel_steps.set(self.parallel_steps.all())
        
        return new_step

    def __str__(self):
        return f"{self.blueprint.name} - {self.name}"

from django.contrib.auth.models import User

class ToolDefinition(models.Model):
    """A tool that can be invoked by an agent node."""
    name = models.CharField(max_length=255, unique=True)  # e.g. "search_knowledge"
    description = models.TextField()  # Used in LLM tool descriptions
    
    TOOL_TYPES = [
        ('builtin', 'Built-in Python Function'),
        ('api', 'HTTP API Call'),
        ('blueprint', 'Execute Sub-Blueprint'),
        ('django_action', 'Django ORM Action'),
    ]
    tool_type = models.CharField(max_length=20, choices=TOOL_TYPES)
    
    # For 'builtin': dotted path to the Python function
    # e.g. "metacognition.actions.handle_research"
    python_path = models.CharField(max_length=500, blank=True)
    
    # For 'api': the URL template
    api_url = models.URLField(blank=True)
    
    # For 'blueprint': FK to the sub-blueprint
    sub_blueprint = models.ForeignKey('CognitiveBlueprint', null=True, blank=True, on_delete=models.SET_NULL, related_name="tool_definitions")
    
    # The Pydantic schema for the tool's input parameters (stored as JSON Schema)
    input_schema = models.TextField(blank=True, help_text="JSON Schema for tool parameters")
    
    # The Pydantic schema for the tool's output (stored as JSON Schema)
    output_schema = models.TextField(blank=True, help_text="JSON Schema for tool return value")
    
    # Governance
    is_active = models.BooleanField(default=True)
    requires_approval = models.BooleanField(default=False, help_text="If true, triggers human-in-the-loop before execution")
    is_promoted = models.BooleanField(default=False, help_text="If true, this tool has been promoted to production and is fully available.")
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return f"{self.name} ({self.get_tool_type_display()})"
    
    def get_callable(self):
        """Resolves the Python function from the dotted path."""
        import importlib
        if self.tool_type == 'builtin' and self.python_path:
            module_path, func_name = self.python_path.rsplit('.', 1)
            module = importlib.import_module(module_path)
            return getattr(module, func_name)
        return None

class AgentCheckpoint(models.Model):
    """
    Django implementation of LangGraph BaseCheckpointSaver storage.
    """
    thread_id = models.CharField(max_length=255, db_index=True)
    checkpoint_id = models.CharField(max_length=255, db_index=True)
    parent_id = models.CharField(max_length=255, blank=True, null=True)
    
    # Stored as serialized JSON representations
    state_json = models.JSONField(default=dict)
    metadata_json = models.JSONField(default=dict)
    
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = (('thread_id', 'checkpoint_id'),)
        ordering = ['-created_at']

    def __str__(self):
        return f"Checkpoint {self.checkpoint_id} for Thread {self.thread_id}"