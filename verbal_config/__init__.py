import warnings
warnings.filterwarnings("ignore", message=r".*allowed_objects.*")
warnings.filterwarnings("ignore", category=DeprecationWarning, module=r"langgraph.*")
warnings.filterwarnings("ignore", category=PendingDeprecationWarning, module=r"langgraph.*")

try:
    from llm_api.torch_compat import apply_torch_compatibility_shims
    apply_torch_compatibility_shims()
except Exception as _shim_err:
    import logging as _logging
    _logging.getLogger(__name__).warning("Torch compatibility shim skipped at package init: %s", _shim_err)