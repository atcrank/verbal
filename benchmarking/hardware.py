"""
Hardware Detection & Adaptive Scaling for Fine-Tuning and Model Execution.

Dynamically inspects available host hardware (CUDA, VRAM capacity, compute capability,
multi-GPU topology, or CPU fallback) and computes optimal training hyperparameters.

This module ensures that QLoRA training and inference code are NEVER locked to the
specifics of a single development machine, scaling effortlessly from consumer GPUs
(e.g., 6GB VRAM) to enterprise accelerators (e.g., A100/H100 40GB-80GB).
"""

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Tuple

import torch

logger = logging.getLogger(__name__)


@dataclass
class HardwareProfile:
    """
    Representation of detected host compute and memory resources.

    >>> profile = HardwareProfile(device_type="cuda", total_vram_gb=24.0, compute_capability=(8, 6), device_name="RTX 3090")
    >>> profile.supports_bf16
    True
    >>> profile.tier
    'workstation'
    """
    device_type: Literal["cuda", "cpu", "mps"] = "cpu"
    device_name: str = "Host CPU"
    device_count: int = 1
    total_vram_gb: float = 0.0
    compute_capability: Tuple[int, int] = (0, 0)
    supports_bf16: bool = False
    supports_flash_attn: bool = False
    supports_4bit_quant: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.compute_capability >= (8, 0):
            self.supports_bf16 = True
            self.supports_flash_attn = True

    @property
    def tier(self) -> Literal["consumer", "workstation", "datacenter", "cpu"]:
        """Categorizes compute tier based on VRAM capacity."""
        if self.device_type != "cuda":
            return "cpu"
        if self.total_vram_gb >= 40.0:
            return "datacenter"
        if self.total_vram_gb >= 16.0:
            return "workstation"
        return "consumer"

    @property
    def cuda_available(self) -> bool:
        return self.device_type == "cuda"

    @property
    def bf16_supported(self) -> bool:
        return self.supports_bf16

    @property
    def flash_attn_supported(self) -> bool:
        return self.supports_flash_attn

    @property
    def cpu_threads(self) -> int:
        return self.metadata.get("cpu_threads", os.cpu_count() or 4)



@dataclass
class TrainingConfig:
    """
    Hardware-adapted hyperparameters for LoRA/QLoRA training.

    >>> cfg = TrainingConfig(batch_size=2, gradient_accumulation_steps=4, quantization="4bit")
    >>> cfg.effective_batch_size
    8
    """
    lora_r: int = 16
    lora_alpha: int = 32
    lora_dropout: float = 0.05
    target_modules: List[str] = field(default_factory=lambda: [
        "q_proj", "k_proj", "v_proj", "o_proj",
        "gate_proj", "up_proj", "down_proj"
    ])
    quantization: Literal["4bit", "8bit", "none"] = "4bit"
    precision: Literal["bf16", "fp16", "fp32"] = "fp16"
    batch_size: int = 1
    gradient_accumulation_steps: int = 8
    learning_rate: float = 2e-4
    num_train_epochs: int = 3
    max_seq_length: int = 2048
    gradient_checkpointing: bool = True
    optimizer: str = "paged_adamw_8bit"
    warmup_ratio: float = 0.03
    weight_decay: float = 0.01
    use_unsloth: bool = False

    @property
    def effective_batch_size(self) -> int:
        return self.batch_size * self.gradient_accumulation_steps

    @property
    def optim(self) -> str:
        return self.optimizer



def detect_hardware_profile() -> HardwareProfile:
    """
    Dynamically interrogates PyTorch and CUDA runtime to detect device capabilities.

    Returns:
        HardwareProfile describing the host's actual hardware characteristics.
    """
    if not torch.cuda.is_available():
        # Check Apple Silicon MPS
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            return HardwareProfile(
                device_type="mps",
                device_name="Apple Silicon (MPS)",
                device_count=1,
                total_vram_gb=16.0,  # Unified memory approximation
                compute_capability=(0, 0),
                supports_bf16=True,
                supports_flash_attn=False,
                supports_4bit_quant=False,
            )
        return HardwareProfile(
            device_type="cpu",
            device_name="Host CPU",
            device_count=1,
            total_vram_gb=0.0,
            compute_capability=(0, 0),
            supports_bf16=False,
            supports_flash_attn=False,
            supports_4bit_quant=False,
        )

    device_count = torch.cuda.device_count()
    device_name = torch.cuda.get_device_name(0)
    device_props = torch.cuda.get_device_properties(0)
    total_vram_gb = round(device_props.total_memory / (1024 ** 3), 2)
    compute_cap = (device_props.major, device_props.minor)

    # BF16 is supported on Ampere (8.0), Ada Lovelace (8.9), Hopper (9.0)+
    supports_bf16 = compute_cap >= (8, 0)

    # Flash Attention 2 requires Ampere+ and compatible package
    supports_flash_attn = supports_bf16

    return HardwareProfile(
        device_type="cuda",
        device_name=device_name,
        device_count=device_count,
        total_vram_gb=total_vram_gb,
        compute_capability=compute_cap,
        supports_bf16=supports_bf16,
        supports_flash_attn=supports_flash_attn,
        supports_4bit_quant=True,
        metadata={
            "cuda_version": torch.version.cuda or "unknown",
            "pytorch_version": torch.__version__,
            "multi_processor_count": getattr(device_props, "multi_processor_count", 0),
        }
    )


def recommend_training_config(
    hardware: Optional[HardwareProfile] = None,
    base_model_id: str = "default",
    max_seq_length: Optional[int] = None,
    user_overrides: Optional[Dict[str, Any]] = None,
) -> TrainingConfig:
    """
    Computes optimal, hardware-adapted training hyperparameters.

    Adapts quantization, batch size, gradient accumulation, precision, and
    context length based on detected VRAM and compute architecture.

    >>> hw = HardwareProfile(device_type="cuda", total_vram_gb=80.0, compute_capability=(9, 0), supports_bf16=True)
    >>> cfg = recommend_training_config(hw)
    >>> cfg.precision
    'bf16'
    >>> cfg.batch_size >= 4
    True
    >>> cfg.quantization in ['none', '8bit', '4bit']
    True
    """
    if hardware is None:
        hardware = detect_hardware_profile()

    user_overrides = user_overrides or {}

    # CPU or Non-CUDA Fallback
    if hardware.device_type != "cuda":
        cfg = TrainingConfig(
            quantization="none",
            precision="fp32",
            batch_size=1,
            gradient_accumulation_steps=1,
            gradient_checkpointing=False,
            optimizer="adamw_torch",
            max_seq_length=max_seq_length or 512,
        )
        for k, v in user_overrides.items():
            if hasattr(cfg, k):
                setattr(cfg, k, v)
        return cfg

    # 1. Quantization & Precision
    if hardware.supports_bf16:
        precision = "bf16"
    else:
        precision = "fp16"

    # 2. Scaling by VRAM Capacity
    vram = hardware.total_vram_gb

    if vram >= 40.0:  # e.g., A100 40GB/80GB, H100
        quantization = "none" if vram >= 70.0 else "4bit"
        batch_size = 8
        grad_accum = 2
        grad_checkpointing = False
        seq_len = max_seq_length or 4096
        optimizer = "adamw_torch" if quantization == "none" else "paged_adamw_8bit"

    elif vram >= 16.0:  # e.g., RTX 3090, RTX 4090, A4000 (16-24GB)
        quantization = "4bit"
        batch_size = 4
        grad_accum = 4
        grad_checkpointing = True
        seq_len = max_seq_length or 2048
        optimizer = "paged_adamw_8bit"

    elif vram >= 10.0:  # e.g., RTX 3080 10GB/12GB
        quantization = "4bit"
        batch_size = 2
        grad_accum = 8
        grad_checkpointing = True
        seq_len = max_seq_length or 2048
        optimizer = "paged_adamw_8bit"

    else:  # e.g., 6GB-8GB consumer/mobile (e.g. GTX 1660 Ti, RTX 3060 mobile)
        quantization = "4bit"
        batch_size = 1
        grad_accum = 16
        grad_checkpointing = True
        seq_len = max_seq_length or 1024
        optimizer = "paged_adamw_8bit"

    config = TrainingConfig(
        lora_r=16,
        lora_alpha=32,
        lora_dropout=0.05,
        quantization=quantization,
        precision=precision,
        batch_size=batch_size,
        gradient_accumulation_steps=grad_accum,
        learning_rate=2e-4,
        num_train_epochs=3,
        max_seq_length=seq_len,
        gradient_checkpointing=grad_checkpointing,
        optimizer=optimizer,
    )

    # Apply any explicit user overrides
    for key, val in user_overrides.items():
        if hasattr(config, key) and val is not None:
            setattr(config, key, val)

    logger.info(
        f"Hardware Auto-Tune | Tier: {hardware.tier.upper()} ({hardware.device_name}, {hardware.total_vram_gb} GB) -> "
        f"Batch: {config.batch_size}, GradAccum: {config.gradient_accumulation_steps} "
        f"(Eff: {config.effective_batch_size}), Precision: {config.precision}, Quant: {config.quantization}"
    )

    return config
