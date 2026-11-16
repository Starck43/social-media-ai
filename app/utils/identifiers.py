"""Pure identifier predicates; no coercion, ORM, settings or I/O."""


def strict_positive_int(value: object) -> bool:
    """Accept an exact positive int, excluding bool and int subclasses.

    No storage-width cap is implied. Callers with bounded IDs or subclass-aware
    semantics must retain their own predicate rather than reuse this one.
    """
    return type(value) is int and value > 0
