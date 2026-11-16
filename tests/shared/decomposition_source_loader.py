"""Reviewed P11-T13 source dependencies for isolated facade tests.

These are real implementations, not replacement behavior. Each dependency is
file-loaded under the SAME explicit infrastructure bindings as its facade;
normal app package initializers and sys.modules infrastructure are untouched.
Unknown dependencies still follow the original loader's normal import policy.
"""

from pathlib import Path

from tests.source_import_isolation import load_isolated_source

ROOT = Path(__file__).resolve().parents[2]
DEPENDENCIES = {
    "app/models/managers/base_manager.py": (
        "app.models.managers.lookup_compiler",
        "app.models.managers.query_compilation",
        "app.models.managers.relation_loading",
    ),
    "app/models/managers/query_compilation.py": ("app.models.managers.lookup_compiler",),
    "app/models/managers/relation_loading.py": ("app.models.managers.lookup_compiler",),
    "app/models/managers/job_manager.py": ("app.models.managers.job_lifecycle",),
    "app/models/managers/agent_task_manager.py": ("app.models.managers.task_inputs",),
    "app/jobs/handlers.py": ("app.jobs.job_handlers.digest", "app.jobs.job_handlers.prune"),
    "app/services/ai/analyzer.py": ("app.services.ai.analysis_stats",),
    "app/services/ai/reporting.py": ("app.services.ai.report_scope",),
    "app/services/ai/llm_client.py": ("app.services.ai.response_cost",),
}


def load_decomposed_source(name, path, imports=None, *, module_imports=None):
    """File-load only listed extraction dependencies, respecting caller doubles."""
    path = Path(path).resolve()
    relative = path.relative_to(ROOT).as_posix()
    bindings = dict(imports or {})
    for dependency in DEPENDENCIES.get(relative, ()):
        if dependency not in bindings:
            bindings[dependency] = load_decomposed_source(
                dependency, ROOT / (dependency.replace(".", "/") + ".py"), bindings
            )
    return load_isolated_source(name, path, bindings, module_imports=module_imports)
