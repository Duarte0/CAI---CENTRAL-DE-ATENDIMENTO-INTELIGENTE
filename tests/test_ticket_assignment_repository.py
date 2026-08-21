import inspect

from src.core import db, ticket_assignment_repository


def test_ticket_assignment_persistence_is_owned_by_repository() -> None:
    public_operations = (
        "record_ticket_assignment",
        "resolve_ticket_assignments",
    )

    for name in public_operations:
        operation = getattr(db, name)
        assert operation is getattr(ticket_assignment_repository, name)
        assert inspect.getmodule(operation) is ticket_assignment_repository

    assert not hasattr(db, "_record_ticket_assignment_sync")
    assert not hasattr(db, "_resolve_ticket_assignments_sync")
