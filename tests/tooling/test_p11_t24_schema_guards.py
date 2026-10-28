"""P11-T24: the guard DDL module must cover every guard object migration history creates.

Tool-only: migrations are parsed with AST (never imported — they read settings
and bind Alembic at module scope) and ``scripts/schema_guards.py`` is loaded by
path, so the offline lane never needs DB settings or a driver.

How a migration's guard SQL is read (nothing is executed):

* Literal constants, the build-schema name (``SCHEMA``/``schema = q(SCHEMA)``)
  and ``qualified = _q(table)``-style locals resolve first; a helper's
  parameters resolve through its call sites, positional or keyword.
* Each interpolation of an f-string stays a marker in the static text plus the
  source of its expression. An expression built from literals and helper
  parameters renders symbolically: ``_q('reject_' + stem + '_mutation')`` over
  the parameter value ``t`` becomes ``x.reject_t_mutation``, the name the
  migration really creates in the build schema ``x``.
* A conditional block chosen by a literal argument (0103) renders the branch
  that argument selects; a local built from parameters resolves at its use.
* A ``for`` loop over a literal table tuple expands over that tuple, otherwise
  a helper's table parameters expand over its call sites. A guard trigger left
  on an unqualified ``ON`` target is a query-built name and is skipped: it is
  not an object this module can carry.
"""

import ast
import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "migrations/versions"
# scripts is a package; register the module before exec so dataclasses resolve
# their own module. Nothing in it imports app, Alembic or a driver.
SPEC = importlib.util.spec_from_file_location("scripts.schema_guards", ROOT / "scripts/schema_guards.py")
GUARDS = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = GUARDS
SPEC.loader.exec_module(GUARDS)

# Bulk-guarded tables and the guard each ``{table}_validate`` trigger executes.
# Mirrors the module's ``_validate`` triggers one-for-one; the equality test
# checks both directions. The ``llm_*`` tables are guarded one-off instead.
# The ``llm_*`` tables carry their own immutable/no-truncate guards instead.
ONE_OFF_TABLES = {"llm_dispatch_receipts", "llm_outcome_events", "llm_budget_reservations"}
BULK_TABLES = {
    "personal_llm_dispatches": "check_personal_llm_dispatch_account",
    "task_billing_payers": "check_task_billing_payer_consent",
    "personal_llm_bound_policies": "check_personal_llm_bound_policy",
    "personal_llm_reservations": "check_personal_llm_reservation",
    "personal_llm_ledger_entries": "check_personal_llm_ledger_entry",
    "personal_llm_billing_incidents": "check_personal_llm_billing_incident",
    "personal_billing_cutovers": "check_personal_billing_cutover",
    "personal_legacy_attribution_consents": "check_personal_legacy_attribution_consent",
    "personal_legacy_attributions": "check_personal_legacy_attribution_review",
}

# Helper parameters that hold a table name; a loop variable is the same kind.
_TABLE_PARAMS = ("table", "stem")
_SCHEMA = "x"
# The schema placeholder must be an identifier character so ``x.name`` (a
# function in the build schema) and ``x.{name}`` (a built one) stay distinct.
assert _SCHEMA.isidentifier()

_CREATE_FUNCTION = re.compile(
    r"CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+(?P<name>[A-Za-z_][\w.]*)\s*\([^)]*\)\s*RETURNS\s+trigger", re.IGNORECASE
)
_CREATE_TRIGGER = re.compile(r"CREATE\s+TRIGGER\s+(?P<name>[A-Za-z_]\w*)\s+(?P<rest>.*)", re.IGNORECASE | re.DOTALL)
# Identifiers are ``[\w$]``: a bare word would stop at the first underscore.
_TRIGGER_ON = re.compile(r"\bON\s+(?P<table>[A-Za-z_][\w$.]*)\s+FOR\s+EACH\b", re.IGNORECASE)
_TRIGGER_EVENTS = re.compile(r"\bBEFORE\s+(.+?)\s+ON\s+(?:[A-Za-z_][\w$.]*)", re.IGNORECASE | re.DOTALL)
_TRIGGER_LEVEL = re.compile(r"FOR\s+EACH\s+(ROW|STATEMENT)", re.IGNORECASE)
_TRIGGER_FUNCTION = re.compile(r"EXECUTE\s+(?:FUNCTION|PROCEDURE)\s+([A-Za-z_][\w.]*)\(", re.IGNORECASE)
# Marks an interpolation the parse cannot render yet; unlike ``{}`` it never
# occurs inside literal SQL.
_MARKER = "\u241e"
# ``match`` anchors at the start: a multi-line string starts with indentation.
_GUARD_DDL = re.compile(r"\s*CREATE\s+(?:OR\s+REPLACE\s+)?(?:TRIGGER|FUNCTION)", re.IGNORECASE)


class _Scope:
    """Names visible inside one migration module, wherever a function defines them.

    ``text``: names with fixed SQL text (constants, the build schema).
    ``quoted``: names holding a helper parameter as ``schema.<parameter>``.
    ``expressions``: locals built from other locals, resolved at their use site.
    ``flags``: literal booleans that select a conditional SQL block.
    """

    def __init__(self, tree):
        self.text = {}
        self.quoted = {}
        self.flags = {}
        self.expressions = {}
        self.placeholders = set()
        # ``SCHEMA = settings.DB_SCHEMA``: the build schema, whatever its name
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Attribute):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        self.placeholders.add(target.id)
        assignments = [node for node in ast.walk(tree) if isinstance(node, ast.Assign)]
        for node in assignments:
            for target in node.targets:
                if isinstance(target, ast.Name) and self._is_schema(node.value):
                    self.text.setdefault(target.id, _SCHEMA)
        # The build schema reaches SQL quoted too: ``schema = q(SCHEMA)``
        for node in assignments:
            for target in node.targets:
                if isinstance(target, ast.Name) and self._names_schema(node.value):
                    self.text.setdefault(target.id, _SCHEMA)
        for node in assignments:
            value = node.value
            for target in node.targets:
                if not isinstance(target, ast.Name) or target.id in self.text:
                    continue
                if _is_call(value, "_q", "_qualified") and value.args:
                    # ``qualified = _q(table)``: a name built from a parameter
                    self.quoted.setdefault(target.id, value.args[0])
                elif not isinstance(value, ast.Constant):
                    # Built from helper parameters: only a use site can resolve it
                    self.expressions.setdefault(target.id, value)
                elif isinstance(value.value, str):
                    self.text.setdefault(target.id, value.value)
                elif isinstance(value.value, bool):
                    self.flags.setdefault(target.id, value.value)

    def _is_schema(self, node):
        return isinstance(node, ast.Name) and node.id in self.placeholders

    def _names_schema(self, node):
        """``q(SCHEMA)`` / ``quote_schema(schema)``: a quoted build-schema name."""
        if not (isinstance(node, ast.Call) and node.args):
            return False
        function = node.func
        name = function.id if isinstance(function, ast.Name) else getattr(function, "attr", "")
        if "quote" not in name:
            return False
        argument = node.args[0]
        return isinstance(argument, ast.Name) and (
            argument.id in self.placeholders or self.text.get(argument.id) == _SCHEMA
        )


class _Binding:
    """Helper parameters at one call site: rendered text plus literal flags."""

    def __init__(self, values=None, flags=None):
        self.values = dict(values or {})
        self.flags = dict(flags or {})

    def __contains__(self, name):
        return name in self.values

    def __getitem__(self, name):
        return self.values[name]

    def fill(self, parameter, node, scope):
        """Bind one argument: a literal text (and its truth), or a rendered name."""
        if isinstance(node, ast.Constant):
            self.values[parameter] = str(node.value)
            if isinstance(node.value, bool):
                self.flags[parameter] = node.value
            return
        rendered = _render(node, scope, self)
        if rendered is not None:
            self.values[parameter] = rendered


def _is_call(node, *names):
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in names


def _condition(node, scope, binding):
    """Literal truth of a conditional expression's test, or ``None`` when unknown."""
    if isinstance(node, ast.Constant) and isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in binding.flags:
            return binding.flags[node.id]
        return scope.flags.get(node.id)
    return None


def _render(node, scope, binding=None):
    """Symbolic text of a string expression, or ``None`` when it is run-time only.

    The schema renders as ``x``. A helper parameter renders as its binding, or
    as the schema-qualified ``x.<parameter>`` when a local quotes it
    (``qualified = _q(table)``) and no binding is in hand.
    """
    binding = binding or _Binding()
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.FormattedValue):
        return _render(node.value, scope, binding)
    if isinstance(node, ast.Name):
        if node.id in binding:
            return binding[node.id]
        if node.id in scope.text:
            return scope.text[node.id]
        if node.id in scope.quoted:
            aliased = _render(scope.quoted[node.id], scope, binding)
            if aliased is None:
                return None
            return aliased if aliased.startswith(_SCHEMA + ".") else f"{_SCHEMA}.{aliased}"
        if node.id in scope.expressions:
            return _render(scope.expressions[node.id], scope, binding)
        return None
    if isinstance(node, ast.JoinedStr):
        pieces = [_render(part, scope, binding) for part in node.values]
        return None if any(piece is None for piece in pieces) else "".join(pieces)
    if _is_call(node, "f"):
        return _render(node.args[0], scope, binding) if node.args else None
    if _is_call(node, "_q", "_qualified"):
        rendered = _render(node.args[0], scope, binding) if node.args else None
        return None if rendered is None else f"{_SCHEMA}.{rendered}"
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _render(node.left, scope, binding), _render(node.right, scope, binding)
        return None if left is None or right is None else left + right
    if isinstance(node, ast.IfExp):
        # ``body if flag else other``: the flag is a literal at the call site
        condition = _condition(node.test, scope, binding)
        if condition is None:
            return None
        return _render(node.body if condition else node.orelse, scope, binding)
    return None


def _fragments(node, scope):
    """``(sql, unresolved)``: static text with each unrenderable interpolation as ``{}``.

    ``{}`` also occurs in literal SQL (``'{}'::jsonb``), so each unresolved
    interpolation is kept as a marker: the count always matches the list.
    """
    if isinstance(node, ast.JoinedStr):
        sql, unresolved = [], []
        for part in node.values:
            rendered = _render(part, scope)
            if rendered is None:
                sql.append(f"{_MARKER}{len(unresolved)}{_MARKER}")
                unresolved.append(part)
            else:
                sql.append(rendered)
        return "".join(sql), unresolved
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _fragments(node.left, scope), _fragments(node.right, scope)
        return left[0] + right[0], left[1] + right[1]
    rendered = _render(node, scope)
    return (f"{_MARKER}0{_MARKER}", [node]) if rendered is None else (rendered, [])


def _definition(tree, name):
    return next((node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name), None)


def _parameters(definition):
    return [argument.arg for argument in definition.args.args]


def _reachable_functions(tree, entrypoint="upgrade"):
    """Functions reachable from a migration entrypoint through direct calls."""
    reachable = set()
    pending = [entrypoint]
    while pending:
        name = pending.pop()
        if name in reachable:
            continue
        reachable.add(name)
        definition = _definition(tree, name)
        if definition is None:
            continue
        for node in ast.walk(definition):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if _definition(tree, node.func.id) is not None:
                    pending.append(node.func.id)
    return reachable


def _call_sites(tree, name, reachable=None):
    """``{parameter: literal or symbol}`` at reachable calls to ``name``."""
    definition = _definition(tree, name)
    if definition is None:
        return []
    parameters = _parameters(definition)
    defaults = dict(zip(parameters[len(parameters) - len(definition.args.defaults) :], definition.args.defaults))
    scope = _Scope(tree)
    owners = {}
    for definition_node in ast.walk(tree):
        if isinstance(definition_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for child in ast.walk(definition_node):
                owners.setdefault(child, definition_node.name)
    sites = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == name):
            continue
        arguments = dict(zip(parameters, node.args), **defaults)
        arguments.update({keyword.arg: keyword.value for keyword in node.keywords if keyword.arg})
        binding = _Binding()
        for parameter, value in arguments.items():
            binding.fill(parameter, value, scope)
        caller = owners.get(node)
        if reachable is None or caller in reachable:
            sites.append(binding)
    return sites


def _loop_values(tree, function, name):
    """Literal tuple a ``for name in (...)`` loop iterates in ``function``."""
    definition = _definition(tree, function)
    if definition is None:
        return None
    for node in ast.walk(definition):
        if not (isinstance(node, ast.For) and isinstance(node.target, ast.Name) and node.target.id == name):
            continue
        if isinstance(node.iter, (ast.Tuple, ast.List)):
            values = [element.value for element in node.iter.elts if isinstance(element, ast.Constant)]
            if len(values) == len(node.iter.elts):
                return values
    return None


def _executes(tree):
    """``(owning function, sql, unresolved interpolations)`` for each static ``op.execute``."""
    scope = _Scope(tree)
    owner = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for child in ast.walk(node):
                owner.setdefault(child, node.name)
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "execute"):
            continue
        if not node.args:
            continue
        argument = node.args[0]
        if isinstance(argument, ast.Call):  # sa.text(<text>)
            argument = argument.args[0] if argument.args else None
        if argument is not None:
            yield owner.get(node, ""), *_fragments(argument, scope)


def _expand(sql, unresolved, scope, binding):
    """Fill the unrenderable interpolations of ``sql``; a run-time one becomes ``x``."""
    rendered = []
    for piece in re.split(f"({_MARKER}\\d+{_MARKER})", sql):
        if piece.startswith(_MARKER):
            index = int(piece[len(_MARKER) : -len(_MARKER)])
            rendered.append(_render(unresolved[index], scope, binding) or _SCHEMA)
        else:
            rendered.append(piece)
    return "".join(rendered)


def _statements(sql, unresolved, function, tree):
    """Every statement one ``op.execute`` text stands for.

    A helper's ``table``/``stem`` parameter expands over its call sites, a
    ``for`` loop over its literal tuple; a remaining interpolation is the schema.
    """
    if not unresolved:
        return {sql}
    scope = _Scope(tree)
    names = {child.id for node in unresolved for child in ast.walk(node) if isinstance(child, ast.Name)}
    # A helper-built name (``function = _q('reject_' + stem + '_mutation')``)
    # or a local alias hides the parameter, so the parameter is only a single
    # table when every interpolation mentions it directly.
    table_parameter = None
    # An alias of a module constant is resolved, not shared.
    shared = {alias for alias, value in scope.quoted.items() if _render(value, scope) is None}
    # A local built from helper parameters only resolves with a call-site binding.
    needs_arguments = names & (shared | set(scope.expressions))
    direct = not needs_arguments and not names & set(scope.text)
    if direct:
        table_parameter = next((name for name in _TABLE_PARAMS if name in names), None)
    if direct and table_parameter is None:
        return {_expand(sql, unresolved, scope, _Binding())}
    loops = _loop_values(tree, function, table_parameter)
    bindings = [_Binding({table_parameter: value}) for value in loops] if loops else []
    if not bindings:
        bindings = _call_sites(tree, function, _reachable_functions(tree)) if function else []
    if not bindings:
        raise ValueError(f"Cannot resolve migration call arguments for {function!r}")
    # Locals of this function that are built from its parameters
    for binding in bindings:
        for name in sorted(names):
            expression = scope.expressions.get(name)
            if name not in binding and expression is not None and function:
                rendered = _render(expression, scope, binding)
                if rendered is not None:
                    binding.fill(name, ast.Constant(rendered), scope)
        for name in sorted(names & set(scope.quoted)):
            _bind_quoted(scope, binding, name)
    return {_expand(sql, unresolved, scope, binding) for binding in bindings}


def _bind_quoted(scope, binding, name):
    """Resolve ``qualified = _q(table)`` into ``binding`` once its parameter is bound."""
    parameter = scope.quoted[name]
    argument = parameter.id if isinstance(parameter, ast.Name) else None
    resolved = binding[argument] if argument in binding else None
    if resolved is None:
        return
    binding.fill(name, ast.Constant(f"{_SCHEMA}.{resolved}"), scope)


def _query_target(statement):
    """Queried SQL text a guard statement targets; ``None`` for a guard object.

    A ``CREATE TRIGGER``/``FUNCTION`` names the schema it targets in place; any
    other statement qualifies only the schema and leaves tables to the query.
    """
    if _GUARD_DDL.match(statement):
        return None
    return statement


def _run_time_only(statement):
    """Guard DDL whose identifiers only come from a queried loop; not reproducible here."""
    if _CREATE_TRIGGER.search(statement):
        return _table_of(statement) is None
    return _function_name(statement) == _SCHEMA


def _table_of(statement):
    """Bare table a ``CREATE TRIGGER`` targets; ``None`` when it is not the placeholder schema."""
    match = _TRIGGER_ON.search(statement)
    if match is None:
        return None
    table = match.group("table")
    if not table.startswith(_SCHEMA + "."):
        return None
    return table[len(_SCHEMA) + 1 :]


def _bare(statement):
    """``statement`` with the schema placeholder stripped from qualified names."""
    return re.sub(re.escape(f"{_SCHEMA}."), "", statement)


def _function_name(statement):
    match = _CREATE_FUNCTION.search(statement)
    return match.group("name").rsplit(".", 1)[-1] if match else None


def migration_statements():
    """``(path, statement)`` for every upgrade-path guard statement in migration history."""
    for path in sorted(MIGRATIONS.glob("*.py")):
        tree = ast.parse(path.read_text())
        for function, sql, unresolved in _executes(tree):
            if function.startswith("downgrade"):
                continue
            for statement in _statements(sql, unresolved, function, tree):
                if _GUARD_DDL.match(statement):
                    yield path, statement


def migration_trigger_pairs():
    """``(trigger name, table)`` for every reproducible ``CREATE TRIGGER``."""
    return {
        (match.group("name"), _table_of(statement))
        for _path, statement in migration_statements()
        for match in [_CREATE_TRIGGER.search(statement)]
        if match
    }


def migration_function_names():
    names = set()
    for _path, statement in migration_statements():
        name = _function_name(statement)
        if name:
            names.add(name)
    return names


def migration_function_bodies():
    """Last statically complete body per function, with the migration that wrote it.

    The body keeps the build schema the migration interpolated; callers strip
    schema qualification before comparing.
    """
    bodies = {}
    for path, statement in migration_statements():
        name = _function_name(statement)
        if not name or "$$" not in statement:
            continue
        bodies[name] = (path.name, statement.split("$$", 1)[1].split("$$", 1)[0])
    return bodies


def migration_trigger_statements():
    """``(trigger name, table) -> statement`` for the last ``CREATE TRIGGER`` per pair."""
    statements = {}
    for _path, statement in migration_statements():
        match = _CREATE_TRIGGER.search(statement)
        if match:
            statements[(match.group("name"), _table_of(statement))] = statement
    return statements


def normalized(sql):
    """Lower-case; collapse whitespace, identifier quotes and schema refs away."""
    text = re.sub(r'"(\w+)"', r"\1", sql.lower())
    text = re.sub(r"\b" + _SCHEMA + r"\.", "", text)
    text = re.sub(r"[^a-z0-9_]+", " ", text)
    return " ".join(text.split())


def _module_pairs():
    return {(trigger.name, trigger.table) for trigger in GUARDS.GUARD_TRIGGERS}


def test_the_bulk_table_map_covers_every_bulk_guarded_table():
    triples = {trigger.table for trigger in GUARDS.GUARD_TRIGGERS if trigger.name == f"{trigger.table}_immutable"}
    assert set(BULK_TABLES) == triples - ONE_OFF_TABLES, (
        "scripts/schema_guards.py and the migration-parsing BULK_TABLES map disagree: "
        f"guarded in the module only {sorted(triples - ONE_OFF_TABLES - set(BULK_TABLES))}, "
        f"in the map only {sorted(set(BULK_TABLES) - triples)}."
    )
    assert ONE_OFF_TABLES <= triples, f"One-off guards lost from the module: {sorted(ONE_OFF_TABLES - triples)}"
    validators = {trigger.function for trigger in GUARDS.GUARD_TRIGGERS if trigger.name == f"{trigger.table}_validate"}
    assert set(BULK_TABLES.values()) == validators


def test_every_migration_trigger_is_in_the_guard_module():
    missing = sorted(migration_trigger_pairs() - _module_pairs(), key=str)
    assert not missing, (
        f"Migration-created trigger(s) absent from scripts/schema_guards.py: {missing}. "
        "A metadata-built schema would lose them and accept rows the migrated schema refuses. "
        "Add the trigger (and its function) to the guard module."
    )


def test_no_guard_trigger_exists_only_in_the_module():
    extra = sorted(_module_pairs() - migration_trigger_pairs(), key=str)
    assert not extra, (
        f"scripts/schema_guards.py installs trigger(s) no migration creates: {extra}. "
        "Remove them or add the migration; the test schema must not diverge from a migrated one."
    )


def test_trigger_timing_events_and_function_match_the_migrations():
    """Name+table identity is not enough: a wrong event or function changes the rule."""
    migrated = migration_trigger_statements()
    diverged = []
    for trigger in GUARDS.GUARD_TRIGGERS:
        expected = migrated.get((trigger.name, trigger.table))
        if expected is None:
            continue
        events = _TRIGGER_EVENTS.search(expected)
        level = _TRIGGER_LEVEL.search(expected)
        function = _TRIGGER_FUNCTION.search(expected)
        actual = trigger.definition(_SCHEMA)
        actual_events = re.search(r"BEFORE (.+?) ON ", actual).group(1)
        actual_level = _TRIGGER_LEVEL.search(actual).group(1)
        if (
            not events
            or normalized(events.group(1)) != normalized(actual_events)
            or (level and level.group(1).upper() != actual_level)
            or (function and function.group(1).rsplit(".", 1)[-1] != trigger.function)
        ):
            diverged.append(trigger.name)
    assert not diverged, (
        f"Trigger definitions in scripts/schema_guards.py differ from the migrations: {diverged}. "
        "The test schema would enforce a different rule than a migrated one."
    )


def test_guard_trigger_functions_exist():
    for trigger in GUARDS.GUARD_TRIGGERS:
        assert trigger.function in GUARDS.GUARD_FUNCTIONS, f"{trigger.name} executes unknown {trigger.function}"


def test_every_guard_function_exists_in_migration_history():
    missing = sorted(set(GUARDS.GUARD_FUNCTIONS) - migration_function_names())
    assert not missing, f"Guard functions no migration creates: {missing}"


def test_every_migration_guard_function_is_in_the_module():
    extra = sorted(migration_function_names() - set(GUARDS.GUARD_FUNCTIONS))
    assert not extra, f"Migration guard functions missing from scripts/schema_guards.py: {extra}"


def test_migration_helper_arguments_follow_upgrade_reachability():
    """0103's installed body comes from upgrade even though downgrade calls its helper too."""
    path = MIGRATIONS / "0103_allow_platform_task_initiator.py"
    tree = ast.parse(path.read_text())
    reachable = _reachable_functions(tree)
    sites = _call_sites(tree, "_replace_validator", reachable)
    assert len(sites) == 1, "Upgrade must resolve to one validator helper invocation"
    assert sites[0].flags == {"allow_platform_operator": True}
    reversed_tree = ast.Module(body=list(reversed(tree.body)), type_ignores=[])
    reversed_sites = _call_sites(reversed_tree, "_replace_validator", _reachable_functions(reversed_tree))
    assert [site.flags for site in reversed_sites] == [site.flags for site in sites]


def test_function_bodies_match_the_final_migration_bodies():
    """Later revisions replace a body; the module keeps only the final one.

    ``CREATE OR REPLACE`` replaces a body, and 0103 appends a clause to the
    0099 body, so the last body per function is the only comparable one.
    """
    diverged = []
    compared = 0
    for name, (source, expected) in sorted(migration_function_bodies().items()):
        template = GUARDS.GUARD_FUNCTIONS.get(name)
        if template is None:
            continue
        compared += 1
        actual = template.replace("{s}", _SCHEMA)
        if normalized(_bare(expected)) != normalized(_bare(actual)):
            diverged.append(f"{name} (last written by {source})")
    assert compared >= 15, f"Only {compared} guard bodies were statically comparable"
    assert not diverged, (
        f"Guard bodies in scripts/schema_guards.py differ from the final migration body: {diverged}. "
        "Copy the current migration text, or record the intentional change here."
    )


def test_rendered_ddl_is_schema_qualified_and_idempotent():
    statements = GUARDS.ddl("draft_schema")
    functions = [s for s in statements if s.startswith("CREATE OR REPLACE FUNCTION")]
    triggers = [s for s in statements if s.startswith("CREATE TRIGGER")]
    drops = [s for s in statements if s.startswith("DROP TRIGGER IF EXISTS")]
    assert len(statements) == 2 * len(GUARDS.GUARD_TRIGGERS) + len(GUARDS.GUARD_FUNCTIONS)
    assert len(functions) == len(GUARDS.GUARD_FUNCTIONS)
    assert len(triggers) == len(GUARDS.GUARD_TRIGGERS) == len(drops)
    for statement in [*functions, *triggers, *drops]:
        assert "public." not in statement and '"public"' not in statement
    for statement in functions:
        assert statement.startswith('CREATE OR REPLACE FUNCTION "draft_schema"."')
    for statement in triggers:
        assert ' ON "draft_schema"."' in statement
        function = statement.rsplit('"', 2)[1]
        assert function in GUARDS.GUARD_FUNCTIONS, f"{statement} executes unknown function {function}"
        assert statement.endswith(f'EXECUTE FUNCTION "draft_schema"."{function}"()')


def test_unsafe_schema_names_are_refused():
    for name in ("public; drop schema x", 'quote"d', "", "../x"):
        try:
            GUARDS.ddl(name)
        except ValueError:
            continue
        raise AssertionError(f"Guard DDL accepted unsafe schema name {name!r}")


def test_trigger_events_keep_migration_order():
    """PostgreSQL renders DELETE OR UPDATE for UPDATE OR DELETE; both must work."""
    immutable = next(t for t in GUARDS.GUARD_TRIGGERS if t.name == "llm_dispatch_receipts_immutable")
    assert immutable.definition("s").startswith(
        'CREATE TRIGGER "llm_dispatch_receipts_immutable" BEFORE UPDATE OR DELETE'
    )
    truncate = next(t for t in GUARDS.GUARD_TRIGGERS if t.name == "llm_dispatch_receipts_no_truncate")
    assert truncate.definition("s").endswith('FOR EACH STATEMENT EXECUTE FUNCTION "s"."reject_llm_receipt_mutation"()')
    column_scoped = next(t for t in GUARDS.GUARD_TRIGGERS if t.table == "sources")
    assert 'UPDATE OF "tenant_id", "owner_task_id" ON' in column_scoped.definition("s")
