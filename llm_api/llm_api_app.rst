LLM API - Local Inference, Proxy Routing & Hardware-Aware Serving
===================================================================

The **LLM API** application provides the core inference engine, model abstraction layer, and proxy routing infrastructure for the Verbal study design assistant. It exposes standardized OpenAI-compatible interfaces while isolating heavy GPU memory workloads from lightweight web clients and background task workers.

.. contents:: Table of Contents
   :local:
   :depth: 2


1. Purpose & Motivating Problem
-------------------------------

Why a Dedicated Inference Layer is Necessary
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Running Large Language Models directly within interactive web applications introduces immediate operational hazards:

* **VRAM Exhaustion & Resource Freezes**: Loading 2B to 8B parameter models into GPU memory requires between 3 GB and 16 GB of VRAM. If web worker processes each load model weights into memory, the GPU immediately encounters CUDA Out-of-Memory (OOM) errors and crashes.
* **Synchronous Web Blocking**: Generating text token-by-token is computationally intensive (taking hundreds of milliseconds to multiple seconds). If performed directly within a web server thread (such as Gunicorn or Django's WSGI thread), the entire web interface freezes, causing browser requests to time out.
* **Schema Drift & Malformed Output**: Open-weight local models frequently drop punctuation, hallucinate trailing commas, or omit required keys when asked to generate raw JSON strings. Without constrained token generation, downstream application logic crumbles.

**LLM API** resolves these challenges by separating process roles, enforcing strict FSM grammar constraints on generation, dynamically monitoring host hardware headroom, and unifying diverse execution backends.


2. Architecture & Mechanism
---------------------------

Multi-Role Process Topology
~~~~~~~~~~~~~~~~~~~~~~~~~~~
The application isolates concerns using the ``VERBAL_ROLE`` environment variable:

* ``VERBAL_ROLE=inference``: Operates as an internal microservice on port 8001. It alone loads heavy weights into GPU memory, handles tensor computations, and processes inference requests sequentially or via continuous batching.
* ``VERBAL_ROLE=web``: Operates the public web interface on port 8000. It loads a lightweight CPU tokenizer for rapid client-side token counting, but immediately proxies all prompt generation requests to the inference server over HTTP.
* ``VERBAL_ROLE=worker``: Used by background task runners (`verbal_tasks`). Like the web role, it routes requests to the inference server, inserting brief interleaving pauses so that interactive user UI requests jump ahead of long-running batch jobs.

Tri-Backend Hosting Architecture
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The system allows experiment designers to select among three distinct hosting engines depending on their available hardware:

1. **Local PyTorch (In-Process)**:
   Loads models directly via HuggingFace `transformers` and `accelerate`. Provides native integration with `outlines` for structured finite-state machine (FSM) token guidance and supports dynamic PEFT LoRA adapter attachment.
2. **Containerized vLLM Service**:
   Routes requests to an optimized Docker container (`verbal_vllm` on port 8003). Employs PagedAttention and continuous batching for high throughput, alongside volume-mounted dynamic LoRA adapters (`/lora_adapters`).
3. **Containerized Ollama Service**:
   Routes requests to an Ollama container (`verbal_ollama` on port 11434). Specializes in running GGUF-quantized models with automated background model pulling and idle VRAM unloading.

Hardware Inspection & Resource Advisor
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
To eliminate hardcoded assumptions that crash on consumer hardware or underutilize workstation GPUs, [llm_api/hardware.py](file:///home/crank/coding/antigrav/verbal/llm_api/hardware.py) dynamically probes the host system:

* Measures physical and free CUDA VRAM, compute capability, native FP16/BF16 instruction support, and host CPU/RAM metrics.
* Auto-calculates safe vLLM memory utilization (leaving required headroom for host display servers, e.g. 0.75 on 6 GB GPUs vs 0.90 on 24 GB workstation cards).
* Detects Turing-generation GPUs (Compute Capability 7.5, such as GTX 1660 Ti) that lack native BF16 instructions, automatically prescribing `float16` precision to prevent initialization warnings and performance drops.

Expanded Quantization Modes
~~~~~~~~~~~~~~~~~~~~~~~~~~~
Rather than offering only a single binary 4-bit toggle, the `LocalAIModel` schema supports granular precision options:

* **NF4 (NormalFloat4)**: 4-bit BitsAndBytes quantization optimized for normally distributed weights (recommended for 6 GB to 8 GB VRAM).
* **FP4 (Float4)**: Standard 4-bit floating-point quantization.
* **INT8**: 8-bit integer quantization using BitsAndBytes.
* **NONE**: Full unquantized 16-bit precision (`float16` or `bfloat16`).
* **AWQ & GPTQ**: Activation-aware and post-training quantized formats for containerized vLLM deployment.

Structured JSON Generation with Outlines
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
When structured schema output is required (e.g. factor extraction, plan generation, claim evaluation), the service uses `outlines` to construct regex-based finite-state machines. Only tokens that conform to the Pydantic schema are permitted to be sampled from the vocabulary logits, guaranteeing 100% valid JSON without parsing retries.

Endpoints
~~~~~~~~~

* ``POST /api/llm/v1/chat/completions``: Standardized OpenAI-compatible chat completion endpoint supporting streaming, tools, and JSON schema constraints.
* ``GET /api/llm/hardware/``: Diagnostic endpoint returning detected GPU/CPU metrics, free VRAM, and recommended engine configurations.
* ``POST /api/llm/internal/unload-vram/``: Internal coordination endpoint that releases PyTorch GPU memory cache when switching backends.


3. Observability & Health Signals
---------------------------------

How to Know Inference is Working Well
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

1. **Host Hardware & Resource Advisor Banner**:
   In Django Admin under **LLM API > Global System Configurations**, inspect the live diagnostic card. It reports active VRAM utilization, detected compute capability, precision compatibility badges (e.g., native FP16 vs emulated BF16), and recommended sequence lengths.
2. **Generation Metrics in Prompt Logs**:
   Every completion logs duration in milliseconds, input tokens, output tokens, and tokens-per-second (TPS) to `PromptResponseLog`. A healthy local setup on a modern consumer GPU typically achieves:
   * **7B–8B 4-bit models**: 15–30 tokens/sec.
   * **2B–3B 4-bit models**: 40–70 tokens/sec.
3. **Absence of JSON Parsing Retries**:
   Structured requests (`generate_outline`) should return instantly upon model completion without triggering validation fallback loops.


4. Diagnostic Tips & Failure Modes
----------------------------------

When Inference Misses the Mark & How to Tune
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

* **vLLM Container Crashes on Startup (Allocation Mismatch)**:
  * *Hazard*: vLLM defaults to requesting 92% of *total physical* device VRAM. On consumer systems with active desktop environments (which consume ~1 GB VRAM), free memory is less than requested, causing vLLM to exit with an allocation error.
  * *Remedy*: In `SystemConfiguration`, ensure `vllm_gpu_memory_utilization` is set to the advisor's recommended value (e.g. `0.70` or `0.75`), or leave it blank to allow auto-tuning.
* **Prompt Overload & Context Truncation ("Context Rot")**:
  * *Hazard*: Stuffing excessive RAG excerpts and conversation history into a 4096-token model leads to attention saturation, where the model forgets system instructions or outputs repetitive gibberish.
  * *Remedy*: Lower `context_window` in the model definition, restrict RAG retrieval counts, or summarize conversation history using `PromptResponseLog` state tree snapshots.
* **CUDA Out of Memory During Backend Switching**:
  * *Hazard*: If the internal PyTorch inference server loaded a model and a user switches the active backend to vLLM or Ollama, PyTorch may retain cached CUDA memory blocks.
  * *Remedy*: The system automatically broadcasts a cache flush to `POST /api/llm/internal/unload-vram/`. If switching manually, execute the Django Admin action **"Unload all Local Models"** or restart the inference process.
* **Repetitive or Looping Generations**:
  * *Hazard*: Greedy sampling (`temperature=0.0`) on small models can induce infinite repetition loops.
  * *Remedy*: Increase temperature slightly (`0.6`–`0.7`) or verify that end-of-sequence tokens (`<|im_end|>`, `<|eot_id|>`) are correctly registered in `ai_service.terminators`.


Module Reference
----------------

.. automodule:: llm_api.models
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: llm_api.hardware
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: llm_api.ai_service
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: llm_api.api
   :members:
   :undoc-members:
   :show-inheritance:
