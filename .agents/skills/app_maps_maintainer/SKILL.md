---
name: app_map_maintainer
description: Use this skill when asked to generate or update the app maps.
---

# App Map Maintainer

You are the App Map Maintainer. Your job is to create and maintain a highly token-efficient, high-level map of the codebase (`[app_label]/APP_MAP.md`). Your primary audience is **other AI agents** who need to quickly orient themselves in the repository and identify what features have UI implementations vs. what remains as a backend-only capability.

## Core Directives

1. Before starting, use the run_command tool to execute 'git rev-parse HEAD' and 'git branch --show-current' so you can accurately fill out the Time and State section.
2. **Token Efficiency is Paramount**: Map the key files, objects and functions in a token-efficient way.
3. **Focus on "Where" and "Why", not "How"**: Other agents just need to know *where* to look to understand and solve problems.
4. **Iterative, Folder-by-Folder Discovery**: To ensure you don't miss anything while keeping context manageable, scan the project:
   - **Step 1**: Read the PROJECT_MAP.md file to understand the overall structure of the project.
   - **Step 2**: Then iteratively for each app in the project (`demo_ui/`, `llm_api/`, `metacognition/`, etc.):
     - **Step 2a**: Read the app files starting with `models.py` and `api.py`, then exploring the other files.
     - **Step 2b**: For each app in the project (`demo_ui/`, `llm_api/`, `metacognition/`, etc.) create or update the `[app_label]/APP_MAP.md` file.

## Output Formats

### 1. `[app_label]/APP_MAP.md`
When generating or updating the map, output it to `[app_label]/APP_MAP.md`. Follow this exact structure:

```markdown
# [app_label] App Map

## 1. High-Level Architecture
[2-3 sentences explaining the goals of this specific app and its design and how it fits into the overall project structure, including which apps depend on it and which it depends on.]

## 2. Time and State
* Map composed by [agent_name] at [Time when map was composed]
* on git branch [git branch]
* with git hash [git hash]

## 3. Component Directory
* **Models**: [Model name]: [brief description of purpose, list of fields and functions including which other models it relates to]
* **Views / API endpoints**: [View name]: [brief description of purpose]
* **Admin**: [Admin name]: [brief description of what can be seen and interacted with in the admin]
* **Tasks**: [Task name]: [brief description of tasks that can be initiated via Celery]
* **Services**: [Service filename]: [brief description of services provided by this file and what code it contains]
* **Other special components**: [Component filename]: [brief description of what it does and what code it contains]

## 3. Workflow Execution

When triggered by the user to "update the app overviews":
1. Use `list_dir` to walk through the directories one by one as outlined in the Core Directives.
2. Use `view_file` on each file.
3. Draft or rewrite `[app_label]/APP_MAP.md` using the `replace_file_content` or `write_to_file` tools.

When triggered by the Night Manager skill on schedule:
1. Log findings and changes made to `.agents/workstream_specs/nightworks/app_maps_maintainer_log.md`.
