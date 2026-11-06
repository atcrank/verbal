"""
Compatibility shims for PyTorch versions and downstream packages (torchao, transformers).

Provides polyfills for missing attributes, data types, and enum symbols
in PyTorch releases (e.g. PyTorch < 2.9/2.11) that newer versions of
transformers and torchao attempt to import during FP8 / low-precision quantization.
"""
from enum import Enum
import logging

logger = logging.getLogger(__name__)


def apply_torch_compatibility_shims() -> None:
    """
    Applies runtime compatibility polyfills to `torch` and `torch.nn.functional`.
    Safe to call multiple times (idempotent).
    """
    try:
        import torch
        import torch.nn.functional as F

        # 1. Polyfill float8_e8m0fnu if missing (transformers fp8 check)
        if not hasattr(torch, "float8_e8m0fnu"):
            setattr(torch, "float8_e8m0fnu", None)

        # 2. Polyfill ScalingType and _ScalingType on torch.nn.functional
        # Newer torchao / transformers FP8 routines expect torch.nn.functional.ScalingType
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

        # 4. Defensive Quarantine: If torchao is in site-packages but broken
        # (incompatible PyTorch ABI), quarantine it so transformers treats it as unavailable
        # rather than crashing with cascading ImportErrors on missing internal C++ kernels.
        try:
            import importlib.util
            if importlib.util.find_spec("torchao") is not None:
                try:
                    import torchao  # Test if clean import works
                except Exception as err:
                    logger.warning(
                        "torchao found in environment but failed to import (%s). "
                        "Quarantining torchao from Hugging Face transformers to allow standard model loading.",
                        err,
                    )
                    import transformers.utils.import_utils as tu
                    tu.is_torchao_available = lambda *args, **kwargs: False
        except Exception:
            pass
    except ImportError:
        pass

