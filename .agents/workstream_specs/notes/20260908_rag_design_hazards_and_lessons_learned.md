# Note: RAG Design History, Hazards & Lessons Learned

## Timestamp
2026-09-08T14:15:00+10:00

## User says
"the glossary leapfrogging was for token efficiency, but it is certainly a problem if it matches on any word match rather than important and unusual words or terms. Token efficiency also motivated the penalization of long sections; perhaps the solutions we adopted then need to be revised in this experience.
The motivation for reducing large blocks of RAG material was that the smaller models we're using often became confused if a small user prompt triggered inclusion of a huge chunk, leading to all the generated text being about the chunk, and at times chunks would be included that were not closely related.
Input on the history of the design of the RAG system, lessons learned. There are a number of hazards to the RAG system."

## Current context
During the shakedown and empirical rigor trials (Workstream 16), Trial 1 retrieved generic dictionary definitions from `FirefightingGlossary.txt` (e.g. definitions of "Smoke", "Fire") instead of empirical research papers (Li et al. 2023, Talavera et al. 2023). When investigating, two prior design mitigations were found in `verify_rag_relevance`:
1. `is_relevant_definition = 1` acted as the primary sort key (`x[2]`).
2. Chunks > 100 lemmas suffered a blunt length penalty (`min(1.0, 100.0 / len(chunk_lemmas))`).

## What needs to be done
Document the foundational design history, failure modes, and hazards of RAG when pairing small local language models (SLMs like `gemma-2-2b-it`) with scientific retrieval, and define the architectural synthesis that resolves both failure modes without swinging the pendulum back.

### The Two Opposite Failure Modes (Hazards)
1. **Hazard 1: Prompt Hijacking & Context Overload ("Chunk Captivity")**:
   - *Cause*: Small local models (2B–8B) have constrained attention bandwidth and fragile instruction adherence.
   - *Symptom*: When a compact user prompt (e.g. 20 tokens) is combined with a huge technical chunk (e.g. 800–1,500 tokens), the model's self-attention is overwhelmed by the chunk.
   - *Result*: The model ignores the prompt's structural instructions and outputs a rambling summary or fixation on whatever was in the chunk. If the chunk was only loosely related (e.g. general domain vocabulary), the generation goes completely off the rails.
   - *Earlier Mitigation*: Favor tiny glossary definitions and aggressively penalize long sections (`100 / len(lemmas)`).

2. **Hazard 2: The Superficiality Trap & Glossary Monopoly ("Word-Match Crowding")**:
   - *Cause*: Attempting to fix Hazard 1 by prioritizing glossary definitions (`is_relevant_definition = 1`) on any lemma overlap.
   - *Symptom*: Broad unigrams and common domain words ("fire", "smoke", "system", "water", "device") trigger glossary items.
   - *Result*: Single-sentence dictionary entries leapfrog peer-reviewed empirical papers. Simultaneously, real empirical sections (300–600 lemmas) suffer an 80% length penalty, suppressing scientific rigor entirely.

### Architectural Solution & Revised Principles
1. **Discriminative Glossary Matching (Inverted Term Specificity)**:
   - Filter out common domain stopwords ("fire", "smoke", "system", "water", "equipment").
   - Require glossary prioritization to match:
     - Multi-word domain compounds (e.g. "time-of-flight", "smoke attenuation"),
     - High-specificity/low-frequency technical acronyms or terms (e.g. "UWB", "FMCW", "LiDAR", "IMU"), OR
     - Explicit definition queries ("what is X", "define X").
2. **Salience Windowing & Concept Concentration (Protecting Small Models without Penalizing Papers)**:
   - Score chunks by the maximum concept density in a focused sliding window (e.g. 150–250 tokens), not by total chunk length. A 600-word empirical paper section with a dense 150-word experimental finding should score high.
   - When injecting into context for the local model, extract the salient excerpt window accompanied by its academic citation header (`[Source: Author (Year) — Section: Name]`), preventing prompt bloat and chunk hijacking.
3. **Explicit Context Framing in LLM Prompt**:
   - In `demo_ui/views.py`, frame the context with clear guardrails:
     `"Use the following reference excerpts to ground your response to the user's question. If an excerpt is not directly relevant to the user's specific experimental design question, ignore it."`

## Tags
rag_service, demo_ui, grobid_client, background_resources, slm_prompt_hijacking, context_window, token_efficiency, enduring

## Status
enduring
