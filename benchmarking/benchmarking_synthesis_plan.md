# Plan: Smart Benchmarking & "Science" Package

## 1. Philosophy: Two Pillars of Truth
To scientifically evaluate the system, we need to separate "Engine Performance" from "Domain Performance."

*   **Pillar A: The Standard Candle (General Knowledge)**
    *   **Goal:** Prove the code works. If the system can't answer "What is the capital of France?" given a Wikipedia article about France, the engine is broken.
    *   **Source:** Adapt a subset of **SQuAD** (Stanford Question Answering Dataset) or **RAGBench**.
    *   **Implementation:** Ship a `fixtures/standard_candle.json` containing 20-50 high-quality QA pairs and a small "General Knowledge" corpus (e.g., 5 clean Wikipedia articles).

*   **Pillar B: The Synthetic Domain (Specific Knowledge)**
    *   **Goal:** Prove the *strategies* work. Does `RegexStrategy` actually help define "Water Hammer"?
    *   **Source:** Your "Firefighting" documents.
    *   **Problem:** You are not a fire expert.
    *   **Solution:** **AI-Generated Benchmarks (Synthetic Data)**. We build a tool that reads your document and *generates* the questions and "Ideal Answers" for you, categorizing them by difficulty.

---

## 2. Implementation: The "Smart" Tools

### A. The Synthetic Scenario Generator
We will create a management command (and Admin Action) `generate_synthetic_scenarios`.
*   **Input:** A `Document` (e.g., `Firefighting.pdf`).
*   **Process:**
    1.  **Chunking:** Read the document using the Default Strategy.
    2.  **Generation:** For every Nth chunk, ask the AI Service to generate:
        *   **1 Factoid Question:** "What is the flow rate of a red hydrant?" (Easy retrieval).
        *   **1 Reasoning Question:** "Why should you avoid closing a valve too quickly?" (Requires synthesis).
        *   **1 Negative Question:** "What does this section say about aircraft fires?" (When the section is about hoses. Tests hallucination resistance).
    3.  **Output:** Creates a `ScenarioGroup` named "Synthetic - [Doc Title]" populated with these `BenchmarkScenario`s.

### B. The "Standard Candle" Fixture
We will create a Django fixture file `benchmarking/fixtures/standard_candle.json`.
*   **Content:**
    *   `BenchmarkCorpus`: "General Knowledge (Wikipedia Subset)".
    *   `Document`: "Paris.txt", "Python_Programming.txt", "Apollo_11.txt".
    *   `ScenarioGroup`: "Standard Validation Set".
    *   `BenchmarkScenario`: 50 curated questions with strict ground truth.
*   **Usage:** `python manage.py loaddata standard_candle.json`. This gives every developer an immediate baseline to test against.

---

## 3. The Experiments: Pre-Defined Investigations
We will ship the system with 3 "Canonical Investigations" that answer specific engineering questions.

### Investigation 1: "The Chunk Size Physics"
*   **Hypothesis:** Smaller chunks improve precision for definitions, but larger chunks improve reasoning for complex procedures.
*   **Setup:**
    *   **Corpus:** Firefighting Manual.
    *   **Scenarios:** Synthetic Group (Factoids + Reasoning).
    *   **Experiment A:** `chunk_size=200`, `overlap=20`.
    *   **Experiment B:** `chunk_size=500`, `overlap=50`.
    *   **Experiment C:** `chunk_size=1500`, `overlap=150`.
*   **Outcome:** A graph showing the "Goldilocks" zone for this specific document type.

### Investigation 2: "The Strategy Lift"
*   **Hypothesis:** Higher-order strategies (Regex, Prompt) significantly improve recall for jargon terms compared to raw vector search.
*   **Setup:**
    *   **Corpus:** Firefighting Glossary.
    *   **Scenarios:** Synthetic Group (Definitions).
    *   **Experiment A (Baseline):** Default Reading only.
    *   **Experiment B (Augmented):** Default + `RegexStrategy` (Glossary Extractor).
    *   **Experiment C (AI Augmented):** Default + `PromptStrategy` (Summary/Keywords).
*   **Outcome:** Quantifiable proof that "Regex Strategy added +15% recall on terminology."

### Investigation 3: "Robustness (The RGB Test)"
*   **Hypothesis:** The system should refuse to answer questions when the information is missing (Negative Rejection).
*   **Setup:**
    *   **Corpus:** A small, focused document (e.g., "Hydrant Maintenance").
    *   **Scenarios:** A group of "Negative Questions" (questions about "Aircraft Rescue" which are *not* in the Hydrant manual).
    *   **Experiment:** Run standard retrieval.
    *   **Success Metric:** High "Semantic Score" against an ideal answer of "I cannot answer this from the provided context." (We may need to tune the scoring logic to handle this specific case).

---

## 4. Roadmap

1.  **Step 1: The Generator.** Build the `generate_synthetic_scenarios` command. This unblocks you immediately by turning your PDF into a test suite.
2.  **Step 2: The Fixture.** Create the `standard_candle.json` so you have a sanity check that doesn't depend on your local files.
3.  **Step 3: The Metrics.** Integrate **RAGAS**-style metrics (Faithfulness, Answer Relevance) into `BenchmarkResult` (currently we only have Recall and Similarity).
4.  **Step 4: The Dashboard.** Add a simple view to visualize `Investigation` results (e.g., a bar chart comparing Experiment A vs B).
