from django.db import models


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
        max_length=255, blank=True, 
        help_text="Name of a registered model (e.g., 'Hydrant', 'Factor'). Must exist in llm_api.api.OUTPUT_TYPES."
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
    moderation_lists = models.ManyToManyField(ModerationList, blank=True, help_text="Reusable moderation rules applied to all steps in this blueprint.")

    def __str__(self):
        return self.name


class ReasoningStep(models.Model):
    """
    A single node in the cognitive state machine.
    Represents one interaction with the LLM.
    """
    blueprint = models.ForeignKey(CognitiveBlueprint, on_delete=models.CASCADE, related_name="steps")
    name = models.CharField(max_length=255, help_text="e.g., 'Draft Hypothesis', 'Critique Draft'")

    is_start_node = models.BooleanField(default=False,
                                        help_text="Check this if this is the first step in the blueprint.")

    # The core instruction for this specific step
    system_prompt = models.TextField(help_text="The prompt driving this step of the thought process.")

    # The Routing / Graph Edges
    on_success_step = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True,
                                        related_name='success_sources',
                                        help_text="The next step if this step completes successfully.")
    on_failure_step = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True,
                                        related_name='failure_sources',
                                        help_text="The step to route to if this step fails its evaluation criteria.")
    # Constraints & Formatting
    
    output_schema = models.ForeignKey(ResponseSchema, on_delete=models.SET_NULL, null=True, blank=True, help_text="Select a structured output format for this step.")

    # Quality Control
    evaluation_criteria = models.TextField(blank=True,
                                           help_text="Optional: What defines a 'success' for this step? Used by the LLM-as-a-judge to decide whether to follow the success or failure edge.")

    class Meta:
        ordering = ['id']  # Basic ordering, though the actual execution order is defined by the graph edges

    def __str__(self):
        return f"{self.blueprint.name} - {self.name}"