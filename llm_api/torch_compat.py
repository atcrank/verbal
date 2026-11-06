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
    except ImportError:
        pass

