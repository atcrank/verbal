import warnings
warnings.filterwarnings("ignore", message=r".*allowed_objects.*")
warnings.filterwarnings("ignore", category=DeprecationWarning, module=r"langgraph.*")
warnings.filterwarnings("ignore", category=PendingDeprecationWarning, module=r"langgraph.*")

try:
    from llm_api.torch_compat import apply_torch_compatibility_shims
    apply_torch_compatibility_shims()
except Exception:
    pass