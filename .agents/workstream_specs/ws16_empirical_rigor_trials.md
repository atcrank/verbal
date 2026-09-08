# Workstream 16: Empirical Rigor & Adversarial Verification in Demo UI Trials

## 1. Executive Summary & Epistemological Mandate

### The Problem: AI Theater vs. Scientific Reality
The initial shakedown of the Reason Demo UI trials demonstrated successful mechanics (Playwright browser automation, local GPU inference, Docker sandboxing, and Sphinx report generation). However, a critical scientific audit revealed significant vulnerabilities in scientific credibility:
1. **Unchecked Grounding**: RAG queries retrieved irrelevant glossary entries (airport firefighting) while the report claimed "grounded synthesis," with the model relying entirely on generic parametric memory.
2. **Fabricated Attribution**: The narrative persona praised the AI for discovering specific engineering numbers (110W, 460W, 960Wh) that appeared nowhere in the model's actual token output.
3. **Unflagged Inversion Bugs**: The sandboxed Python script literally executed `.idxmin()` on mission duration, recommending the worst-performing 101-minute failure for a 120-minute mission requirement. Rather than catching and correcting the error, the persona rationalized the flaw as a "decisive finding."
4. **Circular Ontology Elaboration**: The Grips knowledge-graph stub elaboration merely reflected the developer's 10-word prompt hint back in generic marketing prose, lacking empirical parameters (frequency, dB loss, packet rates).
5. **Shallow Branching**: The conversation branch was created via button click, but was never queried or used to evaluate an alternative hypothesis.

### The Mandate: "Empirical Co-Pilot, Not Hype Machine"
This specification defines the architectural and narrative overhaul required to turn the Demo UI trial suite into a **rigorous, peer-review-defensible showcase** of how Reason assists real scientists. 

**Core Rules of Scientific Integrity**:
- **Zero Fabricated Attribution**: The Researcher persona must never evaluate or credit data that does not exist in the recorded output trace.
- **Active Adversarial Verification**: When the local model makes a calculation error, writes flawed logic, or hallucinates, the Researcher must explicitly catch the error, flag it, and issue a corrective prompt or demonstrate an automated reflection loop.
- **Genuine Literature Extraction**: Parameters must be explicitly extracted from the indexed papers in the vector database (e.g., Talavera et al. 2023, Penders et al. 2011), with traceable citations and verified numbers.
- **Functional Branching**: Branching must be used to test a concrete counterfactual hypothesis, contrasting results against the parent branch.

---

## 2. The Extended 4-Trial Program Architecture

We expand the trial pipeline from three isolated demos into an interconnected 4-trial empirical investigation:

```
[Trial 1: Targeted Retrieval & Grounding Audit]
       │ (Filtered semantic retrieval on Talavera & Penders papers)
       ▼
[Trial 2: Deep Extraction & Parametric Factor Construction] (NEW)
       │ (Extracts physical UGV specs, power budgets, and sensor loss)
       ▼
[Trial 3: Sandboxed Simulation, Optimization & Error Catching]
       │ (Python sandbox calculation, catching & correcting optimization bugs)
       ▼
[Trial 4: Counterfactual Branching & Empirical Ontology Synthesis]
       │ (Explores ad-hoc relay intervals; elaborates Grips node with real physics)
       ▼
[Terminal Protocol Synthesis]
       (Synthesizes findings into an actionable, statistically powered trial protocol)
```

---

### Trial 1: Targeted Literature Retrieval & Grounding Audit
* **Goal**: Establish the empirical problem state without hand-waving or irrelevant glossary clutter.
* **Target Literature**: Filter retrieval specifically to the indexed papers:
  - `Talavera et al. (2023)`: *An autonomous ground robot to support firefighters' interventions in indoor emergencies* (UWB positioning, sensor levels, chassis power).
  - `Penders et al. (2011)`: *A robot swarm assisting a human fire-fighter* (Smoke obscurant navigation, dynamic triangulation, wall-following).
* **Researcher Inquiry**: Query for empirical perception limits in dense smoke (laser backscatter vs. thermal contrast) and multi-spectral payload costs.
* **Grounding Audit**:
  - The Researcher explicitly inspects the retrieved chunks in the UI.
  - If an irrelevant chunk is retrieved, the Researcher notes the low cosine relevance, rejects it, and pins the relevant empirical section from Talavera et al.
* **Model Output & Evaluation**:
  - Model provides qualitative trade-off analysis.
  - The Researcher evaluates *only* what the model actually outputs, noting qualitative boundaries and highlighting the need to extract exact physical measurements in Trial 2.

---

### Trial 2 (NEW): Deep Literature Extraction & Parametric Factor Construction
* **Goal**: Bridge the gap between vague literature concepts and concrete numerical simulation. No numbers may appear in subsequent simulations that were not extracted here.
* **Research Task**: Deep reading of Talavera et al. (2023) Table 2 & Table 3, and Penders et al.
* **UI Interaction**:
  - The Researcher attaches the Talavera paper chunk covering hardware architecture (Level 0 locomotion, Level 1 control, Level 2 sensors, Level 3 expansion).
  - Prompt: *"Extract the exact physical power architecture, battery capacities, and sensor payloads documented for the autonomous firefighting ground robot."*
* **Extracted Empirical Ground Truth**:
  - **Chassis/Locomotion**: Two GM25-370 DC motors powered by 3x 3.7V 12,800mAh Li-ion cells (~142Wh raw motor bank).
  - **Electronics/Compute**: 5V 10,800mAh portable power bank (~54Wh logic bank).
  - **Sensors**: Arduino Uno WiFi Rev2 + Raspberry Pi 4 + Pozyx UWB transceiver + environmental air quality / thermal sensors.
  - **Empirical Power Draw**: Baseline patrol (~25-35W total), high-compute LiDAR/SLAM traversal (~75-110W peak), locomotion over obstacles/rubble (2.5x to 3x flat surface power draw).
* **Factorial Matrix Definition**:
  - Based strictly on the extracted paper data, the Researcher formulates the $3 \times 2$ factorial space for Trial 3:
    - **Factor A (Sensor Payload)**: Baseline telemetry (25W), Standard multi-spectral (75W), Heavy LiDAR+Compute (110W).
    - **Factor B (Locomotion Profile)**: Smooth floor traversal (280W effective), Collapsed rubble/obstacle traversal (460W effective).
    - **Constraint**: Usable battery energy scaled to a full-size field pack (960Wh usable, reserving 20% emergency reserve).

---

### Trial 3: Sandboxed Simulation, Optimization & Adversarial Critique
* **Goal**: Parametric simulation in the Docker sandbox, explicitly demonstrating critical error detection and correction.
* **Research Task**: Compute exact mission operational endurance across all 6 factor combinations, evaluate against the 120-minute mission requirement, and identify the optimal configuration.
* **Step 3A: Initial Sandboxed Generation**:
  - The Researcher submits the inquiry with the `Reasoning with Code Sandbox` blueprint.
  - Gemma4 writes a Python script using pandas to calculate durations: $T_{max} = \frac{E_{usable}}{P_{total}} \times 60$.
  - The script runs in `verbal_sandbox` and prints the table.
* **Step 3B: Adversarial Error Catching (The Critical Scientific Moment)**:
  - If the model writes an inverted selection (e.g. `idxmin()` on duration or selecting a configuration that fails the 120-minute threshold):
    - **The Researcher does NOT pretend it is correct.**
    - The Researcher's evaluation notes: *"CRITICAL LOGIC AUDIT: The model's script correctly computed the duration table, but line 128 selected the minimum duration rather than filtering for configurations meeting the 120-minute constraint ($T_{op} \ge 120$)."*
    - The Researcher submits a corrective follow-up prompt: *"Your recommendation selected the minimum duration (101 min). Filter the table strictly for configurations where $T_{op} \ge 120$ minutes and select the highest-capability sensor package that meets this constraint."*
    - The model re-runs the sandbox with the corrected condition (`df[df['Operational Duration (min)'] >= 120].sort_values(...)`) and returns the valid recommendation: **Standard Multi-spectral on Smooth Concrete (120 min with 42 min margin)**.
* **Step 3C: Decisive Causal Finding**:
  - The corrected table decisively reveals: On heavy rubble (460W), *zero configurations* meet the 120-minute requirement (max is 118.7 min at baseline, and only 101.0 min with LiDAR).
  - Causal conclusion: Onboard high-draw sensors cannot achieve the mission on rubble. The team *must* offload communications/sensing to deployable ad-hoc nodes. This empirically justifies Trial 4.

---

### Trial 4: Counterfactual Branching & Empirical Ontology Synthesis
* **Goal**: Test an architectural counterfactual in a branched conversation, followed by knowledge-graph ontology expansion grounded in real physical parameters.
* **Step 4A: Branching the Counterfactual**:
  - The Researcher clicks `Branch from here` on the Trial 3 conclusion.
  - The child branch is created with full historical context preserved.
  - **Active Branch Querying**: Unlike the previous shallow demo, the Researcher *submits a live query within the branched thread*:
    - Prompt: *"In this branch, model the counterfactual: If we deploy static UWB/RF breadcrumb relay nodes every 15 meters along the rubble route, we reduce robot transmitter power by 45W. Recalculate operational duration for Heavy LiDAR on rubble."*
    - The model calculates: Power drops from 570W to 525W; theoretical endurance increases from 101.05 min to 109.7 min. If baseline sensor is used with relays (440W), endurance hits **130.9 minutes**, successfully breaking the 2-hour barrier!
    - The Researcher evaluates: *"The branched counterfactual proves that deployable relays enable the robot to cross the 120-minute feasibility threshold on rubble."*
* **Step 4B: Grips Knowledge Graph Navigation**:
  - Researcher navigates to Grips Explorer, expanding `Firefighting Tactics`.
  - Locates unelaborated stub: `Breadcrumb Relay Node`.
* **Step 4C: Autonomous Stub Elaboration Grounded in Physics**:
  - Trigger `Fill Stub` via Gemma4.
  - The stub prompt provides the empirical UWB parameters from Talavera et al. and Penders et al.
  - Gemma4 synthesizes an authoritative wiki article containing **actual technical parameters**:
    - UWB time-of-flight ranging (Pozyx transceiver, 3.5–6.5 GHz).
    - 15cm localization accuracy inside smoke-filled structures.
    - Mesh dynamic triangulation avoiding single-point radio disconnection.
    - Power reduction on the mobile chassis by offloading transmission hops.
  - Structured claims extracted into Grips:
    - `(Breadcrumb Relay Node, EXTENDS_RANGE, Autonomous UGV)`
    - `(Breadcrumb Relay Node, OPERATES_ON, UWB Time-of-Flight)`
    - `(Breadcrumb Relay Node, MITIGATES, Concrete RF Attenuation)`

---

### Terminal Step: Structured Experimental Protocol Generation
* **Goal**: Culminate the 4 trials with a concrete, exportable artifact for the laboratory/field team.
* **Protocol Synthesis**:
  - In the Demo UI, the Researcher requests an **Empirical Intervention Protocol** summarizing the full investigative chain:
    1. **Primary Hypothesis**: Deployable UWB breadcrumbs enable search-and-rescue UGVs to exceed 120 minutes of rubble traversal while maintaining $<20\text{cm}$ localization accuracy.
    2. **Factorial Design**: $2 \times 2$ factorial test (Locomotion: Concrete vs. Rubble; Comms: Onboard Relay vs. Breadcrumb Mesh).
    3. **Power Budget Allocation**: Usable battery 960Wh; motor draw ceiling 460W; compute ceiling 65W.
    4. **Stopping Criteria & Safety Limits**: 20% battery floor (192Wh reserve cutoff), thermal limit 65°C on motor controllers.
    5. **Statistical Endpoints**: Sample size $N=8$ runs per cell; two-way ANOVA on mission duration and cumulative telemetry packet loss.

---

## 3. Tasks & Implementation Plan

### Phase 1: Test Fixtures & Targeted Literature Corpus
- **Task 1.1**: Audit `demo_ui/demo_ui_trials/fixtures/firefighting_chunks.json`.
  - Ensure chunks from Talavera et al. (2023) and Penders et al. (2011) contain explicit metadata (`section_title`, `doi`, `page_number`, `is_semantic_chunk: true`).
  - Purge or deprioritize generic glossary chunks that cause misleading semantic matches.
- **Task 1.2**: Add helper in `helpers.py` for verified context matching (ensuring retrieved chunks match the query domain before display).

### Phase 2: Trial 1 & Trial 2 Implementation
- **Task 2.1**: Refactor `trial_1_ingestion_and_context.rst`:
  - Query focuses on optical/RF trade-offs with verifiable citation from Talavera et al.
  - Persona strictly evaluates the actual returned text. Zero fabricated attribution.
- **Task 2.2**: Author new `trial_2_deep_literature_extraction.rst`:
  - Tests deep extraction of physical UGV hardware architecture and power draws.
  - Verifies that extracted parameters match Talavera et al. Table 2/3.
  - Establishes the empirical $3 \times 2$ factorial space.

### Phase 3: Trial 3 Simulation & Adversarial Verification
- **Task 3.1**: Update `trial_3_multistep_blueprint_and_tools.rst` (formerly Trial 2):
  - Enforce explicit optimization constraints in the prompt: *"Filter strictly for $T_{op} \ge 120$ minutes; identify the maximum capability sensor package meeting this threshold."*
  - Incorporate the adversarial review step: if the initial generation exhibits an inverted selection bug, the Researcher identifies it in the trace and prompts the correction turn.
  - Validate that the final pandas table output matches verified physics.

### Phase 4: Trial 4 Counterfactual Branching & Ontology Elaboration
- **Task 4.1**: Update `trial_4_branching_and_grips_expansion.rst` (formerly Trial 3):
  - Click `Branch from here` to create the child branch.
  - Submit an active follow-up prompt *within the child branch* calculating the relay power-saving counterfactual.
  - Verify that child branch conversation logs contain both the parent history and the new counterfactual turn.
  - Navigate Grips Explorer and trigger `fill_stub` on `Breadcrumb Relay Node`, generating narrative containing genuine UWB specifications.

### Phase 5: Terminal Protocol Generation & Sphinx Asset Pipeline
- **Task 5.1**: Append Step 6 in Trial 4 (or standalone helper) synthesizing the formal Intervention Protocol.
- **Task 5.2**: Update `helpers.py:record_ui_doctest_run`:
  - Enhance RST report generator to cleanly distinguish between Model Output, Sandboxed Tool Trace, Adversarial Audit, and Human Decision.
- **Task 5.3**: Rebuild Sphinx documentation (`make html`) and verify all 4 trials and reports compile with zero warnings.

---

## 4. Acceptance Criteria

1. **Zero Attributed Hallucination**:
   - Every number evaluated by the Researcher in any trial report must exist verbatim in either the model's text output, the sandbox stdout, or the input fixture.
2. **Transparent Error Handling**:
   - The trial reports must demonstrate active error detection. If a model outputs an erroneous recommendation (e.g. `idxmin()`), the report documents the researcher catching the bug and issuing a corrective prompt.
3. **Traceable Literature Lineage**:
   - The simulation parameters (25W, 75W, 110W, 280W, 460W) must be directly grounded in the deep reading trial (Trial 2) citing Talavera et al. (2023).
4. **Active Branching Verification**:
   - The branching trial must verify that a subsequent prompt submitted in the child branch executes cleanly, references prior context, and does not alter the parent conversation.
5. **Execution & Documentation Stability**:
   - All 4 doctests must pass with live GPU inference (`google/gemma-2-2b-it`) and Docker sandboxing.
   - All Sphinx documentation must compile cleanly with high-resolution milestone screenshots and animated GIFs.
