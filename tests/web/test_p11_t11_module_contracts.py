"""Public integration seams for the split web task modules."""

from typing import get_type_hints

from app.models import AgentTask, Job
from app.web import job_routes, task_create_routes, task_forms, task_presenters, task_routes, task_use_cases, tasks


def test_moved_model_annotations_resolve_for_runtime_introspection():
    """Split modules keep the names needed by typing.get_type_hints."""
    assert get_type_hints(task_presenters._job_summary)["job"] is Job
    assert get_type_hints(task_presenters._cancel_info)["job"] is Job
    assert get_type_hints(task_use_cases.run_task_now)["task"] is AgentTask


def test_compatibility_facade_reexports_canonical_objects():
    assert tasks.router is task_routes.router
    assert tasks.router is task_create_routes.router
    assert tasks.router is job_routes.router
    assert tasks._job_summary is task_presenters._job_summary
    assert tasks._check_task_sources is task_forms._check_task_sources
    assert tasks.AgentTask is AgentTask
    assert tasks.Job is Job


def test_task_router_has_stable_methods_paths_and_handler_order():
    expected = [
        ("POST", "/tasks", "task_create"),
        ("GET", "/tasks/vk-connections", "web_vk_connections"),
        ("GET", "/tasks/", "tasks_list"),
        ("GET", "/tasks", "tasks_list"),
        ("GET", "/tasks/{task_id}", "task_detail"),
        ("GET", "/tasks/{task_id}/latest-progress", "task_latest_progress"),
        ("GET", "/tasks/{task_id}/edit", "task_edit_page"),
        ("POST", "/tasks/{task_id}/toggle", "task_toggle"),
        ("POST", "/tasks/{task_id}/run-now", "task_run_now"),
        ("POST", "/tasks/{task_id}/retry-analysis", "task_retry_analysis"),
        ("POST", "/tasks/{task_id}/digest/{job_id}/send", "task_send_generated_digest"),
        ("POST", "/tasks/{task_id}", "task_update"),
        ("POST", "/tasks/{task_id}/delete", "task_delete"),
        ("GET", "/tasks/job/{job_id}/status", "job_status"),
        ("POST", "/tasks/job/{job_id}/cancel", "job_cancel_from_modal"),
    ]
    actual = [
        (method, route.path, route.endpoint.__name__)
        for route in tasks.router.routes
        for method in sorted(route.methods or ())
    ]
    assert actual == expected
    assert len({endpoint for _, _, endpoint in expected}) == 14


def test_route_modules_register_into_the_facade_router_once():
    route_endpoints = [route.endpoint for route in tasks.router.routes]
    assert route_endpoints.count(task_create_routes.task_create) == 1
    assert route_endpoints.count(task_routes.web_vk_connections) == 1
    assert route_endpoints.count(job_routes.job_status) == 1
    assert route_endpoints.count(job_routes.job_cancel_from_modal) == 1
