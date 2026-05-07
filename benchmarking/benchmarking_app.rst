Benchmarking
============

This app provides an automated test harness to evaluate the performance of AI models and RAG retrieval configurations against the local knowledge base.

App achievements
=================

* **Synthetic Data Generation:** Autonomously reads uploaded documents and generates challenging, context-grounded Question/Answer pairs.
* **Automated Evaluation:** Scores model outputs mathematically using established metrics like Semantic Similarity and Faithfulness (grounding).
* **Asynchronous Runners:** Benchmarking suites are delegated to Celery, allowing hundreds of tests to run in the background.
* **Traceable Experiments:** Records all experiments, parameters, and historical benchmark runs directly in the database for longitudinal comparison.

App enhancement
===============

* **LLM-as-a-Judge:** Implement advanced evaluators where a larger "Teacher" model (e.g., GPT-4o) grades the output of the local models on nuance, tone, and formatting.
* **Data Visualization:** Build integrated charts/graphs in the Django Admin to visually compare the performance of different models over time.
* **Prompt Optimization:** Add automated prompt-tuning loops (like DSPy) that slightly tweak system prompts to maximize the benchmark score over successive runs.
* **Retrieval Metric Expansion:** Add metrics specifically for the RAG pipeline, such as Mean Reciprocal Rank (MRR) and Normalized Discounted Cumulative Gain (NDCG).