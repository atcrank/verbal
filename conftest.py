import pytest

@pytest.fixture(autouse=True)
def enable_db_access_for_doctests(db):
    """
    pytest-django strictly blocks database access by default.
    This autouse fixture grants DB access to all tests, including our .rst doctests.
    """
    pass


@pytest.fixture(autouse=True)
def force_proxy_for_tests():
    """
    Forces the AIService to act as a proxy client ('web' role) during testing.
    This prevents the test suite from attempting to load heavy LLMs into VRAM,
    and instead routes requests to the already-running local inference server.
    """
    from llm_api.ai_service import AIService
    original_role = AIService.role
    AIService.role = "web"
    yield
    AIService.role = original_role