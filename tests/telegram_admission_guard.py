"""Private owner-runner guard helpers: strict INSERT rows, safe diagnostics.

No app/DB/SDK imports, logging of originals, or bypass flags. Bind decoding uses
actual INSERT shape and table column names; never combine fences across rows.
"""
from __future__ import annotations
from pathlib import Path
import re

_GUARD_MESSAGES = {'guard_01': 'Stop: UPDATE/DELETE lacks positive owned fixture predicate', 'guard_02': 'Stop: admission write outside raw/job/cursor contract', 'guard_03': 'Stop: cleanup DELETE only', 'guard_04': 'Stop: creation INSERT only', 'guard_05': 'Stop: existing Telegram platform missing; no bootstrap permitted', 'guard_06': 'Stop: expected test_schema mappings', 'guard_07': 'Stop: fixture scope/phase violation', 'guard_08': 'Stop: fixture tenant marker changed; no cleanup', 'guard_09': 'Stop: foreign source INSERT', 'guard_10': 'Stop: foreign tenant INSERT', 'guard_11': 'Stop: only fenced DML or SELECT', 'guard_12': 'Stop: unapproved COMMIT', 'guard_13': 'Stop: uncompiled write/control/DDL forbidden', 'guard_14': 'Stop: unexpected fixture UPDATE fields', 'guard_15': 'Stop: unmarked analytics fixture', 'guard_16': 'Stop: unmarked raw identity', 'guard_17': 'Stop: unmarked/api source fixture required', 'guard_18': 'Stop: unmarked/inactive tenant fixture required', 'guard_19': 'Stop: unsafe/unmarked queued fixture', 'guard_20': 'Stop: unverified fixture marker; no cleanup'}
_GUARD_MESSAGES.update({
    'batch_insert_shape': 'Stop: unsupported multi-row INSERT shape',
    'batch_insert_bind': 'Stop: unknown/malformed multi-row INSERT bind',
    'batch_insert_row': 'Stop: incomplete or unexpected multi-row INSERT row',
    'guard_code_invalid': 'Stop: invalid fixture guard diagnostic code',
})


class FixtureGuardError(RuntimeError):
    def __init__(self, code):
        self.code = code if type(code) is str and code in _GUARD_MESSAGES else 'guard_code_invalid'
        super().__init__(_GUARD_MESSAGES[self.code])


def insert_parameter_rows(command, values, *, compiled):
    """Decode compiler _mN bindings only for a real values([...]) INSERT.

    ORM single-row/execute-many parameters keep their normal column keys. The
    application uses one VALUES group. Chained/unknown groups fail closed.
    """
    groups = getattr(command, '_multi_values', ())
    if not groups:
        return [values]
    if (not getattr(command, 'is_insert', False) or len(groups) != 1
            or type(groups[0]) not in (list, tuple) or not groups[0]):
        raise FixtureGuardError('batch_insert_shape')
    rows = [{} for _ in groups[0]]
    columns = {column.name for column in command.table.columns}
    expected = [{getattr(key, 'name', key) for key in row} for row in groups[0]]
    if any(not names <= columns for names in expected):
        raise FixtureGuardError('batch_insert_shape')
    # SQLAlchemy binds Python defaults for row zero WITHOUT _m0; later rows
    # get _mN. Accept only actual compiler-prefetched columns, never ownership
    # columns supplied under an unindexed alias to borrow another row's fence.
    first_defaults = {column.name for column in compiled.insert_prefetch
                      if getattr(column, 'table', None) is not None
                      and column.table.name == command.table.name
                      and column.table.schema == command.table.schema
                      and getattr(column, 'default', None) is not None}
    for key, value in values.items():
        # Compiler keys can be SQLAlchemy str subclasses (_truncated_label).
        # Use the builtin implementation, never a custom __str__ override.
        if isinstance(key, str): key = str.__str__(key)
        if type(key) is str and key in first_defaults and key not in expected[0]:
            if key in rows[0]: raise FixtureGuardError('batch_insert_row')
            rows[0][key] = value
            continue
        match = re.fullmatch(r'(.+)_m(0|[1-9][0-9]*)', key) if type(key) is str else None
        if not match or match.group(1) not in columns:
            raise FixtureGuardError('batch_insert_bind')
        name, index = match.group(1), int(match.group(2))
        if index >= len(rows) or name in rows[index]:
            raise FixtureGuardError('batch_insert_row')
        rows[index][name] = value
    if any(not row or not expected[index] <= set(row) for index, row in enumerate(rows)):
        raise FixtureGuardError('batch_insert_row')
    return rows


_SA_CODES = {'IntegrityError': 'integrity', 'ProgrammingError': 'programming',
             'OperationalError': 'operational', 'StatementError': 'statement',
             'DBAPIError': 'dbapi', 'InvalidRequestError': 'invalid_request'}
_SQLSTATES = {'23505', '23502', '42P01', '42703', '42804', '42501', '40P01', '55P03', '57014'}
_VALIDATION_CODES = {'content_identity_conflict', 'content_receipt_missing',
                     'content_tenant_invalid', 'content_source_invalid', 'content_identity_invalid'}
_LOCATIONS = ('app/services/monitoring/staging.py', 'app/models/managers/collected_item_manager.py',
              'app/models/managers/job_manager.py', 'app/models/managers/base_manager.py')


def admission_diagnostic(error, *, root):
    """Inspect suppressed context without printing error/SQL/params/locals.

    Guard codes dominate wrapper classes. Otherwise only an allowlisted error
    category, known SQLSTATE and bounded repository file/line are returned.
    Production from-None/privacy behavior is deliberately NOT changed.
    """
    stack = [error]; seen = set(); errors = []
    while stack:
        current = stack.pop()
        if not isinstance(current, BaseException) or id(current) in seen:
            continue
        seen.add(id(current)); errors.append(current)
        if type(current) is FixtureGuardError:
            return {'kind': 'fixture_guard', 'code': current.code, 'reason': _GUARD_MESSAGES[current.code]}
        stack.extend((current.__context__, current.__cause__))
        if type(current).__module__ == 'sqlalchemy.exc' and type(current).__name__ in _SA_CODES:
            stack.append(current.orig if hasattr(current, 'orig') else None)
    code = 'unclassified'; sqlstate = None; location = None
    paths = {str(Path(root) / path): path for path in _LOCATIONS}
    for current in errors:
        kind = type(current)
        if kind.__module__ == 'sqlalchemy.exc' and kind.__name__ in _SA_CODES:
            code = _SA_CODES[kind.__name__]
        if kind is ValueError and current.args and type(current.args[0]) is str and current.args[0] in _VALIDATION_CODES:
            code = current.args[0]
        if kind.__module__.startswith('asyncpg.exceptions'):
            state = getattr(current, 'sqlstate', None)
            if type(state) is str and state in _SQLSTATES:
                sqlstate = state
        trace = current.__traceback__
        while trace:
            path = paths.get(trace.tb_frame.f_code.co_filename)
            if path is not None and type(trace.tb_lineno) is int and 0 < trace.tb_lineno < 100000:
                location = path + ':' + str(trace.tb_lineno)
            trace = trace.tb_next
    result = {'kind': 'production_error', 'code': code}
    if sqlstate is not None: result['sqlstate'] = sqlstate
    if location is not None: result['location'] = location
    return result
