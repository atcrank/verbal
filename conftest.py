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
            # rag_service.force_reindex_all()  # Disabled to speed up testing and prevent hangs on inference server
            
            from metacognition.seed import seed_all
            seed_all()
        except Exception as e:
            logger.info(f'\n⚠️ Could not load test_data.json fixture or seed data: {e}')

@pytest.fixture(scope='session', autouse=True)
def force_test_db_disconnect(django_db_setup, django_db_blocker):
    """
    Session-scoped fixture that yields during tests and forcefully severs
    all active Postgres connections to the test database right before teardown.
    Because it depends on django_db_setup, its teardown (post-yield) runs 
    strictly BEFORE pytest-django attempts to drop the database.
    """
    yield
    with django_db_blocker.unblock():
        from django.db import connection
        try:
            with connection.cursor() as cursor:
                cursor.execute('''
                    SELECT pg_terminate_backend(pid)
                    FROM pg_stat_activity
                    WHERE datname = current_database()
                      AND pid <> pg_backend_pid();
                ''')
        except Exception as e:
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

def pytest_sessionstart(session):
    """Verify the inference server is reachable before running tests."""
    from django.conf import settings
    inf_url = getattr(settings, "INFERENCE_URL", "http://127.0.0.1:8001/api/llm")
    ping_url = f"{inf_url.rstrip('/')}/internal/ping/"
    fallback_url = "http://127.0.0.1:8000/api/llm/internal/ping/"
    
    server_found = False
    for url in [ping_url, fallback_url]:
        try:
            resp = requests.get(url, timeout=2)
            if resp.status_code in [200, 400, 404, 405]:
                server_found = True
                break
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
            continue
            
    if not server_found:
        logger.info(f'\n❌ CRITICAL: The local inference server is NOT running at {inf_url}. Please start it via start_inference.sh\n')
        sys.exit(1)

def _report_doctest_run(test_name, prompt, result):
    """Helper function to document the agent's run."""
    os.makedirs("doctest_reports", exist_ok=True)
    filename = f"metacognition/metacognition_trials/{test_name}_report.rst"
    
    with open(filename, "w", encoding="utf-8") as f:
        report_title = f"Doctest Report: {test_name}\n"
        f.write(f"{"=" * len(report_title)}\n")
        f.write(report_title)
        f.write(f"{"=" * len(report_title)}\n\n")
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

        if cid:
            try:
                from llm_api.models import Conversation
                current_conv = Conversation.objects.get(id=cid)
                superseded = Conversation.objects.filter(
                    user=current_conv.user,
                    title=current_conv.title
                ).exclude(id=cid)
                if superseded.exists():
                    superseded.delete()
            except Exception as e:
                logger.error(f"Failed to clean up superseded conversations: {e}")

        def write_steps(f, steps, prefix_str="Step", indent=""):
            for i, step_entry in enumerate(steps):
                step_num = f"{prefix_str} {i+1}"
                f.write(f"{indent}**{step_num}: {step_entry.get('step_name', 'Unknown')}**\n\n")
                
                model_name = step_entry.get("model_name")
                if model_name:
                    f.write(f"{indent}*(Generated by: {model_name})*\n\n")

                if "system_prompt" in step_entry and step_entry["system_prompt"]:
                    f.write(f"{indent}*System*:\n")
                    for line in str(step_entry["system_prompt"]).splitlines():
                        f.write(f"{indent}  {line}\n")
                    f.write("\n")

                if "user_prompt" in step_entry and step_entry["user_prompt"] and str(step_entry["user_prompt"]).strip() != "[]":
                    f.write(f"{indent}*User*:\n")
                    for line in str(step_entry["user_prompt"]).splitlines():
                        f.write(f"{indent}  {line}\n")
                    f.write("\n")

                if "output" in step_entry and step_entry["output"]:
                    f.write(f"{indent}*Assistant*:\n")
                    for line in str(step_entry["output"]).splitlines():
                        f.write(f"{indent}  {line}\n")
                    f.write("\n")

                if "tool_result" in step_entry and step_entry["tool_result"]:
                    f.write(f"{indent}*Tool Output*:\n")
                    for line in str(step_entry["tool_result"]).splitlines():
                        f.write(f"{indent}  {line}\n")
                    f.write("\n")

                if "sub_monologue" in step_entry and step_entry["sub_monologue"]:
                    f.write(f"{indent}  --- Sub-Blueprint Trace ---\n\n")
                    write_steps(f, step_entry["sub_monologue"], prefix_str=f"{step_num}.", indent=indent + "  ")
                    f.write(f"{indent}  ---------------------------\n\n")

        if monologue:
            write_steps(f, monologue)
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