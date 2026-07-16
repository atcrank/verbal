# WS2: Documentation — Project Expounder Skill & Architecture Docs

## Goal

Create a reusable "Project Expounder" agent skill that generates and maintains deep explanatory Sphinx/RST documentation for each app in the Verbal project. Also create the initial documentation skeleton and per-app reference files for agent long-term memory.

This workstream produces documentation about the *final* state of the system, so it should be executed **after** WS1, WS3, WS4, and WS5.

## Prerequisites

- **WS1** (Blueprint evolution) should be complete — the documentation must cover the variant resolution design.
- **WS3** (Benchmarking) should be complete — the documentation must cover throughput metrics and long-context evaluation.
- **WS4** (NightManager) should be at least partially complete — document current capabilities.
- **WS5** (Grobid) should be complete — document citation semantics.
- If prerequisites are not yet complete, document the *current* state and mark sections with `.. todo::` directives for later update.

## Key Files

| File | Role |
|------|------|
| `.agents/skills/demo_ui_maintainer/SKILL.md` | Example skill to follow as a structural template |
| `.agents/skills/project_overview_maintainer/SKILL.md` | Related skill — generates high-level overview |
| `documentation/source/` | Sphinx source directory with existing thin RST stubs |
| `documentation/source/conf.py` | Sphinx configuration |
| `documentation/source/index.rst` | Main Sphinx index |
| Per-app RST files (e.g., `metacognition/metacognition_app.rst`) | Existing stubs with achievements/enhancements but no explanatory prose |
| `PROJECT_MAP.md` | High-level project architecture overview |

## Design Decisions (All Resolved)

1. **Format**: Sphinx/RST — the build works and is valued. RST syntax throughout.
2. **Delegation model**: The skill is designed so a Gemini Flash model can complete individual app docs as standalone tasks.
3. **Structure**: Modular — overview + per-app docs, each self-contained.
4. **Code comments**: The skill includes instructions for maintaining design-decision comments directly in source code, not just external docs.
5. **Existing skills**: The `grips_maintainer`, `metacognition_architect`, `celery_task_manager` skills are for *code maintenance*. This skill is for *explanatory documentation*. Reference them but remain distinct.

## Changes Required

### A. Create `.agents/skills/project_expounder/SKILL.md`

```markdown
---
name: project_expounder
description: Use this skill when asked to generate, update, or maintain explanatory documentation for any app in the Verbal project. Produces Sphinx/RST docs and agent-friendly reference files.
---

# Project Expounder Skill

You are a documentation specialist for the Verbal project. Your job is to produce clear, explanatory documentation that covers both *how* things work and *why* they work that way.

## Bounded Context
- **Allowed Scope:** Documentation files in `documentation/source/`, per-app RST files, and reference files in `.agents/skills/project_expounder/references/`.
- **Restricted Scope:** Do NOT modify application code, models, or tests. You MAY add or update docstrings and design-decision comments in code files if they are missing or inaccurate.

## Documentation Template

Each app or conceptual domain document should follow this structure:

1. **Overview** — What this component does in 2-3 sentences.
2. **How It Works** — Technical walkthrough of the key classes, functions, and data flows. Include code references.
3. **Why It Works This Way** — Design decisions, tradeoffs considered, alternatives rejected.
4. **Walkthroughs** — Step-by-step examples for canonical use cases.
5. **Integration Points** — How this component connects to other apps.
6. **Known Limitations** — Current gaps, TODOs, areas for improvement.

## Code Comment Standards

When you find a non-obvious design decision in code that lacks explanation, add a comment like:
```python
# DESIGN: We use stochastic A/B selection here rather than deterministic
# because it enables discovery of good variant combinations that would
# otherwise be masked by always picking the highest-weight option.
```

Prefix design-decision comments with `# DESIGN:` so they are searchable.

## Output Format

- Architecture docs → `documentation/source/architecture/*.rst`
- Per-app updates → update existing `<app>/<app>_app.rst` files in place
- Agent references → `.agents/skills/project_expounder/references/*.md`
- Sphinx index → update `documentation/source/index.rst` to include new files

## Verification

After writing documentation:
1. Verify RST syntax is valid (no broken cross-references).
2. Verify all file paths referenced in docs actually exist.
3. Verify code examples match the current implementation.
```

### B. Create `documentation/source/architecture/` directory with 7 RST files

Each file follows the template from the skill. Here are the files and their key content areas:

#### `blueprint_lifecycle.rst`
- CognitiveBlueprint and ReasoningStep models
- Copy-on-write via `create_variant()` and `is_canonical` lock
- Variant resolution: `resolve_active_steps()` with stochastic A/B selection
- Edge remapping: canonical edges resolved at compile time
- Blueprint lineage tracking via `parent` FK
- Walkthrough: every canonical Blueprint in `metacognition/seed.py` (The Architect, NightManager, Grill Me, Escalation of Effort, ResearchEvaluation, StrategicPlan, Task Decomposer, LintGripsEdge)
- How to create a new Blueprint from the admin

#### `agentic_execution.rst`
- LangGraph compilation from Django models (`compiler.py`)
- Tool calling: native (OpenAI/Ollama) vs XML fallback
- Step execution loop: system prompt injection, evaluation criteria, routing
- Token budget management and summarizer
- Checkpointing via `DjangoCheckpointer`
- The interrupt/resume cycle for user-input-required steps
- LoRA adapter loading per step

#### `rag_pipeline.rst`
- Document upload → chunking → FAISS indexing
- Reading strategies: default, Grobid semantic, prompt-based, regex, abbreviations
- The `RAGService` class and its role in retrieval
- Active RAG (the `<SEARCH:...>` self-guidance loop in `ai_service.py`)
- Integration with benchmarking (temporary isolated RAG environments)

#### `grips_knowledge_graph.rst`
- ConceptNode: narrative + structured claims + domain membership
- KnowledgeEdge: RelationshipTypes taxonomy (DEPENDS_ON, INCLUDES, EXEMPLIFIES, RELATED_TO)
- Automated curation tasks (stub expansion, edge extraction, linting)
- PGVector integration for concept similarity search
- Integration with RAG retrieval (merged query results)

#### `grobid_citation_graph.rst`
- PDF → GROBID → TEI XML → Reference/Citation models
- 3-algorithm extraction cascade (deterministic → heuristic → LLM fallback)
- Semantic chunking from TEI sections
- Citation relationship classification using Grips vocabulary
- Reference resolution for ghost references
- Citation cascade graph in admin

#### `benchmarking_evaluation.rst`
- Investigation → Experiment → BenchmarkRun → BenchmarkResult hierarchy
- ScenarioGroup and BenchmarkScenario: custom and imported
- The `run_benchmark_suite()` pipeline
- Metrics: RAG recall, semantic similarity, faithfulness, relevance, throughput (tok/s)
- Grid search via `create_grid_experiments()` and `generate_comprehensive_matrix()`
- LLM-as-Judge evaluation with parallel sampling
- Long-context evaluation via cumulative token tracking
- Data flywheel: ScenarioGroup → FineTuningDataset → LoRA training

#### `nightmanager_operations.rst`
- NightManager Blueprint walkthrough (6 steps)
- Celery Beat scheduling (03:00 daily)
- Discover pending work: unscored variants, stale datasets, Grips stubs
- Variant scoring: EWMA from PromptResponseLog.step_status
- Autonomous variant creation (is_active=False, pending review)
- Admin review workflow: activate/retire/reject
- Vision: autonomous practice engine that exercises the system

### C. Create `.agents/skills/project_expounder/references/` directory with 4 files

#### `architecture_overview.md`
Summary of all apps, their roles, inter-app dependencies, the multi-role deployment model (web/inference/worker).

#### `testing_principles.md`
- Django TestCase: unit tests for models, services, views
- Metacognition trials: end-to-end Blueprint execution with logged trajectories
- Benchmark experiments: statistical evaluation of model/RAG performance
- When to use each, how they complement each other

#### `coding_conventions.md`
- Canonical lock bypass pattern (`bypass_canonical_lock` context manager)
- Service registry pattern (`service_registry.ai_service`, `service_registry.rag_service`)
- Seed.py patterns: `get_or_create` vs `update_or_create`, bypassing canonical locks
- Log kwargs propagation for telemetry
- Pydantic schema registration via `OUTPUT_TYPES`

#### `blueprint_design_patterns.md`
- Common step topologies: linear pipeline, self-loop (retry), fan-out (parallel), sub-blueprint delegation
- Writing good `system_prompt` text: be specific, reference tools by name, include constraints
- Writing good `evaluation_criteria`: binary pass/fail conditions the LLM-as-judge can assess
- When to use `output_schema` vs free-form text
- Tool assignment: minimal viable toolset per step

### D. Update `documentation/source/index.rst`

Add a new toctree section for the architecture docs:

```rst
.. toctree::
   :maxdepth: 2
   :caption: Architecture & Design

   architecture/blueprint_lifecycle
   architecture/agentic_execution
   architecture/rag_pipeline
   architecture/grips_knowledge_graph
   architecture/grobid_citation_graph
   architecture/benchmarking_evaluation
   architecture/nightmanager_operations
```

## Testing Requirements

1. **RST validity**: All `.rst` files should have valid syntax. If Sphinx is available, run `make html` in the `documentation/` directory and verify no warnings.
2. **File reference accuracy**: All file paths mentioned in docs should exist.
3. **Code accuracy**: Code examples and descriptions should match the current implementation.

## Verification Checklist

- [ ] `project_expounder` skill created with SKILL.md following the demo_ui_maintainer pattern
- [ ] 7 architecture RST files created with substantive content (not stubs)
- [ ] 4 agent reference files created in `references/` directory
- [ ] `index.rst` updated with architecture toctree
- [ ] RST syntax is valid (no broken references)
- [ ] Code examples match current implementation
- [ ] `# DESIGN:` comments added to key code locations where design decisions are non-obvious
