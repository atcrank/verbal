import logging
logger = logging.getLogger(__name__)

import pytest
import sys
import requests
import os
import datetime
from django.core.management import call_command

from llm_api.models import Conversation


@pytest.fixture(autouse=True)
def enable_db_access_for_doctests(db):
    """
    pytest-django strictly blocks database access by default.
    This autouse fixture grants DB access to all tests, including our .rst doctests.
    """
    pass

@pytest.fixture(scope='session')
def django_db_setup(django_db_setup, django_db_blocker):
    """Loads the database fixture once per test session."""
    with django_db_blocker.unblock():
        try:
            call_command('loaddata', 'test_data.json')
            from background_resources.models import RAGChunk
            RAGChunk.objects.all().update(in_vector_index=False)
            from llm_api.apps import service_registry
            rag_service = service_registry.rag_service
            rag_service.force_reindex_all()
            
            from metacognition.seed import seed_all
            seed_all()
        except Exception as e:
            logger.info(f'\n⚠️ Could not load test_data.json fixture or seed data: {e}')


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

def pytest_sessionstart(session):
    """Verify the inference server is reachable before running tests."""
    try:
        # We expect a 405 Method Not Allowed or 400 Bad Request if the server is up and listening.
        requests.get("http://localhost:8000/api/llm/v1/chat/completions", timeout=2)
    except requests.exceptions.ConnectionError:
        logger.info('\n❌ CRITICAL: The local inference server is NOT running. Please start it.\n')
        sys.exit(1)

def _report_doctest_run(test_name, prompt, result):
    """Helper function to document the agent's run."""
    os.makedirs("doctest_reports", exist_ok=True)
    filename = f"metacognition/metacognition_trials/{test_name}_report.rst"
    
    with open(filename, "w", encoding="utf-8") as f:
        f.write(f"========================================\n")
        f.write(f"Doctest Report: {test_name}\n")
        f.write(f"========================================\n\n")
        f.write(f"Date: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        
        f.write("Conversation Prompts\n--------------------\n\n::\n\n")
        
        prompts = prompt if isinstance(prompt, list) else [prompt]
        for i, p in enumerate(prompts):
            f.write(f"    [Turn {i+1}]:\n")
            for line in p.splitlines():
                f.write(f"    {line}\n")
            f.write("\n")


        # 1. NEW: Print the captured stdout from the sandbox and actions
        stdout = result.get("stdout", "")
        if stdout:
            f.write("Standard Output (Prints & Logs)\n-------------------------------\n\n::\n\n")
            for line in stdout.splitlines():
                f.write(f"    {line}\n")
            f.write("\n")

        # 2. Refactored: Reconstruct the execution trace purely from LangGraph's state
        f.write("Execution Trace\n---------------\n\n")
        monologue = result.get("internal_monologue", [])
        cid = result.get("conversation_id")

        if monologue:
            for i, step_entry in enumerate(monologue):
                step_num = i + 1
                f.write(f"Step {step_num}\n^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\n\n")

                if "system_prompt" in step_entry:
                    f.write("**System**:\n\n::\n\n")
                    for line in str(step_entry["system_prompt"]).splitlines():
                        f.write(f"    {line}\n")
                    f.write("\n")

                if "user_prompt" in step_entry:
                    f.write("**User**:\n\n::\n\n")
                    for line in str(step_entry["user_prompt"]).splitlines():
                        f.write(f"    {line}\n")
                    f.write("\n")

                f.write("**Assistant**:\n\n::\n\n")
                for line in str(step_entry.get("output", "")).splitlines():
                    f.write(f"    {line}\n")
                f.write("\n")

                if "tool_result" in step_entry and step_entry["tool_result"]:
                    f.write("**Tool Output**:\n\n::\n\n")
                    for line in str(step_entry["tool_result"]).splitlines():
                        f.write(f"    {line}\n")
                    f.write("\n")
        else:
            f.write("No granular steps recorded in result.\n\n")

        f.write("Final Response\n--------------\n\n::\n\n")
        for line in str(result.get("final_response", "")).splitlines():
            f.write(f"    {line}\n")
        f.write("\n")

        f.write("Generated Workspace Files\n-------------------------\n\n")
        workspace_dir = os.path.join("workspaces", str(cid)) if cid else None
        if workspace_dir and os.path.exists(workspace_dir):
            for root, dirs, files in os.walk(workspace_dir):
                if ".git" in root: continue
                for file in files:
                    file_path = os.path.join(root, file)
                    rel_path = os.path.relpath(file_path, workspace_dir)
                    f.write(f"File: {rel_path}\n^^^^^^^^^^^^^^^^^^^^\n\n::\n\n")
                    try:
                        with open(file_path, 'r', encoding='utf-8') as code_file:
                            for c_line in code_file.read().splitlines():
                                f.write(f"    {c_line}\n")
                    except Exception as e:
                        f.write(f"    [Error reading file: {e}]\n")
                    f.write("\n")

@pytest.fixture(autouse=True)
def inject_helpers_into_doctests(doctest_namespace):
    """Injects utility functions directly into the doctest global namespace."""
    doctest_namespace["report_doctest_run"] = _report_doctest_run