"""Scenario-only ORM persistence fences; no schema DDL or permission changes.

Flush events cover normal/admin writes; the session event covers literal bulk
configuration INSERT/UPDATE. Existing tenant predicates and transactions are
kept exactly: validation does not grant a bypass, commit or run external work.
"""
from __future__ import annotations

from sqlalchemy import event, inspect, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import BindParameter


def install_scenario_schema_guard(model) -> None:
    """Install once for the concrete model, including synchronous admin sessions."""
    # Do not import app.services.ai during model registration: its package
    # exports the analyzer, which imports the not-yet-complete models package.
    # Resolve the pure contract only at event execution, after registration.
    def values_for(target, fields):
        return {name: getattr(target, name, None) for name in fields}

    def before_insert(mapper, connection, target):
        from app.services.ai.scenario_schema import SCHEMA_FIELDS, validate_scenario_values
        validate_scenario_values(values_for(target, SCHEMA_FIELDS))

    def before_update(mapper, connection, target):
        from app.services.ai.scenario_schema import (
            SCHEMA_FIELDS, schema_write_requires_validation, validate_scenario_values,
        )
        state = inspect(target)
        changes = {name: getattr(target, name, None) for name in SCHEMA_FIELDS | {'is_active'}
                   if state.attrs[name].history.has_changes()}
        if schema_write_requires_validation(changes):
            validate_scenario_values(values_for(target, SCHEMA_FIELDS))

    def before_bulk_write(state):
        if not (state.is_update or state.is_insert):
            return
        statement = state.statement
        table = getattr(statement, 'table', None)
        if table is None or table.name != model.__table__.name or table.schema != model.__table__.schema:
            return
        from app.services.ai.scenario_schema import (
            SCHEMA_FIELDS, ScenarioSchemaError, schema_write_requires_validation, validate_scenario_values,
        )
        raw_values = getattr(statement, '_values', None) or {}
        parameters = state.parameters
        parameter_rows = parameters if isinstance(parameters, list) else [parameters or {}]
        if not all(isinstance(row, dict) for row in parameter_rows):
            raise ScenarioSchemaError('literal_schema_write_required')
        updates = []
        for parameters in parameter_rows:
            changes = {}
            for key, expression in raw_values.items():
                name = key if isinstance(key, str) else key.key
                if name not in SCHEMA_FIELDS | {'is_active'}:
                    continue
                if not isinstance(expression, BindParameter) or expression.callable is not None:
                    raise ScenarioSchemaError('literal_schema_write_required')
                if expression.required and expression.key not in parameters:
                    raise ScenarioSchemaError('literal_schema_write_required')
                changes[name] = parameters.get(expression.key, expression.value)
            changes.update({name: value for name, value in parameters.items() if name in SCHEMA_FIELDS | {'is_active'}})
            if state.is_insert or schema_write_requires_validation(changes):
                updates.append((parameters, changes))
        if not updates:
            return
        if state.is_insert:
            # Current application creation uses ORM flush. Fail closed for a
            # future INSERT FROM SELECT/multi-values expression we cannot inspect.
            if getattr(statement, 'select', None) is not None or getattr(statement, '_multi_values', ()):
                raise ScenarioSchemaError('literal_schema_write_required')
            for parameters, changes in updates:
                validate_scenario_values(changes)
            return
        if state.is_executemany:
            raise ScenarioSchemaError('per_row_schema_update_required')
        # Same transaction/row lock as the prospective UPDATE. Project columns
        # instead of mapped identities so cached ORM snapshots cannot hide changes.
        parameters, changes = updates[0]
        columns = [getattr(model, name) for name in sorted(SCHEMA_FIELDS)]
        query = select(*columns).where(*statement._where_criteria).with_for_update()
        for row in state.session.execute(query, parameters).mappings():
            validate_scenario_values({**dict(row), **changes})

    event.listen(model, 'before_insert', before_insert)
    event.listen(model, 'before_update', before_update)
    event.listen(Session, 'do_orm_execute', before_bulk_write)
