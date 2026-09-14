Sandbox Manager - Isolated Python Execution Environment
=========================================================

The **Sandbox Manager** application provides a secure, resource-constrained Docker environment for executing Python scripts synthesized by autonomous agents during study design workflows. It safeguards the host operating system from infinite loops, memory exhaustion, and unintended filesystem alterations while enabling agents to perform computational logic, simulations, and data transformations.

.. contents:: Table of Contents
   :local:
   :depth: 2


1. Purpose & Motivating Problem
-------------------------------

Why Code Sandboxing is Necessary
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
When autonomous cognitive blueprints (in `metacognition`) are tasked with complex research problems (such as computing experimental power, simulating agent-based models, or verifying causal graphs), relying on pure LLM text generation is insufficient. Language models struggle with precise mathematical computations and symbolic reasoning.

Allowing models to write and execute Python code bridges this gap. However, executing LLM-generated code directly on the host machine presents severe operational hazards:

* **Host System Compromise**: Naked `subprocess` execution allows untrusted scripts to access private files, delete directories, or compromise environment credentials.
* **Runaway Loops & Thread Hijacking**: Mathematical packages (such as NumPy or SciPy) frequently attempt to pre-allocate thread pools matching the host machine's total CPU core count, exhausting virtual memory within seconds.
* **Uncontrolled Resource Depletion**: An unintended infinite loop or exponential recursion will lock 100% of the host CPU and consume all system RAM until the operating system freezes.

**Sandbox Manager** encapsulates all script execution within an isolated, heavily throttled container with strict execution timeouts.


2. Architecture & Mechanism
---------------------------

Container Architecture & Resource Boundaries
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The sandbox runs as a standalone service in [docker-compose.yml](file:///home/crank/coding/antigrav/verbal/docker-compose.yml) (`verbal_sandbox` on port 8002):

* **Filesystem Isolation**: The host only mounts the project's `./workspaces` folder into the container at `/workspace`. The container has no visibility into project source code, database credentials, or system directories.
* **Resource Limits**:
  * **Memory Limit**: Throttled to 1 GB physical RAM. If a script exceeds this ceiling, the Linux kernel terminates the process rather than destabilizing the host.
  * **CPU Throttling**: Restricted to 1.0 CPU cores (`cpus: '1.0'`).
  * **Thread Restraint**: Injects ``OPENBLAS_NUM_THREADS=1`` into the environment to prevent linear algebra libraries from spawning unmanageable thread pools.
* **Execution Timeouts**: Enforces hard timeouts via the Linux `timeout` utility (defaulting to 30 seconds). A script that fails to terminate within the threshold is killed, returning standard UNIX exit code `124`.

Key Data Models
~~~~~~~~~~~~~~~

* **``SandboxConfiguration``**: A singleton model in PostgreSQL managing the active Python package environment. It validates package strings against PEP-508 specifications, writes `sandbox/requirements.txt`, and triggers Docker image rebuilding directly from Django Admin.
* **``SandboxExecutionLog``**: Records every execution event, linking it to the originating conversation ID, script filepath, return code, execution duration, `stdout`, and `stderr`.

Execution Workflow
~~~~~~~~~~~~~~~~~~
1. An agent step in `metacognition` generates a Python script (e.g. `workspace_123/simulate_power.py`).
2. The agent invokes the `run_sandbox_script` meta-tool.
3. The tool issues an HTTP POST to the sandbox microservice at `http://127.0.0.1:8002/execute`.
4. The container runs the script inside `/workspace` with the configured timeout.
5. `stdout`, `stderr`, and the integer return code are streamed back, logged in `SandboxExecutionLog`, and delivered back to the agent's observation context for self-evaluation.


3. Observability & Health Signals
---------------------------------

How to Know the Sandbox is Working Well
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

1. **Clean Return Codes in Execution Logs**:
   In Django Admin under **Sandbox Manager > Sandbox Execution Logs**, inspect recent script runs. A healthy execution displays:
   * **Return Code**: `0`
   * **Stdout**: Contains structured output, tables, or numeric calculations.
   * **Stderr**: Empty or contains non-fatal informational warnings.
2. **Container Heartbeat**:
   Querying `http://127.0.0.1:8002/docs` or pinging the container returns an immediate HTTP 200 response, confirming that the internal FastAPI runner is healthy.


4. Diagnostic Tips & Failure Modes
----------------------------------

When the Sandbox Misses the Mark & How to Tune
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

* **Return Code 124 (Timeout Exceeded)**:
  * *Hazard*: The agent generated a script with an infinite `while` loop, an excessively fine simulation grid, or an unoptimized nested loop.
  * *Remedy*: Inspect `stderr` in the execution log. If the script was legitimately compute-heavy (e.g. Monte Carlo trial with 1,000,000 iterations), increase `execution_timeout` in **Sandbox Environment Configuration** from 30s to 60s, or instruct the agent via prompt to downsample the simulation size.
* **Return Code > 0 with ``ModuleNotFoundError``**:
  * *Hazard*: The generated script attempted to import an uninstalled package (e.g. `scikit-learn` or `statsmodels`).
  * *Remedy*: Navigate to **Sandbox Environment Configuration** in Django Admin, append the required package to `requirements_txt`, and click **Save and Rebuild Sandbox Image**.
* **Exit Code 137 (OOM Killed)**:
  * *Hazard*: The script attempted to load an enormous dataset into memory exceeding the container's 1 GB RAM allocation.
  * *Remedy*: Modify the script to process data in chunked streams or adjust the memory limit in `docker-compose.yml` if the host workstation has sufficient RAM headroom.


Module Reference
----------------

.. automodule:: sandbox_manager.models
   :members:
   :undoc-members:
   :show-inheritance: