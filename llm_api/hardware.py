"""
Hardware introspection and resource advisor module for local AI hosting.

Provides runtime detection of host CPU/GPU capabilities, available VRAM,
tensor precision features, and dynamic configuration recommendations for
internal PyTorch, vLLM, and Ollama backends.

Examples / Doctest:
    >>> from llm_api.hardware import GPUDeviceInfo, BackendRecommendation, get_backend_recommendations, HardwareProfile, HostCPUInfo
    >>> mock_gpu = GPUDeviceInfo(
    ...     index=0,
    ...     name="NVIDIA GeForce GTX 1660 Ti",
    ...     total_vram_mb=6144.0,
    ...     free_vram_mb=5120.0,
    ...     used_vram_mb=1024.0,
    ...     compute_capability=(7, 5),
    ...     supports_bf16=False,
    ...     supports_fp16=True,
    ...     supports_flash_attention=False
    ... )
    >>> mock_cpu = HostCPUInfo(physical_cores=6, total_threads=12, total_ram_mb=16384.0, available_ram_mb=12000.0)
    >>> mock_profile = HardwareProfile(cuda_available=True, devices=[mock_gpu], primary_device=mock_gpu, host_cpu=mock_cpu)
    >>> rec = get_backend_recommendations(profile=mock_profile, requested_context=4096)
    >>> rec.vllm_dtype
    'float16'
    >>> 0.70 <= rec.vllm_gpu_memory_utilization <= 0.76
    True
    >>> rec.recommended_quantization
    'nf4'
"""

from __future__ import annotations

import logging
import os
import psutil
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GPUDeviceInfo:
    """Detailed hardware metadata for an NVIDIA/CUDA device."""
    index: int
    name: str
    total_vram_mb: float
    free_vram_mb: float
    used_vram_mb: float
    compute_capability: tuple[int, int]
    supports_bf16: bool
    supports_fp16: bool
    supports_flash_attention: bool

    @property
    def total_vram_gb(self) -> float:
        return round(self.total_vram_mb / 1024.0, 2)

    @property
    def free_vram_gb(self) -> float:
        return round(self.free_vram_mb / 1024.0, 2)

    @property
    def compute_capability_str(self) -> str:
        return f"{self.compute_capability[0]}.{self.compute_capability[1]}"


@dataclass(frozen=True)
class HostCPUInfo:
    """Host processor and memory metrics."""
    physical_cores: int
    total_threads: int
    total_ram_mb: float
    available_ram_mb: float

    @property
    def total_ram_gb(self) -> float:
        return round(self.total_ram_mb / 1024.0, 2)

    @property
    def available_ram_gb(self) -> float:
        return round(self.available_ram_mb / 1024.0, 2)


@dataclass(frozen=True)
class HardwareProfile:
    """Full host compute profile."""
    cuda_available: bool
    devices: list[GPUDeviceInfo] = field(default_factory=list)
    primary_device: GPUDeviceInfo | None = None
    host_cpu: HostCPUInfo = field(default_factory=lambda: HostCPUInfo(1, 1, 1024.0, 512.0))

    @property
    def summary(self) -> str:
        if not self.cuda_available or not self.primary_device:
            return (
                f"CPU Mode: {self.host_cpu.physical_cores} cores / {self.host_cpu.total_threads} threads, "
                f"{self.host_cpu.available_ram_gb} GB RAM available."
            )
        dev = self.primary_device
        return (
            f"GPU: {dev.name} ({dev.free_vram_gb} GB free / {dev.total_vram_gb} GB total, "
            f"Compute {dev.compute_capability_str})"
        )


@dataclass(frozen=True)
class BackendRecommendation:
    """Recommended execution parameters for local AI engines."""
    vllm_gpu_memory_utilization: float
    vllm_max_model_len: int
    vllm_dtype: str
    recommended_quantization: str
    advisory_notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "vllm_gpu_memory_utilization": self.vllm_gpu_memory_utilization,
            "vllm_max_model_len": self.vllm_max_model_len,
            "vllm_dtype": self.vllm_dtype,
            "recommended_quantization": self.recommended_quantization,
            "advisory_notes": self.advisory_notes,
        }


def get_hardware_profile() -> HardwareProfile:
    """
    Inspects host system hardware using PyTorch CUDA and psutil.
    Safe against environments where CUDA is not present or drivers are missing.
    """
    # 1. CPU & System RAM
    vm = psutil.virtual_memory()
    host_cpu = HostCPUInfo(
        physical_cores=psutil.cpu_count(logical=False) or 1,
        total_threads=psutil.cpu_count(logical=True) or 1,
        total_ram_mb=round(vm.total / (1024.0 * 1024.0), 2),
        available_ram_mb=round(vm.available / (1024.0 * 1024.0), 2),
    )

    # 2. CUDA GPU Inspection
    devices: list[GPUDeviceInfo] = []
    cuda_available = False

    try:
        import torch
        if torch.cuda.is_available():
            cuda_available = True
            count = torch.cuda.device_count()
            for idx in range(count):
                name = torch.cuda.get_device_name(idx)
                cap = torch.cuda.get_device_capability(idx)
                major, minor = cap

                # Memory query: (free_bytes, total_bytes)
                try:
                    free_b, total_b = torch.cuda.mem_get_info(idx)
                except Exception as mem_err:
                    logger.warning(f"Could not query mem_get_info for CUDA device {idx}: {mem_err}")
                    free_b, total_b = (0, 0)

                total_mb = round(total_b / (1024.0 * 1024.0), 2)
                free_mb = round(free_b / (1024.0 * 1024.0), 2)
                used_mb = max(0.0, round(total_mb - free_mb, 2))

                # Precision feature support
                supports_bf16 = False
                try:
                    supports_bf16 = torch.cuda.is_bf16_supported() if hasattr(torch.cuda, "is_bf16_supported") else (major >= 8)
                except Exception:
                    supports_bf16 = (major >= 8)

                supports_fp16 = major >= 5
                supports_fa = major >= 8

                devices.append(
                    GPUDeviceInfo(
                        index=idx,
                        name=name,
                        total_vram_mb=total_mb,
                        free_vram_mb=free_mb,
                        used_vram_mb=used_mb,
                        compute_capability=(major, minor),
                        supports_bf16=supports_bf16,
                        supports_fp16=supports_fp16,
                        supports_flash_attention=supports_fa,
                    )
                )
    except Exception as e:
        logger.warning(f"Hardware inspection encountered an exception when probing CUDA: {e}")

    primary = devices[0] if devices else None
    return HardwareProfile(
        cuda_available=cuda_available,
        devices=devices,
        primary_device=primary,
        host_cpu=host_cpu,
    )


def get_backend_recommendations(
    profile: HardwareProfile | None = None,
    model_param_size_b: float | None = None,
    requested_context: int = 4096,
) -> BackendRecommendation:
    """
    Computes tailored settings for local inference backends given the current hardware profile.

    Args:
        profile: The HardwareProfile to evaluate (defaults to live detection).
        model_param_size_b: Estimated model parameter count in billions (e.g. 2.0, 7.0, 14.0).
        requested_context: Desired context length in tokens (default 4096).
    """
    if profile is None:
        profile = get_hardware_profile()

    notes: list[str] = []

    # CPU-only fallback
    if not profile.cuda_available or not profile.primary_device:
        notes.append("No CUDA GPU detected. Local inference will execute on the CPU.")
        notes.append("For CPU execution, Ollama with GGUF quantized models is strongly recommended.")
        return BackendRecommendation(
            vllm_gpu_memory_utilization=0.50,
            vllm_max_model_len=min(requested_context, 2048),
            vllm_dtype="float32",
            recommended_quantization="nf4",
            advisory_notes=notes,
        )

    dev = profile.primary_device
    major, minor = dev.compute_capability

    # 1. vLLM Memory Utilization
    # vLLM calculates utilization against TOTAL physical device memory.
    # We must leave headroom for the OS / display server (e.g. 512 MB).
    headroom_mb = 512.0
    usable_mb = max(0.0, dev.free_vram_mb - headroom_mb)
    if dev.total_vram_mb > 0:
        raw_util = usable_mb / dev.total_vram_mb
        # Clamp between 0.30 and 0.90
        vllm_util = round(min(0.90, max(0.30, raw_util)), 2)
    else:
        vllm_util = 0.70

    if dev.total_vram_gb <= 8.0:
        notes.append(
            f"Detected consumer/mobile GPU with {dev.total_vram_gb} GB VRAM ({dev.free_vram_gb} GB currently free). "
            f"vLLM memory utilization capped to {vllm_util} to avoid startup allocation crashes."
        )

    # 2. Context Window Sizing
    # Models on <= 6GB VRAM should constrain KV cache tokens
    if dev.total_vram_gb <= 6.5:
        max_context = min(requested_context, 4096)
        if requested_context > 4096:
            notes.append(
                f"Requested context length {requested_context} clamped to 4096 for 6 GB VRAM KV-cache preservation."
            )
    else:
        max_context = requested_context

    # 3. Precision / Dtype Selection
    if major < 8:
        vllm_dtype = "float16"
        notes.append(
            f"GPU Compute Capability {dev.compute_capability_str} (Turing/Pascal) lacks native BF16 instructions. "
            f"Configured float16 precision for compatibility and speed."
        )
    else:
        vllm_dtype = "auto"
        notes.append(
            f"GPU Compute Capability {dev.compute_capability_str} (Ampere or newer) supports native BF16."
        )

    # 4. Model Quantization Recommendation
    param_size = model_param_size_b if model_param_size_b is not None else 3.0
    if dev.total_vram_gb <= 8.0:
        if param_size >= 2.5:
            rec_quant = "nf4"
            notes.append(
                f"For models with ~{param_size}B+ parameters on {dev.total_vram_gb} GB VRAM, 4-bit NormalFloat (NF4) "
                f"or Ollama GGUF Q4_K_M is required to fit into VRAM."
            )
        else:
            rec_quant = "int8"
            notes.append(f"Small model (~{param_size}B) fits comfortably in 8-bit quantization.")
    elif dev.total_vram_gb <= 16.0:
        if param_size > 8.0:
            rec_quant = "nf4"
            notes.append(f"Models > 8B parameters require 4-bit quantization on {dev.total_vram_gb} GB VRAM.")
        else:
            rec_quant = "int8"
    else:
        rec_quant = "none"
        notes.append(f"High-capacity GPU ({dev.total_vram_gb} GB VRAM). Unquantized FP16/BF16 is fully viable.")

    return BackendRecommendation(
        vllm_gpu_memory_utilization=vllm_util,
        vllm_max_model_len=max_context,
        vllm_dtype=vllm_dtype,
        recommended_quantization=rec_quant,
        advisory_notes=notes,
    )
