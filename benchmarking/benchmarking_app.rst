Benchmarking - Automated Evaluation, Synthetic Generation & LoRA Flywheels
===========================================================================

The **Benchmarking** application provides an automated evaluation harness for measuring the performance, accuracy, and factual grounding of LLM models and RAG configurations. It synthesizes context-grounded evaluation datasets, scores model outputs against mathematical metrics, and exports curated scenarios into fine-tuning datasets for local LoRA training.

.. contents:: Table of Contents
   :local:
   :depth: 2


1. Purpose & Motivating Problem
-------------------------------

Why Automated Benchmarking is Essential
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Language model behavior is probabilistic. When developers adjust system prompts, tweak RAG chunking strategies, or swap underlying model weights (e.g., from Gemma to Qwen or Llama), subjective impressions are profoundly deceptive. A model may sound more confident or articulate while subtly hallucinating critical experimental controls or dropping required JSON fields.

Relying on informal "vibe checks" leads to silent regression:

* **Prompt Regression**: An edit that improves response tone for one question may break structured tool output for five others.
* **Retrieval Degeneracy**: A change to embedding similarity thresholds may retrieve higher volumes of text while dropping the single critical paper section containing the true answer.
* **Ungrounded Fine-Tuning**: Training LoRA adapters without baseline benchmarks makes it impossible to verify whether the fine-tuned model actually acquired specialized domain knowledge or simply suffered catastrophic forgetting.

**Benchmarking** replaces subjective impressions with repeatable, automated empirical evaluations.


2. Architecture & Mechanism
---------------------------

The Evaluation Data Model Hierarchy
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
* **``BenchmarkSuite``**: An overarching test collection (e.g. *Robotics Methodology Suite*, *Statistical Inference Validation*).
* **``BenchmarkScenario``**: An individual evaluation test case consisting of:
  * **User Prompt**: The challenge question.
  * **Reference Context**: The source document excerpts containing the ground truth.
  * **Expected Answer**: The authoritative gold-standard response.
  * **Active RAG Configuration**: Specific retrieval thresholds and chunking strategies to evaluate.
* **``BenchmarkRun``**: An execution instance of a suite against a specified model backend, logging total score, duration, and token consumption.
* **``ScenarioResult``**: Granular scoring of an individual scenario within a run, storing the generated response, similarity score, faithfulness score, and latency.

Synthetic Question Generation & Scoring Pipeline
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
* **Synthetic Q&A Generation (``generators.py``)**: Ingests documents from `background_resources` and generates challenging question-answer pairs directly grounded in source paragraphs.
* **Automated Scoring Runner (``runner.py``)**: Evaluates generated completions against ground truth using:
  * **Semantic Similarity**: Cosine similarity between embedding vectors of the generated answer and the reference answer.
  * **Faithfulness / Grounding**: Verifies that every assertion in the response is mathematically supported by the reference context.
  * **Exact / Structural Match**: Checks compliance with required formatting or categorical outputs.
* **Fine-Tuning Data Flywheel (``exporters.py`` & ``train_lora.py``)**: Successful benchmark scenarios can be exported to standard JSONL datasets (ShareGPT format) to train specialized LoRA adapters using Unsloth. The system tracks dataset currency and flags stale datasets when source documents change.
* **Background Queueing**: Benchmark suites are dispatched asynchronously via `verbal_tasks`, allowing suites with hundreds of scenarios to execute without blocking the UI.


3. Observability & Health Signals
---------------------------------

How to Know Benchmarks are Working Well
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

1. **Longitudinal Score Tracking in Django Admin**:
   Under **Benchmarking > Benchmark Runs**, compare average scores across successive model versions and prompt iterations. A healthy pipeline exhibits stable or improving similarity scores (> 0.80) across releases.
2. **Detailed Failure Inspection in Scenario Results**:
   Inspect individual `ScenarioResult` rows. The diff viewer highlights where a candidate model's answer deviated from the reference answer or where hallucinated claims failed grounding checks.
3. **Dataset Freshness Indicators**:
   Under **Benchmarking > Fine Tuning Datasets**, datasets display a currency badge: ``🟢 Up to date`` or ``⚠️ Stale`` (if the underlying research documents have been updated since dataset creation).


4. Diagnostic Tips & Failure Modes
----------------------------------

When Benchmarking Misses the Mark & How to Tune
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

* **Noisy or Fluctuating Benchmark Scores**:
  * *Hazard*: If candidate models generate responses with high non-deterministic temperature (`temperature >= 0.7`), identical prompts will receive swinging scores across runs.
  * *Remedy*: Ensure evaluation suites configure low temperature (`temperature=0.0` or `0.1`) to ensure reproducible, deterministic benchmarking.
* **Synthetic Generation of Trivial Questions**:
  * *Hazard*: Automated Q&A generators may produce superficial questions (e.g., *"What year was paper X written?"*) rather than testing deep study design logic.
  * *Remedy*: In `generators.py`, adjust the prompt strategy to instruct the generator to formulate *inferential* and *causal* questions requiring multi-sentence synthesis.
* **Dataset Staleness Warnings**:
  * *Hazard*: Fine-Tuning Datasets display `⚠️ Stale (Source data updated)`.
  * *Remedy*: Re-export the dataset from the updated benchmark suite to incorporate the revised document excerpts before launching LoRA fine-tuning.


Module Reference
----------------

.. automodule:: benchmarking.models
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: benchmarking.generators
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: benchmarking.runner
   :members:
   :undoc-members:
   :show-inheritance:
