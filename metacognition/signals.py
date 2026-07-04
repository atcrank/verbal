import typing
from django.db.models.signals import post_migrate
from django.dispatch import receiver


def parse_schema_docstring(docstring):
    """Parses custom metadata tags out of Python docstrings."""
    if not docstring:
        return {}
    
    parsed = {
        "description": [], "step_prompt": [], "evaluation_prompt": [],
        "prior_nodes": [], "following_nodes": [], "failure_nodes": []
    }
    
    current_key = "description"
    
    for line in docstring.strip().split('\n'):
        line_str = line.strip()
        lower_line = line_str.lower()
        
        if lower_line.startswith("step prompt:"):
            current_key = "step_prompt"
            parsed[current_key].append(line_str.split(":", 1)[1].strip())
        elif lower_line.startswith("evaluation prompt:"):
            current_key = "evaluation_prompt"
            parsed[current_key].append(line_str.split(":", 1)[1].strip())
        elif lower_line.startswith("prior nodes:"):
            current_key = "prior_nodes"
            parsed[current_key].append(line_str.split(":", 1)[1].strip())
        elif lower_line.startswith("following nodes:"):
            current_key = "following_nodes"
            parsed[current_key].append(line_str.split(":", 1)[1].strip())
        elif lower_line.startswith("failure nodes:"):
            current_key = "failure_nodes"
            parsed[current_key].append(line_str.split(":", 1)[1].strip())
        else:
            parsed[current_key].append(line_str)
            
    return {k: " ".join(v).strip() for k, v in parsed.items()}

