"""AST seam regressions only: these are not HTTP/CLI/browser/admin execution tests."""

import ast
import unittest

from tests.shared.vk_research_journey import ROOT


def function(path, name, *, owner=None):
    tree = ast.parse((ROOT / path).read_text())
    if owner:
        tree = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == owner)
    return next(
        node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
    )


def calls(node):
    return {
        item.func.id if isinstance(item.func, ast.Name) else item.func.attr
        for item in ast.walk(node)
        if isinstance(item, ast.Call) and isinstance(item.func, (ast.Name, ast.Attribute))
    }


def is_confirmation_gate(test, op, value):
    """`confirmed <op> <value>` regardless of formatting or quote style."""
    return (
        isinstance(test, ast.Compare)
        and isinstance(test.left, ast.Name)
        and test.left.id == "confirmed"
        and [type(o) for o in test.ops] == [op]
        and isinstance(test.comparators[0], ast.Constant)
        and type(test.comparators[0].value) is type(value)
        and test.comparators[0].value == value
    )


class SurfaceSeamTests(unittest.TestCase):
    def test_all_five_writers_retain_shared_plan_preparation(self):
        cases = (
            ("app/api/v1/endpoints/tasks.py", "create_task", None, {"prepare_task_plan_create"}),
            ("app/web/task_create_routes.py", "task_create", None, {"prepare_task_plan_create"}),
            ("cli/commands/tasks.py", "task_add", None, {"prepare_task_plan_create"}),
            ("app/agent/toolset/tasks.py", "task_add", None, {"resolve_task_plan_create", "project_task_plan_create"}),
            (
                "app/admin/view_modules/operations.py",
                "on_model_change",
                "AgentTaskAdmin",
                {"resolve_task_plan_create", "project_task_plan_create"},
            ),
        )
        for path, name, owner, expected in cases:
            with self.subTest(surface=path):
                self.assertLessEqual(expected, calls(function(path, name, owner=owner)))

    def test_api_and_chat_run_delegate_to_one_launch_contract_with_request_identity(self):
        cases = (
            ("app/api/v1/endpoints/tasks.py", "run_task_plan"),
            ("app/agent/toolset/tasks.py", "task_execute_once"),
        )
        for path, name in cases:
            with self.subTest(path=path):
                node = function(path, name)
                launch = next(
                    n
                    for n in ast.walk(node)
                    if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "launch_social_task"
                )
                self.assertEqual({k.arg for k in launch.keywords}, {"request_id", "job_type"})
                self.assertIn("command.request_id", ast.unparse(launch))

    def test_previews_do_not_call_launch_collection_or_send(self):
        for path, name in (
            ("app/api/v1/endpoints/tasks.py", "preview_task_plan"),
            ("app/agent/toolset/tasks.py", "task_plan"),
            ("cli/commands/tasks.py", "task_plan_cmd"),
        ):
            with self.subTest(path=path):
                self.assertFalse(
                    calls(function(path, name))
                    & {"launch_social_task", "send_task_digest", "handle_collect", "handle_analyze"}
                )

    def test_cli_operator_and_explicit_tenant_boundaries_are_retained(self):
        path = "cli/commands/tasks.py"
        self.assertIn("_require_operator_context", calls(function(path, "_task_operator_boundary")))
        for name in ("_run_task", "task_update", "task_digest_send_cmd"):
            with self.subTest(name=name):
                self.assertIn("_require_explicit_tenant", calls(function(path, name)))

    def test_readers_use_shared_scoped_result_services(self):
        for path, name, expected in (
            ("app/api/v1/endpoints/tasks.py", "get_task_result", "task_result"),
            ("app/agent/toolset/tasks.py", "task_result", "read_result"),
            ("cli/commands/tasks.py", "task_result_cmd", "task_result"),
            ("app/web/task_routes.py", "task_detail", "task_configuration"),
        ):
            with self.subTest(path=path):
                self.assertIn(expected, calls(function(path, name)))

    def test_agent_delivery_tool_remains_confirmed_and_permission_bound(self):
        node = function("app/agent/toolset/tasks.py", "task_digest_send")
        decorator = next(
            d
            for d in node.decorator_list
            if isinstance(d, ast.Call) and isinstance(d.func, ast.Name) and d.func.id == "tool"
        )
        keywords = {k.arg: ast.literal_eval(k.value) for k in decorator.keywords}
        self.assertIs(keywords["confirm"], True)
        self.assertEqual(keywords["required_permission"], "notifications.digest_run.create")
        self.assertEqual(keywords["parameters"]["required"], ["task_id", "job_id"])

    def test_web_and_api_send_guards_precede_delivery_call(self):
        for path, name, op, value in (
            ("app/api/v1/endpoints/tasks.py", "send_composed_task_digest", ast.IsNot, True),
            ("app/web/task_routes.py", "task_send_generated_digest", ast.NotEq, "true"),
        ):
            with self.subTest(path=path):
                node = function(path, name)
                gate = next(n for n in node.body if isinstance(n, ast.If) and is_confirmation_gate(n.test, op, value))
                self.assertTrue(any(isinstance(n, (ast.Return, ast.Raise)) for n in ast.walk(gate)))
                send = next(
                    n
                    for n in ast.walk(node)
                    if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "send_task_digest"
                )
                self.assertLess(gate.end_lineno, send.lineno)


if __name__ == "__main__":
    unittest.main()
