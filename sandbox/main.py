from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import subprocess
import os
import resource

app = FastAPI()


class ExecuteRequest(BaseModel):
    filepath: str
    timeout: int = 30


def set_limits():
    """
    Executed in the child process just before the script runs.
    Enforces strict OS-level resource limits.
    """
    # 1. Memory Limit
    # NOTE: We do NOT use RLIMIT_AS (Virtual Memory Limit) here because libraries 
    # like NumPy/OpenBLAS attempt to reserve massive amounts of virtual memory 
    # for thread pools at startup, which causes immediate false-positive crashes 
    # (e.g. "OpenBLAS error: Memory allocation still failed").
    # Instead, we rely entirely on the Docker container's physical memory cgroup limit 
    # (e.g., 1G in docker-compose.yml) which safely kills the process if it actually 
    # *uses* too much physical RAM.

    # 2. File Write Limit: 50 MB max per file
    FILE_LIMIT = 50 * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_FSIZE, (FILE_LIMIT, FILE_LIMIT))

    # 3. CPU Time Limit: 30 seconds of active CPU time (failsafe against while True:)
    resource.setrlimit(resource.RLIMIT_CPU, (30, 30))


@app.post("/execute")
def execute(req: ExecuteRequest):
    workspace_root = "/workspace"
    full_path = os.path.abspath(os.path.join(workspace_root, req.filepath))

    if not full_path.startswith(workspace_root):
        raise HTTPException(status_code=403, detail="Path traversal attempt blocked.")

    if not os.path.exists(full_path):
        raise HTTPException(status_code=404, detail=f"File {req.filepath} not found in workspace.")

    try:
        result = subprocess.run(
            ["python", full_path],
            capture_output=True,
            text=True,
            timeout=req.timeout,
            preexec_fn=set_limits,
            cwd=os.path.dirname(full_path)
        )
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
            "status": "success" if result.returncode == 0 else "error"
        }
    except subprocess.TimeoutExpired as e:
        return {
            "stdout": e.stdout.decode() if e.stdout else "",
            "stderr": (
                          e.stderr.decode() if e.stderr else "") + f"\n[SYSTEM] Execution timed out after {req.timeout} seconds.",
            "returncode": 124,
            "status": "timeout"
        }