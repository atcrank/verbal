from django.db import models


class CognitiveBlueprint(models.Model):
    """
    A container for a specific cognitive strategy or thinking pattern.
    (e.g., 'Causal Experiment Designer', 'Devil's Advocate Critique')
    """
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, help_text="Describes when the Router should select this blueprint.")

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

    # Quality Control
    evaluation_criteria = models.TextField(blank=True,
                                           help_text="Optional: What defines a 'success' for this step? Used by the LLM-as-a-judge to decide whether to follow the success or failure edge.")

    class Meta:
        ordering = ['id']  # Basic ordering, though the actual execution order is defined by the graph edges

    def __str__(self):
        return f"{self.blueprint.name} - {self.name}"