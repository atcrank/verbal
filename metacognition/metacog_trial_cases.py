import logging
logger = logging.getLogger(__name__)

"""
DEPRECATED - THIS FILE NOT IN SERVICE
see folder metacognition_trials for further detail

Metacognitive Trial Cases
=========================

This module contains structured, adversarial scenarios designed to test the boundary 
between the AI's internal cognition and external action (the code sandbox).

The built-in metacognitive steps:
Schema_choices [
('Factor', 'Factor (name, state_options)'),
('RegexCandidate', 'RegexCandidate (reasoning, pattern)'),
('DocumentHandles', 'DocumentHandles (long_form, short_form, keywords)'),
('GlossaryItem', 'GlossaryItem (term, definition)'), ('GlossaryExtraction', 'GlossaryExtraction (items)'),
('ActiveReadingEvaluation', 'ActiveReadingEvaluation (reasoning, context_status, draft_answer)'),
('DifficultPromptEvaluation', 'DifficultPromptEvaluation (reasoning, action, search_queries, clarification_question)'),
('MultiSearchEvaluation', 'MultiSearchEvaluation (reasoning, queries)'),
('ExecutionPlan', 'ExecutionPlan (analysis, queue)')]

Action_choices [
('', '--- No Action Hook ---'),
('handle_active_reading', 'handle_active_reading'),
('handle_difficult_prompt', 'handle_difficult_prompt'),
('handle_multi_search', 'handle_multi_search'),
('handle_execution_plan', 'handle_execution_plan')]

These trials are specifically constructed to fail simple zero-shot generation, forcing 
the LLM to rely on its ExecutionPlan ReAct loop to write, test, and iterate on Python code.

These objects can be imported into automated test suites, benchmarking runners, or 
executed directly in the Django shell for debugging.
"""

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class MetacognitiveTrial:
    """
    A reusable harness for executing and verifying an adversarial agentic trajectory.
    """
    id: str
    name: str
    description: str
    user_prompt: str
    expected_strings: List[str]
    
    def run(self, user_id: Optional[int] = None) -> dict:
        """
        Executes the trial using the backend task runner.
        Requires the Django database to be initialized with the Auto-Generated Blueprints.
        """
        from .models import CognitiveBlueprint
        from .tasks import run_blueprint
        
        logger.info(f"\n{'=' * 60}\n🚀 RUNNING TRIAL: {self.id} - {self.name}\n{'=' * 60}")
        
        # We explicitly target the ExecutionPlan blueprint to force tool usage
        try:
            bp = CognitiveBlueprint.objects.get(name="Workflow: ExecutionPlan")
        except CognitiveBlueprint.DoesNotExist:
            return {"error": "Missing Blueprint. Have you run migrations?"}
            
        result = run_blueprint(
            blueprint_id=bp.id,
            user_prompt=self.user_prompt,
            user_id=user_id
        )
        
        final_response = result.get("final_response", "")
        logger.info(f'\n✅ FINAL RESPONSE:\n{final_response}\n')
        
        if "error" in result:
            logger.info(f"❌ CRITICAL FAILURE: {result['error']}")
            return result
            
        # Simple heuristic validation
        missing = [exp for expected in self.expected_strings for exp in [expected] if exp.lower() not in final_response.lower()]
        if missing:
            logger.info(f'⚠️ WARNING: Missing expected strings in final response: {missing}')
            result["trial_passed"] = False
        else:
            logger.info('🏆 TRIAL PASSED: All expected strings found in output.')
            result["trial_passed"] = True
            
        return result


# ---------------------------------------------------------
# SCENARIO 1.1: The Strawberry Counting Problem
# Tests the LLM's ability to recognize a trick question, write 
# deterministic code to verify the answer, and report back.
# ---------------------------------------------------------
STRAWBERRY_TRIAL = MetacognitiveTrial(
    id="1.1",
    name="Strawberry Character Count",
    description="Forces the LLM to count 'r's using Python rather than hallucinating.",
    user_prompt=(
        "How many 'r's are in the word strawberry? "
        "This is a task that LLMs can incorrectly guess without much effort, but we want to be sure. "
        "Write and execute a Python expression or function that returns a correct count of the "
        "number of times the letter 'r' is used in the word strawberry and tell the user."
    ),
    expected_strings=["3", "three"]
)

# ---------------------------------------------------------
# SCENARIO 1.2: Set Addition
# Tests Python knowledge integration and basic script I/O.
# ---------------------------------------------------------
SET_ADDITION_TRIAL = MetacognitiveTrial(
    id="1.2",
    name="Set Addition Code",
    description="Tests basic code generation, execution, and stdout reading for Python Sets.",
    user_prompt=(
        "How can I add the Python sets {1, 3, 4} and {1, 2}? "
        "Show me the code, run it using the sandbox, and provide the result."
    ),
    expected_strings=["1", "2", "3", "4"] # The resulting union will contain these
)

# ---------------------------------------------------------
# SCENARIO 1.3: Monte Carlo Simulation
# Tests mathematical modeling, use of external libraries (numpy), 
# reasoning about statistical significance, and complex sandbox execution.
# ---------------------------------------------------------
MONTE_CARLO_TRIAL = MetacognitiveTrial(
    id="1.3",
    name="Monte Carlo Coin & Dice",
    description="Forces the LLM to design a statistical simulation and justify its parameters.",
    user_prompt=(
        "If we toss a fair coin to decide whether (HEADS) to roll a D6 (six-sided dice) "
        "or (TAILS) a D20 (20-sided dice), I want to use a simple monte carlo model to compute "
        "the expected average value and the standard deviation for a reasonably informative number of trials - "
        "this is a number you should pick and make an argument for. "
        "Write the simulation script, run it, and tell me the mean and standard deviation."
    ),
    # Expected average is (3.5 + 10.5) / 2 = 7.0. We check for '7' or '7.0' loosely.
    expected_strings=["7."] 
)


ALL_TRIALS = [STRAWBERRY_TRIAL, SET_ADDITION_TRIAL, MONTE_CARLO_TRIAL]

def run_all_trials(user_id: Optional[int] = None):
    """Convenience function to execute the full trial suite."""
    return [trial.run(user_id) for trial in ALL_TRIALS]