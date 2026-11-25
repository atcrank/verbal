"""
Compatibility shims and package guardrails for PyTorch, transformers, and sentence-transformers.

Why this module exists:
1. PyTorch < 2.9 polyfills: Newer `transformers` releases (>= 4.48 / 5.x) probe FP8 symbols
   (`torch.float8_e8m0fnu`, `torch.nn.functional.ScalingType`, `scaled_group_mm`) that do not
   exist in PyTorch 2.6–2.8 (used here for CUDA 12.x compatibility).
2. Unconditional `torchao` quarantine (unless `VERBAL_ALLOW_TORCHAO=1`):
   - Reason uses BitsAndBytes (`nf4`, `fp4`, `int8`), AWQ, GPTQ, or GGUF—never `torchao`.
   - `torchao` uses lazy top-level imports in `torchao/__init__.py`, so a naive `try: import torchao`
     check succeeds at startup, leaving `transformers.utils.import_utils.is_torchao_available()` True.
   - Later, during Gemma 4 12B NF4 `from_pretrained(..., BitsAndBytesConfig(...))`, `transformers`
     imports `torchao.quantization` / `torchao.dtypes` (`AffineQuantizedTensor` subclass checks),
     which fails with C++ ABI errors against PyTorch 2.6–2.8 or conflicts with BitsAndBytes `Params4bit`.
   - In `reason-worker`, `sentence_transformers` also eagerly imports `transformers.trainer`, and
     `langchain_huggingface.HuggingFaceEmbeddings` catches ANY transitive `ImportError` (such as a
     broken `torchao` submodule) and rewrites it into the misleading message:
     `"Could not import sentence_transformers python package"`.
   - Setting `sys.modules["torchao"] = None` per PEP 451 makes `importlib.util.find_spec("torchao")`
     return `None` and halts any `import torchao` with `ModuleNotFoundError` without executing any
     `torchao` Python or C++ code, even if a transitive dependency reinstalled `torchao` in the venv.
"""
from enum import Enum
import logging
import os
import sys

logger = logging.getLogger(__name__)


def quarantine_torchao() -> None:
    """
    Blocks `torchao` imports process-wide via PEP 451 (`sys.modules['torchao'] = None`)
    unless `VERBAL_ALLOW_TORCHAO=1` is explicitly set in the environment.
    """
    allow_flag = os.environ.get("VERBAL_ALLOW_TORCHAO", "").strip().lower()
    if allow_flag in ("1", "true", "yes"):
        return

    # Purge any already-loaded torchao submodules and place the PEP 451 None sentinel
    for mod_name in list(sys.modules.keys()):
        if mod_name == "torchao" or mod_name.startswith("torchao."):
            if mod_name != "torchao":
                sys.modules.pop(mod_name, None)
    sys.modules["torchao"] = None

    # If `transformers` was already imported before this shim ran, also override its
    # availability helpers directly (since `transformers.utils` binds them at import time).
    tu_import = sys.modules.get("transformers.utils.import_utils")
    if tu_import is not None:
        tu_import.is_torchao_available = lambda *args, **kwargs: False
        if hasattr(tu_import, "_torchao_available"):
            tu_import._torchao_available = False

    tu_pkg = sys.modules.get("transformers.utils")
    if tu_pkg is not None and hasattr(tu_pkg, "is_torchao_available"):
        tu_pkg.is_torchao_available = lambda *args, **kwargs: False


def apply_torch_compatibility_shims() -> None:
    """
    Applies `torchao` import quarantine and runtime PyTorch symbol polyfills.
    Idempotent and safe to invoke at early Django settings startup as well as service modules.
    """
    # Always quarantine torchao first, before importing torch or transformers
    quarantine_torchao()

    try:
        import torch
        import torch.nn.functional as F

        # 1. Polyfill float8_e8m0fnu if missing (transformers FP8 dtype check)
        if not hasattr(torch, "float8_e8m0fnu"):
            setattr(torch, "float8_e8m0fnu", None)

        # 2. Polyfill ScalingType and _ScalingType on torch.nn.functional
        if not hasattr(F, "ScalingType"):
            class ScalingType(Enum):
                TensorWise = 0
                RowWise = 1
                BlockWise1x16 = 2
                BlockWise1x32 = 3
                BlockWise1x128 = 4
                BlockWise128x128 = 5

            setattr(F, "ScalingType", ScalingType)
            setattr(F, "_ScalingType", ScalingType)

        # 3. Polyfill scaled_group_mm and scaled_grouped_mm stubs if missing
        if not hasattr(F, "scaled_group_mm"):
            setattr(F, "scaled_group_mm", None)
        if not hasattr(F, "scaled_grouped_mm"):
            setattr(F, "scaled_grouped_mm", None)
    except ImportError:
        pass
